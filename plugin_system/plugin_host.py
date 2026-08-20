# Vibe Coding Agent - 插件宿主子进程（P0-1 进程级隔离）
# Copyright (c) 2026 xingluosama
#
# 核心目标：插件代码**严禁直接挂载到主进程**。
#
# 架构：
#   主进程 PluginManager  —— JSON 行协议(stdin/stdout) ——>  插件宿主子进程
#     (RPC 客户端，只维护元数据)                             (真正 importlib 加载并执行插件)
#
# 协议（每行一个 JSON 对象）：
#   请求 {id, op, ...}  →  响应 {id, ok, result|error}
#   op ∈ {load, call_tool, fire_hook, set_context, shutdown}
#
# 隔离保证：
#   - 插件模块对象只存在于子进程，主进程无法被插件直接访问 / 修改
#   - 插件崩溃 / 死循环 / 内存泄漏只影响子进程，主进程可 kill 重启
#   - 插件 import 限制在子进程内继续生效（纵深防御）
#
# 该文件既是子进程入口（python -m plugin_system.plugin_host），
# 也提供主进程侧客户端 PluginHostClient。

import importlib.util
import json
import os
import subprocess
import sys
import threading
import traceback
from typing import Any, Dict, List, Optional, Tuple

from plugin_system.context import PluginContext
from plugin_system.security import (
    PluginSecurity, PluginImportBlocker, StrictImportBlocker, _loading_plugin,
)

# 协议操作
OP_LOAD = "load"
OP_CALL_TOOL = "call_tool"
OP_FIRE_HOOK = "fire_hook"
OP_SET_CONTEXT = "set_context"
OP_SHUTDOWN = "shutdown"

# 子进程默认空闲超时（秒）：超过则退出，避免僵尸宿主进程
HOST_IDLE_TIMEOUT = 600.0
# 主进程与子进程握手/响应默认超时（秒）
DEFAULT_RPC_TIMEOUT = 120.0


# ── 序列化辅助 ───────────────────────────────────────────────────

def _safe_json(obj: Any) -> Any:
    """把任意返回值转成可 JSON 序列化的对象。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (dict, list, tuple)):
        try:
            if isinstance(obj, tuple):
                obj = list(obj)
            json.dumps(obj)  # 测试可序列化
            return obj
        except (TypeError, ValueError):
            pass
    # 兜底：转字符串
    try:
        return str(obj)
    except Exception:
        return f"<unserializable {type(obj).__name__}>"


# ═══════════════════════════════════════════════════════════════════
#  子进程侧：插件宿主进程
# ═══════════════════════════════════════════════════════════════════

class PluginHostProcess:
    """运行在子进程中的插件宿主。

    负责：加载插件模块、执行工具、触发钩子。
    主进程通过 stdin/stdout 的 JSON 行协议与本类交互。
    """

    def __init__(self):
        self._modules: Dict[str, Any] = {}          # plugin_name -> module
        self._contexts: Dict[str, PluginContext] = {}  # plugin_name -> context
        self._blocker: Optional[StrictImportBlocker] = None
        self._plugin_import_blocker: Optional[PluginImportBlocker] = None
        self._import_restrict = "off"
        self._audit_level = "warn"

    # ── 主循环 ──

    def serve(self):
        """从 stdin 逐行读取请求并处理，直到 shutdown 或 stdin 关闭。"""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as exc:
                self._reply({"id": None, "ok": False, "error": f"bad request: {exc}"})
                continue
            resp = self.handle(req)
            self._reply(resp)
            if req.get("op") == OP_SHUTDOWN:
                return

    @staticmethod
    def _reply(resp: dict):
        try:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:
            pass

    # ── 请求分发 ──

    def handle(self, req: dict) -> dict:
        req_id = req.get("id")
        op = req.get("op", "")
        try:
            if op == OP_LOAD:
                return {"id": req_id, "ok": True, "result": self._do_load(req)}
            if op == OP_CALL_TOOL:
                return {"id": req_id, "ok": True, "result": self._do_call_tool(req)}
            if op == OP_FIRE_HOOK:
                return {"id": req_id, "ok": True, "result": self._do_fire_hook(req)}
            if op == OP_SET_CONTEXT:
                self._do_set_context(req)
                return {"id": req_id, "ok": True, "result": None}
            if op == OP_SHUTDOWN:
                return {"id": req_id, "ok": True, "result": None}
            return {"id": req_id, "ok": False, "error": f"unknown op: {op}"}
        except Exception as exc:
            return {"id": req_id, "ok": False,
                    "error": f"{exc}\n{traceback.format_exc(limit=5)}"}

    # ── 安全配置 ──

    def _apply_security(self, req: dict):
        sec = req.get("security_config") or {}
        self._import_restrict = sec.get("import_restrict", "off")
        self._audit_level = sec.get("audit_level", "warn")
        self._teardown_blockers()

        if self._audit_level == "off":
            return
        if self._import_restrict == "strict":
            self._blocker = StrictImportBlocker("vibe_plugin_")
            self._blocker.register()
        elif self._import_restrict == "safe":
            from plugin_system.security import DANGEROUS_IMPORTS, ALWAYS_BLOCKED
            blocked = set(DANGEROUS_IMPORTS.keys()) | ALWAYS_BLOCKED
            self._plugin_import_blocker = PluginImportBlocker(blocked, "vibe_plugin_")
            self._plugin_import_blocker.register()

    def _teardown_blockers(self):
        if self._blocker:
            self._blocker.unregister()
            self._blocker = None
        if self._plugin_import_blocker:
            self._plugin_import_blocker.unregister()
            self._plugin_import_blocker = None

    # ── 操作实现 ──

    def _do_load(self, req: dict) -> dict:
        """在子进程中加载插件模块，返回接口描述。"""
        plugin_name = req.get("plugin_name", "")
        path = req.get("path", "")
        self._apply_security(req)

        if not path or not os.path.isfile(path):
            raise RuntimeError(f"插件文件不存在：{path}")

        mod_name = f"vibe_plugin_{plugin_name}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法为 {path} 创建模块 spec")

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module

        _loading_plugin.active = True
        try:
            spec.loader.exec_module(module)
        finally:
            _loading_plugin.active = False

        self._modules[plugin_name] = module

        # 读取头部元数据
        name = plugin_name
        header_name = getattr(module, "PLUGIN_NAME", None)
        if isinstance(header_name, str) and header_name.strip():
            name = header_name.strip()

        # ★ 注册 header 名 alias：主进程可能用 header 名（PLUGIN_NAME）做 RPC 定位，
        #   这里让文件名 key 与 header 名 key 同时指向同一模块，避免「插件未加载」。
        if name != plugin_name:
            self._modules[name] = module

        publisher = getattr(module, "PLUGIN_PUBLISHER", None)
        version = getattr(module, "PLUGIN_VERSION", None)
        description = getattr(module, "PLUGIN_DESCRIPTION", None)

        tools = getattr(module, "TOOLS", None)
        if not isinstance(tools, list):
            tools = []

        hook_names = []
        from plugin_system.manager import HOOK_NAMES
        for h in HOOK_NAMES:
            fn = getattr(module, h, None)
            if callable(fn):
                hook_names.append(h)

        has_execute = callable(getattr(module, "execute", None))

        # ── 工具审批提示（APPROVAL_HINTS）──
        # 插件可声明「工具 → 审批级别」映射，主进程审批层据此精细控制：
        #   {"tool_name": "none"}   该工具调用无需人工审批（如只读工具）
        #   {"tool_name": "plugin"} 走插件审批总开关（默认行为）
        #   {"tool_name": {"approval": "...", "risk": "L2"}}
        #                           带风险级声明（供 delegate 让渡免审判断）
        approval_hints = {}
        raw_hints = getattr(module, "APPROVAL_HINTS", None)
        if isinstance(raw_hints, dict):
            for tname, hint in raw_hints.items():
                if not isinstance(tname, str):
                    continue
                if isinstance(hint, str):
                    approval_hints[tname] = {"approval": hint}
                elif isinstance(hint, dict):
                    approval_hints[tname] = {
                        "approval": str(hint.get("approval", "plugin")),
                        "risk": str(hint.get("risk", "")),
                    }

        # 预先创建 context（storage 在子进程内持久）；文件名 key 与 header 名 key 共享同一对象
        ctx = self._make_context(plugin_name, req)
        self._contexts.setdefault(plugin_name, ctx)
        if name != plugin_name:
            self._contexts.setdefault(name, ctx)

        return {
            "name": name,
            "tools": _safe_json(tools),
            "hook_names": hook_names,
            "has_execute": has_execute,
            "publisher": (publisher if isinstance(publisher, str) else ""),
            "version": (version if isinstance(version, str) else ""),
            "description": (description if isinstance(description, str) else ""),
            "approval_hints": _safe_json(approval_hints),
        }

    def _make_context(self, plugin_name: str, req: dict) -> PluginContext:
        ctx_cfg = (req.get("context") or {}).get("config") or {}
        return PluginContext(
            plugin_name=plugin_name,
            project_root=(req.get("context") or {}).get("project_root", ""),
            app_dir=(req.get("context") or {}).get("app_dir", ""),
            config=ctx_cfg,
        )

    def _sync_context(self, plugin_name: str, req: dict):
        """更新 context 的只读字段（保留 storage）。"""
        ctx = self._contexts.get(plugin_name)
        if ctx is None:
            ctx = self._make_context(plugin_name, req)
            self._contexts[plugin_name] = ctx
        c = req.get("context") or {}
        ctx.project_root = c.get("project_root", ctx.project_root)
        ctx.app_dir = c.get("app_dir", ctx.app_dir)
        ctx.config = c.get("config") or {}
        ctx.current_step = c.get("current_step", ctx.current_step)
        ctx.total_usage = c.get("total_usage", ctx.total_usage)
        return ctx

    def _do_call_tool(self, req: dict) -> dict:
        plugin_name = req.get("plugin", "")
        module = self._modules.get(plugin_name)
        if module is None:
            raise RuntimeError(f"插件未加载：{plugin_name}")
        execute_fn = getattr(module, "execute", None)
        if not callable(execute_fn):
            raise RuntimeError(f"插件 {plugin_name} 无 execute() 函数")

        tool = req.get("tool", "")
        args = req.get("args") or {}
        ctx = self._sync_context(plugin_name, req)
        try:
            output = execute_fn(tool, args, ctx)
        except Exception:
            return {"output": f"Plugin execution failed:\n{traceback.format_exc()}"}
        return {"output": _safe_json(output)}

    def _do_fire_hook(self, req: dict) -> dict:
        plugin_name = req.get("plugin", "")
        module = self._modules.get(plugin_name)
        if module is None:
            raise RuntimeError(f"插件未加载：{plugin_name}")
        hook = req.get("hook", "")
        fn = getattr(module, hook, None)
        if not callable(fn):
            return {"result": None}

        args = req.get("args") or []
        if not isinstance(args, (list, tuple)):
            args = [args]
        args = list(args)
        ctx = self._sync_context(plugin_name, req)
        # 钩子约定：最后一个参数是 PluginContext（主进程已剥离其 ctx，此处补上）
        try:
            result = fn(*args, ctx)
        except Exception as exc:
            return {"result": None, "error": str(exc)}
        return {"result": _safe_json(result)}

    def _do_set_context(self, req: dict):
        for plugin_name in list(self._contexts.keys()):
            self._sync_context(plugin_name, req)


# ═══════════════════════════════════════════════════════════════════
#  主进程侧：RPC 客户端
# ═══════════════════════════════════════════════════════════════════

class PluginHostClient:
    """主进程侧的插件宿主客户端。

    负责：启动子进程、发送请求、接收响应、检测子进程存活。
    """

    def __init__(self, app_dir: str = "", project_root: str = ""):
        self.app_dir = app_dir
        self.project_root = project_root
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._pending: Dict[int, Dict[str, Any]] = {}  # id -> {event, resp}
        self._reader: Optional[threading.Thread] = None
        self._err_reader: Optional[threading.Thread] = None
        self._alive = False

    # ── 生命周期 ──

    def start(self) -> bool:
        """启动插件宿主子进程。返回是否成功。"""
        with self._lock:
            if self._alive:
                return True
            cmd = self._build_command()
            if not cmd:
                return False
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self.project_root or os.getcwd(),
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                )
            except Exception:
                self._proc = None
                return False

            self._alive = True
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            # 后台读取 stderr，避免子进程 stderr 缓冲区写满导致阻塞
            self._err_reader = threading.Thread(target=self._drain_stderr, daemon=True)
            self._err_reader.start()
            return True

    def _build_command(self) -> Optional[List[str]]:
        """构造子进程启动命令。

        源码运行：``python -m plugin_system.plugin_host``，
        确保 ``sys.path`` 正确（能解析 plugin_system 包）。

        打包运行（PyInstaller frozen）：以「自身 exe + --norp-plugin-host」
        启动。frozen exe 无法用 ``-m`` 跑模块（会变成第二个 GUI 实例并触发
        单实例锁），因此 main.py 入口会检测该参数，直接进入插件宿主主循环。
        """
        try:
            if getattr(sys, 'frozen', False):
                return [sys.executable, "--norp-plugin-host"]
            return [sys.executable, "-m", "plugin_system.plugin_host"]
        except Exception:
            return None

    def _read_loop(self):
        """后台线程：持续读取子进程 stdout，分发响应。"""
        try:
            assert self._proc and self._proc.stdout
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    resp = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rid = resp.get("id")
                with self._lock:
                    entry = self._pending.get(rid)
                if entry is not None:
                    entry["resp"] = resp
                    entry["event"].set()
        except Exception:
            pass

    def _drain_stderr(self):
        """后台读取子进程 stderr（仅丢弃，避免缓冲区写满阻塞）。"""
        try:
            if self._proc and self._proc.stderr:
                for _ in self._proc.stderr:
                    pass
        except Exception:
            pass

    # ── 请求 ──

    def request(self, req: dict, timeout: float = DEFAULT_RPC_TIMEOUT) -> dict:
        """发送请求并同步等待响应。"""
        if not self._alive or self._proc is None or self._proc.stdin is None:
            raise RuntimeError("插件宿主进程未运行")

        with self._lock:
            self._next_id += 1
            rid = self._next_id
            req = dict(req)
            req["id"] = rid
            event = threading.Event()
            self._pending[rid] = {"event": event, "resp": None}
            try:
                self._proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
            except Exception as exc:
                self._pending.pop(rid, None)
                raise RuntimeError(f"向插件宿主进程写入失败：{exc}") from exc

        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(rid, None)
            raise TimeoutError(f"插件宿主进程响应超时（{timeout}s）")

        with self._lock:
            entry = self._pending.pop(rid, None)
        resp = entry["resp"] if entry else None
        if resp is None:
            raise RuntimeError("插件宿主进程响应为空")
        if not resp.get("ok", False):
            raise RuntimeError(resp.get("error", "插件宿主进程返回错误"))
        return resp.get("result")

    def is_alive(self) -> bool:
        """子进程是否存活。"""
        with self._lock:
            if not self._alive or self._proc is None:
                return False
            return self._proc.poll() is None

    def shutdown(self):
        """关闭子进程。"""
        with self._lock:
            if not self._alive or self._proc is None:
                self._alive = False
                return
            try:
                self._proc.stdin.write(json.dumps({"id": -1, "op": OP_SHUTDOWN}) + "\n")
                self._proc.stdin.flush()
            except Exception:
                pass
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._alive = False
            self._proc = None

    def kill(self):
        """强制终止子进程（用于崩溃恢复）。"""
        with self._lock:
            if self._proc is not None:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._alive = False
            self._proc = None


# ═══════════════════════════════════════════════════════════════════
#  子进程入口
# ═══════════════════════════════════════════════════════════════════

def main():
    """子进程入口点。"""
    # ★ 强制 stdout/stderr 用 UTF-8：Windows 子进程默认继承 GBK locale，
    #   会导致中文输出被父进程（utf-8 解码）误读。这里统一为 UTF-8。
    import io
    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", newline="\n")
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace", newline="\n")
    except Exception:
        pass
    host = PluginHostProcess()
    host.serve()


if __name__ == "__main__":
    main()

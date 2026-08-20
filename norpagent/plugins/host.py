# Copyright (c) 2026 xingluosama121, MIT Licensed
"""插件宿主子进程（P4 进程级插件隔离，零第三方依赖）。

核心目标：选择进程隔离的插件代码**不挂载到主进程**。

架构::

    主进程 (norpagent.plugins.isolation)  —— JSON 行协议(stdin/stdout) ——>  本子进程
      (RPC 客户端，只维护元数据)                                          (真正加载并执行插件)

协议（每行一个 JSON 对象）：
    请求 {id, op, ...}  →  响应 {id, ok, result|error}
    op ∈ {load, call_tool, fire_hook, set_context, shutdown}

隔离保证：
    - 插件模块对象只存在于子进程，主进程无法被插件直接访问/修改；
    - 插件崩溃 / 死循环 / 内存泄漏只影响子进程，主进程可 kill 重启；
    - 插件 import 限制在子进程内继续生效（纵深防御）。

入口：``python -m norpagent.plugins.host``（主进程侧客户端见 isolation）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

from norpagent.plugins.loader import (
    ALWAYS_BLOCKED,
    DANGEROUS_IMPORTS_FOR_BLOCK,
    HOOK_NAMES,
    PLUGIN_MODULE_PREFIX,
    PluginContext,
    _ImportBlocker,
    _loading_plugin,
)

# 协议操作
OP_LOAD = "load"
OP_CALL_TOOL = "call_tool"
OP_FIRE_HOOK = "fire_hook"
OP_SET_CONTEXT = "set_context"
OP_SHUTDOWN = "shutdown"


def _safe_json(obj: Any) -> Any:
    """把任意返回值转成可 JSON 序列化的对象。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (dict, list, tuple)):
        try:
            if isinstance(obj, tuple):
                obj = list(obj)
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            pass
    try:
        return str(obj)
    except Exception:
        return f"<unserializable {type(obj).__name__}>"


class PluginHostProcess:
    """运行在子进程中的插件宿主。

    负责：加载插件模块、执行工具、触发钩子。
    主进程通过 stdin/stdout 的 JSON 行协议与本类交互。
    """

    def __init__(self) -> None:
        self._modules: Dict[str, Any] = {}          # plugin_name -> module
        self._contexts: Dict[str, PluginContext] = {}
        self._blocker: Optional[_ImportBlocker] = None
        self._import_restrict = "off"

    # ── 主循环 ──────────────────────────────────────────

    def serve(self) -> None:
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
    def _reply(resp: dict) -> None:
        try:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:
            pass

    # ── 请求分发 ────────────────────────────────────────

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
        except Exception as exc:  # noqa: BLE001 — 任何错误都回传，不杀宿主
            return {"id": req_id, "ok": False,
                    "error": f"{exc}\n{traceback.format_exc(limit=5)}"}

    # ── 安全配置（子进程内继续生效的导入限制）─────────────

    def _apply_security(self, req: dict) -> None:
        sec = req.get("security_config") or {}
        self._import_restrict = sec.get("import_restrict", "off")
        if self._blocker is not None:
            self._blocker.unregister()
            self._blocker = None
        if self._import_restrict in ("safe", "strict"):
            self._blocker = _ImportBlocker(
                set(DANGEROUS_IMPORTS_FOR_BLOCK) | set(ALWAYS_BLOCKED),
                strict=(self._import_restrict == "strict"),
            )
            self._blocker.register()

    # ── 操作实现 ────────────────────────────────────────

    def _do_load(self, req: dict) -> dict:
        """在子进程中加载插件模块，返回接口描述。"""
        plugin_name = req.get("plugin_name", "")
        path = req.get("path", "")
        self._apply_security(req)

        if not path or not os.path.isfile(path):
            raise RuntimeError(f"插件文件不存在：{path}")

        mod_name = f"{PLUGIN_MODULE_PREFIX}{plugin_name}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法为 {path} 创建模块 spec")

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module

        _loading_plugin.active = True
        try:
            spec.loader.exec_module(module)
        except ImportError as exc:
            raise RuntimeError(f"导入被拦截: {exc}") from exc
        finally:
            _loading_plugin.active = False

        self._modules[plugin_name] = module

        name = plugin_name
        header_name = getattr(module, "PLUGIN_NAME", None)
        if isinstance(header_name, str) and header_name.strip():
            name = header_name.strip()

        # header 名 alias：主进程可能用 PLUGIN_NAME 做 RPC 定位
        if name != plugin_name:
            self._modules[name] = module

        publisher = getattr(module, "PLUGIN_PUBLISHER", None)
        version = getattr(module, "PLUGIN_VERSION", None)
        description = getattr(module, "PLUGIN_DESCRIPTION", None)

        tools = getattr(module, "TOOLS", None)
        if not isinstance(tools, list):
            tools = []

        hook_names = [
            h for h in HOOK_NAMES if callable(getattr(module, h, None))
        ]
        has_execute = callable(getattr(module, "execute", None))

        # 工具审批提示（APPROVAL_HINTS）
        approval_hints: Dict[str, dict] = {}
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

        # 预创建 context（storage 在子进程内持久）
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
        c = req.get("context") or {}
        return PluginContext(
            plugin_name=plugin_name,
            project_root=str(c.get("project_root", "")),
            app_dir=str(c.get("app_dir", "")),
            config=(c.get("config") or {}),
        )

    def _sync_context(self, plugin_name: str, req: dict) -> PluginContext:
        ctx = self._contexts.get(plugin_name)
        if ctx is None:
            ctx = self._make_context(plugin_name, req)
            self._contexts[plugin_name] = ctx
        c = req.get("context") or {}
        if c.get("project_root"):
            ctx.project_root = str(c["project_root"])
        if c.get("app_dir"):
            ctx.app_dir = str(c["app_dir"])
        if c.get("config"):
            ctx.config = c["config"]
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
        except Exception:  # noqa: BLE001 — 子进程内兜底，错误回传不杀宿主
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
        try:
            result = fn(*args, ctx)
        except Exception as exc:  # noqa: BLE001
            return {"result": None, "error": str(exc)}
        return {"result": _safe_json(result)}

    def _do_set_context(self, req: dict) -> None:
        for plugin_name in list(self._contexts.keys()):
            self._sync_context(plugin_name, req)


def main() -> None:
    """子进程入口点（python -m norpagent.plugins.host）。"""
    # ★ 强制 stdin/stdout/stderr 用 UTF-8：Windows 子进程默认继承 GBK locale，
    #   会导致中文协议消息被父进程（utf-8）误读。这里统一为 UTF-8。
    import io

    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", newline="\n")
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace", newline="\n")
        sys.stdin = io.TextIOWrapper(
            sys.stdin.buffer, encoding="utf-8", errors="replace", newline="\n")
    except Exception:
        pass
    host = PluginHostProcess()
    host.serve()


if __name__ == "__main__":
    main()

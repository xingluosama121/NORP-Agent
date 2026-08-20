# Copyright (c) 2026 xingluosama121, MIT Licensed
"""主进程侧插件宿主客户端（P4 进程级插件隔离，零第三方依赖）。

    from norpagent.plugins.isolation import ProcessIsolationManager

    iso = ProcessIsolationManager(config={"plugin_security_import_restrict": "safe"})
    meta = iso.load("my_plugin", "/path/plugin.py", ctx_dict, security_config)
    iso.call_tool("my_plugin", "tool_x", {"a": 1}, ctx_dict)
    iso.shutdown()

特性：

- **一个子进程承载多个插件**：JSON 行协议 RPC（stdin/stdout）；
- **崩溃自愈**：请求发现子进程死亡 → 自动重启并重载全部插件 → 重试一次；
- **钩子超时**：fire_hook 在守护线程中执行并限时等待，超时放弃（僵尸
  线程在 shutdown 时回收），插件钩子永不拖死主循环；
- **工具错误不冒泡**：远端异常转为失败 ToolResult，与进程内插件一致。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

# 钩子单次调用最大等待时间（与现有应用 HOOK_TIMEOUT 一致）
HOOK_TIMEOUT = 5.0
# RPC 默认超时
DEFAULT_RPC_TIMEOUT = 120.0
# 子进程空闲超时（宿主进程侧自退，避免僵尸宿主）
HOST_IDLE_TIMEOUT = 600.0

# 因钩子超时被放弃的后台线程（shutdown 时回收）
_zombie_threads: List[threading.Thread] = []
_zombie_lock = threading.Lock()


def _safe_json(obj: Any) -> Any:
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


def reap_zombies() -> None:
    """回收已完成的超时钩子线程（框架 shutdown 时调用）。"""
    with _zombie_lock:
        still: List[threading.Thread] = []
        for t in _zombie_threads:
            if t.is_alive():
                t.join(0.1)
                still.append(t)
        _zombie_threads[:] = still


class ProcessPluginHost:
    """单个插件宿主子进程的 RPC 客户端。"""

    def __init__(
        self,
        security_config: Optional[dict] = None,
        cwd: Optional[str] = None,
        rpc_timeout: float = DEFAULT_RPC_TIMEOUT,
    ) -> None:
        self.security_config = security_config or {}
        self.cwd = cwd or os.getcwd()
        self.rpc_timeout = rpc_timeout
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._pending: Dict[int, Dict[str, Any]] = {}
        self._reader: Optional[threading.Thread] = None
        self._err_reader: Optional[threading.Thread] = None
        self._alive = False
        self._loaded: Dict[str, dict] = {}  # plugin_name -> load 元数据

    # ── 生命周期 ────────────────────────────────────────

    def start(self) -> bool:
        with self._lock:
            if self._alive:
                return True
            try:
                self._proc = subprocess.Popen(
                    self._build_command(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self.cwd,
                    bufsize=0,
                )
            except Exception:
                self._proc = None
                return False
            # 二进制管道 + 手动文本包装：newline="\\n" 禁用 os.linesep 翻译
            import io as _io

            try:
                self._proc.stdin = _io.TextIOWrapper(
                    self._proc.stdin, encoding="utf-8", errors="replace",
                    newline="\n", write_through=True,
                )
                self._proc.stdout = _io.TextIOWrapper(
                    self._proc.stdout, encoding="utf-8", errors="replace",
                    newline="\n",
                )
                self._proc.stderr = _io.TextIOWrapper(
                    self._proc.stderr, encoding="utf-8", errors="replace",
                    newline="",
                )
            except Exception:
                pass
            self._alive = True
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            self._err_reader = threading.Thread(target=self._drain_stderr, daemon=True)
            self._err_reader.start()
            return True

    @staticmethod
    def _build_command() -> List[str]:
        """构造子进程启动命令：python -m norpagent.plugins.host。

        插件宿主子进程需要能 import norpagent（与主进程同解释器环境）。
        """
        return [sys.executable, "-m", "norpagent.plugins.host"]

    def _read_loop(self) -> None:
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

    def _drain_stderr(self) -> None:
        try:
            if self._proc and self._proc.stderr:
                for _ in self._proc.stderr:
                    pass
        except Exception:
            pass

    # ── 请求 ────────────────────────────────────────────

    def request(self, req: dict, timeout: Optional[float] = None) -> Any:
        """发送请求并同步等待响应，返回 result（错误抛 RuntimeError）。"""
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

        if not event.wait(timeout if timeout is not None else self.rpc_timeout):
            with self._lock:
                self._pending.pop(rid, None)
            raise TimeoutError(
                f"插件宿主进程响应超时（{timeout or self.rpc_timeout}s）"
            )
        with self._lock:
            entry = self._pending.pop(rid, None)
        resp = entry["resp"] if entry else None
        if resp is None:
            raise RuntimeError("插件宿主进程响应为空")
        if not resp.get("ok", False):
            raise RuntimeError(resp.get("error", "插件宿主进程返回错误"))
        return resp.get("result")

    def is_alive(self) -> bool:
        with self._lock:
            if not self._alive or self._proc is None:
                return False
            return self._proc.poll() is None

    def shutdown(self) -> None:
        """关闭子进程（先发 shutdown 请求，超时强杀）。"""
        with self._lock:
            if not self._alive or self._proc is None:
                self._alive = False
                return
            try:
                self._proc.stdin.write(
                    json.dumps({"id": -1, "op": "shutdown"}) + "\n"
                )
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
                self._kill()
            self._alive = False
            self._proc = None

    def _kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._alive = False
        self._proc = None


class ProcessIsolationManager:
    """进程级插件隔离管理器：一个宿主子进程 + 自动重载。"""

    def __init__(
        self,
        security_config: Optional[dict] = None,
        cwd: Optional[str] = None,
        rpc_timeout: float = DEFAULT_RPC_TIMEOUT,
    ) -> None:
        self.security_config = dict(security_config or {})
        self.cwd = cwd or os.getcwd()
        self.rpc_timeout = rpc_timeout
        self._host = ProcessPluginHost(
            security_config=self.security_config,
            cwd=self.cwd,
            rpc_timeout=rpc_timeout,
        )
        self._plugins: Dict[str, dict] = {}   # plugin_name -> {path, context}
        self._lock = threading.Lock()

    # ── 插件装载 ────────────────────────────────────────

    def load(
        self,
        plugin_name: str,
        path: str,
        context: Optional[dict] = None,
        security_config: Optional[dict] = None,
    ) -> dict:
        """在子进程中加载插件，返回接口元数据（name/tools/hook_names...）。"""
        req = {
            "op": "load",
            "plugin_name": plugin_name,
            "path": path,
            "context": context or {},
            "security_config": security_config or self.security_config,
        }
        result = self._request_with_respawn(req)
        with self._lock:
            self._plugins[plugin_name] = {"path": path, "context": context or {}}
        return result

    def set_context(self, context: dict) -> None:
        """刷新全部插件共享的上下文只读字段。"""
        try:
            self._request_with_respawn({"op": "set_context", "context": context})
        except RuntimeError:
            pass

    def context_for(self, plugin_name: str, context: Optional[dict] = None) -> dict:
        """合并插件基础上下文与本次任务上下文。"""
        base = self._plugins.get(plugin_name, {}).get("context", {}) or {}
        merged = dict(base)
        if context:
            merged.update(context)
        merged["plugin_name"] = plugin_name
        return merged

    # ── RPC ─────────────────────────────────────────────

    def call_tool(
        self,
        plugin_name: str,
        tool: str,
        args: dict,
        context: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """执行插件工具，返回 output（宿主错误抛 RuntimeError）。"""
        result = self._request_with_respawn({
            "op": "call_tool",
            "plugin": plugin_name,
            "tool": tool,
            "args": _safe_json(args) if isinstance(args, dict) else args,
            "context": self.context_for(plugin_name, context),
        }, timeout=timeout)
        if isinstance(result, dict) and "output" in result:
            return result["output"]
        return result

    def fire_hook(
        self,
        plugin_name: str,
        hook: str,
        args: List[Any],
        context: Optional[dict] = None,
        hook_timeout: float = HOOK_TIMEOUT,
    ) -> Any:
        """触发插件钩子，返回结果（超时放弃返回 None，插件异常回传 None）。"""
        box: Dict[str, Any] = {}

        def worker() -> None:
            try:
                box["result"] = self._request_with_respawn({
                    "op": "fire_hook",
                    "plugin": plugin_name,
                    "hook": hook,
                    "args": _safe_json(args),
                    "context": self.context_for(plugin_name, context),
                }, timeout=max(hook_timeout, 1.0))
            except Exception:  # noqa: BLE001
                box["result"] = None

        t = threading.Thread(
            target=worker, daemon=True,
            name=f"norpagent-pluginhook-{plugin_name}-{hook}",
        )
        t.start()
        t.join(hook_timeout)
        if t.is_alive():
            with _zombie_lock:
                _zombie_threads.append(t)
            return None
        result = box.get("result")
        if isinstance(result, dict):
            return result.get("result")
        return result

    def _request_with_respawn(self, req: dict, timeout: Optional[float] = None) -> Any:
        """请求 + 崩溃自愈：子进程死亡时重启、重载全部插件、重试一次。"""
        if not self._host.is_alive():
            self._respawn()
        else:
            self._host.start()
        try:
            return self._host.request(req, timeout=timeout)
        except (RuntimeError, TimeoutError, OSError, ValueError):
            # 子进程死亡或管道损坏：重启并重试一次
            if not self._host.is_alive():
                self._respawn()
                return self._host.request(req, timeout=timeout)
            raise

    def _respawn(self) -> None:
        """重启子进程并重载全部插件。"""
        self._host._kill()
        if not self._host.start():
            raise RuntimeError("插件宿主子进程启动失败")
        with self._lock:
            plugins = {
                name: dict(meta) for name, meta in self._plugins.items()
            }
        for name, meta in plugins.items():
            self._host.request({
                "op": "load",
                "plugin_name": name,
                "path": meta["path"],
                "context": meta["context"],
                "security_config": self.security_config,
            })

    def is_alive(self) -> bool:
        return self._host.is_alive()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "alive": self._host.is_alive(),
                "plugins": sorted(self._plugins),
            }

    def shutdown(self) -> None:
        self._host.shutdown()
        reap_zombies()

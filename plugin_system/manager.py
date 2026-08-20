# Vibe Coding Agent - Plugin Manager
# Copyright (c) 2026 xingluosama

import importlib.util
import json
import logging
import os
import sys
import threading
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

from plugin_system.context import PluginContext
from plugin_system.security import (
    PluginSecurity, SecurityIssue, Severity,
    PluginImportBlocker, StrictImportBlocker, ResourceLimiter,
    _loading_plugin,
)
from plugin_system.signature import SignatureVerifier, SignatureStatus
from plugin_system.network_policy import NetworkPolicy
from plugin_system.approval import ApprovalPolicy
from plugin_system.plugin_host import PluginHostClient


# ── Hook names (all 15 hooks across 4 layers) ──────────────────────
HOOK_NAMES = [
    # L1 – Lifecycle
    "on_agent_init",
    "on_agent_shutdown",
    # L2 – Task
    "on_task_start",
    "on_task_done",
    "on_task_error",
    "on_task_stopped",
    "on_task_timeout",
    # L3 – Step
    "before_step",
    "after_step",
    "before_tool_call",
    "after_tool_call",
    "on_user_input_required",
    # L4 – Streaming events
    "on_reasoning",
    "on_content",
    "on_event",
    "on_usage_update",
]

# Hooks whose return value can modify the data flow
_MUTATING_HOOKS = {"before_step", "before_tool_call", "after_tool_call"}

# Max seconds a single hook callback is allowed to run
HOOK_TIMEOUT = 5.0

# Logger for plugin system messages
_log = logging.getLogger("plugin_system")

# Track threads abandoned due to hook timeout (reaped at shutdown)
_zombie_threads: List[threading.Thread] = []
_zombie_lock = threading.Lock()


class PluginInfo:
    """Lightweight metadata for one plugin instance."""

    __slots__ = ("name", "path", "version", "publisher", "description",
                 "enabled", "error", "tools", "module", "_hook_names",
                 "signature_status", "trusted", "isolation", "has_execute",
                 "approval_hints")

    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self.version = "0.0.0"
        self.publisher = ""
        self.description = ""
        self.enabled = True
        self.error: Optional[str] = None
        self.tools: List[dict] = []
        self.module: Any = None
        self._hook_names: List[str] = []
        self.signature_status: str = ""     # 签名校验状态
        self.trusted: bool = False          # 是否受信任签名
        self.isolation: str = "process"     # 该插件实际运行模式
        self.has_execute: bool = False      # 是否定义 execute()
        self.approval_hints: Dict[str, dict] = {}  # 工具 → 审批提示（APPROVAL_HINTS）

    @property
    def hook_names(self) -> List[str]:
        return self._hook_names


class PluginManager:
    """
    Discovers, loads, and dispatches external plugins.

    Plugins can be placed in any directory listed in ``plugin_dirs``
    (config.json → plugin_dirs).  Two layout styles are supported::

        plugins/
          my_tool.py              # single-file plugin
          fancy/
            manifest.json         # optional metadata
            plugin.py             # entry point

    Every plugin **must** declare its metadata via module-level constants::

        PLUGIN_NAME = "My Plugin"
        PLUGIN_PUBLISHER = "Author Name"
        PLUGIN_VERSION = "1.0.0"       # optional, default "0.0.0"
        PLUGIN_DESCRIPTION = "..."     # optional, default ""

    Every plugin *may* expose:

    * ``TOOLS`` – a list of OpenAI function-schema dicts
    * ``execute(tool_name, args, context) -> str`` – tool handler
    * Any of the 15 hook functions listed in ``HOOK_NAMES``

    Hooks that can mutate data (``before_step``, ``before_tool_call``,
    ``after_tool_call``) may return a modified value; otherwise their
    return value is ignored.
    """

    def __init__(self, plugin_dirs: List[str], app_dir: str,
                 project_root: str, config: Optional[dict] = None):
        # Normalise plugin directories
        self._plugin_dirs: List[str] = []
        for d in (plugin_dirs or []):
            resolved = os.path.normpath(
                d if os.path.isabs(d) else os.path.join(app_dir, d))
            self._plugin_dirs.append(resolved)

        self.app_dir = app_dir
        self.project_root = project_root

        # Plugin registry
        self._plugins: Dict[str, PluginInfo] = {}
        # tool_name → (plugin_name, execute_fn)
        self._tool_registry: Dict[str, Tuple[str, Optional[Callable]]] = {}
        # hook_name → [(plugin_name, fn), ...]
        self._hooks: Dict[str, List[Tuple[str, Callable]]] = {
            h: [] for h in HOOK_NAMES
        }

        # One context per plugin name, lazily created
        self._contexts: Dict[str, PluginContext] = {}
        self._config_snapshot: dict = {}

        self._lock = threading.Lock()
        self._contexts_lock = threading.Lock()

        # ── Security ──
        self.security = PluginSecurity(config or {})
        self._strict_blocker: Optional[StrictImportBlocker] = None
        self._audit_results: Dict[str, List[dict]] = {}  # plugin_name → issues

        # ── P0-1 进程隔离：插件宿主子进程客户端 ──
        self._isolation: str = (config or {}).get("plugin_isolation", "process")
        if self._isolation not in ("process", "inprocess"):
            self._isolation = "process"
        self._host_client: Optional[PluginHostClient] = None
        self._host_ok: bool = False  # 宿主进程是否成功启动

        # ── P0-5 签名校验 ──
        self.signature_verifier = SignatureVerifier(config or {})
        # ── P0-4 网络策略 ──
        self.network_policy = NetworkPolicy(config or {})
        # ── P0-8 人工审批 ──
        self.approval = ApprovalPolicy(config or {})

    # ── Properties ──────────────────────────────────────────────────

    @property
    def plugin_dirs(self) -> List[str]:
        return list(self._plugin_dirs)

    # ── Public API ──────────────────────────────────────────────────

    def set_plugin_dirs(self, dirs: List[str]):
        """Replace the plugin-directory list and reload everything."""
        self._plugin_dirs = [
            os.path.normpath(
                d if os.path.isabs(d) else os.path.join(self.app_dir, d))
            for d in (dirs or [])
        ]
        self.discover_and_load()

    def discover_and_load(self):
        """Scan all ``plugin_dirs`` and load every valid plugin."""
        # Reset state
        with self._lock:
            self._plugins.clear()
            self._tool_registry.clear()
            for h in HOOK_NAMES:
                self._hooks[h].clear()
            self._contexts.clear()
            self._audit_results.clear()

        # ── 清理残留的插件模块，防止重复加载时旧模块副作用累积 ──
        # （例如 norp_pet_bridge 在 import 时会启动宠物 / watchdog 线程，
        #   若不清理 sys.modules，重复 discover_and_load 会导致这些副作用重复执行）
        stale_modules = [m for m in list(sys.modules) if m.startswith("vibe_plugin_")]
        for mod_name in stale_modules:
            sys.modules.pop(mod_name, None)

        # ── 启动进程隔离宿主 / 或设置进程内 import blocker ──
        if self._isolation == "process":
            self._ensure_host_started()
        else:
            self._setup_import_blockers()

        # Deduplicate directories by realpath to prevent scanning the same
        # physical location twice (e.g. "official_plugins" vs "./official_plugins")
        seen_dirs = set()
        unique_dirs = []
        for d in self._plugin_dirs:
            if not os.path.isdir(d):
                continue
            real = os.path.realpath(d)
            if real not in seen_dirs:
                seen_dirs.add(real)
                unique_dirs.append(d)

        # ★ 按插件入口文件的 realpath 去重：同一物理文件（即使通过
        #   嵌套目录 / 符号链接 / 重复配置被扫描到两次）只加载一次，
        #   从根本上杜绝"重复插件 → 重复工具"的问题。
        seen_files: set = set()

        try:
            for d in unique_dirs:
                try:
                    entries = sorted(os.listdir(d))
                except OSError:
                    continue

                for entry in entries:
                    full = os.path.join(d, entry)

                    # ── single .py file ──
                    if entry.endswith(".py") and os.path.isfile(full):
                        if entry == "__init__.py":
                            continue  # skip package init files
                        real_full = os.path.realpath(full)
                        if real_full in seen_files:
                            continue
                        seen_files.add(real_full)
                        self._load_from_file(entry[:-3], full)

                    # ── package with manifest.json ──
                    elif os.path.isdir(full):
                        manifest_path = os.path.join(full, "manifest.json")
                        if os.path.isfile(manifest_path):
                            try:
                                with open(manifest_path, "r", encoding="utf-8") as fh:
                                    manifest = json.load(fh)
                            except Exception:
                                continue
                            name = manifest.get("name", entry)
                            entry_file = manifest.get("entry", "plugin.py")
                            entry_path = os.path.join(full, entry_file)
                            if os.path.isfile(entry_path):
                                real_entry = os.path.realpath(entry_path)
                                if real_entry in seen_files:
                                    continue
                                seen_files.add(real_entry)
                                self._load_from_file(name, entry_path,
                                                     manifest=manifest)
        finally:
            # Always tear down blockers（仅 inprocess 模式需要）
            if self._isolation != "process":
                self._teardown_import_blockers()

    def shutdown(self):
        """Clean up plugin manager resources (call at agent shutdown)."""
        self._teardown_import_blockers()
        if self._host_client is not None:
            try:
                self._host_client.shutdown()
            except Exception:
                pass
            self._host_client = None
            self._host_ok = False
        self._reap_zombies()

    def unload_plugin(self, plugin_name: str) -> bool:
        """Unload a single plugin by name.

        Returns True if the plugin was found and removed, False otherwise.
        Useful for hot-reloading during plugin development.
        """
        with self._lock:
            info = self._plugins.pop(plugin_name, None)
            if info is None:
                return False

            # Remove registered tools
            for tool in (info.tools or []):
                tname = tool.get("function", {}).get("name", "")
                self._tool_registry.pop(tname, None)

            # Remove hooked listeners
            for hook_name in HOOK_NAMES:
                self._hooks[hook_name] = [
                    (pn, fn) for (pn, fn) in self._hooks[hook_name]
                    if pn != plugin_name
                ]

        with self._contexts_lock:
            self._contexts.pop(plugin_name, None)

        self._audit_results.pop(plugin_name, None)

        # Remove module from sys.modules so it can be re-imported fresh
        mod_name = f"vibe_plugin_{plugin_name}"
        sys.modules.pop(mod_name, None)

        _log.info("Unloaded plugin '%s'", plugin_name)
        return True

    # ── Import blocker management ─────────────────────────────────

    def _setup_import_blockers(self):
        """Register import blockers based on current security config."""
        if self.security.audit_level == "off":
            return

        # If strict mode, use StrictImportBlocker
        if self.security.import_restriction == "strict":
            self._strict_blocker = StrictImportBlocker("vibe_plugin_")
            self._strict_blocker.register()
        else:
            self.security.enable_import_blocker()

    def _teardown_import_blockers(self):
        """Unregister all import blockers."""
        self.security.disable_import_blocker()
        if self._strict_blocker:
            self._strict_blocker.unregister()
            self._strict_blocker = None

    def update_security_config(self, config: dict):
        """Update security settings and re-create the security modules."""
        self.security = PluginSecurity(config or {})
        self.signature_verifier = SignatureVerifier(config or {})
        self.network_policy = NetworkPolicy(config or {})
        self.approval = ApprovalPolicy(config or {})
        new_isolation = (config or {}).get("plugin_isolation", "process")
        if new_isolation in ("process", "inprocess"):
            self._isolation = new_isolation

    def get_audit_results(self) -> Dict[str, List[dict]]:
        """Return security audit results for all plugins (keyed by name)."""
        with self._lock:
            return dict(self._audit_results)

    def get_tools(self) -> List[dict]:
        """Return the merged tool definitions from all enabled plugins.

        ★ 按工具名去重：即便注册表里存在残留的重复插件条目，也保证
        同一个工具名只会返回一次，避免 LLM 收到重复的工具定义而产生
        "重复工具调用"。
        """
        tools: List[dict] = []
        seen: set = set()
        with self._lock:
            for info in self._plugins.values():
                if not info.enabled or not info.tools:
                    continue
                for tool in info.tools:
                    tname = tool.get("function", {}).get("name", "")
                    if not tname or tname in seen:
                        continue
                    seen.add(tname)
                    tools.append(tool)
        return tools

    def get_tool_approval_hints(self) -> Dict[str, dict]:
        """返回所有启用插件声明的「工具 → 审批提示」合并表。

        格式：{tool_name: {"approval": "none"|"plugin", "risk": "L0"~"L3"或空}}
        主进程审批层（async_loop）据此精细决定插件工具是否弹窗审批，
        默认（无 hint）行为不变：走 approval_enabled 总开关。
        """
        hints: Dict[str, dict] = {}
        with self._lock:
            for info in self._plugins.values():
                if not info.enabled:
                    continue
                for tname, hint in (info.approval_hints or {}).items():
                    if tname not in hints:
                        hints[tname] = dict(hint)
        return hints

    def get_all_plugins(self) -> List[dict]:
        """Return metadata for every discovered plugin (for the front-end)."""
        result: List[dict] = []
        with self._lock:
            for info in self._plugins.values():
                entry = {
                    "name": info.name,
                    "version": info.version,
                    "publisher": info.publisher,
                    "description": info.description,
                    "enabled": info.enabled,
                    "error": info.error,
                    "path": info.path,
                    "tool_count": len(info.tools) if info.tools else 0,
                    "hook_count": len(info.hook_names),
                    "hook_names": info.hook_names,
                    "signature_status": info.signature_status,
                    "trusted": info.trusted,
                    "isolation": info.isolation,
                }
                # Attach audit results if available
                audit = self._audit_results.get(info.name)
                if audit:
                    entry["audit_issues"] = audit
                    entry["audit_critical"] = sum(
                        1 for i in audit if i.get("severity") == "critical")
                    entry["audit_warning"] = sum(
                        1 for i in audit if i.get("severity") == "warning")
                    entry["audit_info"] = sum(
                        1 for i in audit if i.get("severity") == "info")
                result.append(entry)
        return result

    def execute(self, tool_name: str, args: dict) -> str:
        """Dispatch a tool call to the plugin that registered it."""
        with self._lock:
            entry = self._tool_registry.get(tool_name)
            info = self._plugins.get(entry[0]) if entry else None

        if entry is None:
            return f"Error: unknown plugin tool '{tool_name}'"

        plugin_name, execute_fn = entry

        # ── 进程隔离模式：通过宿主子进程执行 ──
        if info is not None and info.isolation == "process":
            return self._remote_execute(plugin_name, tool_name, args)

        if not callable(execute_fn):
            return f"Error: plugin '{plugin_name}' has no execute() function"

        ctx = self._get_context(plugin_name)
        ctx.current_step = getattr(self, "_step", 0)
        try:
            return execute_fn(tool_name, args, ctx)
        except Exception:
            return f"Plugin execution failed:\n{traceback.format_exc()}"

    # ── 远程执行辅助（进程隔离） ─────────────────────────────────

    def _remote_execute(self, plugin_name: str, tool_name: str, args: dict) -> str:
        client = self._host_client
        if client is None or not client.is_alive():
            return f"Error: plugin host process is not running (plugin '{plugin_name}')"
        try:
            result = client.request({
                "op": "call_tool",
                "plugin": plugin_name,
                "tool": tool_name,
                "args": args,
                "context": self._context_payload(),
            }, timeout=120.0)
            out = result.get("output") if isinstance(result, dict) else result
            return out if isinstance(out, str) else str(out)
        except Exception as exc:
            return f"Plugin execution failed (IPC): {exc}"

    def update_config_snapshot(self, config: dict):
        """Refresh the read-only config snapshot shared with plugins."""
        self._config_snapshot = config.copy() if config else {}

    def set_step(self, step: int):
        self._step = step

    def set_total_usage(self, usage: dict):
        """更新累计 token 用量（传递给子进程插件上下文）。"""
        self._total_usage = usage or {}

    # ── Hook dispatchers (one per hook) ─────────────────────────────

    def fire_agent_init(self):
        self._broadcast("on_agent_init", lambda ctx: ctx)

    def fire_agent_shutdown(self):
        self._broadcast("on_agent_shutdown", lambda ctx: ctx)

    def fire_task_start(self, task_text: str):
        self._broadcast("on_task_start", lambda ctx: (task_text, ctx))

    def fire_task_done(self, summary: str, final_reply: str):
        self._broadcast("on_task_done", lambda ctx: (summary, final_reply, ctx))

    def fire_task_error(self, error_msg: str):
        self._broadcast("on_task_error", lambda ctx: (error_msg, ctx))

    def fire_task_stopped(self):
        self._broadcast("on_task_stopped", lambda ctx: ctx)

    def fire_task_timeout(self, elapsed: float):
        self._broadcast("on_task_timeout", lambda ctx: (elapsed, ctx))

    def fire_before_step(self, step: int, messages: list) -> list:
        """Return (possibly modified) messages list."""
        self.set_step(step)
        result = self._broadcast_mutating(
            "before_step", lambda ctx: (step, messages, ctx))
        if result is not None and isinstance(result, list):
            return result
        return messages

    def fire_after_step(self, step: int, reasoning: str, content: str,
                        tool_calls: list):
        self.set_step(step)
        self._broadcast("after_step",
                         lambda ctx: (step, reasoning, content, tool_calls, ctx))

    def fire_before_tool_call(self, tool_name: str, args: dict) -> Optional[dict]:
        """
        Called right before a tool executes.

        Returns
        -------
        dict or None
            * ``dict`` – (possibly modified) arguments → proceed.
            * ``None`` – the call is **blocked** (only when a listener
              explicitly returns a non-dict sentinel; no listeners = passed through).
        """
        # Short-circuit: no listeners → don't block
        listeners = self._hooks.get("before_tool_call", [])
        if not listeners:
            return args

        result = self._broadcast_mutating(
            "before_tool_call", lambda ctx: (tool_name, args, ctx))
        if result is None:
            return args  # all listeners returned None → pass through
        if isinstance(result, dict):
            # ── 数据变形追踪：before_tool_call 修改了参数 ──
            if result != args:
                self._record_hook_debug("before_tool_call", "(plugin)",
                                        action="mutated", before=args, after=result)
            return result
        return args  # unrecognised return value → pass through

    def fire_after_tool_call(self, tool_name: str, args: dict,
                             result: str) -> str:
        """Return (possibly modified) tool result string."""
        hook_result = self._broadcast_mutating(
            "after_tool_call", lambda ctx: (tool_name, args, result, ctx))
        if hook_result is not None and isinstance(hook_result, str):
            # ── 数据变形追踪：after_tool_call 修改了返回值 ──
            if hook_result != result:
                self._record_hook_debug("after_tool_call", "(plugin)",
                                        action="mutated", before=result, after=hook_result)
            return hook_result
        return result

    def fire_user_input_required(self, question: str):
        self._broadcast("on_user_input_required",
                         lambda ctx: (question, ctx))

    def fire_reasoning(self, token: str):
        self._broadcast("on_reasoning", lambda ctx: (token, ctx))

    def fire_content(self, token: str):
        self._broadcast("on_content", lambda ctx: (token, ctx))

    def fire_event(self, event_type: str, data: str):
        self._broadcast("on_event", lambda ctx: (event_type, data, ctx))

    def fire_usage_update(self, usage: dict):
        self._broadcast("on_usage_update", lambda ctx: (usage, ctx))

    # ── 调试记录 ────────────────────────────────────────────────────

    def _record_hook_debug(self, hook_name: str, plugin_name: str,
                           action: str = "fired", before=None, after=None):
        """将钩子触发记录到调试收集器（模块 4：插件钩子触发记录）。

        惰性 import + 全异常吞掉，确保调试记录绝不影响插件系统主流程。
        """
        try:
            from debug_logger import get_debug_logger
            get_debug_logger(self.app_dir).record_hook(
                hook_name=hook_name, plugin_name=plugin_name,
                action=action, before=before, after=after)
        except Exception:
            pass

    # ── Internal helpers ────────────────────────────────────────────

    # ── 进程隔离 / 安全分级辅助 ──────────────────────────────────

    def _ensure_host_started(self) -> bool:
        """确保插件宿主子进程已启动。"""
        if self._host_client is not None and self._host_ok:
            return True
        client = PluginHostClient(app_dir=self.app_dir, project_root=self.project_root)
        ok = client.start()
        self._host_client = client
        self._host_ok = ok
        if not ok:
            _log.warning("插件宿主子进程启动失败，将拒绝加载插件（进程隔离开启）")
        return ok

    def _effective_security(self, sig_result) -> Tuple[str, str]:
        """根据签名结果决定该插件的有效审计级别与导入限制。

        信任分级：
          - trusted（签名有效且公钥受信任）→ 审计放宽为 warn、导入限制 off
          - 其他（未签名 / 未信任）→ 沿用用户配置（默认 block / strict）
          - invalid 不进入此分支（在 _load_from_file 中已拒绝）
        """
        if sig_result.status == SignatureStatus.TRUSTED:
            return "warn", "off"
        return self.security.audit_level, self.security.import_restriction

    def _context_payload(self) -> dict:
        """构造传给子进程的插件上下文（只读字段）。"""
        return {
            "project_root": self.project_root,
            "app_dir": self.app_dir,
            "config": self._config_snapshot.copy() if self._config_snapshot else {},
            "current_step": getattr(self, "_step", 0),
            "total_usage": getattr(self, "_total_usage", {}),
        }

    def _load_remote(self, info: PluginInfo, name: str, path: str,
                     manifest: dict, import_restrict: str):
        """通过宿主子进程加载插件（进程隔离）。"""
        client = self._host_client
        if client is None:
            raise RuntimeError("插件宿主进程未初始化")
        desc = client.request({
            "op": "load",
            "plugin_name": name,
            "path": path,
            "manifest": manifest,
            "security_config": {
                "audit_level": self.security.audit_level,
                "import_restrict": import_restrict,
            },
            "context": self._context_payload(),
        }, timeout=120.0)

        header_name = desc.get("name", "")
        if isinstance(header_name, str) and header_name.strip():
            info.name = header_name.strip()
        pub = desc.get("publisher")
        if isinstance(pub, str) and pub.strip():
            info.publisher = pub.strip()
        ver = desc.get("version")
        if isinstance(ver, str) and ver.strip() and not manifest:
            info.version = ver.strip()
        dsc = desc.get("description")
        if isinstance(dsc, str) and dsc.strip() and not manifest:
            info.description = dsc.strip()

        tools = desc.get("tools")
        info.tools = tools if isinstance(tools, list) else []
        hooks = desc.get("hook_names")
        info._hook_names = [h for h in hooks if h in HOOK_NAMES] if isinstance(hooks, list) else []
        info.has_execute = bool(desc.get("has_execute", False))
        hints = desc.get("approval_hints")
        info.approval_hints = hints if isinstance(hints, dict) else {}
        info.module = None  # 模块驻留在子进程
        info.isolation = "process"

    def _load_inprocess(self, info: PluginInfo, name: str, path: str,
                        manifest: dict):
        """进程内加载插件（仅 inprocess 隔离模式 / 调试用）。"""
        spec = importlib.util.spec_from_file_location(
            f"vibe_plugin_{name}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load module spec for {path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module

        # ── Resource limits (if enabled) ──
        limiter = None
        if self.security.resource_limit:
            limiter = ResourceLimiter(max_memory_mb=512, max_cpu_seconds=30)
            limiter.enable()

        try:
            _loading_plugin.active = True
            spec.loader.exec_module(module)
        finally:
            _loading_plugin.active = False
            if limiter:
                limiter.disable()

        info.module = module
        info.isolation = "inprocess"

        header_name = getattr(module, "PLUGIN_NAME", None)
        if isinstance(header_name, str) and header_name.strip():
            info.name = header_name.strip()
        pub = getattr(module, "PLUGIN_PUBLISHER", None)
        if isinstance(pub, str):
            info.publisher = pub.strip()
        ver = getattr(module, "PLUGIN_VERSION", None)
        if isinstance(ver, str) and not manifest:
            info.version = ver.strip()
        dsc = getattr(module, "PLUGIN_DESCRIPTION", None)
        if isinstance(dsc, str) and not manifest:
            info.description = dsc.strip()

        tools = getattr(module, "TOOLS", None)
        info.tools = tools if isinstance(tools, list) else []
        info.has_execute = callable(getattr(module, "execute", None))
        raw_hints = getattr(module, "APPROVAL_HINTS", None)
        hints: Dict[str, dict] = {}
        if isinstance(raw_hints, dict):
            for tname, hint in raw_hints.items():
                if not isinstance(tname, str):
                    continue
                if isinstance(hint, str):
                    hints[tname] = {"approval": hint}
                elif isinstance(hint, dict):
                    hints[tname] = {
                        "approval": str(hint.get("approval", "plugin")),
                        "risk": str(hint.get("risk", "")),
                    }
        info.approval_hints = hints
        for hook_name in HOOK_NAMES:
            fn = getattr(module, hook_name, None)
            if callable(fn):
                info._hook_names.append(hook_name)

    def _load_from_file(self, name: str, path: str, *,
                        manifest: dict = None):
        """Load a plugin (isolated host process or in-process) and register it."""
        info = PluginInfo(name, path)
        info.isolation = self._isolation

        if manifest:
            info.version = manifest.get("version", info.version)
            info.publisher = manifest.get("publisher", manifest.get("author", info.publisher))
            info.description = manifest.get("description", info.description)
            if "enabled" in manifest:
                info.enabled = bool(manifest["enabled"])

        if not info.enabled:
            with self._lock:
                if name in self._plugins:
                    _log.debug("Plugin '%s' (disabled) overwrites previously loaded plugin", name)
                self._plugins[name] = info
            return

        # ── P0-5 签名校验 ──
        sig_result = self.signature_verifier.verify(path, manifest=manifest)
        info.signature_status = sig_result.status
        info.trusted = sig_result.is_trusted

        if sig_result.status == SignatureStatus.INVALID:
            info.error = f"插件签名校验失败：{sig_result.reason}"
            info.enabled = False
            with self._lock:
                self._plugins[name] = info
            return

        # ── 信任分级：决定有效审计级别与导入限制 ──
        effective_audit, effective_import_restrict = self._effective_security(sig_result)

        # ── Security audit (before loading) ──
        audit_issues, audit_allowed = self.security.audit_file(path, audit_level=effective_audit)
        self._audit_results[name] = [i.to_dict() for i in audit_issues]

        if not audit_allowed:
            criticals = [i for i in audit_issues if i.severity == Severity.CRITICAL]
            error_lines = "\n".join(
                f"  L{i.line}: [{i.category}] {i.message}"
                for i in criticals[:5]
            )
            info.error = (
                f"Security audit blocked ({len(criticals)} critical issue(s)):\n"
                f"{error_lines}"
            )
            info.enabled = False
            with self._lock:
                self._plugins[name] = info
            return

        # If only warnings, log them but proceed
        if audit_issues and effective_audit == "warn":
            warnings = [i for i in audit_issues if i.severity == Severity.WARNING]
            if warnings:
                print(f"[PluginSecurity] {name}: {len(warnings)} warning(s) "
                      f"({len(audit_issues)} total).  "
                      f"Set audit=block to reject such plugins.")

        # ── Permission check ──
        if manifest and not self.security.check_permissions(manifest, audit_issues):
            info.error = "Missing required permissions (see audit log)"
            info.enabled = False
            with self._lock:
                self._plugins[name] = info
            return

        # ── 加载（隔离模式） ──
        try:
            if self._isolation == "process":
                if not self._ensure_host_started():
                    raise RuntimeError(
                        "进程级隔离已开启但宿主子进程无法启动，已拒绝加载插件。"
                        "可在设置中临时切换为 inprocess 模式（不推荐）后重试。")
                self._load_remote(info, name, path, manifest, effective_import_restrict)
            else:
                self._load_inprocess(info, name, path, manifest)
        except ImportError as exc:
            info.error = f"Import blocked: {exc}"
            info.enabled = False
            traceback.print_exc()
        except Exception as exc:
            info.error = str(exc)
            info.enabled = False
            traceback.print_exc()

        # ── Skip files that don't define any plugin interface ──
        if not info.tools and not info.hook_names and not info.has_execute:
            if not self._audit_results.get(name):
                _log.debug("Skipping '%s' – no TOOLS, hooks, or execute() defined", name)
                return  # not a plugin
            info.enabled = False
            if not info.error:
                info.error = "Plugin module failed to load (see audit log)"

        # ── 通过了所有检查，现在才安全地注册工具和钩子 ──
        resolved_name = info.name

        with self._lock:
            # ── 同名插件覆盖：先卸载旧版本的工具 & 钩子 ──
            if resolved_name in self._plugins:
                old_info = self._plugins[resolved_name]
                _log.warning(
                    "Plugin '%s' (%s) overwrites previously loaded '%s'",
                    resolved_name, info.path, old_info.path,
                )
                for old_tool in (old_info.tools or []):
                    old_tname = old_tool.get("function", {}).get("name", "")
                    if not old_tname:
                        continue
                    entry = self._tool_registry.get(old_tname)
                    if entry is not None and entry[0] == resolved_name:
                        del self._tool_registry[old_tname]
                for hook_name in HOOK_NAMES:
                    self._hooks[hook_name] = [
                        (pn, fn) for (pn, fn) in self._hooks[hook_name]
                        if pn != resolved_name
                    ]

            # 注册工具（process 模式下 execute_fn 为 None，走 RPC）
            for tool in info.tools:
                func = tool.get("function", {})
                tname = func.get("name", "")
                if tname:
                    if tname in self._tool_registry:
                        existing = self._tool_registry[tname][0]
                        if existing != resolved_name:
                            _log.warning(
                                "Tool '%s' from plugin '%s' (%s) skipped: "
                                "already registered by plugin '%s'",
                                tname, resolved_name, info.path, existing,
                            )
                        continue
                    execute_fn = getattr(info.module, "execute", None)
                    self._tool_registry[tname] = (resolved_name, execute_fn)

            # 注册钩子（process 模式下 fn 为 None，走 RPC）
            for hook_name in info._hook_names:
                fn = getattr(info.module, hook_name, None) if info.module is not None else None
                already = any(
                    pn == resolved_name
                    for pn, _ in self._hooks[hook_name]
                )
                if not already:
                    self._hooks[hook_name].append((resolved_name, fn))

            # 加入插件注册表
            self._plugins[resolved_name] = info

    def _get_context(self, plugin_name: str) -> PluginContext:
        """Return (or lazily create) the PluginContext for *plugin_name*.

        Thread-safe: ``_contexts`` dict mutations are guarded by
        ``_contexts_lock`` to prevent TOCTOU races when multiple hooks
        fire simultaneously (e.g. streaming token + tool call).
        """
        with self._contexts_lock:
            if plugin_name not in self._contexts:
                self._contexts[plugin_name] = PluginContext(
                    plugin_name=plugin_name,
                    project_root=self.project_root,
                    app_dir=self.app_dir,
                    config=self._config_snapshot,
                )
            ctx = self._contexts[plugin_name]
        # Always refresh the config snapshot (safe – PluginContext fields are
        # independently mutable and this is a simple attribute assignment)
        ctx.config = self._config_snapshot.copy() if self._config_snapshot else {}
        return ctx

    def _broadcast(self, hook_name: str, build_args: Callable):
        """Call every listener for *hook_name* (fire-and-forget)."""
        listeners: List[Tuple[str, Callable]] = []
        with self._lock:
            listeners = list(self._hooks.get(hook_name, []))

        for plugin_name, fn in listeners:
            self._record_hook_debug(hook_name, plugin_name, "fired")
            # ── 进程隔离模式：RPC 触发钩子 ──
            if fn is None:
                self._remote_fire(plugin_name, hook_name, build_args)
                continue
            ctx = self._get_context(plugin_name)
            try:
                args = build_args(ctx)
                if isinstance(args, tuple):
                    self._call_with_timeout(fn, *args)
                else:
                    self._call_with_timeout(fn, args)
            except Exception:
                pass  # hook errors never crash the agent

    def _broadcast_mutating(self, hook_name: str, build_args: Callable):
        """
        Like _broadcast, but the **first** non-None return value wins.
        Used for hooks that can modify the data flow.
        """
        listeners: List[Tuple[str, Callable]] = []
        with self._lock:
            listeners = list(self._hooks.get(hook_name, []))

        for plugin_name, fn in listeners:
            self._record_hook_debug(hook_name, plugin_name, "fired")
            # ── 进程隔离模式：RPC 触发钩子并取返回值 ──
            if fn is None:
                result = self._remote_fire_mutating(plugin_name, hook_name, build_args)
                if result is not None:
                    return result
                continue
            ctx = self._get_context(plugin_name)
            try:
                args = build_args(ctx)
                if isinstance(args, tuple):
                    result = self._call_with_timeout(fn, *args)
                else:
                    result = self._call_with_timeout(fn, args)
                if result is not None:
                    return result
            except Exception:
                continue
        return None

    # ── 远程钩子触发辅助（进程隔离） ───────────────────────────────

    @staticmethod
    def _strip_ctx_from_args(args) -> list:
        """把 build_args 产物中末尾的 PluginContext 对象去掉。

        子进程会 append 自己的 PluginContext 作为钩子最后一个参数，
        主进程侧的 ctx 对象不可 JSON 序列化，必须在此剥离。
        """
        if isinstance(args, tuple):
            args_list = list(args)
        elif args is not None:
            args_list = [args]
        else:
            args_list = []
        if args_list and isinstance(args_list[-1], PluginContext):
            args_list = args_list[:-1]
        return args_list

    def _remote_fire(self, plugin_name: str, hook_name: str, build_args: Callable):
        client = self._host_client
        if client is None or not client.is_alive():
            return
        ctx = self._get_context(plugin_name)
        try:
            args = build_args(ctx)
        except Exception:
            return
        args_list = self._strip_ctx_from_args(args)
        try:
            client.request({
                "op": "fire_hook",
                "plugin": plugin_name,
                "hook": hook_name,
                "args": args_list,
                "context": self._context_payload(),
            }, timeout=HOOK_TIMEOUT)
        except Exception:
            pass  # hook errors never crash the agent

    def _remote_fire_mutating(self, plugin_name: str, hook_name: str,
                              build_args: Callable):
        client = self._host_client
        if client is None or not client.is_alive():
            return None
        ctx = self._get_context(plugin_name)
        try:
            args = build_args(ctx)
        except Exception:
            return None
        args_list = self._strip_ctx_from_args(args)
        try:
            result = client.request({
                "op": "fire_hook",
                "plugin": plugin_name,
                "hook": hook_name,
                "args": args_list,
                "context": self._context_payload(),
            }, timeout=HOOK_TIMEOUT)
            if isinstance(result, dict):
                return result.get("result")
            return result
        except Exception:
            return None

    @staticmethod
    def _call_with_timeout(fn: Callable, *args, **kwargs):
        """Call *fn* in a daemon thread with a hard timeout.

        If the hook times out the thread is tracked so it can be reaped
        later (prevents zombie-thread accumulation).
        """
        result_holder = [None]
        error_holder: List[Optional[Exception]] = [None]
        done = threading.Event()

        def _target():
            try:
                result_holder[0] = fn(*args, **kwargs)
            except Exception as exc:
                error_holder[0] = exc
            done.set()

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        done.wait(timeout=HOOK_TIMEOUT)

        if not done.is_set():
            # Timeout – track for later cleanup
            _log.warning(
                "Hook %s timed out after %.1fs – thread abandoned (will be reaped at shutdown)",
                getattr(fn, '__name__', str(fn)), HOOK_TIMEOUT,
            )
            with _zombie_lock:
                _zombie_threads.append(t)
            return None

        if error_holder[0] is not None:
            raise error_holder[0]

        return result_holder[0]

    @staticmethod
    def _reap_zombies():
        """Try to join all abandoned hook threads (call at shutdown)."""
        with _zombie_lock:
            threads = _zombie_threads[:]
            _zombie_threads.clear()
        for t in threads:
            t.join(timeout=2.0)
        remaining = sum(1 for t in threads if t.is_alive())
        if remaining:
            _log.warning("%d zombie hook thread(s) could not be joined", remaining)

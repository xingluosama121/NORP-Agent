# Copyright (c) 2026 xingluosama121, MIT Licensed
"""外部插件加载器：把现有应用的插件安全管线迁移为 pip 库能力。

管线（与现有应用 plugin_system.manager 一致）：

    发现（单文件 / manifest 包）→ 签名校验 → AST 审计（block 级拒绝）
    → 权限声明校验 → 导入限制下加载模块 → 适配为 Plugin 协议
    → 注册进 Registry（工具入工具表、钩子订阅事件总线）

支持的插件格式与现有应用完全兼容：

- 模块级常量：PLUGIN_NAME / PLUGIN_PUBLISHER / PLUGIN_VERSION /
  PLUGIN_DESCRIPTION；
- ``TOOLS``：OpenAI function schema 列表 + ``execute(tool_name, args, ctx)``；
- 15 个钩子函数（on_task_start / before_step / after_tool_call 等）；
- ``APPROVAL_HINTS``：工具 → 审批提示（approval=none 免审批）。

配置键（config dict）沿用现有应用：

- plugin_security_audit: off / warn / block（默认 warn）
- plugin_security_import_restrict: off / safe / strict（默认 off）
- plugin_security_require_permissions: bool（默认 False）
- plugin_signature_verify: bool（默认 True）
- plugin_trusted_keys: list[str]
- plugin_network_policy: deny / audited_public / public_only / allow_all
- approval_enabled: bool（默认 True）

用法::

    from norpagent.plugins import install_plugin_dirs
    loader = install_plugin_dirs(registry, ["/path/to/plugins"], config={})
    for info in loader.plugins:
        print(info.name, info.signature_status, info.enabled)
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from norpagent.protocols.tool import Tool, ToolResult
from norpagent.security.audit import Severity, SourceAuditor
from norpagent.security.approval import ApprovalPolicy
from norpagent.security.network_policy import NetworkPolicy
from norpagent.security.signature import SignatureResult, SignatureStatus, SignatureVerifier

# 与现有应用 15 个 hook 对齐（事件名一致，见 kernel/events.py）
HOOK_NAMES = [
    "on_agent_init",
    "on_agent_shutdown",
    "on_task_start",
    "on_task_done",
    "on_task_error",
    "on_task_stopped",
    "on_task_timeout",
    "before_step",
    "after_step",
    "before_tool_call",
    "after_tool_call",
    "on_user_input_required",
    "on_reasoning",
    "on_content",
    "on_event",
    "on_usage_update",
]

_MUTATING_HOOKS = {"before_step", "before_tool_call", "after_tool_call"}

# 插件加载管线钩子（动态钩子：经 registry.hooks 自动注册，
# 属于钩子体系的「自定义钩子」能力，PluginSystem 安装同名自定义层）
PIPELINE_HOOK_NAMES = [
    "before_plugin_discover",
    "after_plugin_discover",
    "before_plugin_load",
    "after_plugin_load",
    "before_plugin_audit",
    "after_plugin_audit",
    "before_plugin_register",
    "after_plugin_register",
]

# 模块名命名空间：外部插件加载后的模块名统一为 norpagent_ext_<name>
PLUGIN_MODULE_PREFIX = "norpagent_ext_"

_loading_plugin = threading.local()

# 导入限制（与现有应用一致）
ALWAYS_BLOCKED = {"ctypes", "cffi"}

SAFE_MODULES: Set[str] = {
    "json", "re", "datetime", "math", "random", "collections",
    "itertools", "functools", "typing", "enum", "dataclasses",
    "pathlib", "os.path", "glob", "fnmatch",
    "textwrap", "string", "hashlib", "base64", "binascii",
    "traceback", "logging", "warnings",
    "copy", "pprint", "inspect", "contextlib",
    "uuid", "time", "calendar", "zoneinfo",
    "csv", "io", "tempfile", "shutil",
    "html", "xml.etree.ElementTree", "xml",
    "struct", "codecs", "unicodedata",
    "fractions", "decimal", "statistics",
    "norpagent", "norpagent.protocols", "norpagent.protocols.tool",
}

STRICT_SAFE_MODULES: Set[str] = {
    "json", "re", "datetime", "math", "random",
    "collections", "itertools", "functools", "typing", "enum",
    "pathlib", "os.path",
    "textwrap", "string", "hashlib", "base64",
    "traceback", "logging", "warnings", "copy",
    "uuid", "time",
    "norpagent.protocols.tool",
}

DANGEROUS_IMPORTS_FOR_BLOCK = {
    "subprocess", "ctypes", "cffi", "socket", "pickle", "marshal",
    "telnetlib", "ftplib", "smtplib",
}


@dataclass
class PluginContext:
    """传给外部插件钩子 / execute 的上下文（与现有应用 PluginContext 对齐）。"""

    plugin_name: str = ""
    project_root: str = ""
    app_dir: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    current_step: int = 0
    total_usage: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginInfo:
    """一个外部插件的加载结果元数据。"""

    name: str
    path: str
    version: str = "0.0.0"
    publisher: str = ""
    description: str = ""
    enabled: bool = True
    error: str = ""
    tools: List[str] = field(default_factory=list)
    hook_names: List[str] = field(default_factory=list)
    signature_status: str = ""
    trusted: bool = False
    approval_hints: Dict[str, dict] = field(default_factory=dict)
    audit_issues: List[dict] = field(default_factory=list)
    module: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "version": self.version,
            "publisher": self.publisher,
            "description": self.description,
            "enabled": self.enabled,
            "error": self.error,
            "tools": list(self.tools),
            "hook_names": list(self.hook_names),
            "signature_status": self.signature_status,
            "trusted": self.trusted,
            "approval_hints": dict(self.approval_hints),
            "audit_issues": list(self.audit_issues),
        }


# ── 导入限制器（与现有应用 PluginImportBlocker / StrictImportBlocker 一致）─


class _ImportBlocker:
    """sys.meta_path 导入限制器：仅对插件模块生效（栈帧探测）。"""

    def __init__(self, blocked: Set[str], strict: bool = False) -> None:
        self.blocked = blocked
        self.strict = strict
        self._registered = False

    def register(self) -> None:
        if self._registered:
            return
        if self not in sys.meta_path:  # type: ignore[comparison-overlap]
            sys.meta_path.insert(0, self)  # type: ignore[arg-type]
        self._registered = True

    def unregister(self) -> None:
        if not self._registered:
            return
        try:
            sys.meta_path.remove(self)  # type: ignore[arg-type]
        except ValueError:
            pass
        self._registered = False

    def find_spec(self, fullname: str, path=None, target=None):
        if self.strict:
            allowed = fullname in STRICT_SAFE_MODULES or any(
                fullname.startswith(m + ".") for m in STRICT_SAFE_MODULES
            )
            if not allowed:
                if self._caller_is_plugin():
                    raise ImportError(
                        f"插件安全（strict）: 不允许导入 '{fullname}'"
                        "（不在安全模块白名单内）"
                    )
                return None
            return None

        should_block = fullname in self.blocked or any(
            fullname == b or fullname.startswith(b + ".") for b in self.blocked
        )
        if not should_block:
            should_block = any(
                fullname == ab or fullname.startswith(ab + ".")
                for ab in ALWAYS_BLOCKED
            )
        if not should_block:
            return None
        if self._caller_is_plugin():
            raise ImportError(
                f"插件安全: 禁止导入 '{fullname}'（该模块不允许插件代码使用）"
            )
        return None

    @staticmethod
    def _caller_is_plugin() -> bool:
        if getattr(_loading_plugin, "active", False):
            return True
        try:
            frame = sys._getframe()
            depth = 0
            while frame is not None and depth < 80:
                mod_name = frame.f_globals.get("__name__", "")
                if mod_name.startswith(PLUGIN_MODULE_PREFIX):
                    return True
                frame = frame.f_back
                depth += 1
        except Exception:
            pass
        return False


# ── 旧格式插件适配器（TOOLS + execute + 15 钩子 → Plugin 协议）───


class _LegacyToolAdapter:
    """把一个 TOOLS schema + execute 入口适配为 Tool 协议。"""

    def __init__(self, name: str, schema: Dict[str, Any],
                 plugin_name: str, execute_fn: Optional[Callable],
                 loader: "PluginLoader"):
        self.name = name
        self._schema = schema
        self._plugin_name = plugin_name
        self._execute_fn = execute_fn
        self._loader = loader

    def schema(self) -> Dict[str, Any]:
        return self._schema

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        if self._execute_fn is None:
            return ToolResult(
                output=f"插件 '{self._plugin_name}' 未定义 execute() 函数",
                success=False,
                error="no_execute",
            )
        plugin_ctx = self._loader.plugin_context(self._plugin_name, ctx)
        try:
            output = self._execute_fn(self.name, args or {}, plugin_ctx)
            if isinstance(output, ToolResult):
                return output
            return ToolResult(output=str(output))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                output=f"插件工具执行异常: {type(exc).__name__}: {exc}",
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )


# 事件 payload → 旧格式钩子参数 的转换表
# 值 = 从 AgentEvent.payload 取哪些键，按顺序传给钩子（ctx 追加在末尾）
_HOOK_ARG_KEYS: Dict[str, Tuple[str, ...]] = {
    "on_agent_init": (),
    "on_agent_shutdown": (),
    "on_task_start": ("user_input",),
    "on_task_done": ("content",),
    "on_task_error": ("error",),
    "on_task_stopped": ("reason",),
    "on_task_timeout": ("timeout",),
    "before_step": ("step", "messages"),
    "after_step": ("step", "content", "tool_calls"),
    "before_tool_call": ("tool_name", "args"),
    "after_tool_call": ("tool_name", "args"),
    "on_user_input_required": ("question",),
    "on_reasoning": ("content",),
    "on_content": ("content",),
    "on_event": ("event_type", "data"),
    "on_usage_update": ("total",),
}


def _build_hook_args(hook_name: str, payload: Dict[str, Any]) -> List[Any]:
    """按旧格式钩子签名约定，从事件 payload 构建位置参数列表。

    旧格式签名：业务参数在前，PluginContext 在最后（由调用方追加）。
    与现有应用 plugin_system 的派发逻辑完全一致。
    """
    keys = _HOOK_ARG_KEYS.get(hook_name, ())
    args: List[Any] = [payload.get(k) for k in keys]
    if hook_name == "after_tool_call":
        # 旧格式: (tool_name, args, result, ctx)
        args.append(str(payload.get("result") or ""))
    elif hook_name == "on_task_done":
        # 旧格式: (summary, final_reply, ctx)
        args.append(payload.get("content") or "")
    elif hook_name == "after_step":
        # 旧格式: (step, reasoning, content, tool_calls, ctx)
        args = [
            payload.get("step", 0),
            payload.get("content", ""),
            payload.get("tool_calls") or [],
        ]
    elif hook_name == "on_usage_update":
        args = [{
            "input": payload.get("input", 0),
            "output": payload.get("output", 0),
            "total": payload.get("total", 0),
        }]
    return args


def _wrap_hook(hook_name: str, fn: Callable, loader: "PluginLoader",
               plugin_name: str) -> Callable:
    """把旧格式钩子函数包装成 EventBus 订阅者（接收 AgentEvent）。

    旧格式签名约定：业务参数在前，PluginContext 在最后；
    可变钩子（before_step / before_tool_call / after_tool_call）的返回值
    通过 EventBus.intercept 透传给内核；其余钩子的返回值忽略。
    """

    def listener(event: Any) -> Any:
        payload = getattr(event, "payload", {}) or {}
        args = _build_hook_args(hook_name, payload)
        ctx = loader.plugin_context(plugin_name, payload.get("context"))
        args.append(ctx)
        try:
            return fn(*args)
        except Exception:
            return None

    return listener


class _LegacyPluginAdapter:
    """旧格式插件模块 → Plugin 协议适配器。"""

    def __init__(self, info: PluginInfo, module: Any, loader: "PluginLoader"):
        self.info = info
        self.module = module
        self.loader = loader
        self.name = info.name
        self.version = info.version
        self.publisher = info.publisher
        self.description = info.description

    def get_tools(self) -> List[Tool]:
        tools: List[Tool] = []
        raw_tools = getattr(self.module, "TOOLS", None) or []
        execute_fn = getattr(self.module, "execute", None)
        if not callable(execute_fn):
            execute_fn = None
        for tool_def in raw_tools:
            func = tool_def.get("function", {}) if isinstance(tool_def, dict) else {}
            tname = func.get("name", "")
            if tname:
                tools.append(_LegacyToolAdapter(
                    tname, tool_def, self.info.name, execute_fn, self.loader,
                ))
        return tools

    def get_hooks(self) -> Dict[str, Callable]:
        hooks: Dict[str, Callable] = {}
        for hook_name in HOOK_NAMES:
            fn = getattr(self.module, hook_name, None)
            if callable(fn):
                hooks[hook_name] = _wrap_hook(hook_name, fn, self.loader, self.info.name)
        return hooks

    def execute(self, tool_name: str, args: Dict[str, Any], ctx: Any) -> Optional[str]:
        fn = getattr(self.module, "execute", None)
        if not callable(fn):
            return None
        plugin_ctx = self.loader.plugin_context(self.info.name, ctx)
        output = fn(tool_name, args or {}, plugin_ctx)
        return None if output is None else str(output)


# ── 进程隔离插件适配器（P4：插件代码只存在于宿主子进程）────


def _plugin_ctx_dict(plugin_ctx: PluginContext) -> Dict[str, Any]:
    """PluginContext → 可 JSON 序列化的 dict（RPC 传输）。"""
    return {
        "plugin_name": plugin_ctx.plugin_name,
        "project_root": plugin_ctx.project_root,
        "app_dir": plugin_ctx.app_dir,
        "config": plugin_ctx.config,
        "current_step": plugin_ctx.current_step,
        "total_usage": plugin_ctx.total_usage,
    }


class _RemoteToolAdapter:
    """进程隔离插件的工具：执行经 RPC 转发到插件宿主子进程。"""

    def __init__(self, name: str, schema: Dict[str, Any],
                 plugin_name: str, manager: Any, loader: "PluginLoader"):
        self.name = name
        self._schema = schema
        self._plugin_name = plugin_name
        self._manager = manager
        self._loader = loader

    def schema(self) -> Dict[str, Any]:
        return self._schema

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        plugin_ctx = self._loader.plugin_context(self._plugin_name, ctx)
        try:
            output = self._manager.call_tool(
                self._plugin_name, self.name, args or {},
                _plugin_ctx_dict(plugin_ctx),
            )
            if isinstance(output, ToolResult):
                return output
            return ToolResult(output=str(output))
        except Exception as exc:  # noqa: BLE001 — 宿主死亡/超时/插件异常
            return ToolResult(
                output=f"插件工具执行异常（进程隔离）: {type(exc).__name__}: {exc}",
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )


class _RemotePluginAdapter:
    """进程隔离插件：工具走 RPC，钩子经宿主子进程转发回主进程。"""

    def __init__(self, info: PluginInfo, tool_schemas: List[Dict[str, Any]],
                 hook_names: List[str], manager: Any, loader: "PluginLoader"):
        self.info = info
        self.name = info.name
        self.version = info.version
        self.publisher = info.publisher
        self.description = info.description
        self._tool_schemas = tool_schemas
        self._hook_names = hook_names
        self._manager = manager
        self._loader = loader

    def get_tools(self) -> List[Tool]:
        tools: List[Tool] = []
        for tool_def in self._tool_schemas:
            func = tool_def.get("function", {}) if isinstance(tool_def, dict) else {}
            tname = func.get("name", "")
            if tname:
                tools.append(_RemoteToolAdapter(
                    tname, tool_def, self.name, self._manager, self._loader,
                ))
        return tools

    def get_hooks(self) -> Dict[str, Callable]:
        """钩子桥接：事件 → 参数列表 → 宿主子进程 fire_hook RPC。

        可变钩子的返回值经 EventBus.intercept 透传给内核；
        fire_hook 在守护线程中限时执行（HOOK_TIMEOUT），
        超时放弃返回 None——插件钩子永不拖死主循环。
        """
        loader = self._loader
        manager = self._manager
        plugin_name = self.name

        def bridge(hook_name: str, fn_self: "_RemotePluginAdapter" = None):
            def listener(event: Any) -> Any:
                payload = getattr(event, "payload", {}) or {}
                args = _build_hook_args(hook_name, payload)
                plugin_ctx = loader.plugin_context(
                    plugin_name, payload.get("context")
                )
                return manager.fire_hook(
                    plugin_name, hook_name, args,
                    _plugin_ctx_dict(plugin_ctx),
                )

            return listener

        hooks: Dict[str, Callable] = {}
        for hook_name in self._hook_names:
            hooks[hook_name] = bridge(hook_name)
        return hooks

    def execute(self, tool_name: str, args: Dict[str, Any], ctx: Any) -> Optional[str]:
        plugin_ctx = self._loader.plugin_context(self.name, ctx)
        try:
            output = self._manager.call_tool(
                self.name, tool_name, args or {}, _plugin_ctx_dict(plugin_ctx),
            )
            return None if output is None else str(output)
        except Exception:
            return None


# ── 加载器 ────────────────────────────────────────────────


class PluginLoader:
    """外部插件加载器：安全管线 + 注册到 Registry。

    安全管线（每个阶段都是 registry.hooks 上的动态钩子，
    可订阅 / 可 HookVeto 一票否决）：

        发现 -> [签名校验 -> AST 审计 -> 权限声明 -> 导入限制下加载]
             -> 适配为 Plugin 协议 -> 注册进 Registry

    P4 起支持进程级插件隔离：``plugin_isolation: auto/inproc/process``
    或插件模块级 ``ISOLATION = "process"``（auto 时生效），
    选择隔离的插件在宿主子进程中加载执行（见 norpagent.plugins.host）。
    """

    def __init__(self, plugin_dirs: List[str], config: Optional[dict] = None) -> None:
        config = config or {}
        self._plugin_dirs = [os.path.abspath(d) for d in plugin_dirs]
        self.config = config
        self.auditor = SourceAuditor(
            config.get("plugin_security_audit", "warn")
        )
        self.import_restriction = config.get("plugin_security_import_restrict", "off")
        self.require_permissions = bool(
            config.get("plugin_security_require_permissions", False)
        )
        self.signature_required = bool(
            config.get("plugin_signature_required", False)
        )
        self.plugin_isolation = str(
            config.get("plugin_isolation", "auto")
        )
        self.signature_verifier = SignatureVerifier(config)
        self.network_policy = NetworkPolicy(config)
        self.approval = ApprovalPolicy(config)
        self.plugins: List[PluginInfo] = []
        self._lock = threading.Lock()
        self._contexts: Dict[str, PluginContext] = {}
        self._isolation_manager: Any = None
        self._registry: Any = None

    # ── 钩子辅助 ─────────────────────────────────────────

    def _emit(self, hook_name: str, **payload: Any) -> None:
        """向 registry.hooks 发布管线事件（动态钩子，未定义自动注册）。"""
        if self._registry is not None:
            try:
                self._registry.hooks.hook(hook_name).emit(**payload)
            except Exception:
                pass

    def _veto(self, hook_name: str, **payload: Any) -> Optional[str]:
        """可变管线钩子：订阅者抛 HookVeto 返回其 reason。"""
        if self._registry is None:
            return None
        from norpagent.hooks.core import HookVeto

        try:
            self._registry.hooks.hook(hook_name).intercept(**payload)
            return None
        except HookVeto as veto:
            return str(veto)
        except Exception:
            return None

    # ── 进程隔离管理 ─────────────────────────────────────

    def isolation_manager(self) -> Any:
        """取（或惰性创建）进程隔离管理器（所有隔离插件共用宿主子进程）。"""
        if self._isolation_manager is None:
            from norpagent.plugins.isolation import ProcessIsolationManager

            self._isolation_manager = ProcessIsolationManager(
                security_config={
                    "audit_level": self.auditor.audit_level,
                    "import_restrict": self.import_restriction,
                },
            )
        return self._isolation_manager

    # ── 主流程 ──────────────────────────────────────────

    def discover_and_load(self, registry: Any) -> List[PluginInfo]:
        """扫描全部插件目录并注册到 Registry，返回插件元数据列表。"""
        with self._lock:
            self._registry = registry
            self.plugins.clear()
            self._emit("before_plugin_discover", dirs=list(self._plugin_dirs))
            seen_files: Set[str] = set()
            for d in self._plugin_dirs:
                if not os.path.isdir(d):
                    continue
                for entry in sorted(os.listdir(d)):
                    full = os.path.join(d, entry)
                    if entry.endswith(".py") and os.path.isfile(full):
                        if entry == "__init__.py":
                            continue
                        real = os.path.realpath(full)
                        if real in seen_files:
                            continue
                        seen_files.add(real)
                        self._load_from_file(registry, entry[:-3], full, manifest=None)
                    elif os.path.isdir(full):
                        manifest_path = os.path.join(full, "manifest.json")
                        if not os.path.isfile(manifest_path):
                            continue
                        try:
                            with open(manifest_path, "r", encoding="utf-8") as fh:
                                manifest = json.load(fh)
                        except Exception:
                            continue
                        if not isinstance(manifest, dict):
                            continue
                        name = manifest.get("name", entry)
                        entry_file = manifest.get("entry", "plugin.py")
                        entry_path = os.path.join(full, entry_file)
                        if not os.path.isfile(entry_path):
                            continue
                        real = os.path.realpath(entry_path)
                        if real in seen_files:
                            continue
                        seen_files.add(real)
                        self._load_from_file(registry, name, entry_path, manifest=manifest)
            self._emit(
                "after_plugin_discover",
                loaded=len(self.plugins),
                enabled=sum(1 for p in self.plugins if p.enabled),
            )
            return list(self.plugins)

    def plugin_context(self, plugin_name: str, run_ctx: Any = None) -> PluginContext:
        """构造（或取缓存）插件上下文。"""
        if plugin_name not in self._contexts:
            workspace = ""
            if run_ctx is not None:
                params = getattr(run_ctx, "params", {}) or {}
                workspace = str(params.get("workspace_root", ""))
            self._contexts[plugin_name] = PluginContext(
                plugin_name=plugin_name,
                project_root=workspace,
                app_dir=os.path.expanduser("~"),
                config=dict(self.config),
            )
        ctx = self._contexts[plugin_name]
        if run_ctx is not None:
            params = getattr(run_ctx, "params", {}) or {}
            if params.get("workspace_root"):
                ctx.project_root = str(params["workspace_root"])
        return ctx

    def approval_hints(self) -> Dict[str, dict]:
        """合并全部启用插件的 APPROVAL_HINTS（工具 → 审批提示）。"""
        hints: Dict[str, dict] = {}
        for info in self.plugins:
            if not info.enabled:
                continue
            for tname, hint in (info.approval_hints or {}).items():
                if tname not in hints and isinstance(hint, dict):
                    hints[tname] = dict(hint)
        return hints

    # ── 单个插件加载（安全管线）───────────────────────────

    def _load_from_file(self, registry: Any, name: str, path: str,
                        manifest: Optional[dict]) -> None:
        info = PluginInfo(name=name, path=path)

        veto = self._veto("before_plugin_load", name=name, path=path)
        if veto is not None:
            info.enabled = False
            info.error = f"插件加载被钩子否决: {veto}"
            self.plugins.append(info)
            return
        self._emit("before_plugin_load", name=name, path=path)

        # ── 1. 签名校验（与现有应用一致：invalid 拒绝） ──
        sig: SignatureResult = self.signature_verifier.verify(path, manifest)
        info.signature_status = sig.status
        info.trusted = sig.is_trusted
        if sig.status == SignatureStatus.INVALID:
            info.enabled = False
            info.error = f"插件签名校验失败: {sig.reason}"
            self.plugins.append(info)
            return
        # P4 新能力：signature_required（safe(level="high") 开启）——
        # 仅受信任签名的插件可加载，未签名/不可用/不受信任一律拒绝
        if self.signature_required and sig.status != SignatureStatus.TRUSTED:
            info.enabled = False
            info.error = (
                f"安全策略要求受信任签名（当前: {sig.status}）: {sig.reason}"
            )
            self.plugins.append(info)
            return

        # ── 2. 信任分级：受信任签名放宽审计 ──
        effective_audit = "warn" if sig.is_trusted else self.auditor.audit_level

        # ── 3. AST 审计 ──
        self._emit("before_plugin_audit", name=name, path=path,
                   audit_level=effective_audit)
        issues, allowed = self.auditor.audit_file(path, audit_level=effective_audit)
        info.audit_issues = [i.to_dict() for i in issues]
        self._emit(
            "after_plugin_audit", name=name, path=path,
            allowed=allowed, issues=len(issues),
        )
        if not allowed:
            criticals = [i for i in issues if i.severity == Severity.CRITICAL]
            error_lines = "\n".join(
                f"  L{i.line}: [{i.category}] {i.message}" for i in criticals[:5]
            )
            info.enabled = False
            info.error = (
                f"安全审计拦截（{len(criticals)} 个 critical）:\n{error_lines}"
            )
            self.plugins.append(info)
            return

        # ── 4. 权限声明校验 ──
        if self.require_permissions and manifest:
            if not self.auditor.check_permissions(manifest, issues):
                info.enabled = False
                info.error = "缺少权限声明（manifest.json → permissions）"
                self.plugins.append(info)
                return

        # ── 5. 隔离决策：进程隔离走宿主子进程，不进主进程 ──
        isolation = self._decide_isolation(path, manifest)
        if isolation == "process":
            self._load_remote(registry, info, path, manifest)
            return

        # ── 6. 导入限制下加载模块（进程内路径） ──
        module = self._exec_module(info, path)
        if module is None:
            # _exec_module 内部已填 error
            self.plugins.append(info)
            return

        # ── 7. 读取元数据与接口 ──
        self._fill_metadata(info, module, manifest)

        # ── 8. 适配并注册进 Registry ──
        veto = self._veto(
            "before_plugin_register", name=info.name,
            tools=info.tools, isolation="inproc",
        )
        if veto is not None:
            info.enabled = False
            info.error = f"插件注册被钩子否决: {veto}"
            self.plugins.append(info)
            return
        adapter = _LegacyPluginAdapter(info, module, self)
        registry.register_plugin(adapter)
        info.module = module
        self.plugins.append(info)
        self._emit(
            "after_plugin_register", name=info.name,
            enabled=info.enabled, tools=info.tools, isolation="inproc",
        )

    def _load_remote(self, registry: Any, info: PluginInfo, path: str,
                     manifest: Optional[dict]) -> None:
        """进程隔离路径：插件模块只在宿主子进程中加载。"""
        try:
            manager = self.isolation_manager()
            meta = manager.load(
                info.name, path,
                self._base_ctx_dict(info.name),
                security_config={
                    "audit_level": self.auditor.audit_level,
                    "import_restrict": self.import_restriction,
                },
            )
        except Exception as exc:  # noqa: BLE001
            info.enabled = False
            info.error = f"插件宿主子进程加载失败: {type(exc).__name__}: {exc}"
            self.plugins.append(info)
            return

        header_name = meta.get("name") or info.name
        if isinstance(header_name, str) and header_name.strip():
            info.name = header_name.strip()
        if manifest:
            info.version = manifest.get("version", meta.get("version", info.version))
            info.publisher = manifest.get(
                "publisher",
                manifest.get("author", meta.get("publisher", info.publisher)),
            )
            info.description = manifest.get(
                "description", meta.get("description", info.description),
            )
        else:
            info.version = str(meta.get("version") or info.version)
            info.publisher = str(meta.get("publisher") or "")
            info.description = str(meta.get("description") or "")

        raw_tools = meta.get("tools") or []
        info.tools = [
            t.get("function", {}).get("name", "")
            for t in raw_tools
            if isinstance(t, dict) and t.get("function", {}).get("name")
        ]
        info.hook_names = list(meta.get("hook_names") or [])
        hints = meta.get("approval_hints") or {}
        if isinstance(hints, dict):
            for tname, hint in hints.items():
                if isinstance(tname, str) and isinstance(hint, dict):
                    info.approval_hints[tname] = {
                        "approval": str(hint.get("approval", "plugin")),
                        "risk": str(hint.get("risk", "")),
                    }

        veto = self._veto(
            "before_plugin_register", name=info.name,
            tools=info.tools, isolation="process",
        )
        if veto is not None:
            info.enabled = False
            info.error = f"插件注册被钩子否决: {veto}"
            self.plugins.append(info)
            return
        adapter = _RemotePluginAdapter(
            info, raw_tools, info.hook_names,
            self.isolation_manager(), self,
        )
        registry.register_plugin(adapter)
        self.plugins.append(info)
        self._emit(
            "after_plugin_register", name=info.name,
            enabled=info.enabled, tools=info.tools, isolation="process",
        )

    def _base_ctx_dict(self, plugin_name: str) -> Dict[str, Any]:
        return {
            "plugin_name": plugin_name,
            "project_root": "",
            "app_dir": os.path.expanduser("~"),
            "config": dict(self.config),
        }

    def _fill_metadata(self, info: PluginInfo, module: Any,
                       manifest: Optional[dict]) -> None:
        """进程内路径：从模块读取元数据（与 P3 行为一致）。"""
        header_name = getattr(module, "PLUGIN_NAME", None)
        if isinstance(header_name, str) and header_name.strip():
            info.name = header_name.strip()
        pub = getattr(module, "PLUGIN_PUBLISHER", None)
        if isinstance(pub, str):
            info.publisher = pub.strip()
        if manifest:
            info.version = manifest.get("version", info.version)
            info.publisher = manifest.get("publisher",
                                         manifest.get("author", info.publisher))
            info.description = manifest.get("description", info.description)
        else:
            ver = getattr(module, "PLUGIN_VERSION", None)
            if isinstance(ver, str):
                info.version = ver.strip()
            dsc = getattr(module, "PLUGIN_DESCRIPTION", None)
            if isinstance(dsc, str):
                info.description = dsc.strip()

        raw_tools = getattr(module, "TOOLS", None)
        info.tools = [
            t.get("function", {}).get("name", "")
            for t in (raw_tools or [])
            if isinstance(t, dict) and t.get("function", {}).get("name")
        ]
        info.hook_names = [h for h in HOOK_NAMES if callable(getattr(module, h, None))]
        raw_hints = getattr(module, "APPROVAL_HINTS", None)
        if isinstance(raw_hints, dict):
            for tname, hint in raw_hints.items():
                if isinstance(tname, str) and isinstance(hint, dict):
                    info.approval_hints[tname] = {
                        "approval": str(hint.get("approval", "plugin")),
                        "risk": str(hint.get("risk", "")),
                    }

    def _decide_isolation(self, path: str,
                          manifest: Optional[dict]) -> str:
        """隔离模式决策：config 显式值 > manifest > 插件模块常量。"""
        if self.plugin_isolation in ("inproc", "process"):
            return self.plugin_isolation
        if manifest:
            declared = manifest.get("isolation")
            if declared in ("inproc", "process"):
                return declared
        pref = self._static_isolation_pref(path)
        return pref if pref in ("inproc", "process") else "inproc"

    @staticmethod
    def _static_isolation_pref(path: str) -> str:
        """AST 静态读取插件模块级 ISOLATION 常量（不执行插件代码）。"""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
        except Exception:
            return ""
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
                if len(targets) == 1 and isinstance(targets[0], ast.Name) \
                        and targets[0].id == "ISOLATION":
                    if isinstance(node.value, ast.Constant) \
                            and isinstance(node.value.value, str):
                        return node.value.value
        return ""

    def _exec_module(self, info: PluginInfo, path: str) -> Any:
        """在导入限制器保护下执行插件模块。"""
        # ★ 静态导入预检：运行时 meta_path 限制器对「已被主进程缓存」
        # 的模块（如 subprocess）无效（sys.modules 直接命中），因此
        # safe/strict 模式下先用 AST 静态检查插件源码的导入，
        # 命中受限模块则直接拒绝加载。
        if self.import_restriction in ("safe", "strict"):
            violation = self._static_import_violation(path)
            if violation:
                info.enabled = False
                info.error = (
                    f"导入被拦截: '{violation}'"
                    "（插件安全导入限制禁止加载该模块）"
                )
                return None

        mod_name = f"{PLUGIN_MODULE_PREFIX}{info.name}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            info.enabled = False
            info.error = f"无法构造模块 spec: {path}"
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module

        blocker: Optional[_ImportBlocker] = None
        if self.import_restriction in ("safe", "strict"):
            blocked = set(DANGEROUS_IMPORTS_FOR_BLOCK)
            blocker = _ImportBlocker(
                blocked, strict=(self.import_restriction == "strict")
            )
            blocker.register()
        try:
            _loading_plugin.active = True
            spec.loader.exec_module(module)
        except ImportError as exc:
            info.enabled = False
            info.error = f"导入被拦截: {exc}"
            sys.modules.pop(spec.name, None)
            return None
        except Exception as exc:  # noqa: BLE001
            info.enabled = False
            info.error = f"插件加载失败: {type(exc).__name__}: {exc}"
            sys.modules.pop(spec.name, None)
            return None
        finally:
            _loading_plugin.active = False
            if blocker is not None:
                blocker.unregister()
        return module

    @staticmethod
    def _check_module_name(name: str, blocked: Set[str], strict: bool) -> Optional[str]:
        root = name.split(".")[0]
        if strict:
            if name in STRICT_SAFE_MODULES or any(
                name == m or name.startswith(m + ".") for m in STRICT_SAFE_MODULES
            ):
                return None
            return name
        if root in blocked:
            return name
        return None

    def _static_import_violation(self, path: str) -> Optional[str]:
        """AST 静态检查插件源码中的受限导入（safe / strict 模式）。"""
        import ast

        try:
            with open(path, "r", encoding="utf-8") as fh:
                source = fh.read()
            tree = ast.parse(source)
        except Exception:
            return None
        blocked = set(DANGEROUS_IMPORTS_FOR_BLOCK) | ALWAYS_BLOCKED
        strict = self.import_restriction == "strict"
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    violation = self._check_module_name(alias.name, blocked, strict)
                    if violation:
                        return violation
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod:
                    violation = self._check_module_name(mod, blocked, strict)
                    if violation:
                        return violation
        return None

    def unload(self, registry: Any, plugin_name: str) -> bool:
        """卸载单个插件（热重载开发用）。"""
        mod_name = f"{PLUGIN_MODULE_PREFIX}{plugin_name}"
        sys.modules.pop(mod_name, None)
        with self._lock:
            before = len(self.plugins)
            self.plugins = [p for p in self.plugins if p.name != plugin_name]
            self._contexts.pop(plugin_name, None)
        if len(self.plugins) == before:
            return False
        # 提示：工具/钩子仍留在 Registry（EventBus 不支持按名退订时，
        # 建议重建 Registry 后重新加载）。
        return True

    def reload(self, registry: Any, plugin_name: str) -> bool:
        """重新加载单个插件（发现同名文件 → 重新走完整安全管线）。

        注意：旧实例的工具/钩子订阅仍在总线上，生产环境建议重建
        Registry；本方法用于开发期热重载。
        """
        info = next((p for p in self.plugins if p.name == plugin_name), None)
        if info is None:
            return False
        path = info.path
        manifest = None
        if not os.path.isfile(path):
            return False
        self.unload(registry, plugin_name)
        self._load_from_file(registry, plugin_name, path, manifest)
        return True

    def shutdown(self) -> None:
        """释放进程隔离宿主子进程等资源。"""
        if self._isolation_manager is not None:
            try:
                self._isolation_manager.shutdown()
            except Exception:
                pass
            self._isolation_manager = None


def install_plugin_dirs(registry: Any, plugin_dirs: List[str],
                        config: Optional[dict] = None) -> PluginLoader:
    """一次性便捷入口：加载插件目录并注册到 Registry。

    ``config`` 缺省时自动采用 ``registry.security``
    （norpagent.safe() 安装的安全策略）作为兜底配置——
    安全系统剥离后，插件加载默认继承全局安全姿态。
    """
    if config is None:
        security = getattr(registry, "security", None)
        if security is not None:
            plugin_config = getattr(security, "plugin_config", None)
            if callable(plugin_config):
                config = plugin_config()
    loader = PluginLoader(plugin_dirs, config)
    loader.discover_and_load(registry)
    # 工作回退：插件安装 = 一次系统变更，自动快照（失败静默）
    try:
        from norpagent.recovery import notify_system_change

        notify_system_change(
            description="插件安装: " + ", ".join(
                str(d) for d in (plugin_dirs or [])[:3]))
    except Exception:  # noqa: BLE001
        pass
    return loader

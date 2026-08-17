# Copyright (c) 2026 xingluosama121, MIT Licensed
"""注册表：框架的组装中心。

一切皆注册项。模型 / 工具 / 会话 / 沙箱 / 调度器 / UI / 插件 / 预设，
全部按名字注册、按名字解析。AgentRuntime 只与注册表交互，
因此替换任意部件都无需改动核心代码。

注册表本身是内核的一部分，不感知任何具体实现。
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from norpagent.kernel.events import EventBus
from norpagent.protocols.plugin import Plugin
from norpagent.protocols.tool import Tool


class ComponentError(Exception):
    """组件缺失 / 重复注册 / 类型不符等注册表错误。"""


class Registry:
    """组件注册中心。"""

    def __init__(self, bus: Optional[EventBus] = None) -> None:
        self.bus = bus or EventBus()
        self._models: Dict[str, Any] = {}
        self._tools: Dict[str, Tool] = {}
        self._sessions: Dict[str, Callable[[], Any]] = {}
        self._sandboxes: Dict[str, Callable[[], Any]] = {}
        self._schedulers: Dict[str, Callable[[], Any]] = {}
        self._uis: Dict[str, Any] = {}
        self._plugins: Dict[str, Plugin] = {}
        self._presets: Dict[str, Any] = {}
        # 通用组件命名空间：kind -> {name: factory}
        # 上下文存储 / 项目管理 / 任务存储等一切「附加能力」都走这里，
        # 使框架无需改内核即可扩展新的组件种类。
        self._components: Dict[str, Dict[str, Callable[[], Any]]] = {}
        # 安全上下文（norpagent.safe() 安装）：AgentRuntime 与插件加载器
        # 据此读取全套安全策略，实现「安全系统整体剥离」。
        self.security: Any = None
        # 钩子体系（懒加载）：同一总线上的 9 层钩子视图。
        self._hook_system: Any = None
        self._lock = threading.RLock()

    # ── 钩子 ──────────────────────────────────────────────

    @property
    def hooks(self) -> Any:
        """本注册表总线上的钩子系统（HookSystem）。

        ``registry.hooks.before_model_call.subscribe(fn)``
        等同于对 registry.bus 订阅同名事件；未定义的具名事件
        触发时自动成为 dynamic 层钩子。
        """
        with self._lock:
            if self._hook_system is None:
                from norpagent.hooks.core import HookSystem

                self._hook_system = HookSystem(self.bus)
            return self._hook_system


    # ── 注册 ──────────────────────────────────────────────

    def register_model(self, name: str, provider: Any) -> None:
        with self._lock:
            self._models[name] = provider

    def register_tool(self, name: str, tool: Tool) -> None:
        with self._lock:
            self._tools[name] = tool

    def register_session(self, name: str, factory: Callable[[], Any]) -> None:
        with self._lock:
            self._sessions[name] = factory

    def register_sandbox(self, name: str, factory: Callable[[], Any]) -> None:
        with self._lock:
            self._sandboxes[name] = factory

    def register_scheduler(self, name: str, factory: Callable[[], Any]) -> None:
        with self._lock:
            self._schedulers[name] = factory

    def register_ui(self, name: str, adapter: Any) -> None:
        with self._lock:
            self._uis[name] = adapter

    def register_plugin(self, plugin: Plugin) -> None:
        """注册一个插件：工具进入工具表、钩子订阅事件总线。

        重名工具以后注册者覆盖（日志提示），插件元数据保留。
        """
        with self._lock:
            name = getattr(plugin, "name", "") or plugin.__class__.__name__
            for tool in plugin.get_tools():
                tname = getattr(tool, "name", "") or tool.__class__.__name__
                if not tname:
                    continue
                if tname in self._tools:
                    print(f"[Registry] 工具 {tname} 已存在，被插件 {name} 覆盖")
                self._tools[tname] = tool
            for hook, fn in plugin.get_hooks().items():
                self.bus.subscribe(fn, hook)
            self._plugins[name] = plugin

    def register_preset(self, preset: Any) -> None:
        from norpagent.kernel.presets import Preset

        if not isinstance(preset, Preset):
            raise ComponentError(f"预设必须是 Preset 实例，收到 {type(preset)}")
        with self._lock:
            self._presets[preset.name] = preset

    def unregister_plugin(self, name: str) -> None:
        """卸载插件：退订其钩子订阅、移除插件记录。

        工具条目保留（工具注册表为名字覆盖语义，重挂载同名插件时
        自然覆盖；移除旧工具名的历史条目不影响解析——不在预设
        工具集内即不可达）。运行中热挂载 plugins 槽位时使用。
        """
        with self._lock:
            plugin = self._plugins.get(name)
            if plugin is None:
                return
            try:
                for hook, fn in plugin.get_hooks().items():
                    self.bus.unsubscribe(fn, hook)
            except Exception:  # noqa: BLE001 — 单插件异常不阻塞卸载
                pass
            self._plugins.pop(name, None)

    def register_component(self, kind: str, name: str, factory: Callable[[], Any]) -> None:
        """注册一个通用组件：kind（种类，如 context_store）+ name + 工厂。

        与 session / sandbox 等专用命名空间不同，组件种类本身是开放的：
        第三方可以直接 register_component("my_kind", "my_impl", factory)，
        并在预设里声明 components={"my_kind": "my_impl"}。
        """
        with self._lock:
            self._components.setdefault(kind, {})[name] = factory

    # ── 解析 ──────────────────────────────────────────────

    def resolve_model(self, name: str) -> Any:
        with self._lock:
            if name not in self._models:
                raise ComponentError(
                    f"模型 '{name}' 未注册。可用模型: {sorted(self._models)}"
                )
            return self._models[name]

    def resolve_tool(self, name: str) -> Tool:
        with self._lock:
            if name not in self._tools:
                raise ComponentError(
                    f"工具 '{name}' 未注册。可用工具: {sorted(self._tools)}"
                )
            return self._tools[name]

    def resolve_preset(self, name: str) -> Any:
        with self._lock:
            if name not in self._presets:
                raise ComponentError(
                    f"预设模式 '{name}' 未注册。可用模式: {sorted(self._presets)}"
                )
            return self._presets[name]

    def build_session(self, name: str) -> Any:
        with self._lock:
            if name not in self._sessions:
                raise ComponentError(
                    f"会话组件 '{name}' 未注册。可用: {sorted(self._sessions)}"
                )
            return self._sessions[name]()

    def build_sandbox(self, name: str) -> Any:
        with self._lock:
            if name not in self._sandboxes:
                raise ComponentError(
                    f"沙箱组件 '{name}' 未注册。可用: {sorted(self._sandboxes)}"
                )
            return self._sandboxes[name]()

    def build_scheduler(self, name: str) -> Any:
        with self._lock:
            if name not in self._schedulers:
                raise ComponentError(
                    f"调度组件 '{name}' 未注册。可用: {sorted(self._schedulers)}"
                )
            return self._schedulers[name]()

    def resolve_ui(self, name: str) -> Any:
        with self._lock:
            if name not in self._uis:
                raise ComponentError(
                    f"UI 组件 '{name}' 未注册。可用: {sorted(self._uis)}"
                )
            return self._uis[name]

    def build_component(self, kind: str, name: str,
                        workspace_root: Optional[str] = None) -> Any:
        """构建一个通用组件。

        ``workspace_root`` 提示：若工厂声明了同名参数（或 **kwargs），
        自动注入工作区根目录（项目管理等组件据此定位项目）。
        """
        with self._lock:
            bucket = self._components.get(kind)
            if not bucket or name not in bucket:
                available = sorted(bucket) if bucket else []
                raise ComponentError(
                    f"组件 '{kind}={name}' 未注册。"
                    f"可用 {kind}: {available}"
                )
            factory = bucket[name]
        if workspace_root is not None:
            try:
                import inspect

                sig = inspect.signature(factory)
                accepts = any(
                    p.kind is p.VAR_KEYWORD
                    for p in sig.parameters.values()
                ) or "workspace_root" in sig.parameters
            except (TypeError, ValueError):
                accepts = False
            if accepts:
                return factory(workspace_root=workspace_root)
        return factory()

    # ── 查询 ──────────────────────────────────────────────

    def list_models(self) -> List[str]:
        with self._lock:
            return sorted(self._models)

    def list_tools(self) -> List[str]:
        with self._lock:
            return sorted(self._tools)

    def list_presets(self) -> List[str]:
        with self._lock:
            return sorted(self._presets)

    def list_sessions(self) -> List[str]:
        with self._lock:
            return sorted(self._sessions)

    def list_sandboxes(self) -> List[str]:
        with self._lock:
            return sorted(self._sandboxes)

    def list_schedulers(self) -> List[str]:
        with self._lock:
            return sorted(self._schedulers)

    def list_uis(self) -> List[str]:
        with self._lock:
            return sorted(self._uis)

    def list_plugins(self) -> List[str]:
        with self._lock:
            return sorted(self._plugins)

    def list_components(self, kind: Optional[str] = None) -> Dict[str, List[str]]:
        """列出组件：kind 给定时返回该种类的名字列表，否则返回全部种类。"""
        with self._lock:
            if kind is not None:
                return sorted(self._components.get(kind, {}))
            return {k: sorted(v) for k, v in sorted(self._components.items())}

    def tool_schemas(self, names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """按名字（缺省全部）导出工具的 OpenAI function schema 列表。"""
        with self._lock:
            if names is None:
                names = sorted(self._tools)
            schemas: List[Dict[str, Any]] = []
            for n in names:
                tool = self._tools.get(n)
                if tool is None:
                    raise ComponentError(
                        f"工具 '{n}' 未注册。可用工具: {sorted(self._tools)}"
                    )
                schemas.append(tool.schema())
            return schemas

    def validate_preset(self, preset: Any) -> Tuple[List[str], List[str]]:
        """校验预设引用的组件是否齐备。

        返回 (缺失项列表, 缺失的工具列表)；两者皆空表示可用。
        缺失项文本形如 "model=openai_compat"，便于报错提示。
        """
        from norpagent.kernel.presets import Preset

        if not isinstance(preset, Preset):
            raise ComponentError(f"预设必须是 Preset 实例，收到 {type(preset)}")
        missing: List[str] = []
        missing_tools: List[str] = []
        with self._lock:
            if preset.model not in self._models:
                missing.append(f"model={preset.model}")
            if preset.session not in self._sessions:
                missing.append(f"session={preset.session}")
            if preset.sandbox not in self._sandboxes:
                missing.append(f"sandbox={preset.sandbox}")
            if preset.scheduler not in self._schedulers:
                missing.append(f"scheduler={preset.scheduler}")
            if preset.ui not in self._uis:
                missing.append(f"ui={preset.ui}")
            for kind, name in (preset.components or {}).items():
                bucket = self._components.get(kind)
                if not bucket or name not in bucket:
                    missing.append(f"component={kind}:{name}")
            for t in preset.tools:
                if t not in self._tools:
                    missing_tools.append(t)
        return missing, missing_tools

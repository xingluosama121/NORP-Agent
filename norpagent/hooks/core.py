# Copyright (c) 2026 xingluosama121, MIT Licensed
"""钩子系统核心：Hook / HookLayer / HookSystem / BoundHook / HookVeto。

设计目标（系统性工程的「暴露面」原则）：

- **每个钩子都是独立 API**：9 层 29 个标准钩子在 ``norpagent.hooks``
  下全部是可导入的一等对象，可单独订阅 / 触发 / 拦截，不依赖字符串散落。
- **每个执行结构都可被干预**：AgentRuntime 的每一次输入、会话创建、
  消息落库、消息组装、步骤、模型调用、工具调用、结果定型，
  都经过一个具名钩子；可变钩子可改写数据流，``HookVeto`` 可一票否决。
- **自定义层 / 自定义钩子**：第三方可用 ``HookLayer`` 声明自己的层，
  或直接在 ``HookSystem`` 上 ``define_hook``；未注册的具名事件在
  触发时自动成为 dynamic 层钩子，保证「先发事件、后补定义」也成立。
- **与 EventBus 双向兼容**：Hook 的订阅/发布最终落在 EventBus 上，
  旧插件（15 个 hook 名）与新钩子体系无缝共存。

订阅目标解析（``system`` 参数）支持：HookSystem / EventBus / Registry /
AgentRuntime / None（默认系统）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from norpagent.kernel.events import EventBus, HookVeto  # noqa: F401 — 否决语义核心类型（自 events 再导出）

# 动态钩子（未预先定义）所在层
DYNAMIC_LAYER = "dynamic"


@dataclass
class Hook:
    """一个钩子的标准定义（模块级独立 API）。

    - ``name``：钩子名（即 EventBus 事件名）；
    - ``layer``：所属层名；
    - ``order``：层内排序权重；
    - ``mutating``：True 表示订阅者可通过返回值改写数据流；
    - ``payload_keys``：文档化的事件负载键。

    ``subscribe / unsubscribe / emit / intercept`` 需要一个 ``system``
    定位总线：可传 HookSystem / EventBus / Registry / AgentRuntime，
    缺省使用进程默认系统（``norpagent.hooks.get_default_system()``）。
    """

    name: str
    layer: str
    order: int = 0
    mutating: bool = False
    description: str = ""
    payload_keys: tuple = ()

    # ── 独立 API：订阅 / 发布 / 拦截 ──────────────────────

    def subscribe(self, fn: Callable, system: Any = None) -> None:
        _resolve_bus(system).subscribe(fn, self.name)

    def unsubscribe(self, fn: Callable, system: Any = None) -> None:
        _resolve_bus(system).unsubscribe(fn, self.name)

    def emit(self, system: Any = None, **payload: Any) -> None:
        _resolve_bus(system).emit(self.name, **payload)

    def intercept(self, system: Any = None, **payload: Any) -> Any:
        return _resolve_bus(system).intercept(self.name, **payload)

    def bind(self, bus: EventBus) -> "BoundHook":
        """绑定到具体总线，得到运行时可用 API。"""
        return BoundHook(self, bus)

    def __repr__(self) -> str:
        return f"<Hook {self.name} [{self.layer}] mutating={self.mutating}>"


@dataclass
class BoundHook:
    """绑定到某条 EventBus 的钩子（AgentRuntime 暴露的就是这种对象）。"""

    definition: Hook
    bus: EventBus

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def layer(self) -> str:
        return self.definition.layer

    @property
    def mutating(self) -> bool:
        return self.definition.mutating

    @property
    def description(self) -> str:
        return self.definition.description

    @property
    def payload_keys(self) -> tuple:
        return self.definition.payload_keys

    def subscribe(self, fn: Callable) -> None:
        self.bus.subscribe(fn, self.name)

    def unsubscribe(self, fn: Callable) -> None:
        self.bus.unsubscribe(fn, self.name)

    def emit(self, **payload: Any) -> None:
        self.bus.emit(self.name, **payload)

    def intercept(self, **payload: Any) -> Any:
        return self.bus.intercept(self.name, **payload)

    def __repr__(self) -> str:
        return f"<BoundHook {self.name} [{self.layer}] mutating={self.mutating}>"


@dataclass
class HookLayer:
    """一层钩子：同一关注点的一组具名钩子。

    自定义层示例::

        layer = HookLayer("L10_network", order=100, description="网络访问层")
        layer.hook("before_network_call", mutating=True,
                   description="网络请求发出前（可改写 URL 或否决）")
        agent.hooks.install_layer(layer)
        agent.hooks.before_network_call.subscribe(monitor)
    """

    name: str
    order: int = 0
    description: str = ""
    hooks: Dict[str, Hook] = field(default_factory=dict)

    def hook(
        self,
        name: str,
        mutating: bool = False,
        description: str = "",
        payload_keys: tuple = (),
        order: int = 0,
    ) -> Hook:
        """在层内声明一个钩子（返回 Hook 定义，即独立 API）。"""
        h = Hook(
            name=name,
            layer=self.name,
            order=order,
            mutating=mutating,
            description=description,
            payload_keys=payload_keys,
        )
        self.hooks[name] = h
        return h

    def names(self) -> List[str]:
        return list(self.hooks)

    def __repr__(self) -> str:
        return f"<HookLayer {self.name} order={self.order} hooks={len(self.hooks)}>"


class HookSystem:
    """一个总线上的钩子视图：9 层标准钩子 + 自定义层/钩子的装配台。

    ``AgentRuntime.hooks`` / ``Registry.hooks`` 暴露的都是本类实例：
    ``runtime.hooks.before_model_call.subscribe(fn)`` 即钩子 API 的
    标准用法。构造时自动安装 9 层标准层。
    """

    def __init__(self, bus: EventBus, install_standard: bool = True) -> None:
        self.bus = bus
        self._hooks: Dict[str, BoundHook] = {}
        self._layers: List[HookLayer] = []
        self._lock = threading.RLock()
        if install_standard:
            from norpagent.hooks.standard import STANDARD_LAYERS

            for layer in STANDARD_LAYERS:
                self.install_layer(layer)

    # ── 装配 ──────────────────────────────────────────────

    def install_layer(self, layer: HookLayer) -> List[BoundHook]:
        """安装一个层（标准层或自定义层）。同名钩子已存在时保留原定义，
        但层元数据记录在案。返回本层钩子的绑定对象列表。"""
        with self._lock:
            if layer not in self._layers:
                self._layers.append(layer)
            self._layers.sort(key=lambda l: l.order)
            bound: List[BoundHook] = []
            for name, hook in layer.hooks.items():
                if name not in self._hooks:
                    bound_hook = BoundHook(hook, self.bus)
                    self._hooks[name] = bound_hook
                bound.append(self._hooks[name])
            return bound

    def define_hook(
        self,
        name: str,
        *,
        layer: Optional[str] = None,
        mutating: bool = False,
        description: str = "",
        payload_keys: tuple = (),
        order: int = 0,
    ) -> BoundHook:
        """定义一个自定义钩子（可归属自定义层），返回绑定 API。"""
        with self._lock:
            hook = Hook(
                name=name,
                layer=layer or DYNAMIC_LAYER,
                order=order,
                mutating=mutating,
                description=description,
                payload_keys=payload_keys,
            )
            self._hooks[name] = BoundHook(hook, self.bus)
            return self._hooks[name]

    def hook(self, name: str) -> BoundHook:
        """取钩子；未定义的名字自动成为 dynamic 层钩子。"""
        with self._lock:
            if name not in self._hooks:
                return self.define_hook(name, layer=DYNAMIC_LAYER)
            return self._hooks[name]

    def get(self, name: str, default: Any = None) -> Optional[BoundHook]:
        with self._lock:
            return self._hooks.get(name, default)

    def __getattr__(self, name: str) -> BoundHook:
        """属性访问：``hooks.before_step`` 直接拿到绑定 API。"""
        with self._lock:
            if name in self._hooks:
                return self._hooks[name]
        raise AttributeError(f"HookSystem 没有钩子 '{name}'")

    # ── 查询 ──────────────────────────────────────────────

    def layers(self) -> List[HookLayer]:
        with self._lock:
            return list(self._layers)

    def list_hooks(self) -> List[BoundHook]:
        with self._lock:
            return sorted(
                self._hooks.values(),
                key=lambda h: (h.definition.order, h.name),
            )

    def list_hook_names(self) -> List[str]:
        with self._lock:
            return sorted(self._hooks)

    def layer_of(self, name: str) -> Optional[HookLayer]:
        with self._lock:
            for layer in self._layers:
                if name in layer.hooks:
                    return layer
            return None


# ── 默认系统（模块级独立 API 的缺省落点）────────────────────

_default_system: Optional[HookSystem] = None
_default_lock = threading.Lock()


def get_default_system() -> HookSystem:
    """进程级默认钩子系统（自带独立总线）。

    模块级钩子 API（如 ``norpagent.hooks.before_model_call.subscribe(fn)``）
    不带 ``system`` 参数时落在该系统上。显式使用 Registry / AgentRuntime
    时请传 ``system=...``（注册表默认使用私有总线，保证隔离）。
    """
    global _default_system
    with _default_lock:
        if _default_system is None:
            _default_system = HookSystem(EventBus())
        return _default_system


def _resolve_bus(system: Any) -> EventBus:
    if system is None:
        return get_default_system().bus
    if isinstance(system, EventBus):
        return system
    if isinstance(system, HookSystem):
        return system.bus
    hooks = getattr(system, "hooks", None)
    if hooks is not None:
        return hooks.bus
    bus = getattr(system, "bus", None)
    if isinstance(bus, EventBus):
        return bus
    raise TypeError(
        f"无法解析钩子订阅目标: {type(system).__name__}。"
        "请传 HookSystem / EventBus / Registry / AgentRuntime。"
    )

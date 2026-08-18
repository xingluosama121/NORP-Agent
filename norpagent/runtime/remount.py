# Copyright (c) 2026 xingluosama121, MIT Licensed
"""运行中热挂载（Hot Remount）：任何槽位均可运行时替换。

np() 启动后引擎仍在 RUNNING 状态下，随时替换任意槽位实现：

    import norpagent as np
    np()                                   # 启动（默认 Web 前端）
    ...
    np.remount(model="openai_compat")      # 换模型：下一次 run() 生效
    np.remount(tools=["echo", "get_time"]) # 换工具集：下一次 run() 生效
    np.remount(session="sqlite")           # 换会话存储：AgentRuntime 热重建
    np.remount(security="high")            # 换安全级别：旧防护钩子先退订
    np.remount(frontend="norpagent.frontends.console:ConsoleFrontend")
    np.remount(async_loop="myapp.loop:create")
    np.remount(model="myapp.model:create") # 运行中替换模块文件（模块缓存失效）

替换语义按槽位分组：

1. **组件槽位**（model / tools / hooks / security / plugins）：
   重新应用到同一注册表并重写最终预设。model / tools 下一次
   run() 生效——Agent 循环每次 run 都重新解析模型与工具 schema。
   架构级订阅（钩子扩展 / 安全套件 / 插件）重挂前先退订旧的，
   不会叠加重复触发。

2. **装配槽位**（session / sandbox / scheduler / ui / agent_runtime /
   preset / context_store / project_manager）：这些组件在
   AgentRuntime 构造期解析，替换后**热重建运行时**——旧运行时
   关闭（释放沙箱 / 组件 / 退订渲染器），新运行时按当前架构层
   装配，前端渲染器重绑（HTTP 端口不变）。

3. **基础设施槽位**（frontend / async_loop）：停旧实现、启新实现。
   async_loop 替换时旧循环上若有在途任务会被放弃（见 _swap_loop）；
   frontend 替换失败自动回滚旧前端。

4. **基础服务槽位**（logger / storage / error_handler）：直接更新
   引擎引用，无需重建任何结构。

5. **自定义槽位**（v0.9 槽位表热插拔）：register_slot() 注册的槽位
   同样可 np.remount——按规格的 applier 重新应用（须重入安全）；
   规格声明 remount_rebuild_agent=True 的自定义装配槽位替换后热
   重建 AgentRuntime，与内置装配槽位语义一致。

任何槽位都可以 remount —— 这就是「除了底层最小内核外，全部组件
都是槽位」在运行时的体现：拼装体可以边跑边换零件；槽位表本身
也可以边跑边插新槽位（register_slot）。
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import Any, FrozenSet

from norpagent.arch.slots import snapshot_slots
from norpagent.kernel.presets import Preset
from norpagent.runtime.engine import EngineError
from norpagent.runtime.mount import apply_slot_overrides

# 替换后需要热重建 AgentRuntime 的槽位：
# 这些组件在运行时构造期解析（不是每次 run 重新解析）。
_AGENT_REBUILD_SLOTS: FrozenSet[str] = frozenset((
    "session",
    "sandbox",
    "scheduler",
    "ui",
    "agent_runtime",
    "preset",
    "context_store",
    "project_manager",
))


def remount_engine(engine: Any, **slot_values: Any) -> Any:
    """对运行中的引擎执行热挂载（见模块文档的槽位分组语义）。

    返回引擎本身；槽位名非法或未传任何槽位抛 EngineError。
    """
    if not slot_values:
        raise EngineError("热挂载至少需要一个槽位参数")
    known = snapshot_slots()
    unknown = [k for k in slot_values if k not in known]
    if unknown:
        raise EngineError(
            f"未知槽位 {unknown}。可用槽位: {list(known)}"
        )

    layer = engine.layer
    logger = engine._logger

    # 1. 更新架构层：替换槽位值并重新解析实现。
    #    字符串地址先失效模块缓存——「改模块文件 → remount」即热重载。
    for slot, value in slot_values.items():
        layer.remount(slot, value)

    # 2. 在同一注册表上重新应用槽位覆盖（旧的架构级订阅先退订）。
    final, extras = apply_slot_overrides(engine.registry, layer, engine.params)

    # 3. 预设与基础服务更新。
    _apply_preset(engine, final)
    engine.extras = dict(extras)
    engine._logger = engine.extras.get("logger")
    engine._error_handler = engine.extras.get("error_handler")
    logger = engine._logger

    # 4. 装配槽位：热重建 Agent 运行时。
    #    内置装配槽位集合 + 自定义槽位规格的 remount_rebuild_agent
    #    标志共同决定（v0.9 槽位表热插拔）。
    rebuild = bool(_AGENT_REBUILD_SLOTS & set(slot_values)) or any(
        bool(getattr(known.get(k), "remount_rebuild_agent", False))
        for k in slot_values
    )
    if rebuild and engine._agent is not None:
        engine._swap_agent()

    # 5. 基础设施槽位：前端 / 事件循环停旧启新。
    if "frontend" in slot_values:
        _swap_frontend(engine)
    if "async_loop" in slot_values:
        _swap_loop(engine)

    if logger is not None:
        try:
            logger.info("norpagent 热挂载完成: %s", ", ".join(sorted(slot_values)))
        except Exception:  # noqa: BLE001
            pass
    return engine


def _apply_preset(engine: Any, final: Preset) -> None:
    """把重算后的最终预设接到引擎与运行中的 Agent。

    维持初始装配的对象同一性约定：注册表里登记的预设对象与
    AgentRuntime 持有的预设对象保持同一个实例（前端对
    preset.tools 的热改写等依赖这一约定）。
    """
    engine.preset = final
    agent = engine._agent
    if agent is None:
        return
    try:
        agent.preset = final
    except Exception:  # noqa: BLE001 — 自定义运行时不接受则跳过
        # 自定义运行时若把预设塞进了不可写属性，逐字段原地同步兜底
        try:
            old = agent.preset
            for f in dataclasses.fields(Preset):
                setattr(old, f.name, getattr(final, f.name))
            engine.preset = old
        except Exception:  # noqa: BLE001
            pass


# WebFrontend.attach 会从 engine.params 读 port/host/open_browser/language/
# html 并覆盖构造值。这些键在初次启动时来自 np() 的参数透传；热挂载时若不
# 处理，它们会把本次 remount 传入的新值压回去（比如启动时 np(html=...)
# 会让 remount(frontend="...;html=其他页") 永远不生效）。
# 屏蔽规则：只有本次 remount **显式给出**的键才屏蔽启动参数；未显式给出的
# 键（如 port）沿用启动参数——这样「换页面」热挂载后浏览器 URL 不变。
_WEB_ATTACH_PARAM_KEYS: FrozenSet[str] = frozenset((
    "port", "host", "open_browser", "language", "html",
))


def _explicit_web_keys(engine: Any, new_impl: Any) -> FrozenSet[str]:
    """本次 remount 显式给出的 web 参数键。

    - 字符串地址：分句（";key=value"）中的键为显式；
    - 实例：构造参数中与默认值不同的键为显式（html 以 _html 属性
      判定，None 视为未给）。自定义实现无法反射时返回空集
      （全部沿用启动参数，行为安全）。
    """
    raw = engine.layer.config.get("frontend")
    if isinstance(raw, str):
        sub = engine.layer._subconfigs.get("frontend", {}) or {}
        return frozenset(k for k in sub if k in _WEB_ATTACH_PARAM_KEYS)
    try:
        sig = inspect.signature(type(new_impl).__init__)
    except Exception:  # noqa: BLE001
        return frozenset()
    keys = set()
    for name, param in sig.parameters.items():
        if name not in _WEB_ATTACH_PARAM_KEYS:
            continue
        if param.default is inspect.Parameter.empty:
            continue
        if name == "html":
            actual, default = getattr(new_impl, "_html", None), None
        else:
            actual, default = getattr(new_impl, name, None), param.default
        if actual != default:
            keys.add(name)
    return frozenset(keys)


def _swap_frontend(engine: Any) -> None:
    """热替换 frontend 槽位：停旧前端 → 接新前端 → 启动。

    attach 期间临时屏蔽 engine.params 里本次 remount 显式给出的
    web 键，让 remount 值成为权威；attach 完成后恢复（params 同时
    承担任务参数透传职责，需保持原样）。新前端 attach / start 失败
    时回滚旧前端（尽力而为）。
    """
    old = engine.frontend
    new_impl = engine.layer["frontend"]
    if new_impl is old:
        return
    if old is not None:
        try:
            old.stop()
        except Exception:  # noqa: BLE001
            pass
    engine.frontend = new_impl
    params = getattr(engine, "params", None) or {}
    shielded = {k: params.pop(k, None) for k in _explicit_web_keys(engine, new_impl)}
    try:
        new_impl.attach(engine)
        new_impl.start()
    except Exception:
        # 新前端启动失败：尽力回滚
        engine.frontend = old
        if old is not None:
            try:
                old.attach(engine)
                old.start()
            except Exception:  # noqa: BLE001
                pass
        raise
    finally:
        if shielded:
            params.update(shielded)


def _swap_loop(engine: Any) -> None:
    """热替换 async_loop 槽位：停旧循环 → 启新循环。

    注意：旧循环上在途的任务会被放弃（结果无人消费）。建议在
    没有任务执行时替换事件循环；替换失败尽力回滚旧循环。
    """
    old = engine.loop
    new_impl = engine.layer["async_loop"]
    if new_impl is old:
        return
    if old is not None:
        try:
            old.stop()
            old.join(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass
    engine.loop = new_impl
    try:
        new_impl.start()
    except Exception:
        # 新循环启动失败：尽力回滚
        engine.loop = old
        if old is not None:
            try:
                old.start()
            except Exception:  # noqa: BLE001
                pass
        raise


__all__ = ["remount_engine"]

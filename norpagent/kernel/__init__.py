# Copyright (c) 2026 xingluosama121, MIT Licensed
"""内核层：注册表、事件总线、预设模式与通用 Agent 循环。

内核代码是框架中开发者无需修改的部分：它只认识协议与注册表，
一切能力差异都由「注册了哪些组件、选择了哪种预设」决定。
"""

from norpagent.kernel.events import EventBus, EventType, AgentEvent
from norpagent.kernel.registry import Registry, ComponentError
from norpagent.kernel.presets import Preset, load_preset_file, MODE_SINGLE, MODE_PTC, MODE_CUSTOM
from norpagent.kernel.context import RunContext
from norpagent.kernel.agent import AgentRuntime, RunResult

__all__ = [
    "EventBus",
    "EventType",
    "AgentEvent",
    "Registry",
    "ComponentError",
    "Preset",
    "load_preset_file",
    "MODE_SINGLE",
    "MODE_PTC",
    "MODE_CUSTOM",
    "RunContext",
    "AgentRuntime",
    "RunResult",
]

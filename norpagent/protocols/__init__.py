# Copyright (c) 2026 xingluosama121, MIT Licensed
"""协议层：全部组件的抽象接口（Protocol）。

框架核心只依赖本包定义的接口，从不依赖任何具体实现。
开发者实现这些接口即可替换 Agent 的任意部件：

- ModelProvider    模型（大脑）
- Tool             工具（技能）
- SessionManager   会话管理（记忆）
- Sandbox / SandboxProvider  沙箱环境（隔离执行）
- TaskScheduler    任务调度（编排，含未来多智能体）
- UIAdapter        用户界面（交互）
- Plugin           插件（工具的打包分发单位，可附带生命周期钩子）
"""

from norpagent.protocols.model import (
    ChatMessage,
    ToolCallSpec,
    ModelUsage,
    ModelOutput,
    ModelStreamChunk,
    ModelProvider,
)
from norpagent.protocols.tool import Tool, ToolResult
from norpagent.protocols.session import Session, SessionManager
from norpagent.protocols.sandbox import Sandbox, SandboxProvider, SandboxResult
from norpagent.protocols.scheduler import AgentTask, TaskResult, TaskScheduler
from norpagent.protocols.ui import UIAdapter
from norpagent.protocols.plugin import Plugin

__all__ = [
    "ChatMessage",
    "ToolCallSpec",
    "ModelUsage",
    "ModelOutput",
    "ModelStreamChunk",
    "ModelProvider",
    "Tool",
    "ToolResult",
    "Session",
    "SessionManager",
    "Sandbox",
    "SandboxProvider",
    "SandboxResult",
    "AgentTask",
    "TaskResult",
    "TaskScheduler",
    "UIAdapter",
    "Plugin",
]

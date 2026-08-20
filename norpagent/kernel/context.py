# Copyright (c) 2026 xingluosama121, MIT Licensed
"""运行上下文：一次任务执行期间，工具与钩子可访问的全部环境。

``RunContext`` 通过工具 ``run(args, ctx)`` 的 ctx 参数、以及事件 payload
中的 ``context`` 字段传递给外围组件。它是 Agent 内核与外围组件的
唯一耦合面：组件只依赖上下文提供的能力，不依赖任何具体实现类。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:  # 仅类型检查引用，避免循环导入
    from norpagent.kernel.registry import Registry
    from norpagent.protocols.sandbox import Sandbox
    from norpagent.protocols.scheduler import TaskScheduler
    from norpagent.protocols.session import SessionManager
    from norpagent.protocols.ui import UIAdapter


@dataclass
class RunContext:
    """一次任务运行的环境句柄。

    属性：
        registry：组件注册表（可解析其他工具/模型）
        session_manager / session_id：会话存取
        sandbox：当前任务沙箱（命令执行、代码执行）
        scheduler：任务调度器（可提交子任务，多智能体协作的入口）
        ui：UI 适配器（ask_user 人工交互）
        components：预设声明的通用组件实例（{kind: instance}）
        params：预设 params 与任务级参数的合并结果
        task_id：任务 id
    """

    registry: Any = None
    session_manager: Any = None
    session_id: Optional[str] = None
    sandbox: Any = None
    scheduler: Any = None
    ui: Any = None
    params: Dict[str, Any] = field(default_factory=dict)
    task_id: str = ""
    preset_name: str = ""
    components: Dict[str, Any] = field(default_factory=dict)

    def component(self, kind: str, default: Any = None) -> Any:
        """按种类取通用组件（如 "context_store" / "project_manager"）。"""
        return self.components.get(kind, default)

    @property
    def context_store(self) -> Any:
        """上下文存储组件（context_add / context_search 等工具使用）。"""
        return self.components.get("context_store")

    @property
    def project_manager(self) -> Any:
        """项目管理组件（project_status 等工具使用）。"""
        return self.components.get("project_manager")

    @property
    def task_store(self) -> Any:
        """任务存储组件（task_* 工具使用；持久化调度器自带时可为 None）。"""
        return self.components.get("task_store")

    def ask_user(self, question: str, default: str = "") -> str:
        """向用户提问（UI 未提供交互时返回 default）。"""
        if self.ui is not None:
            try:
                return self.ui.ask_user(question, default)
            except Exception:
                return default
        return default

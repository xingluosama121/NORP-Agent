# Copyright (c) 2026 xingluosama121, MIT Licensed
"""任务调度协议：Agent 的「编排」抽象。

P1 提供先进先出的顺序调度器。P3 将扩展队列优先级、并发上限、
长周期任务协作与多智能体编排（子 Agent 复用同一注册表）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Protocol, runtime_checkable


@dataclass
class AgentTask:
    """一个待执行（或已执行）的 Agent 任务。

    ``runtime`` 由调度器注入：调度器把任务交给 AgentRuntime 执行。
    """

    id: str
    user_input: str
    preset_name: str = ""
    session_id: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | running | done | failed | stopped
    result: Optional["TaskResult"] = None


@dataclass
class TaskResult:
    """任务执行结果。``run_result`` 为 AgentRuntime.run 的返回值。"""

    task_id: str
    status: str
    error: str = ""
    run_result: Any = None


@runtime_checkable
class TaskScheduler(Protocol):
    """任务调度器接口。

    P1 契约：``submit`` 入队，``drain`` 依序执行全部待办任务。
    ``run_task`` 为调度器执行单个任务的回调（由运行时注入，
    使调度器与 Agent 循环解耦：未来多智能体编排时回调可指向不同 Agent）。
    """

    def submit(self, task: AgentTask) -> str:
        """入队一个任务并返回其 id。"""
        ...

    def pending(self) -> int:
        """当前待办任务数。"""
        ...

    def drain(self, run_task: Callable[[AgentTask], TaskResult]) -> List[TaskResult]:
        """顺序执行全部待办任务，返回结果列表。"""
        ...

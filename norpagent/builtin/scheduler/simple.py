# Copyright (c) 2026 xingluosama121, MIT Licensed
"""简单任务调度器：先进先出的顺序执行。

P3 将扩展：队列优先级、并发上限、长周期任务协作（任务拆分/续跑）、
多智能体编排（子 Agent 复用同一注册表，通过 RunContext.scheduler 提交子任务）。
"""

from __future__ import annotations

import queue
import uuid
from typing import Callable, List, Optional

from norpagent.protocols.scheduler import AgentTask, TaskResult


class SimpleTaskScheduler:
    """FIFO 顺序任务调度器（线程安全）。"""

    def __init__(self) -> None:
        self._queue: "queue.Queue[AgentTask]" = queue.Queue()

    def submit(self, task: AgentTask) -> str:
        if not task.id:
            task.id = uuid.uuid4().hex[:12]
        task.status = "pending"
        self._queue.put(task)
        return task.id

    def pending(self) -> int:
        return self._queue.qsize()

    def drain(self, run_task: Callable[[AgentTask], TaskResult]) -> List[TaskResult]:
        results: List[TaskResult] = []
        while True:
            try:
                task = self._queue.get_nowait()
            except queue.Empty:
                break
            task.status = "running"
            try:
                task_result = run_task(task)
            except Exception as exc:  # noqa: BLE001 — 单个任务失败不阻塞队列
                task_result = TaskResult(task_id=task.id, status="failed", error=str(exc))
            task.status = task_result.status
            task.result = task_result
            results.append(task_result)
            self._queue.task_done()
        return results

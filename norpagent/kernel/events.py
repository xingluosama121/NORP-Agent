# Copyright (c) 2026 xingluosama121, MIT Licensed
"""事件系统：Agent 循环与一切外围组件（UI / 插件钩子）的解耦点。

事件名与现有 plugin_system 的 15 个 hook 一一对齐，P3 迁移外部插件
时可无缝映射：钩子 = 事件订阅。

事件流（一次任务）：
    on_task_start -> [ before_step -> (on_reasoning/on_content) ->
    before_tool_call -> after_tool_call ]* -> after_step -> on_task_done
    异常时 on_task_error；超步数/超时 on_task_stopped / on_task_timeout
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class EventType(str, Enum):
    # L1 – Agent 生命周期
    ON_AGENT_INIT = "on_agent_init"
    ON_AGENT_SHUTDOWN = "on_agent_shutdown"
    # L2 – 任务
    ON_TASK_START = "on_task_start"
    ON_TASK_DONE = "on_task_done"
    ON_TASK_ERROR = "on_task_error"
    ON_TASK_STOPPED = "on_task_stopped"
    ON_TASK_TIMEOUT = "on_task_timeout"
    # L3 – 步骤
    BEFORE_STEP = "before_step"
    AFTER_STEP = "after_step"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    ON_USER_INPUT_REQUIRED = "on_user_input_required"
    # L4 – 流式事件
    ON_REASONING = "on_reasoning"
    ON_CONTENT = "on_content"
    ON_EVENT = "on_event"
    ON_USAGE_UPDATE = "on_usage_update"


class HookVeto(Exception):
    """可变钩子抛出的一票否决（9 层钩子体系的核心语义）。

    在支持否决语义的执行点上抛出后，运行时按该点语义安全收尾：
    - before_input：任务以 stopped 收尾，reason 进入错误信息；
    - before_tool_call：工具调用被阻止（等价于返回 False）；
    - before_model_call：本轮模型调用被拒绝，任务以 stopped 收尾；
    - before_message_append：该条消息不落库；
    - 其余可变点：本次改写被忽略，沿用原值。

    注意：EventBus.intercept 对其**不捕获**（不同于普通订阅者异常），
    保证否决语义一定能传递到内核。
    """

    def __init__(self, reason: str = "操作被钩子否决"):
        super().__init__(reason)
        self.reason = reason


# 与现有 plugin_system.HOOK_NAMES 对齐的完整名单（含别名兼容）
ALL_EVENT_NAMES = [e.value for e in EventType]


@dataclass
class AgentEvent:
    """一次事件。``payload`` 为事件负载 dict（各事件字段见发布处）。"""

    type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)


Listener = Callable[[AgentEvent], None]


class EventBus:
    """线程安全的事件总线。

    - ``subscribe(listener, event_type=None)``：None 表示订阅全部事件
    - ``emit(type, **payload)``：发布事件；订阅者异常被捕获并记录，不中断主流程

    高并发优化（写时复制，copy-on-write）：

    - 订阅者表是**不可变快照**：subscribe / unsubscribe 在锁内创建
      **新列表**并替换引用，绝不原地修改；
    - emit / intercept 只在锁内取一次引用，随后**无锁直接迭代**——
      高频事件（如流式 on_content 逐 token 推送）下省去每条事件的
      列表复制开销；并发写者替换的是新列表对象，读者持有的旧
      快照不会被修改，线程安全性不变。
    """

    def __init__(self) -> None:
        self._all: List[Listener] = []
        self._typed: Dict[str, List[Listener]] = {}
        self._lock = threading.RLock()
        self._log_error: Optional[Callable[[str], None]] = None

    def set_error_logger(self, logger: Callable[[str], None]) -> None:
        """设置订阅者异常时的记录回调（默认打印到 stderr）。"""
        self._log_error = logger

    @staticmethod
    def _without_one(lst: List[Listener], listener: Listener) -> List[Listener]:
        """返回移除首个等于 listener 元素的新列表（写时复制语义）。"""
        for i, fn in enumerate(lst):
            if fn == listener:
                return lst[:i] + lst[i + 1:]
        return lst

    def subscribe(self, listener: Listener, event_type: Optional[str] = None) -> None:
        with self._lock:
            if event_type is None:
                self._all = self._all + [listener]
            else:
                self._typed[event_type] = self._typed.get(event_type, []) + [listener]

    def unsubscribe(self, listener: Listener, event_type: Optional[str] = None) -> None:
        with self._lock:
            if event_type is None:
                self._all = self._without_one(self._all, listener)
            else:
                lst = self._typed.get(event_type)
                if lst:
                    new = self._without_one(lst, listener)
                    if new:
                        self._typed[event_type] = new
                    else:
                        del self._typed[event_type]

    def _snapshot(self, event_type: str) -> "tuple[List[Listener], Optional[List[Listener]]]":
        """锁内取订阅者引用快照（不复制，见类文档的写时复制说明）。"""
        with self._lock:
            return self._all, self._typed.get(event_type)

    def emit(self, event_type: str, **payload: Any) -> None:
        event = AgentEvent(type=event_type, payload=payload)
        all_listeners, typed_listeners = self._snapshot(event_type)
        for fn in all_listeners:
            try:
                fn(event)
            except Exception as exc:  # noqa: BLE001 — 订阅者不得拖垮主循环
                self._report_error(event_type, exc)
        if typed_listeners:
            for fn in typed_listeners:
                try:
                    fn(event)
                except Exception as exc:  # noqa: BLE001 — 订阅者不得拖垮主循环
                    self._report_error(event_type, exc)

    def intercept(self, event_type: str, **payload: Any) -> Any:
        """可变事件分发：返回第一个非 None 的订阅者返回值。

        与现有应用 plugin_system 的 _broadcast_mutating 语义一致：
        before_step / before_tool_call / after_tool_call 等钩子可通过
        返回值修改数据流（返回 None = 不干预）。无订阅者或全部返回
        None 时返回 None。

        ``HookVeto`` 特殊：**不捕获**（否决语义必须送达内核）；
        其余订阅者异常记录后继续（订阅者不得拖垮主循环）。
        """
        event = AgentEvent(type=event_type, payload=payload)
        all_listeners, typed_listeners = self._snapshot(event_type)
        for fn in all_listeners:
            try:
                result = fn(event)
                if result is not None:
                    return result
            except HookVeto:
                raise
            except Exception as exc:  # noqa: BLE001 — 订阅者不得拖垮主循环
                self._report_error(event_type, exc)
        if typed_listeners:
            for fn in typed_listeners:
                try:
                    result = fn(event)
                    if result is not None:
                        return result
                except HookVeto:
                    raise
                except Exception as exc:  # noqa: BLE001 — 订阅者不得拖垮主循环
                    self._report_error(event_type, exc)
        return None

    def _report_error(self, event_type: str, exc: Exception) -> None:
        msg = f"[EventBus] 事件 {event_type} 订阅者异常: {exc}"
        if self._log_error:
            self._log_error(msg)
        else:
            import sys

            print(msg, file=sys.stderr)

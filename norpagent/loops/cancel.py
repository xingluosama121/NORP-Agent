# Copyright (c) 2026 xingluosama121, MIT Licensed
"""任务取消信号：submit 任务内的取消事件上下文（Ctrl+C / 引擎停止）。

StdLoopRuntime.submit 把每个任务包进一个带取消事件的 contextvars
上下文；任务执行体（沙箱 / 模型适配器 / 工具）随时可读
``cancel_requested()`` 检查是否被请求取消，尽早自行退出——
强杀子进程、中断流式读取，而不是等超时兜底。

实现细节：contextvars 随 ``contextvars.copy_context().run(fn)``
流入工作线程，因此在工作线程内读取到的是当前任务的取消事件；
工作线程之外读到默认值 None（视为未取消 / 不在任务内）。
"""
from __future__ import annotations

import contextvars
import threading
from typing import Optional

_current_cancel: "contextvars.ContextVar[Optional[threading.Event]]" = (
    contextvars.ContextVar("norpagent_current_cancel", default=None)
)


def current_cancel_event() -> Optional[threading.Event]:
    """当前 submit 任务的取消事件；不在 submit 任务内时返回 None。"""
    return _current_cancel.get()


def cancel_requested() -> bool:
    """当前任务是否被请求取消（Ctrl+C / 引擎停止 / 中断）。

    返回 True 表示调用方已放弃等待本任务，执行体应尽快自行退出。
    """
    event = _current_cancel.get()
    return event is not None and event.is_set()


__all__ = ["current_cancel_event", "cancel_requested"]

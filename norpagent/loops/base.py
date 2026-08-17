# Copyright (c) 2026 xingluosama121, MIT Licensed
"""LoopRuntime 协议：事件循环系统的统一契约。

``async_loop`` 槽位（即架构函数 norpagent.nasyncio()）的实现
必须满足本协议。默认实现是标准 asyncio 适配器
（norpagent.loops.std_asyncio.StdLoopRuntime）；填入地址即可
替换成任意事件循环系统——例如把自研 nasync_io 移植为一个
LoopRuntime 模块，整个框架的调度核心即被整体替换。

循环系统与 Agent 逻辑彻底解耦：引擎（runtime.engine）只通过
本协议与循环交互，不 import 任何具体循环实现。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LoopRuntime(Protocol):
    """事件循环运行时接口。

    实现方式不限（标准 asyncio / 自研协程 trampoline / 线程池），
    只需满足以下契约：

    - start() 之后循环进入可提交状态；
    - submit() 在循环上下文中执行同步函数并返回其结果
      （实现可自由选择真实异步或线程池模拟）；
    - stop() 与 join() 支持从任意线程调用，用于收尾。
    """

    name: str

    def start(self) -> None:
        """启动循环（通常内部持有专用线程 run_forever）。"""
        ...

    def stop(self) -> None:
        """请求循环停止（线程安全）。"""
        ...

    def is_running(self) -> bool:
        """循环是否仍在运行。"""
        ...

    def join(self, timeout: float = None) -> None:
        """等待循环线程退出。"""
        ...

    def submit(self, fn, *args, **kwargs) -> Any:
        """在循环上下文中执行同步函数 fn 并返回其结果。

        阻塞调用方直到 fn 完成；循环停止后调用将抛出 RuntimeError。
        """
        ...

    # ── 可选扩展（非强制，引擎用 hasattr 探测）─────────────
    def interrupt(self) -> None:
        """请求取消全部在途 submit 任务（Ctrl+C / 引擎停止路径）。

        默认实现置位每个任务的取消事件（contextvars 传递，见
        norpagent.loops.cancel）：沙箱强杀子进程、模型流式中断、
        任务执行体尽早退出。不等待任务真正结束。未实现时
        引擎回退为仅等待任务自行超时。
        """
        ...


__all__ = ["LoopRuntime"]

# Copyright (c) 2026 xingluosama121, MIT Licensed
"""Frontend 协议：面向用户的输入/输出外壳。

前端与渲染器是两层：

- **frontend（本协议）**：用户交互外壳——从哪里读输入、
  何时启动 / 停止、如何把输入交给引擎；
- **ui（UIAdapter，norpagent.protocols.ui）**：事件渲染层——
  订阅事件总线把 Agent 事件渲染成文本 / 界面元素。

一个 frontend 通常会携带（或引用）一个 ui 渲染器。
前端必须高度可变：console / headless / web / 任意自定义前端
全部满足同一协议，填地址即可整体替换。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Frontend(Protocol):
    """前端外壳接口。"""

    frontend_id: str

    def attach(self, engine: Any) -> None:
        """绑定引擎（NorpEngine）。

        前端通过 engine.submit(text) 提交用户输入；
        通过 engine.request_stop() 请求停止整个应用；
        通常还会订阅引擎的事件总线渲染输出。
        """
        ...

    def start(self) -> None:
        """启动前端（通常内部自建后台线程，不阻塞调用方）。

        例外：控制台前端在 Python 交互式解释器（REPL）中自动切换
        为同步模式——输入循环在调用线程就地运行，阻塞直到用户退出
        （避免后台线程与 REPL 抢 stdin、Ctrl+C 无法送达）。
        """
        ...

    def stop(self) -> None:
        """停止前端（线程安全，可从任意线程调用）。"""
        ...

    def is_alive(self) -> bool:
        """前端是否仍在运行。"""
        ...


__all__ = ["Frontend"]

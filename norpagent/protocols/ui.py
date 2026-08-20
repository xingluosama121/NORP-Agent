# Copyright (c) 2026 xingluosama121, MIT Licensed
"""UI 协议：Agent 的「界面」抽象。

界面与 Agent 循环完全解耦：运行时只通过事件总线广播事件，
UI 适配器订阅事件并自行渲染（控制台 / Web / 桌面 / 托盘）。

P1 提供控制台适配器；P3 将迁移现有 FastAPI 后端与桌面前端作为
Web UI 适配器插件。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class UIAdapter(Protocol):
    """用户界面适配器接口。"""

    ui_id: str

    def on_event(self, event: Any) -> None:
        """接收并渲染一个 AgentEvent。"""
        ...

    def ask_user(self, question: str, default: str = "") -> str:
        """向用户提问并返回答复（人工审批、澄清等场景使用）。"""
        ...

    def notify(self, message: str, level: str = "info") -> None:
        """非阻塞通知（提示、警告等）。"""
        ...

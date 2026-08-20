# Copyright (c) 2026 xingluosama121, MIT Licensed
"""控制台 UI 适配器：默认交互界面。

订阅事件总线并按事件类型渲染。P3 将迁移现有 FastAPI 后端 + 桌面前端
为 Web UI 适配器（同一事件协议，切换 UI 只需改预设的 ui 字段）。
"""

from __future__ import annotations

import sys
from typing import Any


class ConsoleUI:
    """极简控制台渲染器：流式正文直接输出，工具调用打印摘要。"""

    ui_id = "console"

    def __init__(self, stream: Any = None, verbose: bool = True) -> None:
        self._stream = stream or sys.stdout
        self.verbose = verbose
        self._inline = False  # 上一段流式正文未换行
        self._streamed = ""   # 本轮任务已流式累积的正文（去重用）

    def _write(self, text: str, end: str = "\n") -> None:
        if end != "" and self._inline:
            self._stream.write("\n")
            self._inline = False
        self._stream.write(text + end)
        self._stream.flush()
        self._inline = end == ""

    def on_event(self, event: Any) -> None:
        etype = event.type
        p = event.payload
        if etype == "on_task_start":
            self._streamed = ""
            self._write(f"\n== 任务开始 [{p.get('preset')}] {p.get('task_id')} ==")
            if self.verbose and p.get("user_input"):
                self._write(f">> {p.get('user_input')}")
        elif etype == "on_content":
            content = p.get("content", "")
            if p.get("stream"):
                self._streamed += content
                self._write(content, end="")
            else:
                # 流式已输出过全文时跳过（避免重复）
                if content.strip() != self._streamed.strip():
                    self._write(content)
                self._streamed = ""
        elif etype == "before_tool_call" and self.verbose:
            self._write(f"\n[tool] {p.get('tool_name')} {p.get('args')}")
        elif etype == "after_tool_call" and self.verbose:
            result = p.get("result")
            ok = p.get("success", True)
            mark = "OK" if ok else "FAIL"
            out = str(result)[:200] + ("..." if len(str(result)) > 200 else "")
            self._write(f"[tool:{mark}] {p.get('tool_name')} -> {out}")
        elif etype == "on_task_done":
            if self.verbose:
                self._write(f"\n== 任务完成 [{p.get('task_id')}] 步数={p.get('steps')} ==")
        elif etype == "on_task_error":
            self._write(f"\n== 任务异常: {p.get('error')} ==")
        elif etype == "on_task_stopped":
            self._write(f"\n== 任务停止: {p.get('reason', '')} ==")
        elif etype == "on_task_timeout":
            self._write(f"\n== 任务超时: {p.get('timeout')}s ==")
        elif etype == "on_usage_update" and self.verbose:
            self._write(
                f"[usage] in={p.get('input')} out={p.get('output')} total={p.get('total')}"
            )

    def ask_user(self, question: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        self._write(f"{question}{suffix}: ", end="")
        try:
            answer = input().strip()
        except EOFError:
            return default
        return answer or default

    def notify(self, message: str, level: str = "info") -> None:
        prefix = {"error": "[错误]", "warn": "[警告]"}.get(level, "[提示]")
        self._write(f"{prefix} {message}")

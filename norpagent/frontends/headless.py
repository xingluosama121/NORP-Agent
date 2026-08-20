# Copyright (c) 2026 xingluosama121, MIT Licensed
"""无头前端：不读输入、程序 API 驱动，但输出必须可见。

用于库嵌入、基准测试与单次任务（prompt 模式）：
Agent 在引擎 submit() 被程序调用时执行；本前端挂载一个
轻量 stdout 渲染器，保证「输入看得见、输出也看得见」——

- 预设 ui=console 时复用已有渲染器，不重复输出；
- 预设 ui=web 等静默渲染器时换用本前端的可见渲染器；
- 单次任务（prompt 模式）完成时引擎额外打印最终结果。
"""

from __future__ import annotations

import sys
from typing import Any, Optional

from norpagent.frontends.base import Frontend


class _HeadlessRenderer:
    """headless 模式的可见渲染器：关键事件打印到 stdout。"""

    ui_id = "headless"

    def __init__(self, stream: Any = None) -> None:
        self._stream = stream or sys.stdout
        self._inline = False
        self._streamed = ""  # 本轮任务已流式累积的正文（去重用）

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
            self._write(f"[headless] task start [{p.get('preset')}] {p.get('task_id')}")
            if p.get("user_input"):
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
        elif etype == "before_tool_call":
            self._write(f"\n[tool] {p.get('tool_name')} {p.get('args')}")
        elif etype == "after_tool_call":
            result = str(p.get("result"))[:200]
            ok = p.get("success", True)
            mark = "OK" if ok else "FAIL"
            self._write(f"[tool:{mark}] {p.get('tool_name')} -> {result}")
        elif etype == "on_tool_error":
            self._write(f"\n[tool:FAIL] {p.get('tool_name')} -> {p.get('error')}")
        elif etype == "on_usage_update":
            self._write(
                f"\n[usage] in={p.get('input')} out={p.get('output')}"
                f" total={p.get('total')}"
            )
        elif etype == "on_task_done":
            self._write(f"\n[headless] task done [{p.get('task_id')}] steps={p.get('steps')}")
        elif etype == "on_task_error":
            self._write(f"\n[headless] task error: {p.get('error')}")
        elif etype == "on_task_stopped":
            self._write(f"\n[headless] task stopped: {p.get('reason', '')}")
        elif etype == "on_task_timeout":
            self._write(f"\n[headless] task timeout: {p.get('timeout')}s")


class HeadlessFrontend:
    """静默输入、可见输出：一切输入由程序 API 控制，事件渲染到 stdout。"""

    frontend_id = "headless"

    def __init__(self, renderer: Any = None, stream: Any = None, **kwargs: Any) -> None:
        self.renderer = renderer
        self._stream = stream
        self._engine: Any = None
        self._started = False

    def attach(self, engine: Any) -> None:
        self._engine = engine
        # 已有可见渲染器（预设 ui=console）→ 复用，不重复输出
        agent = getattr(engine, "agent", None)
        existing = getattr(agent, "ui", None)
        if existing is not None and hasattr(existing, "on_event"):
            if getattr(existing, "ui_id", "") == "console":
                self._started = True
                return
            # 静默渲染器（如注册表单例 WebUI）：退订，换可见渲染器
            listener = getattr(agent, "_ui_listener", None)
            if listener is not None:
                try:
                    engine._bus.unsubscribe(listener)
                except Exception:  # noqa: BLE001
                    pass
                agent._ui_listener = None
            agent.ui = None
        # 挂载可见渲染器
        if self.renderer is None:
            self.renderer = _HeadlessRenderer(stream=self._stream)
        engine.subscribe_ui(self.renderer)
        self._started = True

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def is_alive(self) -> bool:
        return self._started


__all__ = ["HeadlessFrontend"]

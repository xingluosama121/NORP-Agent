# Copyright (c) 2026 xingluosama121, MIT Licensed
"""控制台前端：命令行交互 REPL 外壳（显式指定 frontend 时使用）。

两种运行模式，自动检测：

- **脚本模式**（默认）：后台线程循环读取 stdin，把每一行提交给
  引擎执行；
- **REPL 同步模式**（在 Python 交互式解释器中调用 np() 时）：
  输入循环直接运行在调用线程（主线程），np() 阻塞直到用户退出。

  交互式解释器（>>> REPL）的主线程也在读 stdin，若用后台线程会
  与 REPL 竞争输入（用户打的字被两个输入方随机抢走）；且 Ctrl+C
  只会投递给主线程，后台 input() 线程永远无法退出——进程收尾时
  还会刷「signal wakeup fd / WinError 10038」噪音。同步模式彻底
  消除这些问题。

内置命令：

    /exit  /quit  exit  quit  exit()  quit()   退出应用
    /help                                      显示帮助
    /reset                                     开启新会话

事件渲染委托给 ConsoleUI（可被 ui 槽位整体替换）。
配合主线程的轮询循环（见 norpagent.runtime）即为命令行形态的
Agent 应用：

    import norpagent as np
    np(frontend="norpagent.frontends.console:ConsoleFrontend")
    while True:
        if np.stop():
            break
"""

from __future__ import annotations

import sys
import threading
from typing import Any, Optional

from norpagent.frontends.base import Frontend


class ConsoleFrontend:
    """stdin 输入 → 引擎执行 → 事件渲染（显式指定，Web 为默认前端）。"""

    frontend_id = "console"

    #: 退出命令（全部等价于 /exit）
    EXIT_COMMANDS = ("/exit", "/quit", "exit", "quit", "exit()", "quit()")

    def __init__(
        self,
        renderer: Optional[Any] = None,
        stream: Any = None,
        **kwargs: Any,
    ) -> None:
        # renderer：事件渲染器（UIAdapter）。缺省用 ConsoleUI。
        # stream：渲染输出流（缺省 sys.stdout）。
        self.renderer = renderer
        self._stream = stream
        self._engine: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._running_inline = False
        self._session_id: Optional[str] = None
        self._stop = threading.Event()

    # ── Frontend 协议 ────────────────────────────────────

    def attach(self, engine: Any) -> None:
        self._engine = engine
        # 优先复用引擎 Agent 运行时的渲染器（避免同一事件被渲染两次）
        agent = getattr(engine, "agent", None)
        existing = getattr(agent, "ui", None)
        if existing is not None and hasattr(existing, "on_event"):
            self.renderer = existing
            return
        if self.renderer is None:
            from norpagent.builtin.ui.console import ConsoleUI

            self.renderer = ConsoleUI(stream=self._stream, verbose=True)
        engine.subscribe_ui(self.renderer)

    def start(self) -> None:
        if self._thread is not None or self._running_inline:
            return
        self._stop.clear()
        if self._interactive_shell():
            # REPL 同步模式：主线程独占 stdin，输入循环就地运行。
            # np() 会阻塞到用户退出（/exit、exit()、Ctrl+C、EOF）。
            self._running_inline = True
            try:
                self._input_loop()
            finally:
                self._running_inline = False
            return
        self._thread = threading.Thread(
            target=self._input_loop, name="norpagent-frontend-console",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # 阻塞在 input() 的线程无法被事件唤醒：等待其自行退出。
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def is_alive(self) -> bool:
        return bool(
            self._running_inline
            or (self._thread is not None and self._thread.is_alive())
        )

    # ── 模式检测 ─────────────────────────────────────────

    @staticmethod
    def _interactive_shell() -> bool:
        """是否运行在 Python 交互式解释器中（REPL / python -i）。"""
        return getattr(sys, "ps1", None) is not None

    # ── 输入循环 ─────────────────────────────────────────

    def _input_loop(self) -> None:
        print("norpagent 已启动。输入消息与 Agent 对话；/exit 退出。")
        try:
            while not self._stop.is_set():
                line = self._read_line()
                if line is None:  # EOF（Ctrl+Z）或 Ctrl+C：干净退出
                    break
                text = line.strip()
                if not text:
                    continue
                if text in self.EXIT_COMMANDS:
                    print("再见。")
                    break
                if text == "/help":
                    print("  /exit /quit exit quit exit() quit()   退出应用\n"
                          "  /reset                               开启新会话")
                    continue
                if text == "/reset":
                    self._session_id = None
                    print("已开启新会话")
                    continue
                engine = self._engine
                if engine is None:
                    continue
                try:
                    result = engine.submit(text, session_id=self._session_id)
                except KeyboardInterrupt:
                    # 任务执行中被 Ctrl+C 打断：同样干净退出
                    print("\n任务已中断（Ctrl+C），正在退出。")
                    break
                except RuntimeError:
                    break  # 引擎已停止
                self._session_id = getattr(result, "session_id", None)
        finally:
            # 输入循环结束 = 用户退出 = 整个应用请求停止
            engine = self._engine
            if engine is not None:
                engine.request_stop()

    def _read_line(self) -> Optional[str]:
        """读取一行输入（EOF / Ctrl+C / stdin 关闭时返回 None）。"""
        try:
            return input("> ")
        except KeyboardInterrupt:
            print("\n已收到 Ctrl+C，退出。")
            return None
        except EOFError:
            print()
            return None
        except (OSError, ValueError):
            # stdin 已关闭（如宿主进程退出阶段）：视为 EOF
            return None


__all__ = ["ConsoleFrontend"]

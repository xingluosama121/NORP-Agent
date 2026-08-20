# Vibe Coding Agent - 屏幕左上角常驻横幅 UI（视觉/操作外挂）
# Copyright (c) 2026 xingluosama
#
# 横幅需求（已定稿，见 vision_daemon.BannerController 注释）：
#   - 三击横幅熔断：两次点击间隔 5ms~500ms（过快/过慢不算）
#   - 空闲默认自动隐藏（可配置：banner.auto_hide=false 时灰色待机）
#   - 长按可拖动（按住超过 long_press_ms 后进入拖动模式）
#   - 操作中绿色，熔断后红色
#
# 实现：Tkinter 无边框置顶小窗 + 命令队列（tkinter 线程亲和性：所有
# widget 操作都在 UI 线程完成，外部线程只投递命令）。支持两种运行模式：
#   - 主线程模式（默认，daemon main() 使用）：Tk 在宿主主线程创建，
#     serve() 在主线程跑消息循环（无 Tcl_AsyncDelete 跨线程告警）；
#   - 线程模式：专用 UI 线程 + after 链轮询（独立使用 / 测试场景）。
# daemon 通过 BannerController.set_state() 驱动；三击/拖动在 UI 线程内
# 直接判定，三击命中后回调 on_triple_click（daemon 侧熔断旁路）。
#
# 自研约束：仅标准库（tkinter 为 CPython 标准库随附）+ vision_daemon 契约。

import queue
import threading
import time
from typing import Callable, Optional

from vision_daemon import BannerController

# 状态样式（颜色 / 文案）。无 Emoji，纯文本符号。
STYLES = {
    "green": {
        "bg": "#1e8e3e",
        "fg": "#ffffff",
        "text": "[!] Agent 正在操控电脑 - 按 Ctrl+End 停止",
    },
    "red": {
        "bg": "#c5221f",
        "fg": "#ffffff",
        "text": "[X] 已熔断：操作已停止",
    },
    "idle": {
        "bg": "#5f6368",
        "fg": "#ffffff",
        "text": "[o] 视觉外挂待机中",
    },
}
PAUSED_SUFFIX = "  [用户接管中]"
FONT = ("Microsoft YaHei UI", 10, "bold")
INIT_POS_X = 8
INIT_POS_Y = 8
UI_POLL_MS = 40          # UI 线程轮询命令队列周期
UI_READY_TIMEOUT = 5.0   # 等 Tk 创建完成的超时（秒）


class TripleClickDetector:
    """三击判定纯逻辑（与 UI 解耦，便于 dry-run 测试）。

    规则：两次点击间隔在 [min_interval_ms, max_interval_ms] 内才算连击，
    过快（合成/抖动事件）或过慢都重置计数；连续三次有效连击触发。
    """

    def __init__(self, min_interval_ms: float = 5.0,
                 max_interval_ms: float = 500.0):
        self.min_interval_ms = float(min_interval_ms)
        self.max_interval_ms = float(max_interval_ms)
        self._last_t_ms: Optional[float] = None
        self._count = 0

    def click(self, now_ms: float) -> bool:
        """记录一次点击；返回 True 表示三击触发（触发后计数自动重置）。"""
        if self._last_t_ms is None:
            dt = None
        else:
            dt = now_ms - self._last_t_ms
        if dt is not None and self.min_interval_ms <= dt <= self.max_interval_ms:
            self._count += 1
        else:
            self._count = 1
        self._last_t_ms = now_ms
        if self._count >= 3:
            self.reset()
            return True
        return False

    def reset(self) -> None:
        self._last_t_ms = None
        self._count = 0


class VisionBanner(BannerController):
    """Tkinter 无边框置顶横幅。UI 运行在专用线程，set_state/close 线程安全。"""

    def __init__(self, on_triple_click: Optional[Callable[[], None]] = None,
                 opacity: float = 0.92, long_press_ms: int = 300,
                 triple_min_ms: int = 5, triple_max_ms: int = 500,
                 main_thread: bool = False,
                 log: Optional[Callable[[str], None]] = None):
        self._on_triple_click = on_triple_click
        self._opacity = max(0.3, min(1.0, float(opacity)))
        self._long_press_sec = max(0, int(long_press_ms)) / 1000.0
        self._detector = TripleClickDetector(triple_min_ms, triple_max_ms)
        self._main_thread = bool(main_thread)
        self._log = log or (lambda s: None)

        self._q: "queue.Queue" = queue.Queue()
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._close_requested = False

        # 以下成员只在 UI 线程（主线程模式 = 宿主主线程）访问
        self._root = None
        self._label = None
        self._press_mono = 0.0
        self._dragging = False
        self._drag_off_x = 0
        self._drag_off_y = 0

    # ── BannerController 契约 ─────────────────────────────────────

    def set_state(self, color: str, visible: bool,
                  paused: bool = False) -> None:
        """color: "green" | "red" | "idle" | "hidden"。线程安全（投递队列）。"""
        self._q.put(("set", color, visible, paused))

    def close(self) -> None:
        """销毁横幅并回收 UI 线程。幂等。"""
        if self._close_requested:
            return
        self._close_requested = True
        if self._main_thread or self._thread is None:
            # 主线程模式：UI 归宿主主线程所有，直接销毁（调用方保证
            # 不在 serve() 循环内调用）；否则由队列命令驱动。
            self._destroy_ui()
            return
        self._q.put(("close",))
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    # ── 生命周期 ──────────────────────────────────────────────────

    def start(self) -> bool:
        """完成初始化；成功返回 True。

        - 线程模式：起专用 UI 线程并等待 Tk 创建完成。
        - 主线程模式：在调用方线程（宿主主线程）同步创建 Tk，
          之后由宿主在主线程跑 serve()。
        tkinter 的 import 与 Tk 生命周期始终在同一线程完成，避免
        Tcl_AsyncDelete 跨线程告警。
        """
        if self._main_thread:
            try:
                import tkinter as tk
                self._init_ui(tk)
                self._ready.set()
                return True
            except ImportError as e:
                self._log(f"[banner] tkinter 不可用，横幅禁用：{e}")
                self._closed.set()
                return False
            except Exception as e:
                self._log(f"[banner] 横幅 UI 初始化失败：{e}")
                self._closed.set()
                return False
        self._thread = threading.Thread(
            target=self._ui_loop, name="vision-banner-ui", daemon=True)
        self._thread.start()
        ok = self._ready.wait(UI_READY_TIMEOUT) and not self._closed.is_set()
        if not ok and self._thread is not None:
            self._thread.join(timeout=1.0)
        return ok

    def serve(self, stop_check: Callable[[], bool]) -> None:
        """主线程模式专用：宿主主线程消息循环，直到 stop_check() 为真、
        收到 close 命令或窗口被销毁。退出时保证 UI 已销毁。"""
        if not self._main_thread or self._root is None:
            return
        try:
            import tkinter as tk
        except ImportError:
            self._closed.set()
            return
        while not stop_check() and not self._closed.is_set():
            self._poll()
            if self._closed.is_set():
                break
            try:
                self._root.update()
            except tk.TclError:
                break
            time.sleep(0.01)
        self._destroy_ui()

    # ── UI 线程 / 主线程共用初始化 ────────────────────────────────

    def _init_ui(self, tk) -> None:
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", self._opacity)
        root.withdraw()  # 初始隐藏，等 daemon 下发首个状态

        label = tk.Label(root, text="", font=FONT, padx=14, pady=6, bd=0)
        label.pack()
        label.bind("<Button-1>", self._on_press)
        label.bind("<ButtonRelease-1>", self._on_release)
        label.bind("<B1-Motion>", self._on_motion)

        root.geometry(f"+{INIT_POS_X}+{INIT_POS_Y}")
        if not self._main_thread:
            root.after(UI_POLL_MS, self._poll)  # 线程模式：after 链轮询
        self._root = root
        self._label = label

    def _destroy_ui(self) -> None:
        if self._root is not None:
            try:
                self._root.destroy()
            except Exception:
                pass
            self._root = None
        self._closed.set()

    def _ui_loop(self) -> None:
        try:
            import tkinter as tk
        except ImportError as e:
            self._log(f"[banner] tkinter 不可用，横幅禁用：{e}")
            self._ready.set()   # 初始化尝试结束（失败），让 start() 立即返回
            self._closed.set()
            return
        try:
            self._init_ui(tk)
            self._ready.set()
            self._log("[banner] 横幅 UI 已就绪（左上角）")
            self._root.mainloop()
        except Exception as e:
            self._log(f"[banner] 横幅 UI 线程异常：{e}")
        finally:
            self._closed.set()

    def _poll(self) -> None:
        """UI 线程周期轮询命令队列（避免跨线程直接操作 widget）。

        线程模式由 after 链驱动；主线程模式由 serve() 循环直接调用。
        """
        if self._root is None:
            return
        try:
            while True:
                cmd = self._q.get_nowait()
                if cmd[0] == "close":
                    self._destroy_ui()
                    return
                if cmd[0] == "set":
                    self._apply_state(cmd[1], cmd[2], cmd[3])
        except queue.Empty:
            pass
        if self._closed.is_set() or self._main_thread:
            return
        self._root.after(UI_POLL_MS, self._poll)

    def _apply_state(self, color: str, visible: bool, paused: bool) -> None:
        if not visible or color == "hidden":
            self._root.withdraw()
            return
        style = STYLES.get(color, STYLES["idle"])
        text = style["text"]
        if paused and color in ("green", "red"):
            text += PAUSED_SUFFIX
        self._label.config(text=text, bg=style["bg"], fg=style["fg"])
        self._root.deiconify()
        self._root.lift()

    # ── 三击 / 长按拖动（UI 线程内事件回调） ──────────────────────

    def _on_press(self, ev) -> None:
        self._press_mono = time.monotonic()
        self._dragging = False
        self._drag_off_x = ev.x_root - self._root.winfo_x()
        self._drag_off_y = ev.y_root - self._root.winfo_y()

    def _on_motion(self, ev) -> None:
        if self._dragging:
            x = ev.x_root - self._drag_off_x
            y = ev.y_root - self._drag_off_y
            self._root.geometry(f"+{x}+{y}")
            return
        if time.monotonic() - self._press_mono >= self._long_press_sec:
            self._dragging = True

    def _on_release(self, ev) -> None:
        was_drag = self._dragging
        self._dragging = False
        if was_drag:
            self._detector.reset()  # 拖动打断三击节奏
            return
        if self._detector.click(time.monotonic() * 1000.0):
            cb = self._on_triple_click
            if cb is not None:
                try:
                    cb()
                except Exception as e:
                    self._log(f"[banner] 三击回调异常：{e}")


def create_daemon_banner(banner_cfg: dict,
                         on_triple_click: Optional[Callable[[], None]],
                         log: Optional[Callable[[str], None]] = None,
                         main_thread: bool = False
                         ) -> Optional[VisionBanner]:
    """工厂：按 daemon 配置创建横幅；任何失败都返回 None（横幅禁用，
    绝不影响 daemon 主体运行）。banner_cfg 结构见 vision_daemon._load_config。

    main_thread=True：在调用方线程（daemon 宿主主线程）同步创建 Tk，
    之后宿主必须用 serve() 跑消息循环；否则起专用 UI 线程。
    两种模式下 tkinter 的 import 与 Tk 生命周期都在同一线程完成。
    """
    log = log or (lambda s: None)
    try:
        banner = VisionBanner(
            on_triple_click=on_triple_click,
            opacity=float(banner_cfg.get("opacity", 0.92)),
            long_press_ms=int(banner_cfg.get("long_press_ms", 300)),
            triple_min_ms=int(banner_cfg.get("triple_click_min_ms", 5)),
            triple_max_ms=int(banner_cfg.get("triple_click_max_ms", 500)),
            main_thread=main_thread,
            log=log,
        )
        if banner.start():
            return banner
        banner.close()
    except Exception as e:
        log(f"[banner] 创建失败，横幅禁用：{e}")
    return None

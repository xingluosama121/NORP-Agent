# Vibe Coding Agent - 视觉/操作外挂进程（独立进程，可被杀可重启）
# Copyright (c) 2026 xingluosama
#
# 定位（docs/vision_agent_design.md 第 3 节进程拓扑）：
#   主架构只发指令 + 一次性授权令牌 + 读审计；本进程是「裁决权唯一归属地」：
#   SafetyArbiter / VisionCoordinator / 热键熔断 / 在场检测 / 审计落盘
#   全部在这里，Agent（及主架构）只能申请，不能裁决自己。
#
# 自研约束：仅 Python 标准库（socket/threading/queue/hmac/xml/json/ctypes），
# 视觉/捕获/键鼠复用既有 vision_* 模块（同样只依赖标准库 + Pillow + ctypes Win32）。
#
# 运行方式：
#   python vision_daemon.py --start [--port N] [--app-dir DIR] [--allow-admin] [--fg]
#   python vision_daemon.py --stop  [--app-dir DIR]     # 客户端方式优雅停机
#   python vision_daemon.py --status [--app-dir DIR]    # 客户端方式查状态
#
# 文件约定（app_dir 内）：
#   vision_daemon.lock    —— 端口/secret/pid（发现机制，协议文档 1.1）
#   vision_daemon_config.json —— 安全参数 + 视觉 provider 配置（主架构写入）
#   vision_state.json     —— 裁决器状态（熔断/失败计数/delegate，原子写）
#   vision_audit.jsonl    —— 审计日志（追加写，主架构只读）

import argparse
import ctypes
import json
import os
import queue
import secrets
import socket
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from vision_ipc import (
    EventType,
    IPCError,
    Message,
    PROTOCOL_VERSION,
    ResultStatus,
    TOKEN_FRESHNESS_SEC,
    parse_message,
    verify_op_token,
)
from vision_ipc_transport import (
    ConnectionClosed,
    FrameSocket,
    IDLE_TIMEOUT,
    TokenReplayGuard,
    TransportError,
    VipcServer,
    _now_ms_int,
)

LOCK_FNAME = "vision_daemon.lock"
CONFIG_FNAME = "vision_daemon_config.json"
STATE_FNAME = "vision_state.json"
AUDIT_FNAME = "vision_audit.jsonl"

DEFAULT_PORT = 38476
PORT_TRY_RANGE = 16          # 端口被占用时向后顺延尝试的次数
HOTKEY_ID = 0xC0DE           # RegisterHotKey 热键 ID（Ctrl+End）
PRESENCE_INTERVAL = 1.0      # 在场检测周期（秒）
EXEC_TIMEOUT = 300.0         # 动作类 op 最大执行等待（秒）
LOOK_TTL_MS = 120000         # look 类长请求 ttl
ACTION_TTL_MS = 60000        # 动作类 op ttl

# op → 固定风险级白名单（daemon 二次核对，主架构/LLM 不可自报）
OP_RISK_MAP = {
    "list_windows": "L0",
    "look": "L0",
    "state": "L0",
    "move": "L1",
    "scroll": "L1",
    "click": "L2",
    "type": "L2",
    "key": "L2",
    "delegate": "L0",
    "veto": "L0",
    "manual_reset": "L0",
    "resume_override": "L0",
    "reload_config": "L0",
    "ping": "L0",
    "shutdown": "L0",
}
# 动作类 op（有物理副作用，必须串行执行）
ACTION_OPS = {"move", "scroll", "click", "type", "key"}
# 必须 confirm="user" 的 op（安全关键操作）
CONFIRM_REQUIRED_OPS = {"shutdown", "manual_reset", "resume_override"}


# ═══════════════════════════════════════════════════════════════
#  横幅控制器接口（实现：vision_banner.VisionBanner）
# ═══════════════════════════════════════════════════════════════
#
# 横幅需求（已定稿）：
#   - 三击横幅熔断：两次点击间隔 5ms~500ms（过快/过慢不算）
#   - 空闲默认自动隐藏（可配置：banner.auto_hide=false 时灰色待机）
#   - 长按可拖动
#   - 操作中绿色，熔断后红色
# daemon 通过本接口驱动横幅状态机，并把 banner_changed 事件推给主架构；
# 渲染/三击判定由 vision_banner.VisionBanner 实现（start() 自动接入），
# daemon 不依赖其存在（None = 禁用）。

class BannerController:
    """横幅控制器抽象接口（daemon 侧契约）。"""

    def set_state(self, color: str, visible: bool,
                  paused: bool = False) -> None:
        """color: "green" | "red" | "idle" | "hidden"。"""
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════════

def is_user_admin() -> bool:
    """IsUserAnAdmin 自检（宪法约束：外挂不提权）。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def get_last_input_tick() -> Optional[int]:
    """GetLastInputInfo：系统最近一次键鼠输入的 tick（GetTickCount 基准）。"""
    try:
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT),
                        ("dwTime", wintypes.DWORD)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return int(lii.dwTime)
    except Exception:
        pass
    return None


def atomic_write_json(path: str, data: dict) -> None:
    """原子写 JSON（临时文件 + os.replace，避免读到半截）。"""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def append_jsonl(path: str, records: List[dict]) -> None:
    """追加审计记录（只追加不修改）。"""
    if not records:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ═══════════════════════════════════════════════════════════════
#  执行线程专用 executor：注入后吃掉输入 tick（区分 Agent 输入与用户输入）
# ═══════════════════════════════════════════════════════════════

class _TickTrackerExecutor:
    """包装 SendInputExecutor：每次注入完成后把 GetLastInputInfo 的 tick
    记为「Agent 基线」，在场检测线程据此区分「Agent 自己的输入」与
    「用户介入的输入」（tick 再次变化 = 用户动了键盘鼠标 → override）。
    """

    def __init__(self, inner: Any, on_after_input: Callable[[], None]):
        self._inner = inner
        self._on_after_input = on_after_input

    def execute(self, spec, before) -> None:
        try:
            self._inner.execute(spec, before)
        finally:
            self._on_after_input()


# ═══════════════════════════════════════════════════════════════
#  外挂进程主体
# ═══════════════════════════════════════════════════════════════

class VisionDaemon:
    """视觉/操作外挂进程：socket 服务 + 裁决器 + 热键 + 在场检测 + 审计。"""

    def __init__(self, *, app_dir: str, port: Optional[int] = None,
                 allow_admin: bool = False,
                 banner: Optional[BannerController] = None,
                 enable_banner: bool = True,
                 log: Optional[Callable[[str], None]] = None):
        self.app_dir = app_dir
        self.allow_admin = allow_admin
        self.banner = banner
        self._enable_banner = enable_banner
        self.log = log or (lambda s: None)

        self.port = int(port) if port else DEFAULT_PORT
        self.secret = secrets.token_hex(16)
        self._pid = os.getpid()
        self._lock_path = os.path.join(app_dir, LOCK_FNAME)
        self._config_path = os.path.join(app_dir, CONFIG_FNAME)
        self._state_path = os.path.join(app_dir, STATE_FNAME)
        self._audit_path = os.path.join(app_dir, AUDIT_FNAME)

        self._arbiter = None            # 延迟构建（先读 config）
        self._vision_cfg: Dict[str, Any] = {}
        self._use_framesource = False
        self._banner_cfg: Dict[str, Any] = {}
        self._banner_handled = False    # 横幅接入是否已处理（防重复创建）
        self._config_loaded = False     # 配置是否已加载（幂等）

        # 会话表：session_id → FrameSocket
        self._sessions: Dict[str, FrameSocket] = {}
        self._sessions_lock = threading.Lock()

        # 动作串行执行（maxsize=1：满则拒绝新动作，防堆积）
        self._exec_queue: "queue.Queue" = queue.Queue(maxsize=1)
        self._exec_thread: Optional[threading.Thread] = None

        # 在场检测 / 输入 tick
        self._last_input_tick: Optional[int] = None
        self._idle_flag = False
        self._presence_thread: Optional[threading.Thread] = None

        # 热键
        self._hotkey_thread: Optional[threading.Thread] = None
        self._hotkey_thread_id: Optional[int] = None

        self._server: Optional[VipcServer] = None
        self._replay_guard = TokenReplayGuard()
        self._shutdown_reason: Optional[str] = None
        self._banner_state = {"color": "hidden", "visible": False, "paused": False}

    # ═══════════════════════════════════════════════════════════
    #  启动 / 关闭
    # ═══════════════════════════════════════════════════════════

    def start(self) -> None:
        self._self_check()
        os.makedirs(self.app_dir, exist_ok=True)
        self._port = self._bind_port(self.port)
        self._write_lock()
        self._load_config()
        self._build_arbiter()
        self._attach_banner()

        # 输入 tick 基线（在场检测）
        self._last_input_tick = get_last_input_tick()
        if self._last_input_tick is not None:
            self._arbiter.touch_presence(time.time())

        # 动作执行线程（串行）
        self._exec_thread = threading.Thread(
            target=self._exec_loop, name="vision-daemon-exec", daemon=True)
        self._exec_thread.start()

        # 热键线程（Ctrl+End 物理熔断）
        self._start_hotkey_thread()

        # 在场检测线程
        self._presence_thread = threading.Thread(
            target=self._presence_loop, name="vision-daemon-presence", daemon=True)
        self._presence_thread.start()

        # socket 服务
        self._server = VipcServer(self._port, self.secret, log=self.log)
        self._server.on_session = self.on_session  # 绑定会话业务回调
        self._server.start()
        self.log(f"[daemon] 启动完成 port={self._port} app_dir={self.app_dir}")

    def wait(self) -> None:
        """驻留：阻塞到收到 shutdown。"""
        while self._shutdown_reason is None:
            time.sleep(0.2)
        self._finalize()

    def _self_check(self) -> None:
        if is_user_admin() and not self.allow_admin:
            raise RuntimeError(
                "拒绝以管理员权限启动（外挂不提权，宪法约束）。"
                "如需强制运行请显式 --allow-admin（风险自担）。")

    def _bind_port(self, start_port: int) -> int:
        """尝试绑定端口：被占用则 +1 顺延（最多 PORT_TRY_RANGE 次）。"""
        for offset in range(PORT_TRY_RANGE):
            port = start_port + offset
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", port))
                sock.close()
                return port
            except OSError:
                sock.close()
                continue
        raise RuntimeError(
            f"端口 {start_port}~{start_port + PORT_TRY_RANGE - 1} 全部被占用")

    def _write_lock(self) -> None:
        atomic_write_json(self._lock_path, {
            "version": 1,
            "pid": self._pid,
            "port": self._port,
            "secret": self.secret,
            "started_at": time.time(),
        })

    def _remove_lock(self) -> None:
        try:
            if os.path.isfile(self._lock_path):
                os.remove(self._lock_path)
        except OSError:
            pass

    # ═══════════════════════════════════════════════════════════
    #  配置 / 裁决器
    # ═══════════════════════════════════════════════════════════

    def _load_config(self, force: bool = False) -> None:
        """读 vision_daemon_config.json（主架构拉起前写入）。

        结构：{"arbiter": {...安全参数}, "vision": {...provider 配置},
               "framesource": bool, "banner": {...横幅配置}}
        文件缺失/损坏 → 全部默认值（裁决器默认最保守）。
        幂等：已加载则跳过；force=True 强制重读（reload_config op）。
        """
        if self._config_loaded and not force:
            return
        cfg: Dict[str, Any] = {}
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg = data
        except (OSError, json.JSONDecodeError):
            pass
        self._arbiter_cfg = cfg.get("arbiter") or {}
        self._vision_cfg = cfg.get("vision") or {}
        self._use_framesource = bool(cfg.get("framesource", False))
        # 横幅配置：enabled（是否启用）/ auto_hide（空闲自动隐藏）/
        # opacity（不透明度）/ long_press_ms（长按拖动阈值）/
        # triple_click_min_ms / triple_click_max_ms（三击熔断间隔窗口）
        self._banner_cfg = cfg.get("banner") or {}
        self._config_loaded = True

    def _build_arbiter(self) -> None:
        from vision_safety import SafetyArbiter

        ac = self._arbiter_cfg
        old = self._arbiter
        arbiter = SafetyArbiter(
            idle_timeout_sec=float(ac.get("idle_timeout_sec", 150.0)),
            idle_allow_operate=bool(ac.get("idle_allow_operate", False)),
            delegate_window_sec=float(ac.get("delegate_window_sec", 300.0)),
            delegate_scope=str(ac.get("delegate_scope", "window")),
            delegate_max_risk=str(ac.get("delegate_max_risk", "L2")),
            cooldown_sec=float(ac.get("cooldown_sec", 60.0)),
            max_failures=int(ac.get("max_failures", 3)),
        )
        # 状态迁移：旧实例（含重启前的落盘状态）→ 新实例
        if old is not None:
            arbiter.import_state(old.export_state())
        else:
            try:
                with open(self._state_path, "r", encoding="utf-8") as f:
                    arbiter.import_state(json.load(f))
            except (OSError, json.JSONDecodeError):
                pass
        self._arbiter = arbiter

    def _save_state(self) -> None:
        if self._arbiter is not None:
            atomic_write_json(self._state_path, self._arbiter.export_state())

    def _append_audit(self, records) -> None:
        rows = [{
            "ts": r.ts, "op": r.op, "risk": r.risk,
            "target_window": r.target_window, "decision": r.decision,
            "reason": r.reason, "user_confirmed": r.user_confirmed,
        } for r in records]
        append_jsonl(self._audit_path, rows)

    # ═══════════════════════════════════════════════════════════
    #  横幅接入（屏幕左上角：三击熔断 / 长按拖动 / 颜色状态）
    # ═══════════════════════════════════════════════════════════

    def _attach_banner(self) -> None:
        """启动时按配置创建横幅（vision_banner.VisionBanner，线程模式）。

        已注入 banner 或 enable_banner=False 或配置 banner.enabled=false
        时不创建；已由 prepare_banner_mainthread() 处理过则不重复创建。
        创建失败仅记日志，绝不影响 daemon 主体运行。
        """
        if self.banner is not None:
            return
        if getattr(self, "_banner_handled", False):
            return
        self._banner_handled = True
        if not self._enable_banner:
            self.log("[daemon] 横幅已禁用（--no-banner）")
            return
        if not self._banner_cfg.get("enabled", True):
            self.log("[daemon] 横幅已禁用（banner.enabled=false）")
            return
        try:
            from vision_banner import create_daemon_banner
        except ImportError as e:
            self.log(f"[daemon] 横幅模块不可用，横幅禁用：{e}")
            return
        banner = create_daemon_banner(
            self._banner_cfg,
            on_triple_click=self._banner_triple_click,
            log=self.log)
        if banner is not None:
            self.banner = banner
            self.log("[daemon] 横幅已启用（三击熔断 / 长按拖动）")

    def prepare_banner_mainthread(self):
        """主线程模式接入（main() 使用）：在调用方（主）线程同步创建
        Tk 横幅，之后主线程用其 serve() 跑消息循环——tkinter 生命周期
        全程在主线程，无 Tcl 跨线程告警。

        返回 VisionBanner 或 None（禁用/失败）。调用后 daemon.start()
        里的 _attach_banner 不会再重复创建。
        """
        self._load_config()  # 可能在 start() 之前被调用（配置幂等加载）
        if self.banner is not None:
            self._banner_handled = True
            return self.banner
        self._banner_handled = True
        if not self._enable_banner:
            self.log("[daemon] 横幅已禁用（--no-banner）")
            return None
        if not self._banner_cfg.get("enabled", True):
            self.log("[daemon] 横幅已禁用（banner.enabled=false）")
            return None
        try:
            from vision_banner import create_daemon_banner
        except ImportError as e:
            self.log(f"[daemon] 横幅模块不可用，横幅禁用：{e}")
            return None
        banner = create_daemon_banner(
            self._banner_cfg,
            on_triple_click=self._banner_triple_click,
            log=self.log,
            main_thread=True)
        if banner is not None:
            self.banner = banner
            self.log("[daemon] 横幅已启用（主线程渲染，三击熔断 / 长按拖动）")
        return banner

    def _banner_triple_click(self) -> None:
        """横幅三击：与 Ctrl+End 同级的物理熔断旁路（不经 IPC / Agent）。"""
        if self._arbiter is not None:
            self._arbiter.hotkey_emergency_stop()
        self.log("[daemon] 横幅三击熔断：熔断机 -> OPEN")
        self._save_state()
        self._push_event(EventType.CIRCUIT_OPENED,
                         {"reason": "banner_triple_click", "circuit": "OPEN"})
        self._sync_banner()

    # ═══════════════════════════════════════════════════════════
    #  热键线程（Ctrl+End 物理熔断：不经 IPC / 不经 Agent）
    # ═══════════════════════════════════════════════════════════

    def _start_hotkey_thread(self) -> None:
        self._hotkey_thread = threading.Thread(
            target=self._hotkey_loop, name="vision-daemon-hotkey", daemon=True)
        self._hotkey_thread.start()

    def _hotkey_loop(self) -> None:
        """RegisterHotKey(Ctrl+End) + 仅消息窗口（HWND_MESSAGE）消息循环。"""
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        # HWND_MESSAGE=-3 必须以指针宽度符号扩展（否则 x64 下零扩展成
        # 0x00000000FFFFFFFD → ERROR_INVALID_WINDOW_HANDLE）。
        HWND_MESSAGE = wintypes.HWND(-3)

        # 声明签名：64 位句柄/长参不能被默认 c_int 截断。
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.RegisterHotKey.argtypes = [
            wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == 0x0312:  # WM_HOTKEY
                if wparam == HOTKEY_ID:
                    self._on_hotkey()
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        try:
            self._hotkey_thread_id = kernel32.GetCurrentThreadId()
            wnd_proc_type = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                wintypes.WPARAM, wintypes.LPARAM)
            wnd_proc = wnd_proc_type(_wnd_proc)

            # ctypes.wintypes 未提供 WNDCLASSW / WNDPROC / HCURSOR，这里自建
            # （HCURSOR 本质是 HANDLE 别名，布局一致）。
            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", wnd_proc_type),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            wc = WNDCLASSW()
            wc.lpfnWndProc = wnd_proc
            wc.hInstance = kernel32.GetModuleHandleW(None)
            wc.lpszClassName = "VisionDaemonHotkeySink"
            if not user32.RegisterClassW(ctypes.byref(wc)):
                self.log("[daemon] 警告：热键消息窗口类注册失败，Ctrl+End 熔断不可用")
                return
            hwnd = user32.CreateWindowExW(
                0, wc.lpszClassName, "", 0, 0, 0, 0, 0,
                HWND_MESSAGE, None, wc.hInstance, None)
            if not hwnd:
                self.log("[daemon] 警告：热键消息窗口创建失败，Ctrl+End 熔断不可用")
                return
            if not user32.RegisterHotKey(hwnd, HOTKEY_ID, 0x0002, 0x23):  # MOD_CONTROL + VK_END
                self.log("[daemon] 警告：Ctrl+End 已被占用，热键熔断不可用")

            msg = wintypes.MSG()
            while self._shutdown_reason is None:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            user32.DestroyWindow(hwnd)
            user32.UnregisterClassW("VisionDaemonHotkeySink", wc.hInstance)
        except Exception as e:
            self.log(f"[daemon] 警告：热键线程异常：{e}")

    def _on_hotkey(self) -> None:
        """Ctrl+End：最高优先级旁路，立即熔断 + 事件 + 横幅变红。"""
        if self._arbiter is not None:
            self._arbiter.hotkey_emergency_stop()
        self.log("[daemon] Ctrl+End 物理熔断：熔断机 → OPEN")
        self._save_state()
        self._push_event(EventType.CIRCUIT_OPENED,
                         {"reason": "hotkey", "circuit": "OPEN"})
        self._sync_banner()

    # ═══════════════════════════════════════════════════════════
    #  在场检测 / 介入检测线程
    # ═══════════════════════════════════════════════════════════

    def _presence_loop(self) -> None:
        """每秒：GetLastInputInfo → 在场刷新 + 用户介入检测 + idle 状态翻转。"""
        was_override = False
        while self._shutdown_reason is None:
            time.sleep(PRESENCE_INTERVAL)
            try:
                arbiter = self._arbiter
                if arbiter is None:
                    continue
                tick = get_last_input_tick()
                if tick is None:
                    continue

                # 用户介入检测：tick 变化 = 有新的键鼠输入。
                # Agent 注入后的 tick 已被执行线程吃掉（见 _TickTrackerExecutor），
                # 因此这里的 tick 变化只能来自用户 → notify_user_input。
                if self._last_input_tick is not None and tick != self._last_input_tick:
                    self._last_input_tick = tick
                    arbiter.notify_user_input()
                # 在场检测刷新（时间戳只前进）
                idle_ms = ctypes.windll.kernel32.GetTickCount() - tick
                arbiter.touch_presence(time.time() - max(0, idle_ms) / 1000.0)

                # override 状态翻转 → 事件
                if arbiter.override_active and not was_override:
                    self._push_event(EventType.OVERRIDE_ENGAGED,
                                     {"at": time.time()})
                    self._sync_banner()
                elif was_override and not arbiter.override_active:
                    self._push_event(EventType.OVERRIDE_RESUMED, {})
                    self._sync_banner()
                was_override = arbiter.override_active

                # idle 锁死状态翻转 → 事件
                idle_now = arbiter.idle_locked
                if idle_now and not self._idle_flag:
                    self._push_event(EventType.IDLE_LOCKED,
                                     {"idle_sec": arbiter.idle_timeout_sec})
                elif self._idle_flag and not idle_now:
                    self._push_event(EventType.IDLE_UNLOCKED, {})
                self._idle_flag = idle_now

                # 横幅状态每秒同步（动作执行中 → 绿色由这里驱动）
                self._sync_banner()
            except Exception as e:
                self.log(f"[daemon] 在场检测异常：{e}")

    def _mark_agent_input(self) -> None:
        """执行线程回调：注入完成后吃掉 tick（Agent 基线）。"""
        tick = get_last_input_tick()
        if tick is not None:
            self._last_input_tick = tick

    # ═══════════════════════════════════════════════════════════
    #  动作串行执行线程
    # ═══════════════════════════════════════════════════════════

    class _ExecJob:
        def __init__(self, handler: Callable[[], Message]):
            self.handler = handler
            self.cond = threading.Condition()
            self.result: Optional[Message] = None

        def wait(self, timeout: float) -> Optional[Message]:
            with self.cond:
                self.cond.wait(timeout)
                return self.result

    def _exec_loop(self) -> None:
        while self._shutdown_reason is None:
            try:
                job = self._exec_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                job.result = job.handler()
            except Exception as e:
                job.result = Message(
                    kind="result", op="?", status=ResultStatus.ERROR,
                    payload={"error": f"执行线程异常：{e}"})
            finally:
                with job.cond:
                    job.cond.notify_all()

    # ═══════════════════════════════════════════════════════════
    #  会话处理（VipcServer 回调）
    # ═══════════════════════════════════════════════════════════

    def on_session(self, fsock: FrameSocket, session_id: str,
                   hello: Message) -> None:
        self.log(f"[daemon] 会话建立 session={session_id} role={hello.role}")
        with self._sessions_lock:
            self._sessions[session_id] = fsock
        last_active = time.time()
        try:
            while self._shutdown_reason is None:
                try:
                    xml_str = fsock.recv_raw(IDLE_TIMEOUT)
                except TransportError:
                    # 超时：检查会话空闲时长
                    if time.time() - last_active > IDLE_TIMEOUT:
                        break
                    continue
                last_active = time.time()
                try:
                    msg = parse_message(xml_str)
                except IPCError:
                    break  # 坏消息：断连不猜

                if msg.kind == "ping":
                    fsock.send_message(Message(kind="pong"))
                    continue
                if msg.kind != "op":
                    break  # 会话内只允许 op/ping

                result = self._handle_op(msg)
                fsock.send_message(result)
                if result.op == "shutdown" and self._shutdown_reason is not None:
                    break
        except (ConnectionClosed, TransportError, OSError):
            pass
        except Exception as e:
            self.log(f"[daemon] 会话异常：{e}")
        finally:
            with self._sessions_lock:
                self._sessions.pop(session_id, None)
            self.log(f"[daemon] 会话结束 session={session_id}")

    # ═══════════════════════════════════════════════════════════
    #  op 处理：验签 → 白名单 → 分发
    # ═══════════════════════════════════════════════════════════

    def _handle_op(self, msg: Message) -> Message:
        op = msg.op
        req_id = msg.id
        risk_expected = OP_RISK_MAP.get(op)
        if risk_expected is None:
            return self._result(op, req_id, ResultStatus.REJECTED,
                                {"reason": f"未知 op：{op}"})
        if msg.risk != risk_expected:
            return self._result(
                op, req_id, ResultStatus.REJECTED,
                {"reason": f"风险级不符：op={op} 必须 risk={risk_expected}，收到 {msg.risk}"})
        # 版本 + 令牌验签 + 新鲜度 + 防重放 + ttl
        if msg.version != PROTOCOL_VERSION:
            return self._result(op, req_id, ResultStatus.REJECTED,
                                {"reason": f"协议版本不符：{msg.version}"})
        now_ms = _now_ms_int()
        if msg.ts is None:
            return self._result(op, req_id, ResultStatus.REJECTED,
                                {"reason": "缺少时间戳"})
        if abs(now_ms - msg.ts) > TOKEN_FRESHNESS_SEC * 1000:
            return self._result(op, req_id, ResultStatus.REJECTED,
                                {"reason": "时间戳超出新鲜度窗口"})
        if not verify_op_token(self.secret, op, msg.risk, msg.ts,
                               req_id or "", msg.payload, msg.token):
            return self._result(op, req_id, ResultStatus.REJECTED,
                                {"reason": "令牌校验失败"})
        if not self._replay_guard.check_and_mark(msg.token, msg.ts):
            return self._result(op, req_id, ResultStatus.REJECTED,
                                {"reason": "令牌重放"})
        if msg.ttl_ms is not None and now_ms - msg.ts > msg.ttl_ms:
            return self._result(op, req_id, ResultStatus.TIMEOUT,
                                {"reason": "指令 ttl 超时"})

        try:
            return self._dispatch(op, msg)
        except Exception as e:
            import traceback
            self.log(f"[daemon] op={op} 执行异常：{e}\n{traceback.format_exc(limit=3)}")
            return self._result(op, req_id, ResultStatus.ERROR,
                                {"error": f"{type(e).__name__}: {e}"})

    def _result(self, op: str, req_id: Optional[str], status: str,
                payload: Dict[str, Any]) -> Message:
        return Message(kind="result", op=op, status=status,
                       payload=payload, id=req_id, ts=_now_ms_int())

    def _dispatch(self, op: str, msg: Message) -> Message:
        if op == "ping":
            return self._result(op, msg.id, ResultStatus.OK, {"t": time.time()})
        if op == "state":
            return self._op_state(msg)
        if op == "list_windows":
            return self._op_list_windows(msg)
        if op == "look":
            return self._op_look(msg)
        if op == "veto":
            return self._op_veto(msg)
        if op == "delegate":
            return self._op_delegate(msg)
        if op == "manual_reset":
            return self._op_manual_reset(msg)
        if op == "resume_override":
            return self._op_resume_override(msg)
        if op == "reload_config":
            return self._op_reload_config(msg)
        if op == "shutdown":
            return self._op_shutdown(msg)
        if op in ACTION_OPS:
            return self._op_action(op, msg)
        return self._result(op, msg.id, ResultStatus.REJECTED,
                            {"reason": f"未实现的 op：{op}"})

    # ── L0 只读类 ──

    def _op_state(self, msg: Message) -> Message:
        arbiter = self._arbiter
        d = arbiter.export_state()
        audit_tail: List[dict] = []
        try:
            with open(self._audit_path, "r", encoding="utf-8") as f:
                for line in f.readlines()[-5:]:
                    try:
                        audit_tail.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
        return self._result(msg.op, msg.id, ResultStatus.OK, {
            "arbiter_state": d,
            "audit_tail": audit_tail,
            "banner": dict(self._banner_state),
            "daemon": {"pid": self._pid, "port": self._port},
        })

    def _op_list_windows(self, msg: Message) -> Message:
        from vision_capture import list_capturable_windows
        max_results = int((msg.payload or {}).get("max_results", 30) or 30)
        windows = list_capturable_windows(max_results=max(1, min(max_results, 100)))
        return self._result(msg.op, msg.id, ResultStatus.OK,
                            {"windows": windows})

    def _op_look(self, msg: Message) -> Message:
        from vision_actions import is_window
        from vision_capture import describe_window

        p = msg.payload or {}
        hwnd = int(p.get("hwnd", 0) or 0)
        if not hwnd or not is_window(hwnd):
            return self._result(msg.op, msg.id, ResultStatus.ERROR,
                                {"error": f"窗口无效或已关闭：hwnd={hwnd}"})
        prompt = p.get("prompt") or None
        if prompt is None:
            prompt = (
                "请描述这个窗口的界面布局：列出可见的按钮、输入框、菜单项、"
                "图标及其大致位置，重要控件的中心点请给出「截图坐标 (x, y)」，"
                "坐标原点在图片左上角、单位为像素。如果能看到文字，请一并读出。"
            )
        desc = describe_window(hwnd, self._vision_cfg, prompt=prompt)
        max_chars = int(p.get("max_chars", 2000) or 2000)
        truncated = len(desc) > max_chars
        if truncated:
            desc = desc[:max_chars]
        return self._result(msg.op, msg.id, ResultStatus.OK, {
            "hwnd": hwnd, "description": desc, "truncated": truncated})

    def _op_veto(self, msg: Message) -> Message:
        """主架构审批被拒时上报：用户否决 → 可能触发熔断。"""
        arbiter = self._arbiter
        before = arbiter.circuit.value
        arbiter.user_veto()
        after = arbiter.circuit.value
        p = msg.payload or {}
        self.log(f"[daemon] 用户否决：op={p.get('op')} 熔断={after}")
        self._save_state()
        if before != after and after == "OPEN":
            self._push_event(EventType.CIRCUIT_OPENED,
                             {"reason": "vetoes", "circuit": "OPEN"})
            self._sync_banner()
        return self._result(msg.op, msg.id, ResultStatus.OK, {
            "consecutive_vetoes": arbiter.export_state()["consecutive_vetoes"],
            "circuit": after})

    def _op_delegate(self, msg: Message) -> Message:
        arbiter = self._arbiter
        p = msg.payload or {}
        action = str(p.get("action", "query")).lower()
        if action == "query":
            d = arbiter.export_state().get("delegate")
            return self._result(msg.op, msg.id, ResultStatus.OK,
                                {"delegate": d})
        if action == "revoke":
            arbiter.revoke_delegate()
            self._save_state()
            return self._result(msg.op, msg.id, ResultStatus.OK,
                                {"delegate": None})
        if action == "grant":
            if msg.confirm != "user":
                return self._result(
                    msg.op, msg.id, ResultStatus.REJECTED,
                    {"reason": "让渡确认权必须经用户审批确认（confirm=user）"})
            scope = str(p.get("scope", "window"))
            if scope not in ("window", "app", "session"):
                return self._result(msg.op, msg.id, ResultStatus.REJECTED,
                                    {"reason": "非法 scope"})
            max_risk = str(p.get("max_risk", "L2")).upper()
            if max_risk not in ("L0", "L1", "L2"):
                return self._result(msg.op, msg.id, ResultStatus.REJECTED,
                                    {"reason": "非法 max_risk（L3 永不免确认）"})
            window_sec = float(p.get("window_sec", 300) or 300)
            window_sec = max(10.0, min(window_sec, 86400.0))
            hwnd = int(p.get("hwnd", 0) or 0)
            if scope == "window" and not hwnd:
                return self._result(msg.op, msg.id, ResultStatus.REJECTED,
                                    {"reason": "scope=window 必须提供 hwnd"})
            arbiter.grant_delegate(
                scope=scope, max_risk=max_risk, window_sec=window_sec,
                target_window=str(hwnd) if hwnd else None)
            self._save_state()
            return self._result(msg.op, msg.id, ResultStatus.OK, {
                "delegate": arbiter.export_state().get("delegate")})
        return self._result(msg.op, msg.id, ResultStatus.REJECTED,
                            {"reason": "非法 action（grant/revoke/query）"})

    def _op_manual_reset(self, msg: Message) -> Message:
        arbiter = self._arbiter
        if msg.confirm != "user":
            return self._result(
                msg.op, msg.id, ResultStatus.REJECTED,
                {"reason": "熔断复位必须用户显式确认（confirm=user）"})
        decision = arbiter.manual_reset()
        self._save_state()
        if decision.allowed and arbiter.circuit.value == "HALF_OPEN":
            self._push_event(EventType.CIRCUIT_HALF_OPEN,
                             {"circuit": "HALF_OPEN"})
        self._sync_banner()
        return self._result(msg.op, msg.id,
                            ResultStatus.OK if decision.allowed else ResultStatus.REJECTED,
                            {"allowed": decision.allowed,
                             "reason": decision.reason,
                             "circuit": arbiter.circuit.value})

    def _op_resume_override(self, msg: Message) -> Message:
        if msg.confirm != "user":
            return self._result(
                msg.op, msg.id, ResultStatus.REJECTED,
                {"reason": "恢复操作必须用户显式确认（confirm=user）"})
        self._arbiter.resume_from_override()
        self._save_state()
        self._push_event(EventType.OVERRIDE_RESUMED, {})
        return self._result(msg.op, msg.id, ResultStatus.OK, {})

    def _op_reload_config(self, msg: Message) -> Message:
        self._load_config(force=True)
        self._build_arbiter()
        return self._result(msg.op, msg.id, ResultStatus.OK, {
            "reloaded": True,
            "arbiter": self._arbiter.export_state()})

    def _op_shutdown(self, msg: Message) -> Message:
        if msg.confirm != "user":
            return self._result(
                msg.op, msg.id, ResultStatus.REJECTED,
                {"reason": "shutdown 必须用户显式确认（confirm=user）"})
        result = self._result(msg.op, msg.id, ResultStatus.OK,
                              {"shutting_down": True})
        self._shutdown_reason = "shutdown op"
        return result

    # ── 动作类（串行执行） ──

    def _op_action(self, op: str, msg: Message) -> Message:
        from vision_coordinator import ActionSpec
        from vision_safety import RiskLevel

        arbiter = self._arbiter
        p = dict(msg.payload or {})
        hwnd = int(p.get("hwnd", 0) or 0)
        if op in ("move", "scroll", "click") and hwnd:
            self._check_window(hwnd)
        risk = RiskLevel[OP_RISK_MAP[op]]
        target_window = str(hwnd) if hwnd else None

        # 裁决：L2 的「用户显式确认」来自主架构审批弹窗（confirm="user"）
        user_confirmed = msg.confirm == "user"
        decision = arbiter.evaluate(op, risk, target_window,
                                    user_confirmed=user_confirmed)
        if decision.requires_confirmation:
            return self._result(op, msg.id, ResultStatus.REQUIRES_CONFIRMATION,
                                {"reason": decision.reason})
        if not decision.allowed:
            status = ResultStatus.CIRCUIT_OPEN if "熔断" in decision.reason \
                else ResultStatus.REJECTED
            return self._result(op, msg.id, status,
                                {"reason": decision.reason})

        # 入队串行执行（满则拒绝，防堆积）
        job = self._ExecJob(lambda: self._run_action(op, hwnd, p))
        try:
            self._exec_queue.put_nowait(job)
        except queue.Full:
            return self._result(op, msg.id, ResultStatus.REJECTED,
                                {"reason": "上一条操作尚未完成"})
        self._sync_banner()
        job.wait(EXEC_TIMEOUT)
        result = job.result
        if result is None:
            result = self._result(op, msg.id, ResultStatus.TIMEOUT,
                                  {"reason": "动作执行超时"})
        result.op = op
        result.id = msg.id
        self._save_state()
        self._sync_banner()
        return result

    def _run_action(self, op: str, hwnd: int, p: Dict[str, Any]) -> Message:
        """执行线程内：coordinator 闭环（动作-验证-收敛）。"""
        from vision_coordinator import (
            ActionSpec, CursorPosVerifier, PixelDiffVerifier,
            VisionCoordinator, VisionModelVerifier, SendInputExecutor)

        arbiter = self._arbiter
        spec = ActionSpec(op, OP_RISK_MAP[op], p, target_window=str(hwnd) if hwnd else None)

        # type/key 先尝试把目标窗口带到前台（失败不致命）
        if op in ("type", "key") and hwnd:
            try:
                from vision_actions import set_foreground
                set_foreground(hwnd)
                time.sleep(0.2)
            except Exception:
                pass

        # 验证器选择
        if op == "move":
            verifier = CursorPosVerifier()
        elif (p.get("expect") or "").strip() and \
                (self._vision_cfg.get("provider") or "").strip():
            verifier = VisionModelVerifier(
                self._vision_cfg, expected_desc=str(p.get("expect", "")))
        else:
            verifier = PixelDiffVerifier()

        # 执行器：注入后吃掉输入 tick（区分 Agent 输入与用户介入）
        inner = SendInputExecutor(hwnd)
        executor = _TickTrackerExecutor(inner, self._mark_agent_input)

        coord = VisionCoordinator(
            hwnd=hwnd,
            config=self._vision_cfg,
            arbiter=arbiter,
            executor=executor,
            verifier=verifier,
            frame_source=self._use_framesource,
        )
        try:
            action_result = coord.run(spec)
        finally:
            coord.close()

        return Message(
            kind="result", op=op,
            status=self._verdict_status(action_result),
            payload={
                "verdict": action_result.verdict,
                "reason": action_result.reason,
                "attempts": action_result.attempts,
                "target_window": action_result.target_window,
                "verification_detail":
                    action_result.verification.detail if action_result.verification else "",
            })

    @staticmethod
    def _verdict_status(ar) -> str:
        from vision_coordinator import Verdict
        return {
            Verdict.EXECUTED: ResultStatus.APPROVED,
            Verdict.NEEDS_CONFIRMATION: ResultStatus.REQUIRES_CONFIRMATION,
            Verdict.REJECTED: ResultStatus.REJECTED,
            Verdict.EXHAUSTED: ResultStatus.REJECTED,
            Verdict.ERROR: ResultStatus.ERROR,
            Verdict.CANCELLED: ResultStatus.VETOED,
        }.get(ar.verdict, ResultStatus.REJECTED)

    def _check_window(self, hwnd) -> None:
        from vision_actions import is_window
        if not is_window(hwnd):
            raise ValueError(f"窗口无效或已关闭：hwnd={hwnd}")

    # ═══════════════════════════════════════════════════════════
    #  事件广播 / 横幅同步 / 收尾
    # ═══════════════════════════════════════════════════════════

    def _push_event(self, event: str, payload: Dict[str, Any]) -> None:
        from vision_ipc import build_event
        xml_str = build_event(event, payload)
        dead = []
        with self._sessions_lock:
            sessions = list(self._sessions.items())
        for sid, fsock in sessions:
            try:
                fsock.send_raw(xml_str)
            except (ConnectionClosed, OSError):
                dead.append(sid)
        for sid in dead:
            with self._sessions_lock:
                self._sessions.pop(sid, None)

    def _sync_banner(self) -> None:
        """横幅状态机：熔断 OPEN → 红；Agent 操作中 → 绿；
        空闲 → 默认自动隐藏，banner.auto_hide=false 时显示灰色待机。

        状态变化时驱动 BannerController 渲染（颜色/可见性/接管标记），
        并把 banner_changed 事件推给主架构。
        """
        arbiter = self._arbiter
        if arbiter is None:
            return
        auto_hide = bool(self._banner_cfg.get("auto_hide", True))
        if arbiter.circuit.value == "OPEN":
            state = {"color": "red", "visible": True,
                     "paused": arbiter.override_active}
        elif arbiter.agent_operating:
            state = {"color": "green", "visible": True,
                     "paused": arbiter.override_active}
        elif not auto_hide:
            state = {"color": "idle", "visible": True, "paused": False}
        else:
            state = {"color": "hidden", "visible": False, "paused": False}
        changed = state != self._banner_state
        self._banner_state = state
        if changed:
            if self.banner is not None:
                try:
                    self.banner.set_state(
                        state["color"], state["visible"], state["paused"])
                except Exception as e:
                    self.log(f"[daemon] 横幅控制器异常：{e}")
            self._push_event(EventType.BANNER_CHANGED, dict(state))

    def _finalize(self) -> None:
        """优雅收尾：事件 → 状态落盘 → 关闭会话/热键/服务 → 删 lock。"""
        self.log(f"[daemon] 退出：{self._shutdown_reason or '未知原因'}")
        try:
            self._push_event(EventType.DAEMON_SHUTDOWN,
                             {"reason": self._shutdown_reason or ""})
        except Exception:
            pass
        self._save_state()
        # 关闭全部会话
        with self._sessions_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for fsock in sessions:
            fsock.close()
        if self._server is not None:
            self._server.stop()
        # 热键线程退出（WM_QUIT）
        if self._hotkey_thread_id is not None:
            try:
                ctypes.windll.user32.PostThreadMessageW(
                    self._hotkey_thread_id, 0x0012, 0, 0)
            except Exception:
                pass
        if self.banner is not None:
            try:
                self.banner.close()
            except Exception:
                pass
        self._remove_lock()
        self.log("[daemon] 已退出")


# ═══════════════════════════════════════════════════════════════
#  命令行入口
# ═══════════════════════════════════════════════════════════════

def _default_app_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "NorpAgent")


def _read_lock(app_dir: str) -> Optional[Dict[str, Any]]:
    try:
        with open(os.path.join(app_dir, LOCK_FNAME), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _client_for_stop_status(app_dir: str):
    from vision_ipc_transport import VipcClient
    lock = _read_lock(app_dir)
    if lock is None:
        raise RuntimeError(f"daemon 未运行（找不到 {app_dir}/{LOCK_FNAME}）")
    client = VipcClient("127.0.0.1", int(lock["port"]), str(lock["secret"]),
                        role="daemon-cli")
    client.connect()
    return client


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="视觉/操作外挂进程")
    parser.add_argument("--start", action="store_true", help="启动 daemon（驻留）")
    parser.add_argument("--stop", action="store_true", help="优雅停止 daemon")
    parser.add_argument("--status", action="store_true", help="查询 daemon 状态")
    parser.add_argument("--port", type=int, default=None, help="端口（默认 38476）")
    parser.add_argument("--app-dir", default=None, help="工作目录（lock/config/审计）")
    parser.add_argument("--allow-admin", action="store_true",
                        help="管理员权限下也允许启动（风险自担）")
    parser.add_argument("--no-banner", action="store_true",
                        help="禁用屏幕左上角横幅 UI（测试/无头环境）")
    parser.add_argument("--fg", action="store_true", help="前台运行（日志到 stdout）")
    args = parser.parse_args(argv)

    app_dir = args.app_dir or _default_app_dir()

    if args.stop:
        client = _client_for_stop_status(app_dir)
        try:
            res = client.request("shutdown", "L0", {}, confirm="user",
                                 ttl_ms=5000, timeout=10.0)
            print(f"shutdown → status={res.status} {res.payload}")
            return 0 if res.status == "ok" else 1
        finally:
            client.close()

    if args.status:
        client = _client_for_stop_status(app_dir)
        try:
            res = client.request("state", "L0", {}, ttl_ms=5000, timeout=10.0)
            print(json.dumps(res.payload, ensure_ascii=False, indent=2))
            return 0
        finally:
            client.close()

    if args.start:
        log = (lambda s: print(s, flush=True)) if args.fg else (lambda s: None)
        daemon = VisionDaemon(
            app_dir=app_dir, port=args.port,
            allow_admin=args.allow_admin,
            enable_banner=not args.no_banner,
            log=log)
        # 横幅在主线程创建/渲染（tkinter 生命周期全程主线程，无 Tcl
        # 跨线程告警）；daemon 的其它线程照常在 start() 内启动。
        banner = daemon.prepare_banner_mainthread()
        daemon.start()
        try:
            if banner is not None:
                banner.serve(
                    stop_check=lambda: daemon._shutdown_reason is not None)
                daemon._finalize()
            else:
                daemon.wait()
        except KeyboardInterrupt:
            daemon._shutdown_reason = "Ctrl+C"
            daemon._finalize()
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

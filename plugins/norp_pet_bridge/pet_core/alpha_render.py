# -*- coding: utf-8 -*-
"""
真 alpha 分层窗口渲染器（Windows）
====================================
解决 Tk `-transparentcolor` 只能二值键控（要么全透明、要么全不透明）的
根本缺陷：优香立绘边缘的大量半透明像素（PNG 抗锯齿过渡）在旧方案里被
强行二值化，导致边缘锯齿、发暗、像素感，且反复调优都无法根治。

本模块用 Windows 分层窗口（WS_EX_LAYERED + UpdateLayeredWindow）实现
逐像素 alpha 合成：半透明边缘像素与桌面真实混合，边缘像素级平滑，
彻底告别锯齿/黑线/紫边。

原理：
  1. CreateDIBSection 创建 32bpp BGRA 位图（自顶向下，与 PIL 行序一致）
  2. 每帧把 RGBA 像素 memmove 进位图缓冲（零分配、~0.5ms）
  3. UpdateLayeredWindow + BLENDFUNCTION(AC_SRC_ALPHA) 做真 alpha 合成
  4. 鼠标交互：分层窗口不带 WS_EX_TRANSPARENT（不穿透），形象区域
     接收鼠标消息后经 WNDPROC 子类化转发给 Tk 主窗口 —— Tk 窗口整个
     背景都是键控透明色（点击穿透），必须由分层窗口当「鼠标接收器」。

仅在 Windows 上可用；其他平台 / 初始化失败时返回 None，由调用方回退。
"""

import ctypes
import os
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32


# 钩子诊断日志开关：排障时临时改为 True，平时必须保持 False！
# ⚠️ 钩子回调运行在 Windows 系统输入管线上（每次鼠标按键都触发），
# 里面做任何磁盘 IO（open/write/close）都会被杀软实时扫描，一次
# 卡顿 10~100ms+，直接造成全系统「鼠标突然卡住、几秒后恢复」。
_HOOK_DEBUG_LOG = False


def _hook_dbg(msg):
    """钩子诊断日志（排障用）。默认关闭：系统输入管线上严禁磁盘 IO。"""
    if not _HOOK_DEBUG_LOG:
        return
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "hook_debug.log"), "a",
                  encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000   # 点击不激活、不抢焦点（用户打字时点宠物不失焦）
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DIB_RGB_COLORS = 0
BI_BITFIELDS = 3   # 32bpp 用 BITFIELDS + 显式掩码，否则 GDI 会清空 DIB 的 alpha 通道

# ---- 鼠标消息（转发给 Tk 主窗口）----
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_RBUTTONDBLCLK = 0x0206
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MBUTTONDBLCLK = 0x0209
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
_MOUSE_MSGS = frozenset((WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP,
                         WM_LBUTTONDBLCLK, WM_RBUTTONDOWN, WM_RBUTTONUP,
                         WM_RBUTTONDBLCLK, WM_MBUTTONDOWN, WM_MBUTTONUP,
                         WM_MBUTTONDBLCLK, WM_MOUSEWHEEL, WM_MOUSEHWHEEL))

GWLP_WNDPROC = -4

# WNDPROC 回调类型（LRESULT 在 64 位系统上是 64 位，必须用 c_ssize_t）
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPV4HEADER(ctypes.Structure):
    """BITMAPV4HEADER：32bpp DIB 必须用它 + BI_BITFIELDS + 显式掩码，
    否则 GDI 会把 DIB 的 alpha 通道清零，UpdateLayeredWindow 的
    真 alpha 合成失效（窗口显示为全透明）。"""
    _fields_ = [
        ("bV4Size", wintypes.DWORD),
        ("bV4Width", wintypes.LONG),
        ("bV4Height", wintypes.LONG),
        ("bV4Planes", wintypes.WORD),
        ("bV4BitCount", wintypes.WORD),
        ("bV4V4Compression", wintypes.DWORD),
        ("bV4SizeImage", wintypes.DWORD),
        ("bV4XPelsPerMeter", wintypes.LONG),
        ("bV4YPelsPerMeter", wintypes.LONG),
        ("bV4ClrUsed", wintypes.DWORD),
        ("bV4ClrImportant", wintypes.DWORD),
        ("bV4RedMask", wintypes.DWORD),
        ("bV4GreenMask", wintypes.DWORD),
        ("bV4BlueMask", wintypes.DWORD),
        ("bV4AlphaMask", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 1)]


class LayeredImage:
    """真 alpha 分层窗口：把 RGBA 图像逐像素合成到桌面。

    鼠标交互：窗口不带 WS_EX_TRANSPARENT，形象区域会接收鼠标消息；
    调用 forward_events_to(tk_hwnd) 后，所有鼠标消息经 WNDPROC 子类化
    原样转发给 Tk 主窗口（两窗口位置/尺寸完全重合，客户区坐标一致），
    拖动/单击/双击/右键菜单全部由 Tk 正常处理。
    """

    def __init__(self, x, y, w, h):
        self.w, self.h = w, h
        self._x, self._y = x, y
        self._shown = False
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            "STATIC", None, WS_POPUP, x, y, w, h, 0, 0, 0, None,
        )
        if not self.hwnd:
            raise OSError("CreateWindowExW failed: %d" % kernel32.GetLastError())
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        # 鼠标转发状态
        self._tk_hwnd = None
        self._bridge = None       # 事件桥回调（由 attach_bridge 设置）
        self._wndproc_cb = None   # 必须持有引用，否则回调被 GC 后窗口过程崩溃
        self._old_wndproc = None
        # 32bpp BGRA DIB（负高度 = 自顶向下，与 PIL 行序一致）
        bmi = BITMAPV4HEADER()
        bmi.bV4Size = ctypes.sizeof(BITMAPV4HEADER)
        bmi.bV4Width = w
        bmi.bV4Height = -h
        bmi.bV4Planes = 1
        bmi.bV4BitCount = 32
        bmi.bV4V4Compression = BI_BITFIELDS
        bmi.bV4SizeImage = w * h * 4
        bmi.bV4RedMask = 0x00FF0000
        bmi.bV4GreenMask = 0x0000FF00
        bmi.bV4BlueMask = 0x000000FF
        bmi.bV4AlphaMask = 0xFF000000
        self._pbits = ctypes.POINTER(ctypes.c_ubyte)()
        hdc = user32.GetDC(0)
        self._hbm = gdi32.CreateDIBSection(
            hdc, ctypes.byref(bmi), DIB_RGB_COLORS,
            ctypes.byref(self._pbits), None, 0)
        user32.ReleaseDC(0, hdc)
        if not self._hbm:
            user32.DestroyWindow(self.hwnd)
            raise OSError("CreateDIBSection failed: %d" % kernel32.GetLastError())
        self._nbytes = w * h * 4
        self._buf = ctypes.cast(
            self._pbits, ctypes.POINTER(ctypes.c_ubyte * self._nbytes))

    # ------------------------------------------------------------------
    # 鼠标事件转发（关键：Tk 窗口全键控透明 → 点击穿透 → 由本窗口接收）
    # ------------------------------------------------------------------
    def attach_bridge(self, bridge):
        """设置事件桥回调 bridge(msg, wparam, lparam)，并子类化窗口过程。

        回调在窗口过程（Tk 主线程消息分发）里被调用，只允许做轻量操作
        （如入队），严禁直接调用 Tk API，否则会造成 Tk 重入卡死。
        """
        self._bridge = bridge
        if self._wndproc_cb is not None:
            return
        self._wndproc_cb = WNDPROC(self._mouse_forward)
        try:
            setproc = user32.SetWindowLongPtrW
        except AttributeError:
            setproc = user32.SetWindowLongW
        setproc.restype = wintypes.LPARAM
        setproc.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LPARAM]
        self._old_wndproc = setproc(
            self.hwnd, GWLP_WNDPROC,
            ctypes.cast(self._wndproc_cb, ctypes.c_void_p).value)

    def _mouse_forward(self, hwnd, msg, wparam, lparam):
        """子类化窗口过程：鼠标消息交给事件桥（Tk event_generate），其余走默认处理。

        关键：STATIC 类窗口对 WM_NCHITTEST 默认返回 HTTRANSPARENT
        （静态控件天生点击穿透），必须强制返回 HTCLIENT，鼠标消息
        才会送达本窗口，否则永远点不到宠物。
        """
        try:
            if msg == 0x0084:   # WM_NCHITTEST
                return 1        # HTCLIENT：接收鼠标消息
            if self._bridge is not None and msg in _MOUSE_MSGS:
                self._bridge(msg, wparam, lparam)
                return 0
        except Exception:
            pass
        try:
            if self._old_wndproc:
                user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                                   wintypes.UINT, wintypes.WPARAM,
                                                   wintypes.LPARAM]
                user32.CallWindowProcW.restype = ctypes.c_ssize_t
                return user32.CallWindowProcW(self._old_wndproc, hwnd, msg,
                                              wparam, lparam)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception:
            return 0

    def blit(self, img_rgba, x=None, y=None):
        """把与窗口同尺寸的 RGBA PIL 图像以真 alpha 方式显示。

        x/y 可选：分层窗口的屏幕坐标（UpdateLayeredWindow 的 pptDst）。
        注意：分层窗口用 UpdateLayeredWindow 更新后 SetWindowPos 无法
        移动它，位置必须在这里指定；坐标变化时自动跟随。
        """
        data = img_rgba.tobytes("raw", "BGRA")
        ctypes.memmove(self._pbits, data, self._nbytes)
        hdc = user32.GetDC(self.hwnd)
        hdc_mem = gdi32.CreateCompatibleDC(hdc)
        old = gdi32.SelectObject(hdc_mem, self._hbm)
        size = SIZE(self.w, self.h)
        pt_src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        pt_dst = None
        if x is not None and y is not None:
            # 无条件按传入坐标定位：move() 也会改写 _x/_y（仅记录），
            # 若用「坐标变化才更新」判断会被 move 干扰，导致分层窗口
            # 位置永不刷新（表现为拖拽时形象原地不动）。
            pt_dst = POINT(int(x), int(y))
            self._x, self._y = int(x), int(y)
        ok = user32.UpdateLayeredWindow(self.hwnd, hdc,
                                        ctypes.byref(pt_dst) if pt_dst else None,
                                        ctypes.byref(size),
                                        hdc_mem, ctypes.byref(pt_src),
                                        0, ctypes.byref(blend), ULW_ALPHA)
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(self.hwnd, hdc)
        if not ok:
            raise OSError("UpdateLayeredWindow failed: %d" % kernel32.GetLastError())
        if not self._shown:
            self._shown = True
            user32.ShowWindow(self.hwnd, 8)   # SW_SHOWNA：显示但不抢焦点

    def move(self, x, y):
        """兼容接口：分层窗口位置需经 blit(x,y) 更新，这里仅记录坐标。"""
        self._x, self._y = int(x), int(y)

    def close(self):
        # 恢复原窗口过程，再销毁窗口
        if self.hwnd and self._old_wndproc:
            try:
                setproc = getattr(user32, "SetWindowLongPtrW",
                                  user32.SetWindowLongW)
                setproc.restype = wintypes.LPARAM
                setproc.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LPARAM]
                setproc(self.hwnd, GWLP_WNDPROC, self._old_wndproc)
            except Exception:
                pass
            self._old_wndproc = None
        self._wndproc_cb = None
        if self._hbm:
            gdi32.DeleteObject(self._hbm)
            self._hbm = None
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None


def create_layered(x, y, w, h):
    """安全创建分层窗口；失败返回 None（调用方回退到 Tk 绘制）。"""
    try:
        return LayeredImage(x, y, w, h)
    except Exception:
        return None


# ======================================================================
# 全局低级鼠标钩子（WH_MOUSE_LL）
# ======================================================================
# 背景：宠物所有交互依赖「窗口鼠标消息投递」（WNDPROC 子类化转发）。
# 游戏运行时（全屏 / SetCapture 捕获 / 独占输入 / 管理员权限 UIPI）
# 会劫持或独占鼠标消息，宠物窗口收不到 WM_LBUTTONDOWN / WM_RBUTTONDOWN
# → 表现为「拖不动、右键无反应」。
# 低级鼠标钩子挂在系统输入流上（消息分发之前），不受窗口消息投递
# 影响：只要光标落在宠物窗口上（且 WindowFromPoint 确认宠物确实在
# 顶层可见），就由钩子直接消费事件并喂给事件桥；否则原样放行，
# 游戏操作完全不受影响。
WH_MOUSE_LL = 14            # 全局低级鼠标钩子（回调在安装线程内执行）
HC_ACTION = 0

# 钩子只关心这些消息（拖动/单击/双击/右键；MOUSEMOVE 由 Tk 轮询拖动处理）
_HOOK_MSGS = frozenset((WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK,
                        WM_RBUTTONDOWN, WM_RBUTTONUP, WM_RBUTTONDBLCLK))


class MSLLHOOKSTRUCT(ctypes.Structure):
    """低级鼠标钩子事件数据（lParam 指向的原始结构）。"""
    _fields_ = [
        ("pt", wintypes.POINT),        # 事件发生时的屏幕坐标（物理像素）
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),   # ULONG_PTR
    ]


LowLevelMouseProc = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


# 一次性设置 Win32 签名（防止 64 位句柄被默认 int 截断）
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND,
                                 ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, LowLevelMouseProc,
                                     wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t


class MouseHook:
    """全局低级鼠标钩子：光标在宠物窗口内时直接消费鼠标事件。

    与 WNDPROC 子类化互为双保险（互不冲突）：
      · WNDPROC 收到的是「系统投递给本窗口」的消息 —— 依赖消息投递；
      · 钩子收到的是「系统输入流」上的全部事件 —— 不依赖投递。
    钩子拦截（返回 1）后消息不再继续投递 → WNDPROC 收不到 → 不会重复；
    钩子放行（返回 0）时消息正常流转 → WNDPROC 兜底 → 不会漏事件。
    """

    def __init__(self, hwnd, bridge):
        self._hwnd = hwnd
        self._bridge = bridge          # 事件桥回调（只入队，绝不调 Tk）
        self._hook = None
        self._proc = LowLevelMouseProc(self._callback)
        self.lbutton_down = False      # 钩子侧左键状态（钩子吞掉消息后
                                       # GetAsyncKeyState 读不到输入流，
                                       # 拖动释放判定改由本状态驱动）
        self._drag_armed = False       # 最近一次 DOWN 被宠物命中拦截 → True；
                                       # 收到 UP 后复位。用于把「宠物外松手」
                                       # 的 UP 转发给宠物结束拖动（不拦截）。
        # WH_MOUSE_LL 全局钩子：dwThreadId=0、hMod 可空；
        # 回调在安装线程（Tk 主线程消息循环）上下文执行，只做轻量操作。
        self._hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._proc, None, 0)
        if not self._hook:
            self._proc = None

    @property
    def ok(self):
        return bool(self._hook)

    def _callback(self, nCode, wParam, lParam):
        try:
            if nCode == HC_ACTION and wParam in _HOOK_MSGS:
                data = ctypes.cast(lParam,
                                   ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                pt = data.pt
                # 钩子侧持续跟踪左键物理状态（不依赖 GetAsyncKeyState：
                # 消息被本钩子吞掉后，GetAsyncKeyState 读输入流读不到）
                if wParam == WM_LBUTTONDOWN:
                    self.lbutton_down = True
                elif wParam == WM_LBUTTONUP:
                    self.lbutton_down = False
                # 光标下最顶层窗口必须是宠物本尊才拦截：宠物被其他窗口
                # 盖住（如独占全屏游戏）时 WindowFromPoint 返回盖住它的
                # 窗口 → 放行，绝不抢游戏/其他程序的点击。
                top = user32.WindowFromPoint(pt)
                hit = (top == self._hwnd)
                _hook_dbg("evt=0x%04X pt=(%d,%d) top=0x%X hwnd=0x%X hit=%s armed=%s" % (
                    wParam, pt.x, pt.y, top, self._hwnd, hit, self._drag_armed))
                if hit:
                    rect = wintypes.RECT()
                    if user32.GetWindowRect(self._hwnd, ctypes.byref(rect)):
                        if (rect.left <= pt.x < rect.right
                                and rect.top <= pt.y < rect.bottom):
                            x = pt.x - rect.left
                            y = pt.y - rect.top
                            # 编码成窗口消息 lParam 格式（客户区坐标，
                            # 低 16 位 x / 高 16 位 y），与 pet.py
                            # _consume_mouse 的解析完全兼容。
                            lp = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
                            if wParam == WM_LBUTTONDOWN:
                                self._drag_armed = True
                            elif wParam == WM_LBUTTONUP:
                                self._drag_armed = False
                            _hook_dbg("  -> CONSUME x=%d y=%d" % (x, y))
                            self._bridge(wParam, 0, lp)
                            return 1    # 消费：消息不再投递（游戏收不到）
                        _hook_dbg("  -> rect miss rect=(%d,%d,%d,%d)" % (
                            rect.left, rect.top, rect.right, rect.bottom))
                # 宠物外松手：正在拖宠物（DOWN 曾被命中）时把 UP 转发给
                # 宠物结束拖动（不拦截，其他窗口照常收到点击）
                if wParam == WM_LBUTTONUP and self._drag_armed:
                    rect = wintypes.RECT()
                    if user32.GetWindowRect(self._hwnd, ctypes.byref(rect)):
                        x = pt.x - rect.left
                        y = pt.y - rect.top
                        lp = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
                        self._drag_armed = False
                        _hook_dbg("  -> FORWARD UP (drag end) x=%d y=%d" % (x, y))
                        self._bridge(wParam, 0, lp)
        except Exception:
            pass
        try:
            return user32.CallNextHookEx(None, nCode, wParam, lParam)
        except Exception:
            return 0

    def close(self):
        if self._hook:
            try:
                user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
        self._proc = None


def install_mouse_hook(hwnd, bridge):
    """安装全局低级鼠标钩子；失败返回 None（静默降级到 WNDPROC 方案）。"""
    try:
        h = MouseHook(hwnd, bridge)
        if h.ok:
            return h
    except Exception:
        pass
    return None

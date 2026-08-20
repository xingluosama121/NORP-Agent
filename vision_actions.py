# Vibe Coding Agent - 视觉/操作外挂：操作执行层（坐标闭环 + SendInput 键鼠注入）
# Copyright (c) 2026 xingluosama
#
# 职责：把「视觉模型在截图上给的坐标」换算成「屏幕坐标」，并用 SendInput 注入键鼠事件。
# 这是「操作电脑」的物理执行层——**只提供原子操作原语，不做任何裁决**。
#
# 安全铁律（见 docs/vision_agent_design.md）：
#   - 本模块的每个函数都是「被裁决器保护的原语」，调用前必须先过 vision_safety.SafetyArbiter，
#     绝不允许绕过裁决器直接调用 click / type_text。
#   - 本模块不做提权、不装钩子、不碰驱动，只用标准 SendInput / SetCursorPos。
#   - 坐标换算全程走「物理像素」一致原则（进程设为 Per-Monitor-Aware-V2），
#     避免 DPI 虚拟化导致点偏。
#
# 坐标闭环（物理像素一致，无需额外 DPI 缩放）：
#   视觉模型在截图上的坐标 (img_x, img_y)   ← 截图 = capture_worker 的物理像素帧
#       │ ① 反缩放（若图片被缩放才需要，1:1 时恒等）
#       ▼
#   客户区物理像素坐标
#       │ ② ClientToScreen（进程 Per-Monitor-Aware → 返回物理屏幕坐标）
#       ▼
#   屏幕坐标  ← SetCursorPos / SendInput 要的就是这个
#
# 为什么不再做 DPI 换算：进程设为 Per-Monitor-Aware-V2 后，ClientToScreen 与
# SetCursorPos 全程都在「物理像素」坐标系里，capture 出的帧也是物理像素，
# 三者坐标系一致，故 ①反缩放 + ②ClientToScreen 即可，无需 96/dpi 缩放。

import ctypes
import time
from ctypes import wintypes
from typing import Tuple

# ────────────────────────────────────────────────────────────────
# 进程 DPI 感知：必须最先设置，否则多显示器混插时 DPI 虚拟化导致坐标错位。
# ────────────────────────────────────────────────────────────────
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


def _ensure_dpi_aware() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(
            _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        )
    except Exception:
        # 已设置过 / 系统不支持时静默回退，不阻断操作层
        pass


_ensure_dpi_aware()

user32 = ctypes.windll.user32

# ────────────────────────────────────────────────────────────────
# Win32 常量
# ────────────────────────────────────────────────────────────────
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

# 常用虚拟键码
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_DELETE = 0x2E
VK_END = 0x23
VK_HOME = 0x24

# ────────────────────────────────────────────────────────────────
# ctypes 结构体（SendInput 用）
# ────────────────────────────────────────────────────────────────
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUTUNION),
    ]


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


# ────────────────────────────────────────────────────────────────
# Win32 函数签名（显式 argtypes/restype，保证 64 位下正确）
# ────────────────────────────────────────────────────────────────
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = ctypes.c_uint

user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.ClientToScreen.restype = wintypes.BOOL

user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL

user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = ctypes.c_uint

user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.restype = wintypes.BOOL

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL

user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL

user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL


class VisionActionError(Exception):
    """操作执行失败。"""


# ────────────────────────────────────────────────────────────────
# 坐标闭环
# ────────────────────────────────────────────────────────────────

def get_window_dpi(hwnd: int) -> int:
    """窗口所在显示器的 DPI（每英寸物理像素数）。"""
    return int(user32.GetDpiForWindow(wintypes.HWND(hwnd)))


def client_to_screen(hwnd: int, client_x: int, client_y: int) -> Tuple[int, int]:
    """客户区坐标 → 屏幕坐标。

    进程为 Per-Monitor-Aware-V2 时，输入输出都是「物理像素」。
    客户区坐标原点在窗口内容区左上角（不含标题栏/边框），与 capture 帧一致。
    """
    pt = POINT(int(client_x), int(client_y))
    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt)):
        raise VisionActionError(f"ClientToScreen 失败（hwnd={hwnd}）")
    return int(pt.x), int(pt.y)


def unscale_coordinates(
    img_x: float,
    img_y: float,
    img_width: int,
    img_height: int,
    phys_width: int,
    phys_height: int,
) -> Tuple[float, float]:
    """坐标链第①步「反缩放」的纯函数：图片坐标 → 客户区物理像素坐标。

    无 Win32 调用、零副作用，标定台可离线标定换算公式。
    """
    if img_width <= 0 or img_height <= 0 or phys_width <= 0 or phys_height <= 0:
        raise VisionActionError("坐标换算参数非法（尺寸必须 > 0）")
    phys_x = float(img_x) * (float(phys_width) / float(img_width))
    phys_y = float(img_y) * (float(phys_height) / float(img_height))
    return phys_x, phys_y


def image_to_screen(
    hwnd: int,
    img_x: float,
    img_y: float,
    img_width: int,
    img_height: int,
    phys_width: int,
    phys_height: int,
) -> Tuple[int, int]:
    """视觉模型在「发送给模型的图片」上的坐标 → 屏幕坐标。

    参数：
      img_x/img_y    视觉模型给的坐标（图片像素坐标系，原点左上）
      img_width/img_height   发送给模型的图片尺寸
      phys_width/phys_height 窗口物理像素尺寸（= capture 帧 ContentSize）
    若图片 1:1（未缩放），img_* == phys_*，反缩放恒等。
    """
    # ① 反缩放：图片坐标 → 客户区物理像素坐标
    phys_x, phys_y = unscale_coordinates(
        img_x, img_y, img_width, img_height, phys_width, phys_height)

    # ② ClientToScreen：客户区物理像素 → 屏幕物理像素
    return client_to_screen(hwnd, int(round(phys_x)), int(round(phys_y)))


def get_client_rect(hwnd: int) -> Tuple[int, int, int, int]:
    """窗口客户区矩形 (left, top, right, bottom)，物理像素。"""
    r = RECT()
    if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(r)):
        raise VisionActionError(f"GetClientRect 失败（hwnd={hwnd}）")
    return int(r.left), int(r.top), int(r.right), int(r.bottom)


def get_window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    """窗口整体矩形（含标题栏/边框），屏幕物理像素。"""
    r = RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
        raise VisionActionError(f"GetWindowRect 失败（hwnd={hwnd}）")
    return int(r.left), int(r.top), int(r.right), int(r.bottom)


def get_cursor_pos() -> Tuple[int, int]:
    """当前鼠标屏幕坐标（物理像素）。用于动作验证（鼠标是否到位）。"""
    pt = POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        raise VisionActionError("GetCursorPos 失败")
    return int(pt.x), int(pt.y)


def is_window(hwnd: int) -> bool:
    """窗口句柄是否仍有效。"""
    return bool(user32.IsWindow(wintypes.HWND(hwnd)))


def set_foreground(hwnd: int) -> None:
    """把窗口带到前台（用于后续键鼠注入的目标窗口）。"""
    if not user32.SetForegroundWindow(wintypes.HWND(hwnd)):
        raise VisionActionError(f"SetForegroundWindow 失败（hwnd={hwnd}）")


# ────────────────────────────────────────────────────────────────
# SendInput 底层
# ────────────────────────────────────────────────────────────────

def _send_input(inp: INPUT) -> None:
    """发送单个 INPUT 事件，失败抛 VisionActionError。"""
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if sent != 1:
        err = ctypes.get_last_error()
        raise VisionActionError(f"SendInput 失败（sent={sent}, err={err}）")


def _mouse_event(flags: int, mouse_data: int = 0) -> None:
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi.dwFlags = flags
    inp.mi.mouseData = mouse_data
    inp.mi.dx = 0
    inp.mi.dy = 0
    _send_input(inp)


def _key_event(vk: int, keyup: bool = False) -> None:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = vk
    inp.ki.wScan = 0
    inp.ki.dwFlags = KEYEVENTF_KEYUP if keyup else 0
    _send_input(inp)


# ────────────────────────────────────────────────────────────────
# 高层原语（原子操作，调用前必须已过裁决器）
# ────────────────────────────────────────────────────────────────

def move_mouse(screen_x: int, screen_y: int) -> None:
    """移动鼠标到屏幕坐标（物理像素）。"""
    if not user32.SetCursorPos(int(screen_x), int(screen_y)):
        raise VisionActionError(f"SetCursorPos 失败（{screen_x},{screen_y}）")


def click(
    screen_x: int,
    screen_y: int,
    *,
    button: str = "left",
    move_delay: float = 0.05,
    press_delay: float = 0.03,
) -> None:
    """在屏幕坐标处点击。button: left / right / middle。

    延迟：移动后、按下后、抬起后各插入短延迟，让目标应用有时间处理消息，
    否则快速连点可能丢事件。
    """
    move_mouse(screen_x, screen_y)
    time.sleep(move_delay)

    if button == "left":
        down, up = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
    elif button == "right":
        down, up = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
    elif button == "middle":
        down, up = MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP
    else:
        raise VisionActionError(f"未知按钮：{button}")

    _mouse_event(down)
    time.sleep(press_delay)
    _mouse_event(up)
    time.sleep(press_delay)


def double_click(screen_x: int, screen_y: int, *, button: str = "left") -> None:
    """双击（两次 click，间隔更短）。"""
    click(screen_x, screen_y, button=button, move_delay=0.05, press_delay=0.02)
    time.sleep(0.05)
    click(screen_x, screen_y, button=button, move_delay=0.0, press_delay=0.02)


def scroll(screen_x: int, screen_y: int, clicks: int) -> None:
    """在坐标处滚动鼠标滚轮。clicks 正数向上、负数向下（1 ≈ 120 单位）。"""
    move_mouse(screen_x, screen_y)
    time.sleep(0.03)
    _mouse_event(MOUSEEVENTF_WHEEL, mouse_data=int(clicks) * 120)


def type_text(text: str, *, delay: float = 0.01) -> None:
    """逐字符输入文本（Unicode，支持中文/符号）。

    用 KEYEVENTF_UNICODE 直接按字符注入，不依赖键盘布局。
    """
    for ch in text:
        code = ord(ch)
        down = INPUT()
        down.type = INPUT_KEYBOARD
        down.ki.wScan = code
        down.ki.dwFlags = KEYEVENTF_UNICODE
        _send_input(down)

        up = INPUT()
        up.type = INPUT_KEYBOARD
        up.ki.wScan = code
        up.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
        _send_input(up)
        time.sleep(delay)


def key_press(vk: int, *, hold: float = 0.03) -> None:
    """按下并抬起单个虚拟键（如 VK_RETURN）。"""
    _key_event(vk, keyup=False)
    time.sleep(hold)
    _key_event(vk, keyup=True)


def key_combo(modifiers: Tuple[int, ...], key: int) -> None:
    """组合键，如 key_combo((VK_CONTROL,), ord('C'))。

    先按下所有修饰键，按下主键，抬起主键，再抬起修饰键（逆序）。
    """
    for mod in modifiers:
        _key_event(mod, keyup=False)
        time.sleep(0.02)
    _key_event(key, keyup=False)
    time.sleep(0.02)
    _key_event(key, keyup=True)
    for mod in reversed(modifiers):
        time.sleep(0.02)
        _key_event(mod, keyup=True)

# Vibe Coding Agent - 视觉/操作外挂：窗口捕获封装（Python 侧）
# Copyright (c) 2026 xingluosama
#
# 职责：调用 C++ capture_worker.exe 捕获用户指定窗口的一帧画面，转成视觉模型
#       可吃的 PNG 字节，并可交给 vision.process_visual 做视觉理解。
#
# 这是「主动看图」的源头：Agent 不再只能读磁盘上的图片文件，还能「看」屏幕上的窗口。
#
# 数据流：
#   HWND → capture_worker.exe（Graphics Capture 单窗口，被遮挡也可见）
#        → 32bpp BGRA BMP → Pillow 转 PNG → vision.process_visual → 文字描述
#
# 注意：
#   - capture_worker.exe 需先用 capture_worker\build.bat 编译（MSVC + Windows SDK）。
#   - BMP 之所以要转 PNG：OpenAI / Anthropic 视觉接口不支持 BMP，只认 PNG/JPEG/WebP。
#   - 本模块是同步接口；上层若需异步，请在 async_executor 的线程池中调用，
#     禁止直接在主事件循环里阻塞。

import io
import os
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

from PIL import Image

# capture_worker.exe 相对于本模块的位置（工作区 capture_worker\ 目录）
_WORKER_EXE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "capture_worker",
    "capture_worker.exe",
)


class VisionCaptureError(Exception):
    """窗口捕获失败（含 capture_worker 的 stderr 信息）。"""


@dataclass
class CaptureResult:
    """一次窗口捕获的结果。"""
    png: bytes        # PNG 编码的画面（视觉模型可直接吃）
    width: int        # 物理像素宽（坐标闭环做反缩放用）
    height: int       # 物理像素高
    hwnd: int         # 目标窗口句柄


def _worker_available() -> None:
    """检查 capture_worker.exe 是否存在，不存在则给出可操作的提示。"""
    if not os.path.exists(_WORKER_EXE):
        raise VisionCaptureError(
            f"capture_worker.exe 不存在：{_WORKER_EXE}\n"
            f"请先编译：运行 capture_worker\\build.bat（需安装 VS2022 桌面 C++ 负载）。"
        )


def capture_window_bmp(hwnd: int, timeout: float = 10.0) -> bytes:
    """调用 capture_worker.exe 捕获单帧，返回原始 BMP 字节（调试/标定台用）。

    参数 hwnd：目标窗口句柄（int）。
    抛 VisionCaptureError：进程不存在 / 捕获失败 / 超时。
    """
    _worker_available()
    fd, tmp_path = tempfile.mkstemp(suffix=".bmp")
    os.close(fd)
    try:
        proc = subprocess.run(
            [_WORKER_EXE, str(int(hwnd)), tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            raise VisionCaptureError(
                f"capture_worker 失败（exit={proc.returncode}）：{stderr or '无 stderr 输出'}"
            )
        with open(tmp_path, "rb") as f:
            return f.read()
    except subprocess.TimeoutExpired as e:
        raise VisionCaptureError(f"捕获超时（>{timeout}s）") from e
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def bmp_to_capture_result(bmp_bytes: bytes, hwnd: int) -> CaptureResult:
    """把 capture_worker 产出的 BMP 字节转成 CaptureResult（PNG + 物理尺寸）。

    单帧模式与驻留模式共用此转换（避免两处重复 BMP→PNG 逻辑）。
    """
    try:
        img = Image.open(io.BytesIO(bmp_bytes))
        width, height = img.size
        png_buf = io.BytesIO()
        img.save(png_buf, format="PNG")
        return CaptureResult(png=png_buf.getvalue(), width=width,
                             height=height, hwnd=int(hwnd))
    except Exception as e:
        raise VisionCaptureError(f"BMP 转 PNG 失败：{e}") from e


def capture_window(hwnd: int, timeout: float = 10.0) -> CaptureResult:
    """捕获窗口一帧，返回 PNG 字节 + 物理尺寸。

    这是「看见画面」的主入口。返回的 png 可直接喂给 vision.process_visual。
    """
    bmp_bytes = capture_window_bmp(hwnd, timeout=timeout)
    return bmp_to_capture_result(bmp_bytes, hwnd)


def describe_window(
    hwnd: int,
    config: dict,
    prompt: Optional[str] = None,
    timeout: float = 10.0,
) -> str:
    """捕获窗口并做视觉理解，返回文字描述。

    参数：
      hwnd    目标窗口句柄
      config  配置 dict（含 vision_provider / vision_model / vision_api_key 等）
      prompt  给视觉模型的指令；缺省用「描述界面布局」的默认指令
    """
    from vision import process_visual

    captured = capture_window(hwnd, timeout=timeout)
    if prompt is None:
        prompt = (
            "请描述这个窗口的界面布局：列出可见的按钮、输入框、菜单项、图标及其大致位置"
            "（用「左上/右上/中部/底部」等描述）。如果能看到文字，请一并读出。"
        )
    return process_visual(captured.png, "png", config)


def list_capturable_windows(max_results: int = 50) -> list:
    """枚举当前桌面上的可见顶层窗口（hwnd + 标题），供「选择目标窗口」用。

    纯 ctypes 调 Win32 EnumWindows（自研绑定，零外部库）。
    只返回有标题的可见窗口（无标题的工具窗口/托盘窗口无法给用户辨认，过滤）。
    返回格式：[{"hwnd": int, "title": str}]，按标题排序。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    def _cb(hwnd, _lparam):
        try:
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    found.append({"hwnd": int(hwnd), "title": buf.value})
        except Exception:
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    found.sort(key=lambda e: e["title"].lower())
    if len(found) > max_results:
        found = found[:max_results]
    return found


class FrameSource:
    """capture_worker 驻留模式（--serve）的封装：持续捕获 + 按需取帧。

    相比每次冷启动进程的单帧模式，驻留模式省去重复初始化 Graphics Capture 的开销，
    适合「动作-验证-收敛」里的高频重捕获（每次动作后快速取一帧比对）。

    用法：
        with FrameSource(hwnd) as src:
            bmp = src.shot_ready()          # 最新一帧 BMP bytes
            img = Image.open(io.BytesIO(bmp))  # 转成图像
    """

    _READY_MARKER = b"serve mode ready"

    def __init__(self, hwnd: int, ready_timeout: float = 10.0):
        self.hwnd = int(hwnd)
        self.ready_timeout = ready_timeout
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> None:
        """启动 capture_worker --serve，并等待其就绪。"""
        _worker_available()
        self._proc = subprocess.Popen(
            [_WORKER_EXE, "--serve", str(self.hwnd)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # 等就绪：capture_worker 就绪后向 stderr 打印 "serve mode ready"
        deadline = time.time() + self.ready_timeout
        while time.time() < deadline:
            if self._proc.poll() is not None:
                err = (self._proc.stderr.read() or b"").decode("utf-8", "replace").strip()
                raise VisionCaptureError(f"capture_worker 启动失败：{err or 'exit=' + str(self._proc.returncode)}")
            line = self._proc.stderr.readline()
            if self._READY_MARKER in line:
                return
        raise VisionCaptureError(f"capture_worker 就绪超时（>{self.ready_timeout}s）")

    def shot(self) -> bytes:
        """取最新一帧 BMP。尚无帧时返回 b''（可用 shot_ready 自动重试）。"""
        if self._proc is None or self._proc.poll() is not None:
            raise VisionCaptureError("capture_worker 驻留进程未运行")
        try:
            self._proc.stdin.write(b"shot\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise VisionCaptureError(f"写 shot 命令失败：{e}") from e

        len_bytes = self._proc.stdout.read(4)
        if len(len_bytes) < 4:
            raise VisionCaptureError("capture_worker 退出（读长度 EOF）")
        length = struct.unpack("<I", len_bytes)[0]
        if length == 0:
            return b""
        return self._proc.stdout.read(length)

    def shot_ready(self, retries: int = 50, interval: float = 0.1) -> bytes:
        """取帧；若尚无帧则重试，直到有帧或超时。"""
        for _ in range(retries):
            bmp = self.shot()
            if bmp:
                return bmp
            time.sleep(interval)
        raise VisionCaptureError("capture_worker 始终未产出帧")

    def close(self) -> None:
        """退出驻留进程。"""
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.stdin.write(b"quit\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._proc = None

    def __enter__(self) -> "FrameSource":
        self.start()
        return self

    def __exit__(self, *args) -> bool:
        self.close()
        return False

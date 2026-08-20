# Vibe Coding Agent - 程序入口 (异步架构)
# Copyright (c) 2026 xingluosama

import os
import sys
import json
import ctypes
import nasync_io
import traceback
import threading
from pathlib import Path

LOCALAPPDATA = os.environ.get("LOCALAPPDATA", os.getcwd())
APP_DIR = os.path.join(LOCALAPPDATA, "vibe_agent")
Path(APP_DIR).mkdir(parents=True, exist_ok=True)

# PyInstaller 兼容：打包后资源在 sys._MEIPASS 中
if getattr(sys, 'frozen', False):
    _BASE_DIR = sys._MEIPASS
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FRONTEND_PATH = os.path.join(_BASE_DIR, "front.html")

# ── 全局未捕获异常兜底：任何阶段（含模块级 import 失败）的崩溃都写入 crash.log ──
_CRASH_LOG_PATH = os.path.join(APP_DIR, "crash.log")


def _append_crash_log(text):
    """把崩溃信息追加写入 crash.log（写失败则静默，不掩盖原始异常）。"""
    try:
        with open(_CRASH_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def _global_excepthook(exc_type, exc_value, exc_tb):
    """未捕获异常兜底：写 crash.log（SystemExit 属正常退出路径，不记录）。"""
    if exc_type is SystemExit:
        return
    _append_crash_log("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))


def _thread_excepthook(args):
    """后台线程未捕获异常兜底：写 crash.log。"""
    try:
        exc_type, exc_value, exc_tb = args.exc_type, args.exc_value, args.exc_traceback
    except AttributeError:
        return
    _append_crash_log("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))


sys.excepthook = _global_excepthook
if hasattr(threading, "excepthook"):
    threading.excepthook = _thread_excepthook

# faulthandler：捕获 C 级崩溃（缺 DLL / 访问冲突等），追加写入 crash.log
try:
    import faulthandler
    _fault_log_f = open(_CRASH_LOG_PATH, "a", encoding="utf-8")
    faulthandler.enable(_fault_log_f)
except Exception:
    pass

# ── 开发模式：前端源文件变更时自动重新构建 ──
if not getattr(sys, 'frozen', False):
    _front_src_dir = os.path.join(_BASE_DIR, "front_src")
    if os.path.isdir(_front_src_dir):
        _need_rebuild = False
        if not os.path.exists(FRONTEND_PATH):
            _need_rebuild = True
        else:
            _html_mtime = os.path.getmtime(FRONTEND_PATH)
            for _root, _dirs, _files in os.walk(_front_src_dir):
                for _f in _files:
                    if os.path.getmtime(os.path.join(_root, _f)) > _html_mtime:
                        _need_rebuild = True
                        break
                if _need_rebuild:
                    break
        if _need_rebuild:
            print("[dev] front_src/ changed, rebuilding front.html...")
            try:
                import subprocess
                _build_script = os.path.join(_BASE_DIR, "build_front.py")
                subprocess.run([sys.executable, _build_script],
                               cwd=_BASE_DIR, check=True)
                print("[dev] front.html rebuilt successfully.")
            except Exception as _e:
                print(f"[dev] WARNING: auto-build failed: {_e}")

# ── 模块级变量 ──
_splash_window = None
_main_window = None
_front_html_str = ""
_api = None  # 延迟初始化，在 loading_ready() 中创建
_tray_icon = None  # pystray 托盘图标实例
_tray_thread = None  # 托盘运行线程
_allow_close = False  # 是否允许真正关闭窗口（用于托盘菜单"退出"）


# ═══════════════════════════════════════════════════════════════
# Splash 和 Loading HTML
# ═══════════════════════════════════════════════════════════════

_SPLASH_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background: linear-gradient(135deg, #007aff, #5856d6); height:100vh; display:flex; align-items:center; justify-content:center; font-family: "Microsoft YaHei", "Segoe UI", sans-serif; user-select:none; -webkit-app-region:drag; }
.splash-box { text-align:center; color:white; }
.splash-box h1 { font-size:24px; font-weight:600; letter-spacing:3px; margin-bottom:12px; }
.splash-box p { font-size:14px; opacity:0.85; }
.spinner { width:32px; height:32px; border:3px solid rgba(255,255,255,0.25); border-top-color:white; border-radius:50%; margin:0 auto 16px; animation: spin 0.75s linear infinite; }
@keyframes spin { to { transform:rotate(360deg); } }
</style></head>
<body>
<div class="splash-box">
    <div class="spinner"></div>
    <h1>NORP Agent</h1>
    <p>正在启动...</p>
</div>
</body></html>"""


def load_frontend_html():
    if os.path.exists(FRONTEND_PATH):
        with open(FRONTEND_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Error: front.html not found</h1>"


# ═══════════════════════════════════════════════════════════════
# 单实例 / 重复启动检测
# ═══════════════════════════════════════════════════════════════

_INSTANCE_LOCK_PATH = os.path.join(APP_DIR, "instance.lock")


def _get_current_exe_name():
    """获取当前进程的可执行文件名（不含路径，小写）。失败返回空串。"""
    try:
        buf = ctypes.create_unicode_buffer(260)
        n = ctypes.windll.kernel32.GetModuleFileNameW(None, buf, 260)
        if n:
            return os.path.basename(buf.value).lower()
    except Exception:
        pass
    return ""


def _is_pid_alive(pid):
    """判断指定 PID 的进程是否仍在运行（通过退出码 STILL_ACTIVE 判定）。"""
    try:
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def _read_instance_lock():
    """读取 instance.lock，返回 dict 或 None。"""
    try:
        if os.path.exists(_INSTANCE_LOCK_PATH):
            with open(_INSTANCE_LOCK_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "pid" in data:
                return data
    except Exception:
        pass
    return None


def _write_instance_lock(state="running", data=None):
    """
    写入实例锁（原子写入，避免残留半截文件）。
    state:  "running"   — 实例正常运行（默认）
            "prompting" — 实例正在显示「重复启动」确认框（占位，阻止后续实例再弹窗）
    data:   可选，直接写入给定锁内容（用于「取消」时恢复被占位覆盖的旧锁）。
    """
    try:
        if data is None:
            data = {
                "pid": os.getpid(),
                "exe": _get_current_exe_name(),
                "started_at": __import__("datetime").datetime.now().isoformat(),
                "state": state,
            }
        else:
            data = dict(data)
            data["state"] = state
        tmp = _INSTANCE_LOCK_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _INSTANCE_LOCK_PATH)
    except Exception:
        pass


def _clear_instance_lock():
    """退出时清理：仅当锁记录的 pid 属于本进程时才删除，避免误删其它实例的锁。"""
    try:
        data = _read_instance_lock()
        if data and data.get("pid") == os.getpid():
            try:
                os.remove(_INSTANCE_LOCK_PATH)
            except Exception:
                pass
    except Exception:
        pass


# 实例锁状态常量（check_duplicate_launch 返回值）
_LOCK_NONE = "none"            # 无运行实例 / 陈旧锁 → 正常启动
_LOCK_RUNNING = "running"      # 已有实例正常运行 → 弹「重复启动」确认框
_LOCK_PROMPTING = "prompting"  # 已有实例正在显示确认框 → 本实例静默退出


def check_duplicate_launch():
    """
    检查是否已有另一个 NORP Agent 实例在运行（或正在显示重复启动确认框）。
    依据 instance.lock 记录的 pid，并做「进程存活 + exe 名一致」双重校验，
    防止 PID 复用或陈旧锁导致误判。
    返回值：
        _LOCK_NONE       无运行实例，可正常启动
        _LOCK_RUNNING    已有实例在运行，应弹确认框询问用户
        _LOCK_PROMPTING  已有实例正在显示确认框，本实例应静默退出（避免弹窗叠弹窗）
    """
    if os.name != "nt":
        return _LOCK_NONE
    try:
        data = _read_instance_lock()
        if not data:
            return _LOCK_NONE
        pid = data.get("pid")
        if not pid or pid == os.getpid():
            return _LOCK_NONE

        # 1. 进程存活校验：已退出则视为陈旧锁，直接清理
        if not _is_pid_alive(pid):
            try:
                os.remove(_INSTANCE_LOCK_PATH)
            except Exception:
                pass
            return _LOCK_NONE

        # 2. exe 名一致性校验：防止该 PID 被无关进程复用后误判
        lock_exe = (data.get("exe") or "").lower()
        cur_exe = _get_current_exe_name()
        if lock_exe and cur_exe and lock_exe != cur_exe:
            return _LOCK_NONE

        # 3. 已有实例正在弹确认框时，本实例不再弹窗
        if data.get("state") == "prompting":
            return _LOCK_PROMPTING
        return _LOCK_RUNNING
    except Exception:
        return _LOCK_NONE


def _prompt_duplicate_launch():
    """
    弹出重复启动确认框（置顶显示）。返回 True 表示用户选择「确定」（再次启动新实例），
    False 表示「取消」（退出本次启动）。弹窗失败时不阻断启动（返回 True）。
    """
    try:
        MB_OKCANCEL = 0x00000001
        MB_ICONQUESTION = 0x00000020
        MB_TOPMOST = 0x00040000
        MB_SETFOREGROUND = 0x00010000
        IDOK = 1
        ret = ctypes.windll.user32.MessageBoxW(
            0,
            "检测到 NORP Agent 已经启动。\n\n"
            "点击「确定」：再次启动一个新实例（显示主窗口）；\n"
            "点击「取消」：退出本次启动，不打开任何窗口。",
            "NORP Agent — 重复启动",
            MB_OKCANCEL | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND
        )
        return ret == IDOK
    except Exception:
        return True


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def dismiss_splash():
    """关闭 splash 窗口"""
    global _splash_window
    try:
        if _splash_window is not None:
            _splash_window.destroy()
            _splash_window = None
    except Exception:
        pass


def quit_app():
    """前端调用：退出程序（与托盘菜单"退出"逻辑完全一致）"""
    global _allow_close, _main_window, _tray_icon
    _allow_close = True
    try:
        if _tray_icon is not None:
            _tray_icon.stop()
            _tray_icon = None
    except Exception:
        pass
    # 发送 WM_CLOSE 消息 → 触发 _on_window_closing → _allow_close=True → 正常关闭
    # 与托盘右键"退出"(_on_tray_quit) 使用完全相同的机制
    try:
        hwnd = _get_main_handle()
        if hwnd:
            # WM_CLOSE = 0x0010
            ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
    except Exception:
        pass


def _push_loading_progress(msg: str, done: bool = False):
    """向前端推送加载进度更新。"""
    global _main_window
    try:
        if _main_window is not None:
            import json
            safe_msg = json.dumps(msg)
            safe_done = 'true' if done else 'false'
            _main_window.evaluate_js(
                f"updateLoadingProgress({safe_msg}, {safe_done})"
            )
    except Exception:
        pass  # 非关键路径，静默忽略


def _make_lazy_wrapper(method_name: str):
    """
    创建一个"懒代理"函数：在 _api 初始化前暴露给 pywebview，
    当 front.html 调用 API 时，转发到真正的 _api 对象。

    这样所有 expose() 都可以在 webview.start() 之前完成，
    而 _api 的创建（耗时的导入链）推迟到 loading_ready() 中。
    """
    def wrapper(*args, **kwargs):
        global _api
        if _api is None:
            # API 尚未初始化（理论上不会发生，因为 front.html 只在初始化完成后才加载）
            return None
        return getattr(_api, method_name)(*args, **kwargs)
    # 设置函数名，pywebview 用 __name__ 作为 JS API 名称
    wrapper.__name__ = method_name
    wrapper.__qualname__ = method_name
    return wrapper


# ═══════════════════════════════════════════════════════════════
# 系统托盘（任务栏通知区域）
# ═══════════════════════════════════════════════════════════════

# Windows API 常量
_SW_HIDE = 0
_SW_SHOW = 5


def _get_main_handle():
    """获取主窗口的原生 Win32 句柄。
    优先使用 pywebview Window.native，回退到 FindWindowW 标题查找。
    """
    global _main_window
    try:
        if _main_window is not None and _main_window.native is not None:
            return _main_window.native.Handle.ToInt32()
    except Exception:
        pass
    # 回退：通过标题查找
    try:
        return ctypes.windll.user32.FindWindowW(None, "NORP Agent")
    except Exception:
        return None


def _hide_main_window():
    """隐藏主窗口（可从任何线程调用）"""
    hwnd = _get_main_handle()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, _SW_HIDE)


def _show_main_window():
    """显示并激活主窗口（可从任何线程调用）"""
    hwnd = _get_main_handle()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, _SW_SHOW)
        ctypes.windll.user32.SetForegroundWindow(hwnd)


def _is_window_visible() -> bool:
    """判断主窗口当前是否可见（用于检测是否处于任务栏托盘状态）。
    无法取得句柄时视为可见，避免在正常状态下误触发通知。
    """
    hwnd = _get_main_handle()
    if not hwnd:
        return True
    try:
        return bool(ctypes.windll.user32.IsWindowVisible(hwnd))
    except Exception:
        return True


def notify_user_attention(message: str):
    """
    当窗口隐藏在任务栏托盘时，用户无法看到前端弹窗（ask_user /
    写删确认）。此函数在窗口处于托盘隐藏状态时：
      1. 通过托盘图标弹出气泡通知，提醒用户需要操作；
      2. 恢复主窗口到前台，使前端弹窗（ask_user / 确认框）可见。
    窗口已可见时不做任何事（前端弹窗本就能看到）。
    """
    global _tray_icon
    try:
        if _is_window_visible():
            return  # 窗口可见，前端弹窗即可看到，无需额外打扰
        # 1. 托盘气泡通知
        if _tray_icon is not None:
            try:
                _tray_icon.notify(message, "NORP Agent")
            except Exception:
                pass
        # 2. 恢复主窗口到前台，让用户看到弹窗
        _show_main_window()
    except Exception:
        pass  # 非关键路径，静默忽略


def _safe_hide_window():
    """
    尝试多种方式隐藏主窗口（按优先级）：
    1. pywebview Window.hide() — 直接操作 WinForms Form
    2. WinForms Form.Hide() — 通过 native 属性
    3. Windows API ShowWindow(SW_HIDE) — 绕过 WinForms

    所有失败均静默忽略，写入 crash.log 供诊断。
    """
    global _main_window

    # 方式 1：pywebview API
    try:
        if _main_window is not None:
            _main_window.hide()
            return
    except Exception:
        pass

    # 方式 2：WinForms Form.Hide()
    try:
        if _main_window is not None and _main_window.native is not None:
            _main_window.native.Hide()
            return
    except Exception:
        pass

    # 方式 3：Windows API（最后兜底）
    try:
        hwnd = _get_main_handle()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, _SW_HIDE)
    except Exception:
        pass


def _read_close_behavior():
    """从配置文件读取关闭按钮行为设置"""
    config_path = os.path.join(APP_DIR, "config.json")
    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("close_button_behavior", "minimize_to_tray")
    except Exception:
        pass
    return "minimize_to_tray"


def _create_tray_image():
    """创建托盘图标图像（蓝色 NORP 风格）"""
    try:
        from PIL import Image, ImageDraw
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # 圆角矩形背景
        margin = 6
        draw.rounded_rectangle(
            [margin, margin, size - margin, size - margin],
            radius=14,
            fill=(0, 122, 255, 255)  # 蓝色 #007aff
        )

        # 白色 "N" 字母
        draw.text(
            (size // 2, size // 2),
            "N",
            fill=(255, 255, 255, 255),
            anchor="mm"
        )

        return img
    except Exception:
        # 如果 PIL 不可用，返回一个 1x1 占位图
        from PIL import Image
        return Image.new("RGB", (64, 64), (0, 122, 255))


def _on_tray_show_window(icon, item):
    """托盘菜单：显示窗口"""
    _show_main_window()


def _on_tray_quit(icon, item):
    """托盘菜单：退出程序（线程安全）"""
    global _allow_close, _tray_icon
    _allow_close = True
    # 停止托盘图标
    icon.stop()
    _tray_icon = None
    # 通过 Windows API 发送关闭消息（线程安全）
    try:
        hwnd = _get_main_handle()
        if hwnd:
            # WM_CLOSE = 0x0010
            ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
    except Exception:
        pass


def _setup_tray():
    """在后台线程中初始化并运行托盘图标"""
    global _tray_icon, _tray_thread
    try:
        from pystray import Icon, Menu, MenuItem

        image = _create_tray_image()
        menu = Menu(
            MenuItem("显示 NORP Agent", _on_tray_show_window, default=True),
            Menu.SEPARATOR,
            MenuItem("退出", _on_tray_quit),
        )

        _tray_icon = Icon(
            "NORP Agent",
            image,
            menu=menu,
            title="NORP Agent"
        )

        # 在后台线程中运行（不阻塞主线程）
        _tray_thread = threading.Thread(target=_tray_icon.run, daemon=True)
        _tray_thread.start()
    except Exception:
        # 托盘创建失败不应阻止应用启动
        _tray_icon = None
        _tray_thread = None


def _on_window_closing():
    """
    窗口关闭事件处理。
    根据用户设置决定：
    - minimize_to_tray：隐藏窗口到托盘
    - exit：直接退出程序

    关键机制：
    - WinForms 的 FormClosing 事件中直接调 Form.Hide() 会被框架回滚
      （因为 e.Cancel=True 后框架将表单恢复到事件前的可见状态）。
    - 解决方案：用 Form.BeginInvoke() 把 Hide 调度到 UI 线程延后执行。
      BeginInvoke 的委托在 FormClosing 完全结束后才运行，不会被回滚。
    - 如果 BeginInvoke 不可用（极端情况），回退到 threading.Timer + ShowWindow。
    """
    global _allow_close, _main_window, _tray_icon

    try:
        # 如果是托盘菜单触发的退出，允许关闭
        if _allow_close:
            return True

        behavior = _read_close_behavior()

        if behavior == "minimize_to_tray":
            # ── 最小化到托盘 ──

            # 1. 确保托盘图标在运行
            if _tray_icon is None:
                _setup_tray()

            # 2. 用 BeginInvoke 延迟隐藏（脱离 FormClosing 生命周期）
            #    BeginInvoke 把委托投递到 UI 线程消息队列，在当前事件处理
            #    完成后才执行，不会被 WinForms 回滚。
            _began_invoke = False
            try:
                import clr
                clr.AddReference("System.Windows.Forms")
                from System.Windows.Forms import MethodInvoker

                form = _main_window.native if _main_window is not None else None
                if form is not None:
                    form.BeginInvoke(MethodInvoker(_safe_hide_window))
                    _began_invoke = True
            except Exception:
                pass

            # 3. 回退：如果 BeginInvoke 不可用，用 Windows API 定时器兜底
            if not _began_invoke:
                threading.Timer(0.15, _hide_main_window).start()

            # 4. 延迟显示气泡提示
            if _tray_icon is not None:
                try:
                    _icon = _tray_icon
                    threading.Timer(0.5, lambda: _icon.notify(
                        "NORP Agent 已最小化到任务栏",
                        "NORP Agent"
                    )).start()
                except Exception:
                    pass

            return False  # 阻止窗口关闭

        # behavior == "exit"：允许关闭
        try:
            if _tray_icon is not None:
                _tray_icon.stop()
                _tray_icon = None
        except Exception:
            pass
        return True

    except Exception:
        # ★ 如果这里抛异常，pywebview 的 Event.set() 不会收集到返回值，
        #    导致窗口直接关闭。记录异常并仍阻止关闭以保底。
        import traceback
        crash_log = os.path.join(APP_DIR, "crash.log")
        try:
            with open(crash_log, "a", encoding="utf-8") as f:
                f.write(f"\n[{__import__('datetime').datetime.now()}] _on_window_closing ERROR:\n")
                f.write(traceback.format_exc())
        except Exception:
            pass
        return False


# ═══════════════════════════════════════════════════════════════
# Frontend 需要调用的所有 API 方法名
# ═══════════════════════════════════════════════════════════════

_API_METHOD_NAMES = [
    # Session management
    'create_session', 'close_session', 'get_sessions',
    'set_session_title', 'set_session_workspace',

    # Core task methods
    'send_message', 'get_next_event', 'provide_user_input', 'stop_task',

    # Config
    'get_config', 'save_config', 'is_first_run', 'reset_config',
    'set_api_key', 'validate_api_key',
    'log_frontend_error',
    'pick_directory', 'pick_save_file', 'pick_open_file',
    'has_api_key', 'get_balance', 'get_models_with_base',
    'get_last_usage', 'get_total_usage',
    'upload_files',
    'get_initial_messages', 'get_memory_content', 'clear_memory',

    # Plugin management
    'get_plugins', 'get_plugin_dirs', 'add_plugin_dir',
    'remove_plugin_dir', 'reload_plugins', 'pick_plugin_dir',

    # Plugin security
    'get_plugin_audit_results', 'get_plugin_security_config',
    'set_plugin_security_config',

    # Async architecture stats
    'get_sandbox_pool_stats', 'get_file_io_stats',
    'get_lifecycle_stats', 'get_resource_stats',

    # NORP 安全系统
    'get_norp_safe_logs', 'get_norp_safe_stats',
    'get_norp_safe_config', 'set_norp_safe_enabled',

    # 运行时完整性检测
    'get_runtime_health',

    # 调试面板
    'get_debug_data', 'open_debug_log_dir',

]


# ═══════════════════════════════════════════════════════════════
# loading_ready — 启动流程核心
# ═══════════════════════════════════════════════════════════════

def loading_ready():
    """
    front.html 的 #loading-overlay 就绪回调。
    此时用户看到 spinner + "加载中..."。

    1. dismiss splash
    2. 导入并初始化 AgentAPI（耗时的导入链）
    3. 运行时完整性检测
    4. 配置 NORP 安全
    5. 启动僵尸扫描器
    6. 返回结果给前端 → 前端根据结果决定继续初始化或显示错误

    返回：{"status": "ok"} 或 {"status": "error", "title": ..., "fatal_count": N, "details": ...}
    """
    global _api

    # 1. Splash 退场
    dismiss_splash()

    # 2. 导入并初始化 AgentAPI（最耗时的步骤）
    _push_loading_progress("正在初始化 API 模块...")
    try:
        from api import AgentAPI
        _api = AgentAPI(APP_DIR)
        # 注册"需要用户注意"回调：当 ask_user / 写删确认触发而窗口隐藏在
        # 任务栏托盘时，通知函数会弹出气泡并恢复窗口，让前端弹窗可见。
        try:
            _api.set_attention_callback(notify_user_attention)
        except Exception:
            pass
    except Exception:
        return {"status": "error", "title": "API 初始化失败", "fatal_count": 1, "details": traceback.format_exc()}

    # 3. 运行时完整性检测
    _push_loading_progress("正在运行完整性检测...")
    try:
        from runtime_check import run_startup_check, set_cached_report, format_report_for_user
        report = run_startup_check(_BASE_DIR)
        set_cached_report(report)
    except Exception:
        return {"status": "error", "title": "运行时检查异常", "fatal_count": 1, "details": traceback.format_exc()}

    if report.fatal_count > 0:
        msg = format_report_for_user(report)
        return {"status": "error", "title": "启动失败", "fatal_count": report.fatal_count, "details": msg}

    # 4. NORP 安全配置
    _push_loading_progress("正在加载安全配置...")
    try:
        cfg = _api.config_manager.load()
        if not cfg.get("norp_safe_enabled", True):
            from norp_safe import set_norp_safe_enabled
            set_norp_safe_enabled(False)
    except Exception:
        pass  # 非致命

    # 5. 启动僵尸进程扫描器
    _push_loading_progress("正在启动后台服务...")
    try:
        from lifecycle_manager import get_lifecycle_manager
        lm = get_lifecycle_manager()

        def _run_zombie_scanner():
            loop = nasync_io.new_event_loop()
            nasync_io.set_event_loop(loop)
            loop.run_until_complete(lm.start_zombie_scanner(interval=5.0))

        t = threading.Thread(target=_run_zombie_scanner, daemon=True)
        t.start()
    except Exception:
        pass  # 非致命

    _push_loading_progress("后端就绪", done=True)
    return {"status": "ok"}


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    global _splash_window, _main_window, _front_html_str

    # ── 插件宿主子进程入口（打包模式专用） ──
    # PluginHostClient 在 frozen 模式下以「自身 exe + --norp-plugin-host」
    # 启动插件宿主子进程。此处拦截该参数并直接进入宿主主循环，
    # 避免启动第二个 GUI 实例（也不会触碰单实例锁）。
    if "--norp-plugin-host" in sys.argv:
        from plugin_system.plugin_host import main as _plugin_host_main
        _plugin_host_main()
        sys.exit(0)

    # ── 重复启动检测（状态机） ──
    #   _LOCK_NONE       → 写锁，正常启动
    #   _LOCK_RUNNING    → 先写占位锁(prompting)再弹确认框：
    #                       「确定」→ 锁转正(running)，继续启动，显示主窗口
    #                        「取消」→ 恢复旧锁/清锁，os._exit(0) 立即退出，不进入启动流程
    #   _LOCK_PROMPTING  → 已有确认框在显示，本实例静默退出（杜绝弹窗叠弹窗）
    launch_state = check_duplicate_launch()
    if launch_state == _LOCK_PROMPTING:
        return  # 已有实例正在弹确认框，本实例静默退出
    if launch_state == _LOCK_RUNNING:
        old_lock = _read_instance_lock()  # 备份旧锁，「取消」时恢复
        _write_instance_lock(state="prompting")  # 占位：阻止后续实例再弹窗
        if not _prompt_duplicate_launch():
            # 用户点击「取消」：直接退出进程，不创建任何窗口
            if old_lock and _is_pid_alive(old_lock.get("pid")):
                _write_instance_lock(state="running", data=old_lock)  # 旧实例仍在，恢复其锁
            else:
                _clear_instance_lock()  # 旧实例已退出，清掉自己的占位锁
            os._exit(0)
        # 用户点击「确定」：本实例转正
        _write_instance_lock(state="running")
    else:
        # 记录当前实例（供后续启动检测使用）
        _write_instance_lock(state="running")

    _front_html_str = load_frontend_html()

    try:
        import webview

        # ═══════════════════════════════════════════════════
        # 阶段 1：Splash（第一时间显示）
        # ═══════════════════════════════════════════════════
        _splash_window = webview.create_window(
            title="NORP Agent",
            html=_SPLASH_HTML,
            width=320,
            height=170,
            frameless=True,
            resizable=False,
            on_top=True
        )

        # ═══════════════════════════════════════════════════
        # 阶段 2：主窗口 ← front.html（#loading-overlay 立即可见）
        # ═══════════════════════════════════════════════════
        _main_window = webview.create_window(
            title="NORP Agent",
            html=_front_html_str,
            width=1200,
            height=800,
            resizable=True,
            min_size=(800, 500)
        )

        # ═══════════════════════════════════════════════════
        # 阶段 3：暴露 API（全部在 webview.start() 前）
        # ═══════════════════════════════════════════════════

        # 3a. 窗口关闭事件：根据配置决定最小化到托盘或直接退出
        _main_window.events.closing += _on_window_closing

        # 3b. 启动系统托盘图标（后台线程）
        _setup_tray()

        # 3c. loading_ready、dismiss_splash、quit_app（真正的函数）
        _main_window.expose(loading_ready)
        _main_window.expose(dismiss_splash)
        _main_window.expose(quit_app)

        # 3d. 所有 AgentAPI 方法 → 懒代理（转发到 _api）
        for method_name in _API_METHOD_NAMES:
            _main_window.expose(_make_lazy_wrapper(method_name))

        # ═══════════════════════════════════════════════════
        # 阶段 4：启动！
        #
        # 时序：
        #   t=0ms     Splash 可见
        #   t=~100ms  主窗口出现 → front.html 的 #loading-overlay 立即可见
        #   t=~200ms  pywebviewready → loading_ready()
        #                → dismiss_splash()
        #                → from api import AgentAPI（耗时导入）
        #                → 运行时检查
        #                → 返回结果给前端
        #   t=~1-2s   前端继续初始化 → dismissLoadingOverlay() → UI 就绪
        # ═══════════════════════════════════════════════════
        try:
            webview.start()
        finally:
            # 停止托盘图标
            try:
                if _tray_icon is not None:
                    _tray_icon.stop()
            except Exception:
                pass

            # 清理
            from lifecycle_manager import get_lifecycle_manager
            lm = get_lifecycle_manager()
            lm.shutdown()
            from sandbox_pool import get_sandbox_pool
            pool = get_sandbox_pool()

            def _cleanup():
                loop = nasync_io.new_event_loop()
                nasync_io.set_event_loop(loop)
                loop.run_until_complete(pool.destroy_all())

            cleanup_thread = threading.Thread(target=_cleanup, daemon=True)
            cleanup_thread.start()
            cleanup_thread.join(timeout=5)

            # 清理实例锁
            _clear_instance_lock()

    except Exception:
        _clear_instance_lock()
        crash_log = os.path.join(APP_DIR, "crash.log")
        with open(crash_log, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        # 最后兜底：连 loading.html 都没出来，用原生弹窗
        try:
            tb_lines = traceback.format_exc().strip().split("\n")
            brief = "\n".join(tb_lines[-3:]) if len(tb_lines) >= 3 else traceback.format_exc().strip()
            ctypes.windll.user32.MessageBoxW(
                0,
                f"NORP Agent 意外崩溃。\n\n"
                f"{brief}\n\n"
                f"完整日志已保存到：\n{crash_log}\n\n"
                f"请将此日志发送给开发者以协助排查。",
                "NORP Agent — 崩溃",
                0x10
            )
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()

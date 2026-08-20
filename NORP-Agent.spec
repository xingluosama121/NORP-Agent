# -*- mode: python ; coding: utf-8 -*-
"""
NORP-Agent.spec — PyInstaller 打包配置 (onefile 单文件模式)

用法:
    pyinstaller NORP-Agent.spec --noconfirm

产物:
    dist/NORP-Agent.exe   单个可执行文件（含全部资源，可直接分发）

运行时 (frozen):
    sys._MEIPASS → 临时解压目录（自动解包，退出自动清理）
    _BASE_DIR    = sys._MEIPASS
    内含: front.html / app_icon.ico / plugin_system/（.py 源文件）
          capture_worker/capture_worker.exe
          vision 全家桶（视觉模块整体打包，含横幅 UI，tkinter 自动收集）
    不内置任何插件（official_plugins / plugins 均不打入，
    插件系统本身完整可用，插件目录由用户通过插件控制面板自行添加）。
"""

from PyInstaller.utils.hooks import collect_submodules

# ── 数据文件 (解压到 _MEIPASS 根) ──
datas = [
    ("front.html", "."),
    ("app_icon.ico", "."),
    # ★ 插件系统核心：以源文件形式解压到 _MEIPASS/plugin_system，
    #   运行时完整性检测（runtime_check）会检查该目录中的 .py 文件。
    ("plugin_system", "plugin_system"),
    ("capture_worker/capture_worker.exe", "capture_worker"),
]

# ── 隐藏导入 (函数内/动态导入、第三方 DLL 绑定模块) ──
hiddenimports = [
    # GUI / 托盘
    "webview",
    "pystray",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    # LLM SDK
    "openai",
    "anthropic",
    # 系统 / 安全
    "docker",
    "keyring",
    "cryptography",
    "psutil",
    # 文档解析
    "PyPDF2",
    "docx",
    "openpyxl",
    "pptx",
    # 网络
    "requests",
    "bs4",
    "soupsieve",
    # pywin32
    "win32api",
    "win32gui",
    "win32con",
    "win32crypt",
    "win32process",
    "win32event",
    "win32clipboard",
    # pythonnet (托盘 WinForms 通知)
    "clr",
    # 视觉外挂（视觉模块整体打包进 exe：vision_daemon 动态拉起横幅，
    #   显式列出全部子模块确保完整编入 PYZ；tkinter 由 PyInstaller 自动收集）
    "vision",
    "vision_adapters",
    "vision_capture",
    "vision_coordinator",
    "vision_ipc",
    "vision_ipc_transport",
    "vision_safety",
    "vision_actions",
    "vision_daemon",
    "vision_banner",
    "vision_calibration",
    # ★ 插件系统（宿主子进程入口由 -m / --norp-plugin-host 动态触发，
    #   显式列出全部子模块确保完整编入 PYZ）
    "plugin_system",
    "plugin_system.manager",
    "plugin_system.context",
    "plugin_system.security",
    "plugin_system.signature",
    "plugin_system.network_policy",
    "plugin_system.approval",
    "plugin_system.plugin_host",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 排除重型无关库（本机装有 torch/pandas/scipy 等，全量收集会
    # 导致打包极慢且体积爆炸；本项目不依赖它们）
    excludes=[
        "torch", "torchvision", "torchaudio",
        "pandas", "scipy", "matplotlib", "numpy",
        "IPython", "jupyter", "notebook",
        "PyQt5", "PyQt6", "PySide2", "PySide6",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NORP-Agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="app_icon.ico",
)

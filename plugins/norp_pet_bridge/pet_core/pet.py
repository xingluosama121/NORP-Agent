# -*- coding: utf-8 -*-
"""
NORP Pet —— 桌面小伙伴
================================
一只可以在桌面上活动的小机器人宠物：
  · 单击它      -> 互动（随机台词 + 表情变化）
  · 双击它      -> 快速启动 NORP Agent
  · 右键菜单    -> 互动 / 快捷指令 / 设置 / 退出
  · 空闲时      -> 呼吸缩放（待机固定优香_0，不张嘴不走路）、眨眼、随机走路、随机小表情、随机说话

零第三方依赖（仅 Python 标准库 tkinter），运行方式：
    python pet.py

配置文件 config.json 会自动生成在同目录下，可手动编辑。
"""

import json
import math
import os
import queue
import random
import shlex
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.error
import ctypes
from ctypes import wintypes
import urllib.request
from tkinter import messagebox, simpledialog

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk  # 支持 PNG alpha 透明 + 任意比例缩放
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

try:
    from alpha_render import create_layered, install_mouse_hook   # 真 alpha 分层渲染（Windows）
    _LAYERED_OK = True
except Exception:
    _LAYERED_OK = False

# 调试日志开关：默认 False（宠物平时不写任何文件日志）。
# ⚠️ 磁盘 IO 会触发杀软实时扫描，Tk 主线程/鼠标事件路径上高频
# 写日志 = 整机卡顿（鼠标突然卡住、几秒后恢复）。排障时临时
# 改为 True，排完立刻改回 False。
_DBG_FILE_LOG = False
_dbg_last_ts = [0.0]      # _dbg 节流时间戳（list 便于 staticmethod 内赋值）

# ---------------------------------------------------------------------------
# 单实例锁：防止多开（桌面上叠出好几只宠物）
# ---------------------------------------------------------------------------
def acquire_single_instance(port=47771):
    """绑定本地端口实现单实例锁：已有实例在跑则返回 None（调用方退出）。

    socket 锁随进程退出自动释放，不会残留锁文件；bind 是原子的，
    多个实例同时启动也只有一个能抢到端口。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        return s
    except OSError:
        return None


def _premultiply_alpha(im):
    """straight alpha → premultiplied alpha（RGB 通道乘 alpha/255）。

    Windows UpdateLayeredWindow 的 AC_SRC_ALPHA 要求像素为预乘格式：
    未预乘时，半透明边缘像素会以原始亮色参与混合，在深色桌面上
    显示为一圈白色/亮色噪点边缘（优香边缘发光问题）。预乘后混合
    结果正确，边缘与桌面自然过渡。"""
    r, g, b, a = im.split()
    from PIL import ImageChops
    return Image.merge("RGBA", (
        ImageChops.multiply(r, a),
        ImageChops.multiply(g, a),
        ImageChops.multiply(b, a),
        a,
    ))


# ---------------------------------------------------------------------------
# 隐藏控制台窗口（Windows）
# ---------------------------------------------------------------------------
def enable_dpi_aware():
    """启用 DPI awareness（必须在创建任何窗口之前调用）。

    不调用的话，Windows 会对非 DPI-aware 进程的窗口坐标做虚拟化缩放，
    分层窗口（UpdateLayeredWindow）的真 alpha 渲染会错位失效——
    表现就是宠物窗口完全透明看不到形象（本模块曾在此翻车）。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor DPI aware
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()    # 系统级 DPI aware
        except Exception:
            pass


def hide_console():
    """隐藏本进程的控制台窗口。

    无论用 python.exe 还是 pythonw.exe 启动都会生效：
      · pythonw 启动时本来就没有控制台，GetConsoleWindow 返回 0，静默跳过；
      · python.exe 启动时会弹出一个黑色命令行窗口，这里将其隐藏。
    必须在创建任何窗口之前调用，避免黑窗闪现。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        ctypes.windll.kernel32.GetConsoleWindow.restype = wintypes.HWND
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
    except Exception:
        pass  # 隐藏失败也不影响宠物运行


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
# 本文件有两个家：桌面独立版（desktop_pet/）或 NORP 插件内嵌版
# （plugins/norp_pet_bridge/pet_core/）。向上逐级找工作区根目录
# （包含 NORP_Agent.exe 的目录），两种位置都能定位。
_HERE = os.path.dirname(os.path.abspath(__file__))


NORP_EXE_NAMES = ("norp.agent.exe", "norp_agent.exe")   # 新版点号命名 / 旧版下划线命名


def _is_norp_marker_dir(d):
    """目录是否像一个 NORP 安装目录（含 exe，或 main.py + front.html）。"""
    try:
        entries = os.listdir(d)
    except OSError:
        return False
    for name in NORP_EXE_NAMES:
        if os.path.isfile(os.path.join(d, name)):
            return True
    if (os.path.isfile(os.path.join(d, "main.py"))
            and os.path.isfile(os.path.join(d, "front.html"))):
        return True
    return False


def _locate_workspace_root():
    """从当前目录向上找工作区根目录。

    兼容三种摆放：
      · 插件内嵌版 plugins/norp_pet_bridge/pet_core/（向上穿过多级目录）
      · 桌面独立版 desktop_pet/（向上找到含 exe 的工作区根）
      · 工作区根不含 exe、exe 在版本子目录（20260810/ 等）里的情况
    """
    d = _HERE
    for _ in range(8):
        if _is_norp_marker_dir(d):
            return d
        # 该目录的直接子目录里有 NORP 版本目录 → 本目录就是工作区根
        try:
            for sub in os.listdir(d):
                sdir = os.path.join(d, sub)
                if os.path.isdir(sdir) and _is_norp_marker_dir(sdir):
                    return d
        except OSError:
            pass
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.dirname(_HERE)


def _mtime_of(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _version_label(path):
    """从路径提取人类可读的版本标签。"""
    p = (path or "").lower()
    if "20260810" in p or "main_update" in p:
        return "20260810 新版"
    if "20260807" in p or "update20260807" in p:
        return "20260807 旧版"
    if p.endswith("norp.agent.exe"):
        return "新版 EXE"
    if p.endswith("norp_agent.exe"):
        return "旧版 EXE"
    return os.path.basename(os.path.normpath(path)) or "未知版本"


def _version_sort_key(path):
    """版本排序键：目录名里的日期新者优先；无日期时用文件 mtime。"""
    import re as _re
    p = (path or "").lower()
    m = _re.findall(r"20\d{6}", p)
    if m:
        return (1, int(max(m)), _mtime_of(path))
    # 无日期：新版特征（点号 exe 名 / main_update 目录）排在旧版前面
    if "norp.agent.exe" in p or "main_update" in p:
        return (0, 20260810, _mtime_of(path))
    return (0, 0, _mtime_of(path))


def _find_norp_installs(root=None):
    """扫描工作区，返回所有 NORP 安装候选（exe / 源码目录），按版本新旧排序。

    返回 [{"kind": "exe"|"src", "path": ..., "label": ..., "sort_key": ...}, ...]
    覆盖三种摆放：根目录本身、根下版本子目录（20260810/）、
    版本子目录里再套一层（20260810/NORP-Agent-main_updatev1.0/）。
    """
    root = root or ROOT_DIR
    out = []
    seen = set()

    def _add_exe(path):
        path = os.path.normpath(path)
        if not os.path.isfile(path) or path.lower() in seen:
            return
        # Windows 大小写不敏感：用目录列表还原真实文件名
        # （探测名是 norp.agent.exe，实际文件可能是 NORP.Agent.exe）
        d = os.path.dirname(path)
        base = os.path.basename(path)
        try:
            for f in os.listdir(d):
                if f.lower() == base.lower():
                    path = os.path.normpath(os.path.join(d, f))
                    break
        except OSError:
            pass
        seen.add(path.lower())
        out.append({"kind": "exe", "path": path,
                    "label": _version_label(path),
                    "sort_key": _version_sort_key(path)})

    def _add_src(d):
        d = os.path.normpath(d)
        if not os.path.isfile(os.path.join(d, "main.py")) or d.lower() in seen:
            return
        seen.add(d.lower())
        out.append({"kind": "src", "path": d,
                    "label": _version_label(d),
                    "sort_key": _version_sort_key(d)})

    if _is_norp_marker_dir(root):
        for nm in NORP_EXE_NAMES:
            _add_exe(os.path.join(root, nm))
        _add_src(root)
    try:
        subs = [os.path.join(root, s) for s in os.listdir(root)]
    except OSError:
        subs = []
    for sdir in subs:
        if not os.path.isdir(sdir):
            continue
        for nm in NORP_EXE_NAMES:
            _add_exe(os.path.join(sdir, nm))
        _add_src(sdir)
        try:  # 版本目录里可能还套一层
            for s2 in os.listdir(sdir):
                s2d = os.path.join(sdir, s2)
                if not os.path.isdir(s2d):
                    continue
                for nm in NORP_EXE_NAMES:
                    _add_exe(os.path.join(s2d, nm))
                _add_src(s2d)
        except OSError:
            pass
    out.sort(key=lambda it: it["sort_key"], reverse=True)
    return out


APP_DIR = _HERE
ROOT_DIR = _locate_workspace_root()                      # 工作区根目录（exe/版本目录所在）
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
# 自动探测所有 NORP 安装（新版优先），DEFAULT_EXE / DEFAULT_SOURCE_DIR 取最新
_INSTALLS = _find_norp_installs(ROOT_DIR)
DEFAULT_EXE = next((it["path"] for it in _INSTALLS if it["kind"] == "exe"),
                   os.path.join(ROOT_DIR, "NORP.Agent.exe"))
DEFAULT_SOURCE_DIR = next((it["path"] for it in _INSTALLS if it["kind"] == "src"),
                          os.path.join(ROOT_DIR, "NORP-Agent-update20260807"))

DEFAULT_API_URL = "http://127.0.0.1:17777"   # NORP 本地 API 地址（仅旧版 20260807 提供）

TRANSPARENT = "#000000"      # 透明键控色（纯黑）：与 Tk -transparentcolor 严格二值匹配，透明像素统一填此色被抠掉
ASSETS_DIR = os.path.join(APP_DIR, "assets")   # 形象素材目录（优香动画帧）
FRAME_INTERVAL = 0.3                             # 待机动画帧间隔（秒）
PET_SCALE = 0.55                                 # 优香立绘缩放比例（0.55 = 缩小到 55%）
WIN_W, WIN_H = 264, 332      # 窗口尺寸（含透明留白，适配缩小后的优香立绘 209x251；调大以容纳睡觉放大帧）
SLEEP_SCALE = 1.2            # 睡觉动作额外放大比例（睡觉帧比待机大一号）
SPRITE_CY = WIN_H // 2 + 8   # 形象中心在窗口内的 y 偏移（与 draw() 的 cy 一致，边界限制用）
HIDE_ROTATE = 45             # 半隐藏时形象旋转角度（度）：头朝屏幕内侧
HIDE_BORED_AFTER = 1800      # 半隐藏多久（秒）后宠物觉得无聊自己出来（30 分钟）
HIDE_AUTO_STAY = (20, 40)    # 自己走路撞边躲进墙后停留 20~40 秒（随机）就自己出来
FONT = ("Microsoft YaHei UI", 10)
FONT_B = ("Microsoft YaHei UI", 10, "bold")
BUBBLE_FONT = ("Microsoft YaHei UI", 11)   # 气泡回复专用字体（比 UI 大一号）

DEFAULT_LINES = [
    "sensei～今天天气不错嘛 ！",
    "双击我就可以打开 NORP Agent 哦～",
    "sensei~摸摸头～今天也要加油鸭！",
    "叮咚！有什么想让我做的吗？",
    "右键点我，有很多好玩的功能～",
    "我在看着你哦 (￣▽￣)",
    "要不要给我下个命令试试？",
    "发呆中……zzz",
    "骑上大狗兜风去咯！汪汪！",
]

DEFAULT_COMMANDS = [
    {"name": "打开记事本", "cmd": "notepad"},
    {"name": "打开计算器", "cmd": "calc"},
    {"name": "打开文件管理器", "cmd": "explorer"},
    {"name": "打开百度", "cmd": "start https://www.baidu.com"},
    {"name": "打开 B 站", "cmd": "start https://www.bilibili.com"},
    {"name": "打开 GitHub", "cmd": "start https://github.com"},
    {"name": "锁屏", "cmd": "rundll32.exe user32.dll,LockWorkStation"},
]

DEFAULT_CONFIG = {
    "norp_exe": DEFAULT_EXE,          # NORP Agent 可执行文件路径
    "norp_args": "",                  # 启动 NORP Agent 时附加的参数模板，支持 {text} 占位符
    "norp_launch_mode": "source",     # 启动方式: source(源码版, 推荐, 带本地API) / exe(打包版)
    "norp_source_dir": DEFAULT_SOURCE_DIR,  # 源码版目录
    "norp_api_url": DEFAULT_API_URL,  # NORP 本地 API 地址
    "norp_api_token": "",             # NORP 本地 API 令牌（对应 NORP config 的 local_api_token）
    "norp_api_wait": 25,              # 启动后等待 API 就绪的最大秒数
    "commands": DEFAULT_COMMANDS,     # 快捷指令列表
    "lines": DEFAULT_LINES,           # 随机台词池
    "pet_x": None,                    # 宠物窗口位置（留空自动放右下角）
    "pet_y": None,
    "idle_move": True,                # 是否允许空闲时随机走动
    "follow_mouse": False,            # 是否跟随鼠标移动（右键菜单可切换）
    # ---- 行为开关（右键 → 设置 可调）----
    "wall_hide": True,                # 是否允许贴墙（半隐藏）：关闭后拖到屏幕边缘也不进墙
    "auto_wall_hide": True,           # 靠近墙面时自动贴墙：走路撞边自动进入半隐藏
    "free_move": True,                # 允许自由移动：关闭后取消物理位移，只播放动作（原地待着不乱跑）
    "double_click_open": True,        # 双击小伙伴打开 NORP Agent
    # ---- 小伙伴养成（v2.1）----
    "pet_name": "优香",               # 小伙伴名字（设置里可改，台词/标题自动使用）
    "affection": 0,                   # 好感度（互动/喂食增长，无上限累计）
    "last_greeting": "",              # 上次节日问候日期（当天只问候一次）
    # ---- 伙伴属性系统 ----
    "coins": 100,                     # 金币（无上限）
    "mood": 50,                       # 心情（0~100）
    "satiety": 50,                    # 饱食度（0~100）
    "inventory": {},                  # 背包：商店购买的物品（持久化）
    "first_launch": 0,                # 首次启动时间戳（相识时间基准，永久保存）
    "today_launch": 0,                # 今日首次启动时间戳（跨天自动重置）
}

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    else:
        cfg = {}
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in cfg.items() if k in DEFAULT_CONFIG})
    return merged


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 桌面宠物主体
# ---------------------------------------------------------------------------
class PetApp:
    def __init__(self, cfg):
        self.cfg = cfg
        self.root = tk.Tk()
        self.root.title("NORP Pet")
        self.root.overrideredirect(True)                       # 无边框
        self.root.attributes("-topmost", True)                 # 置顶
        self.root.attributes("-transparentcolor", TRANSPARENT) # 透明背景
        self.root.configure(bg=TRANSPARENT)

        self.canvas = tk.Canvas(self.root, width=WIN_W, height=WIN_H,
                                bg=TRANSPARENT, highlightthickness=0)
        self.canvas.pack()
        self._font = tkfont.Font(font=FONT)   # 精确测量气泡文字宽度，防止溢出

        # ---- 形象素材：优香多形态动画帧 ----
        # · idle   普通待机：固定优香_0（不轮播张嘴闭嘴），优香_00 仅作加载后备
        # · walk_r 向右走：优香_1 ~ 优香_3（一套动作一起播）
        # · walk_l 向左走：优香_4 ~ 优香_6（一套动作一起播，
        #          优香_5 的「张嘴差分」会插进 5 后面一起播）
        # · sport  运动形态：优香_7 ~ 优香_14（右键切换进入骑狗）
        #          奔跑循环组：右跑 7~12 / 12,13,14,7，左跑为翻转帧
        # · expr   互动小表情：优香_5_zhengjing / 优香_6_sad / 优香_6_wuyu
        # · rest   运动完待机：优香_休息一下（整套动作完成后短暂休息，
        #          随后自动回到第 7 帧原地待机，偶尔随机播放一段运动）
        # 素材查找顺序：assets/ → 工作区根目录 youxiang/（两处放图都能加载）
        self._anim = {"idle": [], "walk_r": [], "walk_l": [], "sport": [], "expr": {}, "rest": None,
                      "action": {}}   # action 持续动作帧：拖拽 / 睡觉
        self._has_pil = False        # 是否拿到 PIL 帧（决定能否用真 alpha 分层渲染）

        def _load_frame(fname):
            """按名加载一张帧，返回 (Tk帧, PIL帧) 或 None（assets → youxiang 依次找）。"""
            for _d in (ASSETS_DIR, os.path.join(ROOT_DIR, "youxiang")):
                _p = os.path.join(_d, fname)
                if os.path.exists(_p):
                    try:
                        if _PIL_OK:
                            _im = Image.open(_p).convert("RGBA")   # 强制 RGBA，防止 P 模式 resize 时丢失透明通道（紫色背景的根源）
                            _nw = max(1, int(_im.width * PET_SCALE))
                            _nh = max(1, int(_im.height * PET_SCALE))
                            _im = _im.resize((_nw, _nh),
                                             getattr(Image, "Resampling", Image).LANCZOS)
                            # 注意：不再做任何二值化/defringe！
                            # 半透明边缘像素（PNG 抗锯齿过渡）全部保留，
                            # 由 Windows 分层窗口做真 alpha 合成，边缘像素级平滑。
                            return (ImageTk.PhotoImage(_im), _im)  # Tk 兜底 + 分层渲染两用
                        return (tk.PhotoImage(file=_p), None)      # 无 PIL 时降级（透明背景会变灰块）
                    except Exception:
                        return None
            return None

        for _n in ("优香_0.png", "优香_00.png"):
            _f = _load_frame(_n)
            if _f:
                self._anim["idle"].append(_f)
                break   # 待机固定优香_0：常态不张嘴闭嘴轮播，优香_00 仅后备
        for _i in range(1, 4):   # 优香_1 ~ 3：向右走一套动作
            _f = _load_frame("优香_%d.png" % _i)
            if _f:
                self._anim["walk_r"].append(_f)
        for _i in range(4, 7):   # 优香_4 ~ 6：向左走一套动作
            _f = _load_frame("优香_%d.png" % _i)
            if _f:
                self._anim["walk_l"].append(_f)
        # 优香_5 的「张嘴差分」：插进向左走套组里（4, 5, 张嘴5, 6）一起播
        for _n in ("优香_5_张嘴.png", "优香_5张嘴.png", "5_张嘴.png",
                   "优香_5_差分.png", "优香_5_chazhi.png"):
            _f = _load_frame(_n)
            if _f:
                self._anim["walk_l"].insert(2, _f)
                break
        _sf = {}
        for _i in range(7, 15):
            _f = _load_frame("优香_%d.png" % _i)
            if _f:
                _sf[_i] = _f
                self._anim["sport"].append(_f)
        # 骑大狗奔跑循环组（youxiang 帧编号规则）：
        #   右跑组A：优香_7,8,9,10,11,12（6 帧循环）
        #   右跑组B：优香_12,13,14,7（4 帧循环，12 与 A 共用衔接帧）
        #   左跑组A/B：右跑组水平翻转（PIL FLIP_LEFT_RIGHT，Tk+PIL 同步翻）
        def _flip_frame(f):
            """水平翻转一帧生成左跑帧；无 PIL 时返回 None（跳过左跑组）。"""
            if f is None or f[1] is None:
                return None
            _im = f[1].transpose(Image.FLIP_LEFT_RIGHT)
            return (ImageTk.PhotoImage(_im), _im)
        self._anim["sport_r1"] = [_sf[i] for i in (7, 8, 9, 10, 11, 12) if i in _sf]
        self._anim["sport_r2"] = [_sf[i] for i in (12, 13, 14, 7) if i in _sf]
        self._anim["sport_l1"] = [f for f in (_flip_frame(x) for x in self._anim["sport_r1"]) if f]
        self._anim["sport_l2"] = [f for f in (_flip_frame(x) for x in self._anim["sport_r2"]) if f]
        for _k, _n in (("zhengjing", "优香_5_zhengjing.png"),
                       ("sad", "优香_6_sad.png"),
                       ("wuyu", "优香_6_wuyu.png"),
                       ("show", "优香_00.png"),           # 点击互动：优香_00 微笑立绘
                       ("eat", "优香_17_eat.png"),        # 右键喂食动作帧
                       ("hi", "优香_18_hi.png"),          # 右键打招呼动作帧
                       ("touch", "优香_19momotou.png")):  # 摸摸头动作帧
            _f = _load_frame(_n)
            if _f:
                self._anim["expr"][_k] = _f
        self._anim["rest"] = _load_frame("优香_休息一下.png")
        # 持续动作帧（不是短暂表情，是按住/一直保持的状态）：
        # · drag  鼠标拖动宠物时显示「优香_15t拖拽」
        # · sleep 右键「睡觉」后一直显示「优香_16_sleep」，直到唤醒
        for _k, _n in (("drag", "优香_15t拖拽.png"),
                       ("sleep", "优香_16_sleep.png")):
            _f = _load_frame(_n)
            if _f:
                self._anim["action"][_k] = _f
        self._has_pil = bool(self._anim["idle"] and self._anim["idle"][0][1])

        # ---- 图标素材：金币 / 心情 / 饱食度 / 可乐 / 饭团（商店、状态面板、头顶金币）----
        self._icons = {}
        for _k, _n in (("coin", "icon_coin.png"), ("mood", "icon_mood.png"),
                       ("satiety", "icon_satiety.png"), ("cola", "icon_cola.png"),
                       ("riceball", "icon_riceball.png")):
            for _d in (ASSETS_DIR, os.path.join(ROOT_DIR, "youxiang")):
                _p = os.path.join(_d, _n)
                if os.path.exists(_p):
                    try:
                        if _PIL_OK:
                            _im = Image.open(_p).convert("RGBA")
                            self._icons[_k] = (ImageTk.PhotoImage(_im), _im)
                        else:
                            self._icons[_k] = (tk.PhotoImage(file=_p), None)
                    except Exception:
                        pass
                    break

        # ---- 伙伴属性系统：金币 / 心情 / 饱食度（config 持久化）----
        self.coins = int(self.cfg.get("coins", 100))      # 金币：初始 100，无上限
        self.mood = int(self.cfg.get("mood", 50))         # 心情：初始 50，上限 100
        self.satiety = int(self.cfg.get("satiety", 50))   # 饱食度：初始 50，上限 100
        self.inventory = dict(self.cfg.get("inventory", {}) or {})  # 背包：商店购买的物品
        self._ride_coin_at = 0.0        # 骑大狗：下次赚金币时刻
        self._coin_pop_until = 0.0      # 头顶金币图案显示截止时刻
        self._coin_pop_img = None       # 头顶金币缩放缓存
        # ---- 属性消耗计时：从启动起每满 1 小时 -10 心情、-20 饱食度 ----
        self._decay_at = float(self.cfg.get("decay_at", 0) or 0)
        if self._decay_at <= time.time():
            self._decay_at = time.time() + 3600   # 启动满 1 小时后第一次扣
        # ---- 相识时间（首次启动记录，永久保存）----
        self._first_launch = float(self.cfg.get("first_launch", 0) or 0)
        if self._first_launch <= 0:
            # 防呆：即使 JSON 解析失败/键缺失，也先尝试从文件原文找回旧值，避免误重置
            _old_ts = 0.0
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as _f:
                    import re as _re
                    _m = _re.search(r'"first_launch"\s*:\s*([0-9.]+)', _f.read())
                if _m:
                    _old_ts = float(_m.group(1))
            except Exception:
                _old_ts = 0.0
            self._first_launch = _old_ts if _old_ts > 0 else time.time()
            self.cfg["first_launch"] = self._first_launch
        # ---- 今日工作计时：今天第一次启动的时刻（跨天自动重置）----
        self._today_launch = float(self.cfg.get("today_launch", 0) or 0)
        _today0 = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        if self._today_launch < _today0:
            self._today_launch = time.time()
            self.cfg["today_launch"] = self._today_launch
        # ---- 小伙伴养成（v2.1）：名字 / 好感度 / 相伴天数 / 节日问候 ----
        self._pet_name = str(self.cfg.get("pet_name", "优香") or "优香").strip() or "优香"
        self._affection = int(self.cfg.get("affection", 0) or 0)
        self._companion_days = max(0, int((time.time() - self._first_launch) // 86400))
        self.root.title("NORP Pet · %s" % self._pet_name)
        self.root.after(2600, self._greet_once)   # 启动 2.6 秒后说节日/深夜/纪念问候
        try:
            save_config(self.cfg)
        except Exception:
            pass
        # 骑大狗奔跑循环状态（run / rest 状态机）
        self._run_seq = []              # 当前奔跑循环帧序列（左右/两组随机）
        self._run_dir = "r"             # 当前奔跑方向：r=向右 / l=向左
        self._run_until = 0.0           # 本次奔跑结束（跑累）时刻
        self._run_base_x = 0            # 本次奔跑位移基准点（1/4 屏限制用）
        self._run_step_px = 6           # 奔跑每帧水平位移（像素）
        self._status_win = None         # 伙伴状态窗口引用
        self._shop_win = None           # 商店窗口引用

        self._frame_w = self._anim["idle"][0][0].width() if self._anim["idle"] else 0
        self._frame_h = self._anim["idle"][0][0].height() if self._anim["idle"] else 0
        self._frame_idx = 0
        self._last_frame = time.time()

        # ---- 人物实际边缘检测（非透明像素 bbox，画布内坐标）----
        # 素材 PNG 四周有透明留白：画布 264x332，但人物可见部分约 191x240。
        # 若按画布尺寸做边界检测/半隐藏触发会「太敏感」——人物还没贴边就
        # 被拦下。以待机帧 alpha 通道的可见 bbox 为人物边缘基准：
        #   _ccx 人物水平中心（画布内 x）   _cct 人物可见顶（画布内 y）
        #   _ccy 人物垂直中心（画布内 y）   _char_edge (l,t,r,b) 画布内坐标
        self._ccx = WIN_W // 2        # 默认 = 画布中心（无 PIL 时兜底）
        self._cct = 0                 # 默认 = 画布顶（无 PIL 时兜底）
        self._ccy = SPRITE_CY         # 默认 = 画布中心偏下（无 PIL 时兜底）
        self._char_edge = None
        try:
            _base = self._anim["idle"][0][1] if self._anim and _PIL_OK else None
            if _base is not None:
                _bb = _base.getchannel("A").point(
                    lambda v: 255 if v > 8 else 0).getbbox()   # alpha>8 视为可见
                if _bb:
                    _l, _t, _r, _b = _bb
                    _px = WIN_W // 2 - _base.width / 2   # 与 draw() 居中绘制一致
                    _py = SPRITE_CY - _base.height / 2
                    _cl, _ct = _px + _l, _py + _t
                    _cr, _cb = _px + _r, _py + _b
                    self._char_edge = (_cl, _ct, _cr, _cb)
                    self._ccx = (_cl + _cr) / 2
                    self._cct = _ct
                    self._ccy = (_ct + _cb) / 2
        except Exception:
            pass  # 拿不到 bbox 时退回画布尺寸检测（旧行为）

        # 窗口初始位置（clamp 进屏幕活动范围：顶部不超屏、底部最多露一半、
        # 左右最多半身出屏——防止 config 里存了屏幕外的旧坐标）
        if self.cfg.get("pet_x") is not None and self.cfg.get("pet_y") is not None:
            _sw = self.root.winfo_screenwidth()
            _sh = self.root.winfo_screenheight()
            # 按人物实际边缘 clamp（人物左/右边缘不越屏、人物可见顶不超屏顶）
            _cl0, _cr0 = self._char_h_edges()
            _ix = round(max(-_cl0, min(int(self.cfg['pet_x']), _sw - _cr0)))
            _iy = round(max(-self._cct, min(int(self.cfg['pet_y']), _sh - self._ccy)))
            self.root.geometry(f"{WIN_W}x{WIN_H}+{_ix}+{_iy}")
        else:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            self.root.geometry(f"{WIN_W}x{WIN_H}+{sw - WIN_W - 40}+{sh - WIN_H - 90}")
        self.root.update_idletasks()   # 立即映射窗口，避免 Tk 在 mainloop 时
                                       # 因 DPI 上下文变化重新缩放窗口尺寸

        # ---- 真 alpha 分层渲染窗口（与 Tk 窗口完全重合，鼠标点击穿透）----
        # 优香立绘边缘是 PNG 半透明抗锯齿像素，Tk 键控透明无法显示，
        # 由分层窗口做逐像素 alpha 合成，边缘像素级平滑。创建失败自动
        # 回退到 Tk canvas 绘制（旧渲染）。
        self._layered = None
        self._mouse_hook = None    # 全局低级鼠标钩子（游戏/全屏兜底交互）
        self._pil_font = None      # 缓存 PIL 气泡字体
        if _LAYERED_OK and self._has_pil:
            try:
                self._layered = create_layered(self.root.winfo_x(),
                                               self.root.winfo_y(),
                                               WIN_W, WIN_H)
                if self._layered is not None:
                    # 关键：Tk 窗口整片背景是键控透明色（点击穿透），
                    # 形象显示在分层窗口上——由分层窗口接收鼠标消息，
                    # 经事件桥转成 Tk 事件（event_generate），否则宠物
                    # 点不到、拖不动。Tk 对注入的 Win32 消息不产生 Tk
                    # 事件，必须用 Tk 官方事件生成 API。
                    self._layered.attach_bridge(self._mouse_bridge)
                    # 全局低级鼠标钩子兜底：游戏/全屏/SetCapture/独占输入
                    # 会劫持窗口鼠标消息（拖不动、右键无反应），钩子从
                    # 系统输入流直接消费光标落在宠物上的事件，不依赖
                    # 消息投递；光标在宠物外时原样放行，互不干扰。
                    self._mouse_hook = install_mouse_hook(
                        self._layered.hwnd, self._mouse_bridge)
                    self._dbg("mouse hook install: %s" % (
                        "OK" if self._mouse_hook else
                        "FAILED (WNDPROC fallback)"))
            except Exception:
                self._layered = None

        # ---- 状态 ----
        self.expr = "idle"            # idle / happy / talk / sleep / sad
        # ---- 动画形态状态机 ----
        # mode: normal(普通待机) / sport(运动形态)
        #  normal 下 anim_state: idle(00/0 轮播) / expr(互动小表情)
        #  sport  下 anim_state: play(7~14整套动作) / rest(休息一下)
        #                        idle7(第7帧原地待机) / random(随机一段运动)
        self.mode = "normal"          # 当前形态（右键菜单可切换）
        self.anim_state = "idle"      # 形态内动画状态
        self._expr_key = "wuyu"       # 当前互动小表情
        self._expr_until2 = 0.0       # 互动小表情结束时刻
        self._anim_state_until = 0.0  # 运动形态阶段（休息等）结束时刻
        self._next_frame_at = 0.0     # 运动帧定时器（播放中逐帧前进）
        self._rand_seq = []           # 随机运动帧序号序列
        self._rand_idx = 0            # 随机运动当前播放到第几帧
        self._walk_dir = "r"          # 常态走路方向：r=向右(1~3) / l=向左(4~6)
        self._walk_interval = FRAME_INTERVAL  # 本次走路方案的帧间隔（秒）
        self._walk_step_px = 18       # 本次走路方案的每步水平位移（像素）
        self._next_random_at = time.time() + random.uniform(12, 30)  # 随机走路触发时刻（常态 & 运动形态共用）
        self.breath = 1.0
        self.wobble = 0.0
        self.bounce = 0.0            # 走路/奔跑时的上下弹跳（像素），让动作更有节奏感
        self.blinking = False
        self._last_blink = time.time()
        self._blink_until = 0.0
        self._expr_until = 0.0        # 表情自动恢复时间
        self._last_line = time.time() # 随机台词计时
        self._last_move = time.time() # 随机移动计时
        self._move_target = None      # (dx, dy) 目标偏移
        self._dragging = False
        self._hidden_side = None        # 半隐藏状态：None=正常 / "l"=贴左边缘 / "r"=贴右边缘
        self._hidden_at = 0.0           # 进入半隐藏的时刻（30 分钟无聊自动出来计时）
        self._hidden_cache = None       # 半隐藏旋转帧缓存 (Tk帧, PIL帧)，进入/退出时重建
        self._click_job = None
        self._drag_polling = False   # 拖动轮询（不依赖 WM_MOUSEMOVE，消息会丢）
        self._drag_poll_job = None
        self._mouse_queue = queue.Queue()  # 分层窗口鼠标消息（WNDPROC→tick 解耦）
        self._follow_mouse = bool(self.cfg.get("follow_mouse", False))  # 跟随鼠标模式
        self._drag_frame = 0          # 拖动重绘帧计数（加速分层窗口同步）
        self._bubble = None           # 当前气泡文本
        self._bubble_until = 0.0
        self._panel = None            # 指令面板窗口引用
        self._last_topmost = 0.0      # 上次强制置顶时间（防掉层）

        # ---- NORP 本地 API 对接 ----
        self._api_online = False      # NORP 本地 API 是否在线
        self._norp_state = "none"     # none=未运行 / exe=旧版EXE在跑(无API) / exe_new=新版EXE在跑(无API) / api=API在线
        self._norp_label = ""         # 当前 NORP 的版本标签（如 "20260810 新版"）
        self._api_check_at = 0.0      # 下次探测 API 的时间
        self._api_check_interval = 5.0
        self._event_box = queue.Queue()   # 轮询线程 → UI 线程 的事件队列
        self._control_box = queue.Queue() # 控制服务器 → UI 线程 的指令队列
        self._poll_thread = None          # 事件轮询线程
        self._poll_stop = threading.Event()
        self._task_running = False    # 当前是否有任务在跑
        self._waiting_user = False    # 是否正在等待用户输入（Q: 事件）
        self._pending_reply = ""      # 累积 R: 流式回复片段，等 D: 完整答案一起展示

        # ---- 绑定事件 ----
        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Double-Button-1>", self.on_double)
        self.canvas.bind("<Button-3>", self.on_menu)

        # ---- 启动 ----
        self._start_control_server()   # 本地控制端口（供 NORP 插件指挥）
        self._start_polling()
        self._sync_layered()           # 分层窗口与 Tk 初始位置对齐
        self._enforce_topmost()        # 初始强置顶（Tk 窗口 + 分层窗口）
        self.draw()
        self.tick()
        self.root.mainloop()

    # ==================================================================
    # 动画
    # ==================================================================
    def tick(self):
        """主循环（受保护）：任何异常都记录日志并继续，保证 tick 链不断。"""
        try:
            self._tick_loop()
        except Exception as ex:
            self._dbg("tick EXC: %r" % (ex,))
        self.root.after(90, self.tick)

    def _tick_loop(self):
        now = time.time()
        # 拖动跟随已改为独立 60fps 高频循环 _drag_follow()（按下即启动），
        # 不再依赖本 tick（90ms）轮询，跟手性大幅提升。
        # 周期性强制置顶：Tk 的 -topmost 被全屏/其他置顶窗口顶掉后
        # 不会自动恢复（表现就是宠物「掉下去」），这里每隔 1.5 秒
        # 重新把 Tk 窗口 + 分层窗口挂回 HWND_TOPMOST
        if now - self._last_topmost > 1.5:
            self._last_topmost = now
            self._enforce_topmost()
        # 呼吸（待机唯一的动态：轻微缩放模拟呼吸，不再走动/晃动）
        self.breath = 1.0 + 0.045 * math.sin(now * 2.6)
        # 摇摆（走路/发呆时的轻微晃动）——已按需求取消：待机只呼吸
        self.wobble = 0.0
        # 弹跳：走路轻微起伏、奔跑颠簸更明显（expr/待机/休息时贴地不动）
        if self.anim_state == "walk":
            self.bounce = 2.5 * math.sin(now * 14.0)
        elif self.anim_state == "run":
            self.bounce = 4.5 * math.sin(now * 17.0)
        else:
            self.bounce = 0.0
        # 眨眼
        if not self.blinking and now - self._last_blink > random.uniform(2.2, 4.8):
            self._blink_until = now + 0.14
            self._last_blink = now
        self.blinking = now < self._blink_until
        # ---- 动画形态状态机 ----
        # 普通形态：idle（固定优香_0）⇄ walk（偶尔走一段：1~3 向右 / 4~6 向左）
        #           ⇄ expr（互动小表情，仅由单击互动触发，不随机冒表情）
        # 运动形态：play（7~14整套动作）→ rest（休息一下）→ idle7（第7帧
        #           原地待机）→ 偶尔 random（随机一段连续运动）→ 回 idle7
        if self.mode == "normal":
            if self.anim_state == "expr" and now >= self._expr_until2:
                self.anim_state = "idle"        # 互动表情播完回普通待机
                self._next_random_at = now + random.uniform(15, 35)
            if self.anim_state == "walk" and now >= self._next_frame_at:
                # 走路动画（1~3 向右 / 4~6 向左）播放中：逐帧前进 + 水平移动
                self._next_frame_at = now + self._walk_interval
                if self._rand_idx >= len(self._rand_seq) - 1:
                    self.anim_state = "idle"    # 播完回到优香_0 待机
                    self._next_random_at = now + random.uniform(15, 35)
                else:
                    self._rand_idx += 1
                    self._step_walk()           # 走一步（水平位移）
            if self.anim_state == "idle":
                # 待机固定优香_0：不轮播张嘴闭嘴，只做呼吸缩放
                self._frame_idx = 0
                if now >= self._next_random_at and self.expr != "sleep" and not self._hidden_side:
                    # 偶尔走一小段路（不再随机冒表情、不再拿 7~14 运动帧播）
                    self._next_random_at = now + random.uniform(15, 35)
                    self._start_random_walk()
        elif not self._hidden_side:   # sport 骑大狗形态（半隐藏时整段跳过：不奔跑不赚金币）
            # 骑大狗赚金币：每 30 秒 +1 金币，头顶弹出金币图案（睡觉时不赚）
            if now >= self._ride_coin_at and self.expr != "sleep":
                self._ride_coin_at = now + 30
                self.add_coins(1)
                self._coin_pop_until = now + 1.3
            if self.anim_state == "expr" and now >= self._expr_until2:
                # 互动表情播完 → 继续奔跑循环
                self._start_ride_run()
            elif self.anim_state == "run":
                if now >= self._next_frame_at:
                    self._next_frame_at = now + self._run_interval()
                    self._frame_idx += 1
                    self._step_ride()      # 每帧水平位移（超 1/4 屏自动转向）
                    if now >= self._run_until:
                        # 跑累了 → 「休息一下」过渡，随后重新开始奔跑循环
                        self.anim_state = "rest"
                        self._anim_state_until = now + random.uniform(3, 6)
            elif self.anim_state == "rest":
                if now >= self._anim_state_until:
                    # 休息结束 → 重新开始奔跑（随机换方向 / 换循环组）
                    self._start_ride_run()
        # 表情自动恢复
        if now > self._expr_until and self.expr in ("happy", "talk", "sad"):
            self.expr = "idle"
        # 属性消耗：从启动起每满 1 小时 -10 心情、-20 饱食度（0~100 封底）
        if now >= self._decay_at:
            self._decay_at = now + 3600
            self.cfg["decay_at"] = self._decay_at
            self.add_mood(-10)
            self.add_satiety(-20)
        # 随机台词（睡觉时不触发：不点击就一直睡，不会自己醒；
        # 骑大狗奔跑时也不触发：奔跑循环专注奔跑，不冒泡；
        # 半隐藏时不触发：安静躲墙角，不暴露自己；
        # 心情/饱食度过低（<20）时改为提醒用户关心小伙伴）
        if self.mode != "sport" and self.expr != "sleep" and not self._hidden_side \
                and now - self._last_line > random.uniform(16, 34):
            self._last_line = now
            if self.mood < 20 or self.satiety < 20:
                if self.mood < 20 and self.satiety < 20:
                    line = random.choice([
                        "sensei……我又饿又难过，快理理我嘛(｡•́︿•̀｡)",
                        "呜呜……心情和肚子都好空，sensei陪陪我好不好…",
                    ])
                elif self.mood < 20:
                    line = random.choice([
                        "sensei，我心情好低落……摸摸我的头好不好(´;ω;`)",
                        "今天有点不开心……sensei多陪陪我嘛(｡•́︿•̀｡)",
                    ])
                else:
                    line = random.choice([
                        "肚子咕咕叫了……sensei带我去买好吃的吧(´;ω;`)",
                        "好饿好饿……商店里的饭团在等着我呢！",
                    ])
                self.say(line, expr="sad")
            else:
                if random.random() < 0.5:
                    self.say(random.choice(self.cfg["lines"]), expr="talk")
                else:
                    self.say(random.choice(self._dynamic_lines()), expr="talk")
        # 跟随鼠标：开启后小人自动跟着光标走（偏移右下方，不遮挡鼠标点击）
        # 半隐藏时禁止跟随：宠物贴边躲着，不能自己跑出来
        # 自由移动关闭时也禁止：宠物被限制原地不动
        if self._follow_mouse and self.cfg.get("free_move", True) \
                and self.mode != "sport" and not self._dragging \
                and self.expr != "sleep" and not self._hidden_side:
            try:
                user32 = ctypes.windll.user32
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
                # 跟随范围同样按人物实际边缘：人物左/右边缘不越屏
                _cl0, _cr0 = self._char_h_edges()
                tx = round(max(-_cl0, min(pt.x + 90, sw - _cr0)))
                ty = round(max(-self._cct, min(pt.y + 90, sh - self._ccy)))
                self.root.geometry(f"+{tx}+{ty}")
                self._sync_layered()
            except Exception:
                pass
        # 随机走动已移除：待机时只做呼吸缩放，小人不再自己满桌乱跑
        # （运动形态第 7 帧本来就是原地待机，同样不移动）
        # 气泡消失
        if self._bubble and now > self._bubble_until:
            self._bubble = None
        # 半隐藏自动出来：自己走路撞边躲进去的（auto）待 20~40 秒就出来；
        # 用户拖进去的（drag）不主动出来——除非右键 / 拖出 / 单击 / 30 分钟无聊
        if self._hidden_side:
            _src = self._hidden_source
            _stay = (random.uniform(*HIDE_AUTO_STAY)
                     if _src == "auto" else HIDE_BORED_AFTER)
            if now - self._hidden_at >= _stay:
                self._exit_hide("auto" if _src == "auto" else "bored")
                if _src == "auto":
                    self.say("偷偷躲一会儿就好啦，出来继续溜达～", expr="happy")
                else:
                    self.say("躲墙角好无聊……还是出来找sensei玩吧！", expr="happy")

        # NORP 状态定期探测（5 秒一次）：以「最近启动的 NORP 进程」为准。
        # 在线状态由 _poll_loop 后台线程每 3 秒探测并回写 _api_online，
        # 这里只读标志——绝不在 Tk 主线程发同步 HTTP 请求（NORP 关闭时
        # 连接可能等 2 秒超时，期间全局鼠标钩子无法响应 → 整机卡顿）。
        # ★ 版本适配：新版（20260810+，norp.agent.exe）没有本地 API。
        #   若用户最新启动的是新版，即使 17777 上有旧版残留 API 在线，
        #   也不把它当作当前 NORP —— 当前 NORP 永远是最近启动的那个。
        if now >= self._api_check_at:
            self._api_check_at = now + self._api_check_interval
            online = self._api_online
            latest = self.latest_norp_proc()      # 最近启动的 NORP 进程
            if latest is not None:
                label = latest["label"]
                if latest["has_api"] is False:
                    state = "exe_new"             # 新版 EXE：无本地 API
                else:
                    state = "api" if online else "exe"
            else:
                # 没有 exe 进程：API 在线说明源码版（python 运行）在跑
                state = "api" if online else "none"
                label = ""
            if state != self._norp_state:
                self._norp_state = state
                self._api_online = online
                self._norp_label = label
                if state == "api":
                    self._start_polling()
                    self.say("已连接 NORP Agent（%s）～有啥任务尽管说！" % label
                             if label else "已连接 NORP Agent～有啥任务尽管说！",
                             expr="happy")
                elif state == "exe":
                    self._task_running = False
                    self.say("看到 NORP Agent 在运行（EXE 版）～\n但它不带本地 API，收发任务功能用不了", expr="talk")
                elif state == "exe_new":
                    self._task_running = False
                    self._stop_polling()
                    self.say("检测到 NORP Agent（%s）正在运行～\n新版不带本地 API，任务播报用不了，但我会一直陪你干活！"
                             % label, expr="happy")
                else:
                    self._task_running = False

        # 消费事件（从轮询线程来的）
        if not self._event_box.empty():
            self._consume_events()

        # 消费控制指令（来自 NORP 插件的指挥）
        if not self._control_box.empty():
            self._consume_controls()

        # 消费分层窗口鼠标消息（WNDPROC 只入队，这里在 Tk 空闲上下文处理）
        if not self._mouse_queue.empty():
            self._consume_mouse()

        self.draw()
        # self.root.after(90, self.tick)  → 移到 tick() 外层，链永不断

    def draw(self):
        c = self.canvas
        c.delete("all")
        cx, cy = WIN_W // 2, WIN_H // 2 + 8
        w = self.wobble

        if self._layered is not None:
            self._draw_layered(cx, cy, w)
            return

        # ---- 兜底：Tk canvas 绘制（分层窗口不可用时）----
        self._draw_tk(cx, cy, w)

    def _enforce_topmost(self):
        """强置顶：Tk 窗口 + 真 alpha 分层窗口都重新挂到 HWND_TOPMOST。

        Windows 上 Tk 的 -topmost 只是设置一次，被全屏窗口或其它置顶
        程序盖住后不会自动恢复（表现就是宠物「掉下去」）。这里用 Win32
        SetWindowPos 周期性重挂置顶，SWP_NOACTIVATE 保证不抢焦点、
        SWP_NOMOVE/SWP_NOSIZE 保证不挪位置不改变尺寸。
        """
        if sys.platform != "win32":
            return
        try:
            self.root.attributes("-topmost", True)
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.SetWindowPos.restype = wintypes.BOOL
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                            ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int,
                                            wintypes.UINT]
            HWND_TOPMOST = -1
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOACTIVATE = 0x0010
            # Tk 顶层窗口：winfo_id 可能是客户区子窗口，先提升到真正顶层
            hwnd = self.root.winfo_id()
            parent = user32.GetParent(hwnd)
            if parent:
                hwnd = parent
            if hwnd:
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            # 真 alpha 分层窗口（显示形象的那一个）
            layered = getattr(self, "_layered", None)
            if layered is not None and getattr(layered, "hwnd", None):
                user32.SetWindowPos(layered.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:
            pass

    def _sync_layered(self):
        """把分层窗口同步到 Tk 窗口当前位置（拖拽/走动/初始化后调用）。"""
        if self._layered is not None:
            try:
                self._layered.move(self.root.winfo_x(), self.root.winfo_y())
            except Exception:
                pass

    # ==================================================================
    # 活动范围限制 + 侧边半隐藏
    # ==================================================================
    def _char_h_edges(self):
        """人物可见部分左/右边缘在画布内的 x 坐标（无 bbox 时兜底=画布边缘）。"""
        if self._char_edge:
            return self._char_edge[0], self._char_edge[2]
        return 0.0, float(WIN_W)

    def _char_limits(self):
        """基于「绘制的人物实际边缘」计算窗口活动范围（而非画布尺寸）。

        素材 PNG 四周有透明留白（画布 264x332，人物可见部分约 191x240），
        若按画布检测会过早触发限制/半隐藏（人物还没贴边就被拦下）。
        用待机帧 alpha bbox 得出人物边缘在画布内的坐标：
        · 水平：人物左边缘不越屏左、右边缘不越屏右 → 贴边即可进墙
        · 顶部：人物可见顶 cct 不高于屏顶 → 窗口允许再上探 cct 像素
        · 底部：人物中心 ccy 不低过屏幕底 → 最多下半身探出屏外"""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        cl, cr = self._char_h_edges()
        return (round(-cl), round(sw - cr), round(-self._cct), round(sh - self._ccy))

    def _clamp_pos(self, x, y):
        """把窗口坐标 clamp 进屏幕活动范围（人物边缘检测版，取整防误差）。"""
        x0, x1, y0, y1 = self._char_limits()
        return (round(max(x0, min(x, x1))),
                round(max(y0, min(y, y1))))

    def _build_hidden_frames(self):
        """构建半隐藏旋转帧：把待机立绘旋转 45°，头部朝屏幕内侧。

        PIL 正角度 = 逆时针旋转：
          · 右边缘半隐藏 → 逆时针 45°（头部朝左上方，探进屏幕内）
          · 左边缘半隐藏 → 顺时针 45°（头部朝右上方，探进屏幕内）
        缓存 (Tk帧, PIL帧) 两份，无 PIL 时跳过（Tk 兜底不旋转，仅贴边）。"""
        self._hidden_cache = None
        try:
            base = self._anim["idle"][0]
            if base is None or base[1] is None:
                return
            ang = HIDE_ROTATE if self._hidden_side == "r" else -HIDE_ROTATE
            img = base[1].rotate(ang, expand=True,
                                 resample=getattr(Image, "Resampling", Image).BICUBIC)
            self._hidden_cache = (ImageTk.PhotoImage(img), img)
        except Exception:
            self._hidden_cache = None

    def _enter_hide(self, side, source="auto"):
        """进入半隐藏：贴到屏幕左/右边缘，形象旋转 45° 只露头，不再主动走动。

        side: "l"=贴左边缘（头朝右探出）/ "r"=贴右边缘（头朝左探出）
        位置：保持宠物当前窗口位置不动（触发时必然已贴边，左隐藏 x 在
        [-WIN_W/2, 0] 内、右隐藏 x 在 [sw-WIN_W, sw-WIN_W/2] 内），只 clamp
        垂直坐标——松手/撞边瞬间不再把窗口横移到墙边，避免「被吸进墙里」
        的跳变感；旋转帧由 _draw_layered/_draw_tk 对齐屏幕边缘保证半藏半露。"""
        if not self.cfg.get("wall_hide", True):
            return                     # 贴墙开关已关闭：不进半隐藏，只停在边缘
        if self._hidden_side == side:
            return
        self._hidden_side = side
        self._hidden_at = time.time()
        self._hidden_source = source
        sh = self.root.winfo_screenheight()
        nx = self.root.winfo_x()          # 保持原位：不瞬移、不横跳
        ny = round(max(-self._cct, min(self.root.winfo_y(), sh - self._ccy)))
        self.root.geometry(f"+{nx}+{ny}")
        # 半隐藏期间不奔跑不走路不说话：回到普通待机形态
        self.mode = "normal"
        self.anim_state = "idle"
        self.expr = "idle"
        self._build_hidden_frames()
        self._sync_layered()
        self.draw()

    def _exit_hide(self, reason="drag"):
        """退出半隐藏：恢复正常行走 / 互动 / 跟随鼠标。

        reason: drag=拖出 / menu=右键 / click=单击 / auto=自己躲进墙待够时间出来
               / bored=30分钟无聊自己出来"""
        if not self._hidden_side:
            return
        self._hidden_side = None
        self._hidden_at = 0.0
        self._hidden_source = None
        self._hidden_cache = None
        self._next_random_at = time.time() + random.uniform(15, 35)
        self.draw()

    def _current_frame(self):
        """返回当前应显示的 (Tk帧, PIL帧)，由形态 + 动画状态机共同决定。

        普通形态：待机固定优香_0；偶尔走一段（1~3 向右 / 4~6 向左）；
                  单击互动时显示小表情。
        运动形态：奔跑循环帧（右跑 7~12 / 12,13,14,7，左跑为翻转帧）；
                  跑累了休息时显示「休息一下」。
        """
        a = self._anim
        # 拖拽动作帧（最高优先级）：拖动宠物时显示「优香_15t拖拽」
        if self._dragging and a["action"].get("drag"):
            return a["action"]["drag"]
        # 睡觉动作帧：右键「睡觉」后一直显示「优香_16_sleep」，直到唤醒
        if self.expr == "sleep" and a["action"].get("sleep"):
            return a["action"]["sleep"]
        if self.anim_state == "expr" and self._expr_key in a["expr"]:
            return a["expr"][self._expr_key]   # 互动小表情优先（两种形态通用）
        if self.mode == "normal":
            if self.anim_state == "walk":
                # 走路动画：按 _walk_dir 选右走(1~3)或左走(4~6)套组
                _frames = a["walk_r"] if self._walk_dir == "r" else a["walk_l"]
                if _frames and 0 <= self._rand_idx < len(self._rand_seq):
                    return _frames[self._rand_seq[self._rand_idx]]
            if a["idle"]:
                return a["idle"][self._frame_idx % len(a["idle"])]  # 固定优香_0 待机
            return None
        # sport 运动形态
        if self.anim_state == "run" and self._run_seq:
            return self._run_seq[self._frame_idx % len(self._run_seq)]  # 奔跑循环帧
        if self.anim_state == "rest" and a["rest"]:
            return a["rest"]          # 跑累了 → 「休息一下」
        if a["sport"]:
            return a["sport"][0]      # 兜底 = 优香_7
        return None

    def _draw_layered(self, cx, cy, wobble):
        """真 alpha 分层渲染：形象 + 气泡在 PIL 画布合成后一次性贴到分层窗口。
        坐标系与旧 Tk 绘制完全一致，半透明边缘像素被 Windows 逐像素混合，
        边缘平滑无锯齿、无黑线、无紫边。"""
        try:
            canvas_img = Image.new("RGBA", (WIN_W, WIN_H), (0, 0, 0, 0))
            # ---- 半隐藏：旋转45°的形象，头朝屏幕内侧，另外半边身子藏屏幕外 ----
            if self._hidden_side and self._hidden_cache:
                _tk, _img = self._hidden_cache
                # 图片中心对齐「屏幕边缘」而非窗口边缘：进入半隐藏时窗口
                # 保持原位（可能停在半出屏位置），只有按屏幕边缘对齐才能
                # 保证旋转帧正好一半藏在屏幕外、头部探进屏幕内
                _edge = 0 if self._hidden_side == "l" else self.root.winfo_screenwidth()
                _hx = _edge - self.root.winfo_x()
                canvas_img.alpha_composite(_img, (int(_hx - _img.width / 2),
                                                  int(cy - _img.height / 2)))
                # 半隐藏不主动说话/不弹金币：气泡若残留也一并隐藏
                self._layered.blit(_premultiply_alpha(canvas_img),
                                   self.root.winfo_x(), self.root.winfo_y())
                return
            # ---- 优香形象（呼吸缩放 + 形态状态机帧 + 摇摆）----
            frame = self._current_frame()
            cur_h = self._frame_h        # 当前帧实际显示高度（气泡定位用，随放大同步）
            if frame is not None and frame[1] is not None:
                img = frame[1]
                s = self.breath
                if self.expr == "sleep":
                    s *= SLEEP_SCALE     # 睡觉动作放大：比待机大 1.2 倍
                if abs(s - 1.0) > 0.001:        # 呼吸：轻微缩放（1 ± 4.5%）
                    nw = max(1, int(img.width * s))
                    nh = max(1, int(img.height * s))
                    img = img.resize((nw, nh),
                                     getattr(Image, "Resampling", Image).LANCZOS)
                    cur_h = nh
                px = int(cx + wobble * 0.6 - img.width / 2)
                py = int(cy - img.height / 2 - self.bounce)   # 弹跳：走路/奔跑时上下起伏
                canvas_img.alpha_composite(img, (px, py))
            # ---- 气泡（跟随形象弹跳，保持贴头）----
            if self._bubble:
                self._draw_bubble_pil(canvas_img, cx, cy - self.bounce, cur_h)
            # ---- 头顶金币图案（骑大狗赚金币时弹出，缓缓上浮）----
            if self._coin_pop_until > time.time():
                self._draw_coin_pop_pil(canvas_img, cx, cur_h - self.bounce)
            # 分层窗口要求预乘 alpha 像素：未预乘时半透明边缘像素会以
            # 原始亮色参与混合 → 白色噪点边缘。先预乘再交给 Windows。
            self._layered.blit(_premultiply_alpha(canvas_img),
                               self.root.winfo_x(), self.root.winfo_y())
        except Exception:
            # 分层渲染失败：关闭分层窗口，回退 Tk 绘制（不打断宠物运行）
            try:
                if self._layered is not None:
                    self._layered.close()
            except Exception:
                pass
            self._layered = None
            # 分层窗口已销毁，全局钩子一并卸载（hwnd 失效后钩子无意义）
            if self._mouse_hook is not None:
                try:
                    self._mouse_hook.close()
                except Exception:
                    pass
                self._mouse_hook = None
            self._draw_tk(cx, cy, wobble)

    def _bubble_font(self):
        """PIL 气泡字体（微软雅黑，加载失败回退默认字体）。"""
        if self._pil_font is None:
            for name in ("msyh.ttc", "msyh.ttf", "segoeui.ttf", "arial.ttf"):
                try:
                    self._pil_font = ImageFont.truetype(name, 16)  # 气泡回复字号 16（大一号）
                    break
                except Exception:
                    continue
            if self._pil_font is None:
                self._pil_font = ImageFont.load_default()
        return self._pil_font

    def _wrap_pil(self, text, max_px, font):
        """按像素宽度换行（PIL 字体测量），中英文混排不挤爆。"""
        res = []
        for para in text.split("\n"):
            cur = ""
            for ch in para:
                if cur and font.getlength(cur + ch) > max_px:
                    res.append(cur)
                    cur = ch
                else:
                    cur += ch
            if cur:
                res.append(cur)
        return res or [""]

    def _draw_bubble_pil(self, canvas_img, cx, cy, fh=None):
        """在 PIL 画布上画白色圆角气泡 + 三角箭头 + 文字（真 alpha）。"""
        text = self._bubble
        font = self._bubble_font()
        lines = self._wrap_pil(text, 176, font)
        line_h = font.size + 8   # 行距 = 气泡字号 + 8px，16 号字行不粘连
        bw = max(int(font.getlength(x)) for x in lines) + 28
        bh = len(lines) * line_h + 24
        bx = cx - bw // 2
        fh = fh or self._frame_h
        by = cy - fh // 2 - 16   # 图片顶部上方 16px（整体抬高，不再压着头）
        if by < 4:
            by = 4
        bx = max(6, min(bx, WIN_W - bw - 6))
        d = ImageDraw.Draw(canvas_img)
        d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=12,
                            fill=(255, 255, 255, 255),
                            outline=(201, 214, 224, 255), width=2)
        d.polygon([(bx + 24, by + bh - 1), (bx + 40, by + bh + 12),
                   (bx + 52, by + bh - 1)], fill=(255, 255, 255, 255))
        for i, ln in enumerate(lines):
            d.text((bx + bw / 2, by + 28 + i * line_h), ln,
                   font=font, fill=(61, 61, 61, 255), anchor="mm")

    def _draw_coin_pop_pil(self, canvas_img, cx, cy, fh=None):
        """头顶弹出金币图案：骑大狗赚到金币时在头顶显示一枚金币，
        起始位置更高，缓缓上浮并渐渐淡出消失。"""
        ico = self._icons.get("coin")
        if ico is None or ico[1] is None:
            return
        img = self._coin_pop_img
        if img is None:
            img = ico[1].resize((46, 46),
                                getattr(Image, "Resampling", Image).LANCZOS)
            self._coin_pop_img = img
        fh = fh or self._frame_h
        remaining = self._coin_pop_until - time.time()
        if remaining <= 0:
            return
        # 上浮动画：进度 0→1，金币从头顶上方 52px 处开始，
        # 随时间向上飘 46px（真正往上飘，不再往下掉），最后渐渐淡出
        total = 1.3
        progress = max(0.0, min(1.0, 1.0 - remaining / total))
        rise = int(progress * 46)
        px = int(cx - img.width / 2)
        py = int(cy - fh // 2 - 52 - rise)
        if py < 2:
            py = 2
        # 渐隐：前 40% 时长保持全亮，后 60% 时长随上浮线性淡出（保留 PNG 自身形状）
        fade_start = 1.3 * 0.4
        if remaining < fade_start:
            ratio = max(0.0, remaining / fade_start)
            fade_img = img.copy()
            alpha = fade_img.getchannel("A").point(lambda v: int(v * ratio))
            fade_img.putalpha(alpha)
            img = fade_img
        canvas_img.alpha_composite(img, (px, py))

    def _draw_tk(self, cx, cy, w):
        """旧渲染路径（兜底）：Tk canvas 二值键控绘制。"""
        c = self.canvas
        # ---- 优香形象（形态状态机帧）----
        frame = self._current_frame()
        fh = self._frame_h
        if self._hidden_side and self._hidden_cache:
            # 半隐藏：旋转45°帧，图片中心对齐屏幕边缘（与分层路径一致）
            tk_img = self._hidden_cache[0]
            fh = tk_img.height()
            _edge = 0 if self._hidden_side == "l" else self.root.winfo_screenwidth()
            hx = _edge - self.root.winfo_x()
            c.create_image(hx, cy, image=tk_img)
        elif frame is not None:
            img = frame[0]
            fh = img.height()
            c.create_image(cx + int(w * 0.6), cy - int(self.bounce), image=img)  # 弹跳：上下起伏
        else:
            # 素材缺失时的兜底形象（小圆球，避免空白窗口）
            c.create_oval(cx - 40, cy - 40, cx + 40, cy + 40,
                          fill="#7ec8e3", outline="#4a8bb0", width=3)

        # ---- 气泡 ----
        if self._bubble:
            text = self._bubble
            lines = self._wrap(text, 176)                 # 按像素宽度换行，正确处理 \n 与中英文混排
            line_h = tkfont.Font(font=BUBBLE_FONT).metrics("linespace") + 5  # 行距 = 气泡字体行高 + 5px，行与行绝不粘连
            bw = max(self._font.measure(x) for x in lines) + 28
            bh = len(lines) * line_h + 24
            bx = cx - bw // 2
            by = cy - int(self.bounce) - fh // 2 - 16  # 图片顶部上方 16px（整体抬高，不再压着头）
            if by < 4:
                by = 4
            bx = max(6, min(bx, WIN_W - bw - 6))
            c.create_rectangle(bx, by, bx + bw, by + bh, fill="#ffffff",
                               outline="#c9d6e0", width=2)
            c.create_polygon(bx + 24, by + bh - 1, bx + 40, by + bh + 12,
                             bx + 52, by + bh - 1, fill="#ffffff", outline="")
            for i, ln in enumerate(lines):
                c.create_text(bx + bw // 2, by + 28 + i * line_h, text=ln,
                              font=BUBBLE_FONT, fill="#3d3d3d")

        # ---- 头顶金币图案（骑大狗赚金币时弹出，缓缓上浮并渐渐消失）----
        if self._coin_pop_until > time.time() and self._icons.get("coin") is not None:
            ico = self._icons["coin"][0]
            remaining = self._coin_pop_until - time.time()
            total = 1.3
            progress = max(0.0, min(1.0, 1.0 - remaining / total))
            rise = int(progress * 46)
            py = cy - int(self.bounce) - fh // 2 - 52 - rise
            if py < 2:
                py = 2
            # Tk 不支持半透明：最后 30% 时长直接消失，近似淡出效果
            if remaining >= 1.3 * 0.3:
                c.create_image(cx, py, image=ico)

    def _wrap(self, text, max_px):
        """按像素宽度换行：先按 \n 拆段，再逐字测量拼接，
        保证每行不超出 max_px 像素（中英文混排也不会挤爆）。"""
        res = []
        for para in text.split("\n"):
            cur = ""
            for ch in para:
                if cur and self._font.measure(cur + ch) > max_px:
                    res.append(cur)
                    cur = ch
                else:
                    cur += ch
            if cur:
                res.append(cur)
        return res or [""]

    # ==================================================================
    # 交互
    # ==================================================================
    def _hook_pause(self):
        """暂停全局低级鼠标钩子（阻塞模态弹窗期间必须调用）。

        tk_popup / simpledialog 等模态循环运行期间，光标往往落在宠物
        身上（菜单贴着宠物弹出、宠物就在对话框下方），此时钩子会把
        「点击模态窗口外部→取消/关闭模态」的消息全部吞掉（return 1），
        模态循环永远结束不了 → 整个 Tk 卡死（菜单取消不掉、点宠物
        无反应、拖不动、动画冻结）。模态开始前卸载钩子即可根治；
        模态结束后由 _hook_resume 重装，游戏/全屏兜底交互不受影响。
        """
        if self._mouse_hook is not None:
            try:
                self._mouse_hook.close()
            except Exception:
                pass
            self._mouse_hook = None

    def _hook_resume(self):
        """模态结束后重装全局低级鼠标钩子（游戏/全屏兜底交互）。

        安装失败自动重试（Windows 全局钩子有瞬时资源限制，重装可能
        偶发失败——失败后钩子丢失会表现为游戏/全屏里拖不动），
        最多重试 5 次，并记录日志便于排查。"""
        if self._layered is not None and self._mouse_hook is None:
            try:
                self._mouse_hook = install_mouse_hook(
                    self._layered.hwnd, self._mouse_bridge)
                if self._mouse_hook is None:
                    self._hook_retry = getattr(self, "_hook_retry", 0) + 1
                    if self._hook_retry <= 5:
                        self._dbg("hook resume FAILED (%d/5), retry in 800ms"
                                  % self._hook_retry)
                        self.root.after(800, self._hook_resume)
                    else:
                        self._dbg("hook resume give up after 5 tries")
                        self._hook_retry = 0
                else:
                    self._hook_retry = 0
                    self._dbg("hook resume OK")
            except Exception:
                self._mouse_hook = None
                self._hook_retry = getattr(self, "_hook_retry", 0) + 1
                if self._hook_retry <= 5:
                    self.root.after(800, self._hook_resume)

    def _drain_mouse_queue(self):
        """丢弃模态期间积压的鼠标消息（WNDPROC 仍会入队，避免模态
        关闭后触发一次意外的点击/拖拽）。"""
        while True:
            try:
                self._mouse_queue.get_nowait()
            except Exception:
                break

    def _mouse_bridge(self, msg, wparam, lparam):
        """分层窗口鼠标消息 → 队列（WNDPROC 回调内只入队，绝不调用 Tk！

        之前在 WNDPROC（DispatchMessage 上下文）里直接 event_generate，
        同步触发绑定回调再调 Tk API，会造成 Tk 重入、消息循环卡死。
        现在只做 queue.put（线程安全、微秒级返回），由 tick() 在
        空闲上下文消费并生成 Tk 事件，零重入风险。
        """
        try:
            self._mouse_queue.put((msg, wparam, lparam))
        except Exception:
            pass

    def _consume_mouse(self):
        """在 tick（Tk 空闲上下文）里消费鼠标消息队列并生成 Tk 事件。"""
        while True:
            try:
                msg, wparam, lparam = self._mouse_queue.get_nowait()
            except Exception:
                break
            try:
                x = lparam & 0xFFFF
                y = (lparam >> 16) & 0xFFFF
                # 屏幕坐标统一用 GetCursorPos（物理坐标，与 tick 拖动轮询
                # 一致；winfo_rootx + lParam 在 DPI 下会与物理坐标错位）
                user32 = ctypes.windll.user32
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                sx, sy = pt.x, pt.y
                c = self.canvas
                modal = self._grab_active()
                if msg == 0x0201:      # WM_LBUTTONDOWN
                    self._drag_polling = True
                    # lParam 是「按下时刻」的客户区坐标 → 换算成按下时刻的
                    # 屏幕坐标（GetCursorPos 在 tick 消费时已滞后，快速拖动
                    # 会丢失按下~启动间的位移，表现为小人掉队）。
                    # 进程 Per-Monitor V2 aware：winfo_rootx 与 lParam 均为物理像素。
                    rx = self.root.winfo_rootx() + x
                    ry = self.root.winfo_rooty() + y
                    if modal:
                        # 模态弹窗（simpledialog/messagebox 等）grab 期间，
                        # Tk 会把 event_generate 的鼠标事件重定向到弹窗，
                        # 宠物点不到、拖不动 → 绕过 Tk 事件系统直接拖拽
                        # （60fps 轮询跟手，不再依赖注入的 Tk 事件）。
                        self._press_direct(rx, ry)
                    else:
                        c.event_generate("<Button-1>", x=x, y=y, rootx=rx, rooty=ry)
                elif msg == 0x0203:    # WM_LBUTTONDBLCLK
                    if not modal:
                        c.event_generate("<Double-Button-1>", x=x, y=y,
                                         rootx=sx, rooty=sy)
                elif msg == 0x0202:    # WM_LBUTTONUP
                    self._drag_polling = False
                    if modal:
                        if self._dragging:
                            self.on_release(None)
                    else:
                        c.event_generate("<ButtonRelease-1>", x=x, y=y,
                                         rootx=sx, rooty=sy)
                elif msg == 0x0204:    # WM_RBUTTONDOWN
                    if not modal:
                        c.event_generate("<Button-3>", x=x, y=y, rootx=sx, rooty=sy)
            except Exception:
                pass

    def _grab_active(self):
        """是否有模态弹窗 grab 在劫持 Tk 鼠标事件（simpledialog/messagebox 等）。

        这类弹窗打开时用 Tk grab 锁定输入：event_generate 注入的鼠标
        事件会被 Tk 重定向到弹窗窗口 → 主窗口收不到 Button 事件，
        表现为「弹窗打开后小人拖不动」。关闭弹窗 grab 释放后自动恢复。
        """
        try:
            g = self.root.grab_current()
            return g is not None and g != self.root and g.winfo_exists()
        except Exception:
            return False

    def _press_direct(self, rx, ry):
        """模态 grab 期间绕过 Tk 事件系统直接启动拖拽（复用 60fps 轮询）。"""
        self._dragging = True
        self._press_xy = (rx, ry)
        self._win_xy = (self.root.winfo_x(), self.root.winfo_y())
        if self._click_job:
            self.root.after_cancel(self._click_job)
            self._click_job = None   # 模态期间不触发单击互动（避免抢弹窗焦点）
        self._drag_follow()

    # ==================================================================
    # 伙伴属性系统：金币 / 心情 / 饱食度
    # ==================================================================
    def _save_stats(self):
        """把当前属性写回 cfg 并持久化到 config.json。"""
        self.cfg["coins"] = self.coins
        self.cfg["mood"] = self.mood
        self.cfg["satiety"] = self.satiety
        self.cfg["inventory"] = self.inventory
        try:
            save_config(self.cfg)
        except Exception:
            pass

    def add_item(self, key, n=1):
        """物品进背包（商店购买所得）。"""
        self.inventory[key] = int(self.inventory.get(key, 0) or 0) + n
        self._save_stats()

    def use_item(self, key):
        """从背包消耗 1 个物品，背包里没有则返回 False。"""
        if int(self.inventory.get(key, 0) or 0) <= 0:
            return False
        self.inventory[key] = int(self.inventory[key]) - 1
        if self.inventory[key] <= 0:
            self.inventory.pop(key, None)
        self._save_stats()
        return True

    def add_coins(self, n):
        """加金币（无上限）。"""
        self.coins += n
        self._save_stats()

    def spend_coins(self, n):
        """扣金币，余额不足返回 False。"""
        if self.coins < n:
            return False
        self.coins -= n
        self._save_stats()
        return True

    def add_mood(self, n):
        """加心情（0~100 封顶）。"""
        self.mood = max(0, min(100, self.mood + n))
        self._save_stats()

    def add_satiety(self, n):
        """加饱食度（0~100 封顶）。"""
        self.satiety = max(0, min(100, self.satiety + n))
        self._save_stats()

    def say(self, text, expr=None, duration=2.6):
        self._bubble = text
        self._bubble_until = time.time() + duration
        if expr:
            self.expr = expr
            self._expr_until = time.time() + duration

    def on_press(self, e):
        self._dbg("on_press: x=%d y=%d win=(%d,%d)" % (
            e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y()))
        self._dragging = True
        # 按下即退出半隐藏：用户来拖它了（拖出是退出方式之一）
        self._exit_hide("drag")
        # e.x_root 由 _consume_mouse 用 lParam（按下时刻客户区坐标）+ 窗口
        # 位置换算，是精确的「按下时刻」屏幕坐标；快速拖动也零位移丢失。
        # （Tk 原生 Button-1 触发的 e.x_root 同样是按下时刻坐标）
        self._press_xy = (e.x_root, e.y_root)
        self._win_xy = (self.root.winfo_x(), self.root.winfo_y())
        # 单击延迟判定（区分双击）
        if self._click_job:
            self.root.after_cancel(self._click_job)
        self._click_job = self.root.after(260, self.single_click)
        # 按下即跟手：启动 60fps 高频拖动循环（系统 WM_MOUSEMOVE 会合并丢失）
        self._drag_follow()

    def _drag_follow(self):
        """60fps 高频拖动跟随：轮询光标位置直接驱动窗口位置，
        按下即跟手，不依赖系统 WM_MOUSEMOVE（消息会被合并/丢失）。"""
        if not self._dragging:
            return
        # 模态期间 _press_direct 直接进入的拖拽也先退出半隐藏（双保险）
        if self._hidden_side:
            self._exit_hide("drag")
        try:
            user32 = ctypes.windll.user32
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            # ---- 释放判定（双保险，修复「点完右键菜单后拖不动」）----
            # 全局低级钩子拦截（return 1 消费）左键按下后，GetAsyncKeyState
            # 读不到「按下」状态（输入流被钩子吞掉）→ 老代码按下瞬间就判定
            # 为已松开 → 拖拽立即结束 = 拖不动。钩子侧已内置 lbutton_down
            # 物理状态跟踪（回调开头无条件更新，与拦截与否无关），
            # 以它为准；钩子缺失/未安装时退回 GetAsyncKeyState。
            _hook = self._mouse_hook
            if _hook is not None and _hook.lbutton_down:
                _released = False       # 钩子确认左键仍按下 → 继续拖
            else:
                _ks = user32.GetAsyncKeyState(0x01)
                _released = not (_ks & 0x8000)
            if _released:
                _ks = user32.GetAsyncKeyState(0x01)
                self._dbg("drag_follow RELEASE: key=0x%X hook_down=%s pt=(%d,%d)" % (
                    _ks, (_hook.lbutton_down if _hook is not None else None),
                    pt.x, pt.y))
                self.on_release(None)
                return
            dx = pt.x - self._press_xy[0]
            dy = pt.y - self._press_xy[1]
            # clamp 进屏幕活动范围：垂直最多露一半在屏底、水平最多半身出屏
            nx, ny = self._clamp_pos(self._win_xy[0] + dx, self._win_xy[1] + dy)
            self.root.geometry(f"+{nx}+{ny}")
            self._sync_layered()
            # 拖动期间加速重绘（~30fps）：分层窗口（肉眼看到的形象）位置
            # 只在 draw()->blit() 里同步，平时 90ms 一次会显得不跟手
            self._drag_frame += 1
            if self._drag_frame % 2 == 0:
                self.draw()
            if self._click_job:
                self.root.after_cancel(self._click_job)
                self._click_job = None
        except Exception:
            pass
        self.root.after(16, self._drag_follow)

    def on_drag(self, e):
        if self._press_xy:
            dx = e.x_root - self._press_xy[0]
            dy = e.y_root - self._press_xy[1]
            nx, ny = self._clamp_pos(self._win_xy[0] + dx, self._win_xy[1] + dy)
            self.root.geometry(f"+{nx}+{ny}")
            self._sync_layered()
            if self._click_job:
                self.root.after_cancel(self._click_job)
                self._click_job = None

    def on_release(self, e):
        self._dbg("on_release")
        self._dragging = False
        # 松手时若人物实际边缘已贴上屏幕左/右边缘 → 进入半隐藏：旋转45°露头，
        # 半边身子藏屏外（检测「绘制的人物」边缘而非画布——画布有透明留白，
        # 按画布检测人物还没贴边就会提前触发，太敏感；人物边缘一碰屏边就进墙，
        # 不用拖到一半身体出屏；2px 容差吸收坐标取整误差，保证必定触发）
        _cl0, _cr0 = self._char_h_edges()
        xl = self.root.winfo_x() + _cl0
        xr = self.root.winfo_x() + _cr0
        sw = self.root.winfo_screenwidth()
        if xl <= 2:
            self._enter_hide("l", source="drag")
        elif xr >= sw - 2:
            self._enter_hide("r", source="drag")
        # 保存位置
        self.cfg["pet_x"] = self.root.winfo_x()
        self.cfg["pet_y"] = self.root.winfo_y()
        save_config(self.cfg)

    @staticmethod
    def _dbg(msg):
        """调试日志（排障用）。默认关闭：磁盘 IO 触发杀软实时扫描，
        Tk 主线程/鼠标事件路径上高频调用会造成整机卡顿。
        排障时把模块级 _DBG_FILE_LOG 改为 True 即可打开（自带节流）。"""
        if not _DBG_FILE_LOG:
            return
        try:
            now = time.time()
            if now - _dbg_last_ts[0] < 0.25:   # 节流：最多每秒 4 条
                return
            _dbg_last_ts[0] = now
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "pet_events.log"), "a",
                      encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    def single_click(self):
        if self._hidden_side:
            # 单击半隐藏中的宠物 = 被发现了，出来（用户没拖没右键，轻轻一点也算）
            self._exit_hide("click")
            self.say("被sensei发现啦！嘿嘿～", expr="happy")
            return
        if self.expr == "sleep":
            # 点击睡觉中的宠物 = 叫醒（不点就一直睡，不会自动醒）
            self.say("唔……sensei早上好！", expr="happy")
            return
        # 单击 = 随机互动：随机台词 / 摸头撒娇 / 优香_00 微笑立绘 / 打招呼 / 被戳无语，
        # 每个动作都有配套表情帧与台词，动作与回话严格匹配；
        # 吃东西只保留在右键菜单「🍰 喂食」里
        act = random.choice(["line", "touch", "show", "hi", "wuyu"])
        self.add_affection(1, save=False)   # 每次互动 +1 好感（攒一批再落盘）
        if act == "line":
            self.say(random.choice(self.cfg["lines"]), expr="talk")
            self._play_expr("zhengjing")          # 优香_5_zhengjing 惊讶脸
        elif act == "touch":
            self.say(random.choice([
                "嘿嘿~~！！，sensei不要捉弄我了，快去工作啦",
                "嘿嘿~！被sensei摸摸头好舒服，再摸一下嘛~",
                "啊……sensei的手好暖和，但我还要去工作啦！",
            ]), expr="happy")
            self._play_expr("touch")              # 优香_19momotou 摸头动作帧
        elif act == "show":
            self.say(random.choice([
                "嗯！我一直都在sensei身边哦～",
                "嘿嘿，被sensei发现我在摸鱼了……才没有！",
                "今天也元气满满，sensei也要加油哦(≧▽≦)",
            ]), expr="happy")
            self._play_expr("show")               # 优香_00 微笑立绘
        elif act == "hi":
            self.say(random.choice([
                "嗨～sensei！今天也要好好工作哦！",
                "哟！sensei来啦～有我在，任务肯定没问题的！",
            ]), expr="happy")
            self._play_expr("hi")                 # 优香_18_hi 打招呼动作帧
        else:
            self.say(random.choice([
                "sensei……再戳就要生气了哦(￣^￣)",
                "戳戳戳，sensei今天怎么这么调皮呀～",
            ]), expr="talk")
            self._play_expr("wuyu")               # 优香_6_wuyu 无语脸

    def _start_random_walk(self):
        """常态偶尔走一段：随机选向右走(1~3)或向左走(4~6)。

        走路不是「播完一套就停」，而是把中间帧循环播放，形成持续的走路姿态：
          · 向右走循环：优香_2 ↔ 优香_3
          · 向左走循环：优香_4 ↔ 优香_5（含张嘴差分，若加载到则一并循环）
        每次随机选一种走路方案（小碎步 / 普通走 / 大步慢走），
        循环组数、步长、帧速都不同 → 时长时短、节奏不一，更随机。
        走完自动回优香_0 待机。不播 7~14 运动帧、不随机冒表情。"""
        if not self.cfg.get("idle_move", True):
            return                     # 空闲走动开关已关闭：不启动走路
        wl, wr = self._anim["walk_l"], self._anim["walk_r"]
        pool = ([("l", wl)] if wl else []) + ([("r", wr)] if wr else [])
        if not pool:
            return
        d, frames = random.choice(pool)
        # 多套走路逻辑：小碎步（快而短）/ 普通走 / 大步慢走（慢而长），随机抽一套
        plan = random.choice([
            {"interval": 0.20, "step": 14, "loops": (1, 3)},   # 小碎步：快、轻、短
            {"interval": 0.30, "step": 18, "loops": (3, 6)},   # 普通走：中速、中等距离
            {"interval": 0.40, "step": 26, "loops": (4, 8)},   # 大步慢走：慢、重、远
        ])
        loops = random.randint(*plan["loops"])
        self._walk_dir = d
        self._walk_interval = plan["interval"]
        self._walk_step_px = plan["step"]
        # 循环段（中间帧）：
        #   向右走 [1,2] = 优香_2 ↔ 优香_3
        #   向左走 [0,1] = 优香_4 ↔ 优香_5；若插入了张嘴差分（len==4）则 [0,1,2] 一起循环
        if d == "r":
            loop = [1, 2] if len(frames) >= 3 else list(range(len(frames)))
        elif len(frames) >= 4:      # 4, 5, 张嘴5, 6 → 循环 4→5→张嘴5
            loop = [0, 1, 2]
        else:                       # 4, 5, 6 → 循环 4→5
            loop = [0, 1]
        # 帧序列 = 起步帧 → 循环段×N → 收尾帧（最后一步落地），一直在走路状态
        seq = [0] + loop * loops + [len(frames) - 1]
        self._rand_seq = seq
        self._rand_idx = 0
        self._next_frame_at = time.time() + self._walk_interval
        self.anim_state = "walk"
        self._step_walk()

    def _step_walk(self):
        """走路帧水平位移：向右走往右移、向左走往左移；撞到屏幕边缘进入半隐藏。"""
        if not self._walk_dir:
            return
        if self._dragging:
            return   # 拖动中冻结自动位移：拖拽与走路互不抢位置
        if not self.cfg.get("free_move", True):
            # 自由移动已关闭：只原地播放走路动作，不产生物理位移（防止到处乱跑）
            return
        step = self._walk_step_px if self._walk_dir == "r" else -self._walk_step_px
        try:
            sw = self.root.winfo_screenwidth()
            x = self.root.winfo_x() + step
            _cl0, _cr0 = self._char_h_edges()
            if x + _cl0 < 0 or x + _cr0 > sw:   # 人物实际边缘贴到屏幕边才撞
                self.anim_state = "idle"        # 到边了：不再硬走
                self._next_random_at = time.time() + random.uniform(15, 35)
                # 走到屏幕左右边缘 → 顺势躲进半隐藏状态（贴边旋转45°露头）
                # 自动贴墙开关关闭时：只停在边缘，不进半隐藏
                if self.cfg.get("auto_wall_hide", True) and self.cfg.get("wall_hide", True):
                    self._enter_hide("l" if x + _cl0 < 0 else "r")
                return
            self.root.geometry(f"+{x}+{self.root.winfo_y()}")
            self._sync_layered()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 骑大狗奔跑循环（sport 模式）
    # ------------------------------------------------------------------
    def _run_interval(self):
        """奔跑帧间隔：比走路快，带随机节奏（约 0.18~0.26 秒一帧）。"""
        return random.uniform(0.18, 0.26)

    def _start_ride_run(self):
        """开始一段骑狗奔跑：随机方向（左/右）+ 随机循环组，记录位移基准点。

        奔跑循环组（youxiang 帧）：
          · 右跑组A：优香_7~12（6 帧循环）
          · 右跑组B：优香_12,13,14,7（4 帧循环）
          · 左跑组A/B：右跑组水平翻转生成（PIL FLIP_LEFT_RIGHT）
        跑 4~8 秒会累 → 状态机切到 rest（优香_休息一下），休息完再重开。"""
        pool = []
        if self._anim.get("sport_l1") and self._anim.get("sport_l2"):
            pool = [("l", self._anim["sport_l1"]), ("l", self._anim["sport_l2"]),
                    ("r", self._anim["sport_r1"]), ("r", self._anim["sport_r2"])]
        else:   # 无 PIL 翻转帧时只向右跑（兜底）
            pool = [("r", self._anim["sport_r1"]), ("r", self._anim["sport_r2"])]
        d, seq = random.choice(pool)
        self._run_dir = d
        self._run_seq = seq
        self._frame_idx = 0
        self._next_frame_at = time.time() + self._run_interval()
        self._run_until = time.time() + random.uniform(4, 8)   # 跑这么久会累
        self._run_base_x = self.root.winfo_x()                 # 位移基准点
        self.anim_state = "run"

    def _step_ride(self):
        """奔跑水平位移：按方向每帧移动，撞屏幕边缘或超出 1/4 屏距离自动转向。

        移动范围约束：以本次奔跑起点为基准，向左/右累计位移不得超过
        屏幕宽度的 1/4（screenwidth // 4），超限立即反向 → 宠物始终
        在 1/4 屏宽的带状区域内来回奔跑，不会越跑越远。"""
        if not self._run_dir or not self._run_seq:
            return
        if self._dragging:
            return   # 拖动中冻结奔跑位移：拖拽与奔跑互不抢位置（修「骑大狗时拖不动」）
        if not self.cfg.get("free_move", True):
            # 自由移动已关闭：原地播放奔跑动作，不产生位移
            return
        step = self._run_step_px if self._run_dir == "r" else -self._run_step_px
        try:
            sw = self.root.winfo_screenwidth()
            limit = max(80, sw // 4)                    # 1/4 屏（最小 80px 防超小屏）
            # 水平限制按「人物实际边缘」计算：左/右边缘不越屏 = 贴边即转向
            _cl0, _cr0 = self._char_h_edges()
            x = round(max(-_cl0, min(self.root.winfo_x() + step, sw - _cr0)))
            # 撞屏幕边缘（人物边缘贴边）→ 转向
            if x <= -_cl0 and self._run_dir == "l":
                self._run_dir = "r"
                self._run_base_x = x
            elif x >= sw - _cr0 and self._run_dir == "r":
                self._run_dir = "l"
                self._run_base_x = x
            # 超出 1/4 屏距离 → 转向（基准点移到当前位置，反向跑满 1/4 屏）
            if self._run_dir == "r" and x - self._run_base_x >= limit:
                self._run_dir = "l"
                self._run_base_x = x
            elif self._run_dir == "l" and self._run_base_x - x >= limit:
                self._run_dir = "r"
                self._run_base_x = x
            self.root.geometry(f"+{x}+{self.root.winfo_y()}")
            self._sync_layered()
        except Exception:
            pass

    def _play_expr(self, key):
        """互动后播放一张小表情帧，随后自动回到当前形态的待机状态。

        普通形态回优香_0 待机；运动形态回第 7 帧原地待机（不打断运动节奏）。"""
        if key in self._anim["expr"]:
            self.anim_state = "expr"
            self._expr_key = key
            self._expr_until2 = time.time() + random.uniform(2.4, 3.0)  # 表情停留 2.4~3 秒，节奏更自然

    def toggle_mode(self):
        """右键切换形态：普通待机 ⇄ 骑大狗。

        进入骑大狗 → 立即播放骑狗动作（优香_7~14）→ 休息一下（8~15秒）→
        回到原地待机 → 偶尔随机播放一段；骑乘期间每 30 秒赚 1 金币，
        头顶弹出金币图案。"""
        if self.mode == "sport":
            self.mode = "normal"
            self.anim_state = "idle"
            self._frame_idx = 0
            self._run_seq = []
            self.say("好，下狗休息啦～", expr="happy")
        else:
            self.mode = "sport"
            self._ride_coin_at = time.time() + 30   # 骑乘 30 秒后开始赚金币
            self._start_ride_run()                  # 直接进入奔跑循环
            self.say("骑上大狗！出发咯！(≧▽≦)", expr="happy")

    def on_double(self, e):
        if self._click_job:
            self.root.after_cancel(self._click_job)
            self._click_job = None
        if not self.cfg.get("double_click_open", True):
            # 双击打开已关闭：双击退回为单击互动（随机台词 + 表情）
            self.single_click()
            return
        self.launch_norp()

    # ------------------------------------------------------------------
    # NORP Agent 连接（本地 HTTP API）
    # ------------------------------------------------------------------
    @staticmethod
    def _background_python():
        """返回用于后台启动 NORP 的解释器：优先 pythonw.exe（无控制台窗口），
        找不到则退回当前解释器（配合 CREATE_NO_WINDOW 同样不弹窗）。"""
        exe = sys.executable
        if os.path.basename(exe).lower() == "pythonw.exe":
            return exe
        alt = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(alt):
            return alt
        return exe

    def norp_headers(self):
        h = {"Content-Type": "application/json"}
        token = self.cfg.get("norp_api_token", "")
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def norp_api_base(self):
        return (self.cfg.get("norp_api_url") or DEFAULT_API_URL).rstrip("/")

    def norp_request(self, method, path, body=None, timeout=8):
        """向 NORP 本地 API 发起请求，返回 (ok, data)。"""
        url = self.norp_api_base() + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, method=method, headers=self.norp_headers(), data=data)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return True, json.loads(resp.read().decode("utf-8"))
        except Exception:
            return False, {}

    def check_norp_api(self):
        """探测 NORP 本地 API 是否在线。"""
        ok, data = self.norp_request("GET", "/api/status", timeout=2)
        return ok and data.get("status") == "ok"

    def check_norp_process(self):
        """检测 NORP_Agent.exe 进程是否在运行（纯 Win32 API 枚举，无子进程开销）。"""
        if sys.platform != "win32":
            return False
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260),
                ]

            kernel32 = ctypes.windll.kernel32
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
            kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
            kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

            TH32CS_SNAPPROCESS = 0x00000002
            snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if snap == -1 or not snap:
                return False
            try:
                entry = PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
                    return False
                while True:
                    if entry.szExeFile.lower() == "norp_agent.exe":
                        return True
                    if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                        return False
            finally:
                kernel32.CloseHandle(snap)
        except Exception:
            return False

    def scan_norp_processes(self):
        """枚举所有 NORP Agent 进程（纯 Win32 API），按启动时间降序返回。

        每项：{"pid", "name", "path", "created", "label", "has_api"}
          · name    小写 exe 名（norp_agent.exe 旧版 / norp.agent.exe 新版）
          · path    exe 完整路径（取不到为空串）
          · created 进程创建时间（epoch 秒，取不到为 0）
          · label   版本标签（如 "20260810 新版"）
          · has_api 该版本是否带本地 API：新版 False / 旧版 True / 未知 None
        """
        if sys.platform != "win32":
            return []
        results = []
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260),
                ]

            kernel32 = ctypes.windll.kernel32
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
            kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
            kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, ctypes.c_wchar_p,
                ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetProcessTimes.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_ulonglong),
                ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_ulonglong)]

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            _NORP_EXE_NAMES = ("norp_agent.exe", "norp.agent.exe")

            def _proc_info(pid):
                info = {"path": "", "created": 0.0}
                h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                         False, pid)
                if h:
                    try:
                        buf = ctypes.create_unicode_buffer(32768)
                        size = wintypes.DWORD(len(buf))
                        if kernel32.QueryFullProcessImageNameW(
                                h, 0, buf, ctypes.byref(size)):
                            info["path"] = buf.value
                        ct = ctypes.c_ulonglong()
                        et = ctypes.c_ulonglong()
                        kt = ctypes.c_ulonglong()
                        ut = ctypes.c_ulonglong()
                        if kernel32.GetProcessTimes(
                                h, ctypes.byref(ct), ctypes.byref(et),
                                ctypes.byref(kt), ctypes.byref(ut)):
                            # FILETIME → unix epoch
                            info["created"] = (ct.value - 116444736000000000) / 1e7
                    finally:
                        kernel32.CloseHandle(h)
                return info

            snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
            if not snap or snap == -1:
                return []
            try:
                entry = PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
                    return []
                while True:
                    name = (entry.szExeFile or "").lower()
                    if name in _NORP_EXE_NAMES:
                        info = _proc_info(entry.th32ProcessID)
                        path = info["path"] or name
                        label = _version_label(path)
                        p = path.lower()
                        # 版本判定：路径特征优先，文件名兜底
                        if ("20260810" in p or "main_update" in p
                                or name == "norp.agent.exe"):
                            has_api = False      # 新版：无本地 API
                        elif ("20260807" in p or "update20260807" in p
                              or name == "norp_agent.exe"):
                            has_api = True       # 旧版：默认带本地 API
                        else:
                            has_api = None
                        results.append({"pid": entry.th32ProcessID,
                                        "name": name, "path": path,
                                        "created": info["created"],
                                        "label": label, "has_api": has_api})
                    if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                        break
            finally:
                kernel32.CloseHandle(snap)
        except Exception:
            pass
        results.sort(key=lambda p: p["created"], reverse=True)
        return results

    def latest_norp_proc(self):
        """最近启动的 NORP Agent 进程（None = 没在跑）。

        「当前正在使用的 NORP」= 最近启动的那个 —— 双击打开的
        永远是本地上一个（最近）启动的版本，而不是残留的旧版 API。
        """
        procs = self.scan_norp_processes()
        return procs[0] if procs else None

    def _focus_norp_window(self, proc=None):
        """把正在运行的 NORP 窗口带到前台（找不到/失败就静默跳过）。"""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            SW_RESTORE = 9
            found = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def _cb(hwnd, lparam):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if 0 < length <= 200:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        if "NORP" in buf.value.upper():
                            found.append(hwnd)
                return True

            user32.EnumWindows(_cb, 0)
            for hwnd in found:
                user32.ShowWindow(hwnd, SW_RESTORE)
                try:
                    user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
        except Exception:
            pass

    def launch_norp(self, extra_args=None):
        """打开 NORP Agent：已在运行则聚焦它；否则启动「最新版本」。

        ★ 版本适配：不再先查 17777 API（旧版残留 API 在线会误判成
        「已经在运行」），而是先查最近启动的 NORP 进程：
          1. 有进程在跑 → 把它的窗口带到前台（就是用户正在用的那个）；
          2. 没在跑 → 优先启动上次启动过的版本（norp_last_exe），
             其次按配置，配置失效则自动探测工作区里的最新版本。
        """
        latest = self.latest_norp_proc()
        if latest is not None:
            self._focus_norp_window(latest)
            if latest["has_api"] is False:
                self._norp_state = "exe_new"
                self._norp_label = latest["label"]
                self._stop_polling()
                self.say("NORP Agent（%s）已经在运行啦～\n新版不带本地 API，任务播报用不了，但我会一直陪你干活！"
                         % latest["label"], expr="happy")
            elif self.check_norp_api():
                self._norp_state = "api"
                self._norp_label = latest["label"]
                self._start_polling()
                self.say("NORP Agent（%s）已经在运行啦～" % latest["label"],
                         expr="happy")
            else:
                self._norp_state = "exe"
                self._norp_label = latest["label"]
                self.say("检测到 EXE 版 NORP（%s）正在运行～\n它没开本地 API，收发任务功能用不了"
                         % latest["label"], expr="talk")
            return

        # ── 没在运行：选启动目标 ──
        # ① 上次启动过的版本（「双击打开的是本地上一个启动的版本」）
        last_exe = self.cfg.get("norp_last_exe", "")
        mode = self.cfg.get("norp_launch_mode", "source")
        target = None
        if last_exe and os.path.isfile(last_exe):
            target = ("exe", last_exe, _version_label(last_exe))
        # ② 按配置的启动方式/路径
        if target is None:
            if mode == "source":
                src = self.cfg.get("norp_source_dir") or DEFAULT_SOURCE_DIR
                if os.path.isfile(os.path.join(src, "main.py")):
                    target = ("src", src, _version_label(src))
            else:
                exe = self.cfg.get("norp_exe") or DEFAULT_EXE
                if os.path.isfile(exe):
                    target = ("exe", exe, _version_label(exe))
        # ③ 自动探测工作区里的最新版本（exe 优先）
        if target is None:
            installs = _find_norp_installs(ROOT_DIR)
            exe_it = next((it for it in installs if it["kind"] == "exe"), None)
            src_it = next((it for it in installs if it["kind"] == "src"), None)
            if exe_it:
                target = ("exe", exe_it["path"], exe_it["label"])
            elif src_it:
                target = ("src", src_it["path"], src_it["label"])
            else:
                self.say("找不到 NORP Agent 啦…\n请在设置里检查路径", expr="sad")
                return

        kind, path, label = target
        try:
            if kind == "src":
                subprocess.Popen([self._background_python(),
                                  os.path.join(path, "main.py")],
                                 cwd=path,
                                 creationflags=(subprocess.CREATE_NO_WINDOW |
                                                subprocess.CREATE_NEW_PROCESS_GROUP))
            else:
                args = []
                if extra_args:
                    args.extend(extra_args)
                else:
                    tpl = self.cfg.get("norp_args", "").strip()
                    if tpl:
                        args.extend(shlex.split(tpl))
                subprocess.Popen([path] + args, cwd=os.path.dirname(path),
                                 creationflags=subprocess.CREATE_NO_WINDOW)
                self.cfg["norp_last_exe"] = path     # 记住上次启动的版本
                save_config(self.cfg)
            self.say("NORP Agent 启动中～正在连接…", expr="talk")
        except Exception as ex:
            self.say(f"启动失败：{ex}", expr="sad")
            return

        # ── 等待本地 API 就绪 ──
        # 新版（20260810+）没有本地 API：只短等 5 秒，API 没来就正常
        # 判定为 exe_new，不报错；源码版/旧版才按 norp_api_wait 等待。
        is_new_exe = kind == "exe" and label.startswith("20260810")
        wait = 5 if is_new_exe else int(self.cfg.get("norp_api_wait", 25))
        deadline = time.time() + wait
        while time.time() < deadline:
            time.sleep(1.0)
            if self.check_norp_api():
                self._api_online = True
                self._norp_state = "api"
                self._norp_label = label
                self._start_polling()
                self.say("连接成功！NORP Agent 已就绪～", expr="happy")
                return
        self._api_online = False
        if is_new_exe:
            self._norp_state = "exe_new"
            self._norp_label = label
            self._stop_polling()
            self.say("NORP Agent（%s）已启动～\n新版不带本地 API，任务播报用不了，但我会一直陪你干活！"
                     % label, expr="happy")
        else:
            self.say("NORP Agent 已启动，但本地 API 没就绪…\n"
                     "（请确认使用的是源码版，且 local_api_enabled=true）", expr="sad")

    def send_command_to_norp(self, text=None, session_id=""):
        """把任务真正发送给 NORP Agent（通过本地 API）。"""
        if self._norp_state == "exe_new":
            self.say("NORP（%s）正在运行，但新版没有本地 API…\n收发任务需要旧版源码版（20260807）"
                     % self._norp_label, expr="sad")
            return
        if not self.check_norp_api():
            if self._norp_state == "exe":
                self.say("NORP 正在运行（EXE 版）但不带本地 API…\n收发任务需要源码版，右键→设置 可切换", expr="sad")
            else:
                self.say("NORP Agent 没在线…双击我先启动它", expr="sad")
            return
        if text is None:
            self._hook_pause()
            try:
                text = simpledialog.askstring("给 NORP Agent 传任务",
                                              "输入要 NORP 执行的任务：\n例如「帮我在桌面创建一个待办清单.md」",
                                              parent=self.root)
            finally:
                self._drain_mouse_queue()
                self._hook_resume()
            if not text:
                return
        if self._task_running:
            self.say("NORP 正在忙上一个任务哦…", expr="talk")
            return
        ok, data = self.norp_request("POST", "/api/send",
                                     {"session_id": session_id, "text": text}, timeout=8)
        if not ok:
            self.say("任务发送失败…连接断了？", expr="sad")
            return
        self._task_running = True
        self._waiting_user = False
        self.say("任务已交给 NORP！它开始干活了～", expr="happy")
        # 确保轮询线程在跑
        self._start_polling()

    # ------------------------------------------------------------------
    # 本地控制服务器（供 NORP 插件指挥宠物）
    # ------------------------------------------------------------------
    def _start_control_server(self):
        """启动 127.0.0.1 控制端口，让 NORP 插件可以指挥宠物说话/做动作/退出。"""
        try:
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            port = int(self.cfg.get("pet_control_port", 17778))
        except Exception:
            return
        try:
            pet = self

            class ControlHandler(BaseHTTPRequestHandler):
                def log_message(self, fmt, *args):
                    pass

                def _send(self, code, obj):
                    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

                def _read_body(self):
                    try:
                        length = int(self.headers.get("Content-Length", 0))
                    except (TypeError, ValueError):
                        length = 0
                    if length <= 0:
                        return {}
                    raw = self.rfile.read(length)
                    try:
                        return json.loads(raw.decode("utf-8"))
                    except Exception:
                        return {}

                def do_GET(self):
                    if self.path.rstrip("/") == "/pet/status":
                        self._send(200, {"online": True, "expr": pet.expr,
                                         "norp_state": pet._norp_state,
                                         "norp_version": getattr(pet, "_norp_label", "")})
                    else:
                        self._send(404, {"error": "not found"})

                def do_POST(self):
                    body = self._read_body()
                    path = self.path.rstrip("/")
                    if path == "/pet/say":
                        pet._control_box.put({"cmd": "say", "text": str(body.get("text", "")),
                                              "expr": body.get("expr"),
                                              "duration": body.get("duration")})
                        self._send(200, {"ok": True})
                    elif path == "/pet/action":
                        pet._control_box.put({"cmd": "action",
                                              "expr": str(body.get("expr", "idle"))})
                        self._send(200, {"ok": True})
                    elif path == "/pet/quit":
                        pet._control_box.put({"cmd": "quit"})
                        self._send(200, {"ok": True})
                    else:
                        self._send(404, {"error": "not found"})

            httpd = ThreadingHTTPServer(("127.0.0.1", port), ControlHandler)
            threading.Thread(target=httpd.serve_forever, daemon=True,
                             name="pet-control-server").start()
        except Exception as ex:
            print(f"[pet] 控制服务器启动失败: {ex}")

    def _consume_controls(self):
        """在 UI 线程处理插件发来的控制指令。"""
        while not self._control_box.empty():
            try:
                ctrl = self._control_box.get_nowait()
            except queue.Empty:
                break
            cmd = ctrl.get("cmd")
            if cmd == "say":
                text = str(ctrl.get("text", "")).strip()
                if text:
                    expr = ctrl.get("expr")
                    duration = ctrl.get("duration") or 2.6
                    try:
                        duration = max(0.5, min(float(duration), 20.0))
                    except (TypeError, ValueError):
                        duration = 2.6
                    self.say(text, expr=expr, duration=duration)
            elif cmd == "action":
                expr = str(ctrl.get("expr", "idle"))
                if expr in ("idle", "happy", "talk", "sleep", "sad"):
                    self.expr = expr
                    self._expr_until = time.time() + 3.0
            elif cmd == "quit":
                self.root.after(50, lambda: self.quit_app(user_initiated=False))

    # ------------------------------------------------------------------
    # 事件轮询（后台线程）
    # ------------------------------------------------------------------
    def _start_polling(self):
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True,
                                             name="norp-event-poll")
        self._poll_thread.start()

    def _stop_polling(self):
        """停止事件轮询（新版 NORP 无 API 时调用，不再轮询 17777，
        避免误连旧版残留 API 的事件流）。线程为 daemon，自然退出。"""
        self._poll_stop.set()
        self._poll_thread = None

    def _poll_loop(self):
        """后台线程：探测 API 在线状态 + 长轮询 NORP 事件流。

        在线状态写回 self._api_online（简单赋值，原子），UI 线程只读
        该标志、绝不自己发同步 HTTP 请求 —— 同步请求会卡 Tk 主线程，
        NORP 关闭时连接被拖到超时（2s），期间 WH_MOUSE_LL 钩子回调
        也无法响应 → 整机鼠标周期性卡顿。

        ★ 版本适配：当前 NORP 是新版（exe_new，无本地 API）时不再
        轮询 17777 —— 端口上的旧版残留 API 不是用户正在用的 NORP。
        """
        while not self._poll_stop.is_set():
            if self._norp_state == "exe_new":
                time.sleep(3)
                continue
            online = self.check_norp_api()
            self._api_online = online      # 后台线程探测 → UI 线程只读标志
            if not online:
                time.sleep(3)
                continue
            ok, data = self.norp_request("GET", "/api/events?timeout=12", timeout=20)
            if not ok:
                time.sleep(2)
                continue
            for ev in data.get("events", []):
                self._event_box.put(ev)
            time.sleep(0.2)

    def _consume_events(self):
        """在 UI 线程处理轮询到的事件。"""
        events = []
        while not self._event_box.empty():
            try:
                events.append(self._event_box.get_nowait())
            except queue.Empty:
                break
        for ev in events:
            self._handle_event(ev)

    def _handle_event(self, ev):
        """处理单个 NORP 事件：T:/R:/Q:/D:/E:/U: 等。"""
        if len(ev) < 2 or ev[1] != ":":
            return
        kind, content = ev[0], ev[2:]
        if kind == "R":        # 流式回复片段 → 只累积，等 D 事件展示完整答案（避免碎片刷屏/覆盖落盘）
            self._pending_reply = (self._pending_reply or "") + content
        elif kind == "Q":      # NORP 提问 → 弹窗让用户回答
            self._waiting_user = True
            self._ask_user_dialog(content)
        elif kind == "D":      # 任务完成：content 就是 NORP 的完整最终答案 → 气泡展示 + 落盘
            self._task_running = False
            text = (content or "").strip() or (self._pending_reply or "").strip()
            self._pending_reply = ""
            if not text:
                text = "任务完成！"
            saved = self._save_task_result(text)
            self._show_result_bubble(text, saved)
        elif kind == "E":      # 错误
            self._task_running = False
            self._pending_reply = ""
            self.say("NORP 说它出错了…", expr="sad")
        elif kind == "U":      # token 用量（不打扰用户）
            pass
        # T:（思考）/ C:（工具调用）/ F: 等不打扰用户

    def _show_result_bubble(self, text, saved):
        """展示 NORP 的最终答案：只显示摘要，完整结果已落盘可查。"""
        MAX_LEN = 50
        if len(text) > MAX_LEN:
            hint = "\n(全文已存 task_results，右键→查看)" if saved else "…"
            self.say(text[:MAX_LEN] + hint, expr="happy", duration=8)
        else:
            self.say(text, expr="happy", duration=8)

    def _save_task_result(self, content):
        """把 NORP 的完整回复落盘保存，返回保存路径；失败返回空串。"""
        try:
            results_dir = os.path.join(APP_DIR, "task_results")
            os.makedirs(results_dir, exist_ok=True)
            fname = "norp_result_%s.txt" % time.strftime("%Y%m%d_%H%M%S")
            path = os.path.join(results_dir, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write("任务完成时间: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
                f.write("-" * 40 + "\n")
                f.write(content)
            return path
        except Exception:
            return ""

    def open_task_results(self):
        """打开任务结果保存目录。"""
        try:
            d = os.path.join(APP_DIR, "task_results")
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception:
            pass

    def _ask_user_dialog(self, question):
        """NORP 需要用户确认/输入时，弹出对话框并把回答回传。"""
        try:
            self._hook_pause()
            try:
                answer = simpledialog.askstring(
                    "NORP 需要你确认",
                    question[:200] + "\n\n（输入内容会回传给 NORP）",
                    parent=self.root)
            finally:
                self._drain_mouse_queue()
                self._hook_resume()
            if answer is not None:
                self.norp_request("POST", "/api/user_input", {"text": answer}, timeout=5)
                self.say("你的回答已传给 NORP～", expr="happy")
            else:
                self.norp_request("POST", "/api/user_input", {"text": "（用户取消）"}, timeout=5)
                self.say("好的，已告诉 NORP 你取消了", expr="talk")
        except Exception:
            pass
        finally:
            self._waiting_user = False

    # ------------------------------------------------------------------
    # 快捷指令
    # ------------------------------------------------------------------
    def run_command(self, cmd):
        try:
            subprocess.Popen(cmd, shell=True, cwd=ROOT_DIR,
                             creationflags=subprocess.CREATE_NO_WINDOW)
            self.say("搞定！(≧▽≦)", expr="happy")
        except Exception as ex:
            self.say(f"执行失败：{ex}", expr="sad")

    def open_panel(self):
        if self._panel and self._panel.winfo_exists():
            self._panel.lift()
            return
        panel = tk.Toplevel(self.root)
        panel.title("NORP Pet · 快捷指令")
        panel.attributes("-topmost", True)
        panel.resizable(False, False)
        self._panel = panel

        tk.Label(panel, text="✦ 快捷指令 ✦", font=FONT_B,
                 fg="#2b6c8f").pack(pady=(10, 4))

        frame = tk.Frame(panel)
        frame.pack(padx=12, pady=4)
        lb = tk.Listbox(frame, width=34, height=10, font=FONT,
                        activestyle="dotbox", selectbackground="#bfe3f2")
        lb.pack(side=tk.LEFT, fill=tk.BOTH)
        sb = tk.Scrollbar(frame, orient=tk.VERTICAL, command=lb.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb.config(yscrollcommand=sb.set)

        # 内置项
        lb.insert(tk.END, "🚀 打开 NORP Agent")
        lb.insert(tk.END, "📡 发送任务给 NORP…")
        lb.insert(tk.END, "──────────────")
        self._cmd_items = []
        for item in self.cfg.get("commands", []):
            self._cmd_items.append(item)
            lb.insert(tk.END, f"⚡ {item['name']}")

        def do_execute():
            sel = lb.curselection()
            if not sel:
                return
            idx = sel[0]
            if idx == 0:
                self.launch_norp()
            elif idx == 1:
                self.send_command_to_norp()
            elif idx > 2:
                item = self._cmd_items[idx - 3]
                if item.get("cmd"):
                    self.run_command(item["cmd"])

        def do_add():
            self._hook_pause()
            try:
                name = simpledialog.askstring("添加指令", "指令名称：", parent=panel)
                if not name:
                    return
                cmd = simpledialog.askstring("添加指令", "要执行的命令（shell）：\n例如 notepad 或 start https://...", parent=panel)
                if not cmd:
                    return
            finally:
                self._drain_mouse_queue()
                self._hook_resume()
            self.cfg.setdefault("commands", []).append({"name": name, "cmd": cmd})
            save_config(self.cfg)
            lb.insert(tk.END, f"⚡ {name}")
            self.say("新指令已添加～", expr="happy")

        def do_del():
            sel = lb.curselection()
            if not sel or sel[0] <= 2:
                return
            idx = sel[0] - 3
            if 0 <= idx < len(self._cmd_items):
                self._cmd_items.pop(idx)
                self.cfg["commands"].pop(idx)
                save_config(self.cfg)
                lb.delete(sel[0])

        btn_row = tk.Frame(panel)
        btn_row.pack(pady=(4, 10))
        tk.Button(btn_row, text="执行", font=FONT, width=8,
                  command=do_execute).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_row, text="添加", font=FONT, width=8,
                  command=do_add).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_row, text="删除", font=FONT, width=8,
                  command=do_del).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_row, text="关闭", font=FONT, width=8,
                  command=panel.destroy).pack(side=tk.LEFT, padx=4)

        panel.bind("<Destroy>", lambda e: setattr(self, "_panel", None))

    # ------------------------------------------------------------------
    # 右键菜单
    # ------------------------------------------------------------------
    def on_menu(self, e):
        # 右键 = 退出半隐藏（用户点名要求的退出方式之一），恢复正常状态再弹菜单
        self._exit_hide("menu")
        menu = tk.Menu(self.root, tearoff=0, font=FONT)
        if self._norp_state == "api":
            status = "🟢 已连接 · API"
        elif self._norp_state == "exe_new":
            status = "🟡 运行中 · 新版EXE"
        elif self._norp_state == "exe":
            status = "🟡 运行中 · EXE"
        else:
            status = "🔴 未运行"
        menu.add_command(label=f"NORP Agent · {status}", state="disabled")
        menu.add_separator()
        menu.add_command(label="🚀 打开 NORP Agent",
                         command=self.launch_norp)
        menu.add_command(label="📡 发送任务给 NORP…",
                         command=self.send_command_to_norp)
        menu.add_command(label="📁 查看任务结果",
                         command=self.open_task_results)
        menu.add_separator()
        menu.add_command(label="⚡ 快捷指令面板…", command=self.open_panel)
        menu.add_separator()
        menu.add_command(label="🎯 跟随鼠标" + ("：开 ✓" if self._follow_mouse else "：关"),
                         command=self.toggle_follow_mouse)
        menu.add_command(label="🐕 骑大狗" + ("：骑乘中 ✓" if self.mode == "sport" else "：关"),
                         command=self.toggle_mode)
        menu.add_command(label="🛒 商店", command=self.open_shop)
        menu.add_command(label="🐾 伙伴状态", command=self.open_status)
        menu.add_command(label="🖐 摸摸头", command=lambda: (self.add_affection(2), self.say("嘿嘿~~！！，sensei不要捉弄我了，快去工作啦", expr="happy"), self._play_expr("touch")))
        # 🍰 喂食：级联子菜单，列出背包里商店购买的食物
        feed_menu = tk.Menu(menu, tearoff=0, font=FONT)
        _foods = [
            ("cola", "🥤 可乐", "+5 心情 +10 饱食"),
            ("riceball", "🍙 饭团", "+10 心情 +80 饱食"),
        ]
        _total = sum(int(self.inventory.get(k, 0) or 0) for k, _nm, _fx in _foods)
        if _total <= 0:
            feed_menu.add_command(label="（背包空空，去商店买点吃的吧）", state="disabled")
        else:
            for k, nm, fx in _foods:
                _cnt = int(self.inventory.get(k, 0) or 0)
                if _cnt > 0:
                    feed_menu.add_command(
                        label=f"{nm} ×{_cnt}（{fx}）",
                        command=lambda key=k: self._feed_item(key))
        menu.add_cascade(label="🍰 喂食" + ("" if _total else "（空）"), menu=feed_menu)
        if self.expr == "sleep":
            menu.add_command(label="🌞 叫醒我", command=self.wake_up)
        else:
            menu.add_command(label="🌙 睡觉", command=self.go_sleep)
        menu.add_command(label="💬 打招呼", command=lambda: (self.add_affection(1), self.say("sensei～你好呀！今天也要加油鸭！", expr="happy"), self._play_expr("hi")))
        menu.add_separator()
        menu.add_command(label="⚙ 设置…", command=self.open_settings)
        menu.add_separator()
        menu.add_command(label="❌ 退出宠物", command=self.quit_app)
        # 模态期间必须暂停全局钩子：钩子会把「点击宠物取消菜单」的
        # 消息吞掉 → 菜单取消不掉 → 整个 Tk 卡死（点宠物无反应、
        # 拖不动、动画冻结）。菜单关闭后再恢复钩子。
        self._hook_pause()
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass
            self._drain_mouse_queue()
            self._hook_resume()

    def toggle_follow_mouse(self):
        """切换跟随鼠标模式：开启后小人自动跟着光标移动。"""
        self._follow_mouse = not self._follow_mouse
        self.cfg["follow_mouse"] = self._follow_mouse
        save_config(self.cfg)
        if self._follow_mouse:
            self.say("我会一直跟着你哦～", expr="happy")
        else:
            self.say("好，我停下来啦。", expr="happy")

    def go_sleep(self):
        self.expr = "sleep"
        self._bubble = "zzz…"
        self._bubble_until = time.time() + 3
        self._expr_until = time.time() + 3600
        # 立即中断走路/移动：睡着就原地不动，绝不梦游
        self.anim_state = "idle"
        self._walk_dir = None
        self._next_random_at = time.time() + 99999   # 睡觉期间不触发随机走动

    def wake_up(self):
        self.say("唔……早上好！", expr="happy")
        # 恢复走动节奏：睡醒后过一小会儿又可以散步了
        self._next_random_at = time.time() + random.uniform(15, 35)

    # ==================================================================
    # 商店 & 伙伴状态
    # ==================================================================
    def _icon_photo(self, key, size=None):
        """取图标的 Tk PhotoImage（可选缩放），用于商店 / 状态面板。"""
        ico = self._icons.get(key)
        if ico is None:
            return None
        if size and ico[1] is not None:
            try:
                im = ico[1].resize((size, size),
                                   getattr(Image, "Resampling", Image).LANCZOS)
                return ImageTk.PhotoImage(im)
            except Exception:
                return ico[0]
        return ico[0]

    def open_shop(self):
        """商店：可乐 ¥2（心情+5 饱食度+10）/ 饭团 ¥10（心情+10 饱食度+80）。"""
        if self._shop_win is not None and self._shop_win.winfo_exists():
            self._shop_win.lift()
            self._shop_win.focus_force()
            return
        win = tk.Toplevel(self.root)
        win.title("🛒 商店")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        self._shop_win = win

        tk.Label(win, text="🛒 %s 的小商店" % self._pet_name, font=("Microsoft YaHei UI", 13, "bold"),
                 fg="#b8860b").grid(row=0, column=0, columnspan=3, pady=(12, 4))
        coin_lb = tk.Label(win, text="", font=("Microsoft YaHei UI", 11),
                           fg="#8b6914")
        coin_lb.grid(row=1, column=0, columnspan=3, pady=(0, 10))

        def _refresh_coin():
            try:
                coin_lb.config(text=f"💰 金币：{self.coins}")
            except Exception:
                pass

        _refresh_coin()

        items = [
            ("cola", "🥤 可乐", 2, "恢复心情 +5\n饱食度 +10"),
            ("riceball", "🍙 饭团", 10, "恢复心情 +10\n饱食度 +80"),
        ]
        own_lbs = {}
        for idx, (key, name, price, effect) in enumerate(items):
            card = tk.Frame(win, relief="groove", bd=1, padx=10, pady=8)
            card.grid(row=2, column=idx, padx=10, pady=(0, 12))
            tk.Label(card, text=name, font=("Microsoft YaHei UI", 12, "bold")).pack()
            ico = self._icon_photo(key, 72)
            if ico is not None:
                lb = tk.Label(card, image=ico)
                lb.image = ico
                lb.pack(pady=4)
            tk.Label(card, text=effect, font=("Microsoft YaHei UI", 10),
                     fg="#666666").pack()
            tk.Label(card, text=f"售价：{price} 金币", font=("Microsoft YaHei UI", 10),
                     fg="#b8860b").pack(pady=(2, 6))
            own_lb = tk.Label(card, text="", font=("Microsoft YaHei UI", 9),
                              fg="#2b8a3e")
            own_lb.pack(pady=(2, 0))
            own_lbs[key] = own_lb
            tk.Button(card, text="购买", font=("Microsoft YaHei UI", 10, "bold"),
                      command=lambda k=key, p=price: self._buy_item(k, p, _refresh_all)
                      ).pack()

        def _refresh_own():
            for k, lb in own_lbs.items():
                cnt = int(self.inventory.get(k, 0) or 0)
                lb.config(text=f"背包里：×{cnt}" if cnt else "背包里：暂无")

        def _refresh_all():
            _refresh_coin()
            _refresh_own()

        _refresh_own()

        tk.Label(win, text="（买来的食物会放进背包，右键「🍰 喂食」选择食用～）",
                 font=("Microsoft YaHei UI", 9), fg="#999999").grid(
            row=3, column=0, columnspan=3, pady=(0, 10))
        win.protocol("WM_DELETE_WINDOW",
                     lambda: (win.destroy(), setattr(self, "_shop_win", None)))

    def _buy_item(self, key, price, refresh_cb=None):
        """购买商品：扣金币 → 物品放入背包（之后可在右键「喂食」中选择食用）。"""
        if not self.spend_coins(price):
            self.say("金币不够啦，先去骑大狗赚点金币吧！", expr="sad")
            return
        self.add_item(key, 1)
        if key == "cola":
            self.say("可乐买好啦！放进背包～右键喂食就能喝啦！", expr="happy")
        else:
            self.say("饭团买好啦！放进背包～右键喂食就能吃啦！", expr="happy")
        if refresh_cb:
            refresh_cb()
        if self._status_win is not None and self._status_win.winfo_exists():
            self._refresh_status()

    # ------------------------------------------------------------------
    # 小伙伴养成（v2.1）：好感度 / 节日问候 / 动态台词
    # ------------------------------------------------------------------
    def add_affection(self, n, save=True):
        """增加好感度（互动/喂食/摸头等），并持久化到 config。"""
        self._affection = max(0, self._affection + int(n))
        self.cfg["affection"] = self._affection
        if save:
            try:
                save_config(self.cfg)
            except Exception:
                pass
        if self._status_win is not None and self._status_win.winfo_exists():
            self._refresh_status()

    def affection_level(self):
        """好感度等级：每 100 点升 1 级，最高 10 级。"""
        return min(10, 1 + self._affection // 100)

    def affection_progress(self):
        """当前等级内的进度（0.0 ~ 1.0）。"""
        return (self._affection % 100) / 100.0

    def _greet_once(self):
        """启动问候：节日 / 深夜 / 相伴纪念日（当天只问候一次，不重复打扰）。"""
        try:
            _today = time.strftime("%Y-%m-%d")
            if self.cfg.get("last_greeting") == _today:
                return
            line, expr = self._greeting_for_today()
            if line:
                self.cfg["last_greeting"] = _today
                try:
                    save_config(self.cfg)
                except Exception:
                    pass
                self.say(line, expr=expr, duration=4)
        except Exception:
            pass

    def _greeting_for_today(self):
        """按日期返回 (台词, 表情)；无特别日子返回 (None, None)。"""
        _now = time.localtime()
        _md = (_now.tm_mon, _now.tm_mday)
        _h = _now.tm_hour
        _name = self._pet_name
        _festivals = {
            (1, 1): ("新年快乐！今年也请多多指教呀，sensei～(≧▽≦)", "happy"),
            (2, 14): ("今天是情人节哦～sensei有喜欢的人了吗？", "talk"),
            (3, 8): ("女神节快乐～祝 sensei 身边的女孩子们都开开心心！", "happy"),
            (5, 1): ("劳动节快乐！sensei 辛苦啦，今天也要劳逸结合哦～", "happy"),
            (6, 1): ("六一儿童节！今天可以理直气壮地摸鱼啦，sensei～", "happy"),
            (10, 1): ("国庆快乐！祝 sensei 假期玩得开心～", "happy"),
            (12, 24): ("平安夜快乐～希望 sensei 今晚做个甜甜的梦！", "happy"),
            (12, 25): ("圣诞快乐！%s 祝 sensei 圣诞开心～" % _name, "happy"),
        }
        if _md in _festivals:
            return _festivals[_md]
        if _h >= 23 or _h < 5:
            return ("夜深啦……sensei 早点休息哦，%s 会守着你的！" % _name, "sleep")
        if self._companion_days >= 1 and self._companion_days % 7 == 0:
            return ("我们已经相伴 %d 天啦，sensei～以后也要一直在一起哦！"
                    % self._companion_days, "happy")
        return (None, None)

    def _dynamic_lines(self):
        """动态台词池：相伴天数 / 自我介绍 / 撒娇，插进随机说话里增加新鲜感。"""
        _name = self._pet_name
        _lines = [
            "我叫%s！以后请多关照呀，sensei～" % _name,
            "sensei，%s今天也元气满满哦！" % _name,
            "sensei~摸摸头～%s会很开心的！" % _name,
        ]
        if self._companion_days >= 1:
            _lines.append("我们已经相伴 %d 天啦，sensei～(≧▽≦)" % self._companion_days)
            if self._companion_days >= 7:
                _lines.append("整整 %d 天了呢……%s会一直陪着 sensei 的！"
                              % (self._companion_days, _name))
        return _lines

    def _feed_item(self, key):
        """喂食：从背包消耗 1 个物品，恢复心情 / 饱食度，播放吃东西动画。"""
        if not self.use_item(key):
            self.say("背包里没有这个了哦～去商店买点吧！", expr="sad")
            return
        if key == "cola":
            self.add_mood(5)
            self.add_satiety(10)
            self.say("咕嘟咕嘟～可乐真好喝！心情+5 饱食度+10", expr="happy")
        else:
            self.add_mood(10)
            self.add_satiety(80)
            self.say("啊呜啊呜～饭团好香！心情+10 饱食度+80", expr="happy")
        self.add_affection(3)          # 喂食 +3 好感
        self._play_expr("eat")
        if self._status_win is not None and self._status_win.winfo_exists():
            self._refresh_status()

    def open_status(self):
        """伙伴状态面板：金币 / 心情 / 饱食度（图标 + 进度条，每秒自动刷新）。"""
        if self._status_win is not None and self._status_win.winfo_exists():
            self._status_win.lift()
            self._status_win.focus_force()
            return
        win = tk.Toplevel(self.root)
        win.title("🐾 %s 的状态" % self._pet_name)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        self._status_win = win
        self._status_refs = {}

        tk.Label(win, text="🐾 %s 的状态" % self._pet_name,
                 font=("Microsoft YaHei UI", 13, "bold"),
                 fg="#2b6c8f").grid(row=0, column=0, columnspan=4, pady=(12, 8))

        def _row(row, key, name, has_max=True, icon_key=None):
            tk.Label(win, text=name, font=("Microsoft YaHei UI", 11)).grid(
                row=row, column=0, sticky="w", padx=(12, 6), pady=4)
            ico = self._icon_photo(icon_key or key, 34)
            if ico is not None:
                lb = tk.Label(win, image=ico)
                lb.image = ico
                lb.grid(row=row, column=1, padx=2)
            bar = tk.Canvas(win, width=150, height=16, bg="#eeeeee",
                            highlightthickness=1, highlightbackground="#cccccc")
            bar.grid(row=row, column=2, padx=(6, 12), pady=4)
            txt = tk.Label(win, text="", font=("Microsoft YaHei UI", 10))
            txt.grid(row=row, column=3, padx=(0, 12))
            self._status_refs[key] = (bar, txt, has_max)

        _row(1, "coin", "金币", has_max=False)
        _row(2, "mood", "心情", has_max=True)
        _row(3, "satiety", "饱食度", has_max=True)
        _row(4, "affection", "好感度", has_max=True, icon_key="mood")

        # 第 5、6 行：今日工作陪伴时长 / 相识时长（纯文本，每秒自动刷新）
        self._status_time_refs = None
        t_today = tk.Label(win, text="", font=("Microsoft YaHei UI", 10), fg="#8a8a8a")
        t_today.grid(row=5, column=0, columnspan=4, sticky="w", padx=(12, 6), pady=(8, 0))
        t_known = tk.Label(win, text="", font=("Microsoft YaHei UI", 10), fg="#8a8a8a")
        t_known.grid(row=6, column=0, columnspan=4, sticky="w", padx=(12, 6), pady=(2, 12))
        self._status_time_refs = (t_today, t_known)

        self._refresh_status()
        win.protocol("WM_DELETE_WINDOW",
                     lambda: (win.destroy(), setattr(self, "_status_win", None)))
        win.after(1000, self._status_auto_refresh)

    def _refresh_status(self):
        """刷新状态面板数值与进度条。"""
        try:
            if self._status_win is None or not self._status_win.winfo_exists():
                return
            for key, val, has_max in (("coin", self.coins, False),
                                      ("mood", self.mood, True),
                                      ("satiety", self.satiety, True)):
                bar, txt, _ = self._status_refs[key]
                bar.delete("all")
                if has_max:
                    frac = max(0.0, min(1.0, val / 100.0))
                    bar.create_rectangle(2, 2, 2 + 146 * frac, 14,
                                         fill="#7ec8e3", outline="")
                    txt.config(text=f"{val} / 100")
                else:
                    bar.create_rectangle(2, 2, 146, 14, fill="#ffd700", outline="")
                    txt.config(text=f"{val}")
            # 好感度行：粉色进度条（当前等级内进度）+ Lv 显示
            _abar, _atxt, _ = self._status_refs["affection"]
            _abar.delete("all")
            _frac = max(0.0, min(1.0, self.affection_progress()))
            if self.affection_level() >= 10:
                _abar.create_rectangle(2, 2, 146, 14, fill="#ff9eb5", outline="")
                _atxt.config(text=f"MAX · {self._affection}")
            else:
                _abar.create_rectangle(2, 2, 2 + 146 * _frac, 14,
                                       fill="#ff9eb5", outline="")
                _atxt.config(text=f"Lv.{self.affection_level()} · {self._affection}")
            # 今日陪伴 / 相识时长（每秒刷新）
            if self._status_time_refs:
                _now = time.time()
                t_today, t_known = self._status_time_refs
                _t0 = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
                secs_today = max(0, int(_now - max(self._today_launch, _t0)))
                h, m = secs_today // 3600, secs_today % 3600 // 60
                t_today.config(text=f"🕐 今天已陪伴 sensei 工作：{h} 小时 {m} 分")
                secs_known = max(0, int(_now - self._first_launch))
                d, rem = divmod(secs_known, 86400)
                h2, m2 = rem // 3600, rem % 3600 // 60
                if d > 0:
                    t_known.config(text=f"💞 与 sensei 相识：{d} 天 {h2} 小时")
                else:
                    t_known.config(text=f"💞 与 sensei 相识：{h2} 小时 {m2} 分")
        except Exception:
            pass

    def _status_auto_refresh(self):
        """状态窗口每秒自动刷新（面板开着时属性变化能实时看到）。"""
        if self._status_win is not None and self._status_win.winfo_exists():
            self._refresh_status()
            self._status_win.after(1000, self._status_auto_refresh)

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("NORP Pet · 设置")
        win.attributes("-topmost", True)
        win.resizable(False, False)

        tk.Label(win, text="NORP Agent 设置", font=FONT_B,
                 fg="#2b6c8f").grid(row=0, column=0, columnspan=2, pady=(10, 6))

        # 启动方式
        tk.Label(win, text="启动方式：", font=FONT).grid(row=1, column=0,
                                                        sticky="e", padx=8, pady=4)
        mode_var = tk.StringVar(value=self.cfg.get("norp_launch_mode", "source"))
        mode_frame = tk.Frame(win)
        mode_frame.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        tk.Radiobutton(mode_frame, text="源码版（推荐，支持远程任务）",
                       variable=mode_var, value="source", font=FONT).pack(anchor="w")
        tk.Radiobutton(mode_frame, text="打包版 EXE（仅启动）",
                       variable=mode_var, value="exe", font=FONT).pack(anchor="w")

        # 源码目录
        tk.Label(win, text="源码目录：", font=FONT).grid(row=2, column=0,
                                                        sticky="e", padx=8, pady=4)
        src_var = tk.StringVar(value=self.cfg.get("norp_source_dir", ""))
        tk.Entry(win, textvariable=src_var, width=38, font=FONT).grid(row=2, column=1,
                                                                      padx=8, pady=4)

        # 可执行文件路径
        tk.Label(win, text="EXE 路径：", font=FONT).grid(row=3, column=0,
                                                         sticky="e", padx=8, pady=4)
        path_var = tk.StringVar(value=self.cfg.get("norp_exe", ""))
        tk.Entry(win, textvariable=path_var, width=38, font=FONT).grid(row=3, column=1,
                                                                       padx=8, pady=4)

        # 本地 API 地址
        tk.Label(win, text="本地 API 地址：", font=FONT).grid(row=4, column=0,
                                                              sticky="e", padx=8, pady=4)
        api_var = tk.StringVar(value=self.cfg.get("norp_api_url", DEFAULT_API_URL))
        tk.Entry(win, textvariable=api_var, width=38, font=FONT).grid(row=4, column=1,
                                                                      padx=8, pady=4)

        # API 令牌（可选）
        tk.Label(win, text="API 令牌（可选）：", font=FONT).grid(row=5, column=0,
                                                                 sticky="e", padx=8, pady=4)
        token_var = tk.StringVar(value=self.cfg.get("norp_api_token", ""))
        tk.Entry(win, textvariable=token_var, width=38, font=FONT).grid(row=5, column=1,
                                                                        padx=8, pady=4)

        tk.Label(win, text="启动参数模板（EXE 模式用）：", font=FONT).grid(row=6, column=0,
                                                                        sticky="e", padx=8, pady=4)
        args_var = tk.StringVar(value=self.cfg.get("norp_args", ""))
        tk.Entry(win, textvariable=args_var, width=38, font=FONT).grid(row=6, column=1,
                                                                       padx=8, pady=4)
        tk.Label(win, text="支持 {text} 占位符（传命令时替换为用户输入）",
                 font=("Microsoft YaHei UI", 8), fg="#888").grid(row=7, column=1,
                                                                 sticky="w", padx=8)

        idle_var = tk.BooleanVar(value=self.cfg.get("idle_move", True))
        tk.Checkbutton(win, text="允许空闲时随机走动", variable=idle_var,
                       font=FONT).grid(row=8, column=0, columnspan=2, pady=6)

        # ---- 行为开关 ----
        tk.Label(win, text="行为设置", font=FONT_B,
                 fg="#2b6c8f").grid(row=9, column=0, columnspan=2, pady=(8, 2))
        wall_var = tk.BooleanVar(value=self.cfg.get("wall_hide", True))
        tk.Checkbutton(win, text="允许贴墙（拖到屏幕边缘可半隐藏露头）",
                       variable=wall_var, font=FONT).grid(
            row=10, column=0, columnspan=2, sticky="w", padx=24, pady=2)
        auto_wall_var = tk.BooleanVar(value=self.cfg.get("auto_wall_hide", True))
        tk.Checkbutton(win, text="靠近墙面时自动贴墙（走路撞边自动半隐藏）",
                       variable=auto_wall_var, font=FONT).grid(
            row=11, column=0, columnspan=2, sticky="w", padx=24, pady=2)
        free_move_var = tk.BooleanVar(value=self.cfg.get("free_move", True))
        tk.Checkbutton(win, text="允许自由移动（关闭后原地播放动作，不乱跑）",
                       variable=free_move_var, font=FONT).grid(
            row=12, column=0, columnspan=2, sticky="w", padx=24, pady=2)
        dbl_var = tk.BooleanVar(value=self.cfg.get("double_click_open", True))
        tk.Checkbutton(win, text="双击打开 NORP Agent",
                       variable=dbl_var, font=FONT).grid(
            row=13, column=0, columnspan=2, sticky="w", padx=24, pady=2)
        tk.Label(win, text="提示：关闭「允许贴墙」后，自动贴墙也会一并失效",
                 font=("Microsoft YaHei UI", 8), fg="#888").grid(
            row=14, column=0, columnspan=2, padx=8)

        # ---- 小伙伴设置（v2.1）----
        tk.Label(win, text="小伙伴设置", font=FONT_B,
                 fg="#2b6c8f").grid(row=15, column=0, columnspan=2, pady=(8, 2))
        tk.Label(win, text="名字：", font=FONT).grid(row=16, column=0,
                                                    sticky="e", padx=8, pady=4)
        name_var = tk.StringVar(value=self.cfg.get("pet_name", "优香"))
        tk.Entry(win, textvariable=name_var, width=20, font=FONT).grid(
            row=16, column=1, sticky="w", padx=8, pady=4)
        tk.Label(win, text="（台词 / 窗口标题 / 状态面板会自动使用）",
                 font=("Microsoft YaHei UI", 8), fg="#888").grid(
            row=17, column=1, sticky="w", padx=8)
        tk.Label(win, text="💞 相伴天数：%d 天 · ❤ 好感度：%d（Lv.%d）"
                 % (self._companion_days, self._affection,
                    self.affection_level()), font=FONT, fg="#c2576c").grid(
            row=18, column=0, columnspan=2, pady=(4, 2))

        def do_save():
            self.cfg["norp_launch_mode"] = mode_var.get()
            self.cfg["norp_source_dir"] = src_var.get().strip() or DEFAULT_SOURCE_DIR
            self.cfg["norp_exe"] = path_var.get().strip() or DEFAULT_EXE
            self.cfg["norp_api_url"] = api_var.get().strip() or DEFAULT_API_URL
            self.cfg["norp_api_token"] = token_var.get().strip()
            self.cfg["norp_args"] = args_var.get().strip()
            self.cfg["idle_move"] = idle_var.get()
            self.cfg["wall_hide"] = wall_var.get()
            self.cfg["auto_wall_hide"] = auto_wall_var.get()
            self.cfg["free_move"] = free_move_var.get()
            self.cfg["double_click_open"] = dbl_var.get()
            # 小伙伴名字：保存后即时生效（窗口标题跟随）
            _new_name = name_var.get().strip() or "优香"
            if _new_name != self._pet_name:
                self._pet_name = _new_name
                self.cfg["pet_name"] = _new_name
                self.root.title("NORP Pet · %s" % _new_name)
                self.say("我改名叫%s啦～sensei要记住哦！" % _new_name, expr="happy")
            # 关闭「允许贴墙」时若宠物正躲在墙角：立即把它放出来
            if not wall_var.get() and self._hidden_side:
                self._exit_hide("setting")
            save_config(self.cfg)
            self.say("设置已保存～", expr="happy")
            win.destroy()

        btn = tk.Frame(win)
        btn.grid(row=19, column=0, columnspan=2, pady=(4, 10))
        tk.Button(btn, text="保存", font=FONT, width=10, command=do_save).pack(side=tk.LEFT, padx=6)
        tk.Button(btn, text="取消", font=FONT, width=10,
                  command=win.destroy).pack(side=tk.LEFT, padx=6)

    def quit_app(self, user_initiated=True):
        """退出宠物。

        user_initiated=True  —— 用户右键菜单退出：写「用户主动退出」
                                标记，插件看门狗看到后不会自动拉起。
        user_initiated=False —— 插件通过控制端口指挥退出（/pet/quit）：
                                不写标记，看门狗仍可因崩溃自愈而复活。
        """
        self._poll_stop.set()
        self.cfg["pet_x"] = self.root.winfo_x()
        self.cfg["pet_y"] = self.root.winfo_y()
        save_config(self.cfg)
        if user_initiated:
            try:
                with open(os.path.join(_HERE, ".user_quit"), "w",
                          encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
            except Exception:
                pass
        if self._layered is not None:
            try:
                self._layered.close()
            except Exception:
                pass
            self._layered = None
        if self._mouse_hook is not None:
            try:
                self._mouse_hook.close()
            except Exception:
                pass
            self._mouse_hook = None
        self.root.destroy()


def main():
    enable_dpi_aware()   # 先启用 DPI awareness（否则分层窗口真 alpha 渲染会失效）
    hide_console()       # 再隐藏控制台黑窗，然后创建 Tk 窗口
    _lock = acquire_single_instance()   # 单实例锁：已有宠物在跑则直接退出
    if _lock is None:
        sys.exit(0)      # 防多开：桌面上只会有一只宠物
    if sys.platform != "win32":
        print("提示：NORP Pet 的透明窗口特性面向 Windows，其他系统可能显示为色块背景。")
    cfg = load_config()
    PetApp(cfg)


if __name__ == "__main__":
    main()

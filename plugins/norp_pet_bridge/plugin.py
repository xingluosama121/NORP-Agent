# -*- coding: utf-8 -*-
"""
NORP Pet Bridge 插件（v2.0 —— 宠物整个塞进来了）
==================================================
「桌面小伙伴」已完整内嵌为本插件的一部分：

· pet_core/pet.py  —— 宠物本体（原 desktop_pet/pet.py 的插件内嵌版）
· embedded.py      —— 宠物进程托管器：启动拉起 / 退出回收 / 崩溃自愈
  无需再双击任何 vbs/bat，NORP 启动时宠物自动出来，退出时自动收工。

· 注册 5 个工具：pet_say / pet_action / pet_status / pet_launch / pet_quit
  —— 你可以在对话里直接说「让宠物说…」「让宠物开心一点」。
· 生命周期联动：NORP 启动时自动唤出宠物；任务开始/完成/出错/提问时，
  宠物自动冒泡播报。
· 通信方式：插件通过 127.0.0.1:17778 的宠物控制端口（pet.py 内置）指挥宠物。

注：urllib 会被安全审计标记为 WARNING(network)，属预期行为——
本插件必须通过本机回环端口与宠物进程通信，仅监听 127.0.0.1。
"""

PLUGIN_NAME = "NORP Pet Bridge"
PLUGIN_PUBLISHER = "norp-pet"
PLUGIN_VERSION = "2.1.0"
PLUGIN_DESCRIPTION = "桌面小伙伴已完整内嵌插件：启动自动唤出、退出自动回收、崩溃自愈；含好感度/陪伴天数/名字自定义/节日问候养成系统"

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

# 把插件目录加入搜索路径，加载内嵌组件（embedded.py / pet_core）
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import embedded

PET_CONTROL_PORT = 17778
PET_LAUNCHED_BY_PLUGIN = "pet_launched_by_plugin"   # storage 标记

# ----------------------------------------------------------------------
# 宠物守护线程（watchdog）—— 不依赖 on_agent_init 钩子
# ----------------------------------------------------------------------
# 之前版本依赖 on_agent_init 钩子在 NORP 启动时唤出宠物，但打包版
# exe 不会触发该钩子，导致宠物"又没自己出来"。现在改为：
#   ① 插件被加载（import）时立即检查并拉起宠物 —— 只要插件加载成功
#      宠物就必然出来，这条链路与钩子无关，已验证稳定；
#   ② 后台守护线程每 15 秒检查一次，宠物意外退出/崩溃时自动复活。
_WATCHDOG_INTERVAL = 15.0       # 检查间隔（秒）
_user_quit = False              # 用户主动退出标记（True 时不自动拉起）
_watchdog_started = False
_watchdog_lock = threading.Lock()
_USER_QUIT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "pet_core", ".user_quit")


def _user_quit_file_exists():
    """右键退出会写 .user_quit 文件；存在即视为用户主动退出。"""
    return os.path.exists(_USER_QUIT_FILE)


def _clear_user_quit_file():
    try:
        if os.path.exists(_USER_QUIT_FILE):
            os.remove(_USER_QUIT_FILE)
    except Exception:
        pass


def _watchdog_loop():
    """后台守护：宠物意外消失时自动重新拉起。

    尊重用户主动退出：内存标记（pet_quit 工具）或文件标记
    （右键退出）存在时，不自动拉起。
    """
    while True:
        time.sleep(_WATCHDOG_INTERVAL)
        try:
            if _user_quit or _user_quit_file_exists():
                continue
            if not embedded.is_alive():
                ok, msg = embedded.launch()
                if not ok:
                    print("[NORP Pet] watchdog 拉起宠物失败:", msg)
        except Exception:
            pass


def _start_watchdog():
    """启动守护线程（幂等，多次调用只启一个）。"""
    global _watchdog_started
    with _watchdog_lock:
        if _watchdog_started:
            return
        _watchdog_started = True
    t = threading.Thread(target=_watchdog_loop, daemon=True,
                         name="norp-pet-watchdog")
    t.start()


def _bootstrap_pet():
    """插件加载即唤出宠物（幂等：已在跑则跳过）。

    NORP 全新启动（插件重新加载）时清除上次的退出标记并唤出宠物；
    这与「右键退出本次不再拉起」不冲突 —— 退出标记在本次进程内
    通过内存/文件双通道生效，重启后视为新的开始。
    """
    _clear_user_quit_file()
    try:
        if not embedded.is_alive():
            ok, msg = embedded.launch()
            if not ok:
                print("[NORP Pet] 插件加载时唤出宠物失败:", msg)
    except Exception:
        pass


# ── 插件被加载时立即执行：唤出宠物 + 启动守护线程 ──
_bootstrap_pet()
_start_watchdog()


# ----------------------------------------------------------------------
# 与宠物的控制通道（127.0.0.1:17778）
# ----------------------------------------------------------------------
def _pet_request(method, path, body=None, timeout=3):
    """向宠物控制端口发起请求；失败返回 None。"""
    url = "http://127.0.0.1:%d%s" % (PET_CONTROL_PORT, path)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _pet_online():
    """宠物控制端口是否在线。"""
    return _pet_request("GET", "/pet/status") is not None


def _pet_say(text, expr=None, duration=None):
    body = {"text": text}
    if expr:
        body["expr"] = expr
    if duration:
        body["duration"] = duration
    return _pet_request("POST", "/pet/say", body)


def _pet_action(expr):
    return _pet_request("POST", "/pet/action", {"expr": expr})


def _pet_quit():
    return _pet_request("POST", "/pet/quit", {})


# ----------------------------------------------------------------------
# 工具注册（AI 可直接调用）
# ----------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "pet_say",
            "description": "让桌面小伙伴（NORP Pet）冒泡说一句话。适合给用户一个可爱的提示、播报任务进度或表达情绪。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要宠物说的话（一句话，别太长）"
                    },
                    "expr": {
                        "type": "string",
                        "enum": ["idle", "happy", "talk", "sleep", "sad"],
                        "description": "宠物表情：happy=开心 / sad=难过 / talk=说话 / sleep=睡觉 / idle=发呆"
                    },
                    "duration": {
                        "type": "number",
                        "description": "气泡停留秒数，默认 2.6，最大 20"
                    }
                },
                "required": ["text"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pet_action",
            "description": "让桌面小伙伴（NORP Pet）做出指定表情动作，不说话。",
            "parameters": {
                "type": "object",
                "properties": {
                    "expr": {
                        "type": "string",
                        "enum": ["idle", "happy", "talk", "sleep", "sad"],
                        "description": "宠物表情：happy=开心 / sad=难过 / talk=说话 / sleep=睡觉 / idle=发呆"
                    }
                },
                "required": ["expr"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pet_status",
            "description": "查询桌面小伙伴（NORP Pet）是否在线、当前表情、NORP 连接状态。",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pet_launch",
            "description": "启动桌面小伙伴（NORP Pet）。宠物未运行时调用，它会安静地出现在桌面右下角。",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pet_quit",
            "description": "让桌面小伙伴（NORP Pet）退出。它会记住当前位置，下次启动还在原地。",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        }
    }
]


def execute(tool_name: str, args: dict, context) -> str:
    """处理 AI 对宠物相关工具的调用。"""
    global _user_quit
    try:
        if tool_name == "pet_say":
            if not _pet_online():
                return "❌ 宠物未运行，请先调用 pet_launch 启动它"
            text = args.get("text", "")
            ok = _pet_say(text, args.get("expr"), args.get("duration"))
            return "✅ 宠物已收到，正在冒泡说话" if ok else "❌ 控制指令发送失败（宠物掉线？）"

        if tool_name == "pet_action":
            if not _pet_online():
                return "❌ 宠物未运行，请先调用 pet_launch 启动它"
            ok = _pet_action(args.get("expr", "idle"))
            return "✅ 宠物已做出表情" if ok else "❌ 控制指令发送失败（宠物掉线？）"

        if tool_name == "pet_status":
            data = _pet_request("GET", "/pet/status")
            if data is None:
                return "🐾 宠物当前**不在线**。可以用 pet_launch 把它叫出来。"
            state = data.get("norp_state", "?")
            ver = data.get("norp_version", "")
            state_map = {
                "api": "🟢 已连接（本地 API）",
                "exe": "🟡 运行中（EXE，无 API）",
                "exe_new": "🟡 运行中（新版 EXE，无 API）",
                "none": "🔴 未运行",
            }
            line = "  • NORP 连接状态：%s" % state_map.get(state, state)
            if ver:
                line += "（%s）" % ver
            return ("🐾 宠物在线！\n"
                    "  • 表情：%s\n" % data.get("expr", "?") + line)

        if tool_name == "pet_launch":
            _user_quit = False            # 用户主动唤出 → 看门狗接管守护
            _clear_user_quit_file()       # 清除右键退出标记
            if embedded.is_alive():
                return "✅ 宠物已经在桌面上啦，无需重复启动"
            ok, msg = embedded.launch()
            if ok:
                context.storage[PET_LAUNCHED_BY_PLUGIN] = True
                return "✅ 正在把宠物叫出来～它马上出现在桌面右下角"
            return "❌ 宠物启动失败：%s" % msg

        if tool_name == "pet_quit":
            _user_quit = True             # 用户主动退出 → 看门狗不再自动拉起
            try:
                with open(_USER_QUIT_FILE, "w", encoding="utf-8") as f:
                    f.write("plugin-quit")
            except Exception:
                pass
            if not embedded.is_alive():
                return "✅ 宠物本来就没在运行"
            embedded.stop()
            context.storage[PET_LAUNCHED_BY_PLUGIN] = False
            return "✅ 宠物已退出，位置已记住"

        return "Unknown tool: %s" % tool_name
    except Exception as e:
        return "❌ 工具执行异常: %s" % e


# ----------------------------------------------------------------------
# 启动宠物（内嵌托管：直接拉起 pet_core/pet.py，无黑窗、无需 vbs）
# ----------------------------------------------------------------------
def _launch_pet(context) -> bool:
    ok, _ = embedded.launch()
    if ok:
        return True
    # 兜底：若 pet_core 缺失，回退到桌面独立版 vbs（老版本兼容）
    candidates = []
    root = getattr(context, "project_root", None)
    if root:
        candidates.append(os.path.join(root, "desktop_pet", "启动桌面小伙伴.vbs"))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "..", "..", "..", "desktop_pet",
                                   "启动桌面小伙伴.vbs"))
    for path in candidates:
        path = os.path.normpath(path)
        if os.path.exists(path):
            try:
                os.startfile(path)
                return True
            except Exception:
                continue
    return False


# ----------------------------------------------------------------------
# L1 — 生命周期钩子
# ----------------------------------------------------------------------
def on_agent_init(context):
    """NORP 启动时自动唤出宠物（若未运行）；宠物意外死亡则自动复活。

    注意：本钩子可能在每次会话创建时触发，因此若用户在本会话中主动
    退出了宠物（_user_quit=True），则不再强行唤出 —— 尊重用户选择；
    只有全新启动（插件重新加载，_user_quit 复位为 False）才会自动唤出。
    """
    if _user_quit or _user_quit_file_exists():
        context.logger.info("用户已主动退出宠物，跳过自动唤出")
        return
    context.storage[PET_LAUNCHED_BY_PLUGIN] = False
    if embedded.is_alive():
        context.logger.info("宠物已在运行，跳过自动启动")
        _pet_say("NORP 醒啦！我来陪你干活～(≧▽≦)", "happy")
    elif _launch_pet(context):
        context.storage[PET_LAUNCHED_BY_PLUGIN] = True
        context.logger.info("已自动唤出桌面小伙伴（内嵌 pet_core）")
    else:
        context.logger.warn("宠物启动失败，请检查 pet_core/pet.py")


def on_agent_shutdown(context):
    """NORP 退出时，若宠物是由本插件唤出的，则一并退出。"""
    if context.storage.get(PET_LAUNCHED_BY_PLUGIN):
        _pet_say("NORP 去休息啦，我也睡啦～晚安！", "sleep", 3)
        embedded.stop()


# ----------------------------------------------------------------------
# L2 — 任务钩子：宠物自动播报
# ----------------------------------------------------------------------
def on_task_start(task_text: str, context):
    _pet_say("收到任务！(≧▽≦) 正在努力干活…", "happy")


def on_task_done(summary: str, final_reply: str, context):
    _pet_say("任务完成啦！٩(◕‿◕)۶ 干得漂亮～", "happy")


def on_task_error(error_msg: str, context):
    _pet_say("呜…任务出错了 (´;ω;`)" + (error_msg[:40] or ""), "sad", 5)


def on_task_stopped(context):
    _pet_say("任务被你停掉啦～随时再叫我！", "talk")


def on_task_timeout(elapsed: float, context):
    _pet_say("任务跑太久超时了…要不要换个思路？(・_・;)", "talk")


def on_user_input_required(question: str, context):
    """NORP 提问时，宠物先冒泡提醒用户看界面。"""
    _pet_say("NORP 在等你确认哦：" + question[:60], "talk", 6)


# ----------------------------------------------------------------------
# L3 — 步骤钩子（保持轻量）
# ----------------------------------------------------------------------
def before_step(step: int, messages: list, context):
    return messages


def after_step(step: int, reasoning: str, content: str,
               tool_calls: list, context):
    pass


def before_tool_call(tool_name: str, args: dict, context):
    return args


def after_tool_call(tool_name: str, args: dict, result: str, context):
    return result


# ----------------------------------------------------------------------
# L4 — 流式钩子（忽略，宠物自己会轮询 NORP 事件）
# ----------------------------------------------------------------------
def on_reasoning(token: str, context):
    pass


def on_content(token: str, context):
    pass


def on_event(event_type: str, data: str, context):
    pass


def on_usage_update(usage: dict, context):
    pass

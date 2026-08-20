# -*- coding: utf-8 -*-
"""
NORP Task Board 插件（对齐 DeepSeek Harness 的 dsh-task-board）
================================================================
多列看板任务管理 + 5 段 cron 定时任务：

· 看板管理    —— 默认四列 backlog / todo / doing / done，任务可在列间流转
· 持久化      —— 任务保存到 app_dir/norp_task_board/tasks.json（跨会话保留）
· cron 定时   —— 支持 5 段 cron（分 时 日 月 周），如 "0 23 * * *"
· 到点提醒    —— 定时任务到点后，通过 before_step 钩子把提醒注入当前会话，
                让 Agent（和用户）知道该任务已到点

限制说明（诚实告知）：
  1. 定时检查发生在 Agent 的每个 ReAct 步骤（before_step）——没有任务在跑时
     不会主动唤醒，这与 dsh-task-board 的「浏览器端需标签页打开」类似。
  2. 到点后是否执行真实任务取决于 Agent 当前会话，本插件只负责提醒，不驱动新会话。
"""

PLUGIN_NAME = "NORP Task Board"
PLUGIN_PUBLISHER = "norp-community"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = (
    "多列看板任务管理 + 5 段 cron 定时任务；到点后自动把提醒注入会话。"
    "任务持久化到本地 JSON，跨会话保留。"
)

import json
import os
import re
import threading
from datetime import datetime, timedelta

DEFAULT_COLUMNS = ["backlog", "todo", "doing", "done"]

# 模块级状态（插件加载一次，跨工具调用保留）
_DATA_LOCK = threading.Lock()
_TASKS = None
_SEQ = 0
_FIRED = {}  # (task_id, minute_key) -> True，避免同一分钟重复提醒


# ----------------------------------------------------------------------
# 数据持久化
# ----------------------------------------------------------------------
def _config_dir(context):
    d = os.path.join(context.app_dir, "norp_task_board")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _data_path(context):
    return os.path.join(_config_dir(context), "tasks.json")


def _load(context):
    global _TASKS, _SEQ
    with _DATA_LOCK:
        if _TASKS is None:
            p = _data_path(context)
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        _TASKS = json.load(f).get("tasks", [])
                except Exception:
                    _TASKS = []
            else:
                _TASKS = []
            _SEQ = 0
            for t in _TASKS:
                m = re.match(r"t-(\d+)", t.get("id", "") or "")
                if m:
                    _SEQ = max(_SEQ, int(m.group(1)))
        return _TASKS


def _save(context):
    global _TASKS
    with _DATA_LOCK:
        try:
            p = _data_path(context)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"tasks": _TASKS}, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False


def _find(context, task_id):
    for t in _load(context):
        if t.get("id") == task_id:
            return t
    return None


# ----------------------------------------------------------------------
# cron 解析（5 段：分 时 日 月 周；周 0=周日）
# ----------------------------------------------------------------------
def _match_field(field, value, lo, hi):
    field = (field or "").strip()
    if field in ("*", "?"):
        return True
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s) if step_s.isdigit() else 1
            if base == "*":
                if (value - lo) % step == 0:
                    return True
                continue
            part = base
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                a, b = int(a), int(b)
            except ValueError:
                continue
            if a <= value <= b:
                if step > 1:
                    if (value - a) % step == 0:
                        return True
                else:
                    return True
        else:
            try:
                if int(part) == value:
                    return True
            except ValueError:
                continue
    return False


def _valid_cron(cron):
    """返回 (ok, errmsg)。"""
    if not cron or not cron.strip():
        return False, "cron 为空"
    fields = cron.split()
    if len(fields) != 5:
        return False, "cron 需要 5 段（分 时 日 月 周），如 '0 23 * * *'"
    try:
        for part in fields:
            # 校验每段是否由合法字符组成
            if not re.fullmatch(r"[0-9*/,\-]+", part):
                return False, "非法 cron 字段：%s" % part
    except Exception:
        return False, "cron 解析失败"
    return True, ""


def _cron_matches(cron, dt):
    ok, _ = _valid_cron(cron)
    if not ok:
        return False
    minute, hour, day, month, weekday = cron.split()
    return (
        _match_field(minute, dt.minute, 0, 59)
        and _match_field(hour, dt.hour, 0, 23)
        and _match_field(day, dt.day, 1, 31)
        and _match_field(month, dt.month, 1, 12)
        and _match_field(weekday, (dt.weekday() + 1) % 7, 0, 6)
    )


def _next_run(cron, after=None):
    ok, _ = _valid_cron(cron)
    if not ok:
        return None
    after = after or datetime.now()
    dt = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=400)
    while dt <= limit:
        if _cron_matches(cron, dt):
            return dt
        dt += timedelta(minutes=1)
    return None


def _describe_cron(cron):
    ok, err = _valid_cron(cron)
    if not ok:
        return "❌ %s" % err
    nxt = _next_run(cron)
    if nxt is None:
        return "（未来 400 天内无匹配）"
    return "下次触发：%s" % nxt.strftime("%Y-%m-%d %H:%M")


# ----------------------------------------------------------------------
# 工具定义
# ----------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "board_list",
            "description": "列出看板任务。可按列筛选，或按关键字过滤标题/描述/标签。",
            "parameters": {
                "type": "object",
                "properties": {
                    "column": {"type": "string", "description": "只列某列，如 todo；缺省全部"},
                    "query": {"type": "string", "description": "按标题/描述/标签模糊过滤"}
                },
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "board_add",
            "description": "在看板中新增一个任务。可附带优先级、标签与 cron 定时。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "任务标题"},
                    "description": {"type": "string", "description": "任务详情（可选）"},
                    "column": {"type": "string", "description": "列：backlog/todo/doing/done，默认 todo"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"], "description": "优先级，默认 medium"},
                    "tags": {"type": "string", "description": "逗号分隔的标签"},
                    "cron": {"type": "string", "description": "5 段 cron 定时（分 时 日 月 周），如 0 23 * * *（可选）"}
                },
                "required": ["title"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "board_move",
            "description": "把任务移动到指定列。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "任务 ID"},
                    "column": {"type": "string", "description": "目标列：backlog/todo/doing/done"}
                },
                "required": ["id", "column"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "board_update",
            "description": "更新任务字段（标题/描述/优先级/标签/cron/列）。未提供的字段保持不变。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "任务 ID"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                    "tags": {"type": "string", "description": "逗号分隔的标签"},
                    "cron": {"type": "string", "description": "5 段 cron；传空字符串清除定时"},
                    "column": {"type": "string"}
                },
                "required": ["id"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "board_delete",
            "description": "删除一个任务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "任务 ID"}
                },
                "required": ["id"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "board_next_run",
            "description": "校验一个 cron 表达式并计算它下一次触发的时间。",
            "parameters": {
                "type": "object",
                "properties": {
                    "cron": {"type": "string", "description": "5 段 cron 表达式"}
                },
                "required": ["cron"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "board_columns",
            "description": "列出看板各列及其任务数量（看板概览）。",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        }
    }
]


# ----------------------------------------------------------------------
# 工具调度
# ----------------------------------------------------------------------
def execute(tool_name, args, context):
    try:
        if tool_name == "board_list":
            return _cmd_list(context, args.get("column"), args.get("query"))
        if tool_name == "board_add":
            return _cmd_add(context, args)
        if tool_name == "board_move":
            return _cmd_move(context, args.get("id"), args.get("column"))
        if tool_name == "board_update":
            return _cmd_update(context, args)
        if tool_name == "board_delete":
            return _cmd_delete(context, args.get("id"))
        if tool_name == "board_next_run":
            return _cmd_next_run(context, args.get("cron"))
        if tool_name == "board_columns":
            return _cmd_columns(context)
        return "Unknown tool: %s" % tool_name
    except Exception as e:
        return "❌ 工具执行异常：%s" % e


# ----------------------------------------------------------------------
# 各工具实现
# ----------------------------------------------------------------------
def _is_due(task, now):
    cron = task.get("cron")
    if not cron or task.get("column") == "done":
        return False
    return _cron_matches(cron, now)


def _cmd_list(context, column, query):
    tasks = _load(context)
    if column:
        tasks = [t for t in tasks if t.get("column") == column]
    if query:
        q = query.lower()
        tasks = [t for t in tasks if
                 q in (t.get("title", "") or "").lower()
                 or q in (t.get("description", "") or "").lower()
                 or q in " ".join(t.get("tags", []) if isinstance(t.get("tags"), list) else str(t.get("tags", "")).split(",")).lower()]
    if not tasks:
        return "📋 没有匹配的任务。用 board_add 新增一个。"
    now = datetime.now()
    prio_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    lines = ["📋 **任务看板**（%d 条）\n" % len(tasks)]
    for t in tasks:
        icon = prio_icon.get(t.get("priority"), "⚪")
        cron = " | ⏰%s" % t["cron"] if t.get("cron") else ""
        due = " 🔔已到点" if _is_due(t, now) else ""
        tags = "".join(" #%s" % x for x in t.get("tags", []) if x)
        lines.append("  %s `%s` **%s** [%s]%s%s%s" % (
            icon, t.get("id"), t.get("title"), t.get("column"), cron, due, tags))
        if t.get("description"):
            lines.append("      └ %s" % t["description"][:100])
    return "\n".join(lines)


def _cmd_add(context, args):
    global _SEQ
    title = (args.get("title") or "").strip()
    if not title:
        return "❌ 标题不能为空"
    column = args.get("column") or "todo"
    if column not in DEFAULT_COLUMNS:
        return "❌ 未知列：%s（可选：%s）" % (column, ", ".join(DEFAULT_COLUMNS))
    cron = (args.get("cron") or "").strip()
    if cron:
        ok, err = _valid_cron(cron)
        if not ok:
            return "❌ %s" % err
    tags = [x.strip() for x in (args.get("tags") or "").split(",") if x.strip()]
    with _DATA_LOCK:
        _SEQ += 1
        task_id = "t-%d" % _SEQ
        now = datetime.now().isoformat(timespec="seconds")
        task = {
            "id": task_id,
            "title": title,
            "description": (args.get("description") or "").strip(),
            "column": column,
            "priority": args.get("priority") or "medium",
            "tags": tags,
            "cron": cron,
            "created": now,
            "updated": now,
            "completed_at": now if column == "done" else "",
        }
        _load(context).append(task)
    _save(context)
    extra = " | ⏰%s" % cron if cron else ""
    return "✅ 已新增任务 `%s` **%s** → %s%s" % (task_id, title, column, extra)


def _cmd_move(context, task_id, column):
    if column not in DEFAULT_COLUMNS:
        return "❌ 未知列：%s" % column
    t = _find(context, task_id)
    if not t:
        return "❌ 未找到任务：%s" % task_id
    t["column"] = column
    t["updated"] = datetime.now().isoformat(timespec="seconds")
    if column == "done":
        t["completed_at"] = t["updated"]
    else:
        t["completed_at"] = ""
    _save(context)
    return "✅ 任务 `%s` 已移动到 **%s**" % (task_id, column)


def _cmd_update(context, args):
    task_id = args.get("id")
    t = _find(context, task_id)
    if not t:
        return "❌ 未找到任务：%s" % task_id
    if "title" in args and args["title"]:
        t["title"] = args["title"].strip()
    if "description" in args:
        t["description"] = (args["description"] or "").strip()
    if "priority" in args and args["priority"]:
        t["priority"] = args["priority"]
    if "tags" in args and args["tags"] is not None:
        t["tags"] = [x.strip() for x in args["tags"].split(",") if x.strip()]
    if "cron" in args:
        cron = (args["cron"] or "").strip()
        if cron:
            ok, err = _valid_cron(cron)
            if not ok:
                return "❌ %s" % err
        t["cron"] = cron
    if "column" in args and args["column"]:
        if args["column"] not in DEFAULT_COLUMNS:
            return "❌ 未知列：%s" % args["column"]
        t["column"] = args["column"]
        if args["column"] == "done":
            t["completed_at"] = datetime.now().isoformat(timespec="seconds")
        else:
            t["completed_at"] = ""
    t["updated"] = datetime.now().isoformat(timespec="seconds")
    _save(context)
    return "✅ 任务 `%s` 已更新" % task_id


def _cmd_delete(context, task_id):
    t = _find(context, task_id)
    if not t:
        return "❌ 未找到任务：%s" % task_id
    global _TASKS
    with _DATA_LOCK:
        _TASKS = [x for x in _TASKS if x.get("id") != task_id]
    _save(context)
    return "✅ 已删除任务 `%s`" % task_id


def _cmd_next_run(context, cron):
    ok, err = _valid_cron(cron)
    if not ok:
        return "❌ %s" % err
    return "⏰ cron `%s` %s" % (cron, _describe_cron(cron))


def _cmd_columns(context):
    tasks = _load(context)
    lines = ["🗂️ **看板列概览**\n"]
    for col in DEFAULT_COLUMNS:
        n = sum(1 for t in tasks if t.get("column") == col)
        due = sum(1 for t in tasks if t.get("column") == col and t.get("cron"))
        bar = "█" * min(n, 20)
        lines.append("  • **%s**：%d 个任务 %s%s" % (
            col, n, bar, ("（含定时 %d）" % due if due else "")))
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 钩子：cron 到点提醒
# ----------------------------------------------------------------------
def _prune_fired(now):
    global _FIRED
    cutoff = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
    _FIRED = {k: v for k, v in _FIRED.items()
              if len(k) == 2 and k[1] >= cutoff}


def _collect_due(context):
    tasks = _load(context)
    now = datetime.now()
    _prune_fired(now)
    minute_key = now.strftime("%Y-%m-%d %H:%M")
    due = []
    for t in tasks:
        cron = t.get("cron")
        if not cron or t.get("column") in ("done",):
            continue
        if not _cron_matches(cron, now):
            continue
        key = (t.get("id"), minute_key)
        if key in _FIRED:
            continue
        _FIRED[key] = True
        due.append(t)
    return due


def before_step(step, messages, context):
    due = _collect_due(context)
    if not due:
        # 返回 None 表示「不修改」，且不抢占本钩子，避免阻断其他插件的 before_step
        return None
    note_lines = ["[NORP 任务看板] 以下定时任务现已到点："]
    for t in due:
        note_lines.append("  • %s（%s）" % (t.get("title"), t.get("id")))
    note_lines.append("请在不打断当前工作的前提下，酌情处理或提醒用户。")
    note = "\n".join(note_lines)
    return list(messages) + [{"role": "system", "content": note}]


def on_agent_init(context):
    n = len(_load(context))
    context.logger.info("NORP Task Board 就绪，%d 个任务" % n)


def on_agent_shutdown(context):
    _save(context)

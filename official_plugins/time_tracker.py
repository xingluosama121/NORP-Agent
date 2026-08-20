# ──────────────────────────────────────────────────────────────
# Plugin: Time Tracker
# Publisher: xingluosama
# Version: 1.0.0
# Description: 时间追踪与生产力报告：任务计时、工具调用耗时统计、
#   效率趋势分析。
# ──────────────────────────────────────────────────────────────

PLUGIN_NAME = "Time Tracker"
PLUGIN_PUBLISHER = "xingluosama"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "时间追踪与生产力报告：任务计时、工具调用耗时、效率趋势分析。"

import os
import time
from datetime import datetime, timedelta

# ── 1. 工具注册 ────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "time_report",
            "description": (
                "显示当前会话的时间追踪和生产力报告。"
                "包括：当前任务耗时、历史任务耗时对比、工具调用分布、"
                "最耗时的操作、平均任务时长、效率趋势等。"
                "当用户询问「用了多少时间」「效率如何」「做了多少任务」时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False
            }
        }
    }
]

# ── 2. 时间格式化工具 ──────────────────────────────────────────

def _fmt_duration(seconds: float) -> str:
    """将秒数格式化为人类可读的字符串。"""
    if seconds < 60:
        return f"{seconds:.1f}秒"
    if seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}分{s}秒"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}时{m}分"


def _fmt_ago(timestamp: float) -> str:
    """格式化为「X 分钟前」风格。"""
    diff = time.time() - timestamp
    if diff < 60:
        return "刚刚"
    if diff < 3600:
        return f"{int(diff // 60)} 分钟前"
    if diff < 86400:
        return f"{int(diff // 3600)} 小时前"
    return f"{int(diff // 86400)} 天前"


# ── 3. 工具执行函数 ────────────────────────────────────────────

def execute(tool_name: str, args: dict, context) -> str:
    if tool_name == "time_report":
        return _generate_report(context)

    return f"Unknown tool: {tool_name}"


def _generate_report(context) -> str:
    """生成时间追踪报告。"""
    s = context.storage

    # ── 基础数据 ──
    session_start = s.get("session_start", time.time())
    total_elapsed = time.time() - session_start
    task_count = s.get("task_count", 0)
    done_count = s.get("done_count", 0)
    error_count = s.get("error_count", 0)
    paused_seconds = s.get("paused_seconds", 0.0)
    active_seconds = total_elapsed - paused_seconds

    # ── 任务耗时详情 ──
    task_durations = s.get("task_durations", [])
    current_task_start = s.get("current_task_start", 0)

    # ── 工具调用耗时 ──
    tool_total_time = s.get("tool_total_time", 0.0)
    tool_breakdown = s.get("tool_breakdown", {})  # tool_name -> (count, total_time)
    tool_calls_total = s.get("tool_calls_total", 0)

    # ── 构建报告 ──
    lines = [
        "⏱️ **时间追踪报告**",
        "",
        f"📅 会话开始: {datetime.fromtimestamp(session_start).strftime('%Y-%m-%d %H:%M:%S')}",
        f"⏳ 总运行时长: {_fmt_duration(total_elapsed)}",
        f"⚡ 活跃时长: {_fmt_duration(active_seconds)}",
    ]

    if paused_seconds > 0:
        lines.append(f"⏸️  暂停时长: {_fmt_duration(paused_seconds)}")
        lines.append(f"📊 活跃率: {active_seconds / max(total_elapsed, 1) * 100:.0f}%")

    lines.append("")

    # ── 任务统计 ──
    lines.append("📋 **任务统计**:")
    lines.append(f"  • 总任务: {task_count}")
    lines.append(f"  • 已完成: {done_count}")
    lines.append(f"  • 失败: {error_count}")

    if task_durations:
        avg_duration = sum(task_durations) / len(task_durations)
        max_duration = max(task_durations)
        min_duration = min(task_durations)
        lines.append(f"  • 平均耗时: {_fmt_duration(avg_duration)}")
        lines.append(f"  • 最长任务: {_fmt_duration(max_duration)}")
        lines.append(f"  • 最短任务: {_fmt_duration(min_duration)}")

        # 最近 5 个任务
        recent = task_durations[-5:]
        lines.append("")
        lines.append("📜 **最近任务耗时**:")
        for i, dur in enumerate(recent, 1):
            label = f"任务 #{task_count - len(recent) + i}"
            bar_len = min(int(dur / max(max_duration, 1) * 20), 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            lines.append(f"  {label}: {bar} {_fmt_duration(dur)}")

    lines.append("")

    # ── 工具调用统计 ──
    if tool_calls_total > 0:
        lines.append("🔧 **工具调用统计**:")
        lines.append(f"  • 总调用次数: {tool_calls_total}")
        lines.append(f"  • 总耗时: {_fmt_duration(tool_total_time)}")

        if tool_breakdown:
            sorted_tools = sorted(
                tool_breakdown.items(),
                key=lambda x: x[1].get("total_time", 0) if isinstance(x[1], dict) else x[1],
                reverse=True
            )

            # 判断存储格式（兼容旧格式）
            if sorted_tools and isinstance(sorted_tools[0][1], dict):
                lines.append("")
                lines.append("📊 **各工具耗时分布**:")
                for tool_name, data in sorted_tools[:8]:
                    count = data.get("count", 0)
                    t = data.get("total_time", 0)
                    pct = (t / max(tool_total_time, 0.001)) * 100
                    bar_len = max(int(pct / 5), 1)
                    bar = "▓" * bar_len
                    lines.append(f"  {tool_name}: {bar} {pct:.0f}% ({count}次, {_fmt_duration(t)})")
            else:
                lines.append(f"  • 工具种类: {len(tool_breakdown)}")

    lines.append("")

    # ── 效率评分 ──
    if task_count > 0:
        success_rate = done_count / max(task_count, 1) * 100
        lines.append("🏆 **效率评分**:")
        lines.append(f"  • 成功率: {success_rate:.0f}%")

        if task_durations:
            recent_avg = sum(task_durations[-3:]) / min(len(task_durations[-3:]), 3) if len(task_durations) >= 3 else sum(task_durations) / len(task_durations)
            all_avg = sum(task_durations) / len(task_durations)
            if all_avg > 0 and recent_avg < all_avg * 0.8:
                lines.append(f"  • 趋势: 📈 越来越快！(最近平均 {_fmt_duration(recent_avg)} vs 总平均 {_fmt_duration(all_avg)})")
            elif all_avg > 0 and recent_avg > all_avg * 1.2:
                lines.append(f"  • 趋势: 📉 近期任务耗时增加 (最近平均 {_fmt_duration(recent_avg)} vs 总平均 {_fmt_duration(all_avg)})")
            else:
                lines.append(f"  • 趋势: ➡️ 稳定")

        # 效率值（任务/小时）
        tasks_per_hour = task_count / max(active_seconds / 3600, 0.001)
        lines.append(f"  • 效率: {tasks_per_hour:.1f} 任务/小时")

    return "\n".join(lines)


# ── 4. 钩子：生命周期 ──────────────────────────────────────────

def on_agent_init(context):
    """初始化时间追踪器。"""
    s = context.storage
    s["session_start"] = time.time()
    s["task_count"] = 0
    s["done_count"] = 0
    s["error_count"] = 0
    s["paused_seconds"] = 0.0
    s["task_durations"] = []
    s["tool_calls_total"] = 0
    s["tool_total_time"] = 0.0
    s["tool_breakdown"] = {}
    s["_tool_start_times"] = {}
    s["_pause_start"] = 0.0
    context.logger.info("Time Tracker plugin loaded ⏱️")


def on_agent_shutdown(context):
    """会话结束时输出总结。"""
    s = context.storage
    elapsed = time.time() - s.get("session_start", time.time())
    context.logger.info(
        f"Session ended | {s.get('task_count', 0)} tasks | "
        f"{_fmt_duration(elapsed)} total | "
        f"{s.get('tool_calls_total', 0)} tool calls"
    )


# ── 5. 钩子：任务生命周期 ──────────────────────────────────────

def on_task_start(task_text: str, context):
    """新任务开始，记录起始时间。"""
    s = context.storage
    s["task_count"] = s.get("task_count", 0) + 1
    s["current_task_start"] = time.time()
    s["current_task_text"] = task_text[:100]
    context.logger.info(f"Task #{s['task_count']} started: {task_text[:60]}...")


def on_task_done(summary: str, final_reply: str, context):
    """任务完成，记录耗时。"""
    s = context.storage
    s["done_count"] = s.get("done_count", 0) + 1

    start_time = s.get("current_task_start", 0)
    if start_time > 0:
        duration = time.time() - start_time
        durations = s.get("task_durations", [])
        durations.append(duration)
        s["task_durations"] = durations
        context.logger.info(
            f"Task #{s['task_count']} done in {_fmt_duration(duration)}"
        )
    s["current_task_start"] = 0


def on_task_error(error_msg: str, context):
    """任务失败，仍记录耗时。"""
    s = context.storage
    s["error_count"] = s.get("error_count", 0) + 1

    duration = None  # initialised before use
    start_time = s.get("current_task_start", 0)
    if start_time > 0:
        duration = time.time() - start_time
        durations = s.get("task_durations", [])
        durations.append(duration)
        s["task_durations"] = durations

    s["current_task_start"] = 0
    context.logger.error(
        f"Task #{s['task_count']} failed after "
        f"{_fmt_duration(duration) if duration is not None else '?'}"
    )


# ── 6. 钩子：工具调用计时 ──────────────────────────────────────

def before_tool_call(tool_name: str, args: dict, context):
    """记录工具调用开始时间。"""
    s = context.storage
    s["tool_calls_total"] = s.get("tool_calls_total", 0) + 1

    start_times = s.get("_tool_start_times", {})
    start_times[tool_name] = time.time()
    s["_tool_start_times"] = start_times

    return args  # 放行


def after_tool_call(tool_name: str, args: dict, result: str, context):
    """记录工具调用耗时。"""
    s = context.storage
    start_times = s.get("_tool_start_times", {})
    start = start_times.pop(tool_name, None)

    if start is not None:
        duration = time.time() - start
        s["tool_total_time"] = s.get("tool_total_time", 0.0) + duration

        breakdown = s.get("tool_breakdown", {})
        if tool_name not in breakdown:
            breakdown[tool_name] = {"count": 0, "total_time": 0.0}
        breakdown[tool_name]["count"] += 1
        breakdown[tool_name]["total_time"] += duration
        s["tool_breakdown"] = breakdown

    return result  # 不修改返回值

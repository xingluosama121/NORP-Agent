# ──────────────────────────────────────────────────────────────
# Plugin: Task Logger
# Publisher: VibeCodingCommunity
# Version: 1.0.0
# Description: 一个完整的插件示例，演示如何：
#     1. 注册自定义工具（列出历史 / 导出日志）
#     2. 使用全部 4 层 15 个钩子追踪 Agent 行为
#     3. 利用 PluginContext 读写持久化存储
#
# 将此文件放入 plugin_dirs 中的任意目录即可激活。
# ──────────────────────────────────────────────────────────────

PLUGIN_NAME = "Task Logger"
PLUGIN_PUBLISHER = "VibeCodingCommunity"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = (
    "完整的插件示例：将任务历史记录到本地 JSON 日志中，"
    "支持通过工具查询历史、导出 Markdown 报告。"
    "覆盖全部 15 个钩子。"
)

import json
import os
import time
from datetime import datetime

# ═══════════════════════════════════════════════════════════════
# 1. 工具定义（OpenAI function-calling 格式）
# ═══════════════════════════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "plugin_list_tasks",
            "description": (
                "列出当前会话中所有已记录的任务摘要。"
                "返回 JSON 数组，每个元素包含任务编号、描述、状态和耗时。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "返回最近 N 条记录（默认 10，最大 50）"
                    }
                },
                "required": [],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "plugin_export_report",
            "description": (
                "将当前会话的任务历史导出为 Markdown 报告文件。"
                "文件保存到工作区根目录下的 task_report.md。"
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


# ═══════════════════════════════════════════════════════════════
# 2. 工具调度入口
# ═══════════════════════════════════════════════════════════════

def execute(tool_name: str, args: dict, context) -> str:
    """Agent 调用本插件注册的工具时，通过此函数分发。"""
    if tool_name == "plugin_list_tasks":
        limit = min(args.get("limit", 10), 50)
        return _list_tasks(limit, context)

    if tool_name == "plugin_export_report":
        return _export_report(context)

    return f"[Task Logger] 未知工具: {tool_name}"


def _list_tasks(limit: int, context) -> str:
    """从 storage 中读取任务历史并格式化输出。"""
    tasks = context.storage.get("task_history", [])
    if not tasks:
        return "📋 暂无任务记录。"

    recent = tasks[-limit:]
    lines = [f"📋 **最近 {len(recent)} 条任务记录**\n"]
    for t in recent:
        status_icon = {"done": "✅", "error": "❌", "stopped": "⏹️", "timeout": "⏰"}.get(
            t.get("status", ""), "❓")
        lines.append(
            f"  {status_icon} #{t['id']} | {t['description'][:60]} | "
            f"{t.get('elapsed', 'N/A')}"
        )
    return "\n".join(lines)


def _export_report(context) -> str:
    """将任务历史导出为 Markdown 文件。"""
    tasks = context.storage.get("task_history", [])
    session_start = context.storage.get("session_start", time.time())
    elapsed = time.time() - session_start

    # 构建 Markdown
    h = int(elapsed // 3600)
    m = int((elapsed % 3600) // 60)
    s = int(elapsed % 60)

    md = f"""# 🤖 Vibe Coding Agent — 会话报告

> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 会话时长：{h}时{m}分{s}秒
> 项目路径：{context.project_root}

---

## 📊 统计概览

| 指标 | 数值 |
|------|------|
| 任务总数 | {len(tasks)} |
| 成功 | {sum(1 for t in tasks if t.get('status') == 'done')} |
| 失败 | {sum(1 for t in tasks if t.get('status') == 'error')} |
| 已停止 | {sum(1 for t in tasks if t.get('status') == 'stopped')} |
| 超时 | {sum(1 for t in tasks if t.get('status') == 'timeout')} |

---

## 📋 任务明细

| # | 状态 | 描述 | 耗时 |
|---|------|------|------|
"""
    for t in tasks:
        status_map = {"done": "✅", "error": "❌", "stopped": "⏹️", "timeout": "⏰"}
        icon = status_map.get(t.get("status", ""), "❓")
        md += f"| {t['id']} | {icon} | {t['description'][:80]} | {t.get('elapsed', 'N/A')} |\n"

    md += "\n---\n*由 Task Logger 插件自动生成*\n"

    report_path = os.path.join(context.project_root, "task_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md)

    context.logger.info(f"Report exported to {report_path} ({len(tasks)} tasks)")
    return f"✅ 报告已导出到 `task_report.md`（{len(tasks)} 条任务记录）"


# ═══════════════════════════════════════════════════════════════
# 3. L1 — 生命周期钩子
# ═══════════════════════════════════════════════════════════════

def on_agent_init(context):
    """Agent 启动时：初始化 storage 字段。"""
    context.storage["session_start"] = time.time()
    context.storage["task_history"] = []
    context.storage["current_task"] = None
    context.storage["step_count"] = 0
    context.storage["total_tokens"] = 0

    context.logger.info(
        f"[Task Logger] 已就绪 | 工作区: {os.path.basename(context.project_root)}"
    )


def on_agent_shutdown(context):
    """Agent 关闭时：输出汇总。"""
    tasks = context.storage.get("task_history", [])
    done = sum(1 for t in tasks if t.get("status") == "done")
    context.logger.info(
        f"[Task Logger] 会话结束 — {len(tasks)} 任务, {done} 成功"
    )


# ═══════════════════════════════════════════════════════════════
# 4. L2 — 任务生命周期钩子
# ═══════════════════════════════════════════════════════════════

def on_task_start(task_text: str, context):
    """任务开始时：创建新的任务记录。"""
    task_id = len(context.storage.get("task_history", [])) + 1
    context.storage["current_task"] = {
        "id": task_id,
        "description": task_text,
        "start_time": time.time(),
        "status": "running",
        "elapsed": "",
        "steps": 0,
    }
    context.logger.info(f"[Task Logger] ▶️ 任务 #{task_id}: {task_text[:80]}")


def on_task_done(summary: str, final_reply: str, context):
    """任务成功完成时：写入完成状态并归档。"""
    _finish_task("done", summary, context)


def on_task_error(error_msg: str, context):
    """任务失败时：写入错误状态并归档。"""
    _finish_task("error", error_msg, context)


def on_task_stopped(context):
    """用户手动停止任务。"""
    _finish_task("stopped", "用户手动停止", context)


def on_task_timeout(elapsed: float, context):
    """任务超时。"""
    _finish_task("timeout", f"超时（{elapsed:.0f}秒）", context)


def _finish_task(status: str, detail: str, context):
    """完成当前任务记录并推入历史列表。"""
    task = context.storage.get("current_task")
    if not task:
        return

    task["status"] = status
    task["end_time"] = time.time()
    elapsed = task["end_time"] - task.get("start_time", task["end_time"])
    task["elapsed"] = f"{elapsed:.1f}s"
    task["detail"] = detail

    history = context.storage.get("task_history", [])
    history.append(task)
    context.storage["task_history"] = history
    context.storage["current_task"] = None

    # 同时追加到磁盘上的 JSON 日志
    _append_json_log(task, context)

    icon = {"done": "✅", "error": "❌", "stopped": "⏹️", "timeout": "⏰"}.get(status, "")
    context.logger.info(
        f"[Task Logger] {icon} 任务 #{task['id']} {status} ({task['elapsed']})"
    )


def _append_json_log(task: dict, context):
    """将单条任务记录追加到工作区根目录的 task_log.json 中。"""
    log_path = os.path.join(context.project_root, "task_log.json")
    try:
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = []

        # 只保留关键字段，去掉不可序列化的内容
        record = {
            "id": task["id"],
            "description": task.get("description", ""),
            "status": task.get("status", ""),
            "elapsed": task.get("elapsed", ""),
            "steps": task.get("steps", 0),
            "detail": task.get("detail", ""),
            "timestamp": datetime.now().isoformat(),
        }
        data.append(record)

        # 最多保留 200 条，避免文件膨胀
        if len(data) > 200:
            data = data[-200:]

        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        context.logger.warn(f"[Task Logger] 写入日志失败: {e}")


# ═══════════════════════════════════════════════════════════════
# 5. L3 — 步骤 / 工具调用钩子
# ═══════════════════════════════════════════════════════════════

def before_step(step: int, messages: list, context):
    """每个 ReAct 步骤前：记录步骤数。"""
    context.storage["step_count"] = step

    task = context.storage.get("current_task")
    if task:
        task["steps"] = task.get("steps", 0) + 1

    return messages  # 不修改消息列表


def after_step(step: int, reasoning: str, content: str,
               tool_calls: list, context):
    """每个 ReAct 步骤后：可选地将摘要写入日志。"""
    # 生产环境请注释掉，避免日志刷屏
    # context.logger.debug(f"[Task Logger] Step {step} 完成")
    pass


def before_tool_call(tool_name: str, args: dict, context):
    """工具调用前：可在此拦截或修改参数。"""
    # 示例：对文件写入操作做额外日志
    if tool_name in ("write_file", "replace_in_file"):
        path = args.get("path", "?")
        context.logger.debug(f"[Task Logger] ✏️ 即将写入: {path}")

    return args  # 返回 args 放行；返回 None 则拦截本次调用


def after_tool_call(tool_name: str, args: dict, result: str, context):
    """工具调用后：可在此修改返回值。"""
    # 示例：对长返回结果截断日志
    short = result[:150].replace("\n", " ")
    if len(result) > 150:
        short += f"... ({len(result)} 字符)"
    context.logger.debug(f"[Task Logger] 📥 {tool_name} → {short}")

    return result  # 原样返回


def on_user_input_required(question: str, context):
    """Agent 向用户提问时触发。"""
    context.logger.info(f"[Task Logger] ❓ Agent 提问: {question[:100]}")


# ═══════════════════════════════════════════════════════════════
# 6. L4 — 流式事件钩子
# ═══════════════════════════════════════════════════════════════

def on_reasoning(token: str, context):
    """每个推理 token 输出时触发（高频！保持轻量）。"""
    pass  # 生产环境请不要在此做重操作


def on_content(token: str, context):
    """每个内容 token 输出时触发（高频！保持轻量）。"""
    pass


def on_event(event_type: str, data: str, context):
    """通用事件队列事件。"""
    # 仅记录关键事件类型
    if event_type in ("task_done", "error"):
        context.logger.debug(f"[Task Logger] 事件: {event_type}")


def on_usage_update(usage: dict, context):
    """Token 用量更新时触发。"""
    total = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    context.storage["total_tokens"] = total
    # context.logger.debug(f"[Task Logger] Tokens: {total}")

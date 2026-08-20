# Copyright (c) 2026 xingluosama121, MIT Licensed
"""长周期任务协作工具：task_submit / task_list / task_status / task_cancel。

Agent 在长周期任务中可以把子任务提交到调度器队列（task_submit），
由宿主应用 / 后台 drain 循环执行，从而实现：

- 任务拆分：大任务拆成若干子任务，按优先级排队执行；
- 多智能体编排：子任务可指定不同 preset（task_submit 的 preset 参数），
  由对应模式的 Agent 执行；
- 断点续跑：persistent 调度器落盘后，进程重启任务不丢。

工具通过 ``ctx.scheduler`` 访问调度器；查询类工具（list/status/cancel）
需要调度器提供同名方法（persistent 调度器全部支持）。
"""

from __future__ import annotations

from typing import Any, Dict, List

from norpagent.protocols.scheduler import AgentTask
from norpagent.protocols.tool import Tool, ToolResult


def _schedule(scheduler: Any, task: AgentTask) -> str:
    task.id = task.id or ""
    task.params = dict(task.params or {})
    task.params.setdefault("priority", 5)
    return scheduler.submit(task)


def _fmt_task(t: Dict[str, Any]) -> str:
    params = t.get("params") or {}
    parts = [
        f"{t.get('id')}  [{t.get('status')}]  pri={params.get('priority', '-')}"
        f"  preset={t.get('preset_name') or '(继承)'}"
    ]
    text = t.get("user_input") or ""
    if len(text) > 80:
        text = text[:80] + "..."
    parts.append(f"      {text}")
    error = t.get("error") or ""
    if error:
        parts.append(f"      error: {error}")
    return "\n".join(parts)


class TaskSubmitTool:
    name = "task_submit"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "把一个子任务提交到任务队列（长周期任务协作 / 多智能体编排）。"
                    "子任务由宿主应用按优先级调度执行；可用 preset 参数指定执行模式，"
                    "用 session_id 复用已有会话。提交后返回任务 id，可用 task_status 查询。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {"type": "string", "description": "子任务的完整描述（自包含，执行者看不到父任务上下文）"},
                        "preset": {"type": "string", "description": "执行该任务的预设模式名，缺省继承当前模式"},
                        "priority": {"type": "integer", "description": "优先级（0 最高，默认 5）"},
                        "session_id": {"type": "string", "description": "复用的会话 id（可选，缺省新会话）"},
                    },
                    "required": ["input"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        if ctx.scheduler is None:
            return ToolResult(
                output="当前模式未装配任务调度器。", success=False,
                error="scheduler 未装配",
            )
        user_input = str(args.get("input") or "").strip()
        if not user_input:
            return ToolResult(output="input 参数为空。", success=False, error="empty_input")
        preset = str(args.get("preset") or "") or ctx.preset_name
        params: Dict[str, Any] = {}
        try:
            params["priority"] = max(0, min(int(args.get("priority") or 5), 99))
        except (TypeError, ValueError):
            params["priority"] = 5
        try:
            task_id = _schedule(ctx.scheduler, AgentTask(
                id="",
                user_input=user_input,
                preset_name=preset,
                session_id=str(args.get("session_id") or "") or None,
                params=params,
            ))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"提交失败: {exc}", success=False, error=str(exc))
        return ToolResult(
            output=(
                f"子任务已入队：id={task_id}，模式={preset}，优先级={params['priority']}。"
                "宿主应用 drain 队列时执行；可用 task_status 查询进度。"
            )
        )


class TaskListTool:
    name = "task_list"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "列出任务队列中的任务（按优先级排序），可按状态过滤。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "description": "按状态过滤：pending/running/done/failed/stopped/cancelled（可选）"},
                        "limit": {"type": "integer", "description": "最多返回条数（默认 20，最大 200）"},
                    },
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        scheduler = ctx.scheduler
        if scheduler is None:
            return ToolResult(output="当前模式未装配任务调度器。", success=False, error="scheduler 未装配")
        list_fn = getattr(scheduler, "list_tasks", None)
        if not callable(list_fn):
            return ToolResult(
                output=(
                    f"当前调度器（{type(scheduler).__name__}）不支持任务查询。"
                    "请使用 persistent 调度器（预设 scheduler=\"persistent\"）。"
                ),
                success=False,
                error="scheduler 不支持查询",
            )
        try:
            tasks = list_fn(
                status=str(args.get("status") or "") or None,
                limit=int(args.get("limit") or 20),
            )
            counts = getattr(scheduler, "counts", None)
            counts = counts() if callable(counts) else {}
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"读取失败: {exc}", success=False, error=str(exc))
        lines = ["[任务队列]", "", f"统计: {counts or '不可用'}", ""]
        if not tasks:
            lines.append("（队列为空）")
        for t in tasks:
            lines.append(_fmt_task(t))
            lines.append("")
        return ToolResult(output="\n".join(lines).rstrip())


class TaskStatusTool:
    name = "task_status"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "查询单个任务的状态与结果（长周期任务进度追踪）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "任务 id（task_submit 返回）"},
                    },
                    "required": ["task_id"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        scheduler = ctx.scheduler
        if scheduler is None:
            return ToolResult(output="当前模式未装配任务调度器。", success=False, error="scheduler 未装配")
        status_fn = getattr(scheduler, "status", None)
        if not callable(status_fn):
            return ToolResult(
                output=f"当前调度器（{type(scheduler).__name__}）不支持状态查询，请使用 persistent 调度器。",
                success=False,
                error="scheduler 不支持状态查询",
            )
        task_id = str(args.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(output="task_id 参数为空。", success=False, error="empty_task_id")
        try:
            status = status_fn(task_id)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"查询失败: {exc}", success=False, error=str(exc))
        if status is None:
            return ToolResult(output=f"任务 {task_id} 不存在。", success=False, error="not_found")
        lines = [_fmt_task(status)]
        result = status.get("result") or ""
        if result:
            lines.append(f"结果摘要: {result}")
        return ToolResult(output="\n".join(lines))


class TaskCancelTool:
    name = "task_cancel"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "取消一个仍在排队（pending）的任务。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "任务 id"},
                    },
                    "required": ["task_id"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        scheduler = ctx.scheduler
        if scheduler is None:
            return ToolResult(output="当前模式未装配任务调度器。", success=False, error="scheduler 未装配")
        cancel_fn = getattr(scheduler, "cancel", None)
        if not callable(cancel_fn):
            return ToolResult(
                output=f"当前调度器（{type(scheduler).__name__}）不支持取消，请使用 persistent 调度器。",
                success=False,
                error="scheduler 不支持取消",
            )
        task_id = str(args.get("task_id") or "").strip()
        if not task_id:
            return ToolResult(output="task_id 参数为空。", success=False, error="empty_task_id")
        try:
            ok = cancel_fn(task_id)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"取消失败: {exc}", success=False, error=str(exc))
        if not ok:
            return ToolResult(
                output=f"任务 {task_id} 不存在或不在 pending 状态（运行中/已完成的任务不可取消）。",
                success=False,
                error="not_cancellable",
            )
        return ToolResult(output=f"任务 {task_id} 已取消。")

# Copyright (c) 2026 xingluosama121, MIT Licensed
"""9 层标准钩子：Agent 生命周期每个执行结构的完整暴露面。

        L1  运行时生命周期     on_agent_init / on_agent_shutdown
        L2  任务生命周期       on_task_start / done / error / stopped / timeout
        L3  输入管线           before_input / after_input / on_user_input_required
        L4  会话与历史         before/after_session_create、before/after_message_append
        L5  消息组装           before/after_build_messages
        L6  步骤               before_step / after_step
        L7  模型调用           before/after_model_call + 4 个流式事件
        L8  工具调用           before/after_tool_call + on_tool_error
        L9  结果定型           before_result / after_result

共 29 个钩子，每一个都是独立的模块级 API：

    from norpagent.hooks import before_model_call, after_tool_call
    before_model_call.subscribe(fn, system=registry)
    agent.hooks.before_model_call.subscribe(fn)      # 与上一行等价

可变钩子（mutating=True）可通过返回值改写数据流，语义见各钩子注释；
任何可变钩子都可抛 ``HookVeto`` 一票否决（运行时按执行点语义收尾）。
"""

from __future__ import annotations

from typing import List

from norpagent.hooks.core import Hook, HookLayer

# ═══════════════════════════════════════════════════════════════════
#  L1 – 运行时生命周期（order 10）
# ═══════════════════════════════════════════════════════════════════

L1 = HookLayer(
    "L1_runtime", order=10,
    description="运行时生命周期：Agent 实例的创建与销毁",
)

on_agent_init = L1.hook(
    "on_agent_init",
    description="运行时初始化完成（组件装配、UI 订阅之后）",
    payload_keys=("preset",),
)
on_agent_shutdown = L1.hook(
    "on_agent_shutdown",
    description="运行时关闭（沙箱/组件释放之后广播）",
    payload_keys=("preset",),
)

# ═══════════════════════════════════════════════════════════════════
#  L2 – 任务生命周期（order 20）
# ═══════════════════════════════════════════════════════════════════

L2 = HookLayer(
    "L2_task", order=20,
    description="任务生命周期：一次 run() 的开始、完成与各种终止",
)

on_task_start = L2.hook(
    "on_task_start",
    description="任务开始（输入已通过 L3、会话已就绪）",
    payload_keys=("task_id", "session_id", "preset", "user_input"),
)
on_task_done = L2.hook(
    "on_task_done",
    description="任务正常完成（有最终回复产出）",
    payload_keys=("task_id", "session_id", "content", "steps", "context"),
)
on_task_error = L2.hook(
    "on_task_error",
    description="任务异常终止（内部异常兜底后广播）",
    payload_keys=("task_id", "error"),
)
on_task_stopped = L2.hook(
    "on_task_stopped",
    description="任务被停止（步数超限 / 安全拦截 / 钩子否决等）",
    payload_keys=("task_id", "reason"),
)
on_task_timeout = L2.hook(
    "on_task_timeout",
    description="任务超时（task_timeout 轮次边界或 call_timeout 硬中断）",
    payload_keys=("task_id", "timeout", "kind"),
)

# ═══════════════════════════════════════════════════════════════════
#  L3 – 输入管线（order 30）
# ═══════════════════════════════════════════════════════════════════

L3 = HookLayer(
    "L3_input", order=30,
    description="输入管线：用户输入进入系统前可改写、可否决、可观测",
)

before_input = L3.hook(
    "before_input", mutating=True,
    description=(
        "用户输入处理前。返回 str = 替换输入；抛 HookVeto = "
        "任务以 stopped 收尾（reason 进入错误信息）；返回 None = 不改写"
    ),
    payload_keys=("task_id", "user_input", "session_id", "params"),
)
after_input = L3.hook(
    "after_input",
    description="输入确定后（改写/安全扫描完成）",
    payload_keys=("task_id", "user_input", "session_id"),
)
on_user_input_required = L3.hook(
    "on_user_input_required",
    description="需要用户提供额外输入（人工审批 / UI 提问）",
    payload_keys=("question", "default"),
)

# ═══════════════════════════════════════════════════════════════════
#  L4 – 会话与历史（order 40）
# ═══════════════════════════════════════════════════════════════════

L4 = HookLayer(
    "L4_session", order=40,
    description="会话与历史：会话创建、每条消息落库前后的完整钩子",
)

before_session_create = L4.hook(
    "before_session_create", mutating=True,
    description=(
        "会话创建前。返回 str 或 {'title': str} = 改写会话标题；"
        "抛 HookVeto = 放弃创建（任务以 stopped 收尾）"
    ),
    payload_keys=("session_id", "title", "params", "task_id"),
)
after_session_create = L4.hook(
    "after_session_create",
    description="会话创建后",
    payload_keys=("session_id", "title", "task_id"),
)
before_message_append = L4.hook(
    "before_message_append", mutating=True,
    description=(
        "消息落库前。返回 ChatMessage = 替换；返回 False = 丢弃该条；"
        "抛 HookVeto = 丢弃该条；None = 不改写"
    ),
    payload_keys=("session_id", "message", "task_id"),
)
after_message_append = L4.hook(
    "after_message_append",
    description="消息落库后",
    payload_keys=("session_id", "message", "task_id"),
)

# ═══════════════════════════════════════════════════════════════════
#  L5 – 消息组装（order 50）
# ═══════════════════════════════════════════════════════════════════

L5 = HookLayer(
    "L5_assembly", order=50,
    description="消息组装：系统提示词与历史合并成模型输入的过程",
)

before_build_messages = L5.hook(
    "before_build_messages", mutating=True,
    description=(
        "组装消息前。返回 str = 替换系统提示词；"
        "返回 {'system_prompt': str} = 同左；None = 不改写"
    ),
    payload_keys=("system_prompt", "session_id", "step", "task_id", "tool_names"),
)
after_build_messages = L5.hook(
    "after_build_messages", mutating=True,
    description="组装消息后。返回 List[ChatMessage] = 替换整组消息",
    payload_keys=("messages", "system_prompt", "step", "task_id"),
)

# ═══════════════════════════════════════════════════════════════════
#  L6 – 步骤（order 60）
# ═══════════════════════════════════════════════════════════════════

L6 = HookLayer(
    "L6_step", order=60,
    description="步骤：主循环每一轮迭代的边界",
)

before_step = L6.hook(
    "before_step", mutating=True,
    description=(
        "每轮迭代开始。返回 List[ChatMessage] = 替换本轮消息；"
        "抛 HookVeto = 跳过本轮；None = 不改写"
    ),
    payload_keys=("task_id", "step", "messages", "context", "params"),
)
after_step = L6.hook(
    "after_step",
    description="本轮模型输出处理完毕（进入工具执行或结束）",
    payload_keys=("task_id", "step", "content", "tool_calls"),
)

# ═══════════════════════════════════════════════════════════════════
#  L7 – 模型调用（order 70）
# ═══════════════════════════════════════════════════════════════════

L7 = HookLayer(
    "L7_model", order=70,
    description="模型调用：请求发出前、响应返回后、流式增量全过程",
)

before_model_call = L7.hook(
    "before_model_call", mutating=True,
    description=(
        "模型请求发出前。返回 {'messages': [...], 'params': {...}} = "
        "按需替换；抛 HookVeto = 拒绝本轮调用（任务 stopped）；None = 不改写"
    ),
    payload_keys=("task_id", "step", "messages", "tool_schemas", "params"),
)
after_model_call = L7.hook(
    "after_model_call", mutating=True,
    description="模型响应返回后。返回 ModelOutput = 替换本次输出",
    payload_keys=("task_id", "step", "output"),
)
on_reasoning = L7.hook(
    "on_reasoning",
    description="模型思维链增量（reasoning 模型流式输出）",
    payload_keys=("task_id", "content", "stream"),
)
on_content = L7.hook(
    "on_content",
    description="模型正文增量（流式逐段 / 整段）",
    payload_keys=("task_id", "content", "stream", "final"),
)
on_event = L7.hook(
    "on_event",
    description="模型侧任意子事件（兼容事件透传）",
    payload_keys=("event_type", "data", "task_id"),
)
on_usage_update = L7.hook(
    "on_usage_update",
    description="token 用量累计更新",
    payload_keys=("task_id", "input", "output", "total"),
)

# ═══════════════════════════════════════════════════════════════════
#  L8 – 工具调用（order 80）
# ═══════════════════════════════════════════════════════════════════

L8 = HookLayer(
    "L8_tool", order=80,
    description="工具调用：参数改写、一票否决、结果改写、异常观测",
)

before_tool_call = L8.hook(
    "before_tool_call", mutating=True,
    description=(
        "工具执行前。返回 dict = 替换参数；返回 False / 抛 HookVeto = "
        "阻止调用（回填 blocked_by_hook 结果）；None = 不改写"
    ),
    payload_keys=("task_id", "tool_name", "args", "context"),
)
after_tool_call = L8.hook(
    "after_tool_call", mutating=True,
    description=(
        "工具执行后。返回 str = 替换结果文本；返回 ToolResult = 替换结果；"
        "None = 不改写"
    ),
    payload_keys=("task_id", "tool_name", "args", "result", "success", "context"),
)
on_tool_error = L8.hook(
    "on_tool_error",
    description="工具执行抛出异常（已被框架捕获转 ToolResult 后广播）",
    payload_keys=("task_id", "tool_name", "error", "args"),
)

# ═══════════════════════════════════════════════════════════════════
#  L9 – 结果定型（order 90）
# ═══════════════════════════════════════════════════════════════════

L9 = HookLayer(
    "L9_result", order=90,
    description="结果定型：RunResult 返回给调用方之前的最后改写窗口",
)

before_result = L9.hook(
    "before_result", mutating=True,
    description=(
        "结果定型前（on_task_done/error/stopped 已广播）。"
        "返回 RunResult = 替换最终结果；抛 HookVeto = 保持原结果"
    ),
    payload_keys=("task_id", "result"),
)
after_result = L9.hook(
    "after_result", mutating=True,
    description=(
        "结果定型后。返回 RunResult = 替换最终结果（返回值生效）；"
        "抛 HookVeto = 保持当前结果"
    ),
    payload_keys=("task_id", "result"),
)

# ── 标准层合集 ──────────────────────────────────────────────

STANDARD_LAYERS: List[HookLayer] = [
    L1, L2, L3, L4, L5, L6, L7, L8, L9,
]

# 全部标准钩子（模块级独立 API 合集，供文档与遍历使用）
ALL_STANDARD_HOOKS: List[Hook] = [
    h for layer in STANDARD_LAYERS for h in layer.hooks.values()
]

MUTATING_HOOK_NAMES = [h.name for h in ALL_STANDARD_HOOKS if h.mutating]

__all__ = [
    "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9",
    "STANDARD_LAYERS",
    "ALL_STANDARD_HOOKS",
    "MUTATING_HOOK_NAMES",
    "on_agent_init", "on_agent_shutdown",
    "on_task_start", "on_task_done", "on_task_error",
    "on_task_stopped", "on_task_timeout",
    "before_input", "after_input", "on_user_input_required",
    "before_session_create", "after_session_create",
    "before_message_append", "after_message_append",
    "before_build_messages", "after_build_messages",
    "before_step", "after_step",
    "before_model_call", "after_model_call",
    "on_reasoning", "on_content", "on_event", "on_usage_update",
    "before_tool_call", "after_tool_call", "on_tool_error",
    "before_result", "after_result",
]

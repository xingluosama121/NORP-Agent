# Copyright (c) 2026 xingluosama121, MIT Licensed
"""norpagent.hooks — 9 层 29 钩子体系（标准库自带，零依赖）。

每个钩子都是独立的模块级 API；每个执行结构都可被钩子干预。
用法速览::

    from norpagent.hooks import before_model_call, HookVeto, HookLayer

    # 1. 模块级独立 API：订阅 / 触发 / 拦截
    before_model_call.subscribe(observer, system=registry)

    # 2. 运行时绑定视图：agent.hooks.<钩子名>.*
    agent.hooks.before_tool_call.intercept(...)

    # 3. 自定义钩子 + 自定义层
    layer = HookLayer("L10_network", order=100)
    before_net = layer.hook("before_network_call", mutating=True)
    agent.hooks.install_layer(layer)

    # 4. 一票否决
    def veto(event): raise HookVeto("输入包含敏感信息")
    before_input.subscribe(veto, system=registry)

九层一览：L1 运行时生命周期 → L2 任务 → L3 输入 → L4 会话与历史 →
L5 消息组装 → L6 步骤 → L7 模型调用 → L8 工具调用 → L9 结果定型。
完整文档见 docs/hooks.md。
"""

from norpagent.hooks.core import (
    BoundHook,
    DYNAMIC_LAYER,
    Hook,
    HookLayer,
    HookSystem,
    HookVeto,
    get_default_system,
)
from norpagent.hooks.standard import (
    ALL_STANDARD_HOOKS,
    MUTATING_HOOK_NAMES,
    STANDARD_LAYERS,
    L1, L2, L3, L4, L5, L6, L7, L8, L9,
    on_agent_init, on_agent_shutdown,
    on_task_start, on_task_done, on_task_error,
    on_task_stopped, on_task_timeout,
    before_input, after_input, on_user_input_required,
    before_session_create, after_session_create,
    before_message_append, after_message_append,
    before_build_messages, after_build_messages,
    before_step, after_step,
    before_model_call, after_model_call,
    on_reasoning, on_content, on_event, on_usage_update,
    before_tool_call, after_tool_call, on_tool_error,
    before_result, after_result,
)

__all__ = [
    "Hook", "BoundHook", "HookLayer", "HookSystem", "HookVeto",
    "DYNAMIC_LAYER", "get_default_system",
    "STANDARD_LAYERS", "ALL_STANDARD_HOOKS", "MUTATING_HOOK_NAMES",
    "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9",
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

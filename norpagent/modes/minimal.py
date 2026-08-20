# Copyright (c) 2026 xingluosama121, MIT Licensed
"""极简模式：仅保留最基础工具，用于模型基准测试。

设计目标：环境确定性最大化，变量最小化。
- 默认模型 mock（可替换为任意已注册模型，如 P2 的 openai_compat）
- 工具仅 echo / get_time：无副作用、不触网、不触盘
- 基准测试流程：固定输入集 + 固定工具集，对比不同模型/组件实现的
  输出质量、步数、token 消耗
"""

from norpagent.kernel.presets import Preset, MODE_SINGLE

_SYSTEM_PROMPT = (
    "你是一个极简基准测试助手。直接、简洁地回答用户问题。"
    "仅在被明确要求时调用工具。回答使用用户的语言。"
)


def build_minimal_preset(model: str = "mock") -> Preset:
    return Preset(
        name="minimal",
        description="极简模式：仅最基础工具（echo/get_time），确定性环境，用于模型基准测试",
        model=model,
        tools=["echo", "get_time"],
        session="memory",
        sandbox="subprocess",
        scheduler="simple",
        ui="console",
        mode=MODE_SINGLE,
        params={
            "max_steps": 8,
            "temperature": 0.0,
            "system_prompt": _SYSTEM_PROMPT,
            "task_timeout": 0,
        },
    )

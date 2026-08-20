# Copyright (c) 2026 xingluosama121, MIT Licensed
"""PTC 模式（Programmatic Tool Composition）：
模型通过生成 Python 代码组合多步工具调用。

与标准模式的区别：模型不是逐个发起工具调用，而是把「调用什么工具、
如何组合结果」写成一段代码，由 run_python 工具执行。适合需要条件、
循环、批量聚合的多步编排任务。
"""

from norpagent.kernel.presets import Preset, MODE_PTC

_SYSTEM_PROMPT = (
    "你是一个 PTC（Programmatic Tool Composition）助手。\n"
    "当任务需要多个工具协作时，不要逐个发起工具调用，而是：\n"
    "1. 生成一段 Python 代码；\n"
    "2. 在代码中通过 call_tool(工具名, **参数) 调用 Agent 已注册的工具；\n"
    "3. 用代码组织调用顺序、条件判断与结果聚合；\n"
    "4. 调用 run_python 工具执行这段代码，并依据输出继续或作答。\n"
    "简单问题可以直接回答，无需写代码。"
)


def build_ptc_preset(model: str = "openai_compat") -> Preset:
    return Preset(
        name="ptc",
        description="PTC 模式：模型生成 Python 代码组合多步工具调用",
        model=model,
        tools=[
            "run_python",  # PTC 执行核心
            # 被编排的业务工具（P2 全套 + P3 上下文/项目/任务）
            "echo",
            "get_time",
            "file_read",
            "file_write",
            "file_list",
            "file_delete",
            "exec_cmd",
            "web_search",
            "web_fetch",
            "web_extract_links",
            "context_add",
            "context_search",
            "context_list",
            "context_delete",
            "project_status",
            "task_submit",
            "task_list",
            "task_status",
            "task_cancel",
        ],
        session="sqlite",
        sandbox="pooled",
        scheduler="persistent",
        ui="console",
        mode=MODE_PTC,
        components={
            "context_store": "fts5",
            "project_manager": "basic",
        },
        params={
            "max_steps": 64,
            "temperature": 1.0,
            "system_prompt": _SYSTEM_PROMPT,
            "task_timeout": 0,
        },
    )

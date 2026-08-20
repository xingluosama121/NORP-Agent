# Copyright (c) 2026 xingluosama121, MIT Licensed
"""标准模式：功能完整的通用编码助手（P3 全量能力）。

组件组合（全部可换）：
- 模型 openai_compat（--model mock 可验证流程；norpagent[openai] 提供 SDK）
- 会话 sqlite：任务历史持久化，重启续聊
- 调度 persistent：长周期任务落盘，崩溃后 resume 续跑
- 沙箱 pooled：复用 + 并发上限 + 超时强杀进程树
- 组件：context_store=fts5（跨会话可检索知识库）、project_manager=basic

工具全集：文件 / 命令 / 联网 / 上下文管理 / 项目管理 / 长周期任务协作。
"""

from norpagent.kernel.presets import Preset, MODE_SINGLE

_SYSTEM_PROMPT = (
    "你是一个功能完整的通用编码助手。"
    "你可以使用文件读写、命令执行、联网检索等工具完成复杂工程任务。"
    "对于长周期任务：把中间结论写入上下文库（context_add）供后续检索，"
    "把可独立的子任务提交到队列（task_submit）并行推进。"
    "遵循工程最佳实践：先理解再动手，重要操作前说明计划。"
    "所有文件操作严格限定在工作区根目录内，使用相对路径。"
)


def build_standard_preset(model: str = "openai_compat") -> Preset:
    return Preset(
        name="standard",
        description="标准模式：功能完整的通用编码助手（全套工具 + 持久化 + 长周期协作）",
        model=model,
        tools=[
            # 文件操作（P2）
            "file_read",
            "file_write",
            "file_list",
            "file_delete",
            # 命令执行（P2）
            "exec_cmd",
            # 联网检索（P2）
            "web_search",
            "web_fetch",
            "web_extract_links",
            # 上下文管理（P3）
            "context_add",
            "context_search",
            "context_list",
            "context_delete",
            # 项目管理（P3）
            "project_status",
            # 长周期任务协作（P3）
            "task_submit",
            "task_list",
            "task_status",
            "task_cancel",
        ],
        session="sqlite",  # 持久化会话；内存环境可改 "memory"
        sandbox="pooled",  # 池化沙箱；简单环境可改 "subprocess"
        scheduler="persistent",  # 长周期任务落盘；轻量场景可改 "simple"
        ui="console",  # Web 界面可改 "web"（HTTP + SSE）
        mode=MODE_SINGLE,
        components={
            "context_store": "fts5",  # 跨会话可检索上下文库
            "project_manager": "basic",  # 项目状态 / git 感知
        },
        params={
            "max_steps": 128,
            "temperature": 1.0,
            "system_prompt": _SYSTEM_PROMPT,
            "task_timeout": 0,
            # 单次模型调用硬超时（秒，0=不限）：阻塞期间的强杀能力
            "call_timeout": 0,
            # 安全加固（可选开启）：
            #   "jailbreak_guard": True  输入越狱/注入拦截
            #   "harden_prompt": True    系统提示词附加不可覆盖安全规则
        },
    )

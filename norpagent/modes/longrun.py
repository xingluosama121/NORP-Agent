# Copyright (c) 2026 xingluosama121, MIT Licensed
"""长任务模式：为长周期、多阶段复杂任务优化的预设。

与标准模式共享全套工具与持久化组件，差异在行为参数：
- max_steps 更大（512），复杂任务不易因步数上限中断；
- task_timeout = 0（不设任务级超时，由轮次边界与停止按钮控制）；
- 系统提示词强化分阶段规划：先拆解里程碑，逐步执行并落库中间结论，
  长任务可断点续跑（persistent 调度 + sqlite 会话 + fts5 上下文库）。
"""

from norpagent.kernel.presets import Preset, MODE_SINGLE

_SYSTEM_PROMPT = (
    "你是一个长周期任务执行助手。"
    "面对复杂任务，先拆解为清晰的阶段与里程碑，再逐步执行。"
    "每个阶段的中间结论写入上下文库（context_add）供后续检索，"
    "可独立的子任务提交到队列（task_submit）并行推进。"
    "长时间执行时定期汇报进展；遇到阻塞先记录状态再继续。"
    "所有文件操作严格限定在工作区根目录内，使用相对路径。"
)


def build_longrun_preset(model: str = "openai_compat") -> Preset:
    return Preset(
        name="longrun",
        description="长任务：长周期复杂任务（更大步数上限 + 持久化调度 + 断点续跑）",
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
        session="sqlite",
        sandbox="pooled",
        scheduler="persistent",
        ui="console",
        mode=MODE_SINGLE,
        components={
            "context_store": "fts5",
            "project_manager": "basic",
        },
        params={
            "max_steps": 512,
            "temperature": 1.0,
            "system_prompt": _SYSTEM_PROMPT,
            "task_timeout": 0,
            "call_timeout": 0,
        },
    )


__all__ = ["build_longrun_preset"]

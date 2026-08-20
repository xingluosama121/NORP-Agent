# Copyright (c) 2026 xingluosama121, MIT Licensed
"""嵌入式模式：面向边缘设备 / 低内存 / 无磁盘环境的资源极简组合。

设计目标（嵌入式 / 低资源场景）：
- 组件全部纯内存：memory 会话 + subprocess 沙箱 + simple 调度器，
  不声明任何通用组件（context_store / project_manager 均为空——
  FTS5 / SQLite 等磁盘依赖组件完全不会被构建）；
- 工具最小集：echo / get_time / run_python + 文件读写，不触网；
- 默认无 HTTP 服务：np(preset="embedded") 时前端槽位自动回落
  headless（纯 API 模式，不监听端口、不做浏览器 UI 磁盘配置）；
  需要 Web 界面时显式 np(preset="embedded",
  frontend="norpagent.frontends.web:WebFrontend") 即可；
- 模型声明 openai_compat：与 standard 相同的回落语义——未提供任何
  凭据时装配层自动回落 mock（嵌入式设备无网络 / 无 Key 也开箱可用）。

与 ``install_core()`` 配合：嵌入式场景推荐自建注册表走
``install_core``（连安装阶段都避开 sqlite3 / http.server 依赖），
然后注册本预设；np(preset="embedded") 路径则会走完整
install_defaults（仅注册，不构建重组件），并自动使用 headless 前端。

资源调优（详见手册「嵌入式与超高并发部署」章）：
- 工作池线程数：环境变量 NORPAGENT_MAX_WORKERS（如 =1）或
  np(config={"loop": {"max_workers": 1}})；
- submit 完成轮询：NORPAGENT_SUBMIT_POLL（默认 0.05s，嵌入式可调大）。
"""

from norpagent.kernel.presets import Preset, MODE_SINGLE

_SYSTEM_PROMPT = (
    "你是一个运行在嵌入式设备上的轻量助手。"
    "回答直接、简洁，避免不必要的工具调用，尽量少消耗资源。"
    "仅在被明确要求时才读写文件或执行代码。回答使用用户的语言。"
)


def build_embedded_preset(model: str = "openai_compat") -> Preset:
    return Preset(
        name="embedded",
        description=(
            "嵌入式模式：纯内存组件 + 最小工具集，无磁盘 / 无联网依赖，"
            "边缘设备与低资源环境友好（默认 headless 前端）"
        ),
        model=model,
        tools=[
            "echo",
            "get_time",
            "run_python",
            "file_read",
            "file_write",
            "file_list",
            "file_delete",
        ],
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
            "max_tokens": 2048,
        },
    )


__all__ = ["build_embedded_preset"]

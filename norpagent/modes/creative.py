# Copyright (c) 2026 xingluosama121, MIT Licensed
"""创造模式：用于调试和创建自定义的全新模式。

- 组件组合最小且确定（mock 模型），保证调试环境可复现；
- 配合 CLI ``--mode-file my_mode.py`` 加载自定义模式文件，
  或直接在代码里 ``registry.register_preset(MyPreset)`` 注册新模式；
- 模式文件约定：模块级变量 ``PRESET`` 为 Preset 实例（或 dict）。
"""

from norpagent.kernel.presets import Preset, MODE_CUSTOM

_SYSTEM_PROMPT = (
    "你运行在创造模式（调试环境）。如实描述你的行为，"
    "帮助开发者验证工具与模式配置是否按预期工作。"
)


def build_creative_preset() -> Preset:
    return Preset(
        name="creative",
        description="创造模式：调试与创建自定义模式（支持 --mode-file 加载自定义模式文件）",
        model="mock",
        tools=["echo", "get_time", "run_python"],
        session="memory",
        sandbox="subprocess",
        scheduler="simple",
        ui="console",
        mode=MODE_CUSTOM,
        params={
            "max_steps": 32,
            "temperature": 0.0,
            "system_prompt": _SYSTEM_PROMPT,
            "task_timeout": 0,
        },
    )

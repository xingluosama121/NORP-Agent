# Copyright (c) 2026 xingluosama121, MIT Licensed
"""预设模式系统：一种模式 = 一份声明式组件组合。

预设不包含任何实现，只声明「用哪些组件 + 什么行为参数」。
开发者新增模式 = 新建一个 Preset（可参考 modes/ 下的内置模式，
或使用创造模式通过 --mode-file 加载自定义文件）。
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from norpagent.kernel.registry import ComponentError

# 模式类型
MODE_SINGLE = "single"   # 对话式：模型直接对话 + 工具调用
MODE_PTC = "ptc"         # PTC：模型生成 Python 代码组合多步工具调用
MODE_CUSTOM = "custom"   # 用户自定义模式


@dataclass
class Preset:
    """一种预设模式。

    ``params`` 常用键：
        max_steps：单任务最大步数
        temperature / max_tokens：模型采样参数
        system_prompt：系统提示词
        task_timeout：任务超时秒数（0 = 不限，轮次边界检查）
        call_timeout：单次模型调用硬超时秒数（0 = 不限，阻塞中途强杀）
        workspace_root：文件类工具的工作区根目录（默认进程工作目录）

    ``components`` 声明「组件种类 -> 组件名」的附加装配（P3 起）：
        context_store / project_manager / task_store 等通用组件，
        由运行时按名构建并注入 RunContext，工具通过 ctx 访问。
    """

    name: str
    description: str
    model: str
    tools: List[str] = field(default_factory=list)
    session: str = "memory"
    sandbox: str = "subprocess"
    scheduler: str = "simple"
    ui: str = "console"
    mode: str = MODE_SINGLE
    params: Dict[str, Any] = field(default_factory=dict)
    components: Dict[str, str] = field(default_factory=dict)

    def merged_params(self, task_params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """预设参数与任务级参数合并（任务级覆盖预设级）。"""
        merged = dict(self.params)
        if task_params:
            merged.update(task_params)
        return merged

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "model": self.model,
            "tools": list(self.tools),
            "session": self.session,
            "sandbox": self.sandbox,
            "scheduler": self.scheduler,
            "ui": self.ui,
            "mode": self.mode,
            "params": dict(self.params),
            "components": dict(self.components),
        }


def load_preset_file(path: str) -> Preset:
    """从 .py 文件加载自定义模式（创造模式的核心能力）。

    文件约定：模块级变量 ``PRESET`` 为 Preset 实例（或含同名字段的 dict）。
    示例见 examples/custom_mode_file.py。
    """
    spec = importlib.util.spec_from_file_location("norpagent_user_mode", path)
    if spec is None or spec.loader is None:
        raise ComponentError(f"无法加载模式文件: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    preset = getattr(module, "PRESET", None)
    if preset is None:
        raise ComponentError(f"模式文件 {path} 未定义 PRESET")
    if isinstance(preset, dict):
        preset = Preset(**preset)
    if not isinstance(preset, Preset):
        raise ComponentError(f"模式文件 {path} 的 PRESET 类型无效: {type(preset)}")
    return preset

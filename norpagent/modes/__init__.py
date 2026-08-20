# Copyright (c) 2026 xingluosama121, MIT Licensed
"""预设模式包：内置六种模式，全部通过注册表登记。

新增高频使用模式的方式：
    1. 在本包添加 build_xxx_preset() 并在 register_all_presets 登记；
    2. 使用创造模式：--mode-file 加载自定义模式文件；
    3. 开发者在自己的代码里 registry.register_preset(Preset(...))。
"""

from __future__ import annotations

from typing import Any

from norpagent.modes.minimal import build_minimal_preset
from norpagent.modes.standard import build_standard_preset
from norpagent.modes.ptc import build_ptc_preset
from norpagent.modes.creative import build_creative_preset
from norpagent.modes.longrun import build_longrun_preset
from norpagent.modes.embedded import build_embedded_preset


def register_all_presets(registry: Any) -> Any:
    """注册全部内置预设模式，返回注册表本身（便于链式调用）。"""
    registry.register_preset(build_minimal_preset())
    registry.register_preset(build_standard_preset())
    registry.register_preset(build_ptc_preset())
    registry.register_preset(build_creative_preset())
    registry.register_preset(build_longrun_preset())
    registry.register_preset(build_embedded_preset())
    return registry


__all__ = [
    "register_all_presets",
    "build_minimal_preset",
    "build_standard_preset",
    "build_ptc_preset",
    "build_creative_preset",
    "build_longrun_preset",
    "build_embedded_preset",
]

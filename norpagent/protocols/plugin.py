# Copyright (c) 2026 xingluosama121, MIT Licensed
"""插件协议：工具的打包与分发单位。

一个插件 = 一组工具 + 一组生命周期钩子 + 元数据。
注册到 Registry 后，其工具进入工具注册表、钩子订阅事件总线。

P1 为进程内插件（对象直接注册）。P3 将迁移现有 plugin_system：
单文件插件 / manifest 包插件 / 签名校验 / 进程隔离 / 网络策略 / 人工审批，
使本库外部插件获得与现有应用同等的安全防护。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

from norpagent.protocols.tool import Tool


@runtime_checkable
class Plugin(Protocol):
    """插件接口。全部方法均为可选（空插件 = 仅元数据）。"""

    name: str
    version: str = "0.0.0"
    publisher: str = ""
    description: str = ""

    def get_tools(self) -> List[Tool]:
        """返回本插件提供的工具列表。"""
        return []

    def get_hooks(self) -> Dict[str, Callable[[Any], None]]:
        """返回钩子映射：事件名 -> 回调(event)。

        事件名与现有 plugin_system 的 15 个 hook 对齐
        （on_task_start / before_step / after_tool_call 等）。
        """
        return {}

    def execute(self, tool_name: str, args: Dict[str, Any], ctx: Any) -> Optional[str]:
        """兼容现有单文件插件风格的统一入口（可选）。

        返回 None 表示该插件不处理此工具名。
        """
        return None

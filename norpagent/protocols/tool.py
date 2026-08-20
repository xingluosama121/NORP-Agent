# Copyright (c) 2026 xingluosama121, MIT Licensed
"""工具协议：Agent 的「技能」抽象。

工具的 schema 沿用 OpenAI function 格式（与现有插件系统保持一致），
``run`` 接收已解析的 dict 参数与 :class:`~norpagent.kernel.context.RunContext`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Protocol, runtime_checkable


@dataclass
class ToolResult:
    """工具执行结果。``output`` 会作为 tool 角色消息回填给模型。"""

    output: str = ""
    success: bool = True
    error: str = ""

    def __str__(self) -> str:
        if not self.success:
            return f"[工具执行失败] {self.error or self.output}"
        return self.output


def tool_error(tool_name: str, exc: Exception) -> ToolResult:
    """构造统一的工具异常结果。"""
    return ToolResult(output=f"{tool_name} 执行异常: {exc}", success=False, error=str(exc))


@runtime_checkable
class Tool(Protocol):
    """单个工具接口。"""

    name: str

    def schema(self) -> Dict[str, Any]:
        """返回 OpenAI function schema（含 name/description/parameters）。"""
        ...

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        """执行工具。``ctx`` 为 RunContext，可访问沙箱、会话、注册表等。"""
        ...

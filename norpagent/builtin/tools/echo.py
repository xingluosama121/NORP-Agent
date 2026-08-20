# Copyright (c) 2026 xingluosama121, MIT Licensed
"""echo 工具：回显文本。极简模式的基础工具之一，无任何副作用。"""

from __future__ import annotations

from typing import Any, Dict

from norpagent.protocols.tool import Tool, ToolResult


class EchoTool:
    name = "echo"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "原样回显输入的文本。用于连通性验证与基准测试。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要回显的文本"},
                    },
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(output=f"[echo] {args.get('text', '')}")

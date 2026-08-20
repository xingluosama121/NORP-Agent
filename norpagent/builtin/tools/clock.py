# Copyright (c) 2026 xingluosama121, MIT Licensed
"""get_time 工具：返回当前本地时间。极简模式的基础工具之一。"""

from __future__ import annotations

import time
from typing import Any, Dict

from norpagent.protocols.tool import Tool, ToolResult


class GetTimeTool:
    name = "get_time"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "返回当前本地日期时间（ISO 8601 格式）。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult(
            output=time.strftime("%Y-%m-%d %H:%M:%S (%A)", time.localtime())
        )

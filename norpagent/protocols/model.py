# Copyright (c) 2026 xingluosama121, MIT Licensed
"""模型协议：Agent 的「大脑」抽象。

框架与任何具体模型 SDK 解耦。接入新模型只需实现
:class:`ModelProvider`（建议同时实现 :meth:`ModelProvider.stream`
以支持流式输出与事件广播）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Protocol, runtime_checkable


@dataclass
class ToolCallSpec:
    """模型请求的一次工具调用。"""

    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatMessage:
    """对话消息。角色沿用 OpenAI 语义：system / user / assistant / tool。"""

    role: str
    content: str = ""
    tool_calls: Optional[List[ToolCallSpec]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    reasoning: str = ""  # 思维链原文（reasoning 模型流式传输的独立字段）
    has_reasoning: bool = False  # 模型响应是否实际携带过 reasoning 字段（区分「空串字段」与「无字段」）

    def to_openai(self) -> Dict[str, Any]:
        """转为 OpenAI 协议消息字典（工具角色附 tool_call_id）。

        DeepSeek V4 等推理端点的契约：凡发生工具调用的轮次，
        assistant 消息的 ``reasoning_content`` 必须原样回传
        （即使为空字符串也必须保留该字段），否则下一轮请求返回 400。
        无工具调用的轮次不回传（官方明确该字段会被忽略）。
        """
        msg: Dict[str, Any] = {"role": self.role, "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": _dump_args(tc.arguments)},
                }
                for tc in self.tool_calls
            ]
            if self.has_reasoning:
                msg["reasoning_content"] = self.reasoning
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.name:
            msg["name"] = self.name
        return msg


def _dump_args(arguments: Dict[str, Any]) -> str:
    import json

    return json.dumps(arguments, ensure_ascii=False)


@dataclass
class ModelUsage:
    """一次模型调用的 token 用量。"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ModelOutput:
    """一次模型调用的完整结果。"""

    content: str = ""
    reasoning: str = ""  # 思维链 / 推理过程（reasoning 模型）
    tool_calls: Optional[List[ToolCallSpec]] = None
    usage: Optional[ModelUsage] = None
    finish_reason: str = "stop"  # stop | tool_calls | length
    has_reasoning: bool = False  # 响应是否实际携带过 reasoning 字段（工具轮次回传判定用）


@dataclass
class ModelStreamChunk:
    """流式输出增量。reasoning / delta_content / tool_call_delta 可同时存在。"""

    reasoning: str = ""  # 思维链增量（reasoning 模型流式输出）
    delta_content: str = ""
    tool_call_delta: Optional[ToolCallSpec] = None  # 每次给出该工具调用的完整合并结果
    usage: Optional[ModelUsage] = None
    finish_reason: str = ""
    has_reasoning: bool = False  # 本增量携带过 reasoning 字段（即使为空字符串）


@runtime_checkable
class ModelProvider(Protocol):
    """模型提供者接口。

    ``params`` 为运行时参数 dict（temperature / max_tokens / top_p 等），
    由预设模式的 ``params`` 与调用方参数合并后传入，实现可自由取用。
    """

    model_id: str

    def generate(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]],
        params: Dict[str, Any],
    ) -> ModelOutput:
        """非流式生成。``tools`` 为 OpenAI function schema 列表。"""
        ...

    def stream(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]],
        params: Dict[str, Any],
    ) -> Iterator[ModelStreamChunk]:
        """流式生成（可选实现）。运行时优先使用流式接口以广播事件。"""
        raise NotImplementedError(f"{self.model_id} 不支持流式输出")

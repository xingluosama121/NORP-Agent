# Copyright (c) 2026 xingluosama121, MIT Licensed
"""Anthropic 协议模型适配器：连接 Claude 系列模型。

依赖 ``anthropic>=0.34``（norpagent[anthropic]），客户端懒加载：
未安装 SDK 时注册与配置不报错，首次调用给出明确的安装提示。

协议转换要点：
- system 消息分离为顶层 ``system`` 参数；
- assistant 的 tool_calls 映射为 tool_use 内容块；
- tool 消息映射为紧随其后的 user 消息中的 tool_result 内容块
  （连续多条 tool 消息合并为一条 user 消息，满足 Anthropic 协议要求）；
- OpenAI function schema 映射为 Anthropic tool input_schema。

取消协作：params["_cancel_event"]（threading.Event，由内核注入）
被置位时，流式循环尽早停止，配合内核的 call_timeout 硬中断。
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Iterator, List, Optional, Tuple

from norpagent.protocols.model import (
    ChatMessage,
    ModelOutput,
    ModelStreamChunk,
    ModelUsage,
    ToolCallSpec,
)


class AnthropicProvider:
    """Anthropic 协议适配器。"""

    model_id = "anthropic"

    def __init__(
        self,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.model_name = model_name or os.environ.get("NORPAGENT_ANTHROPIC_MODEL", "") or "claude-sonnet-4-5"
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.base_url = base_url
        self.timeout = timeout or 120.0
        self._client = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise RuntimeError(
                    "未安装 anthropic SDK。请执行: pip install norpagent[anthropic]"
                    "（或 pip install anthropic>=0.34）"
                )
            api_key = self.api_key or os.environ.get(self.api_key_env, "")
            if not api_key:
                raise RuntimeError(
                    f"未找到 API Key。请设置环境变量 {self.api_key_env}，"
                    f"或构造 AnthropicProvider(api_key=...) 时显式传入。"
                )
            kwargs: Dict[str, Any] = {"api_key": api_key, "timeout": self.timeout}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    # ── 协议转换（模块级函数便于单元测试）──────────────────

    def _to_anthropic_messages(
        self, messages: List[ChatMessage]
    ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """返回 (system 文本, Anthropic messages 列表)。"""
        system_parts: List[str] = []
        out: List[Dict[str, Any]] = []

        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.role == "system":
                system_parts.append(msg.content)
                i += 1
                continue

            if msg.role == "tool":
                # 合并连续的 tool 消息为一条 user 消息
                tool_blocks: List[Dict[str, Any]] = []
                while i < len(messages) and messages[i].role == "tool":
                    tmsg = messages[i]
                    tool_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tmsg.tool_call_id or "",
                            "content": tmsg.content or "",
                        }
                    )
                    i += 1
                out.append({"role": "user", "content": tool_blocks})
                continue

            if msg.role == "assistant" and msg.tool_calls:
                blocks: List[Dict[str, Any]] = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments or {},
                        }
                    )
                out.append({"role": "assistant", "content": blocks})
                i += 1
                continue

            out.append({"role": msg.role, "content": msg.content or ""})
            i += 1

        system = "\n\n".join(p for p in system_parts if p) or None
        return system, out

    def _to_anthropic_tools(self, tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        """OpenAI function schema -> Anthropic tool 定义。"""
        if not tools:
            return None
        result: List[Dict[str, Any]] = []
        for tool in tools:
            fn = tool.get("function", tool)
            result.append(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
                }
            )
        return result

    def _to_output(self, message: Any) -> ModelOutput:
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_calls: List[ToolCallSpec] = []
        for block in getattr(message, "content", []) or []:
            btype = getattr(block, "type", "")
            if btype == "text":
                content_parts.append(block.text)
            elif btype == "thinking":
                reasoning_parts.append(getattr(block, "thinking", "") or "")
            elif btype == "redacted_thinking":
                reasoning_parts.append("[思考内容已脱敏]")
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCallSpec(
                        id=block.id,
                        name=block.name,
                        arguments=dict(block.input or {}),
                    )
                )
        finish = {
            "end_turn": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
        }.get(getattr(message, "stop_reason", None) or "", "stop")
        usage = None
        if getattr(message, "usage", None):
            usage = ModelUsage(
                input_tokens=message.usage.input_tokens or 0,
                output_tokens=message.usage.output_tokens or 0,
                total_tokens=(message.usage.input_tokens or 0) + (message.usage.output_tokens or 0),
            )
        output = ModelOutput(
            content="\n".join(content_parts),
            reasoning="\n".join(reasoning_parts),
            tool_calls=tool_calls or None,
            usage=usage,
            finish_reason=finish,
        )
        return output

    def _check_cancel(self, params: Dict[str, Any]) -> bool:
        event = params.get("_cancel_event")
        return isinstance(event, threading.Event) and event.is_set()

    # ── 生成 ─────────────────────────────────────────────

    def generate(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]],
        params: Dict[str, Any],
    ) -> ModelOutput:
        client = self._get_client()
        system, anthropic_messages = self._to_anthropic_messages(messages)
        kwargs: Dict[str, Any] = {
            "model": params.get("model_name") or self.model_name,
            "messages": anthropic_messages,
            "temperature": float(params.get("temperature", 1.0)),
        }
        if system:
            kwargs["system"] = system
        max_tokens = int(params.get("max_tokens") or 4096)
        kwargs["max_tokens"] = max_tokens
        anthropic_tools = self._to_anthropic_tools(tools)
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
        message = client.messages.create(**kwargs)
        return self._to_output(message)

    def stream(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]],
        params: Dict[str, Any],
    ) -> Iterator[ModelStreamChunk]:
        client = self._get_client()
        system, anthropic_messages = self._to_anthropic_messages(messages)
        kwargs: Dict[str, Any] = {
            "model": params.get("model_name") or self.model_name,
            "messages": anthropic_messages,
            "temperature": float(params.get("temperature", 1.0)),
            "max_tokens": int(params.get("max_tokens") or 4096),
            "stream": True,
        }
        if system:
            kwargs["system"] = system
        anthropic_tools = self._to_anthropic_tools(tools)
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        stream = client.messages.create(**kwargs)

        # 按 index 聚合 tool_use 增量
        pending: Dict[int, Dict[str, Any]] = {}
        input_tokens = 0
        output_tokens = 0
        finish_reason = ""

        for event in stream:
            if self._check_cancel(params):
                return
            etype = getattr(event, "type", "")
            if etype == "message_start":
                usage = getattr(event, "message", None)
                usage = getattr(usage, "usage", None) if usage else None
                if usage:
                    input_tokens = getattr(usage, "input_tokens", 0) or 0
            elif etype == "content_block_start":
                block = event.content_block
                if getattr(block, "type", "") == "tool_use":
                    pending[getattr(event, "index", len(pending))] = {
                        "id": getattr(block, "id", ""),
                        "name": getattr(block, "name", ""),
                        "args_parts": [],
                    }
            elif etype == "content_block_delta":
                delta = event.delta
                dtype = getattr(delta, "type", "")
                if dtype == "text_delta" and getattr(delta, "text", None):
                    yield ModelStreamChunk(delta_content=delta.text)
                elif dtype == "thinking_delta" and getattr(delta, "thinking", None):
                    # Claude 扩展思考：思维链增量（未开启 thinking 时不会出现）
                    yield ModelStreamChunk(reasoning=delta.thinking)
                elif dtype == "input_json_delta" and getattr(delta, "partial_json", None):
                    idx = getattr(event, "index", None)
                    if idx in pending:
                        pending[idx]["args_parts"].append(delta.partial_json)
            elif etype == "message_delta":
                if getattr(event, "delta", None) and getattr(event.delta, "stop_reason", None):
                    finish_reason = {
                        "end_turn": "stop",
                        "tool_use": "tool_calls",
                        "max_tokens": "length",
                    }.get(event.delta.stop_reason, event.delta.stop_reason)
                usage = getattr(event, "usage", None)
                if usage:
                    output_tokens = getattr(usage, "output_tokens", 0) or 0

        for idx in sorted(pending):
            slot = pending[idx]
            try:
                arguments = json.loads("".join(slot["args_parts"]) or "{}")
            except json.JSONDecodeError:
                arguments = {}
            yield ModelStreamChunk(
                tool_call_delta=ToolCallSpec(
                    id=slot["id"] or f"toolu_{idx}",
                    name=slot["name"] or "",
                    arguments=arguments,
                )
            )

        total = input_tokens + output_tokens
        yield ModelStreamChunk(
            usage=ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total,
            ),
            finish_reason=finish_reason or ("tool_calls" if pending else "stop"),
        )

# Copyright (c) 2026 xingluosama121, MIT Licensed
"""OpenAI 兼容模型适配器：连接一切 OpenAI 协议服务。

覆盖 OpenAI / DeepSeek / Qwen(DashScope) / vLLM / Ollama 等
OpenAI 兼容端点。依赖 ``openai>=1.40``（norpagent[openai]），
客户端懒加载：未安装 SDK 时注册与配置不报错，首次调用给出
明确的安装提示。

用法::

    from norpagent.builtin.models.openai_compat import OpenAICompatProvider

    provider = OpenAICompatProvider(
        model_name="deepseek-v4-flash",           # 远端模型名
        base_url="https://api.deepseek.com/v1",   # 服务端点
        api_key_env="DEEPSEEK_API_KEY",           # API Key 环境变量名
    )

    reg.register_model("deepseek", provider)
    agent = AgentRuntime(reg, Preset(name="p", ..., model="deepseek", ...))

取消协作：params["_cancel_event"]（threading.Event，由内核注入）
被置位时，流式循环尽早停止，配合内核的 call_timeout 硬中断。
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, Iterator, List, Optional

from norpagent.protocols.model import (
    ChatMessage,
    ModelOutput,
    ModelStreamChunk,
    ModelUsage,
    ToolCallSpec,
)

# 支持 reasoning_effort 参数的推理系列模型名模式
# （DeepSeek V4 / GLM / Kimi / Qwen 等兼容端点不接受该参数，硬传会 400）
_EFFORT_MODEL_RE = re.compile(
    r"(^|[-_.])(o[0-9]+|gpt-5|deepseek-v4[a-z0-9]*)([-_.]|$)",
    re.IGNORECASE,
)

_DS_V4_RE = re.compile(r"^deepseek-v4([-_.]|$)", re.IGNORECASE)


def model_supports_reasoning_effort(model: str) -> bool:
    """判断模型名是否属于支持 reasoning_effort 的推理系列。

    覆盖 OpenAI 推理系列（o1 / o3 / gpt-5 等）与 DeepSeek V4
    （deepseek-v4-pro / deepseek-v4-flash）。"""
    return bool(_EFFORT_MODEL_RE.search(model or ""))


def model_is_deepseek_v4(model: str) -> bool:
    """判断模型名是否属于 DeepSeek V4 系列（思考模式由 thinking 参数控制）。"""
    return bool(_DS_V4_RE.match((model or "").strip()))


def normalize_effort(effort: str) -> str:
    """把调用方传入的推理强度规范化为各端点接受的值。

    DeepSeek V4 仅接受 low / high / max；官方映射：
    medium / xhigh → high。其余值（low / high / max / none / 空）原样返回。
    """
    e = str(effort or "").strip().lower()
    if e in ("medium", "xhigh"):
        return "high"
    return e


def _extract_reasoning(message: Any) -> tuple:
    """从响应 message / 流式 delta 中提取思维链字段。

    返回 ``(text, has_field)``。``has_field`` 表示响应中是否实际携带
    ``reasoning_content`` / ``reasoning`` 字段（即使值为空字符串）。

    DeepSeek V4 契约要求：发生工具调用的轮次，``reasoning_content``
    必须原样回传——空字符串也要保留字段——因此必须区分
    「字段缺失」与「字段存在但为空」两种情况。
    """
    fields_set = set(getattr(message, "model_fields_set", None) or ())
    text = (
        getattr(message, "reasoning_content", "")
        or getattr(message, "reasoning", "")
        or ""
    )
    has_field = "reasoning_content" in fields_set or "reasoning" in fields_set
    if not has_field:
        # 非 pydantic 响应对象（dict / SimpleNamespace 等）的兜底判断
        has_field = bool(
            getattr(message, "reasoning_content", None)
            or getattr(message, "reasoning", None)
        )
    return text, has_field


class OpenAICompatProvider:
    """OpenAI 兼容服务适配器。"""

    model_id = "openai_compat"

    def __init__(
        self,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_key_env: str = "OPENAI_API_KEY",
        timeout: Optional[float] = None,
    ) -> None:
        self.model_name = model_name or os.environ.get("NORPAGENT_OPENAI_MODEL", "") or "gpt-4o-mini"
        self.base_url = base_url or os.environ.get("NORPAGENT_OPENAI_BASE_URL", "") or None
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.timeout = timeout or 120.0
        self._client = None

    # ── SDK 懒加载 ───────────────────────────────────────

    def _get_client(self, params: Optional[Dict[str, Any]] = None) -> Any:
        # params 级覆盖：api_key / base_url / model_name 可在每次调用时
        # 动态指定（FE 前端节点「就近连线」把配置喂给模型节点时使用）。
        p = params or {}
        api_key = p.get("api_key") or self.api_key or os.environ.get(self.api_key_env, "")
        base_url = p.get("base_url") or self.base_url
        if not api_key:
            raise RuntimeError(
                f"未找到 API Key。请设置环境变量 {self.api_key_env}，"
                f"或构造 OpenAICompatProvider(api_key=...) 时显式传入。"
            )
        # 覆盖参数变化时重建客户端（缓存旧客户端会导致旧 Key 一直生效）
        cache_key = f"{api_key}|{base_url or ''}"
        if self._client is None or getattr(self._client, "_norp_cache_key", "") != cache_key:
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError(
                    "未安装 openai SDK。请执行: pip install norpagent[openai]"
                    "（或 pip install openai>=1.40）"
                )
            kwargs: Dict[str, Any] = {"api_key": api_key, "timeout": self.timeout}
            if base_url:
                kwargs["base_url"] = base_url
            self._client = OpenAI(**kwargs)
            try:
                self._client._norp_cache_key = cache_key  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 — SDK 拒绝挂属性时忽略缓存键
                pass
        return self._client

    # ── 协议转换 ─────────────────────────────────────────

    def _to_messages(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        return [m.to_openai() for m in messages]

    def _to_output(self, message: Any) -> ModelOutput:
        content = message.content or ""
        reasoning, has_reasoning = _extract_reasoning(message)
        tool_calls: Optional[List[ToolCallSpec]] = None
        if getattr(message, "tool_calls", None):
            tool_calls = []
            for tc in message.tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                tool_calls.append(
                    ToolCallSpec(id=tc.id or f"call_{len(tool_calls)}", name=tc.function.name, arguments=arguments)
                )
        return ModelOutput(
            content=content,
            reasoning=reasoning,
            has_reasoning=has_reasoning,
            tool_calls=tool_calls,
            finish_reason=getattr(message, "finish_reason", None) or ("tool_calls" if tool_calls else "stop"),
        )

    def _check_cancel(self, params: Dict[str, Any]) -> bool:
        event = params.get("_cancel_event")
        return isinstance(event, threading.Event) and event.is_set()

    # ── 请求参数组装 ─────────────────────────────────────

    def _build_request_kwargs(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]],
        params: Dict[str, Any],
        stream: bool,
    ) -> Dict[str, Any]:
        """组装 chat.completions 请求参数。

        - reasoning_effort：仅对支持的推理系列模型透传（其它端点会 400），
          并按端点规范映射强度（DeepSeek V4：medium/xhigh → high）；
        - DeepSeek V4 思考模式：经 ``extra_body.thinking`` 显式开/关
          （官方默认开启；显式关闭后 temperature 才生效）；
        - OpenAI 推理系列（o*/gpt-5）无法关闭推理：不传 temperature，
          避免 400（推理开启时 OpenAI 禁止同时传温度）。
        """
        model = params.get("model_name") or self.model_name
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": self._to_messages(messages),
            "stream": stream,
        }
        effort = normalize_effort(str(params.get("reasoning_effort") or ""))
        supports = model_supports_reasoning_effort(model)
        is_v4 = model_is_deepseek_v4(model)
        if effort and effort != "none":
            if supports:
                kwargs["reasoning_effort"] = effort
                if is_v4:
                    kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            else:
                kwargs["temperature"] = float(params.get("temperature", 1.0))
        else:
            # 未指定强度 / 显式关闭推理
            if is_v4:
                if effort == "none":
                    kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                # 未指定时保持官方默认（thinking 开启，temperature 被忽略但不报错）
                kwargs["temperature"] = float(params.get("temperature", 1.0))
            elif not supports:
                kwargs["temperature"] = float(params.get("temperature", 1.0))
            # supports 且非 v4（OpenAI o*/gpt-5）：推理不可关闭，不传温度
        max_tokens = params.get("max_tokens")
        if max_tokens:
            kwargs["max_tokens"] = int(max_tokens)
        if tools:
            kwargs["tools"] = tools
        return kwargs

    # ── 生成 ─────────────────────────────────────────────

    def generate(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]],
        params: Dict[str, Any],
    ) -> ModelOutput:
        client = self._get_client(params)
        kwargs = self._build_request_kwargs(messages, tools, params, stream=False)
        resp = client.chat.completions.create(**kwargs)
        usage = None
        if getattr(resp, "usage", None):
            usage = ModelUsage(
                input_tokens=resp.usage.prompt_tokens or 0,
                output_tokens=resp.usage.completion_tokens or 0,
                total_tokens=resp.usage.total_tokens or 0,
            )
        output = self._to_output(resp.choices[0].message)
        output.usage = usage
        return output

    def stream(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]],
        params: Dict[str, Any],
    ) -> Iterator[ModelStreamChunk]:
        client = self._get_client(params)
        kwargs = self._build_request_kwargs(messages, tools, params, stream=True)
        try:
            kwargs["stream_options"] = {"include_usage": True}
        except Exception:  # 旧版本 SDK 不支持时忽略
            pass

        stream_resp = client.chat.completions.create(**kwargs)

        # 工具调用增量聚合（OpenAI 流式按 index 分片）
        pending: Dict[int, Dict[str, Any]] = {}
        finish_reason = ""
        for chunk in stream_resp:
            if self._check_cancel(params):
                return
            if not chunk.choices:
                usage = getattr(chunk, "usage", None)
                if usage:
                    yield ModelStreamChunk(
                        usage=ModelUsage(
                            input_tokens=usage.prompt_tokens or 0,
                            output_tokens=usage.completion_tokens or 0,
                            total_tokens=usage.total_tokens or 0,
                        )
                    )
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta is None:
                continue
            # 思维链增量：DeepSeek V4（reasoning_content）/ OpenAI 推理系列（reasoning）
            # 字段单独传输；即使为空字符串也记录 has_reasoning，
            # 供工具调用轮次的原样回传判定使用。
            reasoning, has_reasoning = _extract_reasoning(delta)
            if has_reasoning or reasoning:
                yield ModelStreamChunk(reasoning=reasoning, has_reasoning=has_reasoning)
            if getattr(delta, "content", None):
                yield ModelStreamChunk(delta_content=delta.content)
            if getattr(delta, "tool_calls", None):
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    slot = pending.setdefault(idx, {"id": "", "name": "", "args_parts": []})
                    if getattr(tc_delta, "id", None):
                        slot["id"] = tc_delta.id
                    if getattr(tc_delta.function, "name", None):
                        slot["name"] = tc_delta.function.name
                    if getattr(tc_delta.function, "arguments", None):
                        slot["args_parts"].append(tc_delta.function.arguments)
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason

        # 流结束：产出完整工具调用
        for idx in sorted(pending):
            slot = pending[idx]
            try:
                arguments = json.loads("".join(slot["args_parts"]) or "{}")
            except json.JSONDecodeError:
                arguments = {}
            yield ModelStreamChunk(
                tool_call_delta=ToolCallSpec(
                    id=slot["id"] or f"call_{idx}",
                    name=slot["name"] or "",
                    arguments=arguments,
                )
            )
        yield ModelStreamChunk(finish_reason=finish_reason or ("tool_calls" if pending else "stop"))

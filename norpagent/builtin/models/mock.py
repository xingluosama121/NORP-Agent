# Copyright (c) 2026 xingluosama121, MIT Licensed
"""Mock 模型：确定性脚本应答，专为基准测试与框架调试设计。

不依赖任何网络或 SDK。``script`` 为应答脚本列表，按调用次序取用，
最后一条重复使用：

    MockModelProvider(script=[
        {"tool_call": {"name": "echo", "args": {"text": "hi"}}},  # 第 1 轮：发起工具调用
        {"reasoning": "先思考再回答", "content": "done"},         # 第 2 轮：思维链 + 最终回答
    ])

条目字段：
- ``content``：正文回复；
- ``tool_call``：工具调用（{name, args}）；
- ``reasoning``：思维链文本（流式时按小段产出，随 content 一起返回）；
- ``has_reasoning``：模拟响应是否携带 reasoning 字段（默认 reasoning 非空为 True；
  用于验证 DeepSeek V4 工具轮次 reasoning_content 回传契约，含空串保留字段的场景）。

未配置 script 时返回引导性提示（说明如何接入真实模型），
便于首次运行的用户明白「mock 模型没有配置」而非无声失败。

基准测试场景：给被测 Agent 装配固定脚本，比较不同实现（模型/工具/会话）
在同一输入下的行为差异与资源消耗。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional

from norpagent.protocols.model import (
    ChatMessage,
    ModelOutput,
    ModelStreamChunk,
    ModelUsage,
    ToolCallSpec,
)

# 未配置脚本时的引导性回复（保留 "mock:" 前缀便于日志/测试识别）
_MOCK_GUIDANCE = (
    "mock: 未配置应答脚本（script 参数），当前回复来自内置 mock 模型，"
    "仅用于验证链路连通性。\n\n"
    "接入真实模型：\n"
    "1. 在前端「设置」中选择模型并填入 API Key；或\n"
    "2. 代码方式：np(model=\"openai_compat\", model_name=\"deepseek-v4-flash\", api_key=\"...\")；或\n"
    "3. 给 mock 指定脚本：MockModelProvider(script=[{\"content\": \"...\"}])。\n\n"
    "工具集已随预设注册（echo / file_read / exec_cmd / web_search 等），"
    "接入真实模型后即可正常调用。"
)


class MockModelProvider:
    """脚本化确定性模型。"""

    model_id = "mock"

    def __init__(self, script: Optional[List[Dict[str, Any]]] = None) -> None:
        self.script = script or [{"content": _MOCK_GUIDANCE}]
        self._call_count = 0

    def _next_entry(self) -> Dict[str, Any]:
        idx = min(self._call_count, len(self.script) - 1)
        self._call_count += 1
        return self.script[idx]

    def _entry_to_output(self, entry: Dict[str, Any]) -> ModelOutput:
        reasoning = str(entry.get("reasoning") or "")
        has_reasoning = bool(entry.get("has_reasoning", bool(reasoning)))
        if "tool_call" in entry:
            spec = entry["tool_call"]
            if isinstance(spec, dict):
                args = spec.get("args") or spec.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args)
                call = ToolCallSpec(
                    id=spec.get("id") or f"mock_call_{self._call_count}",
                    name=spec["name"],
                    arguments=args,
                )
            else:
                raise ValueError(f"mock 脚本 tool_call 条目格式无效: {spec!r}")
            return ModelOutput(
                reasoning=reasoning,
                has_reasoning=has_reasoning,
                tool_calls=[call],
                finish_reason="tool_calls",
            )
        if "content" in entry or reasoning or has_reasoning:
            return ModelOutput(
                content=str(entry.get("content") or ""),
                reasoning=reasoning,
                has_reasoning=has_reasoning,
                finish_reason="stop",
            )
        raise ValueError(f"mock 脚本条目无效（需含 content / tool_call / reasoning）: {entry!r}")

    def generate(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]],
        params: Dict[str, Any],
    ) -> ModelOutput:
        # 任务级可覆盖脚本（params["mock_script"]）
        override = params.get("mock_script")
        if isinstance(override, list):
            entry = override[min(self._call_count, len(override) - 1)]
            self._call_count += 1
        else:
            entry = self._next_entry()
        output = self._entry_to_output(entry)
        output.usage = ModelUsage(
            input_tokens=sum(len(m.content) // 3 + 1 for m in messages),
            output_tokens=max(1, len(output.content) // 3),
        )
        output.usage.total_tokens = output.usage.input_tokens + output.usage.output_tokens
        return output

    def stream(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Dict[str, Any]]],
        params: Dict[str, Any],
    ) -> Iterator[ModelStreamChunk]:
        output = self.generate(messages, tools, params)
        # 工具调用一次性给出；思维链与正文按小段流式产出
        for spec in output.tool_calls or []:
            yield ModelStreamChunk(
                tool_call_delta=spec, has_reasoning=output.has_reasoning
            )
        step = 8
        pieces: List[tuple] = (
            [("reasoning", output.reasoning)] if output.reasoning else []
        ) + ([("content", output.content)] if output.content else [])
        for kind, text in pieces:
            for i in range(0, len(text), step):
                piece = text[i : i + step]
                is_last = i + step >= len(text)
                kwargs: Dict[str, Any] = {}
                if kind == "reasoning":
                    kwargs["reasoning"] = piece
                    kwargs["has_reasoning"] = output.has_reasoning
                else:
                    kwargs["delta_content"] = piece
                if is_last:
                    kwargs["finish_reason"] = output.finish_reason
                    kwargs["usage"] = output.usage
                yield ModelStreamChunk(**kwargs)
        if not output.content and not output.reasoning and output.usage:
            yield ModelStreamChunk(
                usage=output.usage, finish_reason=output.finish_reason,
                has_reasoning=output.has_reasoning,
            )

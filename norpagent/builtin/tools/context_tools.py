# Copyright (c) 2026 xingluosama121, MIT Licensed
"""上下文管理工具：context_add / context_search / context_list / context_delete。

让 Agent 把长周期任务中的中间结论、外部资料、代码片段写入可检索的
上下文库，跨会话、跨任务复用——解决「上下文窗口有限」的问题：

- context_add：写入一条上下文（带来源与可选元数据）；
- context_search：BM25 相关度检索（中英混合分词）；
- context_list / context_delete：浏览与清理。

组件通过 ``ctx.context_store`` 访问（预设 components 声明
``{"context_store": "fts5"}`` 时由运行时注入；未装配时工具返回明确报错）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from norpagent.protocols.tool import Tool, ToolResult


def _store(ctx: Any) -> Optional[Any]:
    return getattr(ctx, "context_store", None)


def _missing() -> ToolResult:
    return ToolResult(
        output=(
            "当前模式未装配上下文存储组件。请在预设中声明 "
            "components={\"context_store\": \"fts5\"}（standard 预设已内置），"
            "或 registry.register_component(\"context_store\", \"fts5\", ...)。"
        ),
        success=False,
        error="context_store 组件未装配",
    )


def _fmt_entry(entry: Dict[str, Any]) -> str:
    text = entry.get("text", "")
    if len(text) > 300:
        text = text[:300] + "..."
    head = f"#{entry.get('id')} [{entry.get('source')}] {entry.get('title') or ''}".strip()
    lines = [head, text]
    if entry.get("metadata"):
        lines.append(f"metadata: {json.dumps(entry['metadata'], ensure_ascii=False)}")
    return "\n".join(lines)


class ContextAddTool:
    name = "context_add"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "把一段文本写入可检索的上下文库（持久化）。适合保存长周期任务中的"
                    "结论、外部资料、代码片段、备忘等，后续用 context_search 检索复用，"
                    "突破模型上下文窗口限制。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要保存的文本内容"},
                        "source": {"type": "string", "description": "来源标签，如 task_plan / research / code_snippet（默认 manual）"},
                        "title": {"type": "string", "description": "简短标题，便于浏览（可选）"},
                        "metadata": {"type": "object", "description": "附加元数据，如 {topic: 网络模块}（可选）"},
                    },
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        store = _store(ctx)
        if store is None:
            return _missing()
        text = str(args.get("text") or "")
        if not text.strip():
            return ToolResult(output="text 参数为空。", success=False, error="empty_text")
        try:
            doc_id = store.add(
                text=text,
                source=str(args.get("source") or "manual"),
                title=str(args.get("title") or ""),
                metadata=args.get("metadata") if isinstance(args.get("metadata"), dict) else None,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"写入失败: {exc}", success=False, error=str(exc))
        return ToolResult(output=f"已保存到上下文库，条目 id={doc_id}。可用 context_search 检索。")


class ContextSearchTool:
    name = "context_search"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "在上下文库中做全文相关度检索（BM25 排序，中英混合分词）。"
                    "适合查找之前用 context_add 保存的资料、结论与代码片段。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索关键词或自然语言描述"},
                        "top_k": {"type": "integer", "description": "返回条数（默认 5，最大 50）"},
                        "source": {"type": "string", "description": "按来源过滤（可选）"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        store = _store(ctx)
        if store is None:
            return _missing()
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(output="query 参数为空。", success=False, error="empty_query")
        try:
            results = store.search(
                query,
                top_k=int(args.get("top_k") or 5),
                source=str(args.get("source") or "") or None,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"检索失败: {exc}", success=False, error=str(exc))
        if not results:
            return ToolResult(output=f"未找到与「{query}」相关的内容。")
        lines = ["[上下文检索结果]", "", f"查询: {query}", f"命中: {len(results)} 条", ""]
        for i, entry in enumerate(results, 1):
            lines.append(f"── {i}. (score={entry['score']}) ──")
            lines.append(_fmt_entry(entry))
            lines.append("")
        return ToolResult(output="\n".join(lines).rstrip())


class ContextListTool:
    name = "context_list"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "列出上下文库中的条目（按写入时间新→旧），可按来源过滤。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "按来源过滤（可选）"},
                        "limit": {"type": "integer", "description": "最多返回条数（默认 20，最大 200）"},
                    },
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        store = _store(ctx)
        if store is None:
            return _missing()
        try:
            entries = store.list(
                source=str(args.get("source") or "") or None,
                limit=int(args.get("limit") or 20),
            )
            stats = store.stats()
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"读取失败: {exc}", success=False, error=str(exc))
        lines = [
            "[上下文库]", "",
            f"总条目: {stats.get('total', '?')}",
            f"来源分布: {json.dumps(stats.get('sources', {}), ensure_ascii=False)}",
            "",
        ]
        if not entries:
            lines.append("（空）")
        for entry in entries:
            lines.append(_fmt_entry(entry))
            lines.append("")
        return ToolResult(output="\n".join(lines).rstrip())


class ContextDeleteTool:
    name = "context_delete"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "按条目 id 删除上下文库中的一条记录。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "条目 id（context_list / context_search 返回的 id）"},
                    },
                    "required": ["id"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        store = _store(ctx)
        if store is None:
            return _missing()
        try:
            doc_id = int(args.get("id"))
        except (TypeError, ValueError):
            return ToolResult(output="id 参数无效。", success=False, error="bad_id")
        try:
            ok = store.delete(doc_id)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"删除失败: {exc}", success=False, error=str(exc))
        if not ok:
            return ToolResult(output=f"条目 id={doc_id} 不存在。", success=False, error="not_found")
        return ToolResult(output=f"已删除条目 id={doc_id}。")

# Copyright (c) 2026 xingluosama121, MIT Licensed
"""文件读写工具：file_read / file_write / file_list / file_delete。

全部操作受工作区路径安全约束（见 pathsafe.py）：
相对路径 + 禁止 .. 穿越 + 锁定工作区根目录。

编码约定：UTF-8 写入；读取按 utf-8 -> gbk -> latin-1 顺序探测，
避免 Windows 中文代码页文件乱码（与现有应用 robust_decode 一致）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from norpagent.builtin.tools.pathsafe import (
    PathSafetyError,
    resolve_safe_path,
)
from norpagent.protocols.tool import Tool, ToolResult

# 全量读取上限：超过则要求用 start_line / end_line 分段读取
_MAX_FULL_READ_BYTES = 100 * 1024


def _robust_read_text(data: bytes) -> str:
    if not data:
        return ""
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_bytes(path: Path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


class FileReadTool:
    name = "file_read"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "读取工作区内文本文件的内容。超过 100KB 的文件需用 "
                    "start_line / end_line 分段读取（返回的每行带行号）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "相对工作区根目录的文件路径"},
                        "start_line": {"type": "integer", "description": "起始行号（从 1 开始），可选"},
                        "end_line": {"type": "integer", "description": "结束行号（含），可选"},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        try:
            target = resolve_safe_path(ctx, args.get("path", ""), must_exist=True)
        except PathSafetyError as exc:
            return ToolResult(output=str(exc), success=False, error=str(exc))
        if target.is_dir():
            return ToolResult(
                output=f"目标是目录而非文件: {args.get('path')}（可用 file_list 浏览）",
                success=False,
                error="is_directory",
            )
        try:
            data = _read_bytes(target)
            start = args.get("start_line")
            end = args.get("end_line")
            if start is None and end is None:
                if len(data) > _MAX_FULL_READ_BYTES:
                    return ToolResult(
                        output=(
                            f"文件过大（{len(data)} 字节），仅能部分读取。"
                            f"请用 start_line / end_line 分段读取。"
                            f"开头 {_MAX_FULL_READ_BYTES} 字节内容:\n"
                            f"{_format_numbered(_robust_read_text(data[:_MAX_FULL_READ_BYTES]))}"
                        ),
                        success=False,
                        error="file_too_large",
                    )
                return ToolResult(output=_format_numbered(_robust_read_text(data)))
            # 行号分段读取
            text = _robust_read_text(data)
            lines = text.splitlines()
            start = max(1, int(start or 1))
            end = min(len(lines), int(end or len(lines)))
            if start > len(lines):
                return ToolResult(output=f"起始行 {start} 超出文件总行数 {len(lines)}", success=False, error="bad_range")
            if end < start:
                return ToolResult(output=f"行号区间无效: start={start} > end={end}", success=False, error="bad_range")
            return ToolResult(output=_format_numbered("\n".join(lines[start - 1 : end]), start))
        except OSError as exc:
            return ToolResult(output=f"读取失败: {exc}", success=False, error=str(exc))


class FileWriteTool:
    name = "file_write"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "在工作区内写入（创建或覆盖）UTF-8 文本文件。"
                    "父目录不存在时自动创建。覆盖已有文件前应先用 file_read 确认内容。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "相对工作区根目录的文件路径"},
                        "content": {"type": "string", "description": "要写入的完整文本内容"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        try:
            target = resolve_safe_path(ctx, args.get("path", ""))
        except PathSafetyError as exc:
            return ToolResult(output=str(exc), success=False, error=str(exc))
        content = args.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            size = len(content.encode("utf-8"))
            return ToolResult(output=f"已写入 {target}（{size} 字节）")
        except OSError as exc:
            return ToolResult(output=f"写入失败: {exc}", success=False, error=str(exc))


class FileListTool:
    name = "file_list"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "列出工作区内目录内容（默认工作区根目录），"
                    "返回相对路径、类型（目录/文件）与文件大小。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "相对工作区根目录的目录路径，默认 '.'"},
                        "depth": {"type": "integer", "description": "递归深度（默认 2，最大 6）"},
                    },
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        try:
            target = resolve_safe_path(
                ctx, args.get("path", "") or ".", must_exist=True
            )
        except PathSafetyError as exc:
            return ToolResult(output=str(exc), success=False, error=str(exc))
        if not target.is_dir():
            return ToolResult(
                output=f"路径不是目录: {args.get('path')}", success=False, error="not_a_dir"
            )
        depth = max(1, min(int(args.get("depth") or 2), 6))
        root = workspace_root_for(ctx)
        lines: list[str] = []
        try:
            _walk(target, root, depth, 1, lines)
        except OSError as exc:
            return ToolResult(output=f"遍历失败: {exc}", success=False, error=str(exc))
        if not lines:
            return ToolResult(output="(空目录)")
        return ToolResult(output="\n".join(lines[:2000]) + ("\n...(超出 2000 条，已截断)" if len(lines) > 2000 else ""))


class FileDeleteTool:
    name = "file_delete"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "删除工作区内的文件或目录（目录将递归删除）。"
                    "此操作不可恢复，执行前必须确认目标正确。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "相对工作区根目录的文件或目录路径"},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        try:
            target = resolve_safe_path(ctx, args.get("path", ""), must_exist=True)
        except PathSafetyError as exc:
            return ToolResult(output=str(exc), success=False, error=str(exc))
        # 拒绝删除工作区根目录本身
        try:
            root = workspace_root_for(ctx)
        except Exception:
            root = None
        if root is not None and target == root:
            return ToolResult(output="禁止删除工作区根目录", success=False, error="forbidden")
        try:
            if target.is_dir():
                import shutil

                shutil.rmtree(target)
                return ToolResult(output=f"已删除目录 {target}")
            target.unlink()
            return ToolResult(output=f"已删除文件 {target}")
        except OSError as exc:
            return ToolResult(output=f"删除失败: {exc}", success=False, error=str(exc))


def workspace_root_for(ctx: Any) -> Path:
    from norpagent.builtin.tools.pathsafe import workspace_root

    return workspace_root(ctx)


def _format_numbered(text: str, start_line: int = 1) -> str:
    """给文本每行加行号（file_read 返回格式）。"""
    if not text:
        return "(空文件)"
    lines = text.splitlines()
    width = len(str(start_line + len(lines)))
    return "\n".join(f"{i + start_line:>{width}}| {ln}" for i, ln in enumerate(lines))


def _walk(dir_path: Path, root: Path, depth: int, level: int, out: list[str]) -> None:
    if level > depth:
        return
    entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    for entry in entries:
        rel = entry.relative_to(root)
        if entry.is_dir():
            out.append(f"[目录] {rel}/")
            _walk(entry, root, depth, level + 1, out)
        elif entry.is_file():
            size = entry.stat().st_size
            out.append(f"[文件] {rel}  ({size} 字节)")
        else:
            out.append(f"[其他] {rel}")

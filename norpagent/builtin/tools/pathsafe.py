# Copyright (c) 2026 xingluosama121, MIT Licensed
"""工作区路径安全：文件类工具的公共防护层。

规则（与框架安全约束一致）：
- 工具参数中的路径必须为相对路径，拒绝绝对系统路径；
- 拒绝 ``..`` 路径穿越（含符号链接解析后的穿越）；
- 所有操作被解析并锁定在工作区根目录之内。

工作区根目录取值顺序：
    ctx.params["workspace_root"]（预设或任务级参数）> 当前进程工作目录。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class PathSafetyError(Exception):
    """路径越界 / 非法。"""


def workspace_root(ctx: Any) -> Path:
    """取工作区根目录（绝对路径）。"""
    root = getattr(ctx, "params", None) or {}
    if isinstance(root, dict):
        root = root.get("workspace_root", "") or ""
    else:
        root = ""
    return Path(root).resolve() if root else Path(os.getcwd()).resolve()


def resolve_safe_path(ctx: Any, path: str, must_exist: bool = False) -> Path:
    """把用户提供的相对路径解析为工作区内的绝对路径。

    ``path`` 必须是相对路径（POSIX / Windows 分隔符均可），
    允许是 ".". 解析失败抛出 PathSafetyError。
    """
    if path is None or not str(path).strip():
        raise PathSafetyError("路径为空")

    raw = str(path).strip()
    if raw.startswith("\\") or raw.startswith("/") or (
        len(raw) >= 2 and raw[1] == ":"
    ):
        raise PathSafetyError(f"禁止绝对路径: {path}")

    root = workspace_root(ctx)
    resolved = (root / raw).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        raise PathSafetyError(
            f"路径越界（不允许 .. 穿越或符号链接跳出工作区）: {path}"
        )
    if relative == Path("..") or ".." in relative.parts:
        raise PathSafetyError(f"路径越界（不允许 .. 穿越）: {path}")
    if must_exist and not resolved.exists():
        raise PathSafetyError(f"路径不存在: {path}")
    return resolved


def format_path_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"

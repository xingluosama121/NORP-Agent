# Copyright (c) 2026 xingluosama121, MIT Licensed
"""项目管理工具：project_status。

汇总工作区项目状态：元数据、文件统计、最近修改、git 分支与变更。
组件通过 ``ctx.project_manager`` 访问（预设 components 声明
``{"project_manager": "basic"}`` 时由运行时注入）。
"""

from __future__ import annotations

from typing import Any, Dict

from norpagent.protocols.tool import Tool, ToolResult


def _fmt_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{n}B"


class ProjectStatusTool:
    name = "project_status"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "查看当前工作区项目的状态：项目元数据、文件数量与大小、"
                    "扩展名分布、最近修改的文件、git 分支与未提交变更。"
                    "适合在动手修改前了解项目全貌。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "recent_files": {
                            "type": "integer",
                            "description": "最近修改文件显示数量（默认 10）",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        manager = ctx.project_manager
        if manager is None:
            return ToolResult(
                output=(
                    "当前模式未装配项目管理组件。请在预设中声明 "
                    "components={\"project_manager\": \"basic\"}（standard 预设已内置）。"
                ),
                success=False,
                error="project_manager 组件未装配",
            )
        try:
            recent_n = max(1, min(int(args.get("recent_files") or 10), 50))
            status = manager.status()
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"读取项目状态失败: {exc}", success=False, error=str(exc))

        meta = status.get("meta") or {}
        scan = status.get("scan") or {}
        lines = ["[项目状态]", ""]
        lines.append(f"项目: {meta.get('name', '?')}（{scan.get('root', '?')}）")
        if meta.get("description"):
            lines.append(f"描述: {meta['description']}")
        lines.append(
            f"任务统计: 共 {meta.get('tasks_total', 0)} 次 / 成功 {meta.get('tasks_success', 0)} 次"
        )
        lines.append("")
        if scan.get("exists"):
            lines.append("── 工作区 ──")
            lines.append(
                f"文件: {scan['files']:,} 个 / 目录: {scan['dirs']:,} 个 / "
                f"总大小: {_fmt_bytes(scan['total_bytes'])}"
            )
            exts = scan.get("extensions") or {}
            if exts:
                lines.append(
                    "扩展名: " + ", ".join(f"{e}×{n}" for e, n in exts.items())
                )
            recent = scan.get("recent_files") or []
            if recent:
                lines.append(f"最近修改（前 {recent_n} 个）:")
                for item in recent[:recent_n]:
                    age = item.get("age_seconds", 0)
                    if age < 60:
                        age_s = f"{int(age)}s"
                    elif age < 3600:
                        age_s = f"{int(age // 60)}min"
                    elif age < 86400:
                        age_s = f"{age / 3600:.1f}h"
                    else:
                        age_s = f"{age / 86400:.1f}d"
                    lines.append(
                        f"  {item['path']}（{_fmt_bytes(item['size'])}，{age_s}前）"
                    )
        else:
            lines.append(f"工作区目录不存在: {scan.get('root')}")
        git = status.get("git")
        lines.append("")
        if git:
            lines.append("── Git ──")
            lines.append(
                f"分支: {git.get('branch') or '(detached)'} / "
                f"变更: {git.get('changes', 0)}（已暂存 {git.get('staged', 0)}，"
                f"未跟踪 {git.get('untracked', 0)}）"
            )
            commits = git.get("recent_commits") or []
            if commits:
                lines.append("最近提交:")
                lines.extend(f"  {c}" for c in commits)
        else:
            lines.append("── Git ──")
            lines.append("（当前工作区不是 git 仓库）")
        return ToolResult(output="\n".join(lines))

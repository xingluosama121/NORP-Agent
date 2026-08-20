# Copyright (c) 2026 xingluosama121, MIT Licensed
"""exec_cmd 工具：在任务沙箱中执行 shell 命令。

命令执行不直接调 subprocess，而是通过 ctx.sandbox（Sandbox 协议）：
替换沙箱实现（如 P3 的沙箱池 / 容器 / 资源限制版）时，
工具与预设都无需修改。

cwd 参数同样受工作区路径安全约束。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from norpagent.builtin.tools.pathsafe import PathSafetyError, resolve_safe_path
from norpagent.protocols.tool import Tool, ToolResult

_MAX_TIMEOUT = 300.0
_DEFAULT_TIMEOUT = 60.0


class ExecCmdTool:
    name = "exec_cmd"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "在沙箱中执行 shell 命令并返回 stdout / stderr / 退出码。"
                    "跨平台：Windows 走 cmd，POSIX 走 /bin/sh。"
                    "适合运行测试、构建、版本控制等工程命令；"
                    "长时间运行的任务请用后台方式或拆分多步。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "要执行的 shell 命令"},
                        "timeout": {"type": "number", "description": f"超时秒数（默认 {_DEFAULT_TIMEOUT}，最大 {_MAX_TIMEOUT}）"},
                        "cwd": {"type": "string", "description": "工作目录（相对工作区根目录，可选）"},
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        command = (args.get("command") or "").strip()
        if not command:
            return ToolResult(output="command 参数为空", success=False, error="缺少命令")

        timeout = _clamp_timeout(args.get("timeout"))

        cwd: Optional[str] = None
        raw_cwd = args.get("cwd")
        if raw_cwd:
            try:
                cwd = str(resolve_safe_path(ctx, str(raw_cwd), must_exist=True))
            except PathSafetyError as exc:
                return ToolResult(output=str(exc), success=False, error=str(exc))

        sandbox = getattr(ctx, "sandbox", None)
        if sandbox is None or not hasattr(sandbox, "run_shell"):
            return ToolResult(
                output="当前任务未配置沙箱，无法执行命令。"
                "请在预设中指定 sandbox（如 'subprocess'）。",
                success=False,
                error="no_sandbox",
            )

        try:
            result = sandbox.run_shell(command, timeout=timeout, cwd=cwd)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output=f"命令执行异常: {exc}", success=False, error=str(exc))

        parts: list[str] = [f"[exec_cmd] $ {command}"]
        if result.stdout:
            parts.append("[stdout]\n" + result.stdout)
        if result.stderr:
            parts.append("[stderr]\n" + result.stderr)
        if result.timed_out:
            parts.append(f"(命令超时，已终止：{timeout}s)")
        parts.append(f"(exit_code={result.exit_code})")
        output = "\n".join(parts)
        return ToolResult(
            output=output,
            success=result.ok,
            error="" if result.ok else (result.stderr or f"exit_code={result.exit_code}"),
        )


def _clamp_timeout(raw: Any) -> float:
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT
    if timeout <= 0:
        return _DEFAULT_TIMEOUT
    return min(timeout, _MAX_TIMEOUT)

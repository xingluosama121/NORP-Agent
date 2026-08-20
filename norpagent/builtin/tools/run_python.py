# Copyright (c) 2026 xingluosama121, MIT Licensed
"""run_python 工具：PTC 模式的执行核心（P4 起沙箱执行）。

PTC（Programmatic Tool Composition）：模型不直接发起多个工具调用，
而是生成一段 Python 代码，在代码中通过 ``call_tool(name, **args)``
组合多步工具调用（条件、循环、聚合），框架执行代码并回传结果。

P4 执行模型：代码在**沙箱子进程**中隔离执行（AST 静态预检 +
受限 builtins + 干净函数命名空间 + 进程隔离 + 超时强杀 + 输出上限），
代码内的 ``call_tool`` 经协议通道回传给宿主执行（走注册表，与
Agent 共享同一套工具/组件）。沙箱未实现 ``run_python`` 时回退到
进程内受限执行（P1 兼容路径，输出中标注 isolation=inproc）。
"""

from __future__ import annotations

import contextlib
import io
from typing import Any, Dict

from norpagent.builtin.sandboxes.isolated_python import (
    _ALLOWED_BUILTIN_NAMES,
    check_ptc_source,
)
from norpagent.protocols.tool import Tool, ToolResult

# 受限 builtins 白名单：无 import / open / exec / eval / type 等危险入口
_ALLOWED_BUILTINS: Dict[str, Any] = {
    name: getattr(__import__("builtins"), name)
    for name in _ALLOWED_BUILTIN_NAMES
}


class RunPythonTool:
    name = "run_python"

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "执行一段 Python 代码（沙箱隔离）。代码可通过 "
                    "call_tool(工具名, **参数) 组合调用 Agent 已注册的任意工具，"
                    "实现多步、条件、循环式工具编排。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "要执行的 Python 代码。可用 call_tool(name, **args) 调用工具。",
                        },
                    },
                    "required": ["code"],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        code = args.get("code", "")
        if not code or not isinstance(code, str):
            return ToolResult(output="code 参数为空", success=False, error="缺少代码")

        # ── AST 静态预检（主安全边界，沙箱与回退路径共用）──
        allowed, reason = check_ptc_source(code)
        if not allowed:
            return ToolResult(
                output=f"[run_python 安全拦截] {reason}",
                success=False,
                error=reason,
            )

        params = getattr(ctx, "params", {}) or {}
        timeout = float(params.get("ptc_timeout", 60) or 60)

        # ── 沙箱执行（首选）：preset 声明的 sandbox 若实现 run_python ──
        sandbox = getattr(ctx, "sandbox", None)
        runner = getattr(sandbox, "run_python", None)
        if callable(runner):
            result = runner(code, self._make_dispatcher(ctx), timeout)
            output = result.stdout.strip()
            if result.timed_out:
                return ToolResult(
                    output=(
                        (output + "\n" if output else "")
                        + f"[run_python 超时] 代码执行超过 {timeout}s，进程已强杀"
                    ).strip(),
                    success=False,
                    error=f"超时: ptc_timeout({timeout}s)，进程已强杀",
                )
            if result.exit_code != 0:
                err = result.stderr.strip() or "沙箱执行失败"
                return ToolResult(
                    output=(
                        (output + "\n" if output else "") + f"[run_python 异常] {err}"
                    ).strip(),
                    success=False,
                    error=err,
                )
            if not output:
                output = "(代码执行完成，无输出)"
            return ToolResult(output=output)

        # ── 回退：进程内受限执行（P1 兼容，标注隔离等级）──
        return self._run_inproc(code, ctx)

    @staticmethod
    def _make_dispatcher(ctx: Any):
        """构造宿主侧工具调度器（代码内 call_tool 的 RPC 服务端）。"""

        def dispatch(name: str, args: Dict[str, Any]) -> str:
            tool = ctx.registry.resolve_tool(name)
            result = tool.run(args or {}, ctx)
            if result.success:
                return result.output
            raise RuntimeError(result.error or result.output)

        return dispatch

    def _run_inproc(self, code: str, ctx: Any) -> ToolResult:
        def call_tool(name: str, **kwargs: Any) -> str:
            """PTC 代码内调用已注册工具的统一入口。"""
            tool = ctx.registry.resolve_tool(name)
            result = tool.run(kwargs, ctx)
            if result.success:
                return result.output
            raise RuntimeError(result.error or result.output)

        sandbox_globals: Dict[str, Any] = {
            "__builtins__": _ALLOWED_BUILTINS,
            "call_tool": call_tool,
        }
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                exec(compile(code, "<run_python>", "exec"), sandbox_globals)  # noqa: S102
        except Exception as exc:  # noqa: BLE001
            printed = buffer.getvalue()
            return ToolResult(
                output=(printed + f"\n[run_python 异常] {type(exc).__name__}: {exc}").strip(),
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        output = buffer.getvalue().strip()
        if not output:
            output = "(代码执行完成，无输出)"
        return ToolResult(output=output)

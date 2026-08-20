# Copyright (c) 2026 xingluosama121, MIT Licensed
"""沙箱协议：Agent 的「隔离执行环境」抽象。

P1 提供子进程命令沙箱（跨平台、无第三方依赖）。
P3 将迁移现有应用的沙箱池、资源限制与进程级插件隔离能力。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol, runtime_checkable


@dataclass
class SandboxResult:
    """沙箱执行结果。"""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@runtime_checkable
class Sandbox(Protocol):
    """一个已创建的沙箱实例。"""

    def run_shell(
        self,
        command: str,
        timeout: float = 60.0,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> SandboxResult:
        """在沙箱中执行 shell 命令并返回结果。"""
        ...

    def close(self) -> None:
        """释放沙箱资源。"""
        ...


@runtime_checkable
class PythonSandbox(Protocol):
    """支持 Python 代码隔离执行的沙箱（PTC / 代码执行类工具）。

    可选能力：未实现 ``run_python`` 的沙箱由工具自行回退
    （如 RunPythonTool 回退到进程内受限执行）。
    """

    def run_python(
        self,
        code: str,
        tool_dispatch: Callable[[str, Dict[str, Any]], str],
        timeout: float = 60.0,
    ) -> SandboxResult:
        """在沙箱中执行 Python 代码。

        ``tool_dispatch(name, args) -> str`` 是宿主侧工具调用服务：
        代码内 ``call_tool(...)`` 的请求经沙箱转发到该回调执行，
        返回值回传给代码；回调抛异常时代码侧收到 RuntimeError。
        """
        ...


@runtime_checkable
class SandboxProvider(Protocol):
    """沙箱提供者：按需创建沙箱实例（池化、容器等策略由实现决定）。"""

    kind: str  # 例如 "subprocess" / "docker" / "pooled"

    def create(self) -> Sandbox:
        """创建（或从池中借出）一个沙箱。"""
        ...

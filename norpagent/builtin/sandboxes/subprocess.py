# Copyright (c) 2026 xingluosama121, MIT Licensed
"""子进程沙箱：跨平台命令执行沙箱（无第三方依赖）。

P1 提供基础隔离（独立进程 + 超时 + 输出捕获）。
P3 将迁移现有应用的沙箱池（复用/并发上限）、资源限制与网络策略。

取消语义：run_shell 分片等待（每 ≤0.5s 检查一次取消事件），
Ctrl+C / 引擎停止时立即强杀进程树并返回 exit_code=-1
（stderr 注明「任务已取消」）；超时同样强杀进程树。
单分片的 TimeoutExpired 只是「这一片到期」，累计达到 timeout
才算真正超时。

编码说明：按 utf-8 → gbk → latin-1 顺序解码子进程输出，
避免 Windows 中文代码页输出乱码（与现有应用 robust_decode 一致）。
"""

from __future__ import annotations

import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from norpagent.builtin.sandboxes.pooled import (
    _kill_process_tree,
    _robust_decode,
    _stream_reader,
)
from norpagent.loops.cancel import cancel_requested
from norpagent.protocols.sandbox import SandboxResult


class SubprocessSandbox:
    """单个子进程沙箱实例（每次 run_shell 启动新进程）。

    P4 起支持 ``run_python``：PTC 代码在独立子进程中执行，
    工具调用经长度前缀协议回传给宿主（见 isolated_python）。
    """

    def run_shell(
        self,
        command: str,
        timeout: float = 60.0,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> SandboxResult:
        try:
            proc = subprocess.Popen(
                command,
                shell=True,  # 跨平台：Windows 走 cmd，POSIX 走 /bin/sh
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        except OSError as exc:
            return SandboxResult(stderr=str(exc), exit_code=-1)

        out_buf: List[bytes] = []
        err_buf: List[bytes] = []
        done_out = threading.Event()
        done_err = threading.Event()
        readers = [
            threading.Thread(
                target=_stream_reader, args=(proc.stdout, out_buf, done_out),
                daemon=True,
            ),
            threading.Thread(
                target=_stream_reader, args=(proc.stderr, err_buf, done_err),
                daemon=True,
            ),
        ]
        for t in readers:
            t.start()

        try:
            cancelled = False
            timed_out = False
            exit_code = 0
            started = time.monotonic()
            while True:
                if cancel_requested():
                    _kill_process_tree(proc)
                    cancelled = True
                    exit_code = -1
                    break
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    timed_out = True
                    _kill_process_tree(proc)
                    try:
                        proc.wait(3)
                    except Exception:
                        pass
                    exit_code = -1
                    break
                try:
                    exit_code = proc.wait(timeout=min(remaining, 0.5))
                    break
                except subprocess.TimeoutExpired:
                    continue  # 分片到期，继续下一片
        finally:
            for t, done in ((readers[0], done_out), (readers[1], done_err)):
                if t.is_alive():
                    done.wait(1.0)
            for stream in (proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass

        stdout = _robust_decode(b"".join(out_buf)).strip()
        stderr = _robust_decode(b"".join(err_buf)).strip()
        if cancelled:
            stderr = stderr or "任务已取消（Ctrl+C），命令进程已终止"
        elif timed_out:
            stderr = stderr or f"命令超时({timeout}s)"
        return SandboxResult(
            stdout=stdout, stderr=stderr,
            exit_code=exit_code, timed_out=timed_out,
        )

    def run_python(
        self,
        code: str,
        tool_dispatch: Callable[[str, Dict[str, Any]], str],
        timeout: float = 60.0,
    ) -> SandboxResult:
        """PTC 代码隔离执行：独立子进程 + 工具 RPC 回传。"""
        from norpagent.builtin.sandboxes.isolated_python import (
            IsolatedPythonRunner,
        )

        return IsolatedPythonRunner().execute(code, tool_dispatch, timeout)

    def close(self) -> None:
        """P1 无驻留资源，直接返回。"""
        return None


class SubprocessSandboxProvider:
    """子进程沙箱提供者：按需新建实例。"""

    kind = "subprocess"

    def create(self) -> SubprocessSandbox:
        return SubprocessSandbox()

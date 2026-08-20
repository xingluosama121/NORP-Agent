# Copyright (c) 2026 xingluosama121, MIT Licensed
"""池化沙箱：复用 + 并发上限 + 超时强杀进程树（零第三方依赖）。

迁移自现有应用的 sandbox_pool，按本库的 Sandbox 协议重新实现：

- **池化复用**：最多 ``max_sandboxes`` 个沙箱实例，借出 / 归还；
  ``create()`` 无空闲且达上限时等待（可配超时）；
- **并发上限**：池容量即并发命令上限，防止模型失控打满机器；
- **超时强杀**：命令超时后杀掉**整个进程树**（Windows: taskkill /T，
  POSIX: 进程组 SIGKILL），杜绝孤儿进程——被强杀的实例不再复用；
- **cwd / env 记忆**：沙箱实例记住上次的工作目录，连续命令像在
  同一个终端里操作。

注册：``registry.register_sandbox("pooled", PooledSandboxProvider(...).create)``。
"""

from __future__ import annotations

import os
import platform
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Set

from norpagent.loops.cancel import cancel_requested
from norpagent.protocols.sandbox import SandboxResult

_IS_WINDOWS = platform.system() == "Windows"


def _robust_decode(data: bytes) -> str:
    if not data:
        return ""
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """杀掉子进程整棵进程树（跨平台尽力而为）。"""
    try:
        if _IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
            )
        else:
            import signal

            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _stream_reader(stream: Any, buf: List[bytes], done: threading.Event) -> None:
    """后台读取子进程输出到字节列表（防管道缓冲写满死锁）。"""
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            buf.append(chunk)
    except Exception:
        pass
    finally:
        done.set()


class PooledSandbox:
    """池中的单个沙箱实例。

    ``close()`` 语义 = 归还池（损坏实例则直接销毁）。
    """

    def __init__(self, provider: "PooledSandboxProvider", sandbox_id: str) -> None:
        self._provider = provider
        self.sandbox_id = sandbox_id
        self._lock = threading.Lock()
        self.in_use = True
        self.owner_task_id = ""
        self.cwd: str = provider.workspace_root or os.getcwd()
        self.env: Optional[Dict[str, str]] = None
        self._broken = False

    # ── Sandbox 协议 ─────────────────────────────────────

    def run_shell(
        self,
        command: str,
        timeout: float = 60.0,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> SandboxResult:
        with self._lock:
            if self._broken:
                return SandboxResult(
                    stderr="沙箱实例已损坏（上次命令超时被强杀），不可复用",
                    exit_code=-1,
                )
            if cwd:
                self.cwd = cwd
            if env:
                self.env = env

            creationflags = 0
            if _IS_WINDOWS:
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000
            try:
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self.cwd,
                    env=self.env,
                    creationflags=creationflags,
                    start_new_session=not _IS_WINDOWS,
                )
            except OSError as exc:
                return SandboxResult(stderr=f"启动命令失败: {exc}", exit_code=-1)

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
                # 分片等待：Ctrl+C / 引擎停止的取消事件置位后
                # 最多 0.5s 内响应，立即强杀进程树。
                # 注意：单个分片的 TimeoutExpired 只是「这一片到期」，
                # 只有累计等待达到 timeout 才算真正超时。
                cancelled = False
                timed_out = False
                exit_code = 0
                started = time.monotonic()
                while True:
                    if cancel_requested():
                        _kill_process_tree(proc)
                        self._broken = True
                        cancelled = True
                        exit_code = -1
                        break
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        timed_out = True
                        # ★ 超时：强杀整个进程树，实例标记损坏不再复用
                        _kill_process_tree(proc)
                        self._broken = True
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
                # 进程已死，读取线程自然退出；短促等待收尾
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
                stderr = stderr or f"命令超时({timeout}s)，进程树已强杀"
            elif exit_code != 0 and not stderr:
                stderr = f"命令退出码 {exit_code}"
            return SandboxResult(
                stdout=stdout, stderr=stderr,
                exit_code=exit_code, timed_out=timed_out,
            )

    def run_python(
        self,
        code: str,
        tool_dispatch: Any,
        timeout: float = 60.0,
    ) -> SandboxResult:
        """PTC 代码隔离执行（与 subprocess 沙箱同款隔离器）。

        说明：代码执行是「每次调用独立子进程」的语义，不占用池内
        实例，也不复用池实例的 cwd/env 状态——代码执行的隔离强度
        优先于终端会话连续性。
        """
        from norpagent.builtin.sandboxes.isolated_python import (
            IsolatedPythonRunner,
        )

        return IsolatedPythonRunner().execute(code, tool_dispatch, timeout)

    def close(self) -> None:
        """归还沙箱到池（损坏实例直接销毁）。"""
        if self._broken:
            self._provider.discard(self)
            return
        self._provider.release(self)

    def destroy(self) -> None:
        """立即销毁（池关闭时使用）。"""
        self._broken = True
        self._provider.discard(self)


class PooledSandboxProvider:
    """沙箱池提供者：管理沙箱实例的借出 / 归还 / 回收。"""

    kind = "pooled"

    def __init__(
        self,
        max_sandboxes: int = 8,
        workspace_root: Optional[str] = None,
        acquire_timeout: float = 30.0,
    ) -> None:
        self.max_sandboxes = max(1, int(max_sandboxes))
        self.workspace_root = (
            os.path.abspath(workspace_root) if workspace_root else None
        )
        self.acquire_timeout = float(acquire_timeout)
        self._cond = threading.Condition(threading.Lock())
        self._available: List[PooledSandbox] = []
        self._all: Set[PooledSandbox] = set()

    # ── SandboxProvider 协议 ─────────────────────────────

    def create(self) -> PooledSandbox:
        """借出一个沙箱；无空闲且达上限时等待 acquire_timeout 秒。"""
        with self._cond:
            deadline = time.monotonic() + self.acquire_timeout
            while not self._available and len(self._all) >= self.max_sandboxes:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        f"沙箱池已满（容量 {self.max_sandboxes}），"
                        f"等待 {self.acquire_timeout}s 未获得空闲实例"
                    )
                self._cond.wait(remaining)
            if self._available:
                sb = self._available.pop()
                sb.in_use = True
                return sb
            sb = PooledSandbox(self, f"sb_{uuid.uuid4().hex[:8]}")
            self._all.add(sb)
            sb.in_use = True
            return sb

    # ── 池管理 ──────────────────────────────────────────

    def release(self, sandbox: PooledSandbox) -> None:
        """归还沙箱（空闲实例复用）。"""
        with self._cond:
            if sandbox in self._all:
                sandbox.in_use = False
                sandbox.owner_task_id = ""
                self._available.append(sandbox)
                self._cond.notify_all()

    def discard(self, sandbox: PooledSandbox) -> None:
        """把沙箱从池中移除（损坏实例）。"""
        with self._cond:
            self._all.discard(sandbox)
            if sandbox in self._available:
                self._available.remove(sandbox)

    def kill_task(self, owner_task_id: str) -> int:
        """强制移除指定任务占用的沙箱（生命周期管理 / 取消语义）。

        与现有应用 kill_task_sandbox 一致：不归还被终止的实例，
        下次 create() 会创建全新实例，避免复用空壳。
        """
        removed = 0
        with self._cond:
            for sb in list(self._all):
                if sb.owner_task_id == owner_task_id and sb.in_use:
                    self._all.discard(sb)
                    removed += 1
        return removed

    def close_all(self) -> None:
        """销毁全部沙箱（运行时 shutdown / 进程退出时调用）。"""
        with self._cond:
            for sb in list(self._all):
                self._all.discard(sb)
            self._available.clear()

    def stats(self) -> Dict[str, Any]:
        with self._cond:
            in_use = sum(1 for s in self._all if s.in_use)
            return {
                "capacity": self.max_sandboxes,
                "created": len(self._all),
                "in_use": in_use,
                "available": len(self._available),
            }

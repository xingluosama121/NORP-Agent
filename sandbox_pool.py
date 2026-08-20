# Vibe Coding Agent - 沙箱池 (Sandbox Pool)
# 大沙池下分出8个以内的沙箱，异步获取/释放
# Copyright (c) 2026 xingluosama

import nasync_io
import os
import subprocess
import sys
import uuid
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List, Set
from contextlib import asynccontextmanager

from agent_shared import robust_decode


MAX_SANDBOXES = 8


@dataclass
class Sandbox:
    """单个沙箱实例。使用子进程隔离（带进程组管理），
    也可选 Docker 容器。"""
    sandbox_id: str
    workspace_root: str           # 宿主工作区路径
    sandbox_root: str = ""        # 沙箱内部路径（路径映射后）
    process: Optional[subprocess.Popen] = None
    is_docker: bool = False
    container_id: str = ""
    in_use: bool = False
    owner_task_id: str = ""       # 当前占用该沙箱的任务 ID
    created_at: float = 0.0

    # 路径映射表：宿主路径 -> 沙箱内路径
    path_map: Dict[str, str] = field(default_factory=dict)
    # 反向映射：沙箱内路径 -> 宿主路径
    reverse_path_map: Dict[str, str] = field(default_factory=dict)

    def map_path(self, host_path: str) -> str:
        """将宿主路径映射为沙箱内路径。"""
        host_path = os.path.abspath(host_path)
        # 精确匹配
        if host_path in self.path_map:
            return self.path_map[host_path]
        # 前缀匹配（子路径）
        for h, s in sorted(self.path_map.items(), key=lambda x: -len(x[0])):
            if host_path.startswith(h):
                rel = os.path.relpath(host_path, h)
                return os.path.normpath(os.path.join(s, rel))
        # 默认：直接返回（不映射）
        return host_path

    def unmap_path(self, sandbox_path: str) -> str:
        """将沙箱内路径还原为宿主路径。"""
        sandbox_path = os.path.normpath(sandbox_path)
        if sandbox_path in self.reverse_path_map:
            return self.reverse_path_map[sandbox_path]
        for s, h in sorted(self.reverse_path_map.items(), key=lambda x: -len(x[0])):
            if sandbox_path.startswith(s):
                rel = os.path.relpath(sandbox_path, s)
                return os.path.normpath(os.path.join(h, rel))
        return sandbox_path


class SandboxPool:
    """大沙池：管理最多 MAX_SANDBOXES 个沙箱实例。
    异步获取/释放，支持等待队列。"""

    def __init__(self):
        self._lock = nasync_io.Lock()
        self._available = nasync_io.Condition(self._lock)
        self._sandboxes: List[Sandbox] = []
        self._total_created = 0
        self._waiters: List[nasync_io.Future] = []

    @property
    def total(self) -> int:
        return len(self._sandboxes)

    @property
    def available_count(self) -> int:
        return sum(1 for s in self._sandboxes if not s.in_use)

    def get_stats(self) -> dict:
        # 沙箱池为懒加载：只有 acquire 时才真正创建子进程沙箱。
        # 因此「总沙箱数」展示为池容量（MAX_SANDBOXES），「已创建」展示
        # 当前实际实例数，避免初始状态下 total/available/in_use 全是 0 的困惑。
        in_use = sum(1 for s in self._sandboxes if s.in_use)
        return {
            "total": MAX_SANDBOXES,               # 总沙箱数（池容量）
            "available": MAX_SANDBOXES - in_use,  # 可用
            "in_use": in_use,                     # 占用中
            "max": MAX_SANDBOXES,                 # 上限
            "created": len(self._sandboxes),      # 已创建实例数（懒加载）
        }

    async def acquire(self, task_id: str, workspace_root: str = "",
                      extra_paths: Optional[Dict[str, str]] = None) -> Sandbox:
        """异步获取一个空闲沙箱。若无空闲则等待。
        
        Args:
            task_id: 任务 ID，用于生命周期关联
            workspace_root: 工作区根目录
            extra_paths: 额外路径映射 {宿主路径: 沙箱内路径}
        """
        async with self._lock:
            # 尝试找一个空闲沙箱
            for sb in self._sandboxes:
                if not sb.in_use:
                    sb.in_use = True
                    sb.owner_task_id = task_id
                    # 更新路径映射
                    self._setup_path_map(sb, workspace_root, extra_paths)
                    return sb

            # 如果没有空闲沙箱，且未达到上限，创建新的
            if self.total < MAX_SANDBOXES:
                sb = await self._create_sandbox(workspace_root, extra_paths)
                sb.in_use = True
                sb.owner_task_id = task_id
                self._sandboxes.append(sb)
                return sb

            # 等待释放
            fut = nasync_io.get_running_loop().create_future()
            self._waiters.append(fut)
            await fut
            # 重新尝试获取
            return await self.acquire(task_id, workspace_root, extra_paths)

    async def release(self, sandbox: Sandbox):
        """释放沙箱，归还到池中。"""
        async with self._lock:
            sandbox.in_use = False
            sandbox.owner_task_id = ""
            # 唤醒等待者
            if self._waiters:
                waiter = self._waiters.pop(0)
                if not waiter.done():
                    waiter.set_result(True)
            self._available.notify_all()

    async def destroy_sandbox(self, sandbox: Sandbox):
        """销毁指定沙箱。"""
        async with self._lock:
            await self._stop_sandbox(sandbox)
            if sandbox in self._sandboxes:
                self._sandboxes.remove(sandbox)

    async def destroy_all(self):
        """销毁所有沙箱。"""
        async with self._lock:
            for sb in list(self._sandboxes):
                await self._stop_sandbox(sb)
            self._sandboxes.clear()

    async def kill_task_sandbox(self, task_id: str):
        """强制终止指定任务占用的沙箱（用于生命周期管理 / 取消语义）。

        ★ P0-7 修复：杀整个沙箱进程树后，将沙箱从池中移除（避免复用
        一个进程已被杀死的空壳沙箱），下次 acquire 会重新创建。
        """
        async with self._lock:
            to_remove = []
            for sb in self._sandboxes:
                if sb.owner_task_id == task_id:
                    await self._stop_sandbox(sb, force=True)
                    sb.in_use = False
                    sb.owner_task_id = ""
                    to_remove.append(sb)
            for sb in to_remove:
                if sb in self._sandboxes:
                    self._sandboxes.remove(sb)

    def _setup_path_map(self, sb: Sandbox, workspace_root: str,
                        extra_paths: Optional[Dict[str, str]] = None):
        """配置沙箱的路径映射表。"""
        sb.path_map.clear()
        sb.reverse_path_map.clear()

        if workspace_root:
            ws = os.path.abspath(workspace_root)
            sb.workspace_root = ws
            sb.sandbox_root = f"/sandbox_ws/{sb.sandbox_id}"
            sb.path_map[ws] = sb.sandbox_root
            sb.reverse_path_map[sb.sandbox_root] = ws

        if extra_paths:
            for host, sandbox in extra_paths.items():
                h = os.path.abspath(host)
                sb.path_map[h] = sandbox
                sb.reverse_path_map[sandbox] = h

    async def _create_sandbox(self, workspace_root: str = "",
                              extra_paths: Optional[Dict[str, str]] = None) -> Sandbox:
        """创建一个新的沙箱实例。"""
        sid = f"sb_{uuid.uuid4().hex[:8]}"
        # ★ 修复：Sandbox dataclass 的 workspace_root 为必填字段，
        # 构造时即传入，避免 TypeError（setup_path_map 会再覆盖为绝对路径）
        sb = Sandbox(sandbox_id=sid, workspace_root=workspace_root or os.getcwd())

        self._setup_path_map(sb, workspace_root, extra_paths)

        # 尝试 Docker，失败则使用子进程隔离
        docker_ok = await self._try_docker_sandbox(sb)
        if not docker_ok:
            await self._start_process_sandbox(sb)

        sb.created_at = nasync_io.get_running_loop().time()
        self._total_created += 1
        return sb

    async def _try_docker_sandbox(self, sb: Sandbox) -> bool:
        """尝试创建 Docker 沙箱。"""
        try:
            import docker
            client = docker.from_env()
            volumes = {}
            for host, sandbox_path in sb.path_map.items():
                volumes[host] = {"bind": sandbox_path, "mode": "rw"}

            container = client.containers.run(
                "python:3.11-slim",
                command="tail -f /dev/null",
                volumes=volumes,
                network_mode="none",
                mem_limit="512m",
                detach=True,
                remove=True,
            )
            sb.is_docker = True
            sb.container_id = container.id
            return True
        except Exception:
            return False

    async def _start_process_sandbox(self, sb: Sandbox):
        """启动子进程隔离沙箱（Windows: Job Object; Unix: 进程组）。"""
        creationflags = 0
        if platform.system() == "Windows":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            # CREATE_NO_WINDOW 避免弹窗
            creationflags |= 0x08000000

        # 启动一个持久 shell 作为沙箱入口
        sb.process = subprocess.Popen(
            ["cmd.exe", "/k", "echo Sandbox Ready"] if platform.system() == "Windows"
            else ["/bin/bash", "-c", "echo 'Sandbox Ready'; exec bash"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=sb.workspace_root or os.getcwd(),
            creationflags=creationflags if platform.system() == "Windows" else 0,
            start_new_session=True,  # Unix: 新会话 = 进程组组长
        )

    async def _stop_sandbox(self, sb: Sandbox, force: bool = False):
        """停止沙箱。"""
        if sb.is_docker and sb.container_id:
            try:
                import docker
                client = docker.from_env()
                container = client.containers.get(sb.container_id)
                container.stop(timeout=2 if force else 10)
            except Exception:
                pass

        if sb.process:
            try:
                if force:
                    # 杀整个进程组
                    if platform.system() == "Windows":
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(sb.process.pid)],
                            capture_output=True,
                        )
                    else:
                        import signal
                        os.killpg(os.getpgid(sb.process.pid), signal.SIGKILL)
                else:
                    sb.process.terminate()
                    try:
                        sb.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        sb.process.kill()
            except Exception:
                pass
            finally:
                sb.process = None

    async def exec_in_sandbox(self, sandbox: Sandbox, cmd: str,
                              timeout: int = 30, cwd: str = "",
                              env: Optional[Dict[str, str]] = None) -> str:
        """在指定沙箱中执行命令。"""
        if sandbox.is_docker:
            return await self._exec_docker(sandbox, cmd, timeout, cwd)
        else:
            return await self._exec_process(sandbox, cmd, timeout, cwd, env)

    async def _exec_docker(self, sb: Sandbox, cmd: str, timeout: int,
                           cwd: str = "") -> str:
        import docker
        client = docker.from_env()
        container = client.containers.get(sb.container_id)
        workdir = cwd or sb.sandbox_root or "/workspace"
        exit_code, output = container.exec_run(
            cmd, workdir=workdir, stdout=True, stderr=True,
        )
        result = robust_decode(output) if output else ""
        if not result.strip():
            result = f"Exit code: {exit_code}"
        return result

    async def _exec_process(self, sb: Sandbox, cmd: str, timeout: int,
                            cwd: str = "", env: Optional[Dict[str, str]] = None) -> str:
        """在子进程沙箱中执行命令（带进程组管理）。"""
        workdir = cwd or sb.workspace_root or os.getcwd()

        creationflags = 0
        if platform.system() == "Windows":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            creationflags |= 0x08000000

        try:
            proc = await nasync_io.create_subprocess_shell(
                cmd,
                stdin=nasync_io.subprocess.DEVNULL,
                stdout=nasync_io.subprocess.PIPE,
                stderr=nasync_io.subprocess.PIPE,
                cwd=workdir,
                env=env,
                creationflags=creationflags if platform.system() == "Windows" else 0,
                start_new_session=True,
            )

            stdout, stderr = await nasync_io.wait_for(
                proc.communicate(), timeout=timeout
            )
            output = (robust_decode(stdout) if stdout else "") + \
                     (robust_decode(stderr) if stderr else "")
            if not output.strip():
                output = f"Command exited with code {proc.returncode}"
            return output.strip()

        except nasync_io.TimeoutError:
            # 超时：杀整个进程组
            try:
                if platform.system() == "Windows":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True,
                    )
                else:
                    import signal
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass
            return f"Command timed out after {timeout}s and was terminated (process group killed)"


# ── 全局单例 ──
_pool_instance: Optional[SandboxPool] = None


def get_sandbox_pool() -> SandboxPool:
    global _pool_instance
    if _pool_instance is None:
        _pool_instance = SandboxPool()
    return _pool_instance

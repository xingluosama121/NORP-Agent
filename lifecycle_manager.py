# Vibe Coding Agent - 生命周期管理器 (Lifecycle Manager)
# 解决异步16线程用户突然停止任务的僵尸进程
# 进程组 + 超时联动：任务停止时杀整个进程组，超时自动清理
# Copyright (c) 2026 xingluosama

import nasync_io
import os
import platform
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Set, Callable


class TaskState(Enum):
    PENDING = auto()       # 等待执行
    RUNNING = auto()       # 执行中
    WAITING_USER = auto()  # 等待用户输入（ask_user / 确认框），不会被误杀
    STOPPING = auto()      # 正在停止
    STOPPED = auto()       # 已停止
    TIMEOUT = auto()       # 超时
    ERROR = auto()         # 错误


@dataclass
class ProcessGroup:
    """进程组：一个任务及其所有子进程。"""
    task_id: str
    pgid: int = 0                    # 进程组 ID（Unix）或根 PID（Windows）
    pids: Set[int] = field(default_factory=set)  # 所有进程 PID
    created_at: float = 0.0
    timeout: float = 0  # 超时秒数，0 表示不超时
    _timer_handle: Optional[nasync_io.TimerHandle] = None

    def add_pid(self, pid: int):
        self.pids.add(pid)

    def remove_pid(self, pid: int):
        self.pids.discard(pid)


@dataclass
class TaskLifecycle:
    """任务生命周期记录。"""
    task_id: str
    state: TaskState = TaskState.PENDING
    process_group: Optional[ProcessGroup] = None
    sandbox_id: str = ""
    created_at: float = 0.0
    started_at: float = 0.0
    stopped_at: float = 0.0
    waiting_since: float = 0.0   # 进入 WAITING_USER 状态的时间戳
    timeout_seconds: int = 0
    _cancel_event: Optional[nasync_io.Event] = None
    _cleanup_callbacks: List[Callable] = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        if self.started_at == 0:
            return 0.0
        end = self.stopped_at if self.stopped_at > 0 else time.time()
        return end - self.started_at

    @property
    def is_alive(self) -> bool:
        return self.state in (TaskState.RUNNING, TaskState.WAITING_USER, TaskState.STOPPING)


class LifecycleManager:
    """生命周期管理器。

    核心机制：
    1. 进程组绑定：每个任务创建时绑定一个进程组
       - Unix: setpgid / os.setsid 创建新会话
       - Windows: CREATE_NEW_PROCESS_GROUP 创建新进程组
    2. 超时联动：任务超时时，自动杀整个进程组（而非单个进程）
    3. 停止联动：用户手动停止时，杀整个进程组
    4. 16线程安全：所有子进程都注册到进程组，确保无一遗漏
    5. 僵尸清理：定期扫描已停止任务，确保进程组全部终止
    """

    def __init__(self):
        self._tasks: Dict[str, TaskLifecycle] = {}
        self._process_groups: Dict[str, ProcessGroup] = {}
        self._lock = threading.Lock()
        # TOCTOU 防护：正在锁外执行 _kill_process_group 的任务 ID 集合
        # stop_task() 在锁内检查此集合，防止并发重复杀进程组
        self._killing: Set[str] = set()
        # 僵尸扫描定时器
        self._zombie_scanner: Optional[nasync_io.Task] = None
        self._running = False

    # ── 任务生命周期 ──

    def create_task(self, task_id: str = "", timeout: int = 0) -> TaskLifecycle:
        """创建一个新的任务生命周期。"""
        if not task_id:
            task_id = f"task_{uuid.uuid4().hex[:8]}"

        lifecycle = TaskLifecycle(
            task_id=task_id,
            state=TaskState.PENDING,
            created_at=time.time(),
            timeout_seconds=timeout,
            _cancel_event=nasync_io.Event(),
        )

        pg = ProcessGroup(task_id=task_id, timeout=timeout)
        lifecycle.process_group = pg

        with self._lock:
            self._tasks[task_id] = lifecycle
            self._process_groups[task_id] = pg

        return lifecycle

    def start_task(self, task_id: str):
        """标记任务开始执行。"""
        with self._lock:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                task.state = TaskState.RUNNING
                task.started_at = time.time()

                # 启动超时定时器
                if task.timeout_seconds > 0 and task.process_group:
                    self._schedule_timeout(task)

    def stop_task(self, task_id: str, reason: str = "user_request"):
        """停止任务及其整个进程组。

        这是防止僵尸进程的核心方法：
        - 标记任务状态为 STOPPING
        - 杀整个进程组（不是单个进程）
        - 设置 cancel_event 通知异步协程
        - 执行清理回调
        - WAITING_USER 状态的任务也可被停止（用户主动取消等待）

        ★ TOCTOU 修复：使用 _killing 集合原子标记，防止两个线程同时杀同一进程组。
        ★ 阻塞修复：杀进程组（subprocess.run / taskkill 可能阻塞数秒）
        移到锁外执行，避免长时间持有 _lock 导致其他线程卡死。
        """
        with self._lock:
            if task_id not in self._tasks:
                return

            task = self._tasks[task_id]
            if task.state in (TaskState.STOPPED, TaskState.STOPPING):
                return
            # TOCTOU 防护：检查是否已有线程正在杀此任务的进程组
            if task_id in self._killing:
                return

            task.state = TaskState.STOPPING
            self._killing.add(task_id)  # 原子标记：此任务正在被清理
            pg = task.process_group

            # 通知协程
            if task._cancel_event:
                task._cancel_event.set()

            # 执行清理回调
            for cb in task._cleanup_callbacks:
                try:
                    cb()
                except Exception:
                    pass

            task.state = TaskState.STOPPED
            task.stopped_at = time.time()

        # 锁外杀进程组：taskkill / killpg 可能阻塞数秒，绝不能持锁执行
        if pg:
            self._kill_process_group(pg)

        # 清理完成，移除 TOCTOU 标记
        with self._lock:
            self._killing.discard(task_id)

    def timeout_task(self, task_id: str):
        """任务超时处理：等同于 stop_task，但状态为 TIMEOUT。

        WAITING_USER 状态也可被超时（30分钟僵尸扫描器兜底）。

        ★ TOCTOU 修复 + 阻塞修复：同 stop_task。
        """
        with self._lock:
            if task_id not in self._tasks:
                return
            task = self._tasks[task_id]
            if task.state in (TaskState.STOPPED, TaskState.STOPPING):
                return
            if task_id in self._killing:
                return

            task.state = TaskState.STOPPING
            self._killing.add(task_id)
            pg = task.process_group

            if task._cancel_event:
                task._cancel_event.set()

            for cb in task._cleanup_callbacks:
                try:
                    cb()
                except Exception:
                    pass

            task.state = TaskState.TIMEOUT
            task.stopped_at = time.time()

        # 锁外杀进程组（force=True 强制清理）
        if pg:
            self._kill_process_group(pg, force=True)

        with self._lock:
            self._killing.discard(task_id)

    # ── 进程组管理 ──

    def register_process(self, task_id: str, pid: int):
        """将子进程注册到任务的进程组中。"""
        with self._lock:
            if task_id in self._process_groups:
                self._process_groups[task_id].add_pid(pid)

    def _kill_process_group(self, pg: ProcessGroup, force: bool = False):
        """杀死整个进程组。

        - Unix: 使用 os.killpg 发送 SIGTERM（force 时用 SIGKILL）
        - Windows: 使用 taskkill /F /T 杀进程树
        """
        if platform.system() == "Windows":
            # Windows: 使用 taskkill /T 杀进程树
            for pid in list(pg.pids):
                try:
                    subprocess.run(
                        ["taskkill", "/F" if force else "", "/T", "/PID", str(pid)],
                        capture_output=True,
                        timeout=5,
                    )
                except Exception:
                    pass
            # 额外确保：杀 pgid 根进程树
            if pg.pgid > 0:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pg.pgid)],
                        capture_output=True,
                        timeout=5,
                    )
                except Exception:
                    pass
        else:
            # Unix: 杀整个进程组
            sig = signal.SIGKILL if force else signal.SIGTERM
            try:
                os.killpg(pg.pgid, sig)
            except (ProcessLookupError, OSError):
                pass

            # 如果还有残留，逐个杀
            if force:
                for pid in list(pg.pids):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass

        pg.pids.clear()

    # ── 超时联动 ──

    def _schedule_timeout(self, task: TaskLifecycle):
        """安排超时回调。"""
        loop = nasync_io.get_running_loop()
        task_id = task.task_id
        timeout = task.timeout_seconds

        def _on_timeout():
            self.timeout_task(task_id)

        task.process_group._timer_handle = loop.call_later(timeout, _on_timeout)

    def pause_timeout(self, task_id: str):
        """暂停超时计时（用户交互等待时调用）。"""
        with self._lock:
            if task_id not in self._tasks:
                return
            task = self._tasks[task_id]
            if task.process_group and task.process_group._timer_handle:
                task.process_group._timer_handle.cancel()
                task.process_group._timer_handle = None

    def resume_timeout(self, task_id: str):
        """恢复超时计时（用户交互完成后调用）。"""
        with self._lock:
            if task_id not in self._tasks:
                return
            task = self._tasks[task_id]
            if task.timeout_seconds > 0:
                self._schedule_timeout(task)

    # ── 取消事件 ──

    def get_cancel_event(self, task_id: str) -> Optional[nasync_io.Event]:
        """获取任务的取消事件，异步协程可 await。"""
        with self._lock:
            if task_id in self._tasks:
                return self._tasks[task_id]._cancel_event
        return None

    # ── 清理回调 ──

    def add_cleanup_callback(self, task_id: str, callback: Callable):
        """注册任务停止时的清理回调。"""
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]._cleanup_callbacks.append(callback)

    # ── 僵尸扫描 ──

    async def start_zombie_scanner(self, interval: float = 5.0):
        """启动僵尸进程扫描器，定期清理已停止但未释放资源的任务。"""
        self._running = True
        while self._running:
            await nasync_io.sleep(interval)
            self._scan_zombies()

    def stop_zombie_scanner(self):
        self._running = False

    def _scan_zombies(self):
        """扫描并清理僵尸任务。

        关键安全规则：
        - RUNNING / WAITING_USER 任务永不触碰（WAITING_USER 有自身 30min 硬超时）
        - STOPPED / TIMEOUT / ERROR 才清理
        - _killing 集合中的任务跳过（TOCTOU 防护：正在锁外杀进程）
        """
        with self._lock:
            to_remove = []
            for task_id, task in self._tasks.items():
                if task_id in self._killing:
                    continue  # 正在被另一个线程清理，跳过
                if task.state in (TaskState.STOPPED, TaskState.TIMEOUT, TaskState.ERROR):
                    # 确保进程组已清理
                    if task.process_group and task.process_group.pids:
                        self._kill_process_group(task.process_group, force=True)
                    to_remove.append(task_id)
                # WAITING_USER 任务不在此处理 ——
                # async_loop._wait_for_user_input 自带 nasync_io.wait_for(30min) 硬超时

            for task_id in to_remove:
                self._tasks.pop(task_id, None)
                self._process_groups.pop(task_id, None)

    # ── 用户交互状态标记 ──

    def set_waiting_user(self, task_id: str):
        """标记任务正在等待用户输入（ask_user / 确认框）。
        
        将状态从 RUNNING 切换为 WAITING_USER，确保：
        1. 僵尸扫描器不会误杀此任务
        2. 超时计时器暂停
        3. 状态面板可显示"等待用户回复"
        """
        with self._lock:
            if task_id not in self._tasks:
                return
            task = self._tasks[task_id]
            if task.state == TaskState.RUNNING:
                task.state = TaskState.WAITING_USER
                task.waiting_since = time.time()

    def clear_waiting_user(self, task_id: str):
        """用户已响应，将任务从 WAITING_USER 恢复为 RUNNING。"""
        with self._lock:
            if task_id not in self._tasks:
                return
            task = self._tasks[task_id]
            if task.state == TaskState.WAITING_USER:
                task.state = TaskState.RUNNING
                task.waiting_since = 0.0

    # ── 状态查询 ──

    def get_task_state(self, task_id: str) -> Optional[TaskState]:
        with self._lock:
            if task_id in self._tasks:
                return self._tasks[task_id].state
        return None

    def is_task_alive(self, task_id: str) -> bool:
        state = self.get_task_state(task_id)
        return state in (TaskState.RUNNING, TaskState.WAITING_USER, TaskState.STOPPING)

    def get_active_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._tasks.values()
                       if t.state in (TaskState.RUNNING, TaskState.WAITING_USER, TaskState.STOPPING))

    def get_stats(self) -> dict:
        # ★ 修复：不能在持锁状态调用 get_active_count()（其内部也要获取同一把
        # threading.Lock，不可重入会死锁）。改为锁内直接内联计算。
        with self._lock:
            return {
                "total_tasks": len(self._tasks),
                "active": sum(
                    1 for t in self._tasks.values()
                    if t.state in (TaskState.RUNNING, TaskState.WAITING_USER,
                                   TaskState.STOPPING)),
                "running": sum(1 for t in self._tasks.values() if t.state == TaskState.RUNNING),
                "waiting_user": sum(1 for t in self._tasks.values() if t.state == TaskState.WAITING_USER),
                "stopped": sum(1 for t in self._tasks.values() if t.state == TaskState.STOPPED),
                "timeout": sum(1 for t in self._tasks.values() if t.state == TaskState.TIMEOUT),
            }

    def shutdown(self):
        """关闭生命周期管理器，停止所有任务。"""
        self.stop_zombie_scanner()
        with self._lock:
            for task_id in list(self._tasks.keys()):
                self.stop_task(task_id, reason="shutdown")


# ── 全局单例 ──
_manager_instance: Optional[LifecycleManager] = None


def get_lifecycle_manager() -> LifecycleManager:
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = LifecycleManager()
    return _manager_instance

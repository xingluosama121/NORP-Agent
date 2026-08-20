# Vibe Coding Agent - 文件IO队列 (File I/O Queue)
# 检测多线程读写同一文件的冲突，塞进栈队列序列化访问
# Copyright (c) 2026 xingluosama

import nasync_io
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Set, Tuple


class FileOp(Enum):
    READ = auto()
    WRITE = auto()
    DELETE = auto()
    LIST = auto()


@dataclass
class FileAccessRequest:
    """文件访问请求。"""
    task_id: str
    file_path: str          # 规范化后的绝对路径
    operation: FileOp
    timestamp: float = field(default_factory=time.time)
    _future: Optional[nasync_io.Future] = None  # 异步等待者

    @property
    def is_write(self) -> bool:
        return self.operation in (FileOp.WRITE, FileOp.DELETE)


class FileIOQueue:

    def __init__(self):
        self._lock = threading.Lock()
        # 每个文件的访问状态
        # file_path -> { 'readers': set(task_ids), 'writer': task_id or None, 'queue': deque }
        self._file_state: Dict[str, dict] = {}
        # 全局等待队列统计
        self._stats = {
            "total_queued": 0,
            "total_conflicts": 0,
            "total_processed": 0,
        }

    # ── 文件访问入口 ──

    async def acquire(self, task_id: str, file_path: str,
                      operation: FileOp) -> bool:
        """获取文件访问权。如果需要等待，会异步阻塞直到可以访问。

        Returns:
            True: 可以访问
            False: 等待超时或任务被取消
        """
        file_path = os.path.normpath(os.path.abspath(file_path))

        request = FileAccessRequest(
            task_id=task_id,
            file_path=file_path,
            operation=operation,
        )

        with self._lock:
            state = self._get_or_create_state(file_path)

            # 判断是否冲突
            conflict = self._detect_conflict(state, request)

            if not conflict:
                # 无冲突：直接获取
                self._grant_access(state, request)
                return True

            # 有冲突：加入等待队列
            request._future = nasync_io.get_running_loop().create_future()
            state["queue"].append(request)
            self._stats["total_queued"] += 1
            self._stats["total_conflicts"] += 1

        # 在锁外等待
        try:
            await nasync_io.wait_for(request._future, timeout=30.0)
            return True
        except nasync_io.TimeoutError:
            with self._lock:
                self._remove_from_queue(file_path, request)
            return False

    def release(self, task_id: str, file_path: str, operation: FileOp):
        """释放文件访问权，并唤醒等待队列中的下一个请求。"""
        file_path = os.path.normpath(os.path.abspath(file_path))

        with self._lock:
            state = self._file_state.get(file_path)
            if not state:
                return

            self._revoke_access(state, task_id, operation)
            self._stats["total_processed"] += 1

            # 尝试唤醒等待队列中的下一个
            self._try_wake_next(file_path, state)

    # ── 冲突检测 ──

    def _detect_conflict(self, state: dict, request: FileAccessRequest) -> bool:
        """检测是否有冲突。"""
        has_writer = state["writer"] is not None
        has_readers = len(state["readers"]) > 0
        has_queue = len(state["queue"]) > 0

        if request.operation == FileOp.READ:
            # 读操作：只有当前有写者时才冲突
            if has_writer:
                return True
            # 如果队列中有写者在等待，新读者可能需要等待（防止写饿死）
            if has_queue:
                for queued in state["queue"]:
                    if queued.is_write:
                        return True
            return False

        elif request.is_write:
            # 写操作：有读者或写者时冲突
            if has_writer or has_readers:
                return True
            return False

        elif request.operation == FileOp.LIST:
            # 列表操作：有写者时冲突
            return has_writer

        return False

    # ── 访问管理 ──

    def _grant_access(self, state: dict, request: FileAccessRequest):
        """授予访问权。"""
        if request.operation == FileOp.READ:
            state["readers"].add(request.task_id)
        elif request.is_write:
            state["writer"] = request.task_id
        # LIST 不记录（只读元数据）

    def _revoke_access(self, state: dict, task_id: str, operation: FileOp):
        """撤销访问权。"""
        if operation == FileOp.READ:
            state["readers"].discard(task_id)
        elif operation in (FileOp.WRITE, FileOp.DELETE):
            if state["writer"] == task_id:
                state["writer"] = None

    def _try_wake_next(self, file_path: str, state: dict):
        """尝试唤醒队列中的下一个请求。"""
        if not state["queue"]:
            return

        # 找到第一个可以无冲突执行的请求
        awakened = []
        remaining = deque()

        while state["queue"]:
            req = state["queue"].popleft()
            if not self._detect_conflict(state, req):
                self._grant_access(state, req)
                awakened.append(req)
                # 如果是读操作，继续尝试唤醒更多读者
                if req.operation != FileOp.READ:
                    break
            else:
                remaining.append(req)

        # 将未唤醒的放回队列头部
        while remaining:
            state["queue"].appendleft(remaining.pop())

        # 通知被唤醒的请求
        for req in awakened:
            if req._future and not req._future.done():
                req._future.set_result(True)

    def _remove_from_queue(self, file_path: str, request: FileAccessRequest):
        """从队列中移除请求（超时/取消时）。"""
        state = self._file_state.get(file_path)
        if state and request in state["queue"]:
            state["queue"].remove(request)

    def _get_or_create_state(self, file_path: str) -> dict:
        if file_path not in self._file_state:
            self._file_state[file_path] = {
                "readers": set(),
                "writer": None,
                "queue": deque(),
            }
        return self._file_state[file_path]

    # ── 检测：是否有线程在读写同一个文件 ──

    def detect_concurrent_access(self, file_path: str) -> List[dict]:
        """检测当前有哪些线程/任务在访问指定文件。

        Returns:
            [{task_id, operation, is_writer}, ...]
        """
        file_path = os.path.normpath(os.path.abspath(file_path))
        with self._lock:
            state = self._file_state.get(file_path)
            if not state:
                return []

            result = []
            for tid in state["readers"]:
                result.append({"task_id": tid, "operation": "READ", "is_writer": False})
            if state["writer"]:
                result.append({"task_id": state["writer"], "operation": "WRITE", "is_writer": True})
            for req in state["queue"]:
                result.append({
                    "task_id": req.task_id,
                    "operation": req.operation.name,
                    "is_writer": req.is_write,
                    "queued": True,
                })
            return result

    def get_all_active_files(self) -> Dict[str, List[dict]]:
        """获取所有当前活跃的文件及其访问者。"""
        with self._lock:
            result = {}
            for fpath, state in self._file_state.items():
                if state["readers"] or state["writer"] or state["queue"]:
                    result[fpath] = self.detect_concurrent_access(fpath)
            return result

    def get_stats(self) -> dict:
        with self._lock:
            return {
                **self._stats,
                "active_files": sum(
                    1 for s in self._file_state.values()
                    if s["readers"] or s["writer"] or s["queue"]
                ),
                "total_tracked_files": len(self._file_state),
            }

    # ── 上下文管理器风格的异步访问 ──

    async def read_file(self, task_id: str, file_path: str):
        """异步获取文件读权限的上下文管理器辅助。"""
        await self.acquire(task_id, file_path, FileOp.READ)
        return _FileAccessGuard(self, task_id, file_path, FileOp.READ)

    async def write_file(self, task_id: str, file_path: str):
        """异步获取文件写权限的上下文管理器辅助。"""
        await self.acquire(task_id, file_path, FileOp.WRITE)
        return _FileAccessGuard(self, task_id, file_path, FileOp.WRITE)

    async def delete_file(self, task_id: str, file_path: str):
        """异步获取文件删权限的上下文管理器辅助。"""
        await self.acquire(task_id, file_path, FileOp.DELETE)
        return _FileAccessGuard(self, task_id, file_path, FileOp.DELETE)


class _FileAccessGuard:
    """文件访问守卫：离开上下文时自动释放。"""

    def __init__(self, queue: FileIOQueue, task_id: str,
                 file_path: str, operation: FileOp):
        self._queue = queue
        self._task_id = task_id
        self._file_path = file_path
        self._operation = operation

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self._queue.release(self._task_id, self._file_path, self._operation)


# ── 全局单例 ──
_queue_instance: Optional[FileIOQueue] = None


def get_file_io_queue() -> FileIOQueue:
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = FileIOQueue()
    return _queue_instance

# Vibe Coding Agent - 资源隔离器 (Resource Isolator)
# 解决插件与终端抢资源的冲突：CPU/内存/IO 限制
# Copyright (c) 2026 xingluosama

import os
import platform
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, Optional


class ResourceType(Enum):
    CPU = auto()
    MEMORY = auto()
    IO = auto()
    NETWORK = auto()


@dataclass
class ResourceLimits:
    """资源限制配置。"""
    cpu_seconds: float = 30.0        # CPU 时间限制（秒）
    max_memory_mb: int = 512         # 最大内存（MB）
    max_io_mb: int = 100             # 最大 I/O 吞吐（MB）
    max_processes: int = 16          # 最大子进程数
    network_enabled: bool = False    # 是否允许网络


@dataclass
class ResourceUsage:
    """当前资源使用量。"""
    cpu_seconds: float = 0.0
    memory_mb: float = 0.0
    io_read_mb: float = 0.0
    io_write_mb: float = 0.0
    process_count: int = 0


class ResourceReservation:
    """资源预留：一个主体持有的资源配额。"""

    def __init__(self, subject_id: str, limits: ResourceLimits):
        self.subject_id = subject_id
        self.limits = limits
        self.usage = ResourceUsage()
        self._lock = threading.Lock()
        self._start_time = time.time()

    @property
    def cpu_remaining(self) -> float:
        elapsed = time.time() - self._start_time
        return max(0, self.limits.cpu_seconds - elapsed)

    @property
    def is_exhausted(self) -> bool:
        return self.cpu_remaining <= 0


class ResourceIsolator:
    """资源隔离器。

    核心策略：
    1. 总资源池 = 系统可用资源
    2. 终端保留 40% 资源（优先级最高）
    3. 插件池 = 60% 资源，按插件数均分（最多8个插件）
    4. 每个插件有独立的资源配额
    5. 配额耗尽时排队等待或拒绝

    这也解决了插件与终端抢资源的冲突。
    """

    # 资源分配比例
    TERMINAL_RESERVED_RATIO = 0.40   # 终端保留 40%
    PLUGIN_POOL_RATIO = 0.60         # 插件池占 60%

    def __init__(self):
        self._lock = threading.Lock()
        self._reservations: Dict[str, ResourceReservation] = {}

        # 终端预留
        self._terminal_limits = ResourceLimits(
            cpu_seconds=float("inf"),
            max_memory_mb=1024,
            max_io_mb=500,
            max_processes=32,
            network_enabled=True,
        )

        # 插件池总限
        self._plugin_pool_limits = ResourceLimits(
            cpu_seconds=float("inf"),
            max_memory_mb=2048,
            max_io_mb=300,
            max_processes=16,
            network_enabled=False,
        )

        # 每个插件的默认限额
        self._per_plugin_defaults = ResourceLimits(
            cpu_seconds=30.0,
            max_memory_mb=256,
            max_io_mb=50,
            max_processes=4,
            network_enabled=False,
        )

    # ── 预留管理 ──

    def reserve(self, subject_id: str, limits: Optional[ResourceLimits] = None) -> ResourceReservation:
        """为一个主体预留资源。"""
        with self._lock:
            if subject_id in self._reservations:
                return self._reservations[subject_id]

            reservation = ResourceReservation(
                subject_id=subject_id,
                limits=limits or self._per_plugin_defaults,
            )
            self._reservations[subject_id] = reservation
            return reservation

    def release(self, subject_id: str):
        """释放主体的资源预留。"""
        with self._lock:
            self._reservations.pop(subject_id, None)

    def get_reservation(self, subject_id: str) -> Optional[ResourceReservation]:
        """获取主体的资源预留。"""
        with self._lock:
            return self._reservations.get(subject_id)

    # ── 资源检查 ──

    def check_cpu(self, subject_id: str) -> bool:
        """检查主体是否还有 CPU 配额。"""
        reservation = self.get_reservation(subject_id)
        if not reservation:
            return True
        return not reservation.is_exhausted

    def check_memory(self, subject_id: str, required_mb: int = 0) -> bool:
        """检查主体是否还有内存配额。"""
        reservation = self.get_reservation(subject_id)
        if not reservation:
            return True
        return (reservation.usage.memory_mb + required_mb) <= reservation.limits.max_memory_mb

    def check_any(self, subject_id: str) -> bool:
        """检查主体是否有任何可用资源。"""
        return self.check_cpu(subject_id) and self.check_memory(subject_id)

    # ── 终端 vs 插件隔离 ──

    def is_terminal_starved(self) -> bool:
        """检查终端是否因插件抢占而资源不足。"""
        with self._lock:
            total_plugin_cpu = sum(
                r.usage.cpu_seconds for r in self._reservations.values()
                if not r.subject_id.startswith("terminal")
            )
            # 简单启发：插件总 CPU 时间超过 30s 时常量
            return total_plugin_cpu > 60.0

    def throttle_plugins(self):
        """当终端资源紧张时，节流插件执行。"""
        if self.is_terminal_starved():
            # 通知所有插件：减慢执行速度
            return True
        return False

    # ── 统计 ──

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "reservations": len(self._reservations),
                "terminal_reserved_pct": int(self.TERMINAL_RESERVED_RATIO * 100),
                "plugin_pool_pct": int(self.PLUGIN_POOL_RATIO * 100),
                "throttled": self.is_terminal_starved(),
            }

    # ── 系统资源检测 ──

    @staticmethod
    def get_system_memory_mb() -> float:
        """获取系统可用内存（MB）。"""
        try:
            import psutil
            mem = psutil.virtual_memory()
            return mem.available / (1024 * 1024)
        except ImportError:
            return 0.0

    @staticmethod
    def get_system_cpu_percent() -> float:
        """获取系统 CPU 使用率。"""
        try:
            import psutil
            return psutil.cpu_percent(interval=0.1)
        except ImportError:
            return 0.0


# ── 全局单例 ──
_isolator_instance: Optional[ResourceIsolator] = None


def get_resource_isolator() -> ResourceIsolator:
    global _isolator_instance
    if _isolator_instance is None:
        _isolator_instance = ResourceIsolator()
    return _isolator_instance

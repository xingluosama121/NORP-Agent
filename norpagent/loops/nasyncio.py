# Copyright (c) 2026 xingluosama121, MIT Licensed
"""自研 nasyncio 循环适配器：async_loop 槽位的默认实现。

独立线程跑一个自研 nasyncio 事件循环（norpagent.nasyncio.EventLoop，
完全自研、零 asyncio 依赖）；submit() 把同步函数交给
**自有守护工作池**执行并阻塞等待结果。对上层完全透明——
想替换成任意事件循环系统时，只需实现 LoopRuntime 协议并填入
async_loop 槽位地址。

本模块不 import 标准 asyncio。调度核心（事件循环 / Future / Task /
协程 trampoline / 自管道唤醒）全部来自库内置的自研
``norpagent.nasyncio`` 核心（原 nasync_io，已打包进库）。

为什么默认核心换掉标准 asyncio（拥抱自研 nasyncio）：

- **掌控力**：取消 / 停止 / 跨线程唤醒的语义全部由库内代码定义。
  标准 asyncio 的 Task.cancel() 非线程安全、cross-thread
  add_done_callback 走 call_soon 不写自管道（循环阻塞在 selector
  上收不到唤醒）；自研核心把这些坑全部修掉（Task.cancel 线程
  安全、Future 跨线程完成通知自动走自管道、abort_main() 即时停止）；
- **依赖面**：自研核心只依赖 threading / selectors / socket / heapq
  等非 asyncio 标准模块，全库审计面 = 库内自己写的代码；
- **退出语义**：不用 asyncio 默认 ThreadPoolExecutor（其工作线程
  被 CPython 登记在 threading._threads_queues，解释器退出时会被
  强制 join——任务卡住进程就僵住）。

取消语义（Ctrl+C 可用性的关键设计）：

- submit() 以轮询方式等待 threading.Event（每 ≤poll_interval 秒，
  默认 0.05s，config / NORPAGENT_SUBMIT_POLL 可调）回到一次
  字节码边界）：Windows 上主线程被 WaitForSingleObject 吞掉后
  pending interrupt 只在字节码边界被检查，一次性阻塞的
  Event.wait() 会让 Ctrl+C 失灵——轮询等待彻底解决；主线程
  Ctrl+C 会**立即**以 KeyboardInterrupt 冒出；
- 每个任务包进一个带取消事件的 contextvars 上下文
  （norpagent.loops.cancel）。KeyboardInterrupt 发生时置位该
  取消事件：沙箱立即强杀子进程、模型流式循环中断、工具尽早
  退出——工作线程不再傻等到超时；
- interrupt() 取消全部在途任务（引擎 request_stop 调用）；
- 工作线程是裸守护线程 + 队列（原因见上「退出语义」）。
"""
from __future__ import annotations

import contextvars
import os
import queue
import threading
from typing import Any, Optional

from norpagent.loops.cancel import _current_cancel
from norpagent.nasyncio import EventLoop, run_coroutine_threadsafe


def _default_workers() -> int:
    try:
        return max(4, os.cpu_count() or 4)
    except Exception:  # noqa: BLE001 — os.cpu_count 不可用时的兜底
        return 4


def _env_int(name: str, floor: int = 1, ceiling: int = 4096) -> Optional[int]:
    """读取整数环境变量（非法值返回 None）。用于资源调优开关。"""
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        return max(floor, min(int(raw), ceiling))
    except (TypeError, ValueError):
        return None


def _resolve_workers(cfg: dict) -> int:
    """工作池线程数解析优先级：config.max_workers > 环境变量
    NORPAGENT_MAX_WORKERS > 默认 max(4, cpu_count)。

    嵌入式场景：NORPAGENT_MAX_WORKERS=1（或 config 显式 1）即可把
    工作线程压到最少；超高并发服务器可显式调大（默认上限 4096）。
    """
    return int(
        cfg.get("max_workers")
        or _env_int("NORPAGENT_MAX_WORKERS")
        or _default_workers()
    )


def _resolve_poll(cfg: dict) -> float:
    """submit/run_async 完成轮询间隔解析：config.poll_interval >
    环境变量 NORPAGENT_SUBMIT_POLL > 默认 0.05s。

    该间隔只影响「阻塞等待任务的调用线程」感知完成的延迟，不增加
    事件循环开销。默认 0.05s：高并发服务器下任务完成感知延迟上限
    50ms；嵌入式 CPU 敏感场景可调大到 0.5s 省电。
    """
    raw = cfg.get("poll_interval")
    if raw is None:
        raw = os.environ.get("NORPAGENT_SUBMIT_POLL")
    if raw is None:
        return 0.05
    try:
        return max(0.001, min(float(raw), 5.0))
    except (TypeError, ValueError):
        return 0.05


class _DaemonPool:
    """守护工作池：submit 任务的执行者（裸线程 + 队列，零退出负担）。

    - 工作线程是裸 threading.Thread(daemon=True)，不注册进任何
      「解释器退出必须 join」的登记表——任务卡在阻塞 I/O 时进程
      也能立即退出（ThreadPoolExecutor 做不到，见模块文档）；
    - 懒启动：按需创建 worker，stop 后再次 start 可复用；
    - shutdown()：投递哨兵让 worker 自然退出，不等待卡住的任务
      （守护线程随进程消亡，无需回收）。
    """

    def __init__(self, max_workers: int) -> None:
        self._max_workers = max(1, int(max_workers))
        # 队列全程复用（不替换）：替换会让旧 worker 卡在旧队列上
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._threads: list = []
        self._spawn_lock = threading.Lock()
        self._stopped = False

    def submit_nowait(self, fn: Any) -> None:
        """投递任务（不阻塞）。"""
        self._ensure_workers()
        self._queue.put_nowait(fn)

    def _ensure_workers(self) -> None:
        with self._spawn_lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._stopped = False
            missing = self._max_workers - len(self._threads)
            for i in range(missing):
                t = threading.Thread(
                    target=self._worker,
                    name=f"norpagent-loop-pool-{len(self._threads) + i}",
                    daemon=True,
                )
                self._threads.append(t)
                t.start()

    def _worker(self) -> None:
        while True:
            fn = self._queue.get()
            if fn is _SENTINEL:
                return
            try:
                fn()
            except BaseException:  # noqa: BLE001 — 兜底：不应发生（submit 包装层已捕获）
                pass

    def shutdown(self) -> None:
        """通知 worker 退出（不等待卡住的任务）。"""
        with self._spawn_lock:
            if self._stopped:
                return
            self._stopped = True
            # 每个存活 worker 一个哨兵（多余的哨兵会被下一次复用消费，
            # 无副作用：worker 收到即退出，下轮 start 会补齐线程数）
            for _ in self._threads:
                try:
                    self._queue.put_nowait(_SENTINEL)
                except Exception:  # noqa: BLE001
                    pass
            for t in self._threads:
                if t.is_alive():
                    t.join(0.2)
            # 死线程引用留给 _ensure_workers 过滤；此处清掉可回收引用
            self._threads = [t for t in self._threads if t.is_alive()]


class _Sentinel:
    pass


_SENTINEL = _Sentinel()


class NasyncioLoopRuntime:
    """基于库内置自研 nasyncio 核心的 LoopRuntime 实现（默认循环）。

    线程模型：
      - ``norpagent-nasync-loop``：跑自研 EventLoop（run_forever），
        承接 run_async 提交的协程；
      - ``norpagent-loop-pool-*``：守护工作池，执行 submit 的同步任务
        （任务本体可能阻塞在沙箱/HTTP I/O，不进循环线程）。
    """

    name = "nasyncio"

    def __init__(self, **kwargs: Any) -> None:
        # config 里可带循环专属配置（架构层工厂注入）；支持：
        #   max_workers  工作池线程数（默认 max(4, cpu_count)；
        #                环境变量 NORPAGENT_MAX_WORKERS 可覆盖）
        #   poll_interval submit 完成轮询间隔秒（默认 0.05；
        #                环境变量 NORPAGENT_SUBMIT_POLL 可覆盖）
        cfg = kwargs.get("config") or {}
        self.max_workers = _resolve_workers(cfg)
        self._poll = _resolve_poll(cfg)
        self._loop: Optional[EventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._pool = _DaemonPool(self.max_workers)
        self._active_cancels: set = set()  # 在途任务的取消事件集合

    # ── LoopRuntime 协议 ──────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return  # 已启动
            self._loop = EventLoop()
            self._closed.clear()
            self._thread = threading.Thread(
                target=self._loop.run_forever,
                name="norpagent-nasync-loop",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        self.interrupt()
        self._closed.set()

    def interrupt(self) -> None:
        """请求取消全部在途 submit 任务（Ctrl+C / 引擎停止路径）。

        置位每个在途任务的取消事件：沙箱强杀子进程、模型流式循环
        中断，任务执行体尽快退出；本方法不等待任务真正结束。
        """
        with self._lock:
            cancels = list(self._active_cancels)
        for event in cancels:
            try:
                event.set()
            except Exception:  # noqa: BLE001
                pass

    def is_running(self) -> bool:
        loop = self._loop
        return bool(
            loop is not None
            and loop.is_running()
            and self._thread is not None
            and self._thread.is_alive()
            and not self._closed.is_set()
        )

    def join(self, timeout: Optional[float] = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if not thread.is_alive():
                # 循环线程退出后关闭循环资源
                loop = self._loop
                if loop is not None and not loop.is_closed():
                    loop.close()
                self._pool.shutdown()
                with self._lock:
                    self._thread = None
                    self._loop = None

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        loop = self._loop
        if loop is None:
            raise RuntimeError("循环尚未 start()")
        # 已在循环线程内：直接同步执行（避免死等）
        if threading.current_thread() is self._thread:
            return fn(*args, **kwargs)
        if not self.is_running():
            raise RuntimeError("循环已停止")
        # 任务执行体经 contextvars 拿到自己的取消事件（见模块文档）；
        # 结果由工作线程直接写入本地盒子并置位事件，不依赖
        # 事件循环 Future 的 done-callback 调度时序（虽然自研核心
        # 已修复跨线程完成通知的自管道唤醒，但 submit 的任务可能
        # 长时间阻塞，放在守护工作池里执行与循环彻底解耦更稳）。
        box: dict = {}
        done = threading.Event()
        job_cancel = threading.Event()
        ctx = contextvars.copy_context()

        def _bind_cancel() -> None:
            _current_cancel.set(job_cancel)

        ctx.run(_bind_cancel)
        with self._lock:
            self._active_cancels.add(job_cancel)

        def _runner() -> None:
            try:
                box["ok"] = ctx.run(fn, *args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 — 异常传回调用方
                box["exc"] = exc
            finally:
                done.set()
                with self._lock:
                    self._active_cancels.discard(job_cancel)

        self._pool.submit_nowait(_runner)
        try:
            # 轮询等待而非一次性阻塞：Windows 上主线程被
            # WaitForSingleObject 吞掉后收不到 SIGINT——pending
            # interrupt 只在字节码边界被检查，无限等待的
            # Event.wait() 永远不会回到字节码边界（Ctrl+C 失灵）。
            # 轮询让主线程每 ≤poll_interval 秒经过一次字节码边界，
            # 控制台 Ctrl+C 即刻以 KeyboardInterrupt 形式冒出。
            # 间隔默认 0.05s（config.poll_interval /
            # NORPAGENT_SUBMIT_POLL 可调）：高并发下任务完成感知
            # 延迟小；嵌入式 CPU 敏感可调大。
            while not done.is_set():
                done.wait(self._poll)
        except KeyboardInterrupt:
            # Ctrl+C：调用方即刻中断；置位取消事件让执行体尽快退出
            # （沙箱强杀子进程 / 模型流中断），不等超时兜底。
            job_cancel.set()
            raise
        if "exc" in box:
            raise box["exc"]
        return box["ok"]

    # ── 附加能力 ─────────────────────────────────────────

    def run_async(self, coro: Any) -> Any:
        """在循环内执行协程并阻塞返回其结果（可选能力）。

        供「以协程形态编写的自定义入口」使用；引擎默认走 submit()。
        跨线程提交走自研核心的 run_coroutine_threadsafe（内部用
        call_soon_threadsafe + 自管道唤醒，循环阻塞在 select 上也
        能立即开始推进协程）；完成通知由 concurrent.futures.Future
        承载，Event.wait() 可被 Ctrl+C 打断（轮询等待）。
        """
        loop = self._loop
        if loop is None:
            raise RuntimeError("循环尚未 start()")
        if threading.current_thread() is self._thread:
            # 已在循环线程内：阻塞等待会让循环无法推进任务，
            # 必然死锁——明确拒绝而不是悬挂。
            raise RuntimeError(
                "run_async 不能在循环线程内调用（阻塞等待会卡死循环）。"
                "请在协程内直接 await，或改用 submit()。"
            )
        fut = run_coroutine_threadsafe(coro, loop)
        done = threading.Event()

        def _on_fut_done(_: Any) -> None:
            done.set()

        # concurrent.futures.Future 已完成时 add_done_callback 会在
        # 调用线程内同步触发回调，因此无竞态；未完成时回调在
        # 写结果的线程（循环线程）内触发，Event 即刻被置位。
        # 轮询等待保证 Windows 上 Ctrl+C 可用（同 submit 的说明）。
        fut.add_done_callback(_on_fut_done)
        while not done.is_set():
            done.wait(self._poll)
        exc = fut.exception()
        if exc is not None:
            raise exc
        return fut.result()


# 兼容旧名：0.7 及以前默认类名 StdLoopRuntime（loops/std_asyncio.py
# 时代）。实现已整体迁移到自研 nasyncio 核心，此别名保留使
# 旧地址字符串 / 旧 import 不失效。
StdLoopRuntime = NasyncioLoopRuntime


__all__ = ["NasyncioLoopRuntime", "StdLoopRuntime"]

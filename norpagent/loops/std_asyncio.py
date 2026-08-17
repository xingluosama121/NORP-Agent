# Copyright (c) 2026 xingluosama121, MIT Licensed
"""标准 asyncio 循环适配器：async_loop 槽位的默认实现。

独立线程跑一个 asyncio 事件循环；submit() 把同步函数交给
**自有守护工作池**执行并阻塞等待结果。对上层完全透明——
想替换成自研事件循环系统（例如 nasync_io 移植版）时，
只需实现 LoopRuntime 协议并填入 async_loop 槽位地址。

取消语义（Ctrl+C 可用性的关键设计）：

- submit() 以轮询方式等待 threading.Event（每 ≤0.2s 回到一次
  字节码边界）：Windows 上主线程被 WaitForSingleObject 吞掉后
  pending interrupt 只在字节码边界被检查，一次性阻塞的
  Event.wait() 会让 Ctrl+C 失灵——轮询等待彻底解决；主线程
  Ctrl+C 会**立即**以 KeyboardInterrupt 冒出；
- 每个任务包进一个带取消事件的 contextvars 上下文
  （norpagent.loops.cancel）。KeyboardInterrupt 发生时置位该
  取消事件：沙箱立即强杀子进程、模型流式循环中断、工具尽早
  退出——工作线程不再傻等到超时；
- interrupt() 取消全部在途任务（引擎 request_stop 调用）；
- 工作线程是裸守护线程 + 队列，**不用** asyncio 默认
  ThreadPoolExecutor——后者的工作线程被 CPython 登记在
  threading._threads_queues，解释器退出时会被 join：一旦任务卡在
  阻塞 I/O（沙箱 subprocess / HTTP 请求），进程将僵住直到超时。
  守护工作池不参与退出 join，Ctrl+C 后进程即刻收尾。
"""
from __future__ import annotations

import asyncio
import contextvars
import os
import queue
import threading
from typing import Any, Optional

from norpagent.loops.cancel import _current_cancel


def _default_workers() -> int:
    try:
        return max(4, os.cpu_count() or 4)
    except Exception:  # noqa: BLE001 — os.cpu_count 不可用时的兜底
        return 4


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


class StdLoopRuntime:
    """基于标准库 asyncio 的 LoopRuntime 实现。"""

    name = "std_asyncio"

    def __init__(self, **kwargs: Any) -> None:
        # config 里可带循环专属配置（架构层工厂注入）；支持：
        #   max_workers 工作池线程数（默认 max(4, cpu_count)）
        cfg = kwargs.get("config") or {}
        self.max_workers = int(cfg.get("max_workers") or _default_workers())
        self._loop: Optional[asyncio.AbstractEventLoop] = None
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
            self._loop = asyncio.new_event_loop()
            self._closed.clear()
            self._thread = threading.Thread(
                target=self._loop.run_forever,
                name="norpagent-std-loop",
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
        # asyncio Future 的 done-callback 调度时序
        # （跨线程 add_done_callback 在 future 已完成时存在竞态：
        #  call_soon 不写自管道，loop 阻塞在 selector 上会收不到唤醒）。
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
            # 轮询让主线程每 ≤0.2s 经过一次字节码边界，
            # 控制台 Ctrl+C 即刻以 KeyboardInterrupt 形式冒出。
            while not done.is_set():
                done.wait(0.2)
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
        跨线程等待用「done 回调置位 threading.Event」：回调由
        concurrent.futures.Future 保证在结果写入线程同步触发，
        无唤醒竞态；Event.wait() 可被 Ctrl+C 打断。
        """
        loop = self._loop
        if loop is None:
            raise RuntimeError("循环尚未 start()")
        if threading.current_thread() is self._thread:
            # 已在循环线程内：注册 task 后原地等待其完成
            task = loop.create_task(coro)
            done = threading.Event()

            def _on_done(_: Any) -> None:
                done.set()

            task.add_done_callback(_on_done)
            while not done.is_set() and loop.is_running():
                done.wait(0.05)
            if task.cancelled():
                raise RuntimeError("协程任务被取消")
            exc = task.exception()
            if exc is not None:
                raise exc
            return task.result()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        done = threading.Event()

        def _on_fut_done(_: Any) -> None:
            done.set()

        # concurrent.futures.Future 已完成时 add_done_callback 会在
        # 调用线程内同步触发回调，因此无竞态；未完成时回调在
        # 写结果的线程（循环线程）内触发，Event 即刻被置位。
        # 轮询等待保证 Windows 上 Ctrl+C 可用（同 submit 的说明）。
        fut.add_done_callback(_on_fut_done)
        while not done.is_set():
            done.wait(0.2)
        exc = fut.exception()
        if exc is not None:
            raise exc
        return fut.result()


__all__ = ["StdLoopRuntime"]

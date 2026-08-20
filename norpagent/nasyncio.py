# ============================================================================
# norpagent.nasyncio —— 自研异步 IO 库（已打包进 norpagent 库）
#
# 事件循环核心完全自研，不调用、不依赖标准 asyncio。
#
# 设计目标：
#   1. 事件循环调度核心（Task 协程 trampoline / 定时器堆 / 线程安全唤醒）
#      全部自研，以获得对异步循环逻辑的完全掌控（自由度）
#   2. 底层仅依赖 Python 标准库的非 asyncio 模块：
#      threading / queue / heapq / selectors / socket /
#      concurrent.futures / subprocess / os / time
#   3. 提供与常见 asyncio 用法一一对应的同名 API，
#      熟悉 asyncio 的代码只需把 import asyncio 改为
#      import norpagent.nasyncio 即可完成移植（无需依赖标准 asyncio）
#
# 线程模型：
#   - 一个 EventLoop 绑定一个线程（与 norpagent 架构一致：每个会话一个循环线程）
#   - run_forever 在哪个线程调用，循环就属于哪个线程
#   - 跨线程提交（call_soon_threadsafe / Event.set / Future 完成通知 /
#     Task.cancel / run_coroutine_threadsafe）
#     通过「线程安全队列 + socketpair 自管道」唤醒阻塞在 select 上的循环线程
#
# 在 norpagent 中的位置：
#   - 本模块是 async_loop 槽位默认实现（norpagent.loops.nasyncio）的
#     调度核心；norpagent 库内不再 import 标准 asyncio（零依赖）。
#   - 顶层 ``norpagent.nasyncio``（即 ``np.nasyncio``）绑定到本模块：
#     直接调用 np.nasyncio() 等价于架构函数（返回 LoopRuntime）；
#     本模块同时可调用（见文末 _CallableCoreModule），因此：
#        import norpagent.nasyncio            → 得到本核心模块
#        np.nasyncio.EventLoop                → 自研事件循环类
#        np.nasyncio()                        → LoopRuntime（默认实现）
#
# 版权：Copyright (c) 2026 xingluosama
# ============================================================================

import concurrent.futures
import heapq
import itertools
import os
import queue as _queue
import selectors
import socket
import subprocess as _subprocess
import sys
import threading
import time as _time
import traceback as _traceback
from collections import deque

__version__ = "2.0.0"

__all__ = [
    # 异常
    "CancelledError",
    "InvalidStateError",
    "TimeoutError",
    # 循环
    "EventLoop",
    "AbstractEventLoop",
    "new_event_loop",
    "set_event_loop",
    "get_event_loop",
    "get_running_loop",
    # 调度句柄
    "Handle",
    "TimerHandle",
    # Future / Task
    "Future",
    "Task",
    "ensure_future",
    "run_coroutine_threadsafe",
    # 同步原语
    "Event",
    "Lock",
    "Condition",
    # 工具函数
    "sleep",
    "wait_for",
    # 子进程
    "Process",
    "create_subprocess_shell",
    "create_subprocess_exec",
    "subprocess",
]


# ═══════════════════════════════════════════════════════════════
#  异常定义
# ═══════════════════════════════════════════════════════════════

class CancelledError(BaseException):
    """协程被取消（与 asyncio.CancelledError 对齐：继承 BaseException，
    不会被普通的 except Exception 吞掉，保证取消语义穿透）。"""


class InvalidStateError(Exception):
    """Future/Task 状态非法操作。"""


# asyncio.TimeoutError 在 3.11 之前就是内建 TimeoutError 的别名，
# 这里直接复用内建类，保证 except TimeoutError 兼容。
TimeoutError = TimeoutError


# ═══════════════════════════════════════════════════════════════
#  调度句柄
# ═══════════════════════════════════════════════════════════════

class Handle:
    """call_soon / call_soon_threadsafe 返回的句柄。"""

    __slots__ = ("_cb", "_args", "_cancelled", "_loop")

    def __init__(self, cb, args, loop):
        self._cb = cb
        self._args = args
        self._cancelled = False
        self._loop = loop

    def cancel(self):
        """取消回调。已执行或已在执行的回调无法取消。"""
        self._cancelled = True

    def cancelled(self) -> bool:
        return self._cancelled

    def _run(self):
        if not self._cancelled:
            self._cb(*self._args)


class TimerHandle(Handle):
    """call_later 返回的定时器句柄。"""

    __slots__ = ("_when", "_seq")

    def __init__(self, when, seq, cb, args, loop):
        super().__init__(cb, args, loop)
        self._when = when
        self._seq = seq

    def when(self) -> float:
        return self._when

    def __lt__(self, other):
        return (self._when, self._seq) < (other._when, other._seq)


# ═══════════════════════════════════════════════════════════════
#  Future —— 可等待结果容器
# ═══════════════════════════════════════════════════════════════

class Future:
    """自研 Future：一次性的结果容器，可被协程 await。

    完成通知通过 _schedule_callbacks 统一调度到所属事件循环线程执行，
    保证 set_result 可以从任意线程触发（配合 call_soon_threadsafe）。
    """

    _PENDING = "pending"
    _CANCELLED = "cancelled"
    _FINISHED = "finished"

    def __init__(self, loop=None):
        self._loop = loop
        self._state = Future._PENDING
        self._result = None
        self._exception = None
        self._callbacks = []
        # run_in_executor 专用：取消时尝试取消底层线程任务
        self._cancel_hook = None

    # ── 状态查询 ──

    def done(self) -> bool:
        return self._state != Future._PENDING

    def cancelled(self) -> bool:
        return self._state == Future._CANCELLED

    def get_loop(self):
        return self._loop

    # ── 完成设置 ──

    def set_result(self, result):
        if self._state != Future._PENDING:
            raise InvalidStateError("invalid state: future already done")
        self._result = result
        self._state = Future._FINISHED
        self._schedule_callbacks()

    def set_exception(self, exception):
        if self._state != Future._PENDING:
            raise InvalidStateError("invalid state: future already done")
        if exception is None:
            raise TypeError("exception must not be None")
        self._exception = exception
        self._state = Future._FINISHED
        self._schedule_callbacks()

    def cancel(self) -> bool:
        """取消 Future。仅 PENDING 状态可取消。"""
        if self._state != Future._PENDING:
            return False
        if self._cancel_hook is not None:
            try:
                self._cancel_hook()
            except Exception:
                pass
        self._state = Future._CANCELLED
        self._schedule_callbacks()
        return True

    # ── 结果读取 ──

    def result(self):
        if self._state == Future._CANCELLED:
            raise CancelledError()
        if self._state == Future._PENDING:
            raise InvalidStateError("result is not ready")
        if self._exception is not None:
            raise self._exception
        return self._result

    def exception(self):
        if self._state == Future._CANCELLED:
            raise CancelledError()
        if self._state == Future._PENDING:
            raise InvalidStateError("exception is not ready")
        return self._exception

    # ── 回调 ──

    def add_done_callback(self, cb):
        """注册完成回调。已完成则立即调度执行。"""
        if self._state != Future._PENDING:
            self._invoke_callback(cb)
        else:
            self._callbacks.append(cb)

    def remove_done_callback(self, cb) -> int:
        removed = 0
        try:
            while True:
                self._callbacks.remove(cb)
                removed += 1
        except ValueError:
            pass
        return removed

    def _invoke_callback(self, cb):
        loop = self._loop
        if loop is None or loop.is_closed():
            # 无循环或循环已关闭：直接同步执行（无协程等待者时安全兜底）
            try:
                cb(self)
            except Exception:
                pass
            return
        # ★ 跨线程完成修复：Future 可能在任意线程被 set_result /
        #   set_exception / cancel（如外部线程即时停止）。此时必须走
        #   call_soon_threadsafe 写自管道唤醒循环——若走 call_soon，
        #   回调会滞留 _ready 队列而循环仍阻塞在 selector.select()，
        #   等待者永远不被唤醒（表现为任务挂起）。
        if loop._thread_id is not None and loop._thread_id != threading.get_ident():
            loop.call_soon_threadsafe(cb, self)
        else:
            loop.call_soon(cb, self)

    def _schedule_callbacks(self):
        callbacks, self._callbacks = self._callbacks, []
        for cb in callbacks:
            self._invoke_callback(cb)

    # ── 可等待协议 ──

    def __await__(self):
        if not self.done():
            yield self
        return self.result()

    def __repr__(self):
        return f"<{type(self).__name__} state={self._state}>"


# ═══════════════════════════════════════════════════════════════
#  Task —— 协程驱动（trampoline 式调度 + 取消注入）
# ═══════════════════════════════════════════════════════════════

class Task(Future):
    """自研 Task：把协程挂在事件循环上逐步推进。

    调度机制（经典 trampoline）：
      - _step() 调 coro.send(None) 推进协程
      - 协程 yield 出 Future 时挂起，注册 _on_waiter_done 回调
      - 等待的 Future 完成后，回调把 _step 重新排入 ready 队列继续推进
      - 取消：向协程 throw(CancelledError)，由协程内部决定是否响应
        （与 asyncio 语义一致：吞掉取消则任务正常完成，否则以 CancelledError 结束）
    """

    def __init__(self, coro, loop=None):
        super().__init__(loop)
        self._coro = coro
        self._fut_waiter = None
        self._must_cancel = False
        self._step_exc = None
        if self._loop is None or self._loop.is_closed():
            raise RuntimeError("Task requires a running event loop")
        self._loop.call_soon(self._step, None)

    # ── 推进协程 ──

    def _step(self, exc):
        if self.done():
            return
        self._fut_waiter = None
        loop = self._loop
        loop._current_task = self
        try:
            if exc is None:
                result = self._coro.send(None)
            else:
                result = self._coro.throw(exc)
        except StopIteration as si:
            self.set_result(si.value)
        except CancelledError as ce:
            # 协程未吞掉取消：任务以 CancelledError 结束
            self.set_exception(ce)
            self._close_coro()
        except BaseException as e:
            self.set_exception(e)
            self._close_coro()
        else:
            if result is None:
                # 协程内部 await 了可等待对象之外的调度点（罕见），继续推进
                loop.call_soon(self._step, None)
            elif isinstance(result, Future):
                if result is self:
                    loop.call_soon(
                        self._step, RuntimeError("Task cannot await on itself"))
                else:
                    self._fut_waiter = result
                    result.add_done_callback(self._on_waiter_done)
            elif hasattr(result, "__next__") or hasattr(result, "send"):
                # generator-based 协程兼容：串行推进其内部 yield 链
                loop.call_soon(self._step, None)
                self._fut_waiter = _chain_generator(loop, result, self)
            else:
                loop.call_soon(
                    self._step,
                    TypeError(f"coroutine yielded invalid object: {result!r}"))
        finally:
            loop._current_task = None
            if self._must_cancel and not self.done():
                self._must_cancel = False
                loop.call_soon(self._step, CancelledError())

    def _close_coro(self):
        try:
            self._coro.close()
        except Exception:
            pass

    def _on_waiter_done(self, fut):
        if self.done():
            return
        if fut.cancelled():
            self._loop.call_soon(self._step, CancelledError())
        elif fut.exception() is not None:
            self._loop.call_soon(self._step, fut.exception())
        else:
            self._loop.call_soon(self._step, None)

    # ── 取消 ──

    def cancel(self) -> bool:
        """请求取消任务（线程安全，可从任意线程调用）。

        返回 False 表示任务已完成、无法取消。

        ★ 即时停止关键改进（v1.1.0）：
        标准 asyncio 的 Task.cancel() 非线程安全（直接操作循环线程的
        ready 队列）。自研实现自动检测调用线程：
          - 循环线程内调用 → call_soon（零开销）
          - 其他线程调用   → call_soon_threadsafe（写自管道立即唤醒循环）
        因此外部线程可以直接取消任意任务，无需额外包装。
        """
        if self.done():
            return False
        self._must_cancel = True
        loop = self._loop
        if loop._thread_id is not None and loop._thread_id == threading.get_ident():
            loop.call_soon(self._throw_cancel)
        else:
            loop.call_soon_threadsafe(self._throw_cancel)
        return True

    def _throw_cancel(self):
        if not self.done() and self._must_cancel:
            self._must_cancel = False
            self._step(CancelledError())

    def cancelled(self) -> bool:
        return (
            self._state == Future._CANCELLED
            or isinstance(self._exception, CancelledError)
        )

    def get_coro(self):
        return self._coro


def _chain_generator(loop, gen, task):
    """串行推进 generator-based 协程的 yield 链（兼容老式协程，业务当前未使用）。"""
    fut = loop.create_future()

    def _advance(exc=None):
        try:
            if exc is None:
                result = gen.send(None)
            else:
                result = gen.throw(exc)
        except StopIteration as si:
            fut.set_result(si.value)
        except CancelledError as ce:
            fut.set_exception(ce)
        except BaseException as e:
            fut.set_exception(e)
        else:
            if isinstance(result, Future):
                result.add_done_callback(_on_sub_done)
            else:
                loop.call_soon(_advance, None)

    def _on_sub_done(sub_fut):
        if sub_fut.cancelled():
            loop.call_soon(_advance, CancelledError())
        elif sub_fut.exception() is not None:
            loop.call_soon(_advance, sub_fut.exception())
        else:
            loop.call_soon(_advance, None)

    loop.call_soon(_advance, None)
    return fut


def ensure_future(coro_or_future):
    """把协程包装为 Task；Future/Task 原样返回。"""
    if isinstance(coro_or_future, Future):
        return coro_or_future
    if hasattr(coro_or_future, "send"):
        loop = get_running_loop()
        return Task(coro_or_future, loop=loop)
    raise TypeError(
        f"expect coroutine or Future, got {type(coro_or_future).__name__}")


def run_coroutine_threadsafe(coro, loop) -> concurrent.futures.Future:
    """线程安全地把协程提交到目标循环执行，返回 concurrent.futures.Future。

    语义对齐 asyncio.run_coroutine_threadsafe：
      - 协程在目标循环线程内执行；
      - 返回的 Future 携带协程返回值；协程被取消时
        set_exception(CancelledError)，异常时 set_exception(异常)；
      - 目标循环已关闭时立即 set_exception(RuntimeError)。

    自研实现要点（v2.0.0）：
      - Task 的创建走 call_soon_threadsafe（写自管道唤醒），循环阻塞
        在 select 上也能立刻收到并开始推进协程；
      - 完成通知由 Task 的 done 回调在循环线程内写 concurrent.futures
        Future（concurrent.futures 本身线程安全），调用方从任意线程
        阻塞等待 result() / exception() 均无唤醒竞态。
    """
    cf = concurrent.futures.Future()

    if loop.is_closed():
        if not cf.done():
            cf.set_exception(RuntimeError("event loop is closed"))
        return cf

    def _on_task_done(task):
        if cf.done():
            return
        if task.cancelled():
            cf.set_exception(CancelledError())
            return
        exc = task.exception()
        if exc is not None:
            cf.set_exception(exc)
        else:
            cf.set_result(task.result())

    def _spawn():
        try:
            task = loop.create_task(coro)
        except RuntimeError as e:
            if not cf.done():
                cf.set_exception(e)
            return
        task.add_done_callback(_on_task_done)

    loop.call_soon_threadsafe(_spawn)
    return cf


# ═══════════════════════════════════════════════════════════════
#  同步原语
# ═══════════════════════════════════════════════════════════════

class Event:
    """自研事件。set() 线程安全：可从任意线程唤醒循环内等待者。

    关键设计（对齐 asyncio 语义并修复跨线程唤醒）：
      - wait() 在循环内注册 waiter future
      - set() 从任意线程调用时，通过 loop.call_soon_threadsafe 把
        future.set_result 调度到循环线程，自管道保证立即唤醒阻塞中的 select
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._flag = False
        self._waiters = []  # [(loop, future)]

    def is_set(self) -> bool:
        return self._flag

    def set(self):
        with self._lock:
            self._flag = True
            waiters, self._waiters = self._waiters, []
        for loop, fut in waiters:
            if loop.is_closed():
                # 循环已关闭：同步完成，避免悬挂
                if not fut.done():
                    fut.set_result(True)
            else:
                loop.call_soon_threadsafe(fut.set_result, True)

    def clear(self):
        with self._lock:
            self._flag = False

    async def wait(self) -> bool:
        loop = get_running_loop()
        with self._lock:
            if self._flag:
                return True
            fut = loop.create_future()
            self._waiters.append((loop, fut))
        try:
            await fut
        except CancelledError:
            # 等待被取消（wait_for 超时场景）：从等待队列移除，避免残留
            with self._lock:
                for item in self._waiters:
                    if item[1] is fut:
                        self._waiters.remove(item)
                        break
            raise
        return True


class Lock:
    """自研异步互斥锁（仅限循环线程内使用，async with 语义）。"""

    def __init__(self):
        self._locked = False
        self._waiters = deque()  # futures

    def locked(self) -> bool:
        return self._locked

    async def acquire(self) -> bool:
        if not self._locked and not self._waiters:
            self._locked = True
            return True
        loop = get_running_loop()
        fut = loop.create_future()
        self._waiters.append(fut)
        try:
            await fut
            self._locked = True
            return True
        except CancelledError:
            # 等待期间被取消：从队列移除，防止锁传给已取消的等待者
            try:
                self._waiters.remove(fut)
            except ValueError:
                pass
            raise

    def release(self):
        # 把锁直接传给队首等待者（锁始终处于已持有状态，仅更换持有者）
        while self._waiters:
            fut = self._waiters.popleft()
            if not fut.done():
                fut.set_result(True)
                return
        self._locked = False

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *exc_info):
        self.release()


class Condition:
    """自研异步条件变量（构建在 Lock 之上）。"""

    def __init__(self, lock=None):
        self._lock = lock if lock is not None else Lock()
        self._waiters = deque()

    @property
    def lock(self) -> Lock:
        return self._lock

    async def acquire(self):
        await self._lock.acquire()

    def release(self):
        self._lock.release()

    async def wait(self):
        if not self._lock.locked():
            raise RuntimeError("cannot wait on un-acquired lock")
        loop = get_running_loop()
        fut = loop.create_future()
        self._waiters.append(fut)
        self._lock.release()
        try:
            await fut
        except CancelledError:
            try:
                self._waiters.remove(fut)
            except ValueError:
                pass
            raise
        finally:
            await self._lock.acquire()

    def notify(self, n: int = 1):
        notified = 0
        while self._waiters and notified < n:
            fut = self._waiters.popleft()
            if not fut.done():
                fut.set_result(True)
                notified += 1

    def notify_all(self):
        waiters, self._waiters = self._waiters, deque()
        for fut in waiters:
            if not fut.done():
                fut.set_result(True)

    async def __aenter__(self):
        await self._lock.acquire()
        return self

    async def __aexit__(self, *exc_info):
        self._lock.release()


# ═══════════════════════════════════════════════════════════════
#  工具协程
# ═══════════════════════════════════════════════════════════════

async def _yield_control():
    """让出一次控制权（sleep(0) 语义）。"""
    loop = get_running_loop()
    fut = loop.create_future()
    loop.call_soon(fut.set_result, None)
    await fut


async def sleep(seconds: float):
    """异步睡眠 seconds 秒。seconds <= 0 时仅让出控制权。"""
    if seconds is None:
        raise TypeError("sleep() seconds must be a number")
    if seconds <= 0:
        await _yield_control()
        return
    loop = get_running_loop()
    fut = loop.create_future()

    def _wake():
        if not fut.done():
            fut.set_result(None)

    handle = loop.call_later(seconds, _wake)
    try:
        await fut
    finally:
        handle.cancel()


async def wait_for(awaitable, timeout):
    """等待可等待对象完成，超时抛 TimeoutError 并取消内部任务。

    语义对齐 asyncio.wait_for：
      - 超时后取消内部 Task（throw CancelledError，由协程自行处理）
      - 内部为 Future 时直接 cancel()
      - 返回内部任务的最终结果
    """
    if timeout is None:
        return await awaitable

    loop = get_running_loop()

    if isinstance(awaitable, Future):
        inner = awaitable
    else:
        inner = loop.create_task(awaitable)

    bridge = loop.create_future()

    def _on_inner_done(f):
        if bridge.done():
            return
        if f.cancelled():
            bridge.set_exception(CancelledError())
        elif f.exception() is not None:
            bridge.set_exception(f.exception())
        else:
            bridge.set_result(f.result())

    def _on_timeout():
        if bridge.done():
            return
        inner.cancel()
        bridge.set_exception(TimeoutError())

    inner.add_done_callback(_on_inner_done)
    timer = loop.call_later(timeout, _on_timeout)
    try:
        return await bridge
    except CancelledError:
        # 外层取消：传播取消到内部任务
        inner.cancel()
        raise
    finally:
        timer.cancel()


# ═══════════════════════════════════════════════════════════════
#  事件循环 —— 自研核心
# ═══════════════════════════════════════════════════════════════

class EventLoop:
    """自研单线程事件循环。

    核心结构：
      - _ready: 待执行回调双端队列（call_soon / 到期定时器 / Task 推进）
      - _scheduled: 时间堆 [(when, seq, TimerHandle)]（call_later / sleep）
      - _ts_queue: 线程安全提交队列（call_soon_threadsafe）
      - 自管道（socketpair）：跨线程唤醒阻塞在 selector.select 上的循环
      - _selector: selectors.DefaultSelector（仅监听自管道可读事件）
    """

    def __init__(self):
        self._ready = deque()
        self._scheduled = []
        self._seq = itertools.count()
        self._selector = selectors.DefaultSelector()
        self._ssock, self._csock = socket.socketpair()
        self._ssock.setblocking(False)
        self._csock.setblocking(False)
        self._selector.register(self._ssock, selectors.EVENT_READ)
        self._ts_queue = _queue.Queue()
        self._running = False
        self._stopping = False
        self._closed = False
        self._thread_id = None
        self._current_task = None
        # run_until_complete 当前的主任务（abort_main 的取消目标）
        self._main_task = None

    # ── 状态 ──

    def time(self) -> float:
        """循环单调时钟（秒）。"""
        return _time.monotonic()

    def is_running(self) -> bool:
        return self._running

    def is_closed(self) -> bool:
        return self._closed

    def close(self):
        """关闭循环（释放 selector 与自管道）。运行中的循环不可关闭。"""
        if self._running:
            raise RuntimeError("cannot close a running event loop")
        if self._closed:
            return
        self._closed = True
        try:
            self._selector.unregister(self._ssock)
        except Exception:
            pass
        try:
            self._selector.close()
        except Exception:
            pass
        try:
            self._ssock.close()
        except Exception:
            pass
        try:
            self._csock.close()
        except Exception:
            pass

    # ── 调度 ──

    def call_soon(self, cb, *args) -> Handle:
        """把回调排入 ready 队列（仅在循环线程调用）。"""
        handle = Handle(cb, args, self)
        self._ready.append(handle)
        return handle

    def call_later(self, delay: float, cb, *args) -> TimerHandle:
        """delay 秒后执行回调（返回可取消的 TimerHandle）。"""
        when = self.time() + max(0.0, delay)
        handle = TimerHandle(when, next(self._seq), cb, args, self)
        heapq.heappush(self._scheduled, (when, handle._seq, handle))
        return handle

    def call_soon_threadsafe(self, cb, *args) -> Handle:
        """线程安全调度：可从任意线程调用，立即唤醒循环线程。"""
        handle = Handle(cb, args, self)
        if self._closed:
            handle.cancel()
            return handle
        self._ts_queue.put(handle)
        self._wake()
        return handle

    def _wake(self):
        """写自管道一个字节，唤醒阻塞在 select 上的循环线程。"""
        try:
            self._csock.send(b"\0")
        except OSError:
            pass  # 管道已满（已有唤醒信号在途）或循环已关闭

    # ── Future / Task ──

    def create_future(self) -> Future:
        return Future(loop=self)

    def create_task(self, coro) -> Task:
        return Task(coro, loop=self)

    # ── 线程池执行 ──

    def run_in_executor(self, executor, fn, *args) -> Future:
        """在线程池执行同步函数，返回可 await 的 Future。

        executor 为 None 时使用全局共享 ThreadPoolExecutor。
        取消返回的 Future 会尽力取消底层线程任务。

        注意：norpagent 的默认循环运行时（norpagent.loops.nasyncio）
        不使用本方法执行 submit 任务——任务本体可能阻塞在沙箱
        subprocess / HTTP I/O，且 ThreadPoolExecutor 的工作线程在
        解释器退出时会被强制 join（任务卡住进程即僵住）。默认
        运行时用裸守护线程工作池执行 submit（详见其模块文档）。
        本方法供循环内协程执行「可控时长」的同步函数使用。
        """
        fut = Future(loop=self)
        if executor is None:
            executor = _get_default_executor()
        try:
            cf = executor.submit(fn, *args)
        except Exception as e:
            fut.set_exception(e)
            return fut

        def _on_done(cfut):
            # 在线程池线程执行：结果必须调度回循环线程
            if fut.cancelled():
                return
            try:
                result = cfut.result()
                self.call_soon_threadsafe(fut.set_result, result)
            except BaseException as e:
                self.call_soon_threadsafe(fut.set_exception, e)

        cf.add_done_callback(_on_done)

        def _cancel():
            return cf.cancel()

        fut._cancel_hook = _cancel
        return fut

    # ── 主循环 ──

    def run_forever(self):
        """运行循环直到 stop()。绑定当前线程为循环线程。"""
        if self._running:
            raise RuntimeError("event loop already running")
        if self._closed:
            raise RuntimeError("event loop is closed")
        self._thread_id = threading.get_ident()
        _register_running_loop(self)
        self._running = True
        try:
            while True:
                self._run_once()
                if self._stopping and not self._ready:
                    break
        finally:
            self._running = False
            self._stopping = False
            self._current_task = None
            _unregister_running_loop(self)

    def run_until_complete(self, coro):
        """运行循环直到协程完成，返回其结果（异常则抛出）。

        ★ 即时停止支持（v1.1.0）：
        主任务注册到 self._main_task，外部线程可随时调用 abort_main()
        向其注入 CancelledError 强制中断（无需等待当前 await 结束）。
        """
        task = self.create_task(coro)
        self._main_task = task
        task.add_done_callback(lambda t: self.stop())
        try:
            self.run_forever()
            return task.result()
        finally:
            if self._main_task is task:
                self._main_task = None

    def stop(self):
        """请求停止循环（当前轮 ready 清空后退出）。

        注意：stop() 是「优雅停止」，不会取消正在运行的任务——
        若主任务正 await 一个长期未完成的 Future，循环线程仍会等待。
        需要「即时停止」请使用 abort_main()（先取消主任务，任务
        完成后 done 回调会自动触发本 stop() 退出循环）。
        """
        self._stopping = True
        self._wake()

    def abort_main(self) -> bool:
        """线程安全：立即取消 run_until_complete 的主任务（即时停止）。

        ★ 自研架构核心能力（v1.1.0）：
        标准 asyncio 没有对外的「取消主任务」入口——外部线程只能设置
        标志位等业务代码自行检查，无法强制中断正在 await 的协程，
        停止延迟取决于当前操作（工具/API 调用可能长达数分钟）。

        本方法向主协程注入 CancelledError（走 call_soon_threadsafe +
        自管道唤醒），立即中断其当前 await（工具执行 / API 流 /
        用户输入等待），协程栈逐层展开：
          - wait_for 会传播取消并取消内部 Future
          - 子进程 communicate 被取消时自动 kill 进程
          - 业务协程捕获 CancelledError 后返回 "stopped" 收口

        主任务完成后 done 回调触发 stop()，循环随之退出。
        返回是否确实发起了取消（False = 无主任务或已完成）。
        """
        task = self._main_task
        if task is None or task.done():
            return False
        return task.cancel()

    def _run_once(self):
        # 1. 到期定时器 → ready
        self._drain_scheduled()

        # 2. 计算 select 等待时长
        if self._ready:
            wait = 0.0
        elif self._scheduled:
            wait = max(0.0, self._scheduled[0][0] - self.time())
        else:
            wait = None

        # 3. 先 drain 一次线程安全队列（减少 select 无谓唤醒）
        self._drain_threadsafe()

        # 4. 阻塞等待（自管道可读或定时器到期）
        try:
            events = self._selector.select(wait)
        except (InterruptedError, OSError):
            events = []
        for _key, _mask in events:
            self._drain_wake_pipe()

        self._drain_threadsafe()

        # 5. 执行 ready 队列（快照长度，防饥饿）
        nready = len(self._ready)
        for _ in range(nready):
            handle = self._ready.popleft()
            if not handle.cancelled():
                try:
                    handle._run()
                except Exception:
                    # 回调异常不能击穿事件循环：记录后继续
                    _traceback.print_exc()

    def _drain_scheduled(self):
        now = self.time()
        while self._scheduled and self._scheduled[0][0] <= now:
            _when, _seq, handle = heapq.heappop(self._scheduled)
            if not handle.cancelled():
                self._ready.append(handle)
            now = self.time()

    def _drain_threadsafe(self):
        while True:
            try:
                handle = self._ts_queue.get_nowait()
            except _queue.Empty:
                break
            if not handle.cancelled():
                self._ready.append(handle)

    def _drain_wake_pipe(self):
        while True:
            try:
                data = self._ssock.recv(4096)
                if not data:
                    break
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break


AbstractEventLoop = EventLoop


# ═══════════════════════════════════════════════════════════════
#  循环管理（全局状态）
# ═══════════════════════════════════════════════════════════════

_running_loops = {}          # thread_id -> EventLoop
_running_loops_lock = threading.Lock()
_global_loop = None
_global_loop_lock = threading.Lock()


def _register_running_loop(loop):
    with _running_loops_lock:
        _running_loops[threading.get_ident()] = loop


def _unregister_running_loop(loop):
    with _running_loops_lock:
        tid = threading.get_ident()
        if _running_loops.get(tid) is loop:
            _running_loops.pop(tid, None)


def new_event_loop() -> EventLoop:
    """创建一个新的事件循环。"""
    return EventLoop()


def set_event_loop(loop):
    """设置当前线程的事件循环（兼容接口；循环绑定由 run_forever 完成）。"""
    global _global_loop
    with _global_loop_lock:
        _global_loop = loop


def get_event_loop() -> EventLoop:
    """返回 set_event_loop 设置的循环（兼容接口）。"""
    with _global_loop_lock:
        return _global_loop


def get_running_loop() -> EventLoop:
    """返回当前线程正在运行的事件循环（无则抛 RuntimeError）。"""
    with _running_loops_lock:
        loop = _running_loops.get(threading.get_ident())
    if loop is None:
        raise RuntimeError("no running event loop")
    return loop


# ═══════════════════════════════════════════════════════════════
#  共享线程池（run_in_executor 默认执行器）
# ═══════════════════════════════════════════════════════════════

_default_executor = None
_default_executor_lock = threading.Lock()


def _get_default_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _default_executor
    with _default_executor_lock:
        if _default_executor is None:
            _default_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=16,
                thread_name_prefix="nasync-io",
            )
        return _default_executor


# ═══════════════════════════════════════════════════════════════
#  子进程支持（自研：Popen + 读管道线程，communicate 被取消时杀进程）
# ═══════════════════════════════════════════════════════════════

class _SubprocessConst:
    """asyncio.subprocess 常量兼容命名空间。"""
    DEVNULL = _subprocess.DEVNULL
    PIPE = _subprocess.PIPE
    STDOUT = _subprocess.STDOUT


subprocess = _SubprocessConst()


class Process:
    """自研子进程封装。

    与 asyncio.subprocess.Process 关键差异（更简、更稳）：
      - 管道读取用专用 daemon 线程（read 至 EOF），不占事件循环
      - communicate() 为协程：在线程池中 join 读线程 + wait 进程
      - communicate() 被取消（wait_for 超时）时自动 kill 进程，杜绝僵尸
    """

    def __init__(self, popen):
        self._popen = popen
        self.returncode = None
        self._stdout_thread = None
        self._stderr_thread = None
        self._stdout_data = None
        self._stderr_data = None
        if popen.stdout is not None:
            self._stdout_thread = self._spawn_reader(
                popen.stdout, "_stdout_data")
        if popen.stderr is not None:
            self._stderr_thread = self._spawn_reader(
                popen.stderr, "_stderr_data")

    def _spawn_reader(self, pipe, attr):
        def _read():
            try:
                data = pipe.read()
            except Exception:
                data = b""
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass
            setattr(self, attr, data)

        t = threading.Thread(
            target=_read, daemon=True,
            name=f"nasync-io-proc-{self._popen.pid}")
        t.start()
        return t

    @property
    def pid(self) -> int:
        return self._popen.pid

    def poll(self):
        rc = self._popen.poll()
        if rc is not None:
            self.returncode = rc
        return rc

    def wait(self, timeout=None) -> int:
        """同步等待进程结束（返回 returncode）。"""
        rc = self._popen.wait(timeout=timeout)
        self.returncode = rc
        return rc

    def terminate(self):
        try:
            self._popen.terminate()
        except (ProcessLookupError, OSError):
            pass

    def kill(self):
        try:
            self._popen.kill()
        except (ProcessLookupError, OSError):
            pass

    def send_signal(self, sig):
        try:
            self._popen.send_signal(sig)
        except (ProcessLookupError, OSError):
            pass

    async def communicate(self, input=None):
        """等待进程结束并收集输出。

        返回 (stdout_bytes, stderr_bytes)。管道为 None 时对应值为 None。

        取消语义（wait_for 超时 / 外部取消）：
          kill 进程后重新抛出 CancelledError，确保不留僵尸进程。
        """
        loop = get_running_loop()

        if input is not None and self._popen.stdin is not None:
            try:
                self._popen.stdin.write(input)
                self._popen.stdin.close()
            except Exception:
                pass

        def _drain():
            if self._stdout_thread is not None:
                self._stdout_thread.join()
            if self._stderr_thread is not None:
                self._stderr_thread.join()
            rc = self._popen.wait()
            self.returncode = rc
            return (self._stdout_data, self._stderr_data)

        try:
            return await loop.run_in_executor(None, _drain)
        except CancelledError:
            # 僵尸进程防护：取消即杀进程，保证读线程尽快 EOF 退出
            self.kill()
            raise

    def __repr__(self):
        return f"<Process pid={self.pid} returncode={self.returncode}>"


def _popen_kwargs(stdin, stdout, stderr, cwd, env, creationflags,
                  start_new_session, extra):
    kwargs = dict(extra)
    kwargs["stdin"] = stdin
    kwargs["stdout"] = stdout
    kwargs["stderr"] = stderr
    if cwd is not None:
        kwargs["cwd"] = cwd
    if env is not None:
        kwargs["env"] = env
    if creationflags:
        kwargs["creationflags"] = creationflags
    if start_new_session is not None:
        # Windows 的 Popen 不支持 start_new_session，忽略
        if os.name != "nt":
            kwargs["start_new_session"] = start_new_session
    return kwargs


async def create_subprocess_shell(cmd, stdin=None, stdout=None, stderr=None,
                                  cwd=None, env=None, creationflags=0,
                                  start_new_session=None, **kwargs) -> Process:
    """通过 shell 启动子进程（自研实现，参数与 asyncio 版本对齐）。"""
    popen_kw = _popen_kwargs(stdin, stdout, stderr, cwd, env,
                             creationflags, start_new_session, kwargs)
    popen = _subprocess.Popen(cmd, shell=True, **popen_kw)
    return Process(popen)


async def create_subprocess_exec(program, *args, stdin=None, stdout=None,
                                 stderr=None, cwd=None, env=None,
                                 creationflags=0, start_new_session=None,
                                 **kwargs) -> Process:
    """直接启动子进程（无 shell，防命令注入）。"""
    popen_kw = _popen_kwargs(stdin, stdout, stderr, cwd, env,
                             creationflags, start_new_session, kwargs)
    popen = _subprocess.Popen([program, *args], shell=False, **popen_kw)
    return Process(popen)


# ═══════════════════════════════════════════════════════════════
#  可调用核心模块：让「nasyncio 打包进库」的名字语义自洽
# ═══════════════════════════════════════════════════════════════

class _CallableCoreModule(type(sys)):
    """把本核心模块变成可调用对象。

    顶层 ``norpagent.nasyncio``（``np.nasyncio``）绑定到本模块。
    直接调用本模块等价于调用架构函数 ``norpagent.nasyncio()``
    （返回 LoopRuntime 默认实现），因此三类用法全部成立：

        import norpagent.nasyncio as core   # 得到本模块
        core.EventLoop                      # 自研事件循环类
        core(...)                           # == np.nasyncio(...)，返回 LoopRuntime
    """

    def __call__(self, address=None, **config):
        from norpagent.loops import nasyncio as _arch_fn

        return _arch_fn(address, **config)


sys.modules[__name__].__class__ = _CallableCoreModule

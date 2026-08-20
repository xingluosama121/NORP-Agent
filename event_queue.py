# -*- coding: utf-8 -*-
#事件队列模块 - Vibe Coding Agent
#线程安全的生产者-消费者队列，用于 Agent Loop 与前端之间的通信。
#支持软上限和智能丢弃策略。

#Copyright (c) 2026 xingluosama


import threading
from collections import deque
from typing import Optional, Union


class EventQueue:
    """
    线程安全的事件队列。

    特性：
    生产者（Agent Loop）调用 put() 放入事件
    消费者（前端轮询）调用 get() 取走事件
    队列超过 max_size 时自动丢弃最老的 T: 事件
    get() 在没有事件时返回 "WAIT"，流结束时返回 None

   """

    def __init__(self, max_size: int = 2000):
        """
        初始化事件队列。

        Args:
            max_size: 队列最大长度，默认 2000。
                      超过此值时自动丢弃最老的 T:（思考）事件。
                      绝不丢弃 R: 等关键内容事件。
        """
        if max_size < 1:
            raise ValueError("max_size 必须大于 0")
        self.max_size = max_size
        self._deque: deque = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._finished = False

    def put(self, event: str) -> None:
        """
        放入一个事件到队列末尾。

        Args:
            event: 事件字符串，格式为 "前缀:内容"。
                   前缀包括 T: R: E: Q: D: 等。

        优化：连续的 T:（思考）事件直接在队尾合并拼接，
        使队列中思考事件数 = 思考块数量（而非 chunk 数量），
        避免长思考过程产生海量小事件触发丢弃导致思考内容吞字。
        """
        with self._lock:
            if event.startswith("T:") and self._deque and self._deque[-1].startswith("T:"):
                # 同一思考块的连续 chunk 合并为一个事件，内容不丢失
                self._deque[-1] += event[2:]
            else:
                self._deque.append(event)

            while len(self._deque) > self.max_size:
                dropped = False
                for i, e in enumerate(self._deque):
                    if e.startswith("T:"):
                        del self._deque[i]
                        # 若紧随其后是 F:（思考块结束标记），一并删除，
                        # 防止前端收到孤立的 F: 把思考块截断导致显示破碎
                        if i < len(self._deque) and self._deque[i].startswith("F:"):
                            del self._deque[i]
                        dropped = True
                        break
                if not dropped:
                    break

            self._not_empty.notify()

    def get(self, timeout: float = 0.05) -> Optional[str]:
        """
        从队列头部取出一个事件。

        Args:
            timeout: 等待超时时间（秒），默认 0.05 秒。
                     用于前端轮询场景，避免永久阻塞。

        Returns:
            - "WAIT": 暂无事件，稍后再试
            - None: 流已结束，队列为空
            - str: 事件字符串
        """
        with self._not_empty:
            if not self._deque:
                if self._finished:
                    return None
                self._not_empty.wait(timeout)
                if not self._deque:
                    return "WAIT" if not self._finished else None

            return self._deque.popleft()

    def signal_finish(self) -> None:
        """
        标记流结束。
        调用后，get() 在队列为空时返回 None 而非 "WAIT"。
        """
        with self._lock:
            self._finished = True
            self._not_empty.notify_all()

    def reset(self) -> None:
        """
        重置队列状态（用于新一轮任务）。
        清空所有事件，重置结束标志。
        """
        with self._lock:
            self._deque.clear()
            self._finished = False

    @property
    def size(self) -> int:
        """当前队列中的事件数量。"""
        with self._lock:
            return len(self._deque)

    @property
    def finished(self) -> bool:
        """流是否已结束。"""
        with self._lock:
            return self._finished


#test
if __name__ == "__main__":
    import time

    print("=== EventQueue Self-Test ===\n")

    # Test 1: Basic put/get
    print("[Test1] Basic put and get")
    q = EventQueue(max_size=10)
    q.put("T:reasoning1")
    q.put("R:reply1")
    q.put("D:done")
    q.signal_finish()

    events = []
    while True:
        e = q.get()
        if e is None:
            break
        events.append(e)

    assert events == ["T:reasoning1", "R:reply1", "D:done"], f"Expected 3 events, got {events}"
    print("  PASS")

    # Test 2: Empty queue returns WAIT
    print("[Test2] Empty queue returns WAIT when not finished")
    q = EventQueue()
    e = q.get(timeout=0.01)
    assert e == "WAIT", f"Expected WAIT, got {e}"
    print("  PASS")

    # Test 3: Finished stream returns None
    print("[Test3] Finished stream returns None")
    q.signal_finish()
    e = q.get(timeout=0.01)
    assert e is None, f"Expected None, got {e}"
    print("  PASS")

    # Test 4: Over limit only drops T: events, NEVER drops R: content
    print("[Test4] Over limit: drop T: only, never drop R:")
    q = EventQueue(max_size=3)
    q.put("T:old1")
    q.put("T:old2")
    q.put("R:important1")  # queue full
    q.put("T:new1")        # should drop T:old1
    q.put("T:new2")        # should drop T:old2
    q.put("R:important2")  # no T: to drop, let queue grow naturally
    q.signal_finish()

    events = []
    while True:
        e = q.get()
        if e is None:
            break
        events.append(e)

    # All R: events must be preserved, T: events may be dropped
    assert "T:old1" not in events, "old1 should be dropped"
    assert "T:old2" not in events, "old2 should be dropped"
    assert "R:important1" in events, "important1 must NOT be dropped"
    assert "R:important2" in events, "important2 must NOT be dropped"
    print(f"  Final queue ({len(events)} events): {events}")
    print("  PASS")

    # Test 5: reset clears everything
    print("[Test5] reset clears the queue")
    q.reset()
    assert q.size == 0
    assert q.finished == False
    q.put("T:new_task")
    e = q.get()
    assert e == "T:new_task"
    q.signal_finish()
    assert q.get() is None
    print("  PASS")

    # Test 6: Thread safety (multiple producers), T: may be merged
    print("[Test6] Multi-threaded concurrent put (T: merged)")
    q = EventQueue(max_size=100)

    def producer(prefix, count):
        for i in range(count):
            q.put(f"{prefix}:{i}")
            time.sleep(0.001)

    t1 = threading.Thread(target=producer, args=("T", 50))
    t2 = threading.Thread(target=producer, args=("R", 50))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    q.signal_finish()

    events = []
    while True:
        e = q.get()
        if e is None:
            break
        events.append(e)

    # All R: events must be preserved individually
    r_events = [e for e in events if e.startswith("R:")]
    assert len(r_events) == 50, f"Expected 50 R events, got {len(r_events)}"
    # T: events may be merged, but merged text must contain EVERY chunk
    t_text = "".join(e[2:] for e in events if e.startswith("T:"))
    expected_t = "".join(str(i) for i in range(50))
    assert t_text == expected_t, "Merged T: text must contain all reasoning chunks"
    print("  PASS")

    # Test 7: Invalid max_size
    print("[Test7] max_size < 1 raises ValueError")
    try:
        EventQueue(max_size=0)
        print("  FAIL: should have raised ValueError")
    except ValueError:
        print("  PASS")

    # Test 8: Consecutive T: events merge into a single event (no char loss)
    print("[Test8] Consecutive T: events merge without losing chars")
    q = EventQueue()
    q.put("T:aaa")
    q.put("T:bbb")
    q.put("T:ccc")
    q.put("F:")
    q.put("R:reply")
    q.signal_finish()

    events = []
    while True:
        e = q.get()
        if e is None:
            break
        events.append(e)

    assert events == ["T:aaabbbccc", "F:", "R:reply"], f"Expected merged T, got {events}"
    print("  PASS")

    # Test 9: Dropping old T: also removes its trailing F: (no broken thinking block)
    print("[Test9] Dropping T: also drops its trailing F:")
    q = EventQueue(max_size=2)
    q.put("T:aaa")
    q.put("T:bbb")   # merged -> "T:aaabbb"
    q.put("F:")      # queue full -> drop "T:aaabbb" and trailing "F:"
    q.put("R:ok")
    q.signal_finish()

    events = []
    while True:
        e = q.get()
        if e is None:
            break
        events.append(e)

    assert "F:" not in events, "Orphan F: must not survive without its thinking block"
    assert events == ["R:ok"], f"Expected only R:ok, got {events}"
    print("  PASS")

    print("\n=== All Tests Passed ===")

# Copyright (c) 2026 xingluosama121, MIT Licensed
"""内存会话管理器：默认会话存储（进程内、线程安全）。

适合演示与基准测试。生产环境请替换为持久化实现（P2 提供 SQLite 版本），
替换方式：``registry.register_session("memory", MySessionManager)``，
预设无需任何修改。
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Dict, List, Optional

from norpagent.protocols.model import ChatMessage
from norpagent.protocols.session import Session


class MemorySessionManager:
    """进程内内存会话存储。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.RLock()

    def create_session(self, title: str = "",
                       session_id: Optional[str] = None) -> Session:
        with self._lock:
            if session_id:
                existing = self._sessions.get(session_id)
                if existing is not None:
                    return existing
            sess = Session(
                id=session_id or uuid.uuid4().hex[:16],
                title=title or f"session-{len(self._sessions) + 1}",
                created_at=time.time(),
            )
            self._sessions[sess.id] = sess
            return sess

    def get_session(self, session_id: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(session_id)

    def append_message(self, session_id: str, message: ChatMessage) -> bool:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                return False
            sess.messages.append(message)
            return True

    def history(self, session_id: str) -> List[ChatMessage]:
        with self._lock:
            sess = self._sessions.get(session_id)
            return list(sess.messages) if sess else []

    def list_sessions(self) -> List[Session]:
        with self._lock:
            return sorted(
                self._sessions.values(), key=lambda s: s.created_at, reverse=True
            )

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

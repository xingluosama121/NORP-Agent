# Copyright (c) 2026 xingluosama121, MIT Licensed
"""SQLite 会话管理器：持久化会话存储（标准库 sqlite3，零第三方依赖）。

与 MemorySessionManager 实现同一 SessionManager 协议，
替换方式不变：

    registry.register_session("memory", SQLiteSessionManager)

默认库文件 ``~/.norpagent/sessions.db``，可用 ``path`` 参数指定
其他位置（测试时传入临时文件即可）。

线程安全：sqlite3 连接以 check_same_thread=False 打开，
所有公开方法由 RLock 串行化；每次写操作立即 commit，
保证进程崩溃后数据仍完整。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import List, Optional

from norpagent.protocols.model import ChatMessage, ToolCallSpec
from norpagent.protocols.session import Session

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_calls TEXT NOT NULL DEFAULT '',
    tool_call_id TEXT,
    name TEXT,
    reasoning TEXT NOT NULL DEFAULT '',
    has_reasoning INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
"""

# 旧库迁移：早期版本的 messages 表无 reasoning 列（DeepSeek V4 契约需要）
_MESSAGE_MIGRATIONS = (
    ("reasoning", "TEXT NOT NULL DEFAULT ''"),
    ("has_reasoning", "INTEGER NOT NULL DEFAULT 0"),
)


def _message_to_row(message: ChatMessage) -> tuple:
    tool_calls_json = ""
    if message.tool_calls:
        tool_calls_json = json.dumps(
            [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in message.tool_calls
            ],
            ensure_ascii=False,
        )
    return (
        message.role,
        message.content or "",
        tool_calls_json,
        message.tool_call_id,
        message.name,
        message.reasoning or "",
        1 if message.has_reasoning else 0,
    )


def _row_to_message(row: sqlite3.Row) -> ChatMessage:
    tool_calls: Optional[List[ToolCallSpec]] = None
    raw = row["tool_calls"] or ""
    if raw:
        try:
            tool_calls = [
                ToolCallSpec(
                    id=item.get("id", f"call_{i}"),
                    name=item.get("name", ""),
                    arguments=item.get("arguments") or {},
                )
                for i, item in enumerate(json.loads(raw))
            ]
        except (json.JSONDecodeError, TypeError):
            tool_calls = None
    return ChatMessage(
        role=row["role"],
        content=row["content"] or "",
        tool_calls=tool_calls,
        tool_call_id=row["tool_call_id"],
        name=row["name"],
        reasoning=row["reasoning"] or "",
        has_reasoning=bool(row["has_reasoning"]),
    )


class SQLiteSessionManager:
    """SQLite 持久化会话存储。"""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.path.join(
            os.path.expanduser("~"), ".norpagent", "sessions.db"
        )
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """旧库迁移：为已存在的 messages 表补充缺失列。"""
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(messages)")}
        for column, decl in _MESSAGE_MIGRATIONS:
            if column not in cols:
                self._conn.execute(
                    f"ALTER TABLE messages ADD COLUMN {column} {decl}"
                )

    # ── SessionManager 协议 ──────────────────────────────

    def create_session(self, title: str = "",
                       session_id: Optional[str] = None) -> Session:
        if session_id:
            existing = self.get_session(session_id)
            if existing is not None:
                return existing
        sess = Session(
            id=session_id or uuid.uuid4().hex[:16],
            title=title or f"session-{int(time.time())}",
            created_at=time.time(),
        )
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO sessions (id, title, created_at) VALUES (?, ?, ?)",
                    (sess.id, sess.title, sess.created_at),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                # 并发下同 id 已被创建：回退到已存在的会话
                existing = self.get_session(sess.id)
                if existing is not None:
                    return existing
                raise
        return sess

    def get_session(self, session_id: str) -> Optional[Session]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, created_at FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            messages = self._load_messages(session_id)
        return Session(
            id=row["id"], title=row["title"], created_at=row["created_at"], messages=messages
        )

    def append_message(self, session_id: str, message: ChatMessage) -> bool:
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if exists is None:
                return False
            self._conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_calls,"
                " tool_call_id, name, reasoning, has_reasoning)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, *_message_to_row(message)),
            )
            self._conn.commit()
            return True

    def history(self, session_id: str) -> List[ChatMessage]:
        with self._lock:
            return self._load_messages(session_id)

    def list_sessions(self) -> List[Session]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, created_at FROM sessions ORDER BY created_at DESC"
            ).fetchall()
        return [
            Session(id=r["id"], title=r["title"], created_at=r["created_at"])
            for r in rows
        ]

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            deleted = cur.rowcount > 0
            if deleted:
                self._conn.execute(
                    "DELETE FROM messages WHERE session_id = ?", (session_id,)
                )
            self._conn.commit()
            return deleted

    # ── 扩展：持久层管理 ─────────────────────────────────

    def close(self) -> None:
        """关闭数据库连接（进程退出 / 测试清理时调用）。"""
        with self._lock:
            self._conn.close()

    def clear(self) -> None:
        """清空全部会话（测试与数据重置用）。"""
        with self._lock:
            self._conn.execute("DELETE FROM messages")
            self._conn.execute("DELETE FROM sessions")
            self._conn.commit()

    # ── 内部 ─────────────────────────────────────────────

    def _load_messages(self, session_id: str) -> List[ChatMessage]:
        rows = self._conn.execute(
            "SELECT role, content, tool_calls, tool_call_id, name,"
            " reasoning, has_reasoning FROM messages"
            " WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        return [_row_to_message(r) for r in rows]

# Copyright (c) 2026 xingluosama121, MIT Licensed
"""会话管理协议：Agent 的「记忆」抽象。

会话管理者负责对话历史的持久化与检索。内置实现为内存存储，
P2 阶段将提供 SQLite 实现；也可由开发者实现文件、数据库、云端同步等任意后端。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable

from norpagent.protocols.model import ChatMessage


@dataclass
class Session:
    """一个会话。``messages`` 为完整历史（含工具消息）。"""

    id: str
    title: str = ""
    created_at: float = 0.0
    messages: List[ChatMessage] = field(default_factory=list)


@runtime_checkable
class SessionManager(Protocol):
    """会话管理者接口。所有方法应当是线程安全的。"""

    def create_session(self, title: str = "") -> Session:
        """新建会话并返回。"""
        ...

    def get_session(self, session_id: str) -> Optional[Session]:
        """按 id 取会话，不存在返回 None。"""
        ...

    def append_message(self, session_id: str, message: ChatMessage) -> bool:
        """向会话追加一条消息，返回是否成功。"""
        ...

    def history(self, session_id: str) -> List[ChatMessage]:
        """返回会话全部历史（副本），会话不存在返回空列表。"""
        ...

    def list_sessions(self) -> List[Session]:
        """列出全部会话（可按创建时间倒序）。"""
        ...

    def delete_session(self, session_id: str) -> bool:
        """删除会话，返回是否成功。"""
        ...

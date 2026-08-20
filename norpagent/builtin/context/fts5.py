# Copyright (c) 2026 xingluosama121, MIT Licensed
"""FTS5 上下文存储组件：超长上下文精确检索器（零第三方依赖）。

迁移自现有应用的 context_index.FTS5Retriever，特性保持一致：

- SQLite FTS5 全文索引，支持中英混合分词（CJK 单字 + 双字 + 英文单词）；
- BM25 相关性排序（FTS5 bm25() 排序函数）；
- 持久化：默认库文件 ``~/.norpagent/context.db``，进程重启不丢；
- 线程安全：所有公开方法由 RLock 串行化。

通过注册表接入：::

    registry.register_component("context_store", "fts5",
                                lambda: FTS5ContextStore(path=...))

预设声明 ``components={"context_store": "fts5"}`` 后，
工具可通过 ``ctx.context_store`` 访问（见 tools/context_tools.py）。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

# ── 分词器（与现有应用一致：CJK 单字 + 双字 + ASCII 单词）────────

_RE_CHINESE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')


def _tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if _RE_CHINESE.match(ch):
            tokens.append(ch)
            if i + 1 < n and _RE_CHINESE.match(text[i + 1]):
                tokens.append(ch + text[i + 1])
            i += 1
        elif ch.isalnum() or ch == '_':
            start = i
            while i < n and (text[i].isalnum() or text[i] == '_'):
                i += 1
            tokens.append(text[start:i].lower())
        else:
            i += 1
    return tokens


def _tokenize_for_query(text: str) -> List[str]:
    """查询去重分词（查询词重复无意义）。"""
    seen: Set[str] = set()
    result: List[str] = []
    for tok in _tokenize(text):
        if tok not in seen:
            seen.add(tok)
            result.append(tok)
    return result


def _fts5_phrase(tokens: List[str]) -> str:
    """把查询 token 串成 FTS5 短语（带前缀匹配的 OR 短语组）。"""
    terms: List[str] = []
    for tok in tokens:
        if len(tok) == 1:
            terms.append(f'"{tok}"')
        else:
            terms.append(f'"{tok}"*')
    return " OR ".join(terms)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL DEFAULT 'manual',
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    tokenized TEXT NOT NULL DEFAULT '',
    indexed_at REAL NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts_idx USING fts5(
    tokenized,
    source UNINDEXED,
    title UNINDEXED,
    tokenize='unicode61 remove_diacritics 2',
    content='documents',
    content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON documents BEGIN
    INSERT INTO fts_idx(rowid, tokenized, source, title)
    VALUES (new.id, new.tokenized, new.source, new.title);
END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON documents BEGIN
    INSERT INTO fts_idx(fts_idx, rowid, tokenized, source, title)
    VALUES ('delete', old.id, old.tokenized, old.source, old.title);
END;
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON documents BEGIN
    INSERT INTO fts_idx(fts_idx, rowid, tokenized, source, title)
    VALUES ('delete', old.id, old.tokenized, old.source, old.title);
    INSERT INTO fts_idx(rowid, tokenized, source, title)
    VALUES (new.id, new.tokenized, new.source, new.title);
END;
"""


class FTS5ContextStore:
    """SQLite FTS5 上下文存储。

    条目 = {source, title, text, metadata}；按 BM25 相关度检索。
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.path.join(
            os.path.expanduser("~"), ".norpagent", "context.db"
        )
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ── 写入 ──────────────────────────────────────────────

    def add(
        self,
        text: str,
        source: str = "manual",
        title: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """写入一条上下文，返回条目 id。"""
        text = text or ""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO documents (source, title, text, tokenized, indexed_at, metadata)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    source,
                    title or "",
                    text,
                    " ".join(_tokenize(text)),
                    time.time(),
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update(self, doc_id: int, text: Optional[str] = None,
               title: Optional[str] = None,
               metadata: Optional[Dict[str, Any]] = None) -> bool:
        """更新一条已存在的上下文（保留未提供的字段）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
            if row is None:
                return False
            new_text = row["text"] if text is None else text
            new_title = row["title"] if title is None else title
            new_meta = row["metadata"] if metadata is None else json.dumps(
                metadata, ensure_ascii=False
            )
            self._conn.execute(
                "UPDATE documents SET text=?, title=?, metadata=?,"
                " tokenized=?, indexed_at=? WHERE id=?",
                (
                    new_text, new_title, new_meta,
                    " ".join(_tokenize(new_text)), time.time(), doc_id,
                ),
            )
            self._conn.commit()
            return True

    # ── 检索 ──────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 5,
        source: Optional[str] = None,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """BM25 相关度检索。

        返回 [{id, source, title, text, metadata, score}]，
        按相关度降序。``min_score`` 为 BM25 分数下限（0 返回全部命中）。
        """
        tokens = _tokenize_for_query(query)
        if not tokens:
            return []
        top_k = max(1, min(int(top_k), 50))
        match = _fts5_phrase(tokens)
        extra = ""
        args: List[Any] = [match]
        if source:
            extra = " AND source = ?"
            args.append(source)
        sql = (
            "SELECT d.id, d.source, d.title, d.text, d.metadata,"
            "       bm25(fts_idx) AS score"
            " FROM fts_idx JOIN documents d ON d.id = fts_idx.rowid"
            f" WHERE fts_idx MATCH ?{extra}"
            " ORDER BY score ASC LIMIT ?"
        )
        args.append(top_k)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        results: List[Dict[str, Any]] = []
        for r in rows:
            score = float(r["score"])
            # FTS5 bm25 返回负值（越小越相关）；转成便于阅读的正分数
            relevance = -score
            if min_score > 0 and relevance < min_score:
                continue
            try:
                metadata = json.loads(r["metadata"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            results.append({
                "id": r["id"],
                "source": r["source"],
                "title": r["title"],
                "text": r["text"],
                "metadata": metadata,
                "score": round(relevance, 3),
            })
        return results

    # ── 管理 ──────────────────────────────────────────────

    def get(self, doc_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        return {
            "id": row["id"], "source": row["source"], "title": row["title"],
            "text": row["text"], "metadata": metadata,
            "indexed_at": row["indexed_at"],
        }

    def list(
        self,
        source: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """按写入顺序列出条目（新→旧）。"""
        sql = "SELECT id, source, title, text, metadata, indexed_at FROM documents"
        args: List[Any] = []
        if source:
            sql += " WHERE source = ?"
            args.append(source)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        args += [max(0, min(int(limit), 200)), max(0, int(offset))]
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                metadata = json.loads(r["metadata"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            out.append({
                "id": r["id"], "source": r["source"], "title": r["title"],
                "text": r["text"], "metadata": metadata,
                "indexed_at": r["indexed_at"],
            })
        return out

    def delete(self, doc_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def clear(self, source: Optional[str] = None) -> int:
        """清空上下文（可按 source 过滤），返回删除条数。"""
        with self._lock:
            if source:
                cur = self._conn.execute(
                    "DELETE FROM documents WHERE source = ?", (source,)
                )
            else:
                cur = self._conn.execute("DELETE FROM documents")
            self._conn.commit()
            return cur.rowcount

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            count = self._conn.execute(
                "SELECT COUNT(*) AS n FROM documents"
            ).fetchone()["n"]
            sources: Dict[str, int] = {}
            for r in self._conn.execute(
                "SELECT source, COUNT(*) AS n FROM documents GROUP BY source"
            ).fetchall():
                sources[r["source"]] = r["n"]
        return {"total": count, "sources": sources, "path": self.path}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

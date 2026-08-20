# ──────────────────────────────────────────────────────────────
# Native Tool: Context Index (超长上下文精确检索器)
# 从 official_plugins/context_retriever.py 迁移而来
# ──────────────────────────────────────────────────────────────

import json
import math
import os
import re
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

# ═══════════════════════════════════════════════════════════════
#  分词器
# ═══════════════════════════════════════════════════════════════

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
    seen: Set[str] = set()
    result: List[str] = []
    for tok in _tokenize(text):
        if tok not in seen:
            seen.add(tok)
            result.append(tok)
    return result


def _tokenize_to_fts5_format(text: str) -> str:
    tokens = _tokenize(text)
    return " ".join(tokens)


# ═══════════════════════════════════════════════════════════════
#  FTS5Retriever — SQLite FTS5 检索引擎
# ═══════════════════════════════════════════════════════════════

_DB_SCHEMA_VERSION = 2
_BATCH_COMMIT_SIZE = 50


class FTS5Retriever:
    """基于 SQLite FTS5 的超大规模检索引擎。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._batch_buffer: List[Tuple[str, str, str, str, int, str]] = []
        self._doc_count: int = 0
        self._total_tokens: int = 0
        self._avgdl: float = 0.0
        self._df_cache: Dict[str, int] = {}
        self._stats_dirty: bool = True
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")
        conn.execute("PRAGMA mmap_size=268435456")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL DEFAULT 'manual',
                    title TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    tokenized TEXT NOT NULL DEFAULT '',
                    indexed_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    chunk_index INTEGER NOT NULL DEFAULT 0,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_idx USING fts5(
                    tokenized,
                    source UNINDEXED,
                    title UNINDEXED,
                    tokenize='unicode61 remove_diacritics 2',
                    content='',
                    content_rowid='id'
                );
                CREATE TABLE IF NOT EXISTS stats (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                );
                CREATE INDEX IF NOT EXISTS idx_docs_source ON documents(source);
                CREATE INDEX IF NOT EXISTS idx_docs_source_chunk
                    ON documents(source, chunk_index);
            """)

            row = conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (_DB_SCHEMA_VERSION,)
                )
            conn.commit()
        finally:
            conn.close()

    def add(self, text: str, source: str = "manual",
            title: str = "", metadata: Optional[Dict] = None,
            chunk_index: int = 0) -> int:
        tokenized = _tokenize_to_fts5_format(text)
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)

        with self._lock:
            self._batch_buffer.append(
                (tokenized, source, title, text, chunk_index, metadata_json)
            )
            self._stats_dirty = True

            if len(self._batch_buffer) >= _BATCH_COMMIT_SIZE:
                self._flush_batch()

        return len(self._batch_buffer)

    def flush(self):
        with self._lock:
            if self._batch_buffer:
                self._flush_batch()

    def _flush_batch(self) -> int:
        if not self._batch_buffer:
            return -1

        conn = self._get_conn()
        last_id = -1
        try:
            with conn:
                for tokenized, source, title, text, chunk_index, metadata_json in self._batch_buffer:
                    cur = conn.execute(
                        """INSERT INTO documents
                           (tokenized, source, title, text, chunk_index, metadata)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (tokenized, source, title, text, chunk_index, metadata_json)
                    )
                    doc_id = cur.lastrowid
                    last_id = doc_id
                    conn.execute(
                        """INSERT INTO fts_idx(rowid, tokenized, source, title)
                           VALUES (?, ?, ?, ?)""",
                        (doc_id, tokenized, source, title)
                    )
        finally:
            conn.close()

        self._batch_buffer.clear()
        return last_id

    def remove_by_source(self, source: str) -> int:
        self.flush()
        conn = self._get_conn()
        removed = 0
        try:
            with conn:
                ids = [row[0] for row in conn.execute(
                    "SELECT id FROM documents WHERE source = ?", (source,)
                )]
                if not ids:
                    return 0
                removed = len(ids)
                for doc_id in ids:
                    conn.execute(
                        "INSERT INTO fts_idx(fts_idx, rowid, tokenized, source, title) "
                        "VALUES ('delete', ?, '', '', '')",
                        (doc_id,)
                    )
                conn.execute(
                    "DELETE FROM documents WHERE source = ?", (source,)
                )
        finally:
            conn.close()
        self._stats_dirty = True
        return removed

    def clear_all(self):
        self.flush()
        conn = self._get_conn()
        try:
            with conn:
                conn.execute("DELETE FROM documents")
                conn.execute("DROP TABLE IF EXISTS fts_idx")
                conn.execute("""
                    CREATE VIRTUAL TABLE fts_idx USING fts5(
                        tokenized,
                        source UNINDEXED,
                        title UNINDEXED,
                        tokenize='unicode61 remove_diacritics 2',
                        content='',
                        content_rowid='id'
                    )
                """)
                conn.execute(
                    "UPDATE sqlite_sequence SET seq=0 WHERE name='documents'"
                )
        finally:
            conn.close()
        self._stats_dirty = True

    def search(self, query: str, top_k: int = 5,
               min_score: float = 0.1,
               source_filter: str = "",
               expand_context: bool = True) -> List[Dict[str, Any]]:
        self.flush()
        query_tokens = _tokenize_for_query(query)
        if not query_tokens:
            return []

        candidates = self._fts5_retrieve(query, query_tokens, source_filter, top_k)
        if not candidates:
            return []

        conn = self._get_conn()
        try:
            ranked = self._rerank_bm25(conn, query, query_tokens, candidates, min_score)
            ranked.sort(key=lambda x: x[0], reverse=True)
            top = ranked[:top_k]
            results = self._build_results(conn, top, query_tokens, expand_context)
        finally:
            conn.close()

        return results

    def _fts5_retrieve(self, query: str, query_tokens: List[str],
                       source_filter: str, top_k: int
                       ) -> List[Tuple[int, str, str, str, float]]:
        conn = self._get_conn()
        try:
            fts5_query = " AND ".join(f'"{t}"' for t in query_tokens)
            candidate_limit = top_k * 20

            where_clause = ""
            params: tuple = ()
            if source_filter:
                where_clause = "AND d.source = ?"
                params = (source_filter,)

            sql = f"""
                SELECT d.id, d.text, d.source, d.title,
                       rank AS fts5_score
                FROM fts_idx f
                JOIN documents d ON f.rowid = d.id
                WHERE fts_idx MATCH ?
                {where_clause}
                ORDER BY rank
                LIMIT ?
            """
            rows = conn.execute(
                sql, (fts5_query,) + params + (candidate_limit,)
            ).fetchall()

            if not rows and len(query_tokens) > 1:
                fts5_or_query = " OR ".join(f'"{t}"' for t in query_tokens)
                rows = conn.execute(
                    sql, (fts5_or_query,) + params + (candidate_limit,)
                ).fetchall()

            return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
        finally:
            conn.close()

    def _rerank_bm25(self, conn: sqlite3.Connection,
                     query: str, query_tokens: List[str],
                     candidates: List[Tuple[int, str, str, str, float]],
                     min_score: float
                     ) -> List[Tuple[float, int, str, str, str]]:
        self._ensure_stats(conn)
        ranked: List[Tuple[float, int, str, str, str]] = []

        for doc_id, text, source, title, _fts5_score in candidates:
            row = conn.execute(
                "SELECT tokenized FROM documents WHERE id = ?",
                (doc_id,)
            ).fetchone()
            if not row:
                continue

            tokenized = row[0]
            doc_tokens = tokenized.split() if tokenized else []
            bm25 = self._bm25_score(query_tokens, doc_tokens)
            phrase_bonus = self._phrase_match_bonus(query, text)
            final_score = bm25 + phrase_bonus

            if final_score >= min_score:
                ranked.append((final_score, doc_id, text, source, title))

        return ranked

    def _build_results(self, conn: sqlite3.Connection,
                       top: List[Tuple[float, int, str, str, str]],
                       query_tokens: List[str],
                       expand_context: bool
                       ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        for score, doc_id, text, source, title in top:
            row = conn.execute(
                "SELECT indexed_at, chunk_index FROM documents WHERE id = ?",
                (doc_id,)
            ).fetchone()
            indexed_at = row[0] if row else ""
            chunk_index = row[1] if row else 0

            result: Dict[str, Any] = {
                "score": round(score, 4),
                "doc_id": doc_id,
                "source": source,
                "title": title,
                "text": text,
                "indexed_at": indexed_at,
                "match_positions": self._find_match_positions(query_tokens, text),
            }

            if expand_context:
                if chunk_index > 0:
                    prev_row = conn.execute(
                        """SELECT text FROM documents
                           WHERE source = ? AND chunk_index = ?
                           LIMIT 1""",
                        (source, chunk_index - 1)
                    ).fetchone()
                    if prev_row:
                        result["context_before"] = prev_row[0][-300:]

                next_row = conn.execute(
                    """SELECT text FROM documents
                       WHERE source = ? AND chunk_index = ?
                       LIMIT 1""",
                    (source, chunk_index + 1)
                ).fetchone()
                if next_row:
                    result["context_after"] = next_row[0][:300]

            results.append(result)

        return results

    def _ensure_stats(self, conn: sqlite3.Connection):
        if not self._stats_dirty:
            return

        with self._lock:
            if not self._stats_dirty:
                return

            row = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
            self._doc_count = row[0] if row else 0

            if self._doc_count > 0:
                row = conn.execute(
                    "SELECT SUM(LENGTH(tokenized) - LENGTH(REPLACE(tokenized, ' ', '')) + 1) "
                    "FROM documents WHERE tokenized != ''"
                ).fetchone()
                self._total_tokens = row[0] if row[0] else 0
                self._avgdl = self._total_tokens / self._doc_count
            else:
                self._total_tokens = 0
                self._avgdl = 0.0

            if self._doc_count < 50000:
                self._df_cache.clear()
                rows = conn.execute(
                    "SELECT tokenized FROM documents WHERE tokenized != ''"
                ).fetchall()
                term_docs: Dict[str, Set[int]] = defaultdict(set)
                for i, (tokenized,) in enumerate(rows):
                    for term in tokenized.split():
                        term_docs[term].add(i)
                self._df_cache = {t: len(s) for t, s in term_docs.items()}

            self._stats_dirty = False

    def _bm25_score(self, query_terms: List[str],
                    doc_tokens: List[str],
                    k1: float = 1.5, b: float = 0.75) -> float:
        score = 0.0
        dl = len(doc_tokens)
        N = max(self._doc_count, 1)

        tf: Dict[str, int] = {}
        for t in doc_tokens:
            tf[t] = tf.get(t, 0) + 1

        for term in query_terms:
            df = self._df_cache.get(term)
            if df is None:
                df = self._query_df(term)
                self._df_cache[term] = df

            if df == 0:
                continue

            idf = math.log((N - df + 0.5) / (df + 0.5) + 1.0)

            f = tf.get(term, 0)
            if f == 0:
                continue
            numerator = f * (k1 + 1)
            denominator = f + k1 * (1 - b + b * dl / max(self._avgdl, 1))
            score += idf * numerator / denominator

        return score

    def _query_df(self, term: str) -> int:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM fts_idx WHERE fts_idx MATCH ?",
                (f'"{term}"',)
            ).fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            conn.close()

    @staticmethod
    def _phrase_match_bonus(query: str, text: str) -> float:
        bonus = 0.0
        q_lower = query.lower()
        t_lower = text.lower()

        if q_lower in t_lower:
            bonus += 0.5

        if len(query) >= 4:
            matched_ngrams = 0
            for i in range(len(query) - 1):
                bigram = query[i:i + 2]
                if (len(bigram.strip()) == 2
                        and not bigram[0].isspace()
                        and not bigram[1].isspace()
                        and bigram in t_lower):
                    matched_ngrams += 1
            bonus += min(matched_ngrams * 0.1, 0.3)

        return bonus

    @staticmethod
    def _find_match_positions(query_terms: List[str],
                              text: str) -> List[int]:
        positions: List[int] = []
        lower = text.lower()
        for term in query_terms:
            pos = lower.find(term.lower())
            if pos >= 0:
                positions.append(pos)
        return sorted(set(positions))

    def stats(self) -> Dict[str, Any]:
        self.flush()
        conn = self._get_conn()
        try:
            doc_count = conn.execute(
                "SELECT COUNT(*) FROM documents"
            ).fetchone()[0]
            total_chars = conn.execute(
                "SELECT COALESCE(SUM(LENGTH(text)), 0) FROM documents"
            ).fetchone()[0]
            source_rows = conn.execute(
                "SELECT source, COUNT(*) as cnt FROM documents "
                "GROUP BY source ORDER BY cnt DESC"
            ).fetchall()
            sources = {r[0]: r[1] for r in source_rows}
            vocab_size = doc_count * 20 if doc_count > 0 else 0

            return {
                "total_documents": doc_count,
                "total_characters": total_chars,
                "vocabulary_size": vocab_size,
                "average_document_length": round(self._avgdl, 1),
                "bm25_params": {"k1": 1.5, "b": 0.75},
                "sources": sources,
                "engine": "SQLite FTS5",
                "db_path": self._db_path,
            }
        finally:
            conn.close()

    def close(self):
        self.flush()


# ═══════════════════════════════════════════════════════════════
#  文本分块器
# ═══════════════════════════════════════════════════════════════

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> List[str]:
    if not text or len(text) <= chunk_size:
        return [text] if text else []

    chunks: List[str] = []
    start = 0
    text_len = len(text)

    separators = [
        ('\n\n', 2), ('\n', 1),
        ('。', 1), ('！', 1), ('？', 1), ('；', 1),
        ('. ', 2), ('! ', 2), ('? ', 2), ('; ', 2),
        ('，', 1), (', ', 2), (' ', 1),
    ]

    while start < text_len:
        end = min(start + chunk_size, text_len)

        if end >= text_len:
            chunks.append(text[start:])
            break

        chunk_slice = text[start:end]
        search_start = max(end - overlap - start, 0)
        best_break = end - start

        for sep, sep_len in separators:
            pos = chunk_slice.rfind(sep, search_start)
            if pos >= 0:
                best_break = pos + sep_len
                break

        actual_end = start + best_break
        chunks.append(text[start:actual_end])
        start = actual_end - overlap
        if start <= 0 or start >= text_len:
            break

    return chunks


# ═══════════════════════════════════════════════════════════════
#  全局索引实例
# ═══════════════════════════════════════════════════════════════

_global_retriever: Optional[FTS5Retriever] = None
_global_retriever_lock = threading.Lock()


def get_context_index(app_dir: str = "", project_root: str = "") -> FTS5Retriever:
    global _global_retriever
    if _global_retriever is None:
        with _global_retriever_lock:
            if _global_retriever is None:
                db_dir = os.path.join(app_dir, "indexes") if app_dir else os.path.join(project_root, ".indexes")
                os.makedirs(db_dir, exist_ok=True)
                db_path = os.path.join(db_dir, "context_index.db")
                _global_retriever = FTS5Retriever(db_path)
    return _global_retriever

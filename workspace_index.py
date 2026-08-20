# ──────────────────────────────────────────────────────────────
# Native Tool: Workspace Index (文件检索器)
# 从 official_plugins/file_searcher.py 迁移而来
# ──────────────────────────────────────────────────────────────

import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from fnmatch import fnmatch
from typing import Any, Dict, List, Optional, Set, Tuple

# ═══════════════════════════════════════════════════════════════
#  常量与工具函数
# ═══════════════════════════════════════════════════════════════

_DB_SCHEMA_VERSION = 1
_CHUNK_LINES = 256
_CHUNK_OVERLAP = 2
_BATCH_COMMIT_SIZE = 50

_DEFAULT_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", "output", "indexes",
    ".mypy_cache", ".pytest_cache", ".tox", ".next", ".nuxt",
    "target", "bin", "obj", ".svn", ".hg",
}

_MAX_LINE_LEN = 1_000_000

_RE_CHINESE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')
_RE_HAS_CJK = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')
_RE_ASCII_WORD = re.compile(r'[a-z0-9_]+')
_RE_SPLIT_CJK = re.compile(r'([\u4e00-\u9fff\u3400-\u4dbf]+)')


def _tokenize(text: str) -> List[str]:
    if not _RE_HAS_CJK.search(text):
        return _RE_ASCII_WORD.findall(text.lower())
    tokens: List[str] = []
    for seg in _RE_SPLIT_CJK.split(text):
        if not seg:
            continue
        if _RE_CHINESE.match(seg):
            i = 0
            n = len(seg)
            while i < n:
                ch = seg[i]
                tokens.append(ch)
                if i + 1 < n and _RE_CHINESE.match(seg[i + 1]):
                    tokens.append(ch + seg[i + 1])
                i += 1
        else:
            tokens.extend(_RE_ASCII_WORD.findall(seg.lower()))
    return tokens


def _tokenize_for_query(text: str) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for tok in _tokenize(text):
        if tok not in seen:
            seen.add(tok)
            result.append(tok)
    return result


def _detect_encoding(path: str) -> str:
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError:
        return "utf-8"
    if head.startswith(b'\xef\xbb\xbf'):
        return "utf-8-sig"
    for enc in ("utf-8", "gbk"):
        try:
            head.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def _is_binary(head: bytes) -> bool:
    return b'\x00' in head


def _iter_lines(path: str, encoding: str):
    with open(path, "r", encoding=encoding, errors="replace",
              newline=None) as f:
        for line in f:
            yield line


def _norm_path(path: str, base: str) -> str:
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(base, path))


# ═══════════════════════════════════════════════════════════════
#  WorkspaceIndex — SQLite FTS5 工作区文件索引引擎
# ═══════════════════════════════════════════════════════════════

class WorkspaceIndex:
    """工作区文件索引引擎（磁盘存储，支持 1GB+ 文本）。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._batch_buffer: List[Tuple[int, int, int, str, str]] = []
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-32000")
        conn.execute("PRAGMA mmap_size=134217728")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL DEFAULT '',
                    ext TEXT NOT NULL DEFAULT '',
                    size INTEGER NOT NULL DEFAULT 0,
                    mtime REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'indexed',
                    indexed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL DEFAULT 0,
                    line_start INTEGER NOT NULL DEFAULT 1,
                    text TEXT NOT NULL,
                    tokenized TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_file
                    ON chunks(file_id, chunk_index);
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_files USING fts5(
                    tokenized,
                    content='',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def get_meta(self, key: str, default: str = "") -> str:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else default
        finally:
            conn.close()

    def set_meta(self, key: str, value: str):
        conn = self._get_conn()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value)
                )
        finally:
            conn.close()

    def upsert_file(self, path: str, size: int, mtime: float,
                    status: str) -> int:
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        conn = self._get_conn()
        try:
            with conn:
                conn.execute(
                    """INSERT INTO files(path, name, ext, size, mtime, status, indexed_at)
                       VALUES(?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(path) DO UPDATE SET
                         name = excluded.name, ext = excluded.ext,
                         size = excluded.size, mtime = excluded.mtime,
                         status = excluded.status,
                         indexed_at = excluded.indexed_at""",
                    (path, name, ext, size, mtime, status,
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                )
                row = conn.execute(
                    "SELECT id FROM files WHERE path = ?", (path,)
                ).fetchone()
                return row[0]
        finally:
            conn.close()

    def get_file(self, path: str) -> Optional[sqlite3.Row]:
        conn = self._get_conn()
        try:
            return conn.execute(
                "SELECT * FROM files WHERE path = ?", (path,)
            ).fetchone()
        finally:
            conn.close()

    def remove_file(self, path: str) -> int:
        self.flush()
        conn = self._get_conn()
        removed_chunks = 0
        try:
            with conn:
                row = conn.execute(
                    "SELECT id FROM files WHERE path = ?", (path,)
                ).fetchone()
                if row is None:
                    return 0
                file_id = row[0]
                chunk_ids = [
                    r[0] for r in conn.execute(
                        "SELECT id FROM chunks WHERE file_id = ?", (file_id,)
                    )
                ]
                for cid in chunk_ids:
                    conn.execute(
                        "INSERT INTO fts_files(fts_files, rowid, tokenized) "
                        "VALUES('delete', ?, '')", (cid,)
                    )
                conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
                conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
                removed_chunks = len(chunk_ids)
        finally:
            conn.close()
        return removed_chunks

    def remove_by_dir(self, dir_path: str) -> int:
        self.flush()
        conn = self._get_conn()
        removed_files = 0
        try:
            paths = [r[0] for r in conn.execute(
                "SELECT path FROM files WHERE path LIKE ?",
                (dir_path.rstrip("\\/") + os.sep + "%",)
            )]
        finally:
            conn.close()
        for p in paths:
            self.remove_file(p)
            removed_files += 1
        return removed_files

    def add_chunk(self, file_id: int, chunk_index: int,
                  line_start: int, text: str):
        tokenized = " ".join(_tokenize(text))
        with self._lock:
            self._batch_buffer.append(
                (file_id, chunk_index, line_start, text, tokenized)
            )
            if len(self._batch_buffer) >= _BATCH_COMMIT_SIZE:
                self._flush_batch()

    def flush(self):
        with self._lock:
            if self._batch_buffer:
                self._flush_batch()

    def _flush_batch(self):
        if not self._batch_buffer:
            return
        conn = self._get_conn()
        try:
            with conn:
                for file_id, chunk_index, line_start, text, tokenized in self._batch_buffer:
                    cur = conn.execute(
                        """INSERT INTO chunks
                           (file_id, chunk_index, line_start, text, tokenized)
                           VALUES (?, ?, ?, ?, ?)""",
                        (file_id, chunk_index, line_start, text, tokenized)
                    )
                    conn.execute(
                        "INSERT INTO fts_files(rowid, tokenized) VALUES(?, ?)",
                        (cur.lastrowid, tokenized)
                    )
        finally:
            conn.close()
        self._batch_buffer.clear()

    def reset_file_chunks(self, file_id: int) -> int:
        self.flush()
        conn = self._get_conn()
        old_ids: List[int] = []
        try:
            with conn:
                old_ids = [r[0] for r in conn.execute(
                    "SELECT id FROM chunks WHERE file_id = ?", (file_id,)
                )]
                for cid in old_ids:
                    conn.execute(
                        "INSERT INTO fts_files(fts_files, rowid, tokenized) "
                        "VALUES('delete', ?, '')", (cid,)
                    )
                conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
        finally:
            conn.close()
        return len(old_ids)

    def search(self, query: str, top_k: int = 10,
               path_filter: str = "", file_pattern: str = "",
               case_sensitive: bool = False,
               exact_phrase: bool = True,
               max_lines_per_file: int = 5,
               line_context: int = 1) -> Dict[str, Any]:
        self.flush()
        query_tokens = _tokenize_for_query(query)
        if not query_tokens:
            return {"matches": [], "file_hits": {}, "total_chunks_scanned": 0,
                    "total_lines_found": 0}

        needle = query if case_sensitive else query.lower()
        regex = None
        if not exact_phrase:
            regex = re.compile(
                "|".join(re.escape(t) for t in query_tokens),
                re.IGNORECASE if not case_sensitive else 0
            )

        conn = self._get_conn()
        try:
            candidates = self._fts5_candidates(
                conn, query_tokens, exact_phrase,
                path_filter, top_k * 20
            )
            if not candidates:
                return {"matches": [], "file_hits": {},
                        "total_chunks_scanned": 0, "total_lines_found": 0}

            matches: List[Dict[str, Any]] = []
            file_hits: Dict[str, int] = {}
            seen_files: Set[str] = set()

            for chunk_id, chunk_text, line_start, file_path, file_name in candidates:
                if file_pattern and not (
                    fnmatch(file_name, file_pattern)
                    or fnmatch(file_path, file_pattern)
                ):
                    continue
                if path_filter and not (
                    file_path == path_filter
                    or file_path.startswith(path_filter.rstrip("\\/") + os.sep)
                ):
                    continue

                lines = chunk_text.splitlines()
                for i, line in enumerate(lines):
                    if len(line) > _MAX_LINE_LEN:
                        line = line[:_MAX_LINE_LEN]
                    if regex:
                        hit = bool(regex.search(line))
                    else:
                        hit = (needle in (line.lower() if not case_sensitive else line))
                    if not hit:
                        continue

                    abs_line = line_start + i
                    before = lines[max(0, i - line_context):i]
                    after = lines[i + 1:i + 1 + line_context]

                    if file_path not in file_hits:
                        file_hits[file_path] = 0
                        seen_files.add(file_path)
                    if file_hits[file_path] >= max_lines_per_file:
                        continue
                    file_hits[file_path] += 1

                    matches.append({
                        "file": file_path,
                        "line": abs_line,
                        "text": line.strip(),
                        "context_before": before,
                        "context_after": after,
                    })
                    if len(matches) >= top_k * max_lines_per_file:
                        break
                if len(matches) >= top_k * max_lines_per_file:
                    break
        finally:
            conn.close()

        matches.sort(key=lambda m: (m["file"], m["line"]))
        return {
            "matches": matches,
            "file_hits": file_hits,
            "total_chunks_scanned": len(candidates),
            "total_lines_found": sum(file_hits.values()),
        }

    def _fts5_candidates(self, conn: sqlite3.Connection,
                         query_tokens: List[str], exact_phrase: bool,
                         path_filter: str, limit: int
                         ) -> List[Tuple[int, str, int, str, str]]:
        sql = """
            SELECT c.id, c.text, c.line_start, f.path, f.name
            FROM fts_files ff
            JOIN chunks c ON ff.rowid = c.id
            JOIN files f ON c.file_id = f.id
            WHERE fts_files MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        rows: List[sqlite3.Row] = []

        phrase = " ".join(f'"{t}"' for t in query_tokens)
        if exact_phrase and len(query_tokens) >= 1:
            rows = conn.execute(sql, (phrase, limit)).fetchall()

        if not rows and len(query_tokens) > 1:
            and_q = " AND ".join(f'"{t}"' for t in query_tokens)
            rows = conn.execute(sql, (and_q, limit)).fetchall()
        if not rows and len(query_tokens) > 1:
            or_q = " OR ".join(f'"{t}"' for t in query_tokens)
            rows = conn.execute(sql, (or_q, limit)).fetchall()

        return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    def find_by_name(self, pattern: str, path_filter: str = "",
                     top_k: int = 30) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT path, name, ext, size, status, indexed_at "
                "FROM files ORDER BY name"
            ).fetchall()
        finally:
            conn.close()

        pat = pattern.lower()
        results: List[Dict[str, Any]] = []
        for r in rows:
            path = r[0]
            if path_filter and not (
                path == path_filter
                or path.startswith(path_filter.rstrip("\\/") + os.sep)
            ):
                continue
            if fnmatch(path.lower(), pat) or fnmatch(r[1].lower(), pat):
                results.append({
                    "path": path, "name": r[1], "ext": r[2],
                    "size": r[3], "status": r[4], "indexed_at": r[5],
                })
                if len(results) >= top_k:
                    break
        return results

    def stats(self) -> Dict[str, Any]:
        self.flush()
        conn = self._get_conn()
        try:
            total_files = conn.execute(
                "SELECT COUNT(*) FROM files"
            ).fetchone()[0]
            status_rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM files "
                "GROUP BY status ORDER BY cnt DESC"
            ).fetchall()
            statuses = {r[0]: r[1] for r in status_rows}
            chunk_count = conn.execute(
                "SELECT COUNT(*) FROM chunks"
            ).fetchone()[0]
            total_chars = conn.execute(
                "SELECT COALESCE(SUM(LENGTH(text)), 0) FROM chunks"
            ).fetchone()[0]
            ext_rows = conn.execute(
                """SELECT ext, COUNT(*) as cnt FROM files
                   WHERE status = 'indexed' AND ext != ''
                   GROUP BY ext ORDER BY cnt DESC LIMIT 15"""
            ).fetchall()
            extensions = {r[0]: r[1] for r in ext_rows}
            last_scan = self.get_meta("last_scan", "")
            root = self.get_meta("index_root", "")
            return {
                "index_root": root,
                "total_files": total_files,
                "status_distribution": statuses,
                "total_chunks": chunk_count,
                "total_characters": total_chars,
                "extensions": extensions,
                "last_scan": last_scan,
                "db_path": self._db_path,
            }
        finally:
            conn.close()

    def clear_all(self):
        self.flush()
        conn = self._get_conn()
        try:
            with conn:
                conn.execute("DELETE FROM files")
                conn.execute("DELETE FROM chunks")
                conn.execute("DROP TABLE IF EXISTS fts_files")
                conn.execute("""
                    CREATE VIRTUAL TABLE fts_files USING fts5(
                        tokenized,
                        content='',
                        content_rowid='id',
                        tokenize='unicode61 remove_diacritics 2'
                    )
                """)
                conn.execute(
                    "UPDATE sqlite_sequence SET seq=0 WHERE name='files'"
                )
                conn.execute(
                    "UPDATE sqlite_sequence SET seq=0 WHERE name='chunks'"
                )
                conn.execute("DELETE FROM meta WHERE key != 'index_root'")
        finally:
            conn.close()

    def close(self):
        self.flush()


# ═══════════════════════════════════════════════════════════════
#  大文件流式检索（无索引，1GB+）
# ═══════════════════════════════════════════════════════════════

def search_large_file_stream(path: str, query: str, *,
                             regex: bool = False,
                             case_sensitive: bool = False,
                             line_context: int = 2,
                             max_matches: int = 30,
                             encoding: str = "") -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {"error": f"文件不存在: {path}"}

    file_size = os.path.getsize(path)
    enc = encoding or _detect_encoding(path)

    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            matcher = re.compile(query, flags)
        except re.error as e:
            return {"error": f"正则表达式无效: {e}"}
    else:
        matcher = None

    needle = query if case_sensitive else query.lower()

    start = time.time()
    hits: List[Dict[str, Any]] = []
    total_lines = 0
    ring: List[str] = []
    long_line_warned = False

    for raw_line in _iter_lines(path, enc):
        total_lines += 1
        line = raw_line.rstrip("\r\n")
        is_hit = False
        if matcher is not None:
            is_hit = bool(matcher.search(line))
        else:
            hay = line if case_sensitive else line.lower()
            is_hit = needle in hay

        if is_hit:
            truncated = len(line) > 300
            display = line[:300] + ("…" if truncated else "")
            hits.append({
                "line": total_lines,
                "text": display,
                "context_before": list(ring),
            })
            if len(hits) >= max_matches:
                break
        elif len(line) > _MAX_LINE_LEN and not long_line_warned:
            long_line_warned = True

        ring.append(line)
        if len(ring) > line_context:
            ring.pop(0)

    elapsed = time.time() - start

    return {
        "path": path,
        "file_size": file_size,
        "encoding": enc,
        "total_lines": total_lines,
        "matches": hits,
        "elapsed_seconds": round(elapsed, 2),
        "stopped_early": len(hits) >= max_matches,
        "long_line_warned": long_line_warned,
    }


# ═══════════════════════════════════════════════════════════════
#  工作区扫描与增量索引
# ═══════════════════════════════════════════════════════════════

def scan_and_index(index: WorkspaceIndex, root: str, *,
                   include_patterns: List[str],
                   exclude_dirs: Set[str],
                   max_file_mb: float,
                   force: bool) -> Dict[str, Any]:
    root = os.path.normpath(root)
    if not os.path.isdir(root):
        return {"error": f"目录不存在: {root}"}

    start = time.time()
    scanned = 0
    indexed = 0
    unchanged = 0
    skipped = 0
    total_chars = 0
    max_bytes = int(max_file_mb * 1024 * 1024)

    patterns = [p.lower() for p in (include_patterns or [])]

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            scanned += 1

            if patterns and not any(
                fnmatch(fname.lower(), p) for p in patterns
            ):
                skipped += 1
                continue

            try:
                st = os.stat(full)
            except OSError:
                skipped += 1
                continue

            existing = index.get_file(full)
            if (not force and existing is not None
                    and abs(existing["mtime"] - st.st_mtime) < 0.001
                    and existing["size"] == st.st_size):
                unchanged += 1
                continue

            try:
                with open(full, "rb") as f:
                    head = f.read(8192)
            except OSError:
                skipped += 1
                continue

            if _is_binary(head):
                index.upsert_file(full, st.st_size, st.st_mtime, "binary")
                skipped += 1
                continue

            if st.st_size > max_bytes:
                index.upsert_file(full, st.st_size, st.st_mtime, "too_large")
                skipped += 1
                continue

            enc = _detect_encoding(full)
            file_id = index.upsert_file(full, st.st_size, st.st_mtime, "indexed")
            index.reset_file_chunks(file_id)

            chunk_index = 0
            pending: List[Tuple[int, str]] = []
            file_chars = 0
            try:
                for line_no, line in enumerate(_iter_lines(full, enc), 1):
                    pending.append((line_no, line))
                    if len(pending) >= _CHUNK_LINES + _CHUNK_OVERLAP:
                        chunk_lines = pending[:_CHUNK_LINES]
                        pending = pending[_CHUNK_LINES - _CHUNK_OVERLAP:]
                        chunk_text = "".join(t for _, t in chunk_lines)
                        index.add_chunk(
                            file_id, chunk_index, chunk_lines[0][0], chunk_text
                        )
                        file_chars += len(chunk_text)
                        chunk_index += 1
                if pending:
                    chunk_text = "".join(t for _, t in pending)
                    index.add_chunk(
                        file_id, chunk_index, pending[0][0], chunk_text
                    )
                    file_chars += len(chunk_text)
            except Exception:
                pass

            index.flush()
            indexed += 1
            total_chars += file_chars

    index.set_meta("last_scan", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    index.set_meta("index_root", root)
    elapsed = time.time() - start

    return {
        "root": root,
        "scanned": scanned,
        "indexed": indexed,
        "unchanged": unchanged,
        "skipped": skipped,
        "total_chars_indexed": total_chars,
        "elapsed_seconds": round(elapsed, 2),
    }


def _fmt_size(size: int) -> str:
    if size >= 1 << 30:
        return f"{size / (1 << 30):.2f} GB"
    if size >= 1 << 20:
        return f"{size / (1 << 20):.1f} MB"
    if size >= 1 << 10:
        return f"{size / (1 << 10):.0f} KB"
    return f"{size} B"


# ═══════════════════════════════════════════════════════════════
#  全局索引实例（跨任务持久化）
# ═══════════════════════════════════════════════════════════════

_global_index: Optional[WorkspaceIndex] = None
_global_index_lock = threading.Lock()


def get_workspace_index(app_dir: str = "", project_root: str = "") -> WorkspaceIndex:
    global _global_index
    if _global_index is None:
        with _global_index_lock:
            if _global_index is None:
                db_dir = os.path.join(app_dir, "indexes") if app_dir else os.path.join(project_root, ".indexes")
                os.makedirs(db_dir, exist_ok=True)
                db_path = os.path.join(db_dir, "workspace_index.db")
                _global_index = WorkspaceIndex(db_path)
    return _global_index

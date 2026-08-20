# Copyright (c) 2026 xingluosama121, MIT Licensed
"""基础项目管理组件：工作区元数据跟踪（零第三方依赖）。

- 每个工作区一份 ``.norpagent/project.json`` 元数据（名称、创建时间、
  最近活动、任务统计）；
- 提供工作区扫描（文件数 / 大小 / 最近修改）与 git 状态感知；
- 全部方法线程安全，持久层为普通 JSON 文件（写时原子替换）。

替换方式与一切组件相同：实现同一接口后
``registry.register_component("project_manager", "我的实现", factory)``。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_META_DIR = ".norpagent"
_META_FILE = "project.json"

# 扫描时跳过的目录（与现有应用 workspace_index 的默认排除一致）
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", "output", "indexes",
    ".mypy_cache", ".pytest_cache", ".tox", ".norpagent",
}


class BasicProjectManager:
    """工作区项目管理：元数据 + 扫描 + git 状态。"""

    def __init__(self, workspace_root: Optional[str] = None) -> None:
        self.workspace_root = os.path.abspath(workspace_root or os.getcwd())
        self._lock = threading.RLock()

    # ── 元数据 ────────────────────────────────────────────

    @property
    def meta_path(self) -> str:
        return os.path.join(self.workspace_root, _META_DIR, _META_FILE)

    def init(self, name: str = "", description: str = "") -> Dict[str, Any]:
        """初始化（或读取）项目元数据。幂等。"""
        with self._lock:
            meta = self.load_meta()
            if not meta:
                meta = {
                    "name": name or os.path.basename(self.workspace_root.rstrip(os.sep)),
                    "description": description,
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "tasks_total": 0,
                    "tasks_success": 0,
                }
                self.save_meta(meta)
            return meta

    def load_meta(self) -> Dict[str, Any]:
        with self._lock:
            try:
                with open(self.meta_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                return data if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError):
                return {}

    def save_meta(self, meta: Dict[str, Any]) -> None:
        with self._lock:
            meta["updated_at"] = time.time()
            path = self.meta_path
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(meta, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)

    def touch(self, task_id: str = "", success: bool = True) -> None:
        """记录一次任务活动（长周期任务协作时由运行时/工具调用）。"""
        with self._lock:
            meta = self.load_meta()
            if not meta:
                meta = self.init()
            meta["tasks_total"] = int(meta.get("tasks_total", 0)) + 1
            if success:
                meta["tasks_success"] = int(meta.get("tasks_success", 0)) + 1
            meta["last_task_id"] = task_id or ""
            self.save_meta(meta)

    # ── 扫描 ──────────────────────────────────────────────

    def scan(self, top_n: int = 10) -> Dict[str, Any]:
        """扫描工作区：文件数 / 总大小 / 最近修改文件 / 目录树概览。"""
        with self._lock:
            root = self.workspace_root
            total_files = 0
            total_bytes = 0
            by_ext: Dict[str, int] = {}
            recent: List[Dict[str, Any]] = []
            dirs_seen = 0
            if not os.path.isdir(root):
                return {"exists": False, "root": root}

            now = time.time()
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    d for d in dirnames
                    if d not in _SKIP_DIRS and not d.startswith(".")
                ]
                dirs_seen += len(dirnames)
                for fname in filenames:
                    full = os.path.join(dirpath, fname)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    total_files += 1
                    total_bytes += st.st_size
                    ext = os.path.splitext(fname)[1].lower() or "(无扩展名)"
                    by_ext[ext] = by_ext.get(ext, 0) + 1
                    recent.append({
                        "path": os.path.relpath(full, root),
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                    })
                if total_files > 200_000:  # 防御超大型目录
                    break

            recent.sort(key=lambda x: x["mtime"], reverse=True)
            recent_top = []
            for item in recent[:top_n]:
                age_s = now - item["mtime"]
                recent_top.append({
                    "path": item["path"],
                    "size": item["size"],
                    "age_seconds": round(age_s, 1),
                })
            top_exts = sorted(by_ext.items(), key=lambda kv: kv[1], reverse=True)[:10]
            return {
                "exists": True,
                "root": root,
                "files": total_files,
                "dirs": dirs_seen,
                "total_bytes": total_bytes,
                "extensions": dict(top_exts),
                "recent_files": recent_top,
            }

    # ── git 状态 ──────────────────────────────────────────

    def git_status(self) -> Optional[Dict[str, Any]]:
        """读取 git 状态（工作区不是 git 仓库时返回 None）。"""
        with self._lock:
            git_dir = os.path.join(self.workspace_root, ".git")
            if not os.path.isdir(git_dir):
                return None
            out: Dict[str, Any] = {"is_repo": True}
            branch = self._git(["rev-parse", "--abbrev-ref", "HEAD"])
            out["branch"] = branch.strip() if branch else ""
            status = self._git(["status", "--porcelain"])
            lines = [l for l in (status or "").splitlines() if l.strip()]
            out["changes"] = len(lines)
            out["staged"] = sum(1 for l in lines if l[:2].strip() != "??" and l[0] != " ")
            out["untracked"] = sum(1 for l in lines if l.startswith("??"))
            log = self._git(["log", "--oneline", "-5"])
            out["recent_commits"] = [l.strip() for l in (log or "").splitlines() if l.strip()]
            return out

    def _git(self, args: List[str], timeout: float = 5.0) -> str:
        import subprocess

        try:
            proc = subprocess.run(
                ["git"] + args,
                cwd=self.workspace_root,
                capture_output=True,
                timeout=timeout,
            )
            if proc.returncode != 0:
                return ""
            for enc in ("utf-8", "gbk", "latin-1"):
                try:
                    return proc.stdout.decode(enc)
                except UnicodeDecodeError:
                    continue
            return proc.stdout.decode("utf-8", errors="replace")
        except Exception:
            return ""

    def status(self) -> Dict[str, Any]:
        """汇总项目状态（project_status 工具的数据源）。"""
        with self._lock:
            meta = self.init()
            scan = self.scan()
            return {
                "meta": meta,
                "scan": scan,
                "git": self.git_status(),
            }

    def close(self) -> None:
        """无驻留资源；接口对齐其他组件。"""
        return None

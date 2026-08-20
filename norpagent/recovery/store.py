# Copyright (c) 2026 xingluosama121, MIT Licensed
"""快照存储层（纯标准库）：manifest 时间线 + 每快照一个 JSON 文件。

崩溃救援 CLI（``norpagent-rescue``，norpagent.rescue 模块）与本模块
共享同一存储格式，且二者都**只依赖标准库**——主程序哪怕因配置错误
或插件问题完全无法启动，救援工具仍能读取 / 回退快照。

目录布局（默认 ``~/.norpagent/snapshots/``，可用环境变量
``NORPAGENT_SNAPSHOT_DIR`` 或 ``set_snapshot_dir()`` 覆盖）：:

    <snapshot_dir>/
        manifest.json              # 时间线元数据 + 当前指针 + 最后正常快照
        snap/<snap_id>.json        # 每快照一个文件
        attachments/<snap_id>/…    # 会话文件副本（快照模式 B：含会话数据）
        rollback_target.json       # 崩溃救援回退目标（下次启动消费后删除）

manifest.json 结构::

    {
      "version": 1,
      "current": 2,                 # 当前指针（最近应用/生成的快照下标；-1=无）
      "last_good": "…",             # 最后一次正常工作的快照 id（可空）
      "snapshots": [
        {"id", "created_at", "description", "tag", "good", "source",
         "sessions", "size"}
      ]
    }

指针语义（Undo / Redo 依赖）：
- 新快照追加到尾部，``current`` 指向它；
- 若在时间线中间（撤销后）生成新快照，**截断**其后所有快照
  （标准撤销语义：新操作开新分支）；
- Undo = 应用 current-1 并把 current 前移；Redo = 应用 current+1。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

MANIFEST_VERSION = 1

_default_dir: Optional[str] = None
_lock = threading.RLock()


def _homedir() -> str:
    return os.path.expanduser("~")


def get_snapshot_dir() -> str:
    """快照目录（显式 set_snapshot_dir > 环境变量 > 默认）。

    显式编程调用最具体、优先级最高；环境变量
    ``NORPAGENT_SNAPSHOT_DIR`` 次之；默认
    ``~/.norpagent/snapshots``。
    """
    global _default_dir
    if _default_dir:
        return _default_dir
    env = os.environ.get("NORPAGENT_SNAPSHOT_DIR", "").strip()
    if env:
        return env
    return os.path.join(_homedir(), ".norpagent", "snapshots")


def set_snapshot_dir(path: str) -> None:
    """覆盖快照目录（进程内生效；不落盘）。"""
    global _default_dir
    with _lock:
        _default_dir = path


def _manifest_path() -> str:
    return os.path.join(get_snapshot_dir(), "manifest.json")


def _snap_path(snap_id: str) -> str:
    return os.path.join(get_snapshot_dir(), "snap", f"{snap_id}.json")


def attachment_dir(snap_id: str) -> str:
    """快照附件目录（会话文件副本，模式 B）。"""
    return os.path.join(get_snapshot_dir(), "attachments", snap_id)


def load_manifest() -> Dict[str, Any]:
    """读取 manifest；缺失 / 损坏时返回空时间线（不抛错——救援场景要皮实）。"""
    with _lock:
        try:
            with open(_manifest_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("snapshots"), list):
                data.setdefault("version", MANIFEST_VERSION)
                data.setdefault("current", -1)
                data.setdefault("last_good", None)
                return data
        except (OSError, ValueError):
            pass
        return {"version": MANIFEST_VERSION, "current": -1,
                "last_good": None, "snapshots": []}


def _save_manifest(manifest: Dict[str, Any]) -> None:
    with _lock:
        root = get_snapshot_dir()
        os.makedirs(root, exist_ok=True)
        tmp = f"{_manifest_path()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _manifest_path())


def _new_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S") + "_" + os.urandom(3).hex()


def write_snapshot(
    data: Dict[str, Any],
    description: str = "",
    tag: str = "auto",
    source: str = "engine",
    sessions: bool = False,
) -> Dict[str, Any]:
    """写入一个新快照并追加到时间线尾部（撤销后写入会截断 redo 分支）。

    返回 manifest 里的快照信息条目（含 id / index）。
    """
    with _lock:
        snap_id = _new_id()
        created_at = time.time()
        # 附件（会话文件）先落盘，再记录到快照数据
        attachments: List[Dict[str, Any]] = []
        if sessions:
            for item in data.get("session_files") or []:
                src = item.get("path")
                if not src or not os.path.isfile(src):
                    continue
                dst_dir = attachment_dir(snap_id)
                os.makedirs(dst_dir, exist_ok=True)
                name = os.path.basename(src)
                dst = os.path.join(dst_dir, name)
                try:
                    shutil.copyfile(src, dst)
                except OSError:
                    continue
                attachments.append({
                    "name": name,
                    "path": src,
                    "size": os.path.getsize(dst),
                })
        data = dict(data)
        data["session_files"] = attachments

        manifest = load_manifest()
        items: List[Dict[str, Any]] = manifest["snapshots"]
        current = int(manifest.get("current", -1))
        # 撤销后开新分支：截断 current 之后的快照
        if 0 <= current < len(items) - 1:
            for stale in items[current + 1:]:
                _delete_snapshot_files(stale.get("id"))
                # 撤销的条目被截断后，last_good 若指向它则失效
                if manifest.get("last_good") == stale.get("id"):
                    manifest["last_good"] = None
            items = items[:current + 1]

        snap_path = _snap_path(snap_id)
        os.makedirs(os.path.dirname(snap_path), exist_ok=True)
        payload = {
            "id": snap_id,
            "created_at": created_at,
            "description": description,
            "tag": tag,
            "source": source,
            "sessions": bool(sessions),
            "data": data,
        }
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        info = {
            "id": snap_id,
            "created_at": created_at,
            "description": description,
            "tag": tag,
            "good": False,
            "source": source,
            "sessions": bool(sessions),
            "size": os.path.getsize(snap_path),
        }
        items.append(info)
        manifest["snapshots"] = items
        manifest["current"] = len(items) - 1
        _save_manifest(manifest)
        info["index"] = len(items) - 1
        return info


def read_snapshot(snap_id: str) -> Optional[Dict[str, Any]]:
    """读取快照全文（id 不存在或文件损坏返回 None）。"""
    with _lock:
        try:
            with open(_snap_path(snap_id), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None


def list_snapshots() -> List[Dict[str, Any]]:
    """时间线全部条目（含 index）。"""
    manifest = load_manifest()
    return [
        {**item, "index": i}
        for i, item in enumerate(manifest.get("snapshots") or [])
    ]


def _index_of(snap_id: str) -> int:
    for item in list_snapshots():
        if item.get("id") == snap_id:
            return int(item["index"])
    return -1


def current_index() -> int:
    return int(load_manifest().get("current", -1))


def current_id() -> Optional[str]:
    items = list_snapshots()
    idx = current_index()
    if 0 <= idx < len(items):
        return items[idx].get("id")
    return None


def set_current(snap_id: str) -> bool:
    """移动当前指针（Undo / Redo / Rollback 应用成功后调用）。"""
    idx = _index_of(snap_id)
    if idx < 0:
        return False
    manifest = load_manifest()
    manifest["current"] = idx
    _save_manifest(manifest)
    return True


def undo_target() -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """撤销目标：current-1 位置（含数据全文）。无则 None。"""
    items = list_snapshots()
    idx = current_index()
    if idx < 1:
        return None
    target = items[idx - 1]
    data = read_snapshot(target["id"])
    if data is None:
        return None
    return target, data


def redo_target() -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """恢复目标：current+1 位置（含数据全文）。无则 None。"""
    items = list_snapshots()
    idx = current_index()
    if idx < 0 or idx >= len(items) - 1:
        return None
    target = items[idx + 1]
    data = read_snapshot(target["id"])
    if data is None:
        return None
    return target, data


def mark_good(snap_id: str) -> Optional[Dict[str, Any]]:
    """把某快照标记为「正常工作」；同时成为 last_good。"""
    manifest = load_manifest()
    items = manifest.get("snapshots") or []
    hit = None
    for item in items:
        if item.get("id") == snap_id:
            item["good"] = True
            hit = item
    if hit is None:
        return None
    manifest["last_good"] = snap_id
    _save_manifest(manifest)
    return {**hit, "index": _index_of(snap_id)}


def last_good_id() -> Optional[str]:
    return load_manifest().get("last_good")


def prune(keep: int = 50) -> int:
    """只保留最近 ``keep`` 个快照（旧的删文件 + 出时间线）。"""
    if keep < 1:
        return 0
    manifest = load_manifest()
    items = manifest.get("snapshots") or []
    if len(items) <= keep:
        return 0
    removed = 0
    stale = items[: len(items) - keep]
    keep_items = items[len(items) - keep:]
    for item in stale:
        _delete_snapshot_files(item.get("id"))
        if manifest.get("last_good") == item.get("id"):
            manifest["last_good"] = None
        removed += 1
    manifest["snapshots"] = keep_items
    # 指针整体前移 removed 位
    manifest["current"] = max(-1, int(manifest.get("current", -1)) - removed)
    _save_manifest(manifest)
    return removed


def _delete_snapshot_files(snap_id: Optional[str]) -> None:
    if not snap_id:
        return
    try:
        os.remove(_snap_path(snap_id))
    except OSError:
        pass
    try:
        shutil.rmtree(attachment_dir(snap_id), ignore_errors=True)
    except OSError:
        pass


# ── 崩溃救援回退目标 ─────────────────────────────────────

def rollback_target_path() -> str:
    return os.path.join(get_snapshot_dir(), "rollback_target.json")


def write_rollback_target(snapshot: Dict[str, Any]) -> str:
    """把某快照落为「下次启动回退目标」（救援 CLI 用）。

    返回目标文件路径。内容包括：快照 id / 时间 / layer / cli /
    webui_config / session_files / providers。下次 np() / norpagent
    启动时消费并删除。
    """
    data = snapshot.get("data", snapshot)
    payload = {
        "format": 1,
        "snapshot_id": snapshot.get("id") or data.get("id"),
        "created_at": snapshot.get("created_at") or time.time(),
        "description": snapshot.get("description") or data.get("description", ""),
        "data": data,
    }
    path = rollback_target_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def consume_rollback_target() -> Optional[Dict[str, Any]]:
    """读取并删除回退目标文件（启动时调用，幂等）。

    返回 payload dict（含 data），无目标文件返回 None；文件损坏时
    删除并返回 None（不阻塞启动）。
    """
    path = rollback_target_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        payload = None
    try:
        os.remove(path)
    except OSError:
        pass
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return None
    return payload


__all__ = [
    "get_snapshot_dir", "set_snapshot_dir",
    "load_manifest", "write_snapshot", "read_snapshot", "list_snapshots",
    "current_index", "current_id", "set_current",
    "undo_target", "redo_target", "mark_good", "last_good_id", "prune",
    "attachment_dir", "write_rollback_target", "consume_rollback_target",
    "rollback_target_path",
]

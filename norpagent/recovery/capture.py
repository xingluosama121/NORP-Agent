# Copyright (c) 2026 xingluosama121, MIT Licensed
"""快照采集与回放：系统状态 <-> JSON 快照。

采集内容（快照模式 A，默认）：
- 架构层槽位配置（layer.config，含模式 / 模型 / 工具 / 会话 / 沙箱 /
  前端 / 插件目录 / 安全级别等——全部槽位值）；
- 引擎运行时参数（engine.params，port / host / html / flow_html /
  model_name / base_url 等，敏感键脱敏）；
- WebUI 设置文件（~/.norpagent/webui_config.json 内容，api_key 脱敏）；
- 自定义提供者数据（register_snapshot_provider 注册的扩展钩子）。

快照模式 B（``snapshots_session="on"``）：额外把会话存储文件复制到
快照附件目录，回退时整文件恢复（会覆盖回退之后的对话记录）。

可序列化规则：
- 内建类型原样保存；
- 实例 / 类 / 函数等不可序列化值 → 记录类型标记
  ``{"__instance__": "pkg.mod.QualName"}``，回放时跳过并提示
  （诚实降级，不伪造状态）。
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

# 敏感键脱敏（快照要落盘，不能把密钥写进快照文件）
_REDACT_KEYS = frozenset((
    "api_key", "api-key", "apikey", "private_key", "private-key",
    "secret", "token", "password", "passwd", "authorization",
))

# 引擎参数里属于 Web 挂载参数 / 模型配置的键（回放时特殊处理）
_WEB_PARAM_KEYS = frozenset((
    "port", "host", "open_browser", "language", "html", "flow_html",
    "sse_queue_size", "sse_queue_policy",
))

# 默认会话存储文件（模式 B 采集目标）
_DEFAULT_SESSION_FILES = (
    os.path.join(os.path.expanduser("~"), ".norpagent", "sessions.db"),
)


def jsonable(value: Any, _depth: int = 0) -> Any:
    """把任意值转成可 JSON 序列化的形态（敏感键脱敏）。

    不可序列化的实例 / 类 / 函数 → ``{"__instance__": 类型限定名}``。
    """
    if _depth > 8:
        return repr(value)[:200]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return [jsonable(v, _depth + 1) for v in value]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if key.lower() in _REDACT_KEYS:
                out[key] = "<redacted>"
            else:
                out[key] = jsonable(v, _depth + 1)
        return out
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {
            "__instance__": (
                f"{type(value).__module__}.{type(value).__qualname__}"
            )
        }


def is_marker(value: Any) -> bool:
    """是否「不可序列化」类型标记（回放时跳过）。"""
    return isinstance(value, dict) and "__instance__" in value


def _read_webui_config() -> Optional[Dict[str, Any]]:
    """读取 WebUI 设置文件内容（不存在 / 损坏返回 None）。"""
    path = os.environ.get(
        "NORPAGENT_WEBUI_CONFIG",
        os.path.join(os.path.expanduser("~"), ".norpagent",
                     "webui_config.json"),
    )
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _session_files(engine: Any) -> List[Dict[str, Any]]:
    """模式 B：收集会话存储文件（默认 sessions.db + 槽位配置显式路径）。"""
    files: List[str] = []
    for path in _DEFAULT_SESSION_FILES:
        if os.path.isfile(path):
            files.append(path)
    if engine is not None:
        layer = getattr(engine, "layer", None)
        if layer is not None:
            session_val = None
            try:
                session_val = layer.config.get("session")
            except Exception:  # noqa: BLE001
                pass
            if isinstance(session_val, str) and not session_val.startswith(";"):
                # 名字（sqlite / memory）或路径（含 .db / 文件存在）
                cand = session_val.partition(";")[0]
                if (cand.endswith(".db") or os.path.isabs(cand)
                        or os.path.sep in cand) and os.path.isfile(cand):
                    files.append(cand)
    seen = set()
    out = []
    for path in files:
        if path in seen:
            continue
        seen.add(path)
        try:
            out.append({
                "name": os.path.basename(path),
                "path": os.path.abspath(path),
                "size": os.path.getsize(path),
            })
        except OSError:
            continue
    return out


def _norpagent_version() -> str:
    try:
        import norpagent as np  # noqa: F401  延迟导入避免循环

        return getattr(np, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def capture_system(
    engine: Any,
    description: str = "",
    tag: str = "auto",
    sessions: bool = False,
    providers: Optional[Dict[str, Callable[[Any], Any]]] = None,
) -> Dict[str, Any]:
    """采集当前系统状态为快照 data dict（尚未落盘）。

    ``providers``：register_snapshot_provider 注册的自定义采集钩子
    （name -> capture(engine) -> JSON 值）。
    """
    layer_config: Dict[str, Any] = {}
    params: Dict[str, Any] = {}
    if engine is not None:
        layer = getattr(engine, "layer", None)
        if layer is not None:
            try:
                layer_config = {
                    str(k): jsonable(v)
                    for k, v in layer.config.items()
                }
            except Exception:  # noqa: BLE001
                layer_config = {}
        try:
            params = jsonable(dict(getattr(engine, "params", None) or {}))
        except Exception:  # noqa: BLE001
            params = {}

    provider_data: Dict[str, Any] = {}
    for name, fn in (providers or {}).items():
        try:
            provider_data[name] = jsonable(fn(engine))
        except Exception:  # noqa: BLE001 — 单个提供者失败不拖垮快照
            provider_data[name] = {"error": "provider capture failed"}

    return {
        "format": 1,
        "source": "engine",
        "description": description,
        "tag": tag,
        "norpagent_version": _norpagent_version(),
        "layer": layer_config,
        "params": params,
        "webui_config": jsonable(_read_webui_config()),
        "session_files": _session_files(engine) if sessions else [],
        "providers": provider_data,
    }


def capture_cli(
    args: Dict[str, Any],
    description: str = "",
    tag: str = "auto",
    sessions: bool = False,
) -> Dict[str, Any]:
    """CLI 启动形态的快照（source="cli"）：命令行参数 + WebUI 设置。

    救援回退时按 CLI 形态合并回启动参数；引擎形态（np()）按 layer
    合并。两种形态的文件级恢复（webui 设置 / 会话文件）一致。
    """
    return {
        "format": 1,
        "source": "cli",
        "description": description,
        "tag": tag,
        "norpagent_version": _norpagent_version(),
        "cli": jsonable(args or {}),
        "webui_config": jsonable(_read_webui_config()),
        "session_files": [] if not sessions else [
            {"name": os.path.basename(p), "path": os.path.abspath(p),
             "size": os.path.getsize(p)}
            for p in _DEFAULT_SESSION_FILES if os.path.isfile(p)
        ],
        "providers": {},
    }


# ── 回放 ────────────────────────────────────────────────

def _write_webui_config(cfg: Optional[Dict[str, Any]]) -> None:
    """把快照里的 WebUI 设置写回磁盘（下次启动 / 重启后生效）。"""
    if not isinstance(cfg, dict):
        return
    path = os.environ.get(
        "NORPAGENT_WEBUI_CONFIG",
        os.path.join(os.path.expanduser("~"), ".norpagent",
                     "webui_config.json"),
    )
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def _restore_session_files(snapshot_data: Dict[str, Any],
                           snap_id: str = "") -> int:
    """模式 B：把快照附带的会话文件复制回原路径。返回恢复的文件数。"""
    from norpagent.recovery.store import attachment_dir

    restored = 0
    for item in snapshot_data.get("session_files") or []:
        cand = None
        if snap_id:
            p = os.path.join(attachment_dir(snap_id), item.get("name", ""))
            if os.path.isfile(p):
                cand = p
        if not cand:
            continue
        target = item.get("path")
        if not target:
            continue
        try:
            import shutil

            os.makedirs(os.path.dirname(os.path.abspath(target)),
                        exist_ok=True)
            shutil.copyfile(cand, target)
            restored += 1
        except OSError:
            continue
    return restored


def restore_files(snapshot_data: Dict[str, Any],
                  snap_id: str = "") -> Dict[str, Any]:
    """文件级恢复：WebUI 设置 + 会话文件（引擎形态与 CLI 形态通用）。

    返回 {"webui": bool, "sessions": int}。进程内回放与救援 CLI
    回退共用本函数。``snap_id`` 用于定位模式 B 的会话附件。
    """
    cfg = snapshot_data.get("webui_config")
    _write_webui_config(cfg)
    sessions = _restore_session_files(snapshot_data, snap_id)
    return {"webui": isinstance(cfg, dict), "sessions": sessions}


__all__ = [
    "jsonable", "is_marker", "capture_system", "capture_cli",
    "restore_files",
]

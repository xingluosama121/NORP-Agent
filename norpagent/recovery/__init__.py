# Copyright (c) 2026 xingluosama121, MIT Licensed
"""工作回退（Recovery）：快照 / Undo / Redo / Rollback / 崩溃救援。

Agent 工作的可回退能力，四个层次：

1. **一键撤销与恢复（Undo / Redo）**：Web UI 按钮 / 快捷键
   （Ctrl+Z / Ctrl+Shift+Z）/ API 撤销或恢复最近一次操作——进程内
   即时生效，复用 remount 热挂载管线（HTTP 端口不变）。

2. **回退到任意版本（Rollback）**：浏览全部历史快照，一键把整个
   系统回退到任何一个版本。

3. **崩溃救援（Crash Rescue）**：独立 CLI ``norpagent-rescue``
   （纯标准库，主程序怎么坏都能跑）回退快照；系统提示「最后一次
   正常工作的快照」供一键恢复。回退落成 rollback_target.json，
   下次 np() / norpagent 启动自动消费。

4. **安全模式（Safe Mode）**：``np(safemode="on")`` / CLI
   ``--safe-mode`` 只加载最小化内核（跳过全部插件、强制 minimal
   预设、不读 WebUI 设置文件），保留核心回退能力，便于修复。

快照内容（默认模式 A = 系统配置级；模式 B 额外含会话数据文件）：
- 架构层全部槽位配置（模式 / 模型 / 工具 / 会话 / 沙箱 / 前端 /
  插件目录 / 安全级别…）；
- 引擎运行时参数（port / host / html / flow_html / 模型选项…，
  敏感键脱敏）；
- WebUI 设置文件内容；
- 自定义提供者数据（register_snapshot_provider 注册的扩展钩子）。

自动快照时机：启动基线、每次系统状态变更（remount / WebUI 设置
保存 / 插件安装 / 模式切换）之后。手动快照：Web UI「快照」按钮或
``np.snapshot_system("说明")``。

「最后一次正常工作的快照」：引擎启动成功后 30 秒健康期（或首个
任务完成）自动 mark-good；用户也可手动标记。救援 CLI 据此提示
一键恢复目标。

用法::

    import norpagent as np
    np()
    np.snapshot_system("装插件之前")          # 手动快照
    np.undo()                                  # 撤销最近一次操作（进程内即时）
    np.redo()                                  # 恢复
    np.rollback("20260818T230101_ab12cd")      # 回退到任意快照
    np.list_snapshots()                        # 时间线
    np.mark_good_snapshot("<id>")              # 标记「正常」

自定义快照内容（钩子式扩展）::

    from norpagent import recovery
    recovery.register_snapshot_provider("my_state", capture=lambda eng: {...},
                                        restore=lambda eng, v: ...)

存储位置：默认 ``~/.norpagent/snapshots/``（环境变量
``NORPAGENT_SNAPSHOT_DIR`` 或 ``np(snapshot_dir=...)`` 自定义）。
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from norpagent.recovery import capture, store

__version__ = "0.9.1"


class RecoveryError(RuntimeError):
    """回退操作失败（无可撤销快照 / 快照不存在 / 应用失败等）。"""


# 回放进行中标记：恢复快照本身会触发 remount / 配置应用，这些
# 变更不能再次自动快照（否则撤销会立刻生成新快照覆盖 redo 分支）。
_applying = False
_apply_lock = threading.RLock()

# 自定义快照提供者：name -> (capture(engine)->json, restore(engine,value))
_providers: Dict[str, tuple] = {}
_providers_lock = threading.Lock()

# 自动快照开关（np(snapshots="off") 关闭）
_enabled = True
_enabled_lock = threading.Lock()

# 自动修剪保留数量上限
_AUTO_PRUNE_KEEP = 200


# ── 配置 ────────────────────────────────────────────────

def set_snapshot_dir(path: str) -> None:
    """覆盖快照存储目录（进程内生效）。"""
    store.set_snapshot_dir(path)


def get_snapshot_dir() -> str:
    return store.get_snapshot_dir()


def set_enabled(enabled: bool) -> None:
    """开关自动快照（显式 API 调用仍可用）。"""
    global _enabled
    with _enabled_lock:
        _enabled = bool(enabled)


def is_enabled() -> bool:
    with _enabled_lock:
        return _enabled


def register_snapshot_provider(
    name: str,
    capture: Callable[[Any], Any],
    restore: Optional[Callable[[Any, Any], None]] = None,
) -> None:
    """注册自定义快照提供者（扩展快照内容 / 恢复逻辑的钩子）。

    - ``capture(engine) -> json 值``：采集自定义状态；
    - ``restore(engine, value) -> None``：回放时恢复（可选；
      未提供时该段只入快照、不参与回放）。
    注册即生效，重名覆盖。
    """
    with _providers_lock:
        _providers[name] = (capture, restore)


def unregister_snapshot_provider(name: str) -> None:
    with _providers_lock:
        _providers.pop(name, None)


def _provider_callbacks() -> Dict[str, Callable[[Any], Any]]:
    with _providers_lock:
        return {k: v[0] for k, v in _providers.items()}


# ── 采集 ────────────────────────────────────────────────

def snapshot_system(
    description: str = "",
    tag: str = "manual",
    engine: Optional[Any] = None,
    sessions: Optional[bool] = None,
) -> Dict[str, Any]:
    """采集当前系统状态并落盘为一个快照，返回 manifest 信息条目。

    用法::

        np.snapshot_system("装插件之前")          # 手动快照
        np.snapshot_system("标注", engine=eng)     # 指定引擎

    ``sessions``：None=跟随引擎配置（np(snapshot_sessions=...)），
    True/False 显式覆盖（快照模式 B / A）。
    """
    if engine is None:
        from norpagent.runtime import current

        engine = current()
    if sessions is None:
        sessions = bool(getattr(engine, "_snapshot_sessions", False))
    data = capture.capture_system(
        engine, description=description, tag=tag,
        sessions=sessions, providers=_provider_callbacks(),
    )
    info = store.write_snapshot(
        data, description=description, tag=tag, source="engine",
        sessions=sessions,
    )
    # 自动修剪：默认只保留最近 200 个快照（防止无限累积）
    try:
        if len(store.list_snapshots()) > _AUTO_PRUNE_KEEP:
            store.prune(_AUTO_PRUNE_KEEP)
    except Exception:  # noqa: BLE001
        pass
    return info


def snapshot_cli(
    args: Dict[str, Any],
    description: str = "",
    tag: str = "auto",
    sessions: bool = False,
) -> Dict[str, Any]:
    """CLI 启动形态的快照（norpagent 命令行内部使用）。"""
    data = capture.capture_cli(args, description=description, tag=tag,
                               sessions=sessions)
    return store.write_snapshot(
        data, description=description, tag=tag, source="cli",
        sessions=sessions,
    )


def notify_system_change(
    engine: Optional[Any] = None,
    description: str = "系统变更",
) -> None:
    """系统状态变更后的自动快照入口（remount / 配置保存 / 插件安装）。

    - 回放进行中跳过（防撤销自拍快照）；
    - 自动快照关闭时跳过；
    - 快照失败只记日志、不拖垮主流程。
    """
    global _applying
    if _applying or not is_enabled():
        return
    if engine is None:
        try:
            from norpagent.runtime import current

            engine = current()
        except Exception:  # noqa: BLE001
            engine = None
    if engine is None:
        return
    if not getattr(engine, "is_running", lambda: False)():
        return
    try:
        snapshot_system(description=description, tag="auto",
                        engine=engine)
    except Exception:  # noqa: BLE001
        try:
            logger = getattr(engine, "_logger", None)
            if logger is not None:
                logger.warning("自动快照失败: %s", description,
                               exc_info=True)
        except Exception:  # noqa: BLE001
            pass


# ── 回放（进程内即时生效） ───────────────────────────────

def _restore_layer(engine: Any, data: Dict[str, Any]) -> List[str]:
    """把快照的槽位配置与页面参数重新应用到运行中引擎。

    返回实际应用了的键列表。走 remount 热挂载管线：组件槽位下一
    次 run 生效，装配槽位热重建 AgentRuntime，HTTP 端口不变。
    """
    from norpagent.arch.slots import snapshot_slots

    known = snapshot_slots()
    layer = getattr(engine, "layer", None)
    slots: Dict[str, Any] = {}
    skipped: List[str] = []
    for key, value in (data.get("layer") or {}).items():
        if key not in known:
            skipped.append(key)
            continue
        if capture.is_marker(value):
            skipped.append(key)
            continue
        # 与当前配置相同的槽位跳过重挂：避免无谓的组件重建 /
        # 前端重启（例如每次撤销都重启 HTTP 监听）。
        try:
            current = layer.config.get(key) if layer is not None else None
            if current is not None and capture.jsonable(current) == value:
                continue
        except Exception:  # noqa: BLE001
            pass
        slots[key] = value

    web_keys = ("html", "flow_html")
    params: Dict[str, Any] = {}
    for key, value in (data.get("params") or {}).items():
        if capture.is_marker(value):
            continue
        if key in web_keys:
            slots[key] = value
        elif key in capture._WEB_PARAM_KEYS or key in (
                "model_name", "base_url"):
            params[key] = value

    # 更新引擎参数（后续 frontend attach / 下次 run 沿用）
    if params:
        engine_params = getattr(engine, "params", None)
        if engine_params is not None:
            engine_params.update(params)

    applied: List[str] = []
    if slots:
        try:
            engine.remount(**slots)
            applied = list(slots)
        except Exception as exc:  # noqa: BLE001
            raise RecoveryError(
                f"快照槽位应用失败: {exc}（已跳过 {len(skipped)} 个不可序列化键）"
            ) from exc
    return applied


def _restore_webui_live(engine: Any, cfg: Optional[Dict[str, Any]]) -> None:
    """把 WebUI 设置恢复到运行中的 UI（写盘 + 内存配置 + 应用）。"""
    if not isinstance(cfg, dict):
        return
    capture._write_webui_config(cfg)
    frontend = getattr(engine, "frontend", None)
    ui = getattr(frontend, "_ui", None) if frontend is not None else None
    restore = getattr(ui, "restore_config", None)
    if callable(restore):
        try:
            restore(cfg)
        except Exception:  # noqa: BLE001
            pass


def _apply_snapshot(engine: Any, info: Dict[str, Any],
                    data: Dict[str, Any]) -> Dict[str, Any]:
    """应用一个快照到运行中引擎（Undo / Redo / Rollback 共用）。"""
    global _applying
    with _apply_lock:
        _applying = True
        try:
            snap_data = data.get("data") if isinstance(data, dict) else None
            if not isinstance(snap_data, dict):
                snap_data = data
            applied = _restore_layer(engine, snap_data)
            _restore_webui_live(engine, snap_data.get("webui_config"))
            # 模式 B：会话文件整文件恢复
            capture.restore_files(snap_data,
                                  snap_id=(data.get("id") or ""))
            # 自定义提供者恢复钩子
            pd = snap_data.get("providers") or {}
            with _providers_lock:
                restorers = {k: v[1] for k, v in _providers.items()
                             if v[1] is not None}
            for name, value in pd.items():
                fn = restorers.get(name)
                if fn is None or (isinstance(value, dict)
                                  and "error" in value):
                    continue
                try:
                    fn(engine, value)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            _applying = False
    return {"ok": True, "applied_slots": applied,
            "snapshot_id": info.get("id")}


def _current_engine() -> Any:
    from norpagent.runtime import current

    engine = current()
    if engine is None:
        raise RecoveryError("尚未启动引擎：先调用 np() 或 norpagent.launch()")
    return engine


def undo(engine: Optional[Any] = None) -> Dict[str, Any]:
    """撤销最近一次操作：恢复到上一快照（进程内即时生效）。"""
    if engine is None:
        engine = _current_engine()
    target = store.undo_target()
    if target is None:
        raise RecoveryError("没有可撤销的快照（已在最早状态）")
    info, data = target
    result = _apply_snapshot(engine, info, data)
    store.set_current(info["id"])
    result["undo"] = True
    result["description"] = info.get("description")
    return result


def redo(engine: Optional[Any] = None) -> Dict[str, Any]:
    """恢复最近一次撤销：回到撤销前快照（进程内即时生效）。"""
    if engine is None:
        engine = _current_engine()
    target = store.redo_target()
    if target is None:
        raise RecoveryError("没有可恢复的快照（已在最新状态）")
    info, data = target
    result = _apply_snapshot(engine, info, data)
    store.set_current(info["id"])
    result["redo"] = True
    result["description"] = info.get("description")
    return result


def rollback(snap_id: Optional[str] = None,
             engine: Optional[Any] = None) -> Dict[str, Any]:
    """回退到任意历史快照。

    用法::

        np.rollback("20260818T230101_ab12cd")   # 回退到指定快照
        np.rollback()                           # 回退到最后正常快照

    ``snap_id`` 缺省时回退到「最后一次正常工作的快照」。
    """
    if engine is None:
        engine = _current_engine()
    if snap_id is None:
        snap_id = store.last_good_id()
        if snap_id is None:
            raise RecoveryError("没有标记为「正常」的快照")
    data = store.read_snapshot(snap_id)
    if data is None:
        raise RecoveryError(f"快照不存在: {snap_id}")
    info = next(
        (item for item in store.list_snapshots()
         if item.get("id") == snap_id),
        {"id": snap_id, "description": ""},
    )
    result = _apply_snapshot(engine, info, data)
    store.set_current(snap_id)
    result["rollback"] = True
    result["description"] = info.get("description")
    return result


def list_snapshots() -> List[Dict[str, Any]]:
    """时间线全部快照条目（含 index / good / description 等）。"""
    items = store.list_snapshots()
    current = store.current_index()
    last_good = store.last_good_id()
    for item in items:
        item["is_current"] = item.get("index") == current
        item["is_last_good"] = item.get("id") == last_good
    return items


def mark_good(snap_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """标记快照为「正常工作」（缺省标记当前快照）。"""
    if snap_id is None:
        snap_id = store.current_id()
    if not snap_id:
        raise RecoveryError("没有可标记的快照")
    return store.mark_good(snap_id)


def last_good_id() -> Optional[str]:
    return store.last_good_id()


def prune(keep: int = 50) -> int:
    """只保留最近 ``keep`` 个快照。"""
    return store.prune(keep)


# ── 启动集成 ────────────────────────────────────────────

def apply_pending_rollback() -> Optional[Dict[str, Any]]:
    """启动时消费崩溃救援留下的回退目标（np() 路径）。

    - 文件级恢复（WebUI 设置 / 会话文件）立即执行；
    - 返回 data dict（含 layer / params），由启动流程合并进
      槽位配置；无回退目标返回 None。
    """
    payload = store.consume_rollback_target()
    if payload is None:
        return None
    data = payload.get("data") or {}
    capture.restore_files(data, snap_id=payload.get("snapshot_id") or "")
    return data


def apply_pending_rollback_cli() -> Optional[Dict[str, Any]]:
    """启动时消费回退目标（CLI 路径）：文件级恢复 + 返回 data dict。"""
    payload = store.consume_rollback_target()
    if payload is None:
        return None
    data = payload.get("data") or {}
    capture.restore_files(data, snap_id=payload.get("snapshot_id") or "")
    return data


__all__ = [
    "RecoveryError",
    "set_snapshot_dir", "get_snapshot_dir",
    "set_enabled", "is_enabled",
    "register_snapshot_provider", "unregister_snapshot_provider",
    "snapshot_system", "snapshot_cli", "notify_system_change",
    "undo", "redo", "rollback",
    "list_snapshots", "mark_good", "last_good_id", "prune",
    "apply_pending_rollback", "apply_pending_rollback_cli",
]

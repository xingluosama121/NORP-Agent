# Copyright (c) 2026 xingluosama121, MIT Licensed
"""运行时包：np() 一键启动、np.stop() 生命周期轮询、引擎管理。

::

    import norpagent as np
    np()                       # 按默认逻辑启动（默认 Web 前端，listening on ...）
    running = True
    while running:
        if np.stop() == True:  # 生命周期函数：True = 应用已结束
            running = False

单次任务：::

    import norpagent as np
    np(prompt="你好")          # headless 跑完自动停止（输出打印到 stdout）
    while True:
        if np.stop():
            break
    print(np.current().last_result.final_content)
"""

from __future__ import annotations

import atexit
import threading
from typing import Any, Dict, Optional

from norpagent.arch.layer import ArchLayer
from norpagent.arch.slots import snapshot_slots
from norpagent.runtime.engine import EngineError, EngineState, NorpEngine
from norpagent.runtime.mount import (
    build_registry,
    coerce_frontend,
    mount_defaults,
)

_current: Optional[NorpEngine] = None
_lock = threading.Lock()
_atexit_registered = False


def _register_atexit() -> None:
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(_atexit_shutdown)
        _atexit_registered = True


def launch(**kwargs: Any) -> NorpEngine:
    """np() 的实现：按架构层装配并启动一个 Agent 应用。

    关键字参数分两类：
    - 架构槽位：与 norpagent.arch.slots 槽位表同名的键
      （18 个内置槽位 async_loop / agent_runtime / model / tools /
      session / sandbox / scheduler / context_store /
      project_manager / hooks / security / plugins / frontend / ui /
      preset / logger / storage / error_handler，加上运行时经
      register_slot() 注册的自定义槽位），不填 = 默认逻辑，
      填地址 = 按地址接上实现；
    - 运行时参数：其余键全部作为任务参数透传 Agent 循环
      （如 max_steps / task_timeout / workspace_root 等）。

    特殊键：``prompt``（单次任务文本，跑完自动停止）、
    ``config``（以字典形式给槽位赋值）、
    ``safemode``（"on" = 安全模式：只加载最小化内核——跳过全部
    插件、强制 minimal 预设、不读 WebUI 设置文件，保留核心回退
    能力便于修复）、
    ``snapshot_dir``（快照存储目录，默认 ~/.norpagent/snapshots/）、
    ``snapshots``（"off" = 关闭自动快照，默认 "on"）、
    ``snapshot_sessions``（"on" = 快照模式 B，额外打包会话数据
    文件，默认 "off"）。

    已有运行中的引擎时直接返回当前引擎（单例语义）。
    """
    global _current
    prompt = kwargs.pop("prompt", None)
    config = kwargs.pop("config", None)
    # 工作回退（recovery）特殊键
    safemode_raw = kwargs.pop("safemode", None)
    safemode_on = str(safemode_raw).strip().lower() in (
        "on", "1", "true", "yes")
    snapshot_dir = kwargs.pop("snapshot_dir", None)
    snapshots_off = str(kwargs.pop("snapshots", "on")).strip().lower() in (
        "off", "0", "false", "no")
    snapshot_sessions = str(
        kwargs.pop("snapshot_sessions", "off")).strip().lower() in (
        "on", "1", "true", "yes")

    if snapshot_dir:
        try:
            from norpagent.recovery import set_snapshot_dir

            set_snapshot_dir(str(snapshot_dir))
        except Exception:  # noqa: BLE001
            pass
    if snapshots_off:
        try:
            from norpagent.recovery import set_enabled

            set_enabled(False)
        except Exception:  # noqa: BLE001
            pass

    # 安全模式：只加载最小化内核——跳过全部插件、强制 minimal
    # 预设、清掉安全套件参数（避免坏配置再次拖垮启动）。
    if safemode_on:
        for key in ("plugins", "preset", "security"):
            kwargs.pop(key, None)
        kwargs["preset"] = "minimal"

    with _lock:
        if _current is not None and _current.is_running():
            return _current

        # 拆分槽位键与运行时参数键（按实时槽位表：含运行时注册的
        # 自定义槽位；注册在 np() 调用之前即可被识别为槽位）
        slots = snapshot_slots()
        slot_values = {k: v for k, v in kwargs.items() if k in slots}
        params = {k: v for k, v in kwargs.items() if k not in slots}

        # 崩溃救援：消费上次 norpagent-rescue 留下的回退目标——
        # 文件级恢复（WebUI 设置 / 会话文件）已执行，这里把快照
        # 里的槽位配置合并进本次启动（用户显式给的参数优先）。
        try:
            from norpagent.recovery import apply_pending_rollback

            pending = apply_pending_rollback()
        except Exception:  # noqa: BLE001
            pending = None
        if pending and isinstance(pending, dict):
            for key, value in (pending.get("layer") or {}).items():
                if key in slots and key not in slot_values:
                    slot_values[key] = value
            for key, value in (pending.get("params") or {}).items():
                if key not in params:
                    params[key] = value
            print(f"[norpagent] 已应用崩溃救援回退快照: "
                  f"{pending.get('description') or pending.get('id') or ''}")

        layer = ArchLayer(config, **slot_values)
        mount_defaults(layer, prompt=prompt)
        layer.connect()

        registry, preset, extras = build_registry(layer, params=params)

        engine = NorpEngine(
            layer=layer,
            registry=registry,
            preset=preset,
            loop=layer["async_loop"],
            # frontend 槽位「HTML 路径直挂」语义化（v0.9）：
            # 槽位值是 .html/.htm 文件路径时装配为
            # WebFrontend(html=<该路径>)，与地址式挂载等价。
            frontend=coerce_frontend(
                layer["frontend"], layer.subconfig("frontend")),
            extras=extras,
            task_params=params,
            prompt=prompt,
            safe_mode=safemode_on,
        )
        if snapshot_sessions:
            engine._snapshot_sessions = True
        try:
            engine.start()
        except Exception as exc:  # noqa: BLE001 — 启动失败：给自救指引
            import sys

            print(f"[norpagent] 启动失败: {exc}", file=sys.stderr)
            print("  → 安全模式：np(safemode='on')（只加载最小化内核）",
                  file=sys.stderr)
            print("  → 崩溃救援：norpagent-rescue rollback --last-good",
                  file=sys.stderr)
            raise
        _current = engine
        _register_atexit()
        return engine


def current() -> Optional[NorpEngine]:
    """当前引擎（未启动返回 None）。"""
    return _current


def stop() -> bool:
    """生命周期函数：应用是否已结束（True = 主循环应退出）。

    对应引擎 STOPPED 状态；无引擎时返回 True（无事可做）。
    """
    engine = _current
    return engine is None or engine.should_stop()


def submit(text: str, session_id: Optional[str] = None) -> Any:
    """向当前引擎提交用户输入（需已 np() 启动）。"""
    engine = _current
    if engine is None:
        raise EngineError("尚未启动：先调用 np() 或 norpagent.launch()")
    return engine.submit(text, session_id=session_id)


def remount(**slot_values: Any) -> NorpEngine:
    """运行中热挂载：向当前引擎替换任意槽位实现（引擎保持运行）。

    用法::

        np.remount(model="openai_compat")     # 换模型（下一次 run 生效）
        np.remount(tools=["echo"])            # 换工具集
        np.remount(frontend="norpagent.frontends.headless:HeadlessFrontend")
        np.remount(flow_html="H:/path/flow.html")  # 运行中换流程页（HTTP 不重启）
        np.remount(html="H:/path/front.html")      # 运行中换主页面

    槽位分组语义（组件 / 装配 / 基础设施 / 基础服务）见
    norpagent.runtime.remount 模块文档；html / flow_html 是页面
    热替换键（frontend 槽位的挂载参数）；需已 np() 启动。
    """
    engine = _current
    if engine is None:
        raise EngineError("尚未启动：先调用 np() 或 norpagent.launch()")
    return engine.remount(**slot_values)


def shutdown() -> None:
    """显式停止当前引擎并清理（幂等）。"""
    global _current
    engine = _current
    if engine is not None:
        try:
            engine.request_stop()
        finally:
            _current = None


def _atexit_shutdown() -> None:
    shutdown()


def is_running() -> bool:
    engine = _current
    return engine is not None and engine.is_running()


__all__ = [
    "launch",
    "current",
    "stop",
    "submit",
    "remount",
    "shutdown",
    "is_running",
    "NorpEngine",
    "EngineState",
    "EngineError",
]

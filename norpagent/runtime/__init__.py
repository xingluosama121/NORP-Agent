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
from norpagent.arch.slots import SLOT_SPECS
from norpagent.runtime.engine import EngineError, EngineState, NorpEngine
from norpagent.runtime.mount import build_registry, mount_defaults

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
    - 架构槽位：与 norpagent.arch.slots.SLOT_SPECS 同名的键
      （async_loop / agent_runtime / model / tools / session / sandbox /
      scheduler / context_store / project_manager / hooks / security /
      plugins / frontend / ui / preset / logger / storage / error_handler），
      不填 = 默认逻辑，填地址 = 按地址接上实现；
    - 运行时参数：其余键全部作为任务参数透传 Agent 循环
      （如 max_steps / task_timeout / workspace_root 等）。

    特殊键：``prompt``（单次任务文本，跑完自动停止）、
    ``config``（以字典形式给槽位赋值）。

    已有运行中的引擎时直接返回当前引擎（单例语义）。
    """
    global _current
    prompt = kwargs.pop("prompt", None)
    config = kwargs.pop("config", None)

    with _lock:
        if _current is not None and _current.is_running():
            return _current

        # 拆分槽位键与运行时参数键
        slot_values = {k: v for k, v in kwargs.items() if k in SLOT_SPECS}
        params = {k: v for k, v in kwargs.items() if k not in SLOT_SPECS}

        layer = ArchLayer(config, **slot_values)
        mount_defaults(layer, prompt=prompt)
        layer.connect()

        registry, preset, extras = build_registry(layer, params=params)

        engine = NorpEngine(
            layer=layer,
            registry=registry,
            preset=preset,
            loop=layer["async_loop"],
            frontend=layer["frontend"],
            extras=extras,
            task_params=params,
            prompt=prompt,
        )
        engine.start()
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

    槽位分组语义（组件 / 装配 / 基础设施 / 基础服务）见
    norpagent.runtime.remount 模块文档；需已 np() 启动。
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

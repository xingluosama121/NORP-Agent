# Copyright (c) 2026 xingluosama121, MIT Licensed
"""内置会话管理。

- memory：进程内存储（演示 / 基准测试 / 嵌入式）；
- sqlite：SQLite 持久化存储（默认 ~/.norpagent/sessions.db），
  零第三方依赖，线程安全，替换方式与 memory 一致。

嵌入式优化（v0.9）：SQLite 实现按需懒导入——只 import 本包
（或只 import memory 子模块）不会拉起 sqlite3 依赖。
"""

from typing import Any

from norpagent.builtin.sessions.memory import MemorySessionManager

_LAZY_EXPORTS = {
    "SQLiteSessionManager": (
        "norpagent.builtin.sessions.sqlite", "SQLiteSessionManager"),
}


def __getattr__(name: str) -> Any:
    entry = _LAZY_EXPORTS.get(name)
    if entry is None:
        raise AttributeError(
            f"module 'norpagent.builtin.sessions' has no attribute '{name}'")
    import importlib

    module = importlib.import_module(entry[0])
    value = getattr(module, entry[1])
    globals()[name] = value
    return value


__all__ = ["MemorySessionManager", "SQLiteSessionManager"]

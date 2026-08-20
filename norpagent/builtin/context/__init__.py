# Copyright (c) 2026 xingluosama121, MIT Licensed
"""上下文管理组件包：超长上下文的可搜索知识库。

- ``FTS5ContextStore``：SQLite FTS5 实现（零依赖，默认组件名 "fts5"）；
- 替换方式与一切组件相同：实现同一接口后
  ``registry.register_component("context_store", "我的实现", factory)``。

配套工具见 norpagent.builtin.tools.context_tools。

嵌入式优化（v0.9）：FTS5 实现按需懒导入——只 import 本包不会
拉起 sqlite3 依赖（嵌入式 / 无磁盘环境友好）。
"""

from typing import Any

_LAZY_EXPORTS = {
    "FTS5ContextStore": (
        "norpagent.builtin.context.fts5", "FTS5ContextStore"),
}


def __getattr__(name: str) -> Any:
    entry = _LAZY_EXPORTS.get(name)
    if entry is None:
        raise AttributeError(
            f"module 'norpagent.builtin.context' has no attribute '{name}'")
    import importlib

    module = importlib.import_module(entry[0])
    value = getattr(module, entry[1])
    globals()[name] = value
    return value


__all__ = ["FTS5ContextStore"]

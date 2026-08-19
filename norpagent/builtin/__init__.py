# Copyright (c) 2026 xingluosama121, MIT Licensed
"""内置组件包：开箱即用的默认组件，全部通过注册表接入。

``install_defaults(registry)`` 注册全部内置组件；
``install_core(registry)`` 注册嵌入式极简组件（见其文档）；
也可按需挑选：只注册你需要的部分，其余用自定义实现替代。

P2 起模型适配器（openai_compat / anthropic）随包注册，其 SDK
按需懒加载：未安装对应 extras 时，注册与列出均正常，只有
实际调用才会给出明确的安装提示。

P3 新增：
- 上下文管理组件（context_store=fts5）+ context_* 工具；
- 项目管理组件（project_manager=basic）+ project_status 工具；
- 持久化任务调度器（scheduler=persistent）+ task_* 工具；
- 池化沙箱（sandbox=pooled）；
- Web UI 适配器（ui=web，零依赖 HTTP + SSE）。

嵌入式优化（v0.9）：

- 本包的**顶层 import 不再导入** sqlite3 / http.server 相关组件
  （FTS5 上下文库 / SQLite 会话 / 持久化调度器 / Web UI 均为
  install_defaults 内的按需导入 + 模块级 __getattr__ 懒解析）——
  只 `import norpagent.builtin` 不会拉起任何重依赖；
- ``install_core()`` 提供完全避开这些组件的极简装配（无磁盘依赖、
  纯内存），适合嵌入式 / 边缘设备 / 受限环境。
"""

from __future__ import annotations

from typing import Any

from norpagent.builtin.models.mock import MockModelProvider
from norpagent.builtin.models.openai_compat import OpenAICompatProvider
from norpagent.builtin.models.anthropic import AnthropicProvider
from norpagent.builtin.tools import (
    EchoTool,
    GetTimeTool,
    RunPythonTool,
    FileReadTool,
    FileWriteTool,
    FileListTool,
    FileDeleteTool,
    ExecCmdTool,
    WebSearchTool,
    WebFetchTool,
    WebExtractLinksTool,
    ContextAddTool,
    ContextSearchTool,
    ContextListTool,
    ContextDeleteTool,
    ProjectStatusTool,
    TaskSubmitTool,
    TaskListTool,
    TaskStatusTool,
    TaskCancelTool,
)
from norpagent.builtin.sessions.memory import MemorySessionManager
from norpagent.builtin.sandboxes.subprocess import SubprocessSandboxProvider
from norpagent.builtin.scheduler.simple import SimpleTaskScheduler
from norpagent.builtin.ui.console import ConsoleUI

# ── 懒导入映射（PEP 562 __getattr__）：重组件只在真正被取用时导入 ──
_LAZY_EXPORTS = {
    "FTS5ContextStore": (
        "norpagent.builtin.context", "FTS5ContextStore"),
    "BasicProjectManager": (
        "norpagent.builtin.projects", "BasicProjectManager"),
    "SQLiteSessionManager": (
        "norpagent.builtin.sessions.sqlite", "SQLiteSessionManager"),
    "PooledSandboxProvider": (
        "norpagent.builtin.sandboxes.pooled", "PooledSandboxProvider"),
    "PersistentTaskScheduler": (
        "norpagent.builtin.scheduler.persistent", "PersistentTaskScheduler"),
    "WebUI": ("norpagent.builtin.ui.web", "WebUI"),
}


def __getattr__(name: str) -> Any:
    """按名懒导入重组件（兼容 from norpagent.builtin import XXX）。"""
    entry = _LAZY_EXPORTS.get(name)
    if entry is None:
        raise AttributeError(f"module 'norpagent.builtin' has no attribute '{name}'")
    import importlib

    module = importlib.import_module(entry[0])
    value = getattr(module, entry[1])
    globals()[name] = value  # 缓存，后续访问走全局命名空间
    return value


def _all_lazy_entries() -> dict:
    """汇总本包及子包（context / sessions）的全部懒加载映射。"""
    entries = dict(_LAZY_EXPORTS)
    for sub in ("norpagent.builtin.context", "norpagent.builtin.sessions"):
        try:
            mod = __import__(sub, fromlist=["_LAZY_EXPORTS"])
            entries.update(getattr(mod, "_LAZY_EXPORTS", {}))
        except Exception:  # noqa: BLE001 — 子包异常不影响主流程
            pass
    return entries


def list_loaded_lazy_modules() -> list:
    """返回本次进程已实际加载的懒加载模块名（按 sys.modules 命中）。

    供前端 / CLI 启动后打印诊断信息：哪些重组件（sqlite3 / http.server
    相关）真正被取用。未加载的懒加载模块不会出现在结果中。
    """
    import sys

    loaded = {mod for mod, _ in _all_lazy_entries().values() if mod in sys.modules}
    return sorted(loaded)


def install_defaults(registry: Any) -> Any:
    """注册全部内置组件到注册表，返回注册表本身（便于链式调用）。"""
    # 重组件按需导入：只在完整装配时引入 sqlite3 / http.server 依赖
    from norpagent.builtin.context import FTS5ContextStore
    from norpagent.builtin.projects import BasicProjectManager
    from norpagent.builtin.sessions.sqlite import SQLiteSessionManager
    from norpagent.builtin.sandboxes.pooled import PooledSandboxProvider
    from norpagent.builtin.scheduler.persistent import PersistentTaskScheduler
    from norpagent.builtin.ui.web import WebUI

    # 模型
    registry.register_model("mock", MockModelProvider())
    registry.register_model("openai_compat", OpenAICompatProvider())
    registry.register_model("anthropic", AnthropicProvider())
    # 工具（基础）
    registry.register_tool("echo", EchoTool())
    registry.register_tool("get_time", GetTimeTool())
    registry.register_tool("run_python", RunPythonTool())
    # 工具（文件：工作区路径安全约束）
    registry.register_tool("file_read", FileReadTool())
    registry.register_tool("file_write", FileWriteTool())
    registry.register_tool("file_list", FileListTool())
    registry.register_tool("file_delete", FileDeleteTool())
    # 工具（命令：沙箱协议）
    registry.register_tool("exec_cmd", ExecCmdTool())
    # 工具（联网：SSRF 防护，零依赖可用）
    registry.register_tool("web_search", WebSearchTool())
    registry.register_tool("web_fetch", WebFetchTool())
    registry.register_tool("web_extract_links", WebExtractLinksTool())
    # 工具（上下文管理：context_store 组件）
    registry.register_tool("context_add", ContextAddTool())
    registry.register_tool("context_search", ContextSearchTool())
    registry.register_tool("context_list", ContextListTool())
    registry.register_tool("context_delete", ContextDeleteTool())
    # 工具（项目管理：project_manager 组件）
    registry.register_tool("project_status", ProjectStatusTool())
    # 工具（长周期任务协作：scheduler）
    registry.register_tool("task_submit", TaskSubmitTool())
    registry.register_tool("task_list", TaskListTool())
    registry.register_tool("task_status", TaskStatusTool())
    registry.register_tool("task_cancel", TaskCancelTool())
    # 会话
    registry.register_session("memory", MemorySessionManager)
    registry.register_session("sqlite", SQLiteSessionManager)
    # 沙箱
    registry.register_sandbox("subprocess", SubprocessSandboxProvider().create)
    registry.register_sandbox("pooled", PooledSandboxProvider().create)
    # 调度
    registry.register_scheduler("simple", SimpleTaskScheduler)
    registry.register_scheduler("persistent", PersistentTaskScheduler)
    # UI
    registry.register_ui("console", ConsoleUI())
    registry.register_ui("web", WebUI())
    # 通用组件（上下文存储 / 项目管理）
    registry.register_component("context_store", "fts5", FTS5ContextStore)
    registry.register_component("project_manager", "basic", BasicProjectManager)
    return registry


def install_core(registry: Any) -> Any:
    """嵌入式极简装配：只注册运行 Agent 最小闭环所需的组件。

    与 ``install_defaults`` 的区别（嵌入式 / 边缘 / 低资源场景）：

    - **零磁盘依赖**：不导入 FTS5 上下文库 / SQLite 会话 / 持久化
      调度器（避开 sqlite3 与文件系统落盘，纯内存运行）；
    - **无 HTTP 组件**：不导入 Web UI（避开 http.server，无监听端口）；
    - **工具最小集**：echo / get_time / run_python + 文件读写工具；
      不注册联网（web_*）、上下文（context_*）、项目管理
      （project_status）与任务协作（task_*）工具；
    - **组件命名空间为空**：context_store / project_manager 均未注册
      ——声明了这些组件的预设（standard 等）无法在此注册表上装配，
      请搭配 embedded 预设或自建最小 Preset 使用。

    用法::

        reg = Registry()
        install_core(reg)
        from norpagent.modes import build_embedded_preset
        reg.register_preset(build_embedded_preset())
        agent = AgentRuntime(reg, preset="embedded")
        agent.run("你好")
    """
    # 工具（嵌入式最小集：无联网 / 无上下文库 / 无任务协作）
    registry.register_model("mock", MockModelProvider())
    registry.register_model("openai_compat", OpenAICompatProvider())
    registry.register_tool("echo", EchoTool())
    registry.register_tool("get_time", GetTimeTool())
    registry.register_tool("run_python", RunPythonTool())
    registry.register_tool("file_read", FileReadTool())
    registry.register_tool("file_write", FileWriteTool())
    registry.register_tool("file_list", FileListTool())
    registry.register_tool("file_delete", FileDeleteTool())
    # 会话 / 沙箱 / 调度：全部纯内存、无落盘组件
    registry.register_session("memory", MemorySessionManager)
    registry.register_sandbox("subprocess", SubprocessSandboxProvider().create)
    registry.register_scheduler("simple", SimpleTaskScheduler)
    # UI：仅控制台渲染器（无 HTTP 服务）
    registry.register_ui("console", ConsoleUI())
    return registry


__all__ = [
    "install_defaults",
    "install_core",
    "MockModelProvider",
    "OpenAICompatProvider",
    "AnthropicProvider",
    "EchoTool",
    "GetTimeTool",
    "RunPythonTool",
    "FileReadTool",
    "FileWriteTool",
    "FileListTool",
    "FileDeleteTool",
    "ExecCmdTool",
    "WebSearchTool",
    "WebFetchTool",
    "WebExtractLinksTool",
    "ContextAddTool",
    "ContextSearchTool",
    "ContextListTool",
    "ContextDeleteTool",
    "ProjectStatusTool",
    "TaskSubmitTool",
    "TaskListTool",
    "TaskStatusTool",
    "TaskCancelTool",
    "FTS5ContextStore",
    "BasicProjectManager",
    "MemorySessionManager",
    "SQLiteSessionManager",
    "SubprocessSandboxProvider",
    "PooledSandboxProvider",
    "SimpleTaskScheduler",
    "PersistentTaskScheduler",
    "ConsoleUI",
    "WebUI",
]

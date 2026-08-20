# Copyright (c) 2026 xingluosama121, MIT Licensed
"""内置工具集。

P1：echo / get_time / run_python（PTC 雏形）。
P2：file_read / file_write / file_list / file_delete（工作区安全约束）、
    exec_cmd（沙箱协议）、web_search / web_fetch / web_extract_links
    （SSRF 防护 + 零依赖可用）。
P3：context_add / context_search / context_list / context_delete（上下文管理）、
    project_status（项目管理）、
    task_submit / task_list / task_status / task_cancel（长周期任务协作）。
"""

from norpagent.builtin.tools.echo import EchoTool
from norpagent.builtin.tools.clock import GetTimeTool
from norpagent.builtin.tools.run_python import RunPythonTool
from norpagent.builtin.tools.file_io import (
    FileReadTool,
    FileWriteTool,
    FileListTool,
    FileDeleteTool,
)
from norpagent.builtin.tools.exec_cmd import ExecCmdTool
from norpagent.builtin.tools.web import (
    WebSearchTool,
    WebFetchTool,
    WebExtractLinksTool,
    is_private_url,
)
from norpagent.builtin.tools.context_tools import (
    ContextAddTool,
    ContextSearchTool,
    ContextListTool,
    ContextDeleteTool,
)
from norpagent.builtin.tools.project_tools import ProjectStatusTool
from norpagent.builtin.tools.task_tools import (
    TaskSubmitTool,
    TaskListTool,
    TaskStatusTool,
    TaskCancelTool,
)

__all__ = [
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
    "is_private_url",
    "ContextAddTool",
    "ContextSearchTool",
    "ContextListTool",
    "ContextDeleteTool",
    "ProjectStatusTool",
    "TaskSubmitTool",
    "TaskListTool",
    "TaskStatusTool",
    "TaskCancelTool",
]

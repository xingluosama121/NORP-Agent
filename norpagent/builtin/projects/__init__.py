# Copyright (c) 2026 xingluosama121, MIT Licensed
"""项目管理组件包：工作区元数据 / 扫描 / git 状态。

- ``BasicProjectManager``：JSON 元数据 + 目录扫描（默认组件名 "basic"）；
- 配套工具见 norpagent.builtin.tools.project_tools。
"""

from norpagent.builtin.projects.basic import BasicProjectManager

__all__ = ["BasicProjectManager"]

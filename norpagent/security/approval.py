# Copyright (c) 2026 xingluosama121, MIT Licensed
"""人工审批安全层（纯决策，零依赖）。

迁移自现有应用 plugin_system.approval，职责不变：

- 原生工具确认：native_confirm_enabled / write / delete / exec，
  按「工具名 → 级别」映射（write_file → WRITE，delete_file → DELETE，
  exec_cmd → EXEC 等，与现有应用 _TOOL_LEVELS 一致）；
- 插件工具调用审批：approval_enabled 总开关 + 插件 APPROVAL_HINTS
  精细控制（approval="none" 免审批，默认走总开关）。

本模块只做「是否需要审批」的决策，不涉及 UI 交互：
实际弹窗由 UI 适配器通过 ``ctx.ask_user`` 完成。
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Optional, Tuple


class ApprovalLevel(Enum):
    NONE = "none"
    WRITE = "write"
    DELETE = "delete"
    EXEC = "exec"
    PLUGIN = "plugin"


# 原生工具 → 审批级别映射（与现有应用默认策略一致）
_TOOL_LEVELS: Dict[str, ApprovalLevel] = {
    "file_write": ApprovalLevel.WRITE,
    "file_delete": ApprovalLevel.DELETE,
    "exec_cmd": ApprovalLevel.EXEC,
    # 现有应用旧工具名兼容（write_file / delete_file / install_dependency 等）
    "write_file": ApprovalLevel.WRITE,
    "replace_in_file": ApprovalLevel.WRITE,
    "surgical_replace": ApprovalLevel.WRITE,
    "copy_file": ApprovalLevel.WRITE,
    "move_file": ApprovalLevel.WRITE,
    "init_project": ApprovalLevel.WRITE,
    "delete_file": ApprovalLevel.DELETE,
    "install_dependency": ApprovalLevel.EXEC,
    "git_commit": ApprovalLevel.EXEC,
    "open_file": ApprovalLevel.EXEC,
}


class ApprovalPolicy:
    """人工审批策略（原生工具确认 + 插件工具调用审批）。"""

    def __init__(self, config: Optional[dict] = None,
                 tool_hints: Optional[dict] = None) -> None:
        config = config or {}
        legacy = config.get("confirm_write_delete", True)
        self.native_enabled = bool(config.get("native_confirm_enabled", legacy))
        self.native_write = bool(config.get("native_confirm_write", legacy))
        self.native_delete = bool(config.get("native_confirm_delete", legacy))
        self.native_exec = bool(config.get("native_confirm_exec", True))
        self.plugin_enabled = bool(config.get("approval_enabled", True))
        self._tool_hints = tool_hints or {}

    def set_tool_hints(self, tool_hints: Optional[dict]) -> None:
        """刷新插件工具审批提示（每次确认前由调用方更新）。"""
        self._tool_hints = tool_hints or {}

    def level_of(self, tool_name: str) -> ApprovalLevel:
        """返回原生工具对应的审批级别。"""
        return _TOOL_LEVELS.get(tool_name, ApprovalLevel.NONE)

    def requires_approval(self, tool_name: str,
                          is_plugin: bool = False) -> Tuple[bool, ApprovalLevel]:
        """判断某个工具调用是否需要人工审批。

        返回 (是否需要审批, 审批级别)。
        """
        if is_plugin:
            hint = self._tool_hints.get(tool_name)
            if isinstance(hint, dict) and hint.get("approval") == "none":
                return False, ApprovalLevel.NONE
            return self.plugin_enabled, ApprovalLevel.PLUGIN

        level = self.level_of(tool_name)
        if level == ApprovalLevel.NONE:
            return False, level
        if not self.native_enabled:
            return False, level
        if level == ApprovalLevel.WRITE:
            return self.native_write, level
        if level == ApprovalLevel.DELETE:
            return self.native_delete, level
        if level == ApprovalLevel.EXEC:
            return self.native_exec, level
        return False, level

    def get_plugin_config(self) -> dict:
        return {"approval_enabled": self.plugin_enabled}

    def get_native_config(self) -> dict:
        return {
            "enabled": self.native_enabled,
            "write": self.native_write,
            "delete": self.native_delete,
            "exec": self.native_exec,
        }

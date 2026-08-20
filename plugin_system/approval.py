# Vibe Coding Agent - 人工审批安全层（P0-8）
# Copyright (c) 2026 xingluosama
#
# 职责拆分（P0-8 修订）：
#   - 原生工具确认（设置面板）：native_confirm_enabled / native_confirm_write /
#     native_confirm_delete / native_confirm_exec。按「工具名 → 级别」映射，
#     仅作用于原生内置工具（write_file / delete_file / exec_cmd 等）。
#   - 插件工具调用审批（插件控制面板）：approval_enabled 总开关。插件工具名由
#     插件自定义、无法预映射级别，因此开启后所有插件工具调用均需人工确认。
#
# 该模块只做「是否需要审批」的纯决策，不涉及 UI 交互；
# 实际弹窗由 async_loop 通过 WC: 事件 + 用户输入完成。

from enum import Enum
from typing import Optional, Tuple


class ApprovalLevel(Enum):
    """审批级别。"""
    NONE = "none"       # 无需审批
    WRITE = "write"     # 写入 / 替换文件
    DELETE = "delete"   # 删除文件 / 目录
    EXEC = "exec"       # 命令 / 子进程执行
    PLUGIN = "plugin"   # 插件工具调用（插件控制面板）


# 原生工具 → 审批级别映射（默认策略）
_TOOL_LEVELS = {
    "write_file": ApprovalLevel.WRITE,
    "replace_in_file": ApprovalLevel.WRITE,
    "surgical_replace": ApprovalLevel.WRITE,
    "copy_file": ApprovalLevel.WRITE,
    "move_file": ApprovalLevel.WRITE,
    "init_project": ApprovalLevel.WRITE,

    "delete_file": ApprovalLevel.DELETE,

    "exec_cmd": ApprovalLevel.EXEC,
    "install_dependency": ApprovalLevel.EXEC,
    "git_commit": ApprovalLevel.EXEC,
    "open_file": ApprovalLevel.EXEC,
}


class ApprovalPolicy:
    """人工审批策略（原生工具确认 + 插件工具调用审批）。

    参数
    ----
    config : dict
        config.json 全量配置。相关键：
        - 原生工具确认（设置面板）：
            ``native_confirm_enabled``   : bool（默认 True，总开关）
            ``native_confirm_write``     : bool（默认 True）
            ``native_confirm_delete``    : bool（默认 True）
            ``native_confirm_exec``      : bool（默认 True）
            兼容旧键 ``confirm_write_delete``（向导 / 旧前端写入，
            未显式配置 native_confirm_* 时作为 write/delete/enabled 的回退值）。
        - 插件工具调用审批（插件控制面板）：
            ``approval_enabled``         : bool（默认 True，总开关）
    """

    def __init__(self, config: Optional[dict] = None,
                 tool_hints: Optional[dict] = None):
        config = config or {}
        # ── 原生工具确认（设置面板）──
        legacy = config.get("confirm_write_delete", True)
        self.native_enabled = bool(config.get("native_confirm_enabled", legacy))
        self.native_write = bool(config.get("native_confirm_write", legacy))
        self.native_delete = bool(config.get("native_confirm_delete", legacy))
        self.native_exec = bool(config.get("native_confirm_exec", True))
        # ── 插件工具调用审批（插件控制面板）──
        self.plugin_enabled = bool(config.get("approval_enabled", True))
        # ── 插件工具审批提示（APPROVAL_HINTS，按工具名精细控制）──
        # 格式：{tool_name: {"approval": "none"|"plugin", "risk": "L0"~"L3"}}
        #  - approval="none"   ：该工具调用不弹审批（如只读工具）
        #  - approval="plugin" ：走 approval_enabled 总开关（默认行为）
        #  - 未声明的工具       ：默认走总开关（向后兼容）
        self._tool_hints = tool_hints or {}

    def set_tool_hints(self, tool_hints: Optional[dict]) -> None:
        """刷新插件工具审批提示（每次确认前由调用方更新）。"""
        self._tool_hints = tool_hints or {}

    def level_of(self, tool_name: str) -> ApprovalLevel:
        """返回原生工具对应的审批级别。"""
        return _TOOL_LEVELS.get(tool_name, ApprovalLevel.NONE)

    def requires_approval(self, tool_name: str, is_plugin: bool = False) -> Tuple[bool, ApprovalLevel]:
        """判断某个工具调用是否需要人工审批。

        参数
        ----
        is_plugin : bool
            True 表示该工具来自插件系统，走「插件工具调用审批」
            （插件控制面板配置）；False 表示原生内置工具，走
            「原生工具确认」（设置面板配置）。

        返回 (是否需要审批, 审批级别)。
        """
        if is_plugin:
            # 插件工具名由插件自定义，无法预映射级别：默认总开关开启即全审。
            # 若插件声明了 APPROVAL_HINTS，则按 hint 精细控制：
            #   "none"   → 免审批（插件自认只读/低风险，如 L0/L1）
            #   "plugin" → 按总开关（默认行为）
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
        """插件工具调用审批配置（插件控制面板）。"""
        return {
            "approval_enabled": self.plugin_enabled,
        }

    def get_native_config(self) -> dict:
        """原生工具确认配置（设置面板）。"""
        return {
            "enabled": self.native_enabled,
            "write": self.native_write,
            "delete": self.native_delete,
            "exec": self.native_exec,
        }

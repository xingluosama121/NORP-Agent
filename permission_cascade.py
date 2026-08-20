# Vibe Coding Agent - 权限级联 (Permission Cascade)
# 解决插件越权冲突：层级权限模型，子操作继承父级权限并受其约束
# Copyright (c) 2026 xingluosama

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


class Permission(Enum):
    """权限原子。"""
    FILE_READ = "file_read"           # 读文件
    FILE_WRITE = "file_write"         # 写文件
    FILE_DELETE = "file_delete"       # 删文件
    FILE_LIST = "file_list"           # 列出目录
    PROCESS_EXEC = "process_exec"     # 执行子进程
    PROCESS_SHELL = "process_shell"   # 执行 shell 命令
    NETWORK_OUT = "network_out"       # 出站网络
    NETWORK_IN = "network_in"         # 入站网络
    SYSTEM_INFO = "system_info"       # 读取系统信息
    PLUGIN_CALL = "plugin_call"       # 调用其他插件


class PermissionLevel(Enum):
    """权限级别。"""
    SYSTEM = 0       # 系统级：内置工具，最高权限
    TERMINAL = 1     # 终端命令
    PLUGIN_ROOT = 2  # 插件根级
    PLUGIN_CHILD = 3 # 插件子调用
    NONE = 99        # 无权限


@dataclass
class PermissionSet:
    """一组权限及其级别。"""
    permissions: Set[Permission] = field(default_factory=set)
    level: PermissionLevel = PermissionLevel.NONE
    max_path_depth: int = -1        # -1 表示无限制
    path_whitelist: Set[str] = field(default_factory=set)
    path_blacklist: Set[str] = field(default_factory=set)

    def has(self, perm: Permission) -> bool:
        return perm in self.permissions

    def add(self, perm: Permission):
        self.permissions.add(perm)

    def remove(self, perm: Permission):
        self.permissions.discard(perm)

    def copy(self) -> "PermissionSet":
        return PermissionSet(
            permissions=set(self.permissions),
            level=self.level,
            max_path_depth=self.max_path_depth,
            path_whitelist=set(self.path_whitelist),
            path_blacklist=set(self.path_blacklist),
        )


# ── 内置权限预设 ──

SYSTEM_PERMISSIONS = PermissionSet(
    permissions={
        Permission.FILE_READ, Permission.FILE_WRITE, Permission.FILE_DELETE,
        Permission.FILE_LIST, Permission.PROCESS_EXEC, Permission.PROCESS_SHELL,
        Permission.NETWORK_OUT, Permission.NETWORK_IN, Permission.SYSTEM_INFO,
        Permission.PLUGIN_CALL,
    },
    level=PermissionLevel.SYSTEM,
)

TERMINAL_PERMISSIONS = PermissionSet(
    permissions={
        Permission.FILE_READ, Permission.FILE_WRITE, Permission.FILE_DELETE,
        Permission.FILE_LIST, Permission.PROCESS_EXEC, Permission.PROCESS_SHELL,
        Permission.NETWORK_OUT,
    },
    level=PermissionLevel.TERMINAL,
)

PLUGIN_DEFAULT_PERMISSIONS = PermissionSet(
    permissions={
        Permission.FILE_READ, Permission.FILE_LIST,
    },
    level=PermissionLevel.PLUGIN_ROOT,
)


class PermissionCascade:
    """权限级联管理器。

    核心规则：
    1. 子操作权限 = 父级权限 ∩ 子级声明权限（取交集）
    2. 任何操作不能超出其父级的权限范围
    3. 插件之间调用时，被调用方权限受调用方权限约束
    4. 越权操作立即拒绝，记录审计日志
    """

    def __init__(self):
        # 权限栈：每个元素是 (主体ID, PermissionSet)
        self._stack: List[PermissionSet] = [SYSTEM_PERMISSIONS.copy()]
        # 插件权限注册表
        self._plugin_permissions: Dict[str, PermissionSet] = {}
        # 当前活跃主体
        self._current_subject: str = "system"
        # 审计日志
        self._audit_log: List[str] = []

    # ── 权限栈操作 ──

    def push(self, subject_id: str, permissions: PermissionSet):
        """压入新的权限上下文（进入子操作）。

        级联规则：新权限 = 当前栈顶权限 ∩ 新声明权限
        """
        current = self._stack[-1] if self._stack else SYSTEM_PERMISSIONS
        cascaded = PermissionSet(
            permissions=current.permissions & permissions.permissions,
            level=permissions.level,
            max_path_depth=min(
                current.max_path_depth if current.max_path_depth >= 0 else 999,
                permissions.max_path_depth if permissions.max_path_depth >= 0 else 999,
            ),
            path_whitelist=current.path_whitelist & permissions.path_whitelist
                           if current.path_whitelist and permissions.path_whitelist
                           else (current.path_whitelist or permissions.path_whitelist),
            path_blacklist=current.path_blacklist | permissions.path_blacklist,
        )
        self._stack.append(cascaded)
        self._current_subject = subject_id
        return cascaded

    def pop(self) -> Optional[PermissionSet]:
        """弹出当前权限上下文（返回父级）。"""
        if len(self._stack) > 1:
            popped = self._stack.pop()
            self._current_subject = "system" if len(self._stack) == 1 else self._current_subject
            return popped
        return None

    @property
    def current(self) -> PermissionSet:
        """获取当前有效的权限集。"""
        return self._stack[-1] if self._stack else SYSTEM_PERMISSIONS

    # ── 权限检查 ──

    def check(self, perm: Permission, path: str = "") -> bool:
        """检查当前上下文是否有指定权限。

        Args:
            perm: 要检查的权限
            path: 可选，操作路径（用于路径白名单/黑名单检查）

        Returns:
            True 表示允许，False 表示拒绝
        """
        current = self.current

        # 基础权限检查
        if perm not in current.permissions:
            self._audit(f"DENIED: {perm.value} (not in current permissions: {current.permissions})")
            return False

        # 路径白名单检查
        if path and current.path_whitelist:
            allowed = any(path.startswith(p) for p in current.path_whitelist)
            if not allowed:
                self._audit(f"DENIED: path '{path}' not in whitelist {current.path_whitelist}")
                return False

        # 路径黑名单检查
        if path and current.path_blacklist:
            denied = any(path.startswith(p) for p in current.path_blacklist)
            if denied:
                self._audit(f"DENIED: path '{path}' in blacklist {current.path_blacklist}")
                return False

        return True

    def check_or_raise(self, perm: Permission, path: str = ""):
        """检查权限，不通过则抛出异常。"""
        if not self.check(perm, path):
            raise PermissionError(
                f"Permission denied: {perm.value} for subject '{self._current_subject}'"
                + (f" on path '{path}'" if path else "")
            )

    # ── 插件权限注册 ──

    def register_plugin(self, plugin_id: str, permissions: PermissionSet):
        """注册插件的权限声明。"""
        self._plugin_permissions[plugin_id] = permissions

    def unregister_plugin(self, plugin_id: str):
        """注销插件的权限。"""
        self._plugin_permissions.pop(plugin_id, None)

    def get_plugin_permissions(self, plugin_id: str) -> Optional[PermissionSet]:
        """获取插件的权限声明。"""
        return self._plugin_permissions.get(plugin_id)

    # ── 级联调用 ──

    def plugin_call_push(self, caller_id: str, callee_id: str) -> PermissionSet:
        """插件间调用：被调用方权限 = 调用方权限 ∩ 被调用方声明权限。"""
        callee_perms = self._plugin_permissions.get(
            callee_id, PLUGIN_DEFAULT_PERMISSIONS
        )
        return self.push(f"{caller_id}->{callee_id}", callee_perms)

    # ── 审计 ──

    def _audit(self, msg: str):
        self._audit_log.append(msg)
        # 最多保留 200 条
        if len(self._audit_log) > 200:
            self._audit_log = self._audit_log[-200:]

    def get_audit_log(self) -> List[str]:
        return list(self._audit_log)

    def clear_audit_log(self):
        self._audit_log.clear()

    # ── 上下文管理器支持 ──

    def __enter__(self):
        return self

    def __exit__(self, *args):
        while len(self._stack) > 1:
            self.pop()


# ── 全局单例 ──
_cascade_instance: Optional[PermissionCascade] = None


def get_permission_cascade() -> PermissionCascade:
    global _cascade_instance
    if _cascade_instance is None:
        _cascade_instance = PermissionCascade()
    return _cascade_instance

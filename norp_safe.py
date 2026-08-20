# Vibe Coding Agent - NORP 安全系统 (NORP Safe)
# 统一安全拦截：危险命令、UAC提权、路径越界、JSON拦截日志
# Copyright (c) 2026 xingluosama

"""
NORP Safe — 核心安全模块。

功能：
  1. 危险命令拦截 — 递归删除根目录/系统目录、格式化磁盘、破坏性操作
  2. UAC 提权拦截 — Win32 API 调用、runas、ShellExecute、token 提升
  3. 路径越界拦截 — 环境变量展开、.. 穿越、系统目录访问
  4. 拦截日志 — 写入 NORPsafe.json（JSON 格式）

集成方式：
  from norp_safe import get_norp_safe
  nsp = get_norp_safe(app_dir)
  nsp.check_command(cmd)      # 返回 (blocked, reason)
  nsp.check_path(path)         # 返回 (blocked, reason)
  nsp.check_uac(cmd)           # 返回 (blocked, reason)
"""

import os
import re
import json
import platform
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Tuple
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════
#  威胁等级
# ═══════════════════════════════════════════════════════════════════

class ThreatLevel(Enum):
    CRITICAL = "critical"   # 毁灭级：磁盘格式化、根目录递归删除、UAC提权
    HIGH = "high"           # 高危：系统目录删除、注册表操作、Win32 API 调用
    MEDIUM = "medium"       # 中危：路径穿越、环境变量读取、非工作区路径访问
    LOW = "low"             # 低危：可疑模式但不明确


class EventType(Enum):
    DANGEROUS_COMMAND = "dangerous_command"
    PATH_TRAVERSAL = "path_traversal"
    UAC_ELEVATION = "uac_elevation"
    PERMISSION_DENIED = "permission_denied"


@dataclass
class InterceptResult:
    """拦截结果。"""
    blocked: bool
    threat_level: ThreatLevel
    event_type: EventType
    reason: str
    details: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
#  危险命令模式库
# ═══════════════════════════════════════════════════════════════════

# 高危系统目录（Windows + Linux）
# NOTE: 不能用 r"..." raw string 表示末尾带 \ 的路径（Python raw string 不允许以 \ 结尾）
_CRITICAL_DIRS_WIN = [
    "C:\\", "C:\\Windows", "C:\\Windows\\System32", "C:\\Windows\\SysWOW64",
    "C:\\Program Files", "C:\\Program Files (x86)", "C:\\ProgramData",
    "C:\\Users", "C:\\boot", "C:\\EFI",
    "D:\\", "E:\\", "F:\\", "G:\\",
]

_CRITICAL_DIRS_UNIX = [
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64",
    "/media", "/mnt", "/opt", "/proc", "/root", "/run", "/sbin",
    "/srv", "/sys", "/tmp", "/usr", "/var",
]

# Windows 递归删除高危模式
_RECURSIVE_DELETE_WIN = re.compile(
    r'(?:del|erase|rd|rmdir)\s+.*(?:/s|/q|/f).*\s+([A-Za-z]:\\(?:Windows|Program\s*Files|ProgramData|Users|boot|EFI)?\\?)',
    re.IGNORECASE
)

# Windows 递归删除根目录
_RECURSIVE_ROOT_WIN = re.compile(
    r'(?:del|erase|rd|rmdir)\s+.*/s.*([A-Za-z]:\\\S*)',
    re.IGNORECASE
)

# Linux 递归删除根目录
_RECURSIVE_ROOT_UNIX = re.compile(
    r'rm\s+.*(?:-r|-rf|--recursive).*\s/(?:\s|$|\*)',
    re.IGNORECASE
)

# 格式化磁盘
_FORMAT_DISK = re.compile(
    r'(?:format\s+[A-Za-z]:|mkfs\.|mke2fs|newfs)',
    re.IGNORECASE
)

# 磁盘操作
_DISKPART = re.compile(
    r'(?:diskpart|fdisk|parted|gparted|gdisk)',
    re.IGNORECASE
)

# 启动配置破坏
_BCDEDIT = re.compile(
    r'(?:bcdedit|bootcfg)\s+.*(?:/delete|/import|/export)',
    re.IGNORECASE
)

# 注册表操作
_REGISTRY = re.compile(
    r'(?:reg\s+(?:delete|add|import|export)|regedit\s+/s)',
    re.IGNORECASE
)

# dd 破坏性操作
_DD_DANGEROUS = re.compile(
    r'dd\s+if=.*of=/(?:dev/sd|dev/hd|dev/nvme|dev/mmcblk)',
    re.IGNORECASE
)

# 系统文件删除
_SYSTEM_FILE_DELETE = re.compile(
    r'(?:del|rm)\s+.*(?:\.sys|\.dll|\.exe|\.drv)\s*$',
    re.IGNORECASE
)

# 批量删除危险扩展名
_DANGEROUS_EXT_DELETE = re.compile(
    r'(?:del|rm)\s+.*\*\.[a-z]{2,4}\s*/s',
    re.IGNORECASE
)


def _build_dangerous_cmd_patterns() -> List[Tuple[re.Pattern, str, ThreatLevel]]:
    """构建危险命令模式列表。"""
    patterns = []

    # ── 递归删除根目录 ──
    patterns.append((_RECURSIVE_DELETE_WIN, "Windows递归删除系统目录", ThreatLevel.CRITICAL))
    patterns.append((_RECURSIVE_ROOT_WIN, "Windows递归删除根目录", ThreatLevel.CRITICAL))
    patterns.append((_RECURSIVE_ROOT_UNIX, "Linux递归删除根目录", ThreatLevel.CRITICAL))

    # ── 传统危险命令 ──
    patterns.append((re.compile(r'(?:sudo|su\s+-).*rm\s+.*-rf?\s+/', re.IGNORECASE),
                      "sudo rm -rf / 危险操作", ThreatLevel.CRITICAL))
    patterns.append((re.compile(r'(?:sudo|su\s+-).*rm\s+.*-rf?\s+/(?:bin|boot|dev|etc|lib|opt|sbin|usr|var|sys|proc)', re.IGNORECASE),
                      "sudo 递归删除系统目录", ThreatLevel.CRITICAL))

    # ── 格式化磁盘 ──
    patterns.append((_FORMAT_DISK, "格式化磁盘操作", ThreatLevel.CRITICAL))

    # ── 磁盘分区操作 ──
    patterns.append((_DISKPART, "磁盘分区操作", ThreatLevel.HIGH))

    # ── 启动配置破坏 ──
    patterns.append((_BCDEDIT, "启动配置破坏", ThreatLevel.CRITICAL))

    # ── 注册表操作 ──
    patterns.append((_REGISTRY, "注册表操作", ThreatLevel.HIGH))

    # ── dd 破坏磁盘 ──
    patterns.append((_DD_DANGEROUS, "dd 写入磁盘设备", ThreatLevel.CRITICAL))

    # ── 系统文件删除 ──
    patterns.append((_SYSTEM_FILE_DELETE, "删除系统文件(.sys/.dll/.exe)", ThreatLevel.HIGH))

    # ── 批量递归删除 ──
    patterns.append((_DANGEROUS_EXT_DELETE, "批量递归删除文件", ThreatLevel.HIGH))

    # ── shutdown / reboot ──
    patterns.append((re.compile(r'(?:shutdown|reboot|halt|poweroff|init\s+[06])', re.IGNORECASE),
                      "系统关机/重启命令", ThreatLevel.HIGH))

    # ── chmod 777 危险目录 ──
    patterns.append((re.compile(r'chmod\s+(?:777|o\+w)\s+/(?:bin|boot|etc|lib|sbin|usr)', re.IGNORECASE),
                      "chmod 危险权限更改", ThreatLevel.HIGH))

    # ── 覆盖系统文件 ──
    patterns.append((re.compile(r'>\s*/(?:dev/sda|dev/hda|dev/nvme)', re.IGNORECASE),
                      "重定向覆盖磁盘设备", ThreatLevel.CRITICAL))

    # ── wget/curl 管道到 shell ──
    patterns.append((re.compile(r'(?:wget|curl)\s+.*\|\s*(?:bash|sh|zsh)', re.IGNORECASE),
                      "curl/wget 管道到 shell（可能下载执行恶意脚本）", ThreatLevel.HIGH))

    # ── PowerShell 危险操作 ──
    patterns.append((re.compile(r'Remove-Item\s+.*-Recurse\s+.*-Path\s+[A-Za-z]:\\', re.IGNORECASE),
                      "PowerShell 递归删除", ThreatLevel.CRITICAL))
    # PowerShell 别名: ri -r -fo C:\, ri -Recurse -Force C:\ 等
    patterns.append((re.compile(r'\bri\s+.*(?:-r(?:ecurse)?|-fo(?:rce)?)\s+[A-Za-z]:\\', re.IGNORECASE),
                      "PowerShell ri 递归删除", ThreatLevel.CRITICAL))
    # PowerShell rd/rmdir 别名也覆盖
    patterns.append((re.compile(r'\b(?:rd|rmdir)\s+.*-Recurse\s+[A-Za-z]:\\', re.IGNORECASE),
                      "PowerShell rd 递归删除", ThreatLevel.CRITICAL))
    patterns.append((re.compile(r'Set-ExecutionPolicy\s+Unrestricted', re.IGNORECASE),
                      "PowerShell 执行策略改为无限制", ThreatLevel.HIGH))
    # PowerShell Invoke-Expression / iex 下载执行
    patterns.append((re.compile(r'(?:Invoke-Expression|iex)\s+.*(?:Net\.WebClient|Invoke-WebRequest|iwr)', re.IGNORECASE),
                      "PowerShell 远程下载执行", ThreatLevel.HIGH))

    return patterns


DANGEROUS_CMD_PATTERNS: List[Tuple[re.Pattern, str, ThreatLevel]] = _build_dangerous_cmd_patterns()


# ═══════════════════════════════════════════════════════════════════
#  UAC 提权模式库
# ═══════════════════════════════════════════════════════════════════

def _build_uac_patterns() -> List[Tuple[re.Pattern, str, ThreatLevel]]:
    """构建 UAC 提权模式列表。"""
    patterns = []

    # ── ShellExecute Win32 API ──
    patterns.append((re.compile(r'(?:shell32|shell)\.ShellExecute[WA]?\s*\(', re.IGNORECASE),
                      "Win32 ShellExecute API 调用", ThreatLevel.CRITICAL))
    patterns.append((re.compile(r'ShellExecute[WA]?\s*\(', re.IGNORECASE),
                      "ShellExecute 函数调用", ThreatLevel.CRITICAL))

    # ── Shell.Application COM 对象 ──
    patterns.append((re.compile(r'CreateObject\s*\(\s*["\']Shell\.Application["\']', re.IGNORECASE),
                      "Shell.Application COM 对象创建", ThreatLevel.CRITICAL))
    patterns.append((re.compile(r'New-Object\s+.*-ComObject\s+Shell\.Application', re.IGNORECASE),
                      "PowerShell Shell.Application COM", ThreatLevel.CRITICAL))

    # ── runas 提权 ──
    patterns.append((re.compile(r'\brunas\b', re.IGNORECASE),
                      "runas 提权命令", ThreatLevel.CRITICAL))
    patterns.append((re.compile(r'Start-Process\s+.*-Verb\s+run[aA]s', re.IGNORECASE),
                      "PowerShell Start-Process runAs", ThreatLevel.CRITICAL))

    # ── UAC 绕过 ──
    patterns.append((re.compile(r'(?:bypass|disable|绕过)\s*(?:UAC|uac)', re.IGNORECASE),
                      "UAC 绕过尝试", ThreatLevel.CRITICAL))
    patterns.append((re.compile(r'bypassuac', re.IGNORECASE),
                      "UAC 绕过技术", ThreatLevel.CRITICAL))

    # ── Token 提升 ──
    patterns.append((re.compile(r'(?:token|privilege)\s*(?:elevation|escalation|提升)', re.IGNORECASE),
                      "Token 权限提升", ThreatLevel.CRITICAL))
    patterns.append((re.compile(r'SeDebugPrivilege|SeTakeOwnershipPrivilege|SeBackupPrivilege|SeRestorePrivilege', re.IGNORECASE),
                      "危险特权请求", ThreatLevel.CRITICAL))

    # ── Win32 API 危险调用 ──
    patterns.append((re.compile(r'(?:kernel32|ntdll|advapi32|user32)\.(?:dll|DLL)', re.IGNORECASE),
                      "Win32 DLL 直接调用", ThreatLevel.HIGH))
    patterns.append((re.compile(r'c\s*types\s*\.\s*windll', re.IGNORECASE),
                      "Python ctypes Win32 DLL 调用", ThreatLevel.HIGH))

    # ── 进程注入 ──
    patterns.append((re.compile(r'(?:WriteProcessMemory|VirtualAllocEx|CreateRemoteThread|NtCreateThreadEx)', re.IGNORECASE),
                      "进程注入 API", ThreatLevel.CRITICAL))

    # ── 服务操作 ──
    patterns.append((re.compile(r'sc\s+(?:create|delete|config)\s+.*binPath', re.IGNORECASE),
                      "Windows 服务创建/修改", ThreatLevel.HIGH))
    patterns.append((re.compile(r'New-Service\s+.*-BinaryPathName', re.IGNORECASE),
                      "PowerShell 新服务创建", ThreatLevel.HIGH))

    return patterns


UAC_PATTERNS: List[Tuple[re.Pattern, str, ThreatLevel]] = _build_uac_patterns()


# ═══════════════════════════════════════════════════════════════════
#  路径越界模式库
# ═══════════════════════════════════════════════════════════════════

# 环境变量模式（Windows + Unix）
_ENV_VAR_PATTERN = re.compile(r'%[A-Z_]+%|\$[A-Z_]+|\$\{[A-Z_]+\}', re.IGNORECASE)

# Windows 系统路径前缀（禁止在工作区操作中访问）
_WINDOWS_SYSTEM_PATHS = [
    r"C:\Windows", r"C:\Windows\System32", r"C:\Windows\SysWOW64",
    r"C:\Program Files", r"C:\Program Files (x86)", r"C:\ProgramData",
    r"C:\Users", r"C:\boot", r"C:\EFI", r"C:\$Recycle.Bin",
    r"C:\System Volume Information", r"C:\Recovery",
    r"%APPDATA%", r"%LOCALAPPDATA%", r"%USERPROFILE%", r"%TEMP%",
    r"%TMP%", r"%PROGRAMFILES%", r"%PROGRAMFILES(X86)%", r"%SYSTEMROOT%",
    r"%SYSTEMDRIVE%", r"%WINDIR%", r"%HOMEDRIVE%", r"%HOMEPATH%",
    r"%ALLUSERSPROFILE%", r"%COMMONPROGRAMFILES%",
]

_UNIX_SYSTEM_PATHS = [
    "/etc/passwd", "/etc/shadow", "/etc/sudoers", "/etc/ssh",
    "/root", "/var/log", "/var/run", "/proc", "/sys",
    "/boot", "/dev", "/lib/modules",
    "$HOME", "${HOME}", "~",
]

# 路径穿越模式
_PATH_TRAVERSAL = re.compile(r'\.\.(?:\/|\\)')


def _expand_env_vars(path: str) -> str:
    """展开环境变量（用于检测）。"""
    expanded = path
    # Windows %VAR%
    for match in re.finditer(r'%([A-Z_]+)%', path, re.IGNORECASE):
        var_name = match.group(1)
        var_value = os.environ.get(var_name, "")
        if var_value:
            expanded = expanded.replace(match.group(0), var_value)
    # Unix $VAR / ${VAR}
    for match in re.finditer(r'\$\{?([A-Z_]+)\}?', path):
        var_name = match.group(1)
        var_value = os.environ.get(var_name, "")
        if var_value:
            expanded = expanded.replace(match.group(0), var_value)
    return os.path.expanduser(expanded)


# ═══════════════════════════════════════════════════════════════════
#  NORP Safe 主类
# ═══════════════════════════════════════════════════════════════════

class NorpSafe:
    """NORP 安全系统 — 统一安全拦截和日志。

    使用方式：
        nsp = NorpSafe(app_dir="%LOCALAPPDATA%\\vibe_agent")
        
        # 检查命令
        result = nsp.check_command("del /f /s /q C:\\")
        if result.blocked:
            print(f"拦截: {result.reason}")
        
        # 检查路径
        result = nsp.check_path("../etc/passwd", workspace_root="H:\\project")
        if result.blocked:
            print(f"拦截: {result.reason}")
        
        # 检查 UAC 提权
        result = nsp.check_uac("shell32.ShellExecuteW(...)")
        if result.blocked:
            print(f"拦截: {result.reason}")
    """

    def __init__(self, app_dir: str = ""):
        self.app_dir = app_dir
        self._log_path = os.path.join(app_dir, "NORPsafe.json") if app_dir else ""
        self._intercept_count = 0
        self._critical_count = 0
        self._enabled = True  # 安全系统默认启用

    # ── 日志路径属性 ──

    @property
    def log_path(self) -> str:
        return self._log_path

    @log_path.setter
    def log_path(self, path: str):
        self._log_path = path

    @property
    def enabled(self) -> bool:
        """安全系统是否启用。"""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    def _bypass(self) -> InterceptResult:
        """安全系统关闭时的空白放行结果。"""
        return InterceptResult(blocked=False, threat_level=ThreatLevel.LOW,
                                event_type=EventType.DANGEROUS_COMMAND, reason="")

    # ═══════════════════════════════════════════════════════════════
    #  命令安全检查
    # ═══════════════════════════════════════════════════════════════

    def check_command(self, cmd: str, task_id: str = "") -> InterceptResult:
        """检查 shell 命令是否包含危险操作。

        分两步：
        1. 先检查 UAC 提权模式（优先级最高）
        2. 再检查危险命令模式

        Args:
            cmd: 要执行的 shell 命令
            task_id: 关联的任务 ID

        Returns:
            InterceptResult: blocked=True 表示应拦截
        """
        if not self._enabled:
            return self._bypass()
        if not cmd or not isinstance(cmd, str):
            return InterceptResult(blocked=False, threat_level=ThreatLevel.LOW,
                                    event_type=EventType.DANGEROUS_COMMAND,
                                    reason="")

        cmd_lower = cmd.lower()

        # ── 第一层：传统危险模式速查 ──
        # 先做最简单的字符串匹配（高性价比）
        quick_blocks = [
            ("sudo rm -rf /", "sudo rm -rf / 根目录递归删除", ThreatLevel.CRITICAL),
            ("del /f /s /q c:\\", "del /f /s /q C:\\ 递归强制删除C盘", ThreatLevel.CRITICAL),
            ("del /f /s /q d:\\", "del /f /s /q D:\\ 递归强制删除D盘", ThreatLevel.CRITICAL),
            ("rm -rf /", "rm -rf / 根目录递归删除", ThreatLevel.CRITICAL),
            ("format c:", "format C: 格式化C盘", ThreatLevel.CRITICAL),
            ("mkfs.", "mkfs 格式化文件系统", ThreatLevel.CRITICAL),
            ("> /dev/sda", "重定向覆盖磁盘设备", ThreatLevel.CRITICAL),
            ("shell32.shellexecute", "Shell32 ShellExecute API 调用", ThreatLevel.CRITICAL),
        ]
        for pattern, reason, level in quick_blocks:
            if pattern in cmd_lower:
                result = InterceptResult(
                    blocked=True, threat_level=level,
                    event_type=EventType.DANGEROUS_COMMAND,
                    reason=f"危险命令拦截: {reason}",
                    details={"command": cmd, "matched_pattern": pattern}
                )
                self._log(result, task_id)
                return result

        # ── 第二层：正则模式匹配 ──
        for pattern, reason, level in DANGEROUS_CMD_PATTERNS:
            match = pattern.search(cmd)
            if match:
                matched = match.group(0)
                result = InterceptResult(
                    blocked=True, threat_level=level,
                    event_type=EventType.DANGEROUS_COMMAND,
                    reason=f"危险命令拦截: {reason}（匹配: {matched[:80]}）",
                    details={"command": cmd, "matched_pattern": pattern.pattern, "matched_text": matched}
                )
                self._log(result, task_id)
                return result

        return InterceptResult(blocked=False, threat_level=ThreatLevel.LOW,
                                event_type=EventType.DANGEROUS_COMMAND,
                                reason="")

    # ═══════════════════════════════════════════════════════════════
    #  UAC 提权检查
    # ═══════════════════════════════════════════════════════════════

    def check_uac(self, cmd: str, task_id: str = "") -> InterceptResult:
        """检查命令是否包含 UAC 提权/绕过尝试。

        在命令执行前调用。如果命令包含 Win32 API 调用、runas、
        ShellExecute 等提权技术，立即拦截。

        Args:
            cmd: 要执行的 shell 命令
            task_id: 关联的任务 ID

        Returns:
            InterceptResult: blocked=True 表示应拦截
        """
        if not self._enabled:
            return self._bypass()
        if not cmd or not isinstance(cmd, str):
            return InterceptResult(blocked=False, threat_level=ThreatLevel.LOW,
                                    event_type=EventType.UAC_ELEVATION, reason="")

        for pattern, reason, level in UAC_PATTERNS:
            match = pattern.search(cmd)
            if match:
                matched = match.group(0)
                result = InterceptResult(
                    blocked=True, threat_level=level,
                    event_type=EventType.UAC_ELEVATION,
                    reason=f"UAC提权拦截: {reason}（匹配: {matched[:80]}）",
                    details={"command": cmd, "matched_pattern": pattern.pattern, "matched_text": matched}
                )
                self._log(result, task_id)
                return result

        return InterceptResult(blocked=False, threat_level=ThreatLevel.LOW,
                                event_type=EventType.UAC_ELEVATION, reason="")

    # ═══════════════════════════════════════════════════════════════
    #  路径越界检查
    # ═══════════════════════════════════════════════════════════════

    def check_path(self, path: str, workspace_root: str = "",
                    task_id: str = "") -> InterceptResult:
        """检查路径是否安全（不越界）。

        检测项：
        1. 路径穿越（..）
        2. 环境变量展开后指向系统目录
        3. 绝对路径指向系统关键目录
        4. 路径在工作区范围之外

        Args:
            path: 要检查的路径
            workspace_root: 工作区根目录
            task_id: 关联的任务 ID

        Returns:
            InterceptResult: blocked=True 表示应拦截
        """
        if not self._enabled:
            return self._bypass()
        if not path or not isinstance(path, str):
            return InterceptResult(blocked=False, threat_level=ThreatLevel.LOW,
                                    event_type=EventType.PATH_TRAVERSAL, reason="")

        # ── 1. 路径穿越检测 ──
        if _PATH_TRAVERSAL.search(path):
            result = InterceptResult(
                blocked=True, threat_level=ThreatLevel.MEDIUM,
                event_type=EventType.PATH_TRAVERSAL,
                reason=f"路径越界拦截: 检测到路径穿越符号 '..'（path={path}）",
                details={"path": path, "issue": "path_traversal"}
            )
            self._log(result, task_id)
            return result

        # ── 1.5 Unix $HOME / ~ 直接检测（不依赖环境变量展开） ──
        # 在非 Unix 平台上 HOME 可能未设置，但意图仍是越界访问
        _home_patterns = [
            (r'^\$HOME[/\\]', "$HOME 路径"),
            (r'^\$\{HOME\}[/\\]', "${HOME} 路径"),
            (r'^~[/\\]', "~ 用户主目录"),
        ]
        for pat, desc in _home_patterns:
            if re.match(pat, path, re.IGNORECASE):
                result = InterceptResult(
                    blocked=True, threat_level=ThreatLevel.HIGH,
                    event_type=EventType.PATH_TRAVERSAL,
                    reason=f"路径越界拦截: 禁止访问{desc}（path={path}）",
                    details={"path": path, "issue": "home_dir_access"}
                )
                self._log(result, task_id)
                return result

        # ── 2. 环境变量检测 ──
        if _ENV_VAR_PATTERN.search(path):
            expanded = _expand_env_vars(path)
            # 检查展开后是否指向系统关键目录
            for sys_path in _WINDOWS_SYSTEM_PATHS:
                expanded_sys = _expand_env_vars(sys_path)
                if expanded.lower().startswith(expanded_sys.lower()):
                    result = InterceptResult(
                        blocked=True, threat_level=ThreatLevel.HIGH,
                        event_type=EventType.PATH_TRAVERSAL,
                        reason=f"路径越界拦截: 环境变量展开后指向系统目录（{path} → {expanded}）",
                        details={"path": path, "expanded": expanded, "system_path": expanded_sys}
                    )
                    self._log(result, task_id)
                    return result

            # Unix $HOME 等
            for sys_path in _UNIX_SYSTEM_PATHS:
                expanded_sys = _expand_env_vars(sys_path)
                if expanded == expanded_sys or expanded.startswith(expanded_sys + os.sep):
                    result = InterceptResult(
                        blocked=True, threat_level=ThreatLevel.HIGH,
                        event_type=EventType.PATH_TRAVERSAL,
                        reason=f"路径越界拦截: 环境变量展开后指向系统目录（{path} → {expanded}）",
                        details={"path": path, "expanded": expanded, "system_path": expanded_sys}
                    )
                    self._log(result, task_id)
                    return result

        # ── 3. 绝对路径指向系统目录 ──
        normalized = os.path.normpath(path)
        if platform.system() == "Windows":
            for sys_path in _WINDOWS_SYSTEM_PATHS:
                expanded_sys = os.path.normpath(_expand_env_vars(sys_path))
                if normalized.lower().startswith(expanded_sys.lower()):
                    result = InterceptResult(
                        blocked=True, threat_level=ThreatLevel.HIGH,
                        event_type=EventType.PATH_TRAVERSAL,
                        reason=f"路径越界拦截: 禁止访问系统目录（{path}）",
                        details={"path": path, "system_path": sys_path}
                    )
                    self._log(result, task_id)
                    return result
        else:
            for sys_path in _UNIX_SYSTEM_PATHS:
                expanded_sys = os.path.normpath(_expand_env_vars(sys_path))
                if normalized == expanded_sys or normalized.startswith(expanded_sys + os.sep):
                    result = InterceptResult(
                        blocked=True, threat_level=ThreatLevel.HIGH,
                        event_type=EventType.PATH_TRAVERSAL,
                        reason=f"路径越界拦截: 禁止访问系统目录（{path}）",
                        details={"path": path, "system_path": sys_path}
                    )
                    self._log(result, task_id)
                    return result

        # ── 4. 工作区边界检查 ──
        if workspace_root:
            ws_root = os.path.abspath(workspace_root)
            # ★ 统一转为相对路径后再 join，避免绝对路径导致 os.path.join 忽略 workspace_root
            _check_path = path
            if os.path.isabs(_check_path):
                try:
                    rel = os.path.relpath(_check_path, ws_root)
                    if not rel.startswith("..") or rel == ".":
                        _check_path = rel
                except ValueError:
                    pass
            full = os.path.abspath(os.path.join(ws_root, _check_path))
            if not full.startswith(ws_root + os.sep) and full != ws_root:
                result = InterceptResult(
                    blocked=True, threat_level=ThreatLevel.MEDIUM,
                    event_type=EventType.PATH_TRAVERSAL,
                    reason=f"路径越界拦截: 路径超出工作区范围（{path} → {full}，工作区={ws_root}）",
                    details={"path": path, "resolved": full, "workspace": ws_root}
                )
                self._log(result, task_id)
                return result

        return InterceptResult(blocked=False, threat_level=ThreatLevel.LOW,
                                event_type=EventType.PATH_TRAVERSAL, reason="")

    def _safe_path(self, path: str, workspace_root: str) -> str:
        """验证并返回安全路径（与 executor._safe_path 一致）。

        Raises:
            ValueError: 路径越界时抛出
        """
        # ★ 统一转为相对路径：避免绝对路径导致 os.path.join 忽略 workspace_root
        if os.path.isabs(path):
            try:
                rel = os.path.relpath(path, workspace_root)
                if not rel.startswith("..") or rel == ".":
                    path = rel
            except ValueError:
                pass

        full = os.path.abspath(os.path.join(workspace_root, path))
        if not full.startswith(workspace_root + os.sep) and full != workspace_root:
            raise ValueError(f"路径越界: {path}")
        return full

    # ═══════════════════════════════════════════════════════════════
    #  综合检查（命令 + UAC）
    # ═══════════════════════════════════════════════════════════════

    def check_command_full(self, cmd: str, task_id: str = "") -> InterceptResult:
        """对命令执行全面安全检查：危险命令 + UAC 提权。

        先检查 UAC（优先级最高），再检查危险命令。
        """
        # UAC 检查
        uac_result = self.check_uac(cmd, task_id)
        if uac_result.blocked:
            return uac_result

        # 危险命令检查
        cmd_result = self.check_command(cmd, task_id)
        if cmd_result.blocked:
            return cmd_result

        return InterceptResult(blocked=False, threat_level=ThreatLevel.LOW,
                                event_type=EventType.DANGEROUS_COMMAND, reason="")

    # ═══════════════════════════════════════════════════════════════
    #  拦截日志 (NORPsafe.json)
    # ═══════════════════════════════════════════════════════════════

    def _log(self, result: InterceptResult, task_id: str = ""):
        """记录拦截事件到 NORPsafe.json。"""
        if not self._log_path:
            return

        self._intercept_count += 1
        if result.threat_level == ThreatLevel.CRITICAL:
            self._critical_count += 1

        record = {
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "threat_level": result.threat_level.value,
            "event_type": result.event_type.value,
            "reason": result.reason,
            "action": "blocked",
            "task_id": task_id,
            "details": result.details,
            "intercept_id": self._intercept_count,
        }

        try:
            # 读取已有日志（追加模式）
            logs = []
            log_file = self._log_path
            if os.path.exists(log_file):
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        logs = json.load(f)
                except (json.JSONDecodeError, FileNotFoundError):
                    logs = []

            logs.append(record)

            # 保留最近 500 条
            if len(logs) > 500:
                logs = logs[-500:]

            with open(log_file, "w", encoding="utf-8") as f:
                json.dump(logs, f, indent=2, ensure_ascii=False)

        except Exception:
            # 日志写入失败不应影响主流程
            pass

    def get_logs(self, limit: int = 50) -> List[dict]:
        """读取最近的拦截日志。"""
        if not self._log_path or not os.path.exists(self._log_path):
            return []
        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                logs = json.load(f)
            return logs[-limit:]
        except Exception:
            return []

    def get_stats(self) -> dict:
        """获取安全统计。"""
        return {
            "total_intercepts": self._intercept_count,
            "critical_intercepts": self._critical_count,
            "log_path": self._log_path,
        }


# ═══════════════════════════════════════════════════════════════════
#  全局单例
# ═══════════════════════════════════════════════════════════════════

_norp_safe_instance: Optional[NorpSafe] = None


def get_norp_safe(app_dir: str = "") -> NorpSafe:
    """获取 NORP Safe 全局单例。

    Args:
        app_dir: 应用数据目录（用于日志路径）
    """
    global _norp_safe_instance
    if _norp_safe_instance is None:
        _norp_safe_instance = NorpSafe(app_dir=app_dir)
    elif app_dir and not _norp_safe_instance.log_path:
        _norp_safe_instance.log_path = os.path.join(app_dir, "NORPsafe.json")
    return _norp_safe_instance


def set_norp_safe_enabled(enabled: bool) -> str:
    """启用/禁用 NORP 安全系统。

    ⚠️ 禁用后所有安全检查直接放行，包括危险命令、UAC提权、路径越界。
    仅建议在受信任的隔离环境中使用。

    Args:
        enabled: True 启用，False 禁用

    Returns:
        "ok" 或错误信息
    """
    global _norp_safe_instance
    if _norp_safe_instance is None:
        _norp_safe_instance = NorpSafe()
    _norp_safe_instance.enabled = enabled
    return "ok"


def is_norp_safe_enabled() -> bool:
    """查询安全系统是否启用。"""
    global _norp_safe_instance
    if _norp_safe_instance is None:
        return True  # 默认启用
    return _norp_safe_instance.enabled

# Copyright (c) 2026 xingluosama121, MIT Licensed
"""插件源码安全审计：AST 静态分析（零第三方依赖）。

迁移自现有应用 plugin_system.security，特性保持一致：

- 危险调用 / 危险导入 / 反射绕过检测（getattr、__getattribute__、
  __dict__ 下标、内省字典间接访问）；
- 三级严重度：critical（拒绝）/ warning（提示）/ info；
- 三种审计级别：off / warn / block；
- 权限声明校验：manifest.permissions 覆盖审计发现的危险类别。

本模块是纯函数式审计器：只做检测与决策，不加载任何代码。
导入限制见 norpagent.plugins.loader（加载流程中按需启用）。
"""

from __future__ import annotations

import ast
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

# ── 严重度 / 问题 ──────────────────────────────────────────


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SecurityIssue:
    """一条审计发现。"""

    __slots__ = ("severity", "line", "code", "message", "category")

    def __init__(self, severity: Severity, line: int, code: str,
                 message: str, category: str):
        self.severity = severity
        self.line = line
        self.code = code
        self.message = message
        self.category = category

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.value,
            "line": self.line,
            "code": self.code,
            "message": self.message,
            "category": self.category,
        }


# ── 危险模式注册表（与现有应用一致）─────────────────────────

# (module, attr) -> (severity, category, message)
DANGEROUS_CALLS: Dict[Tuple[str, ...], Tuple[Severity, str, str]] = {
    # 进程 / shell 执行（CRITICAL）
    ("os", "system"): (Severity.CRITICAL, "shell_exec", "os.system() 执行任意 shell 命令"),
    ("os", "popen"): (Severity.CRITICAL, "shell_exec", "os.popen() 启动任意进程"),
    ("subprocess", "call"): (Severity.CRITICAL, "shell_exec", "subprocess.call() 启动任意进程"),
    ("subprocess", "run"): (Severity.CRITICAL, "shell_exec", "subprocess.run() 启动任意进程"),
    ("subprocess", "Popen"): (Severity.CRITICAL, "shell_exec", "subprocess.Popen() 启动任意进程"),
    ("subprocess", "check_output"): (Severity.CRITICAL, "shell_exec", "subprocess.check_output() 执行命令"),
    ("subprocess", "check_call"): (Severity.CRITICAL, "shell_exec", "subprocess.check_call() 执行命令"),
    ("subprocess", "getoutput"): (Severity.CRITICAL, "shell_exec", "subprocess.getoutput() 执行命令"),
    ("subprocess", "getstatusoutput"): (Severity.CRITICAL, "shell_exec", "subprocess.getstatusoutput() 执行命令"),
    # 代码执行（CRITICAL）
    ("eval",): (Severity.CRITICAL, "code_exec", "eval() 执行任意 Python 表达式"),
    ("exec",): (Severity.CRITICAL, "code_exec", "exec() 执行任意 Python 代码"),
    ("compile",): (Severity.CRITICAL, "code_exec", "compile() 配合 exec 模式可执行代码"),
    ("__import__",): (Severity.CRITICAL, "import_bypass", "__import__() 绕过导入限制"),
    ("importlib", "import_module"): (Severity.CRITICAL, "import_bypass", "importlib.import_module() 绕过限制"),
    # 原生代码（CRITICAL）
    ("ctypes",): (Severity.CRITICAL, "native_exec", "ctypes 可加载任意 DLL / 原生代码"),
    ("cffi",): (Severity.CRITICAL, "native_exec", "cffi 可加载任意原生代码"),
    ("os", "_exit"): (Severity.CRITICAL, "process_terminate", "os._exit() 强杀整个进程"),
    ("os", "kill"): (Severity.CRITICAL, "process_terminate", "os.kill() 可终止其他进程"),
    # 文件删除 / 权限变更（WARNING）
    ("os", "remove"): (Severity.WARNING, "file_delete", "os.remove() 删除文件"),
    ("os", "unlink"): (Severity.WARNING, "file_delete", "os.unlink() 删除文件"),
    ("shutil", "rmtree"): (Severity.WARNING, "file_delete", "shutil.rmtree() 递归删除目录"),
    ("shutil", "move"): (Severity.WARNING, "file_move", "shutil.move() 移动/重命名文件"),
    ("os", "rmdir"): (Severity.WARNING, "file_delete", "os.rmdir() 删除目录"),
    ("os", "chmod"): (Severity.WARNING, "file_permission", "os.chmod() 修改文件权限"),
    ("os", "chown"): (Severity.WARNING, "file_permission", "os.chown() 修改文件属主"),
    # 网络（WARNING）
    ("socket",): (Severity.WARNING, "network", "socket 提供原始 TCP/UDP 网络访问"),
    ("http",): (Severity.WARNING, "network", "http 模块提供 HTTP 客户端/服务"),
    ("urllib",): (Severity.WARNING, "network", "urllib 发起 HTTP 请求"),
    ("requests",): (Severity.WARNING, "network", "requests 库发起 HTTP 请求"),
    ("ftplib",): (Severity.WARNING, "network", "ftplib 建立 FTP 连接"),
    ("smtplib",): (Severity.WARNING, "network", "smtplib 发送邮件"),
    ("poplib",): (Severity.WARNING, "network", "poplib 收取邮件"),
    ("telnetlib",): (Severity.WARNING, "network", "telnetlib 建立 Telnet 连接"),
    # 反序列化（WARNING）
    ("pickle",): (Severity.WARNING, "deserialization", "pickle 反序列化可执行任意代码"),
    ("marshal",): (Severity.WARNING, "deserialization", "marshal.loads() 可执行代码"),
    ("yaml", "unsafe_load"): (Severity.WARNING, "deserialization", "yaml.unsafe_load() 可实例化任意对象"),
    # 系统操纵（WARNING）
    ("sys", "modules"): (Severity.WARNING, "sys_manipulation", "修改 sys.modules 影响其他插件"),
    ("sys", "setprofile"): (Severity.WARNING, "sys_manipulation", "sys.setprofile() 拦截所有函数调用"),
    ("sys", "settrace"): (Severity.WARNING, "sys_manipulation", "sys.settrace() 拦截所有执行"),
    ("builtins",): (Severity.WARNING, "builtin_override", "修改 builtins 影响整个进程"),
    ("sys", "exit"): (Severity.WARNING, "sys_manipulation", "sys.exit() 终止整个进程"),
}

DANGEROUS_IMPORTS: Dict[str, Tuple[Severity, str, str]] = {
    "subprocess": (Severity.CRITICAL, "dangerous_import", "subprocess 启动任意命令"),
    "ctypes": (Severity.CRITICAL, "dangerous_import", "ctypes 加载任意原生代码"),
    "cffi": (Severity.CRITICAL, "dangerous_import", "cffi 加载任意原生代码"),
    "socket": (Severity.WARNING, "dangerous_import", "socket 提供原始网络访问"),
    "pickle": (Severity.WARNING, "dangerous_import", "pickle 反序列化执行代码"),
    "marshal": (Severity.WARNING, "dangerous_import", "marshal 反序列化不可信数据"),
    "telnetlib": (Severity.WARNING, "dangerous_import", "telnetlib 原始 TCP 连接"),
    "ftplib": (Severity.WARNING, "dangerous_import", "ftplib FTP 连接"),
    "smtplib": (Severity.WARNING, "dangerous_import", "smtplib 发送邮件"),
}

# 危险函数名（动态调用绕过检测用，与现有应用 DANGEROUS_FUNC_NAMES 一致）
DANGEROUS_FUNC_NAMES: Set[str] = {
    "system", "popen", "run", "Popen", "call", "check_output", "check_call",
    "getoutput", "getstatusoutput",
    "eval", "exec", "compile", "execfile",
    "__import__", "import_module",
    "remove", "unlink", "rmtree", "rmdir",
    "chmod", "chown",
    "kill", "terminate", "_exit", "exit",
    "setprofile", "settrace",
    "loads", "unsafe_load", "load",
    "ShellExecute", "ShellExecuteW", "ShellExecuteA",
    "WriteProcessMemory", "VirtualAllocEx", "CreateRemoteThread",
}

# 可能借道间接访问的危险模块
_SUSPICIOUS_BASES = ("os", "subprocess", "builtins", "sys", "ctypes", "cffi", "shutil", "importlib")


# ── 审计器 ────────────────────────────────────────────────


class SourceAuditor:
    """AST 源码审计器（纯函数式，线程安全，无全局状态）。"""

    def __init__(self, audit_level: str = "warn") -> None:
        """audit_level: off / warn / block（非法值回落为 warn）。"""
        self.audit_level = audit_level if audit_level in ("off", "warn", "block") else "warn"

    # ── 入口 ────────────────────────────────────────────

    def audit_file(self, file_path: str,
                   audit_level: Optional[str] = None) -> Tuple[List[SecurityIssue], bool]:
        """读取并审计一个源码文件。

        返回 (问题列表, 是否放行)。放行为 False 当且仅当级别为 block
        且存在 critical 问题。
        """
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                source = fh.read()
        except OSError as exc:
            return [SecurityIssue(
                Severity.CRITICAL, 0, "",
                f"无法读取源码文件: {exc}", "io_error",
            )], False
        return self.audit_source(source, audit_level=audit_level)

    def audit_source(self, source: str,
                     audit_level: Optional[str] = None) -> Tuple[List[SecurityIssue], bool]:
        """解析源码并遍历 AST 检测危险模式。"""
        issues: List[SecurityIssue] = []
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            issues.append(SecurityIssue(
                Severity.CRITICAL, getattr(exc, "lineno", 0) or 0, "",
                f"语法错误: {exc.msg}", "syntax_error",
            ))
            return issues, False
        self._walk(tree, issues)
        level = audit_level if audit_level is not None else self.audit_level
        if level == "off":
            return issues, True
        if level == "block" and any(i.severity == Severity.CRITICAL for i in issues):
            return issues, False
        return issues, True

    def check_permissions(self, manifest: Dict[str, Any],
                          issues: List[SecurityIssue]) -> bool:
        """校验 manifest.permissions 是否覆盖审计发现的危险类别。

        权限值：process / network / file_write / file_read。
        """
        declared = set(manifest.get("permissions", []) or [])
        if not isinstance(declared, set):
            declared = {str(p) for p in declared}
        required: Set[str] = set()
        for issue in issues:
            cat = issue.category
            if cat in ("shell_exec", "code_exec", "native_exec", "process_terminate"):
                required.add("process")
            elif cat == "network":
                required.add("network")
            elif cat in ("file_delete", "file_move"):
                required.add("file_write")
            elif cat == "dangerous_import":
                if "subprocess" in issue.code:
                    required.add("process")
                elif "socket" in issue.code:
                    required.add("network")
        missing = required - declared
        if missing:
            issues.append(SecurityIssue(
                Severity.CRITICAL, 0, "",
                f"缺少权限声明: {', '.join(sorted(missing))}"
                "（manifest.json → permissions）",
                "missing_permissions",
            ))
            return False
        return True

    # ── AST 遍历 ────────────────────────────────────────

    def _walk(self, tree: ast.AST, issues: List[SecurityIssue]) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._check_import(alias.name, node.lineno, issues)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    full = f"{mod}.{alias.name}" if mod else alias.name
                    if mod:
                        self._check_import(mod, node.lineno, issues)
                    self._match_dangerous_call(full, node.lineno, issues)
            elif isinstance(node, ast.Call):
                self._check_call(node, issues)
                self._check_dynamic_call(node, issues)
            elif isinstance(node, ast.Attribute):
                self._check_attribute(node, issues)
            elif isinstance(node, ast.Subscript):
                self._check_dynamic_subscript(node, issues)

    def _check_import(self, name: str, lineno: int, issues: List[SecurityIssue]) -> None:
        if name in DANGEROUS_IMPORTS:
            sev, cat, msg = DANGEROUS_IMPORTS[name]
            issues.append(SecurityIssue(sev, lineno, f"import {name}", msg, cat))
            return
        for mod, (sev, cat, msg) in DANGEROUS_IMPORTS.items():
            if name == mod or name.startswith(mod + "."):
                issues.append(SecurityIssue(sev, lineno, f"import {name}", msg, cat))
                return

    def _check_call(self, node: ast.Call, issues: List[SecurityIssue]) -> None:
        name = _resolve_call_name(node.func)
        if name:
            self._match_dangerous_call(name, node.lineno, issues)

    def _match_dangerous_call(self, full_name: str, lineno: int,
                              issues: List[SecurityIssue]) -> None:
        parts = full_name.rsplit(".", 1)
        for pattern, (sev, cat, msg) in DANGEROUS_CALLS.items():
            if len(pattern) == 1:
                if parts[-1] == pattern[0]:
                    issues.append(SecurityIssue(sev, lineno, full_name, msg, cat))
                    return
            elif len(pattern) == 2:
                mod_part, func_part = pattern
                if len(parts) == 2 and parts[0].endswith(mod_part) and parts[1] == func_part:
                    issues.append(SecurityIssue(sev, lineno, full_name, msg, cat))
                    return

    def _check_attribute(self, node: ast.Attribute, issues: List[SecurityIssue]) -> None:
        name = _resolve_attr_name(node)
        if not name:
            return
        for pattern, (sev, cat, msg) in DANGEROUS_CALLS.items():
            if len(pattern) == 2 and name == f"{pattern[0]}.{pattern[1]}":
                if cat in ("sys_manipulation", "process_terminate"):
                    issues.append(SecurityIssue(
                        sev, node.lineno, name,
                        f"访问 {name} 有潜在危险", cat,
                    ))

    def _check_dynamic_call(self, node: ast.Call, issues: List[SecurityIssue]) -> None:
        """检测 getattr / __getattribute__ 动态调用绕过。"""
        func = node.func
        callee: Optional[str] = None
        args = list(node.args)
        obj_arg = 0
        name_arg = 1

        if isinstance(func, ast.Name) and func.id == "getattr":
            callee = "getattr"
        elif isinstance(func, ast.Attribute) and func.attr == "__getattribute__":
            callee = "__getattribute__"
            obj_arg = -1  # 对象就是 func.value，稍后塞回 args[0]
            if func.value is not None:
                args.insert(0, func.value)
        else:
            return

        if not args:
            return
        name_str = None
        if len(args) > name_arg:
            name_node = args[name_arg]
            if isinstance(name_node, ast.Constant) and isinstance(name_node.value, str):
                name_str = name_node.value
        obj_str = None
        if obj_arg is not None and len(args) > obj_arg:
            obj_str = _resolve_call_name(args[obj_arg])

        flagged = False
        if name_str and name_str in DANGEROUS_FUNC_NAMES:
            flagged = True
        elif obj_str:
            base = obj_str.split(".")[0]
            if base in _SUSPICIOUS_BASES:
                flagged = True
        if flagged:
            desc = f"{obj_str or '?'}.{name_str or '?'}" if obj_str else f"getattr(..., {name_str!r})"
            issues.append(SecurityIssue(
                Severity.CRITICAL, node.lineno, desc,
                "动态调用绕过静态审计（getattr/__getattribute__ 间接访问危险能力）",
                "dynamic_bypass",
            ))

    def _check_dynamic_subscript(self, node: ast.Subscript,
                                 issues: List[SecurityIssue]) -> None:
        """检测 __dict__ / globals() / locals() / vars() 下标间接访问。"""
        key_str = None
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            key_str = node.slice.value
        base = _resolve_subscript_base(node.value)
        if base and base.endswith("__dict__") and key_str and key_str in DANGEROUS_FUNC_NAMES:
            issues.append(SecurityIssue(
                Severity.CRITICAL, node.lineno, f"{base}[{key_str!r}]",
                "通过 __dict__ 下标间接访问危险能力，绕过静态审计",
                "dynamic_bypass",
            ))
            return
        if base in ("globals", "locals", "vars"):
            if key_str and key_str in ("os", "subprocess", "builtins", "sys", "ctypes", "importlib"):
                issues.append(SecurityIssue(
                    Severity.CRITICAL, node.lineno, f"{base}()[{key_str!r}]",
                    "通过内省字典间接访问危险模块，绕过静态审计",
                    "dynamic_bypass",
                ))


# ── 名称解析辅助 ──────────────────────────────────────────


def _resolve_call_name(func) -> Optional[str]:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return _resolve_attr_name(func)
    return None


def _resolve_attr_name(node: ast.Attribute) -> Optional[str]:
    parts: List[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        parts.reverse()
        return ".".join(parts)
    return None


def _resolve_subscript_base(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Attribute):
        return _resolve_attr_name(node)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _resolve_call_name(node.func)
    return None


# ── 向后兼容别名 ──────────────────────────────────────────

PluginSecurity = SourceAuditor  # 与现有应用类名对齐，便于迁移

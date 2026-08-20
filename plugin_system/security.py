# Vibe Coding Agent - Plugin Security Module
# Copyright (c) 2026 xingluosama
"""
Plugin security layer providing:
  1. AST-based source code audit (detect dangerous patterns before loading)
  2. Import restriction (block dangerous modules for plugin code)
  3. Permission system (manifest-declared capabilities)
  4. Resource limits (memory / CPU time)

All features are individually togglable via config.json keys:
  - plugin_security_audit          : "off" | "warn" | "block"
  - plugin_security_import_restrict: "off" | "safe" | "strict"
  - plugin_security_require_permissions: true | false
  - plugin_security_resource_limit : true | false
"""

import ast
import builtins
import os
import signal
import sys
import threading
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

# Thread-local flag set during plugin loading to short-circuit frame-walking
# in the import blocker (huge perf win – avoids walking on every import).
_loading_plugin = threading.local()


# ── Enums ──────────────────────────────────────────────────────────

class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SecurityIssue:
    """A single finding from the security audit."""

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


# ── Dangerous pattern registry ────────────────────────────────────

# (module_part, attr, severity, category, message)
DANGEROUS_CALLS: Dict[Tuple[str, ...], Tuple[Severity, str, str]] = {
    # ── Shell / Process execution (CRITICAL) ──
    ("os", "system"):        (Severity.CRITICAL, "shell_exec", "os.system() executes arbitrary shell commands"),
    ("os", "popen"):         (Severity.CRITICAL, "shell_exec", "os.popen() spawns arbitrary processes"),
    ("subprocess", "call"):      (Severity.CRITICAL, "shell_exec", "subprocess.call() spawns arbitrary processes"),
    ("subprocess", "run"):       (Severity.CRITICAL, "shell_exec", "subprocess.run() spawns arbitrary processes"),
    ("subprocess", "Popen"):     (Severity.CRITICAL, "shell_exec", "subprocess.Popen() spawns arbitrary processes"),
    ("subprocess", "check_output"): (Severity.CRITICAL, "shell_exec", "subprocess.check_output() runs commands"),
    ("subprocess", "check_call"):   (Severity.CRITICAL, "shell_exec", "subprocess.check_call() runs commands"),
    ("subprocess", "getoutput"):    (Severity.CRITICAL, "shell_exec", "subprocess.getoutput() runs commands"),
    ("subprocess", "getstatusoutput"): (Severity.CRITICAL, "shell_exec", "subprocess.getstatusoutput() runs commands"),

    # ── Code execution (CRITICAL) ──
    ("eval",):              (Severity.CRITICAL, "code_exec", "eval() executes arbitrary Python expressions"),
    ("exec",):              (Severity.CRITICAL, "code_exec", "exec() executes arbitrary Python code"),
    ("compile",):           (Severity.CRITICAL, "code_exec", "compile() with 'exec' mode can execute code"),
    ("__import__",):        (Severity.CRITICAL, "import_bypass", "__import__() bypasses import restrictions"),
    ("importlib", "import_module"): (Severity.CRITICAL, "import_bypass", "importlib.import_module() bypasses restrictions"),

    # ── Native code (CRITICAL) ──
    ("ctypes",):            (Severity.CRITICAL, "native_exec", "ctypes allows loading arbitrary DLLs / native code"),
    ("cffi",):              (Severity.CRITICAL, "native_exec", "cffi allows loading arbitrary native code"),

    # ── File deletion / permission changes (WARNING) ──
    ("os", "remove"):       (Severity.WARNING, "file_delete", "os.remove() deletes files"),
    ("os", "unlink"):       (Severity.WARNING, "file_delete", "os.unlink() deletes files"),
    ("shutil", "rmtree"):   (Severity.WARNING, "file_delete", "shutil.rmtree() recursively deletes directories"),
    ("shutil", "move"):     (Severity.WARNING, "file_move", "shutil.move() can move/rename files"),
    ("os", "rmdir"):        (Severity.WARNING, "file_delete", "os.rmdir() removes directories"),
    ("os", "chmod"):        (Severity.WARNING, "file_permission", "os.chmod() changes file permissions"),
    ("os", "chown"):        (Severity.WARNING, "file_permission", "os.chown() changes file ownership"),

    # ── Network (WARNING) ──
    ("socket",):            (Severity.WARNING, "network", "socket enables raw TCP/UDP network access"),
    ("http",):              (Severity.WARNING, "network", "http module enables HTTP client/server"),
    ("urllib",):            (Severity.WARNING, "network", "urllib enables HTTP requests"),
    ("requests",):          (Severity.WARNING, "network", "requests library enables HTTP requests"),
    ("ftplib",):            (Severity.WARNING, "network", "ftplib enables FTP connections"),
    ("smtplib",):           (Severity.WARNING, "network", "smtplib enables sending emails"),
    ("poplib",):            (Severity.WARNING, "network", "poplib enables email retrieval"),
    ("telnetlib",):         (Severity.WARNING, "network", "telnetlib enables Telnet connections"),

    # ── Deserialization (WARNING) ──
    ("pickle",):            (Severity.WARNING, "deserialization", "pickle can execute arbitrary code during unpickling"),
    ("marshal",):           (Severity.WARNING, "deserialization", "marshal.loads() can execute code"),
    ("yaml", "unsafe_load"): (Severity.WARNING, "deserialization", "yaml.unsafe_load() can instantiate arbitrary objects"),

    # ── System manipulation (WARNING) ──
    ("sys", "modules"):     (Severity.WARNING, "sys_manipulation", "Modifying sys.modules affects all other plugins"),
    ("sys", "setprofile"):  (Severity.WARNING, "sys_manipulation", "sys.setprofile() intercepts all function calls"),
    ("sys", "settrace"):    (Severity.WARNING, "sys_manipulation", "sys.settrace() intercepts all execution"),
    ("builtins",):          (Severity.WARNING, "builtin_override", "Modifying builtins affects the entire process"),
    ("sys", "exit"):        (Severity.WARNING, "sys_manipulation", "sys.exit() terminates the entire process"),
    ("os", "_exit"):        (Severity.CRITICAL, "process_terminate", "os._exit() forcefully kills the process"),
    ("os", "kill"):         (Severity.CRITICAL, "process_terminate", "os.kill() can terminate other processes"),
}

DANGEROUS_IMPORTS: Dict[str, Tuple[Severity, str, str]] = {
    "subprocess":   (Severity.CRITICAL, "dangerous_import", "subprocess spawns arbitrary commands"),
    "ctypes":       (Severity.CRITICAL, "dangerous_import", "ctypes loads arbitrary native code"),
    "cffi":         (Severity.CRITICAL, "dangerous_import", "cffi loads arbitrary native code"),
    "socket":       (Severity.WARNING, "dangerous_import", "socket enables raw network access"),
    "pickle":       (Severity.WARNING, "dangerous_import", "pickle executes code on deserialization"),
    "marshal":      (Severity.WARNING, "dangerous_import", "marshal can deserialize untrusted data"),
    "telnetlib":    (Severity.WARNING, "dangerous_import", "telnetlib enables raw TCP connections"),
    "ftplib":       (Severity.WARNING, "dangerous_import", "ftplib enables FTP connections"),
    "smtplib":      (Severity.WARNING, "dangerous_import", "smtplib enables email sending"),
}


# ── Default safe-module sets ─────────────────────────────────────

SAFE_MODULES: Set[str] = {
    # Standard library – safe
    "json", "re", "datetime", "math", "random", "collections",
    "itertools", "functools", "typing", "enum", "dataclasses",
    "pathlib", "os.path", "glob", "fnmatch",
    "textwrap", "string", "hashlib", "base64", "binascii",
    "traceback", "logging", "warnings",
    "copy", "pprint", "inspect", "contextlib",
    "uuid", "time", "calendar", "zoneinfo",
    "csv", "io", "tempfile", "shutil",
    "html", "xml.etree.ElementTree", "xml",
    "struct", "codecs", "unicodedata",
    "fractions", "decimal", "statistics",
    # Our plugin system internals (allowed for context objects)
    "plugin_system.context", "plugin_system",
}

STRICT_SAFE_MODULES: Set[str] = {
    "json", "re", "datetime", "math", "random",
    "collections", "itertools", "functools", "typing", "enum",
    "pathlib", "os.path",
    "textwrap", "string", "hashlib", "base64",
    "traceback", "logging", "warnings", "copy",
    "uuid", "time",
    "plugin_system.context",
}

# Modules we ALWAYS block regardless of mode (only for plugin code)
ALWAYS_BLOCKED: Set[str] = {
    "ctypes", "cffi",
}

# ★ P0-3 修复：动态调用绕过检测用的「危险函数名」集合。
#   用于识别 getattr(os, "system")、obj.__getattribute__("eval") 等
#   绕过静态字面量匹配的间接调用。
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

# 动态访问可能借道的「反射 / 内省」入口（用于检测间接绕过）
REFLECTION_ENTRYPOINTS: Set[str] = {
    "getattr", "__getattribute__", "__dict__", "globals", "locals", "vars",
}


# ── Import blocker (sys.meta_path finder) ─────────────────────────

class PluginImportBlocker:
    """
    A `sys.meta_path` finder that restricts imports made by plugin modules.

    Uses ``inspect.stack()`` to determine whether the caller is a plugin
    (module name starts with ``vibe_plugin_``) and blocks dangerous imports
    only for those callers.  Non-plugin code is unaffected.
    """

    def __init__(self, blocked: Set[str], plugin_prefix: str = "vibe_plugin_"):
        self.blocked: Set[str] = blocked
        self.plugin_prefix: str = plugin_prefix
        self._registered: bool = False

    def register(self):
        """Insert this finder at the front of ``sys.meta_path``."""
        if self._registered:
            return
        if self not in sys.meta_path:
            sys.meta_path.insert(0, self)  # type: ignore[arg-type]
        self._registered = True

    def unregister(self):
        """Remove this finder from ``sys.meta_path``."""
        if not self._registered:
            return
        try:
            sys.meta_path.remove(self)  # type: ignore[arg-type]
        except ValueError:
            pass
        self._registered = False

    def find_spec(self, fullname: str, path=None, target=None):
        # 1) Check if the module should be blocked
        should_block = fullname in self.blocked
        if not should_block:
            for b in self.blocked:
                if fullname == b or fullname.startswith(b + "."):
                    should_block = True
                    break

        if not should_block:
            # Also check ALWAYS_BLOCKED
            for ab in ALWAYS_BLOCKED:
                if fullname == ab or fullname.startswith(ab + "."):
                    should_block = True
                    break

        if not should_block:
            return None  # not blocked – let normal import proceed

        # 2) Only block if the caller is a plugin module
        if self._caller_is_plugin():
            raise ImportError(
                f"Plugin security: import '{fullname}' is blocked. "
                f"This module is not allowed for plugin code."
            )

        return None  # not from plugin – allow

    def _caller_is_plugin(self) -> bool:
        """Check whether any frame in the stack belongs to a plugin module.

        Uses ``sys._getframe()`` for a lightweight walk (avoids the heavy
        ``inspect.stack()`` which creates full FrameInfo tuples on every call).
        A thread-local short-circuit is checked first for the common loading path.
        """
        # Fast path: plugin-loading in progress on this thread
        if getattr(_loading_plugin, 'active', False):
            return True

        try:
            frame = sys._getframe()
            depth = 0
            while frame is not None and depth < 80:
                mod_name = frame.f_globals.get("__name__", "")
                if mod_name.startswith(self.plugin_prefix):
                    return True
                frame = frame.f_back
                depth += 1
        except Exception:
            pass
        return False


# ── Source auditor ────────────────────────────────────────────────

class PluginSecurity:
    """
    Audits plugin source code for security risks before loading.

    Parameters
    ----------
    config : dict
        The full config.json dict.  Relevant keys:

        - ``plugin_security_audit`` : ``"off"`` | ``"warn"`` (default) | ``"block"``
        - ``plugin_security_import_restrict`` : ``"off"`` (default) | ``"safe"`` (block dangerous modules)
          | ``"strict"`` (only safe modules)
        - ``plugin_security_require_permissions`` : ``bool`` (default False)
        - ``plugin_security_resource_limit`` : ``bool`` (default False)
    """

    def __init__(self, config: dict):
        self.audit_level: str = config.get("plugin_security_audit", "warn")
        self.import_restriction: str = config.get("plugin_security_import_restrict", "off")
        self.resource_limit: bool = config.get("plugin_security_resource_limit", False)
        self.require_permissions: bool = config.get("plugin_security_require_permissions", False)
        self.plugin_prefix: str = "vibe_plugin_"

        self._blocker: Optional[PluginImportBlocker] = None
        self._setup_import_blocker()

    def _setup_import_blocker(self):
        """Create (but don't register) the import blocker based on config."""
        if self.audit_level == "off":
            self._blocker = None
            return

        blocked: Set[str] = set()

        if self.import_restriction == "safe":
            # Block all known dangerous modules
            blocked = set(DANGEROUS_IMPORTS.keys())
        elif self.import_restriction == "strict":
            # Block everything NOT in STRICT_SAFE_MODULES
            blocked = set(DANGEROUS_IMPORTS.keys())

        # Always-blocked modules only apply when import restriction is active
        if self.import_restriction != "off":
            blocked |= ALWAYS_BLOCKED

        if blocked:
            self._blocker = PluginImportBlocker(blocked, self.plugin_prefix)
        else:
            self._blocker = None

    def enable_import_blocker(self):
        """Register the import blocker (call before loading plugins)."""
        if self._blocker:
            self._blocker.register()

    def disable_import_blocker(self):
        """Unregister the import blocker."""
        if self._blocker:
            self._blocker.unregister()

    # ── Audit entry point ───────────────────────────────────────

    def audit_file(self, file_path: str, audit_level: Optional[str] = None) -> Tuple[List[SecurityIssue], bool]:
        """
        Read and audit a plugin source file.

        Parameters
        ----------
        audit_level : str | None
            覆盖实例级 audit_level（用于签名信任分级：受信任插件放宽审计）。

        Returns
        -------
        (issues, allowed) – *allowed* is ``False`` when the audit level is
        ``"block"`` and critical issues were found.
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
        except Exception as exc:
            return [SecurityIssue(
                Severity.CRITICAL, 0, "",
                f"Cannot read source file: {exc}", "io_error"
            )], False

        return self.audit_source(source, audit_level=audit_level)

    def audit_source(self, source: str, audit_level: Optional[str] = None) -> Tuple[List[SecurityIssue], bool]:
        """Parse *source* and walk the AST looking for dangerous patterns."""
        issues: List[SecurityIssue] = []

        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            issues.append(SecurityIssue(
                Severity.CRITICAL, getattr(exc, 'lineno', 0) or 0, "",
                f"Syntax error: {exc.msg}", "syntax_error"
            ))
            return issues, False

        self._walk(tree, issues)

        # ── Decision ──
        level = audit_level if audit_level is not None else self.audit_level
        if level == "off":
            return issues, True

        has_critical = any(i.severity == Severity.CRITICAL for i in issues)
        if level == "block" and has_critical:
            return issues, False

        return issues, True

    def check_permissions(self, manifest: dict,
                          issues: List[SecurityIssue]) -> bool:
        """
        Verify that the plugin's declared permissions cover the risky
        operations detected in the audit.

        Manifest key used: ``"permissions"`` – a list of strings.  Supported
        permission values:
          ``"process"`` ``"network"`` ``"file_write"`` ``"file_read"``
        """
        if self.audit_level == "off":
            return True

        if not self.require_permissions:
            return True

        declared = set(manifest.get("permissions", []))
        required: Set[str] = set()

        for issue in issues:
            cat = issue.category
            if cat in ("shell_exec", "code_exec", "native_exec",
                       "process_terminate"):
                required.add("process")
            elif cat in ("network",):
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
                f"Missing permission declarations: {', '.join(sorted(missing))}. "
                f"Add them to manifest.json → permissions",
                "missing_permissions"
            ))
            return False

        return True

    # ── AST walker ──────────────────────────────────────────────

    def _walk(self, tree: ast.AST, issues: List[SecurityIssue]):
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._check_import(alias.name, node.lineno, issues)

            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    full = f"{mod}.{alias.name}" if mod else alias.name
                    # Check the base module
                    if mod:
                        self._check_import(mod, node.lineno, issues)
                    # Check the specific call
                    self._match_dangerous_call(full, node.lineno, issues)

            elif isinstance(node, ast.Call):
                self._check_call(node, issues)
                self._check_dynamic_call(node, issues)

            elif isinstance(node, ast.Attribute):
                self._check_attribute(node, issues)

            elif isinstance(node, ast.Subscript):
                self._check_dynamic_subscript(node, issues)

    def _check_dynamic_call(self, node: ast.Call, issues: List[SecurityIssue]):
        """★ P0-3：检测 getattr / __getattribute__ / 反射 等动态调用绕过。

        例如：
          getattr(os, "system")("...")          → 动态调用 os.system
          os.__getattribute__("popen")("...")   → 动态调用 os.popen
          builtins.__dict__["eval"]("...")      → 通过 __dict__ 间接访问
        """
        func = node.func
        callee = None
        args = list(node.args)

        # getattr(obj, name) / obj.__getattribute__(name)
        if isinstance(func, ast.Name) and func.id in ("getattr",):
            callee = "getattr"
            obj_arg, name_arg = 0, 1
        elif isinstance(func, ast.Attribute) and func.attr == "__getattribute__":
            callee = "__getattribute__"
            obj_arg, name_arg = None, 0
            if func.value is not None:
                args.insert(0, func.value)
        else:
            return

        if not args:
            return

        # 取 name 字符串常量
        name_str = None
        if len(args) > name_arg:
            name_node = args[name_arg]
            if isinstance(name_node, ast.Constant) and isinstance(name_node.value, str):
                name_str = name_node.value

        # 取对象名
        obj_str = None
        if obj_arg is not None and len(args) > obj_arg:
            obj_str = self._resolve_call_name(args[obj_arg])

        flagged = False
        if name_str and name_str in DANGEROUS_FUNC_NAMES:
            flagged = True
        elif obj_str:
            base = obj_str.split(".")[0]
            if base in ("os", "subprocess", "builtins", "sys", "ctypes", "cffi", "shutil"):
                flagged = True

        if flagged:
            desc = f"{obj_str or '?'}.{name_str or '?'}" if obj_str else f"getattr(..., {name_str!r})"
            issues.append(SecurityIssue(
                Severity.CRITICAL, node.lineno, desc,
                "动态调用绕过静态审计（getattr/__getattribute__ 间接访问危险能力）",
                "dynamic_bypass",
            ))

    def _check_dynamic_subscript(self, node: ast.Subscript, issues: List[SecurityIssue]):
        """★ P0-3：检测 __dict__ / globals() / locals() / vars() 下标间接访问。

        例如：
          os.__dict__["system"]
          globals()["os"].system
          vars()["subprocess"].run
        """
        key_str = None
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            key_str = node.slice.value

        base = self._resolve_call_name(node.value) if not isinstance(node.value, ast.Call) else None
        if base is None and isinstance(node.value, ast.Call):
            base = self._resolve_call_name(node.value.func)

        # 检测 __dict__ 下标访问危险名
        if base and base.endswith("__dict__") and key_str and key_str in DANGEROUS_FUNC_NAMES:
            issues.append(SecurityIssue(
                Severity.CRITICAL, node.lineno, f"{base}[{key_str!r}]",
                "通过 __dict__ 下标间接访问危险能力，绕过静态审计",
                "dynamic_bypass",
            ))
            return

        # 检测 globals()/locals()/vars() 下标
        if base in ("globals", "locals", "vars"):
            if key_str and key_str in ("os", "subprocess", "builtins", "sys", "ctypes", "importlib"):
                issues.append(SecurityIssue(
                    Severity.CRITICAL, node.lineno, f"{base}()[{key_str!r}]",
                    "通过内省字典间接访问危险模块，绕过静态审计",
                    "dynamic_bypass",
                ))

    def _check_import(self, name: str, lineno: int,
                      issues: List[SecurityIssue]):
        if name in DANGEROUS_IMPORTS:
            sev, cat, msg = DANGEROUS_IMPORTS[name]
            issues.append(SecurityIssue(sev, lineno, f"import {name}", msg, cat))
            return
        # parent match
        for mod, (sev, cat, msg) in DANGEROUS_IMPORTS.items():
            if name == mod or name.startswith(mod + "."):
                issues.append(SecurityIssue(sev, lineno, f"import {name}", msg, cat))
                return

    def _check_call(self, node: ast.Call, issues: List[SecurityIssue]):
        name = self._resolve_call_name(node.func)
        if name:
            self._match_dangerous_call(name, node.lineno, issues)

    def _check_attribute(self, node: ast.Attribute,
                         issues: List[SecurityIssue]):
        name = self._resolve_attr_name(node)
        if name:
            # Only flag attribute access to certain dangerous things
            for pattern, (sev, cat, msg) in DANGEROUS_CALLS.items():
                if len(pattern) == 2 and name == f"{pattern[0]}.{pattern[1]}":
                    if cat in ("sys_manipulation",):
                        issues.append(SecurityIssue(
                            sev, node.lineno, name,
                            f"Accessing {name} is potentially dangerous", cat
                        ))

    def _match_dangerous_call(self, full_name: str, lineno: int,
                              issues: List[SecurityIssue]):
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

    @staticmethod
    def _resolve_call_name(func) -> Optional[str]:
        """Resolve ``ast.Call.func`` to a dotted name string."""
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return PluginSecurity._resolve_attr_name(func)
        return None

    @staticmethod
    def _resolve_attr_name(node: ast.Attribute) -> Optional[str]:
        parts: List[str] = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            parts.reverse()
            return ".".join(parts)
        return None


# ── Resource limiter (cross-platform best-effort) ─────────────────

class ResourceLimiter:
    """
    **EXPERIMENTAL – process-level resource limits.**

    .. warning::

       ``RLIMIT_CPU`` and ``RLIMIT_AS`` are **process-wide** constraints.
       They affect the *entire agent process*, not just the plugin being loaded.
       If a plugin triggers these limits the whole agent may be killed by the OS
       (SIGXCPU / SIGKILL).  Use with caution.

       ``signal.alarm()`` is similarly process-global and may interfere with
       other timers in the application.

    - CPU time: signal.alarm (Unix) or Timer thread (Windows)
    - Memory: resource.setrlimit (Unix only)
    """

    def __init__(self, max_memory_mb: int = 512, max_cpu_seconds: float = 10.0):
        self.max_memory_mb = max_memory_mb
        self.max_cpu_seconds = max_cpu_seconds
        self._active = False
        self._timer: Optional[threading.Timer] = None

    def enable(self):
        if self._active:
            return
        self._active = True

        # Unix resource limits
        try:
            import resource as _resource
            if self.max_cpu_seconds > 0:
                _resource.setrlimit(
                    _resource.RLIMIT_CPU,
                    (int(self.max_cpu_seconds), int(self.max_cpu_seconds)))
            if self.max_memory_mb > 0:
                mem = self.max_memory_mb * 1024 * 1024
                _resource.setrlimit(_resource.RLIMIT_AS, (mem, mem))
        except (ImportError, ValueError, OSError):
            pass  # Windows / not supported

        # CPU timeout via signal (Unix)
        if hasattr(signal, "SIGALRM"):
            try:
                signal.alarm(int(self.max_cpu_seconds))
            except Exception:
                pass

    def disable(self):
        if not self._active:
            return
        self._active = False

        if hasattr(signal, "SIGALRM"):
            try:
                signal.alarm(0)
            except Exception:
                pass

        try:
            import resource as _resource
            inf = _resource.RLIM_INFINITY
            _resource.setrlimit(_resource.RLIMIT_CPU, (inf, inf))
            _resource.setrlimit(_resource.RLIMIT_AS, (inf, inf))
        except (ImportError, ValueError, OSError):
            pass


# ── Stricter import restriction (strict mode) ─────────────────────

class StrictImportBlocker:
    """
    For ``"strict"`` import restriction: only allow modules in
    ``STRICT_SAFE_MODULES``.  Everything else is blocked for plugin code.
    """

    def __init__(self, plugin_prefix: str = "vibe_plugin_"):
        self.plugin_prefix = plugin_prefix
        self._registered = False

    def register(self):
        if self._registered:
            return
        if self not in sys.meta_path:
            sys.meta_path.insert(0, self)  # type: ignore[arg-type]
        self._registered = True

    def unregister(self):
        if not self._registered:
            return
        try:
            sys.meta_path.remove(self)  # type: ignore[arg-type]
        except ValueError:
            pass
        self._registered = False

    def find_spec(self, fullname: str, path=None, target=None):
        # Allow standard library and builtins
        if fullname in STRICT_SAFE_MODULES:
            return None
        for m in STRICT_SAFE_MODULES:
            if fullname.startswith(m + "."):
                return None

        # Always block known dangerous
        for ab in ALWAYS_BLOCKED:
            if fullname == ab or fullname.startswith(ab + "."):
                if self._caller_is_plugin():
                    raise ImportError(f"Plugin security: '{fullname}' is permanently blocked")
                return None

        # For strict mode, block everything else if caller is a plugin
        if self._caller_is_plugin():
            raise ImportError(
                f"Plugin security (strict): import '{fullname}' is not in the "
                f"safe-module list.  Add it to the whitelist if needed."
            )

        return None

    def _caller_is_plugin(self) -> bool:
        # Fast path: plugin-loading in progress on this thread
        if getattr(_loading_plugin, 'active', False):
            return True
        try:
            frame = sys._getframe()
            depth = 0
            while frame is not None and depth < 80:
                mod_name = frame.f_globals.get("__name__", "")
                if mod_name.startswith(self.plugin_prefix):
                    return True
                frame = frame.f_back
                depth += 1
        except Exception:
            pass
        return False

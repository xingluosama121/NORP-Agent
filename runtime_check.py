# Vibe Coding Agent - 运行时完整性检测 (Runtime Integrity Check)
# 在程序启动时检测运行环境是否满足要求，对 Windows Sandbox / VM / 受限环境给出明确反馈
# Copyright (c) 2026 xingluosama

import os
import sys
import json
import socket
import shutil
import ctypes
import platform
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Callable, Any
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
#  数据模型
# ═══════════════════════════════════════════════════════════════

class Severity(Enum):
    """检查项严重级别。"""
    FATAL = "fatal"       # 程序无法正常运行，必须修复
    ERROR = "error"       # 重要功能不可用
    WARNING = "warning"   # 功能降级，仍可运行
    INFO = "info"         # 纯信息提示

    def order(self) -> int:
        _order = {Severity.FATAL: 0, Severity.ERROR: 1, Severity.WARNING: 2, Severity.INFO: 3}
        return _order[self]


@dataclass
class CheckItem:
    """单条检测结果。"""
    category: str          # 检测类别：environment / python / dependency / windows_api / filesystem / network / resource
    name: str              # 检测项名称
    severity: Severity
    passed: bool
    message: str           # 简短描述
    detail: str = ""       # 详细技术信息
    suggestion: str = ""   # 修复建议


@dataclass
class RuntimeReport:
    """完整性检测报告。"""
    checks: List[CheckItem] = field(default_factory=list)
    environment_type: str = "unknown"   # normal / windows_sandbox / docker / vm / wine / restricted
    environment_detail: str = ""
    overall_healthy: bool = True        # 无 FATAL/ERROR 即为健康
    fatal_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0

    def to_dict(self) -> dict:
        return {
            "checks": [
                {
                    "category": c.category,
                    "name": c.name,
                    "severity": c.severity.value,
                    "passed": c.passed,
                    "message": c.message,
                    "detail": c.detail,
                    "suggestion": c.suggestion,
                }
                for c in self.checks
            ],
            "environment_type": self.environment_type,
            "environment_detail": self.environment_detail,
            "overall_healthy": self.overall_healthy,
            "fatal_count": self.fatal_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
        }


# ═══════════════════════════════════════════════════════════════
#  核心检测器
# ═══════════════════════════════════════════════════════════════

class RuntimeChecker:
    """运行时完整性检测器。

    检测流程：
    1. 环境识别（Windows Sandbox / Docker / VM / Wine / 正常）
    2. Python 版本
    3. 关键依赖导入
    4. Windows 专有 API
    5. 文件系统权限
    6. 网络连通性
    7. 系统资源（内存 / 磁盘）
    """

    def __init__(self, app_dir: str = ""):
        self.app_dir = app_dir or os.getcwd()
        self._report = RuntimeReport()
        self._checked = False

    # ── 主入口 ─────────────────────────────────────────────

    def run_all_checks(self) -> RuntimeReport:
        """执行全部检测，返回报告。"""
        if self._checked:
            return self._report

        self._report = RuntimeReport()

        # 1) 环境识别（必须最先执行）
        env_result = self._check_environment()
        self._add(env_result)

        # 2) Python 版本
        py_result = self._check_python_version()
        self._add(py_result)

        # 3) 关键依赖
        for r in self._check_critical_imports():
            self._add(r)

        # 3.5) WebView2 运行时检测（与 pywebview 包分开检测）
        self._add(self._check_webview2_runtime())

        # 3.6) 插件运行时检测
        for r in self._check_plugin_runtime():
            self._add(r)

        # 4) Windows API
        for r in self._check_windows_apis():
            self._add(r)

        # 5) 文件系统
        fs_result = self._check_filesystem()
        self._add(fs_result)

        # 6) 网络
        net_result = self._check_network()
        self._add(net_result)

        # 7) 系统资源
        res_result = self._check_resources()
        self._add(res_result)

        # 汇总
        self._summarize()
        self._checked = True
        return self._report

    def has_fatal(self) -> bool:
        """是否存在 FATAL 级别的问题。"""
        if not self._checked:
            self.run_all_checks()
        return self._report.fatal_count > 0

    def get_report(self) -> RuntimeReport:
        """获取检测报告（若未执行则先执行）。"""
        if not self._checked:
            self.run_all_checks()
        return self._report

    def get_report_dict(self) -> dict:
        return self.get_report().to_dict()

    # ── 内部方法 ───────────────────────────────────────────

    def _add(self, item: CheckItem):
        self._report.checks.append(item)

    def _summarize(self):
        r = self._report
        r.fatal_count = sum(1 for c in r.checks if c.severity == Severity.FATAL)
        r.error_count = sum(1 for c in r.checks if c.severity == Severity.ERROR)
        r.warning_count = sum(1 for c in r.checks if c.severity == Severity.WARNING)
        r.info_count = sum(1 for c in r.checks if c.severity == Severity.INFO)
        r.overall_healthy = (r.fatal_count == 0 and r.error_count == 0)

    # ── 1) 环境识别 ────────────────────────────────────────

    def _check_environment(self) -> CheckItem:
        """识别当前运行环境类型。"""
        env_type = "normal"
        details = []

        system = platform.system()
        details.append(f"OS: {system} {platform.release()} ({platform.version()})")
        details.append(f"Arch: {platform.machine()}")
        details.append(f"Hostname: {socket.gethostname()}")

        # ── Windows Sandbox 检测 ──
        if system == "Windows":
            sandbox_indicators = self._detect_windows_sandbox()
            # 至少需要 2 个独立指标同时命中才判定为 Sandbox，避免单个弱特征误判
            if len(sandbox_indicators) >= 2:
                env_type = "windows_sandbox"
                details.append("⚠ Environment: Windows Sandbox detected")
                details.extend(f"  → {s}" for s in sandbox_indicators)
            elif sandbox_indicators:
                details.append(f"ℹ Sandbox-like indicator (insufficient to classify): {sandbox_indicators[0]}")

        # ── Docker 容器检测（仅在未识别为 Sandbox 时） ──
        if env_type == "normal" and self._is_docker_container():
            env_type = "docker"
            details.append("⚠ Environment: Docker container detected")

        # ── VM 检测（通用，仅在未识别为 Sandbox/Docker 时） ──
        if env_type == "normal" and self._is_vm():
            env_type = "vm"
            details.append("⚠ Environment: Virtual Machine detected")

        # ── Wine 检测（仅在未识别为其他特殊环境时） ──
        if env_type == "normal" and system == "Windows" and self._is_wine():
            env_type = "wine"
            details.append("⚠ Environment: Wine compatibility layer detected")

        self._report.environment_type = env_type
        self._report.environment_detail = "\n".join(details)

        if env_type == "windows_sandbox":
            return CheckItem(
                category="environment",
                name="Windows Sandbox 环境检测",
                severity=Severity.WARNING,
                passed=False,
                message="检测到 Windows Sandbox 环境，部分功能受限",
                detail="\n".join(sandbox_indicators),
                suggestion="Windows Sandbox 是轻量级隔离桌面环境：Docker 不可用、GPU 加速受限、磁盘空间有限（通常 40GB）。程序可以运行但部分高级功能降级。"
            )
        elif env_type == "docker":
            return CheckItem(
                category="environment",
                name="Docker 容器环境检测",
                severity=Severity.WARNING,
                passed=False,
                message="检测到 Docker 容器环境",
                detail="程序运行在 Docker 容器中",
                suggestion="容器内运行存在限制：无法再嵌套 Docker、Windows API 可能不可用。"
            )
        elif env_type == "wine":
            return CheckItem(
                category="environment",
                name="Wine 环境检测",
                severity=Severity.ERROR,
                passed=False,
                message="检测到 Wine 兼容层，Windows API 可能异常",
                detail="Wine 对 Windows API 的支持不完整",
                suggestion="建议在原生 Windows 环境下运行本程序。"
            )
        elif env_type == "vm":
            return CheckItem(
                category="environment",
                name="虚拟机环境检测",
                severity=Severity.INFO,
                passed=True,
                message=f"运行在 {system} 环境中",
                detail="\n".join(details),
                suggestion=""
            )
        else:
            return CheckItem(
                category="environment",
                name="运行环境",
                severity=Severity.INFO,
                passed=True,
                message=f"运行在 {system} 环境中",
                detail="\n".join(details),
                suggestion=""
            )

    def _detect_windows_sandbox(self) -> List[str]:
        """检测 Windows Sandbox 特征，返回检测到的指标列表。"""
        indicators = []

        # 1) 检查典型 Sandbox 用户名格式：WDAGUtilityAccount
        try:
            username = os.environ.get("USERNAME", "")
            if "WDAGUtilityAccount" in username:
                indicators.append(f"Sandbox 用户名: {username}")
        except Exception:
            pass

        # 2) 检查 Sandbox 特有的环境变量
        sandbox_env_vars = ["SANDBOX_ENABLED", "WDAG_UTILITY"]
        for var in sandbox_env_vars:
            if os.environ.get(var):
                indicators.append(f"Sandbox 环境变量: {var}={os.environ[var]}")

        # 3) 检查计算机名是否包含 Sandbox 特征
        try:
            computername = os.environ.get("COMPUTERNAME", "")
            if computername and len(computername) == 15 and computername.startswith("SB"):
                indicators.append(f"Sandbox 计算机名: {computername}")
        except Exception:
            pass

        # 4) 检查 Sandbox 专用注册表键（仅在 Windows Sandbox 中存在）
        #    注意：不能用 AppModel\StateChange，该键在所有正常 Win10/11 上都存在
        try:
            import winreg
            # Windows Sandbox 特有的注册表路径
            sandbox_specific_keys = [
                r"SOFTWARE\Microsoft\Windows\Sandbox",
            ]
            for key_path in sandbox_specific_keys:
                try:
                    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
                    indicators.append(f"Sandbox 注册表: {key_path}")
                    winreg.CloseKey(key)
                except OSError:
                    pass
        except Exception:
            pass

        # 5) 检查典型 Sandbox 环境变量组合：低 CPU + 低内存
        #    Sandbox 默认 2 核 + 4GB，这些软指标配合命中时才加入
        try:
            cpu_count = os.cpu_count()
            if cpu_count and cpu_count <= 2:
                # 如果已有其他指标（用户名 / 环境变量），则追加此项增强可信度
                if indicators:
                    indicators.append(f"低 CPU 核数: {cpu_count}（Sandbox 典型配置）")
        except Exception:
            pass

        return indicators

    def _is_docker_container(self) -> bool:
        """检测是否在 Docker 容器中运行。

        注意：仅 Linux 上 Docker 容器有 /.dockerenv 和 /proc/1/cgroup 特征；
        Windows 上的 Docker 容器通过 WSL2/Hyper-V 运行，此方法不会误报。
        """
        # /.dockerenv 文件（仅 Linux 存在）
        if os.path.exists("/.dockerenv"):
            return True
        # cgroup 检查（仅 Linux 存在 /proc）
        try:
            with open("/proc/1/cgroup", "r") as f:
                content = f.read()
                if "docker" in content or "containerd" in content:
                    return True
        except Exception:
            pass
        return False

    def _is_vm(self) -> bool:
        """检测是否在虚拟机中运行（通用方法）。"""
        system = platform.system()
        if system == "Windows":
            try:
                # 检查常见 VM 厂商
                manufacturer = os.environ.get("PROCESSOR_IDENTIFIER", "")
                vm_keywords = ["VMware", "VirtualBox", "QEMU", "KVM", "Xen"]
                for kw in vm_keywords:
                    if kw.lower() in manufacturer.lower():
                        return True

                # 使用 WMI 查询（如果可用）
                try:
                    import subprocess
                    result = subprocess.run(
                        ["wmic", "computersystem", "get", "manufacturer"],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.stdout:
                        for kw in vm_keywords:
                            if kw.lower() in result.stdout.lower():
                                return True
                except Exception:
                    pass
            except Exception:
                pass
        elif system == "Linux":
            try:
                with open("/sys/class/dmi/id/product_name", "r") as f:
                    product = f.read().lower()
                    vm_keywords = ["vmware", "virtualbox", "qemu", "kvm", "xen", "hyper-v"]
                    for kw in vm_keywords:
                        if kw in product:
                            return True
            except Exception:
                pass
        return False

    def _is_wine(self) -> bool:
        """检测是否在 Wine 环境下运行。"""
        try:
            # Wine 会在注册表中写入特定键
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wine")
            winreg.CloseKey(key)
            return True
        except Exception:
            pass
        # 也可以通过环境变量判断
        if os.environ.get("WINE"):
            return True
        return False

    # ── 2) Python 版本 ─────────────────────────────────────

    def _check_python_version(self) -> CheckItem:
        """检查 Python 版本。"""
        major, minor, micro = sys.version_info[:3]
        version_str = f"{major}.{minor}.{micro}"

        if major < 3 or (major == 3 and minor < 10):
            return CheckItem(
                category="python",
                name="Python 版本",
                severity=Severity.FATAL,
                passed=False,
                message=f"Python {version_str} 版本过低",
                detail=f"当前: Python {version_str}，最低要求: Python 3.10",
                suggestion="请安装 Python 3.10 或更高版本：https://www.python.org/downloads/"
            )
        elif major == 3 and minor < 11:
            return CheckItem(
                category="python",
                name="Python 版本",
                severity=Severity.INFO,
                passed=True,
                message=f"Python {version_str} — 满足最低要求",
                detail=f"当前: Python {version_str}，最低要求: Python 3.10",
                suggestion="建议升级到 Python 3.11+ 以获得更好的异步性能和错误信息。"
            )
        else:
            return CheckItem(
                category="python",
                name="Python 版本",
                severity=Severity.INFO,
                passed=True,
                message=f"Python {version_str} ✓",
                detail=f"Python {version_str}",
                suggestion=""
            )

    # ── 3) 关键依赖 ────────────────────────────────────────

    def _check_critical_imports(self) -> List[CheckItem]:
        """检查关键依赖是否可导入。"""
        results = []

        # (模块名, 显示名, 来源包, 是否致命, 说明)
        critical_modules = [
            ("openai", "OpenAI SDK", "openai>=1.0.0", True, "API 调用核心依赖"),
            ("keyring", "Keyring", "keyring>=24.0", True, "API Key 安全存储（Windows 凭据管理器）"),
            ("webview", "pywebview", "pywebview>=5.0", True, "GUI 窗口框架"),
            ("win32crypt", "pywin32 (win32crypt)", "pywin32>=306", False, "备用 API Key 加密存储"),
            ("requests", "Requests", "requests>=2.28.0", True, "HTTP 请求库"),
            ("PyPDF2", "PyPDF2", "PyPDF2>=3.0.0", False, "PDF 文件读取"),
            ("docx", "python-docx", "python-docx>=0.8.11", False, "Word 文档读取"),
            ("openpyxl", "openpyxl", "openpyxl>=3.1.0", False, "Excel 文件读取"),
            ("docker", "Docker SDK", "docker>=7.0.0", False, "Docker 沙箱隔离（可选）"),
        ]

        for mod_name, display_name, pkg, fatal, description in critical_modules:
            try:
                __import__(mod_name)
                results.append(CheckItem(
                    category="dependency",
                    name=display_name,
                    severity=Severity.INFO,
                    passed=True,
                    message=f"{display_name} ✓",
                    detail=f"来源: {pkg} — {description}",
                    suggestion=""
                ))
            except ImportError:
                sev = Severity.FATAL if fatal else Severity.WARNING
                results.append(CheckItem(
                    category="dependency",
                    name=display_name,
                    severity=sev,
                    passed=False,
                    message=f"{display_name} 未安装",
                    detail=f"无法导入 {mod_name}（来源: {pkg}）\n{description}",
                    suggestion=f"运行: pip install {pkg}"
                ))

        # 额外检查 keyring 是否真的能写入（某些 Sandbox/受限环境可以导入但不能写入）
        try:
            import keyring
            # 尝试一个简单的 set + get + delete 来验证后端可用
            test_service = "_vibe_runtime_test"
            test_user = "_test"
            test_pass = "test_check_12345"
            try:
                keyring.set_password(test_service, test_user, test_pass)
                retrieved = keyring.get_password(test_service, test_user)
                keyring.delete_password(test_service, test_user)
                if retrieved == test_pass:
                    # 已经记录了上面的成功结果，不额外添加
                    pass
                else:
                    results.append(CheckItem(
                        category="dependency",
                        name="Keyring 读写验证",
                        severity=Severity.WARNING,
                        passed=False,
                        message="Keyring 安装但读写异常",
                        detail="keyring 可导入但 set/get 行为不一致",
                        suggestion="尝试切换 API Key 加密方式为 win32crypt（base.env 文件模式）。"
                    ))
            except Exception as e:
                results.append(CheckItem(
                    category="dependency",
                    name="Keyring 后端可用性",
                    severity=Severity.ERROR,
                    passed=False,
                    message=f"Windows 凭据管理器不可用: {e}",
                    detail="keyring 无法写入 Windows Credential Manager。某些 Windows Sandbox 配置中凭据管理器可能受限。",
                    suggestion="请在设置中将加密方式切换为 'win32crypt'（本地文件加密存储），或检查 Windows Credential Manager 服务是否正常运行。"
                ))
        except ImportError:
            pass  # 已在上面记录

        return results

    # ── 3.5) WebView2 运行时 ────────────────────────────────

    def _check_webview2_runtime(self) -> CheckItem:
        """检测 Microsoft Edge WebView2 运行时是否已安装（仅 Windows）。

        pywebview 导入成功 ≠ WebView2 运行时可用。老版本 Windows 10
        可能没有预装 WebView2，需要单独检测系统级运行时。
        """
        if platform.system() != "Windows":
            return CheckItem(
                category="dependency",
                name="WebView2 运行时",
                severity=Severity.INFO,
                passed=True,
                message="非 Windows 系统，跳过 WebView2 检测",
                detail="",
                suggestion=""
            )

        found = False
        found_detail = ""

        # 方法 1: 检查注册表（Evergreen Runtime 的 GUID）
        webview2_guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        reg_locations = [
            (0x80000002, f"SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\{webview2_guid}"),
            (0x80000001, f"Software\\Microsoft\\EdgeUpdate\\Clients\\{webview2_guid}"),
        ]
        for hkey_const, subkey in reg_locations:
            try:
                import winreg
                key = winreg.OpenKey(hkey_const, subkey)
                try:
                    pv, _ = winreg.QueryValueEx(key, "pv")
                    if pv:
                        found = True
                        found_detail = f"注册表: {subkey}\\pv = {pv}"
                        break
                finally:
                    winreg.CloseKey(key)
            except OSError:
                pass

        # 方法 2: 检查文件系统中的 DLL
        if not found:
            search_roots = []
            prog_x86 = os.environ.get("ProgramFiles(x86)", "")
            if prog_x86:
                search_roots.append(os.path.join(prog_x86, "Microsoft\\EdgeWebView\\Application"))
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            if local_appdata:
                search_roots.append(os.path.join(local_appdata, "Microsoft\\EdgeWebView\\Application"))
            prog_files = os.environ.get("ProgramFiles", "")
            if prog_files:
                search_roots.append(os.path.join(prog_files, "Microsoft\\EdgeWebView\\Application"))

            for root in search_roots:
                try:
                    if not os.path.isdir(root):
                        continue
                    for item in os.listdir(root):
                        dll = os.path.join(root, item, "EBWebView", "x64", "EmbeddedBrowserWebView.dll")
                        if os.path.isfile(dll):
                            found = True
                            found_detail = f"文件: {dll}"
                            break
                except Exception:
                    pass
                if found:
                    break

        if found:
            return CheckItem(
                category="dependency",
                name="WebView2 运行时",
                severity=Severity.INFO,
                passed=True,
                message="WebView2 运行时已安装 ✓",
                detail=found_detail,
                suggestion=""
            )
        else:
            return CheckItem(
                category="dependency",
                name="WebView2 运行时",
                severity=Severity.FATAL,
                passed=False,
                message="WebView2 运行时未安装 — GUI 窗口无法启动",
                detail="pywebview 需要 Microsoft Edge WebView2 Evergreen Runtime。\n"
                        "老版本 Windows 10 可能未预装此运行库。",
                suggestion="下载 WebView2 Runtime：https://go.microsoft.com/fwlink/p/?LinkId=2124703"
            )

    # ── 3.6) 插件运行时 ────────────────────────────────────

    def _check_plugin_runtime(self) -> List[CheckItem]:
        """检查插件系统运行所需的一切：插件系统模块完整性、官方插件文件、
        插件所需的平台工具和 Python 依赖。"""
        results = []
        app_dir = self.app_dir

        # ── 插件系统核心模块 ──
        plugin_system_dir = os.path.join(app_dir, "plugin_system")
        required_plugin_system_files = ["__init__.py", "manager.py", "context.py", "security.py"]
        ps_missing = []
        if not os.path.isdir(plugin_system_dir):
            ps_missing = required_plugin_system_files  # 整个目录不存在
        else:
            for fname in required_plugin_system_files:
                fpath = os.path.join(plugin_system_dir, fname)
                if not os.path.isfile(fpath):
                    ps_missing.append(fname)

        if ps_missing:
            results.append(CheckItem(
                category="plugin_runtime",
                name="插件系统核心模块",
                severity=Severity.FATAL,
                passed=False,
                message=f"插件系统缺失 {len(ps_missing)} 个核心文件",
                detail="缺失: " + ", ".join(ps_missing),
                suggestion="请确保 plugin_system/ 目录中存在所有必需的 .py 文件。"
            ))
        else:
            results.append(CheckItem(
                category="plugin_runtime",
                name="插件系统核心模块",
                severity=Severity.INFO,
                passed=True,
                message="插件系统核心模块完整 ✓",
                detail=f"plugin_system/ 包含: {', '.join(required_plugin_system_files)}",
                suggestion=""
            ))

        # ── 官方插件文件 ──
        official_plugins_dir = os.path.join(app_dir, "official_plugins")
        expected_plugins = [
            "clipboard_manager.py", "code_reviewer.py",
            "dev_utilities.py", "doc_reader.py",
            "note_manager.py", "office_writer.py",
            "stress_tester.py", "time_tracker.py",
        ]
        op_missing = []
        op_not_bundled = False  # 打包发行版按设计不内置官方插件
        if not os.path.isdir(official_plugins_dir):
            if getattr(sys, 'frozen', False):
                # 打包发行版不随 exe 分发任何插件（用户可通过插件面板自行添加目录）
                op_not_bundled = True
            else:
                op_missing = expected_plugins  # 整个目录不存在
        else:
            for fname in expected_plugins:
                fpath = os.path.join(official_plugins_dir, fname)
                if not os.path.isfile(fpath):
                    op_missing.append(fname)

        if op_not_bundled:
            results.append(CheckItem(
                category="plugin_runtime",
                name="官方插件文件",
                severity=Severity.INFO,
                passed=True,
                message="发行版按设计不内置官方插件",
                detail="插件系统完整可用。如需官方插件，可在「插件控制面板」中添加"
                       " official_plugins 目录（须与发行版配套提供）。",
                suggestion=""
            ))
        elif op_missing:
            results.append(CheckItem(
                category="plugin_runtime",
                name="官方插件文件",
                severity=Severity.WARNING,
                passed=False,
                message=f"缺失 {len(op_missing)} 个官方插件",
                detail="缺失: " + ", ".join(op_missing),
                suggestion="部分插件功能将不可用。可重新解压 NORP Agent 更新包恢复缺失文件。"
            ))
        else:
            results.append(CheckItem(
                category="plugin_runtime",
                name="官方插件文件",
                severity=Severity.INFO,
                passed=True,
                message=f"官方插件完整 ✓（{len(expected_plugins)} 个）",
                detail="所有官方插件文件均存在",
                suggestion=""
            ))

        # ── 平台工具检测（插件所需） ──
        system = platform.system()
        if system == "Windows":
            # Windows: clip.exe 用于剪贴板插件
            clip_path = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "clip.exe")
            if os.path.isfile(clip_path):
                results.append(CheckItem(
                    category="plugin_runtime",
                    name="剪贴板工具 (clip.exe)",
                    severity=Severity.INFO,
                    passed=True,
                    message="clip.exe 可用 ✓",
                    detail=f"路径: {clip_path}",
                    suggestion=""
                ))
            else:
                results.append(CheckItem(
                    category="plugin_runtime",
                    name="剪贴板工具 (clip.exe)",
                    severity=Severity.WARNING,
                    passed=False,
                    message="clip.exe 不可用 — 剪贴板功能降级",
                    detail=f"未找到: {clip_path}",
                    suggestion="剪贴板写入功能将使用备用方案（临时文件）。"
                ))
        elif system == "Linux":
            # Linux: xclip 或 wl-clipboard
            found_clip = False
            for tool in ["xclip", "wl-copy"]:
                if shutil.which(tool):
                    found_clip = True
                    results.append(CheckItem(
                        category="plugin_runtime",
                        name=f"剪贴板工具 ({tool})",
                        severity=Severity.INFO,
                        passed=True,
                        message=f"{tool} 可用 ✓",
                        detail=f"路径: {shutil.which(tool)}",
                        suggestion=""
                    ))
                    break
            if not found_clip:
                results.append(CheckItem(
                    category="plugin_runtime",
                    name="剪贴板工具",
                    severity=Severity.WARNING,
                    passed=False,
                    message="未找到 xclip 或 wl-copy — 剪贴板功能不可用",
                    detail="Linux 剪贴板需要 xclip（X11）或 wl-clipboard（Wayland）",
                    suggestion="安装: sudo apt install xclip  或  sudo apt install wl-clipboard"
                ))
        elif system == "Darwin":
            # macOS: pbcopy/pbpaste 内置
            if shutil.which("pbcopy"):
                results.append(CheckItem(
                    category="plugin_runtime",
                    name="剪贴板工具 (pbcopy/pbpaste)",
                    severity=Severity.INFO,
                    passed=True,
                    message="pbcopy/pbpaste 可用 ✓",
                    detail="macOS 内置剪贴板命令",
                    suggestion=""
                ))
            else:
                results.append(CheckItem(
                    category="plugin_runtime",
                    name="剪贴板工具",
                    severity=Severity.WARNING,
                    passed=False,
                    message="pbcopy 不可用 — 剪贴板功能降级",
                    detail="macOS 应内置 pbcopy/pbpaste",
                    suggestion=""
                ))

        # ── 插件 Python 依赖（Office 文档读写） ──
        plugin_deps = [
            ("docx", "python-docx", "python-docx>=0.8.11", "Word 文档读写（doc_reader / office_writer）"),
            ("openpyxl", "openpyxl", "openpyxl>=3.1.0", "Excel 文档读写（doc_reader / office_writer）"),
            ("pptx", "python-pptx", "python-pptx>=0.6.21", "PowerPoint 读写（doc_reader / office_writer）"),
        ]
        for mod_name, display_name, pkg, description in plugin_deps:
            try:
                __import__(mod_name)
                results.append(CheckItem(
                    category="plugin_runtime",
                    name=f"插件依赖: {display_name}",
                    severity=Severity.INFO,
                    passed=True,
                    message=f"{display_name} ✓",
                    detail=f"来源: {pkg} — {description}",
                    suggestion=""
                ))
            except ImportError:
                results.append(CheckItem(
                    category="plugin_runtime",
                    name=f"插件依赖: {display_name}",
                    severity=Severity.WARNING,
                    passed=False,
                    message=f"{display_name} 未安装 — 对应 Office 插件功能不可用",
                    detail=f"无法导入 {mod_name}（来源: {pkg}）\n{description}",
                    suggestion=f"运行: pip install {pkg}"
                ))

        return results

    # ── 4) Windows 专有 API ────────────────────────────────

    def _check_windows_apis(self) -> List[CheckItem]:
        """检查 Windows 专有 API 是否可用。"""
        results = []

        if platform.system() != "Windows":
            results.append(CheckItem(
                category="windows_api",
                name="Windows API",
                severity=Severity.INFO,
                passed=True,
                message="非 Windows 系统，跳过 Windows API 检查",
                detail=f"当前系统: {platform.system()}",
                suggestion=""
            ))
            return results

        # 检查 win32crypt
        try:
            import win32crypt
            # 验证 CryptProtectData 是否可用
            test_data = b"runtime_integrity_test"
            try:
                blob = win32crypt.CryptProtectData(test_data, None, None, None, None, 0)
                decrypted = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
                if decrypted[1] == test_data:
                    results.append(CheckItem(
                        category="windows_api",
                        name="win32crypt (DPAPI)",
                        severity=Severity.INFO,
                        passed=True,
                        message="Windows DPAPI 加密正常 ✓",
                        detail="CryptProtectData / CryptUnprotectData 加解密验证通过",
                        suggestion=""
                    ))
                else:
                    results.append(CheckItem(
                        category="windows_api",
                        name="win32crypt (DPAPI)",
                        severity=Severity.ERROR,
                        passed=False,
                        message="Windows DPAPI 加解密结果不一致",
                        detail="CryptProtectData / CryptUnprotectData 返回数据不匹配",
                        suggestion="请检查 Windows 数据保护 API 状态。"
                    ))
            except Exception as e:
                results.append(CheckItem(
                    category="windows_api",
                    name="win32crypt (DPAPI)",
                    severity=Severity.ERROR,
                    passed=False,
                    message=f"Windows DPAPI 调用失败: {e}",
                    detail="win32crypt.CryptProtectData 不可用，API Key 将无法使用文件加密存储",
                    suggestion="请确保在原生 Windows 环境下运行（非 Wine/虚拟机可能不支持 DPAPI）。"
                ))
        except ImportError:
            results.append(CheckItem(
                category="windows_api",
                name="pywin32 (win32crypt)",
                severity=Severity.WARNING,
                passed=False,
                message="pywin32 未安装，备用加密方案不可用",
                detail="win32crypt 模块无法导入",
                suggestion="运行: pip install pywin32>=306"
            ))

        # 检查 Job Object（用于进程组管理）
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateJobObjectW(None, None)
            if handle:
                kernel32.CloseHandle(handle)
                results.append(CheckItem(
                    category="windows_api",
                    name="Windows Job Object",
                    severity=Severity.INFO,
                    passed=True,
                    message="Windows Job Object API 可用 ✓",
                    detail="CreateJobObject 调用成功",
                    suggestion=""
                ))
            else:
                results.append(CheckItem(
                    category="windows_api",
                    name="Windows Job Object",
                    severity=Severity.WARNING,
                    passed=False,
                    message="Windows Job Object 不可用",
                    detail="CreateJobObject 返回空句柄",
                    suggestion="进程组管理功能降级，超时强杀可能不完整。"
                ))
        except Exception as e:
            results.append(CheckItem(
                category="windows_api",
                name="Windows Job Object",
                severity=Severity.WARNING,
                passed=False,
                message=f"Windows Job Object API 异常: {e}",
                detail="无法调用 kernel32.CreateJobObjectW",
                suggestion="进程组管理功能降级。"
            ))

        return results

    # ── 5) 文件系统 ─────────────────────────────────────────

    def _check_filesystem(self) -> CheckItem:
        """检查文件系统权限和可用空间。"""
        issues = []

        # 检查 APP_DIR 是否可写
        test_file = os.path.join(self.app_dir, "._runtime_test_write")
        try:
            Path(self.app_dir).mkdir(parents=True, exist_ok=True)
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
        except Exception as e:
            issues.append(f"应用目录不可写 ({self.app_dir}): {e}")

        # 检查工作区目录
        workspace = os.path.join(os.path.expanduser("~"), "vibe_workspace")
        try:
            Path(workspace).mkdir(parents=True, exist_ok=True)
            test_ws = os.path.join(workspace, "._runtime_test")
            with open(test_ws, "w") as f:
                f.write("test")
            os.remove(test_ws)
        except Exception as e:
            issues.append(f"工作区目录不可写 ({workspace}): {e}")

        # 检查磁盘空间
        try:
            usage = shutil.disk_usage(self.app_dir)
            free_gb = usage.free / (1024 ** 3)
            total_gb = usage.total / (1024 ** 3)
            if free_gb < 1.0:
                issues.append(f"磁盘空间不足: 仅剩 {free_gb:.1f} GB / {total_gb:.1f} GB")
        except Exception:
            pass

        if issues:
            return CheckItem(
                category="filesystem",
                name="文件系统检查",
                severity=Severity.FATAL if any("不可写" in i for i in issues) else Severity.WARNING,
                passed=False,
                message=f"文件系统存在问题: {len(issues)} 项",
                detail="\n".join(f"  • {i}" for i in issues),
                suggestion="请检查磁盘空间和目录权限。"
            )
        else:
            try:
                usage = shutil.disk_usage(self.app_dir)
                free_gb = usage.free / (1024 ** 3)
                total_gb = usage.total / (1024 ** 3)
                detail = f"应用目录: {self.app_dir}\n可用空间: {free_gb:.1f} GB / {total_gb:.1f} GB"
                if free_gb < 5:
                    return CheckItem(
                        category="filesystem",
                        name="文件系统检查",
                        severity=Severity.WARNING,
                        passed=False,
                        message=f"磁盘空间偏低: {free_gb:.1f} GB 可用",
                        detail=detail,
                        suggestion="建议清理磁盘以留出更多空间。"
                    )
                return CheckItem(
                    category="filesystem",
                    name="文件系统检查",
                    severity=Severity.INFO,
                    passed=True,
                    message=f"文件系统正常 ({free_gb:.1f} GB 可用) ✓",
                    detail=detail,
                    suggestion=""
                )
            except Exception:
                return CheckItem(
                    category="filesystem",
                    name="文件系统检查",
                    severity=Severity.INFO,
                    passed=True,
                    message="文件系统可读写 ✓",
                    detail=f"应用目录: {self.app_dir}",
                    suggestion=""
                )

    # ── 6) 网络 ─────────────────────────────────────────────

    def _check_network(self) -> CheckItem:
        """检查网络连通性。"""
        # 测试能否连接到 DeepSeek API
        test_hosts = [
            ("api.deepseek.com", 443, "DeepSeek API"),
            ("api.openai.com", 443, "OpenAI API"),
        ]

        results = []
        for host, port, label in test_hosts:
            try:
                sock = socket.create_connection((host, port), timeout=5)
                sock.close()
                results.append(f"  ✓ {label} ({host}:{port}) 可达")
            except Exception as e:
                results.append(f"  ✗ {label} ({host}:{port}) 不可达: {e}")

        all_ok = all("✓" in r for r in results)

        if not all_ok:
            # 再测试一下最基础的 DNS
            try:
                socket.getaddrinfo("www.baidu.com", 80, timeout=5)
                results.append("  ✓ DNS 解析正常 (www.baidu.com)")
            except Exception:
                results.append("  ✗ DNS 解析失败 — 网络可能完全不通")

        if not all_ok:
            return CheckItem(
                category="network",
                name="网络连通性",
                severity=Severity.WARNING,
                passed=False,
                message="部分 API 端点不可达",
                detail="\n".join(results),
                suggestion="请检查网络连接和防火墙设置。Windows Sandbox 默认有网络访问权限，如果无法连接请检查宿主机代理/VPN 设置。"
            )
        else:
            return CheckItem(
                category="network",
                name="网络连通性",
                severity=Severity.INFO,
                passed=True,
                message="网络连接正常 ✓",
                detail="\n".join(results),
                suggestion=""
            )

    # ── 7) 系统资源 ─────────────────────────────────────────

    def _check_resources(self) -> CheckItem:
        """检查系统内存。"""
        issues = []
        detail = "内存信息不可用"

        # 优先使用 psutil（跨平台）
        try:
            import psutil
            mem = psutil.virtual_memory()
            available_gb = mem.available / (1024 ** 3)
            total_gb = mem.total / (1024 ** 3)
            detail = f"可用内存: {available_gb:.1f} GB / 总计: {total_gb:.1f} GB"
            if available_gb < 0.5:
                issues.append(
                    f"可用内存极低: {available_gb:.1f} GB / {total_gb:.1f} GB"
                )
        except ImportError:
            # psutil 不在核心依赖中，回退到平台 API
            try:
                if platform.system() == "Windows":
                    class MEMORYSTATUSEX(ctypes.Structure):
                        _fields_ = [
                            ("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                        ]
                    mem = MEMORYSTATUSEX()
                    mem.dwLength = ctypes.sizeof(mem)
                    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
                    available_gb = mem.ullAvailPhys / (1024 ** 3)
                    total_gb = mem.ullTotalPhys / (1024 ** 3)
                    detail = f"可用内存: {available_gb:.1f} GB / 总计: {total_gb:.1f} GB"
                    if available_gb < 0.5:
                        issues.append(f"可用内存极低: {available_gb:.1f} GB / {total_gb:.1f} GB")
                else:
                    detail = "内存信息不可用（非 Windows 平台且 psutil 未安装）"
            except Exception:
                detail = "无法获取内存信息"

        if issues:
            return CheckItem(
                category="resource",
                name="系统资源",
                severity=Severity.WARNING,
                passed=False,
                message=f"系统资源紧张: {len(issues)} 项",
                detail="\n".join(f"  • {i}" for i in issues),
                suggestion="请关闭其他应用程序以释放内存。"
            )
        else:
            return CheckItem(
                category="resource",
                name="系统资源",
                severity=Severity.INFO,
                passed=True,
                message="系统资源充足 ✓",
                detail=detail,
                suggestion=""
            )


# ═══════════════════════════════════════════════════════════════
#  全局报告缓存（main.py 写入，api.py 读取，避免循环导入）
# ═══════════════════════════════════════════════════════════════

_cached_report: Optional[RuntimeReport] = None


def set_cached_report(report: RuntimeReport):
    global _cached_report
    _cached_report = report


def get_cached_report() -> Optional[RuntimeReport]:
    return _cached_report


# ═══════════════════════════════════════════════════════════════
#  便捷函数
# ═══════════════════════════════════════════════════════════════

def run_startup_check(app_dir: str) -> RuntimeReport:
    """启动时运行完整性检测，返回报告。"""
    checker = RuntimeChecker(app_dir)
    return checker.run_all_checks()


def format_report_for_user(report: RuntimeReport) -> str:
    """将报告格式化为面向用户的纯文本消息。"""
    lines = []
    lines.append("=" * 60)
    lines.append("  NORP Agent — 运行时完整性检测报告")
    lines.append("=" * 60)
    lines.append(f"环境类型: {report.environment_type}")
    lines.append(f"健康状态: {'[通过]' if report.overall_healthy else '[存在问题]'}")
    lines.append(f"FATAL: {report.fatal_count}  ERROR: {report.error_count}  "
                 f"WARNING: {report.warning_count}  INFO: {report.info_count}")
    lines.append("-" * 60)

    for check in report.checks:
        if not check.passed or check.severity in (Severity.FATAL, Severity.ERROR):
            icon = {"fatal": "[FATAL]", "error": "[ERROR]", "warning": "[WARN]", "info": "[INFO]"}[check.severity.value]
            lines.append(f"{icon} [{check.severity.value.upper()}] {check.name}")
            lines.append(f"   {check.message}")
            if check.detail:
                for d in check.detail.split("\n"):
                    lines.append(f"   {d}")
            if check.suggestion:
                lines.append(f"   >> {check.suggestion}")
            lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)

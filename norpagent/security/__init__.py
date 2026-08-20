# Copyright (c) 2026 xingluosama121, MIT Licensed
"""norpagent.security — 插件安全防护套件（零依赖核心 + 可选 cryptography）。

迁移自现有应用的 plugin_system / jailbreak_guard，按 pip 库形态重组：

- ``audit``：AST 源码审计（危险调用 / 导入 / 反射绕过检测）；
- ``network_policy``：插件网络策略 / SSRF 防护；
- ``approval``：人工审批策略（原生工具确认 + 插件工具审批）；
- ``guard``：越狱 / 提示词注入防护 + 系统提示词加固；
- ``signature``：Ed25519 插件签名（``norpagent[security]`` 提供 cryptography）。

完整加载管线（发现 → 签名校验 → 审计 → 导入限制 → 注册）
见 norpagent.plugins.loader。
"""

from norpagent.security.audit import (
    DANGEROUS_CALLS,
    DANGEROUS_FUNC_NAMES,
    DANGEROUS_IMPORTS,
    SecurityIssue,
    Severity,
    SourceAuditor,
    PluginSecurity,
)
from norpagent.security.approval import ApprovalLevel, ApprovalPolicy
from norpagent.security.guard import (
    CRITICAL_PATTERNS,
    WARNING_PATTERNS,
    ZERO_WIDTH_CHARS,
    harden_system_prompt,
    is_jailbreak_attempt,
    scan_message,
)
from norpagent.security.network_policy import (
    NetworkDecision,
    NetworkPolicy,
    is_internal_host,
)
from norpagent.security.signature import (
    SignatureResult,
    SignatureStatus,
    SignatureVerifier,
    crypto_available,
    generate_keypair,
    sign_plugin_file,
    verify_bytes,
)

__all__ = [
    "DANGEROUS_CALLS",
    "DANGEROUS_FUNC_NAMES",
    "DANGEROUS_IMPORTS",
    "SecurityIssue",
    "Severity",
    "SourceAuditor",
    "PluginSecurity",
    "ApprovalLevel",
    "ApprovalPolicy",
    "CRITICAL_PATTERNS",
    "WARNING_PATTERNS",
    "ZERO_WIDTH_CHARS",
    "harden_system_prompt",
    "is_jailbreak_attempt",
    "scan_message",
    "NetworkDecision",
    "NetworkPolicy",
    "is_internal_host",
    "SignatureResult",
    "SignatureStatus",
    "SignatureVerifier",
    "crypto_available",
    "generate_keypair",
    "sign_plugin_file",
    "verify_bytes",
]

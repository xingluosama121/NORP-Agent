# Vibe Coding Agent - Plugin System
# Copyright (c) 2026 xingluosama
#
# This package contains the plugin framework infrastructure
# (PluginManager, PluginContext, PluginSecurity).
# Actual plugin files belong in the plugins/ directory.

from plugin_system.context import PluginContext, SimpleLogger
from plugin_system.manager import PluginManager, PluginInfo, HOOK_NAMES, HOOK_TIMEOUT
from plugin_system.security import (
    PluginSecurity, SecurityIssue, Severity,
    PluginImportBlocker, StrictImportBlocker, ResourceLimiter,
    _loading_plugin,
)
from plugin_system.signature import (
    SignatureVerifier, SignatureResult, SignatureStatus,
    OFFICIAL_PUBLIC_KEY,
)
from plugin_system.network_policy import (
    NetworkPolicy, NetworkDecision,
    POLICY_DENY, POLICY_AUDITED_PUBLIC, POLICY_PUBLIC_ONLY, POLICY_ALLOW_ALL,
)
from plugin_system.approval import ApprovalPolicy, ApprovalLevel

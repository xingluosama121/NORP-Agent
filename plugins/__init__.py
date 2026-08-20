# Vibe Coding Agent - Plugin System (backward-compat shim)
# Copyright (c) 2026 xingluosama
#
# NOTE: The plugin framework has moved to plugin_system/.
# This file re-exports from the new location for backward compatibility.
# New code should import from plugin_system directly.

from plugin_system.context import PluginContext, SimpleLogger
from plugin_system.manager import PluginManager, PluginInfo, HOOK_NAMES
from plugin_system.security import PluginSecurity, SecurityIssue, Severity, PluginImportBlocker, StrictImportBlocker, ResourceLimiter

# Copyright (c) 2026 xingluosama121, MIT Licensed
"""norpagent.plugins — 外部插件系统（库化：加载器 + 门面 + 进程隔离）。

安全管线：签名校验 → AST 审计 → 权限声明 → 导入限制 → 注册。
P4 起支持进程级插件隔离（插件代码只在宿主子进程，JSON 行协议 RPC）。

    # 便捷入口
    from norpagent.plugins import install_plugin_dirs
    loader = install_plugin_dirs(registry, ["my_plugins"], config={...})

    # 库化门面（生命周期 + 状态 + 热重载）
    from norpagent.plugins import PluginSystem
    ps = PluginSystem(registry, ["my_plugins"])
    ps.load(); ps.status(); ps.shutdown()

    # 进程级隔离（插件模块级 ISOLATION = "process" 或配置强制）
    from norpagent.plugins.isolation import ProcessIsolationManager

加载管线每个阶段都是钩子（PLUGIN_PIPELINE_LAYER 自定义层），
HookVeto 可一票否决加载 / 注册。
"""

from norpagent.plugins.loader import (
    HOOK_NAMES,
    PIPELINE_HOOK_NAMES,
    PLUGIN_MODULE_PREFIX,
    PluginContext,
    PluginInfo,
    PluginLoader,
    install_plugin_dirs,
)
from norpagent.plugins.manager import (
    PLUGIN_PIPELINE_LAYER,
    PluginSystem,
    before_plugin_discover,
    after_plugin_discover,
    before_plugin_load,
    after_plugin_load,
    before_plugin_audit,
    after_plugin_audit,
    before_plugin_register,
    after_plugin_register,
)
from norpagent.plugins.isolation import (
    DEFAULT_RPC_TIMEOUT,
    HOOK_TIMEOUT,
    ProcessIsolationManager,
    ProcessPluginHost,
)

__all__ = [
    "HOOK_NAMES",
    "PIPELINE_HOOK_NAMES",
    "PLUGIN_MODULE_PREFIX",
    "PluginContext",
    "PluginInfo",
    "PluginLoader",
    "install_plugin_dirs",
    "PluginSystem",
    "PLUGIN_PIPELINE_LAYER",
    "before_plugin_discover",
    "after_plugin_discover",
    "before_plugin_load",
    "after_plugin_load",
    "before_plugin_audit",
    "after_plugin_audit",
    "before_plugin_register",
    "after_plugin_register",
    "ProcessIsolationManager",
    "ProcessPluginHost",
    "HOOK_TIMEOUT",
    "DEFAULT_RPC_TIMEOUT",
]

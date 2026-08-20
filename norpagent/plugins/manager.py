# Copyright (c) 2026 xingluosama121, MIT Licensed
"""PluginSystem — 插件系统的库化门面（一个对象管理完整生命周期）。

    from norpagent.plugins import PluginSystem

    ps = PluginSystem(registry, ["my_plugins"], config={"plugin_isolation": "auto"})
    infos = ps.load()
    print(ps.status())
    ps.reload("my_tool")
    ps.shutdown()

与 PluginLoader 的关系：PluginSystem 是门面（配置 / 生命周期 /
状态查询），PluginLoader 是执行引擎（安全管线）。管线每个阶段
都是钩子：``PLUGIN_PIPELINE_LAYER`` 是以 HookLayer 声明的自定义层
（钩子体系「自定义层」能力的标准库用例），安装后即可
``registry.hooks.before_plugin_audit.subscribe(fn)`` 干预加载过程，
抛 HookVeto 可一票否决单个插件的加载 / 注册。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from norpagent.hooks.core import HookLayer
from norpagent.plugins.loader import (
    PIPELINE_HOOK_NAMES,
    PluginInfo,
    PluginLoader,
)

# ── 插件加载管线自定义层（钩子体系自定义层的标准库用例）──────

PLUGIN_PIPELINE_LAYER = HookLayer(
    "plugin_pipeline", order=200,
    description="插件加载管线（发现→签名→审计→注册），HookVeto 可一票否决",
)

before_plugin_discover = PLUGIN_PIPELINE_LAYER.hook(
    "before_plugin_discover", mutating=True,
    description="插件目录扫描前（HookVeto = 取消本次发现）",
    payload_keys=("dirs",),
)
after_plugin_discover = PLUGIN_PIPELINE_LAYER.hook(
    "after_plugin_discover",
    description="插件目录扫描完成",
    payload_keys=("loaded", "enabled"),
)
before_plugin_load = PLUGIN_PIPELINE_LAYER.hook(
    "before_plugin_load", mutating=True,
    description="单个插件进入安全管线前（HookVeto = 拒绝加载该插件）",
    payload_keys=("name", "path"),
)
after_plugin_load = PLUGIN_PIPELINE_LAYER.hook(
    "after_plugin_load",
    description="单个插件管线处理完毕",
    payload_keys=("name", "enabled"),
)
before_plugin_audit = PLUGIN_PIPELINE_LAYER.hook(
    "before_plugin_audit", mutating=True,
    description="AST 审计前（HookVeto = 拒绝加载该插件）",
    payload_keys=("name", "path", "audit_level"),
)
after_plugin_audit = PLUGIN_PIPELINE_LAYER.hook(
    "after_plugin_audit",
    description="AST 审计完成",
    payload_keys=("name", "path", "allowed", "issues"),
)
before_plugin_register = PLUGIN_PIPELINE_LAYER.hook(
    "before_plugin_register", mutating=True,
    description="注册进 Registry 前（HookVeto = 拒绝注册）",
    payload_keys=("name", "tools", "isolation"),
)
after_plugin_register = PLUGIN_PIPELINE_LAYER.hook(
    "after_plugin_register",
    description="注册进 Registry 完成",
    payload_keys=("name", "enabled", "tools", "isolation"),
)


class PluginSystem:
    """插件系统门面：发现 → 安全加载 → 注册 → 生命周期管理。"""

    def __init__(
        self,
        registry: Any,
        plugin_dirs: Optional[List[str]] = None,
        config: Optional[dict] = None,
    ) -> None:
        self.registry = registry
        self.plugin_dirs = [str(d) for d in (plugin_dirs or [])]
        self.config: Dict[str, Any] = dict(config or {})
        # 把管线层安装到注册表钩子体系（同名钩子已存在时保留原定义）
        registry.hooks.install_layer(PLUGIN_PIPELINE_LAYER)
        self._loader: Optional[PluginLoader] = None

    # ── 配置 ─────────────────────────────────────────────

    def configure(self, config: dict) -> "PluginSystem":
        self.config.update(config or {})
        self._loader = None  # 配置变更后重建加载器
        return self

    @property
    def loader(self) -> PluginLoader:
        if self._loader is None:
            self._loader = PluginLoader(self.plugin_dirs, self.config)
        return self._loader

    @property
    def plugins(self) -> List[PluginInfo]:
        return self.loader.plugins

    # ── 生命周期 ─────────────────────────────────────────

    def load(self) -> List[PluginInfo]:
        """扫描全部目录并注册到 Registry，返回插件元数据。"""
        return self.loader.discover_and_load(self.registry)

    def reload(self, plugin_name: str) -> bool:
        """热重载单个插件（开发用；生产建议重建 Registry）。"""
        return self.loader.reload(self.registry, plugin_name)

    def unload(self, plugin_name: str) -> bool:
        return self.loader.unload(self.registry, plugin_name)

    def status(self) -> Dict[str, Any]:
        """系统状态：插件清单 + 隔离宿主状态。"""
        loader = self.loader
        iso_status = None
        if loader._isolation_manager is not None:
            try:
                iso_status = loader._isolation_manager.status()
            except Exception:
                iso_status = {"alive": False}
        return {
            "dirs": list(loader._plugin_dirs),
            "config": dict(loader.config),
            "isolation": iso_status,
            "plugins": [
                {
                    "name": p.name,
                    "version": p.version,
                    "enabled": p.enabled,
                    "tools": list(p.tools),
                    "hook_names": list(p.hook_names),
                    "signature_status": p.signature_status,
                    "trusted": p.trusted,
                    "error": p.error,
                }
                for p in loader.plugins
            ],
        }

    def shutdown(self) -> None:
        """释放全部资源（进程隔离宿主子进程）。"""
        if self._loader is not None:
            self._loader.shutdown()


__all__ = [
    "PluginSystem",
    "PLUGIN_PIPELINE_LAYER",
    "PIPELINE_HOOK_NAMES",
    "before_plugin_discover",
    "after_plugin_discover",
    "before_plugin_load",
    "after_plugin_load",
    "before_plugin_audit",
    "after_plugin_audit",
    "before_plugin_register",
    "after_plugin_register",
]

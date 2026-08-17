# Copyright (c) 2026 xingluosama121, MIT Licensed
"""norpagent.safe() — 安全系统整体剥离的一站式入口。

整个安全体系（越狱防护 / 提示词加固 / 人工审批 / 网络策略 / 源码审计 /
导入限制 / 签名信任 / 插件隔离策略）被收敛为一个独立函数：

    from norpagent import safe, Registry

    reg = Registry()
    safe(reg, level="standard")        # 一句话开启全套安全，返回 SafetyKit

    kit = safe(level="high")           # 或先拿套件，稍后安装
    kit.install(reg)

实现方式（与 9 层钩子体系同构）：

- 输入防护 = L3 ``before_input`` 钩子订阅者（命中即 HookVeto）；
- 提示词加固 = L5 ``before_build_messages`` 可变钩子（改写系统提示词）；
- 人工审批 / 网络策略等运行态决策 = ``registry.security``（SecurityContext），
  由 AgentRuntime 与插件加载器读取；
- 插件加载管线 = ``SecurityContext.plugin_config()`` 默认配置，
  ``norpagent.plugins.install_plugin_dirs`` 未显式给 config 时自动采用。

三级预设：basic（防护+提示）+ standard（+导入限制/网络拒绝/审批）
+ high（+审计拒绝/权限声明/强制可信签名）。全部策略可用 config dict
逐项覆盖，粒度与 norpagent.security 各模块一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from norpagent.security.approval import ApprovalPolicy
from norpagent.security.guard import (
    harden_system_prompt as _harden_system_prompt,
)
from norpagent.security.guard import scan_message as _scan_message
from norpagent.security.network_policy import (
    NetworkPolicy,
    POLICY_DENY,
    POLICY_ALLOW_ALL,
)

# 三级安全预设
LEVEL_BASIC = "basic"
LEVEL_STANDARD = "standard"
LEVEL_HIGH = "high"
VALID_LEVELS = (LEVEL_BASIC, LEVEL_STANDARD, LEVEL_HIGH)


@dataclass
class SecurityContext:
    """registry.security — 运行态安全策略的唯一事实源。

    AgentRuntime 在每次输入扫描 / 提示词加固 / 工具审批时读取本对象；
    插件加载器在 config 缺省时读取 ``plugin_config()``。
    ``params`` 里的显式开关（jailbreak_guard / harden_prompt / approval_*）
    优先级高于本上下文——安全可收紧，不被任务参数悄悄放宽。
    """

    level: str = LEVEL_STANDARD
    guard_enabled: bool = True
    harden_enabled: bool = True
    audit_level: str = "warn"            # off / warn / block
    import_restrict: str = "safe"        # off / safe / strict
    require_permissions: bool = False
    signature_verify: bool = True
    signature_required: bool = False     # True：仅 trusted 签名可加载
    trusted_keys: List[str] = field(default_factory=list)
    network_policy: str = POLICY_DENY    # deny / audited_public / public_only / allow_all
    approval_config: Optional[Dict[str, Any]] = None
    plugin_isolation: str = "auto"       # auto / inproc / process
    extra: Dict[str, Any] = field(default_factory=dict)

    def plugin_config(self) -> Dict[str, Any]:
        """转换为插件加载器配置（config 缺省时的兜底）。"""
        approval_enabled = True
        if isinstance(self.approval_config, dict):
            approval_enabled = bool(
                self.approval_config.get("approval_enabled", True)
            )
        return {
            "plugin_security_audit": self.audit_level,
            "plugin_security_import_restrict": self.import_restrict,
            "plugin_security_require_permissions": self.require_permissions,
            "plugin_signature_verify": self.signature_verify,
            "plugin_signature_required": self.signature_required,
            "plugin_trusted_keys": list(self.trusted_keys),
            "plugin_network_policy": self.network_policy,
            "approval_enabled": approval_enabled,
            "plugin_isolation": self.plugin_isolation,
        }

    def to_dict(self) -> Dict[str, Any]:
        data = self.plugin_config()
        data.update({
            "level": self.level,
            "guard_enabled": self.guard_enabled,
            "harden_enabled": self.harden_enabled,
            "approval_config": dict(self.approval_config or {}),
            "extra": dict(self.extra),
        })
        return data


class SafetyKit:
    """安全套件：safe() 的返回值。

    - ``install(registry)``：挂载 SecurityContext + 钩子订阅者；
    - 一组直接可用的独立安全检查 API（代理 norpagent.security）。
    """

    def __init__(self, context: SecurityContext) -> None:
        self.context = context
        self._installed: List[Any] = []
        self._hook_subscriptions: List[Tuple[Any, str]] = []
        self._approval_cache: Optional[ApprovalPolicy] = None
        self._network_cache: Optional[NetworkPolicy] = None

    # ── 安装 ──────────────────────────────────────────────

    def install(self, registry: Any) -> "SafetyKit":
        """把全套安全策略装到注册表：SecurityContext + 钩子订阅者。"""
        registry.security = self.context
        hooks = registry.hooks

        # L3 输入防护：命中即 HookVeto（任务以 stopped 收尾）。
        # params["jailbreak_guard"]=False 可显式关闭（按任务级覆盖）。
        if self.context.guard_enabled:
            from norpagent.hooks.core import HookVeto

            def guard_input(event: Any) -> Any:
                params = (event.get("params") or {})
                if params.get("jailbreak_guard") is False:
                    return None
                user_input = event.get("user_input") or ""
                blocked, reason, _ = self.scan_input(user_input)
                if blocked:
                    raise HookVeto(reason or "输入被安全防护拦截")
                return None

            hooks.before_input.subscribe(guard_input)
            self._hook_subscriptions.append((guard_input, "before_input"))

        # L5 提示词加固：改写系统提示词。
        if self.context.harden_enabled:
            def harden_messages(event: Any) -> Any:
                params = (event.get("params") or {})
                if params.get("harden_prompt") is False:
                    return None
                prompt = event.get("system_prompt") or ""
                tool_names = event.get("tool_names") or []
                return {"system_prompt": self.harden(prompt, tool_names)}

            def harden_wrapper(event: Any) -> Any:
                # 兼容没有 tool_names 的 payload：从参数侧取
                return harden_messages(event)

            hooks.before_build_messages.subscribe(harden_wrapper)
            self._hook_subscriptions.append(
                (harden_wrapper, "before_build_messages")
            )

        self._installed.append(registry)
        return self

    def uninstall(self, registry: Any) -> None:
        """卸载本套件：退订钩子订阅者、清除安全上下文。

        运行中热挂载 security 槽位时先 uninstall 旧套件再安装新套件，
        避免同一总线上重复叠加防护钩子。
        """
        for fn, event_name in self._hook_subscriptions:
            try:
                registry.bus.unsubscribe(fn, event_name)
            except Exception:  # noqa: BLE001 — 总线状态异常不阻塞卸载
                pass
        self._hook_subscriptions.clear()
        try:
            if getattr(registry, "security", None) is self.context:
                registry.security = None
        except Exception:  # noqa: BLE001
            pass
        if registry in self._installed:
            self._installed.remove(registry)

    # ── 独立安全检查 API（代理 norpagent.security）─────────

    def scan_input(self, text: str) -> Tuple[bool, Optional[str], List[str]]:
        """越狱 / 注入扫描。返回 (是否拦截, 原因, 命中描述列表)。"""
        return _scan_message(text)

    def is_jailbreak_attempt(self, text: str) -> bool:
        blocked, _, _ = self.scan_input(text)
        return blocked

    def harden(self, prompt: str, tool_names: List[str]) -> str:
        """系统提示词加固（注入核心规则与工具清单）。"""
        return _harden_system_prompt(prompt, tool_names)

    def audit_file(self, path: str) -> List[dict]:
        """插件源码 AST 审计。返回问题列表（severity/line/code/message/category）。"""
        from norpagent.security.audit import SourceAuditor

        auditor = SourceAuditor(self.context.audit_level)
        issues, _allowed = auditor.audit_file(path, audit_level=self.context.audit_level)
        return [i.to_dict() for i in issues]

    def audit_source(self, source: str) -> List[dict]:
        """审计源码字符串。返回问题列表。"""
        from norpagent.security.audit import SourceAuditor

        auditor = SourceAuditor(self.context.audit_level)
        issues, _allowed = auditor.audit_source(source, audit_level=self.context.audit_level)
        return [i.to_dict() for i in issues]

    def verify_plugin(self, path: str, manifest: Optional[dict] = None) -> Any:
        """插件签名校验，返回 SignatureResult。"""
        from norpagent.security.signature import SignatureVerifier

        verifier = SignatureVerifier({
            "plugin_signature_verify": self.context.signature_verify,
            "plugin_trusted_keys": self.context.trusted_keys,
        })
        return verifier.verify(path, manifest)

    def check_network(self, url: str) -> bool:
        """按当前网络策略裁决一次网络访问（SSRF 防护）。"""
        return self.network_policy().check_url(url).allowed

    def approval_policy(self, tool_hints: Optional[dict] = None) -> ApprovalPolicy:
        """构造与当前配置一致的审批策略实例。"""
        policy = ApprovalPolicy(self.context.approval_config or {})
        policy.set_tool_hints(tool_hints or {})
        return policy

    def network_policy(self) -> NetworkPolicy:
        """构造与当前配置一致的网络策略实例。"""
        if self._network_cache is None:
            self._network_cache = NetworkPolicy({
                "plugin_network_policy": self.context.network_policy,
            })
        return self._network_cache

    def describe(self) -> Dict[str, Any]:
        """人类可读的当前安全姿态描述。"""
        return self.context.to_dict()

    def __repr__(self) -> str:
        return f"<SafetyKit level={self.context.level}>"


# ── 三级预设 ──────────────────────────────────────────────


def _build_context(level: str, config: Optional[dict],
                   overrides: Optional[dict]) -> SecurityContext:
    level = level or LEVEL_STANDARD
    if level not in VALID_LEVELS:
        raise ValueError(f"未知安全级别 '{level}'。可选: {VALID_LEVELS}")

    ctx = SecurityContext(level=level)
    if level == LEVEL_BASIC:
        ctx.import_restrict = "off"
        ctx.network_policy = POLICY_ALLOW_ALL
        ctx.approval_config = {"approval_enabled": False}
    elif level == LEVEL_STANDARD:
        pass  # 默认即 standard
    elif level == LEVEL_HIGH:
        ctx.audit_level = "block"
        ctx.require_permissions = True
        ctx.signature_required = True

    if config:
        _apply_config(ctx, config)
    if overrides:
        _apply_config(ctx, overrides)
    return ctx


def _apply_config(ctx: SecurityContext, config: dict) -> None:
    """把配置 dict 映射到 SecurityContext（含插件加载器风格键名）。"""
    mapping = {
        "guard_enabled": ("guard_enabled", bool),
        "harden_enabled": ("harden_enabled", bool),
        "plugin_security_audit": ("audit_level", str),
        "plugin_security_import_restrict": ("import_restrict", str),
        "plugin_security_require_permissions": ("require_permissions", bool),
        "plugin_signature_verify": ("signature_verify", bool),
        "plugin_signature_required": ("signature_required", bool),
        "plugin_network_policy": ("network_policy", str),
        "plugin_isolation": ("plugin_isolation", str),
    }
    for key, (attr, cast) in mapping.items():
        if key in config and config[key] is not None:
            setattr(ctx, attr, cast(config[key]))
    if "plugin_trusted_keys" in config:
        keys = config["plugin_trusted_keys"]
        if isinstance(keys, (list, tuple)):
            ctx.trusted_keys = [str(k) for k in keys if k]
    if "approval" in config and isinstance(config["approval"], dict):
        ctx.approval_config = dict(config["approval"])
    elif "approval_config" in config and isinstance(config["approval_config"], dict):
        ctx.approval_config = dict(config["approval_config"])


def safe(registry: Any = None, *, level: str = LEVEL_STANDARD,
         config: Optional[dict] = None, **overrides: Any) -> SafetyKit:
    """norpagent.safe() — 一句话开启全套安全系统。

    用法::

        safe(reg)                          # standard 级
        safe(reg, level="high")            # 严格级
        kit = safe(level="basic", config={...})  # 自定义，稍后 kit.install(reg)

    ``registry`` 给定时立即安装（SecurityContext + 输入防护/提示词加固钩子），
    返回 SafetyKit；不给定则返回未安装的套件。``config`` / ``**overrides``
    可逐项覆盖预设（键名与 norpagent.security / 插件加载器配置一致）。
    """
    kit = SafetyKit(_build_context(level, config, overrides or None))
    if registry is not None:
        kit.install(registry)
    return kit


__all__ = [
    "safe",
    "SafetyKit",
    "SecurityContext",
    "LEVEL_BASIC",
    "LEVEL_STANDARD",
    "LEVEL_HIGH",
    "VALID_LEVELS",
]

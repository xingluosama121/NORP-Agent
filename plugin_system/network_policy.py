# Vibe Coding Agent - 插件网络策略 / SSRF 防护模块（P0-4）
# Copyright (c) 2026 xingluosama
#
# 四粒度网络策略（plugin_network_policy）：
#   "deny"            — 不允许插件访问任何网络（默认，最安全）
#   "audited_public"  — 仅允许访问「受审计的公网」：必须命中 URL 白名单 / 域名白名单
#   "public_only"     — 允许访问全部公网，但禁止访问内网 / 环回 / 链路本地地址
#   "allow_all"       — 允许访问全部网络（含内网；仅建议受信任插件）
#
# 内网拦截（SSRF 防护）：无论何种粒度，只要策略不是 allow_all，
# 一律拒绝指向私网 / 保留网段 / 环回 / 链路本地 / 元数据地址的目标。

import ipaddress
import socket
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urlparse

# 网络策略取值
POLICY_DENY = "deny"
POLICY_AUDITED_PUBLIC = "audited_public"
POLICY_PUBLIC_ONLY = "public_only"
POLICY_ALLOW_ALL = "allow_all"

VALID_POLICIES = (POLICY_DENY, POLICY_AUDITED_PUBLIC, POLICY_PUBLIC_ONLY, POLICY_ALLOW_ALL)

# 云元数据服务地址（SSRF 高价值目标，强制拦截）
_METADATA_HOSTS = {
    "169.254.169.254",           # AWS / GCP / Azure 等
    "metadata.google.internal",  # GCP
    "metadata",                  # 部分环境
}


@dataclass
class NetworkDecision:
    """一次网络访问的裁决结果。"""
    allowed: bool
    reason: str = ""


def _parse_host(url: str) -> Optional[str]:
    """从 URL 提取主机名（不含端口）。"""
    try:
        parsed = urlparse(url if "://" in url else f"http://{url}")
        host = parsed.hostname
        return host
    except Exception:
        return None


def _resolve(host: str) -> List[str]:
    """解析主机名，返回 IP 字符串列表。失败返回空列表。"""
    try:
        infos = socket.getaddrinfo(host, None)
        ips = []
        for info in infos:
            addr = info[4][0]
            if addr not in ips:
                ips.append(addr)
        return ips
    except Exception:
        return []


def _is_internal_ip(ip_str: str) -> bool:
    """判断 IP 是否为内网 / 保留 / 环回 / 链路本地地址。"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # 无法解析视为内部/不可信
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_internal_host(host: str) -> bool:
    """判断主机是否指向内网 / 环回 / 元数据地址。

    优先做文本级判断（不触发 DNS 解析，防 DNS rebinding 的常见路径），
    再对解析出的每个 IP 做判断。
    """
    if not host:
        return True
    host_l = host.strip().lower()

    # 元数据服务
    if host_l in _METADATA_HOSTS:
        return True

    # 直接是 IP 字面量
    try:
        ip = ipaddress.ip_address(host_l)
        return _is_internal_ip(host_l)
    except ValueError:
        pass

    # 内网常见域名 / localhost 别名
    if host_l in ("localhost", "localhost.localdomain") or host_l.endswith(".localhost"):
        return True
    if host_l.endswith(".local") or host_l.endswith(".internal") or host_l.endswith(".lan"):
        return True

    # DNS 解析后逐个 IP 判断
    for ip_str in _resolve(host):
        if _is_internal_ip(ip_str):
            return True
    return False


class NetworkPolicy:
    """插件网络策略引擎。

    参数
    ----
    config : dict
        config.json 全量配置。相关键：
        - ``plugin_network_policy`` : "deny"（默认）| "audited_public" | "public_only" | "allow_all"
        - ``plugin_network_url_allowlist`` : list[str]（audited_public 粒度的 URL 白名单）
        - ``plugin_network_domain_allowlist`` : list[str]（audited_public 粒度的域名白名单）
    """

    def __init__(self, config: Optional[dict] = None):
        config = config or {}
        self.policy = config.get("plugin_network_policy", POLICY_DENY)
        if self.policy not in VALID_POLICIES:
            self.policy = POLICY_DENY
        self.url_allowlist = self._norm_list(config.get("plugin_network_url_allowlist"))
        self.domain_allowlist = self._norm_list(config.get("plugin_network_domain_allowlist"))

    @staticmethod
    def _norm_list(v) -> List[str]:
        if not isinstance(v, (list, tuple)):
            return []
        out = []
        for item in v:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out

    def check_url(self, url: str) -> NetworkDecision:
        """裁决一个 URL 是否允许访问。"""
        if not url or not isinstance(url, str):
            return NetworkDecision(False, "空 URL")

        host = _parse_host(url)
        if not host:
            return NetworkDecision(False, f"无法解析 URL 主机：{url}")

        # deny：一律拒绝
        if self.policy == POLICY_DENY:
            return NetworkDecision(False, "网络策略为 deny，插件禁止访问网络")

        # 任何非 allow_all 的策略都强制拦截内网目标（SSRF 防护）
        if self.policy != POLICY_ALLOW_ALL:
            if is_internal_host(host):
                return NetworkDecision(False, f"SSRF 拦截：禁止访问内网/环回/元数据地址（{host}）")

        # audited_public：必须命中白名单
        if self.policy == POLICY_AUDITED_PUBLIC:
            if not self._in_allowlist(url, host):
                return NetworkDecision(False, f"URL 未命中白名单（{host}）")

        # public_only / allow_all：放行
        return NetworkDecision(True, "ok")

    def _in_allowlist(self, url: str, host: str) -> bool:
        """检查 URL/域名是否命中白名单（支持前缀匹配）。"""
        for allowed in self.url_allowlist:
            if url == allowed or url.startswith(allowed):
                return True
        for allowed_domain in self.domain_allowlist:
            if host == allowed_domain or host.endswith("." + allowed_domain):
                return True
        return False

    def get_config(self) -> dict:
        return {
            "policy": self.policy,
            "url_allowlist": list(self.url_allowlist),
            "domain_allowlist": list(self.domain_allowlist),
        }

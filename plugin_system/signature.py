# Vibe Coding Agent - 插件签名 / 来源校验模块（P0-5）
# Copyright (c) 2026 xingluosama
#
# 自定「NORP 插件签名协议」v1：
#   - 算法：Ed25519（基于 cryptography 库，非对称签名）
#   - 签名对象：插件入口文件字节的 SHA-256 摘要（规范化为「哈希 → 签名」两段式，
#     与具体文件编码无关，跨平台稳定）
#   - 签名存储：
#       * package 插件  → manifest.json 的 "signature" 字段
#       * 单文件插件    → sidecar 文件 "<入口文件>.sig"（JSON）
#   - 信任模型：
#       * 内置 NORP Studio 官方公钥（硬编码）
#       * 用户可在 config.json → plugin_trusted_keys 追加自定义信任公钥
#
# 签名/来源校验默认开启，可在设置里关闭（plugin_signature_verify = false）。

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

ALGORITHM = "ed25519"

# NORP Studio 官方公钥（用于签名官方插件）。
# 对应的私钥仅存于开发者本地，绝不出现在源码 / 打包产物中。
OFFICIAL_PUBLIC_KEY = "199adcb6801cb969212120487df0b1af4c9099f1ec597873cd9f6216cea09178"

# 视觉外挂专属签名公钥（vision_agent.py 等视觉官方插件）。
# 与官方公钥并列信任：两者签出的插件均走 trusted 通道
# （审计放宽为 warn、导入限制 off）。私钥仅存开发者本地。
VISION_PUBLIC_KEY = "5cefeac5f416786edae0373b8fdff9d07702547441f5cda9a640b79a960c406a"


class SignatureStatus:
    """签名校验结果状态。"""
    TRUSTED = "trusted"        # 验签通过且公钥受信任
    UNTRUSTED = "untrusted"    # 验签通过但公钥不在信任列表
    INVALID = "invalid"        # 验签失败（文件被篡改 / 签名损坏）
    UNSIGNED = "unsigned"      # 无签名信息
    DISABLED = "disabled"      # 签名校验被关闭


@dataclass
class SignatureResult:
    """一次签名校验的结果。"""
    status: str
    reason: str = ""
    public_key: str = ""
    signed_files: List[str] = field(default_factory=list)
    verified_files: List[str] = field(default_factory=list)

    @property
    def is_trusted(self) -> bool:
        return self.status == SignatureStatus.TRUSTED

    @property
    def passed(self) -> bool:
        """签名校验是否「放行」（trusted 或 disabled 视为放行）。"""
        return self.status in (SignatureStatus.TRUSTED, SignatureStatus.DISABLED)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "public_key": self.public_key,
            "signed_files": self.signed_files,
            "verified_files": self.verified_files,
        }


# ── 底层 Ed25519 原语 ────────────────────────────────────────────

def _import_ed25519():
    """惰性导入 cryptography，失败时抛出自定义错误。"""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey, Ed25519PublicKey,
        )
        from cryptography.hazmat.primitives import serialization
        return Ed25519PrivateKey, Ed25519PublicKey, serialization
    except ImportError as exc:
        raise RuntimeError(
            "插件签名校验需要 cryptography 库（pip install cryptography）"
        ) from exc


def hash_file(path: str) -> bytes:
    """计算文件内容的 SHA-256 摘要（规范化字节）。"""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).digest()


def sign_bytes(privkey_hex: str, data: bytes) -> bytes:
    """用 Ed25519 私钥对数据签名。"""
    Ed25519PrivateKey, _, _ = _import_ed25519()
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(privkey_hex))
    return key.sign(data)


def verify_bytes(pubkey_hex: str, signature_hex: str, data: bytes) -> bool:
    """用 Ed25519 公钥验证签名。"""
    try:
        _, Ed25519PublicKey, _ = _import_ed25519()
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
        key.verify(bytes.fromhex(signature_hex), data)
        return True
    except Exception:
        return False


def generate_keypair() -> Tuple[str, str]:
    """生成一对 Ed25519 密钥，返回 (公钥 hex, 私钥 hex)。"""
    Ed25519PrivateKey, _, serialization = _import_ed25519()
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    priv = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption()).hex()
    return pub, priv


# ── 签名存储格式 ─────────────────────────────────────────────────

def build_signature_block(pubkey_hex: str, signature_hex: str,
                          signed_files: Optional[List[str]] = None) -> dict:
    """构造签名块（写入 manifest 的 "signature" 字段或 .sig 文件）。"""
    block = {
        "algorithm": ALGORITHM,
        "public_key": pubkey_hex,
        "signature": signature_hex,
    }
    if signed_files:
        block["signed_files"] = signed_files
    return block


def _load_sig_sidecar(entry_path: str) -> Optional[dict]:
    """从 sidecar 文件 "<entry>.sig" 读取签名块。"""
    sig_path = entry_path + ".sig"
    if not os.path.isfile(sig_path):
        return None
    try:
        with open(sig_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _extract_signature_block(entry_path: str,
                             manifest: Optional[dict]) -> Optional[dict]:
    """从 manifest 或 sidecar 提取签名块。"""
    # 1) manifest 优先
    if manifest:
        sig = manifest.get("signature")
        if isinstance(sig, dict) and sig.get("signature"):
            return sig
    # 2) sidecar
    return _load_sig_sidecar(entry_path)


# ── 签名校验器 ───────────────────────────────────────────────────

class SignatureVerifier:
    """插件签名校验器。

    参数
    ----
    config : dict
        config.json 全量配置。相关键：
        - ``plugin_signature_verify`` : bool（默认 True）
        - ``plugin_trusted_keys``     : list[str]（用户自定义信任公钥）
    """

    def __init__(self, config: Optional[dict] = None):
        config = config or {}
        self.enabled: bool = bool(config.get("plugin_signature_verify", True))
        self._trusted_keys: List[str] = [OFFICIAL_PUBLIC_KEY, VISION_PUBLIC_KEY]
        user_keys = config.get("plugin_trusted_keys", [])
        if isinstance(user_keys, (list, tuple)):
            for k in user_keys:
                if isinstance(k, str) and k.strip():
                    self._trusted_keys.append(k.strip())

    @property
    def trusted_keys(self) -> List[str]:
        return list(self._trusted_keys)

    def add_trusted_key(self, pubkey_hex: str):
        """动态添加一个信任公钥（运行时）。"""
        if pubkey_hex and pubkey_hex not in self._trusted_keys:
            self._trusted_keys.append(pubkey_hex)

    def is_trusted_key(self, pubkey_hex: str) -> bool:
        return pubkey_hex in self._trusted_keys

    def verify(self, entry_path: str,
               manifest: Optional[dict] = None) -> SignatureResult:
        """校验插件入口文件的签名。

        返回 SignatureResult，其 status 决定 manager 如何处置插件。
        """
        if not self.enabled:
            return SignatureResult(
                status=SignatureStatus.DISABLED,
                reason="签名校验已关闭",
            )

        block = _extract_signature_block(entry_path, manifest)
        if not block:
            return SignatureResult(
                status=SignatureStatus.UNSIGNED,
                reason="插件未签名（manifest.signature 或 .sig 文件缺失）",
            )

        pubkey = block.get("public_key", "")
        signature = block.get("signature", "")
        algorithm = block.get("algorithm", ALGORITHM)

        if not pubkey or not signature:
            return SignatureResult(
                status=SignatureStatus.INVALID,
                reason="签名块缺少 public_key 或 signature 字段",
            )

        if algorithm != ALGORITHM:
            return SignatureResult(
                status=SignatureStatus.INVALID,
                reason=f"不支持的签名算法：{algorithm}",
            )

        # 确定需要验签的文件列表（默认仅入口文件本身）
        signed_files = block.get("signed_files") or []
        if not signed_files:
            signed_files = [os.path.basename(entry_path)]

        # 逐文件验签：对每个文件内容做「哈希 → 验签」
        # 协议约定：多文件时对「排序后的文件名:哈希」拼接再做一次签名，
        # 以保证文件集合的完整性。这里为简洁采用「每个文件独立签名」的
        # 描述需要匹配签名工具 —— 见 _sign_plugin.py 中 sign() 的实现。
        verified = []
        entry_dir = os.path.dirname(os.path.abspath(entry_path))
        for rel_name in signed_files:
            full = rel_name if os.path.isabs(rel_name) else os.path.join(entry_dir, rel_name)
            if not os.path.isfile(full):
                return SignatureResult(
                    status=SignatureStatus.INVALID,
                    reason=f"签名清单中的文件不存在：{rel_name}",
                    public_key=pubkey,
                    signed_files=signed_files,
                )
            digest = hash_file(full)
            if not verify_bytes(pubkey, signature, digest):
                return SignatureResult(
                    status=SignatureStatus.INVALID,
                    reason=f"签名验证失败：文件被篡改或签名不匹配（{rel_name}）",
                    public_key=pubkey,
                    signed_files=signed_files,
                    verified_files=verified,
                )
            verified.append(rel_name)

        # 验签通过 —— 判断公钥是否受信任
        if self.is_trusted_key(pubkey):
            return SignatureResult(
                status=SignatureStatus.TRUSTED,
                reason="签名有效且公钥受信任",
                public_key=pubkey,
                signed_files=signed_files,
                verified_files=verified,
            )

        return SignatureResult(
            status=SignatureStatus.UNTRUSTED,
            reason="签名有效但公钥不在信任列表",
            public_key=pubkey,
            signed_files=signed_files,
            verified_files=verified,
        )

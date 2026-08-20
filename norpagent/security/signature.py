# Copyright (c) 2026 xingluosama121, MIT Licensed
"""插件签名 / 来源校验（NORP 插件签名协议 v1）。

迁移自现有应用 plugin_system.signature，协议不变：

- 算法：Ed25519（cryptography 库，按需懒加载）；
- 签名对象：插件入口文件字节的 SHA-256 摘要；
- 签名存储：manifest.json 的 "signature" 字段 / sidecar ``<入口>.sig`` 文件；
- 信任模型：内置官方公钥 + 用户自定义信任公钥
  （config → plugin_trusted_keys）。

注意：``cryptography`` 属于可选依赖（``pip install norpagent[security]``）。
未安装时签名校验返回 UNAVAILABLE——按「不受信任」处理（永不当作 trusted），
安全姿态不因缺依赖而放宽。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

ALGORITHM = "ed25519"

# NORP Studio 官方公钥（签名官方插件；私钥仅存开发者本地）。
OFFICIAL_PUBLIC_KEY = "199adcb6801cb969212120487df0b1af4c9099f1ec597873cd9f6216cea09178"
# 视觉外挂专属签名公钥（与官方公钥并列信任）。
VISION_PUBLIC_KEY = "5cefeac5f416786edae0373b8fdff9d07702547441f5cda9a640b79a960c406a"


class SignatureStatus:
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    INVALID = "invalid"
    UNSIGNED = "unsigned"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"  # 缺少 cryptography：视为不受信任


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
        """是否「放行」：trusted 或 disabled（校验关闭）视为放行。"""
        return self.status in (SignatureStatus.TRUSTED, SignatureStatus.DISABLED)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "public_key": self.public_key,
            "signed_files": self.signed_files,
            "verified_files": self.verified_files,
        }


# ── 底层 Ed25519 原语（惰性导入）───────────────────────────

def _import_ed25519():
    """惰性导入 cryptography，失败返回 None（调用方按 UNAVAILABLE 处理）。"""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # type: ignore
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
        from cryptography.hazmat.primitives import serialization  # type: ignore

        return Ed25519PrivateKey, Ed25519PublicKey, serialization
    except ImportError:
        return None


def crypto_available() -> bool:
    return _import_ed25519() is not None


def hash_file(path: str) -> bytes:
    """计算文件内容的 SHA-256 摘要。"""
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).digest()


def generate_keypair() -> Optional[Tuple[str, str]]:
    """生成一对 Ed25519 密钥，返回 (公钥 hex, 私钥 hex)。缺依赖返回 None。"""
    loaded = _import_ed25519()
    if loaded is None:
        return None
    Ed25519PrivateKey, _, serialization = loaded
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    priv = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    ).hex()
    return pub, priv


def sign_bytes(privkey_hex: str, data: bytes) -> bytes:
    """用 Ed25519 私钥对数据签名（缺依赖抛 RuntimeError）。"""
    loaded = _import_ed25519()
    if loaded is None:
        raise RuntimeError(
            "插件签名需要 cryptography 库（pip install norpagent[security]）"
        )
    Ed25519PrivateKey, _, _ = loaded
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(privkey_hex))
    return key.sign(data)


def verify_bytes(pubkey_hex: str, signature_hex: str, data: bytes) -> bool:
    """用 Ed25519 公钥验证签名（缺依赖返回 False）。"""
    loaded = _import_ed25519()
    if loaded is None:
        return False
    try:
        _, Ed25519PublicKey, _ = loaded
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
        key.verify(bytes.fromhex(signature_hex), data)
        return True
    except Exception:
        return False


# ── 签名存储格式 ──────────────────────────────────────────

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


def sign_plugin_file(entry_path: str, privkey_hex: str) -> dict:
    """对插件入口文件签名，写入 sidecar ``<entry>.sig``，返回签名块。

    与现有应用 _sign_plugin.sign_file 一致：签名对象为入口文件字节的
    SHA-256 摘要，signed_files 固定为 [入口文件 basename]。
    """
    import os.path

    entry_path = os.path.abspath(entry_path)
    digest = hash_file(entry_path)
    sig_hex = sign_bytes(privkey_hex, digest).hex()
    block = build_signature_block(
        _pubkey_from_priv(privkey_hex), sig_hex,
        signed_files=[os.path.basename(entry_path)],
    )
    _write_sidecar(entry_path + ".sig", block)
    return block


def _pubkey_from_priv(privkey_hex: str) -> str:
    loaded = _import_ed25519()
    if loaded is None:
        raise RuntimeError(
            "插件签名需要 cryptography 库（pip install norpagent[security]）"
        )
    Ed25519PrivateKey, _, serialization = loaded
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(privkey_hex))
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()


def _write_sidecar(sig_path: str, block: dict) -> None:
    with open(sig_path, "w", encoding="utf-8") as fh:
        json.dump(block, fh, ensure_ascii=False, indent=2)


def _load_sig_sidecar(entry_path: str) -> Optional[dict]:
    sig_path = entry_path + ".sig"
    if not os.path.isfile(sig_path):
        return None
    try:
        with open(sig_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _extract_signature_block(entry_path: str,
                             manifest: Optional[dict]) -> Optional[dict]:
    if manifest:
        sig = manifest.get("signature")
        if isinstance(sig, dict) and sig.get("signature"):
            return sig
    return _load_sig_sidecar(entry_path)


# ── 签名校验器 ────────────────────────────────────────────


class SignatureVerifier:
    """插件签名校验器。"""

    def __init__(self, config: Optional[dict] = None) -> None:
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

    def add_trusted_key(self, pubkey_hex: str) -> None:
        if pubkey_hex and pubkey_hex not in self._trusted_keys:
            self._trusted_keys.append(pubkey_hex)

    def is_trusted_key(self, pubkey_hex: str) -> bool:
        return pubkey_hex in self._trusted_keys

    def verify(self, entry_path: str,
               manifest: Optional[dict] = None) -> SignatureResult:
        """校验插件入口文件的签名，返回 SignatureResult。"""
        if not self.enabled:
            return SignatureResult(
                status=SignatureStatus.DISABLED, reason="签名校验已关闭",
            )
        if not crypto_available():
            return SignatureResult(
                status=SignatureStatus.UNAVAILABLE,
                reason="未安装 cryptography（pip install norpagent[security]），签名按不受信任处理",
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
                reason=f"不支持的签名算法: {algorithm}",
            )

        signed_files = block.get("signed_files") or []
        if not signed_files:
            signed_files = [os.path.basename(entry_path)]

        verified = []
        entry_dir = os.path.dirname(os.path.abspath(entry_path))
        for rel_name in signed_files:
            full = rel_name if os.path.isabs(rel_name) else os.path.join(entry_dir, rel_name)
            if not os.path.isfile(full):
                return SignatureResult(
                    status=SignatureStatus.INVALID,
                    reason=f"签名清单中的文件不存在: {rel_name}",
                    public_key=pubkey,
                    signed_files=signed_files,
                )
            digest = hash_file(full)
            if not verify_bytes(pubkey, signature, digest):
                return SignatureResult(
                    status=SignatureStatus.INVALID,
                    reason=f"签名验证失败: 文件被篡改或签名不匹配（{rel_name}）",
                    public_key=pubkey,
                    signed_files=signed_files,
                    verified_files=verified,
                )
            verified.append(rel_name)

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

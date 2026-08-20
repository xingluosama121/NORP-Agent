# Vibe Coding Agent - 视觉/操作外挂 IPC 协议层（自定规范）
# Copyright (c) 2026 xingluosama
#
# 消息格式（见 docs/vision_agent_design.md 第 4 节）：
#   外层 XML 标签作信封（承载控制元信息），内层 JSON 承载业务数据（CDATA 包裹）。
#   - 指令消息（主架构 → 外挂）：<vision-op ...><payload><![CDATA[{...}]]></payload></vision-op>
#   - 结果消息（外挂 → 主架构）：<vision-result ...><payload>...</payload></vision-result>
#
# 信封/负载分离：XML 属性只放控制字段（version/op/risk/token/ttl_ms/ts/status），
# 业务数据（坐标、文本、窗口信息）全部沉到 JSON，互不污染。
#
# 本模块只负责「编解码 + 信封校验」，不含传输层（命名管道 / socket 等）。

import hashlib
import hmac
import json
import secrets
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple
from xml.sax.saxutils import quoteattr

PROTOCOL_VERSION = "1.0"

OP_TAG = "vision-op"
RESULT_TAG = "vision-result"
EVENT_TAG = "vision-event"
HELLO_TAG = "vision-hello"
CHALLENGE_TAG = "vision-challenge"
AUTH_TAG = "vision-auth"
ACK_TAG = "vision-hello-ack"
PING_TAG = "vision-ping"
PONG_TAG = "vision-pong"

# 帧/会话层常量（与 vision_ipc_transport.py 对齐）
TOKEN_HEX_LEN = 64                     # HMAC-SHA256 hex 长度
TOKEN_FRESHNESS_SEC = 10               # token ts 新鲜度窗口（秒）
SIGN_PREFIX_OP = "vipc-op|"            # op 令牌签名前缀
SIGN_PREFIX_AUTH = "vipc-auth|"        # 握手 proof 签名前缀


class ResultStatus:
    """结果消息 status 枚举。"""
    APPROVED = "approved"          # 已放行并执行
    REJECTED = "rejected"          # 被裁决器拒绝
    VETOED = "vetoed"              # 被用户否决
    CIRCUIT_OPEN = "circuit_open"  # 熔断中
    TIMEOUT = "timeout"            # 令牌/指令超时
    REQUIRES_CONFIRMATION = "requires_confirmation"  # 需用户确认
    ERROR = "error"                # 执行异常
    OK = "ok"                      # 管理类 op 成功


class EventType:
    """<vision-event> 的 event 枚举（见 docs/vision_ipc_protocol.md 4.3）。"""
    CIRCUIT_OPENED = "circuit_opened"
    CIRCUIT_HALF_OPEN = "circuit_half_open"
    CIRCUIT_CLOSED = "circuit_closed"
    OVERRIDE_ENGAGED = "override_engaged"
    OVERRIDE_RESUMED = "override_resumed"
    IDLE_LOCKED = "idle_locked"
    IDLE_UNLOCKED = "idle_unlocked"
    BANNER_CHANGED = "banner_changed"
    DAEMON_SHUTDOWN = "daemon_shutdown"


def new_nonce(byte_len: int = 16) -> str:
    """密码学随机 nonce（secrets 标准库）。"""
    return secrets.token_hex(byte_len)


class IPCError(Exception):
    """协议编解码/校验错误。"""


@dataclass
class Message:
    """解析后的消息对象。"""
    kind: str                       # "op" | "result" | "event" | "hello" | "challenge"
                                    # | "auth" | "ack" | "ping" | "pong"
    op: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    version: str = PROTOCOL_VERSION
    risk: Optional[str] = None      # L0~L3（op 消息必填）
    token: Optional[str] = None     # 一次性授权令牌（op 消息必填）
    ttl_ms: Optional[int] = None    # 令牌有效期（毫秒）
    ts: Optional[int] = None        # 时间戳（毫秒）
    status: Optional[str] = None    # result 消息的 status 枚举
    id: Optional[str] = None        # 请求 ID（op/result 关联）
    confirm: Optional[str] = None   # "user" = 主架构用户审批链路已批准
    event: Optional[str] = None     # event 消息的事件名
    auth_ok: Optional[bool] = None  # hello-ack 的认证结论
    reason: Optional[str] = None    # hello-ack 的失败原因
    session_id: Optional[str] = None  # 握手成功后分配的会话 ID
    role: Optional[str] = None      # hello 的客户端角色
    nonce_c: Optional[str] = None   # hello 的客户端 nonce
    nonce_s: Optional[str] = None   # challenge 的服务端 nonce
    proof: Optional[str] = None     # auth 的 HMAC 应答
    extra: Dict[str, str] = field(default_factory=dict)  # 未知属性兜底（向前兼容）


def _now_ms() -> int:
    return int(time.time() * 1000)


def _attr(name: str, value) -> str:
    """构造 XML 属性（含转义，防注入）。value 为 None 时省略。"""
    if value is None:
        return ""
    return f" {name}={quoteattr(str(value))}"


def _payload_to_cdata(payload: Dict[str, Any]) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    # CDATA 不能包含 "]]>" 序列，出现则拒绝（业务数据里几乎不可能出现）
    if "]]>" in payload_json:
        raise IPCError("payload 含非法序列 ]]>，无法安全放入 CDATA")
    return payload_json


def build_op(
    op: str,
    risk: str,
    payload: Dict[str, Any],
    token: str,
    ttl_ms: int = 3000,
    ts: Optional[int] = None,
    version: str = PROTOCOL_VERSION,
    id: Optional[str] = None,
    confirm: Optional[str] = None,
) -> str:
    """构造指令消息（主架构 → 外挂）。

    id      请求 ID（8 hex 随机），result 原样回带（并发请求关联）。
    confirm "user" = 主架构用户审批链路已批准（审批弹窗点过「批准」）。
    """
    if not token:
        raise IPCError("指令消息必须有 token")
    ts = _now_ms() if ts is None else int(ts)
    cdata = _payload_to_cdata(payload)
    return (
        f'<{OP_TAG}{_attr("version", version)}{_attr("op", op)}{_attr("risk", risk)}'
        f'{_attr("token", token)}{_attr("ttl_ms", ttl_ms)}{_attr("ts", ts)}'
        f'{_attr("id", id)}{_attr("confirm", confirm)}>'
        f"<payload><![CDATA[{cdata}]]></payload>"
        f"</{OP_TAG}>"
    )


def build_result(
    op: str,
    status: str,
    payload: Dict[str, Any],
    risk: Optional[str] = None,
    ts: Optional[int] = None,
    version: str = PROTOCOL_VERSION,
    id: Optional[str] = None,
) -> str:
    """构造结果/审计消息（外挂 → 主架构）。id 回带请求 ID。"""
    ts = _now_ms() if ts is None else int(ts)
    cdata = _payload_to_cdata(payload)
    return (
        f'<{RESULT_TAG}{_attr("version", version)}{_attr("op", op)}'
        f'{_attr("status", status)}{_attr("risk", risk)}{_attr("ts", ts)}'
        f'{_attr("id", id)}>'
        f"<payload><![CDATA[{cdata}]]></payload>"
        f"</{RESULT_TAG}>"
    )


def build_event(
    event: str,
    payload: Dict[str, Any],
    ts: Optional[int] = None,
    version: str = PROTOCOL_VERSION,
) -> str:
    """构造事件推送消息（外挂 → 主架构，主动上报，无需应答）。"""
    ts = _now_ms() if ts is None else int(ts)
    cdata = _payload_to_cdata(payload)
    return (
        f'<{EVENT_TAG}{_attr("version", version)}{_attr("event", event)}'
        f'{_attr("ts", ts)}>'
        f"<payload><![CDATA[{cdata}]]></payload>"
        f"</{EVENT_TAG}>"
    )


def build_hello(role: str, nonce_c: str, version: str = PROTOCOL_VERSION) -> str:
    """构造握手第 1 步：客户端打招呼。"""
    return (
        f'<{HELLO_TAG}{_attr("version", version)}{_attr("role", role)}'
        f'{_attr("nonce_c", nonce_c)}{_attr("ts", _now_ms())}/>'
    )


def build_challenge(nonce_s: str, ts: Optional[int] = None,
                    version: str = PROTOCOL_VERSION) -> str:
    """构造握手第 2 步：服务端挑战。"""
    ts = _now_ms() if ts is None else int(ts)
    return (
        f'<{CHALLENGE_TAG}{_attr("version", version)}{_attr("nonce_s", nonce_s)}'
        f'{_attr("ts", ts)}/>'
    )


def build_auth(proof: str, version: str = PROTOCOL_VERSION) -> str:
    """构造握手第 3 步：客户端 HMAC 应答。"""
    return f'<{AUTH_TAG}{_attr("version", version)}{_attr("proof", proof)}/>'


def build_ack(auth_ok: bool, reason: str = "", session_id: Optional[str] = None,
              version: str = PROTOCOL_VERSION) -> str:
    """构造握手第 4 步：服务端结论。"""
    return (
        f'<{ACK_TAG}{_attr("version", version)}'
        f'{_attr("auth_ok", "true" if auth_ok else "false")}'
        f'{_attr("reason", reason)}{_attr("session_id", session_id)}'
        f'{_attr("ts", _now_ms())}/>'
    )


def build_ping(version: str = PROTOCOL_VERSION) -> str:
    return f'<{PING_TAG}{_attr("version", version)}{_attr("ts", _now_ms())}/>'


def build_pong(version: str = PROTOCOL_VERSION) -> str:
    return f'<{PONG_TAG}{_attr("version", version)}{_attr("ts", _now_ms())}/>'


# ═══════════════════════════════════════════════════════════════
#  HMAC 令牌（一次性授权令牌 + 握手 proof，标准库 hmac/hashlib）
# ═══════════════════════════════════════════════════════════════

def payload_digest(payload: Dict[str, Any]) -> str:
    """payload 的规范 JSON 摘要（与签名/验签双方保持字节级一致）。

    sort_keys + 固定 separators 保证 dict 键序无关；截取 SHA-256 前 16 hex。
    """
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def sign_op_token(secret: str, op: str, risk: str, ts: int,
                  req_id: str, payload: Dict[str, Any]) -> str:
    """主架构自签一次性授权令牌（见 docs/vision_ipc_protocol.md 3.2）。"""
    material = (
        f"{SIGN_PREFIX_OP}{op}|{risk}|{int(ts)}|{req_id}|"
        f"{payload_digest(payload)}"
    )
    return hmac.new(secret.encode("utf-8"), material.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_op_token(secret: str, op: str, risk: str, ts: int,
                    req_id: str, payload: Dict[str, Any],
                    token: Optional[str]) -> bool:
    """daemon 侧验签：等长比较 + 常量时间比较（hmac.compare_digest）。"""
    if not token or len(token) != TOKEN_HEX_LEN:
        return False
    expected = sign_op_token(secret, op, risk, ts, req_id, payload)
    return hmac.compare_digest(token.lower(), expected)


def sign_auth_proof(secret: str, nonce_c: str, nonce_s: str,
                    challenge_ts: int) -> str:
    """握手第 3 步的 proof：HMAC-SHA256(secret, vipc-auth|nonce_c|nonce_s|ts)。"""
    material = f"{SIGN_PREFIX_AUTH}{nonce_c}|{nonce_s}|{int(challenge_ts)}"
    return hmac.new(secret.encode("utf-8"), material.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_auth_proof(secret: str, nonce_c: str, nonce_s: str,
                      challenge_ts: int, proof: Optional[str]) -> bool:
    if not proof or len(proof) != TOKEN_HEX_LEN:
        return False
    expected = sign_auth_proof(secret, nonce_c, nonce_s, challenge_ts)
    return hmac.compare_digest(proof.lower(), expected)


def parse_message(xml_str: str) -> Message:
    """解析消息（op / result），返回 Message。"""
    if not xml_str or not xml_str.strip():
        raise IPCError("空消息")
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        raise IPCError(f"XML 解析失败：{e}")

    tag = root.tag
    kind_map = {
        OP_TAG: "op",
        RESULT_TAG: "result",
        EVENT_TAG: "event",
        HELLO_TAG: "hello",
        CHALLENGE_TAG: "challenge",
        AUTH_TAG: "auth",
        ACK_TAG: "ack",
        PING_TAG: "ping",
        PONG_TAG: "pong",
    }
    kind = kind_map.get(tag)
    if kind is None:
        raise IPCError(f"未知消息根元素：{tag}")

    op = root.get("op") or ""
    if kind in ("op", "result") and not op:
        raise IPCError("缺少 op 字段")

    # payload 在 CDATA 中，ET 解析后成为 <payload> 元素的 text
    payload_node = root.find("payload")
    payload_text = (payload_node.text or "") if payload_node is not None else ""
    payload_text = payload_text.strip()
    try:
        payload = json.loads(payload_text) if payload_text else {}
    except json.JSONDecodeError as e:
        raise IPCError(f"payload JSON 解析失败：{e}")
    if not isinstance(payload, dict):
        raise IPCError("payload 必须是 JSON 对象")

    def _int_opt(key):
        v = root.get(key)
        return int(v) if v else None

    def _bool_opt(key):
        v = root.get(key)
        if v is None:
            return None
        return v.lower() == "true"

    known_attrs = {
        "version", "op", "risk", "token", "ttl_ms", "ts", "status",
        "id", "confirm", "event", "auth_ok", "reason", "session_id",
        "role", "nonce_c", "nonce_s", "proof",
    }
    extra = {k: v for k, v in root.attrib.items() if k not in known_attrs}

    return Message(
        kind=kind,
        op=op,
        payload=payload,
        version=root.get("version", PROTOCOL_VERSION),
        risk=root.get("risk"),
        token=root.get("token"),
        ttl_ms=_int_opt("ttl_ms"),
        ts=_int_opt("ts"),
        status=root.get("status"),
        id=root.get("id"),
        confirm=root.get("confirm"),
        event=root.get("event"),
        auth_ok=_bool_opt("auth_ok"),
        reason=root.get("reason"),
        session_id=root.get("session_id"),
        role=root.get("role"),
        nonce_c=root.get("nonce_c"),
        nonce_s=root.get("nonce_s"),
        proof=root.get("proof"),
        extra=extra,
    )


def validate_op(
    msg: Message,
    expected_token: Optional[str] = None,
    now_ms: Optional[int] = None,
) -> Tuple[bool, str]:
    """校验指令消息：类型 / 版本 / 令牌 / 有效期。返回 (是否通过, 原因)。"""
    if msg.kind != "op":
        return False, f"不是指令消息（kind={msg.kind}）"
    if msg.version != PROTOCOL_VERSION:
        return False, f"协议版本不匹配：收到 {msg.version}，期望 {PROTOCOL_VERSION}"
    if not msg.token:
        return False, "缺少授权令牌"
    if expected_token is not None and msg.token != expected_token:
        return False, "令牌不匹配"
    if msg.ts is None or msg.ttl_ms is None:
        return False, "缺少时间戳或有效期"
    now = _now_ms() if now_ms is None else int(now_ms)
    if now - msg.ts > msg.ttl_ms:
        return False, "指令已过期（ttl 超时）"
    return True, "ok"

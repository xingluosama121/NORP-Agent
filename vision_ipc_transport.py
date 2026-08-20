# Vibe Coding Agent - 视觉/操作外挂 IPC 传输层（loopback socket + 自研帧格式）
# Copyright (c) 2026 xingluosama
#
# 职责：把 vision_ipc.py 的消息（XML 信封 + JSON 负载）搬到 TCP 回环通道上：
#   - 帧格式：8 字节头（magic "VIPC" + uint32 小端长度）+ UTF-8 XML 消息体
#   - 会话握手：HMAC 挑战应答（四次消息，每连接一次认证机会）
#   - 一次性令牌：每条 <vision-op> 由客户端用共享 secret 自签，daemon 验签防重放
#   - VipcClient：连接 + 握手 + 同步请求/响应（req_id 关联）+ 事件回调
#   - VipcServer：accept 循环 + 鉴权会话分发（on_session 回调交给业务层）
#
# 协议定义见 docs/vision_ipc_protocol.md。自研约束：仅 Python 标准库
# （socket/threading/hmac/hashlib/secrets/struct/json/xml.etree），零第三方依赖。

import json
import secrets
import socket
import struct
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from vision_ipc import (
    AUTH_TAG,
    HELLO_TAG,
    IPCError,
    Message,
    PROTOCOL_VERSION,
    TOKEN_FRESHNESS_SEC,
    build_ack,
    build_auth,
    build_challenge,
    build_event,
    build_hello,
    build_op,
    build_ping,
    build_pong,
    build_result,
    new_nonce,
    parse_message,
    sign_auth_proof,
    sign_op_token,
    verify_auth_proof,
    verify_op_token,
)

# ── 帧格式常量 ──
MAGIC = b"VIPC"
HEADER_FMT = "<4sI"          # magic + payload_len（uint32 小端）
LEN_FMT = "<I"               # 长度字段单独格式
HEADER_SIZE = struct.calcsize(HEADER_FMT)
MAX_FRAME = 16 * 1024 * 1024  # 16 MiB 硬顶，防内存攻击

DEFAULT_PORT = 38476
HANDSHAKE_TIMEOUT = 10.0      # 握手整体超时（秒）
IDLE_TIMEOUT = 600.0          # 会话空闲超时（秒）
DEFAULT_TTL_MS = 3000         # op 指令默认有效期
REQUEST_TIMEOUT = 120.0       # 客户端请求默认超时（秒）
REPLAY_WINDOW_SEC = 300       # 已用 token 保留时长（秒）


class TransportError(Exception):
    """传输层错误（帧损坏 / 断连 / 超时）。"""


class AuthError(TransportError):
    """握手认证失败。"""


class ConnectionClosed(TransportError):
    """连接被对端关闭。"""


# ═══════════════════════════════════════════════════════════════
#  帧编解码（自研：magic + 长度前缀；FeedDecoder 处理粘包/半包）
# ═══════════════════════════════════════════════════════════════

def encode_frame(xml_str: str) -> bytes:
    """消息字符串 → 一帧（magic + length + UTF-8 体）。"""
    body = xml_str.encode("utf-8")
    if not body:
        raise TransportError("空消息体")
    if len(body) > MAX_FRAME:
        raise TransportError(f"消息体 {len(body)} 字节超过上限 {MAX_FRAME}")
    return MAGIC + struct.pack(LEN_FMT, len(body)) + body


def _decode_one(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as e:
        raise TransportError(f"消息体 UTF-8 解码失败：{e}")


class FeedDecoder:
    """流式帧解码器：feed() 喂入字节，产出完整 XML 消息字符串。

    TCP 是字节流，帧可能被拆散（半包）或粘连（粘包）。本类维护内部缓冲：
    只有凑够「完整头 + 完整体」才 yield 一条消息，其余留待下次 feed。
    """

    def __init__(self, max_frame: int = MAX_FRAME):
        self.max_frame = max_frame
        self._buf = bytearray()

    def feed(self, data: bytes):
        self._buf += data
        while True:
            msg = self._try_extract()
            if msg is None:
                return
            yield msg

    def _try_extract(self) -> Optional[str]:
        buf = self._buf
        if len(buf) < HEADER_SIZE:
            return None
        magic = bytes(buf[0:4])
        if magic != MAGIC:
            raise TransportError(f"帧头 magic 错误：{magic!r}（预期 {MAGIC!r}）")
        (body_len,) = struct.unpack(LEN_FMT, bytes(buf[4:HEADER_SIZE]))
        if body_len < 1 or body_len > self.max_frame:
            raise TransportError(f"帧长度非法：{body_len}")
        if len(buf) < HEADER_SIZE + body_len:
            return None  # 半包：等更多数据
        body = bytes(buf[HEADER_SIZE:HEADER_SIZE + body_len])
        del self._buf[:HEADER_SIZE + body_len]
        return _decode_one(body)

    @property
    def pending(self) -> int:
        return len(self._buf)


# ═══════════════════════════════════════════════════════════════
#  FrameSocket：对单个 socket 连接的帧级读写封装
# ═══════════════════════════════════════════════════════════════

class FrameSocket:
    """帧级读写：send(xml_str) / recv(timeout) → xml_str。

    线程安全：send 加锁（避免并发写粘帧）；recv 只应在单线程调用
    （客户端接收线程 / 服务端会话线程），与 send 不互斥。
    """

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._send_lock = threading.Lock()
        self._decoder = FeedDecoder()

    def fileno(self) -> int:
        return self._sock.fileno()

    def peer(self) -> str:
        try:
            return f"{self._sock.getpeername()[0]}:{self._sock.getpeername()[1]}"
        except OSError:
            return "?"

    def send_message(self, msg: Message) -> None:
        """发送一个 Message 对象（按 kind 序列化）。"""
        xml_str = _message_to_xml(msg)
        self.send_raw(xml_str)

    def send_raw(self, xml_str: str) -> None:
        frame = encode_frame(xml_str)
        with self._send_lock:
            try:
                self._sock.sendall(frame)
            except OSError as e:
                raise ConnectionClosed(f"发送失败：{e}")

    def recv_raw(self, timeout: Optional[float]) -> str:
        """阻塞取一帧（timeout=None 无限等待）。断连/超时抛异常。"""
        self._sock.settimeout(timeout)
        while True:
            try:
                data = self._sock.recv(65536)
            except socket.timeout:
                raise TransportError("接收超时")
            except OSError as e:
                raise ConnectionClosed(f"连接已断开：{e}")
            if not data:
                raise ConnectionClosed("对端关闭连接")
            for msg in self._decoder.feed(data):
                return msg

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


def _message_to_xml(msg: Message) -> str:
    """Message → XML（与 build_* 互补；op/result 走既有构造器保证格式一致）。"""
    kind = msg.kind
    if kind == "op":
        return build_op(
            msg.op, msg.risk or "L0", msg.payload, msg.token or "",
            ttl_ms=msg.ttl_ms or DEFAULT_TTL_MS, ts=msg.ts, id=msg.id,
            confirm=msg.confirm)
    if kind == "result":
        return build_result(
            msg.op, msg.status or "ok", msg.payload, risk=msg.risk,
            ts=msg.ts, id=msg.id)
    if kind == "event":
        return build_event(msg.event or "", msg.payload, ts=msg.ts)
    if kind == "hello":
        return build_hello(msg.role or "unknown", msg.nonce_c or new_nonce())
    if kind == "challenge":
        return build_challenge(msg.nonce_s or new_nonce(), ts=msg.ts)
    if kind == "auth":
        return build_auth(msg.proof or "")
    if kind == "ack":
        return build_ack(bool(msg.auth_ok), msg.reason or "", msg.session_id)
    if kind == "ping":
        return build_ping()
    if kind == "pong":
        return build_pong()
    raise TransportError(f"无法序列化的消息类型：{kind}")


# ═══════════════════════════════════════════════════════════════
#  握手（HMAC 挑战应答，四次消息，每连接一次认证机会）
# ═══════════════════════════════════════════════════════════════

def server_handshake(fsock: FrameSocket, secret: str,
                     timeout: float = HANDSHAKE_TIMEOUT) -> Tuple[Message, str]:
    """服务端握手：返回 (hello_msg, session_id)。失败抛 AuthError。"""
    deadline = time.time() + timeout

    def _left():
        return max(0.1, deadline - time.time())

    # 1. 首帧必须是 hello
    first = fsock.recv_raw(_left())
    hello = parse_message(first)
    if hello.kind != "hello":
        raise AuthError(f"首帧必须是 vision-hello，收到 {hello.kind}")
    if hello.version != PROTOCOL_VERSION:
        raise AuthError(f"协议版本不支持：{hello.version}")

    # 2. 发挑战
    nonce_s = new_nonce()
    challenge = Message(kind="challenge", nonce_s=nonce_s, ts=_now_ms_int())
    fsock.send_message(challenge)

    # 3. 收应答（唯一一次机会）
    auth_xml = fsock.recv_raw(_left())
    auth_msg = parse_message(auth_xml)
    if auth_msg.kind != "auth":
        raise AuthError(f"握手第 3 步必须是 vision-auth，收到 {auth_msg.kind}")
    if not verify_auth_proof(
            secret, hello.nonce_c or "", nonce_s, challenge.ts or 0,
            auth_msg.proof):
        # 先给对端一个明确拒绝，再断连
        try:
            fsock.send_message(Message(
                kind="ack", auth_ok=False, reason="HMAC 应答校验失败"))
        except TransportError:
            pass
        raise AuthError("HMAC 应答校验失败（secret 不匹配）")

    # 4. 回执
    session_id = new_nonce(8)
    fsock.send_message(Message(
        kind="ack", auth_ok=True, reason="ok", session_id=session_id))
    return hello, session_id


def client_handshake(fsock: FrameSocket, secret: str, role: str,
                     timeout: float = HANDSHAKE_TIMEOUT) -> str:
    """客户端握手：返回 session_id。失败抛 AuthError。"""
    deadline = time.time() + timeout

    def _left():
        return max(0.1, deadline - time.time())

    nonce_c = new_nonce()
    try:
        fsock.send_message(Message(kind="hello", role=role, nonce_c=nonce_c))
        challenge_xml = fsock.recv_raw(_left())
        challenge = parse_message(challenge_xml)
    except (TransportError, IPCError) as e:
        raise AuthError(f"握手失败：{e}")
    if challenge.kind != "challenge":
        raise AuthError(f"握手第 2 步必须是 vision-challenge，收到 {challenge.kind}")
    if challenge.version != PROTOCOL_VERSION:
        raise AuthError(f"服务端协议版本不支持：{challenge.version}")

    proof = sign_auth_proof(
        secret, nonce_c, challenge.nonce_s or "", challenge.ts or 0)
    try:
        fsock.send_message(Message(kind="auth", proof=proof))
        ack_xml = fsock.recv_raw(_left())
        ack = parse_message(ack_xml)
    except (TransportError, IPCError) as e:
        raise AuthError(f"认证被拒绝（连接关闭）：{e}")
    if ack.kind != "ack":
        raise AuthError(f"握手第 4 步必须是 vision-hello-ack，收到 {ack.kind}")
    if not ack.auth_ok:
        raise AuthError(f"认证被拒绝：{ack.reason or '未知原因'}")
    return ack.session_id or ""


def _now_ms_int() -> int:
    return int(time.time() * 1000)


# ═══════════════════════════════════════════════════════════════
#  VipcClient（主架构侧）：连接 + 握手 + 同步请求 + 事件回调
# ═══════════════════════════════════════════════════════════════

class PendingRequest:
    def __init__(self, req_id: str):
        self.req_id = req_id
        self.cond = threading.Condition()
        self.result: Optional[Message] = None
        self.error: Optional[str] = None


class VipcClient:
    """主架构侧客户端。

    用法：
        c = VipcClient("127.0.0.1", 38476, secret, role="plugin-host")
        c.connect()                       # 内部完成握手
        c.on_event(lambda msg: ...)       # 可选：订阅事件（接收线程回调）
        res = c.request("state", "L0", {})  # 同步请求，返回 Message
        c.close()
    """

    def __init__(self, host: str, port: int, secret: str, *,
                 role: str = "plugin-host",
                 handshake_timeout: float = HANDSHAKE_TIMEOUT,
                 request_timeout: float = REQUEST_TIMEOUT):
        if not secret:
            raise TransportError("secret 不能为空")
        self.host = host
        self.port = port
        self.secret = secret
        self.role = role
        self.handshake_timeout = handshake_timeout
        self.request_timeout = request_timeout
        self._sock: Optional[socket.socket] = None
        self._fsock: Optional[FrameSocket] = None
        self._session_id: Optional[str] = None
        self._req_seq = 0
        self._pending: Dict[str, PendingRequest] = {}
        self._pending_lock = threading.Lock()
        self._event_cb: Optional[Callable[[Message], None]] = None
        self._recv_thread: Optional[threading.Thread] = None
        self._closed = False

    # ── 生命周期 ──

    @property
    def connected(self) -> bool:
        return self._fsock is not None and not self._closed

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def connect(self) -> None:
        if self.connected:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.handshake_timeout)
            sock.connect((self.host, self.port))
        except OSError as e:
            raise TransportError(
                f"连接 daemon {self.host}:{self.port} 失败：{e}")
        fsock = FrameSocket(sock)
        try:
            self._session_id = client_handshake(
                fsock, self.secret, self.role, self.handshake_timeout)
        except Exception:
            fsock.close()
            raise
        self._sock, self._fsock = sock, fsock
        self._closed = False
        self._recv_thread = threading.Thread(
            target=self._recv_loop, name="vipc-client-recv", daemon=True)
        self._recv_thread.start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        fsock = self._fsock
        if fsock is not None:
            fsock.close()
        # 唤醒所有等待中的请求
        with self._pending_lock:
            pendings = list(self._pending.values())
            self._pending.clear()
        for p in pendings:
            with p.cond:
                p.error = "连接已关闭"
                p.cond.notify_all()

    def on_event(self, callback: Callable[[Message], None]) -> None:
        """注册事件回调（<vision-event> 到达时在接收线程中调用）。"""
        self._event_cb = callback

    # ── 请求 ──

    def _next_req_id(self) -> str:
        self._req_seq += 1
        return f"{int(time.time() * 1000) % 0x100000000:08x}{self._req_seq:04x}"

    def request(
        self,
        op: str,
        risk: str,
        payload: Dict[str, Any],
        *,
        confirm: Optional[str] = None,
        ttl_ms: int = DEFAULT_TTL_MS,
        timeout: Optional[float] = None,
    ) -> Message:
        """同步请求：发 <vision-op>（自动签名一次性令牌），等 <vision-result>。"""
        if not self.connected:
            raise TransportError("未连接（先调用 connect()）")
        req_id = self._next_req_id()
        ts = _now_ms_int()
        token = sign_op_token(
            self.secret, op, risk, ts, req_id, payload)
        msg = Message(
            kind="op", op=op, risk=risk, payload=payload,
            token=token, ttl_ms=ttl_ms, ts=ts, id=req_id, confirm=confirm)
        pending = PendingRequest(req_id)
        with self._pending_lock:
            self._pending[req_id] = pending
        try:
            try:
                self._fsock.send_message(msg)
            except Exception as e:
                with self._pending_lock:
                    self._pending.pop(req_id, None)
                raise TransportError(f"发送失败：{e}")
            wait = self.request_timeout if timeout is None else timeout
            with pending.cond:
                pending.cond.wait(wait)
            if pending.error is not None:
                raise TransportError(pending.error)
            if pending.result is None:
                raise TransportError(f"请求超时（{wait:.0f}s）：op={op}")
            return pending.result
        finally:
            with self._pending_lock:
                self._pending.pop(req_id, None)

    def ping(self, timeout: float = 10.0) -> float:
        """心跳：返回 daemon 处理耗时（秒）。"""
        t0 = time.time()
        self.request("ping", "L0", {}, timeout=timeout)
        return time.time() - t0

    # ── 接收线程 ──

    def _recv_loop(self) -> None:
        fsock = self._fsock
        while not self._closed:
            try:
                xml_str = fsock.recv_raw(1.0)
            except ConnectionClosed:
                break
            except TransportError:
                # 超时仅用于定期检查 _closed
                continue
            try:
                msg = parse_message(xml_str)
            except IPCError:
                continue  # 坏消息丢弃（协议层防御）
            if msg.kind == "result":
                with self._pending_lock:
                    pending = self._pending.get(msg.id or "")
                if pending is not None:
                    with pending.cond:
                        pending.result = msg
                        pending.cond.notify_all()
            elif msg.kind == "event":
                cb = self._event_cb
                if cb is not None:
                    try:
                        cb(msg)
                    except Exception:
                        pass  # 事件回调异常不影响接收线程
            # 其他类型（pong 等）忽略
        # 接收循环退出 → 通知所有等待请求失败
        with self._pending_lock:
            pendings = list(self._pending.values())
            self._pending.clear()
        for p in pendings:
            with p.cond:
                p.error = "连接已断开"
                p.cond.notify_all()
        self._closed = True


# ═══════════════════════════════════════════════════════════════
#  VipcServer（外挂进程侧）：accept 循环 + 鉴权 + 会话回调
# ═══════════════════════════════════════════════════════════════

class VipcServer:
    """服务端基类：bind 127.0.0.1 + accept 循环 + 每连接一个线程。

    on_session(fsock, session_id, hello_msg) 由业务层实现：
    在该回调里完成 recv/处理/send 循环；回调返回后连接关闭。
    鉴权（server_handshake）在回调之前完成，失败直接断开。
    """

    def __init__(self, port: int, secret: str, *,
                 host: str = "127.0.0.1",
                 handshake_timeout: float = HANDSHAKE_TIMEOUT,
                 log: Optional[Callable[[str], None]] = None):
        if not secret:
            raise TransportError("secret 不能为空")
        self.host = host
        self.port = port
        self.secret = secret
        self.handshake_timeout = handshake_timeout
        self.log = log or (lambda s: None)
        self._listener: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """bind + listen，启动 accept 线程。"""
        if self._running:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
        except OSError:
            sock.close()
            raise
        sock.listen(8)
        self._listener = sock
        self._running = True
        self._thread = threading.Thread(
            target=self._accept_loop, name="vipc-server-accept", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
            self._listener = None

    def _accept_loop(self) -> None:
        listener = self._listener
        while self._running:
            try:
                conn, _addr = listener.accept()
            except OSError:
                break  # listener 被关闭 → 退出
            t = threading.Thread(
                target=self._client_entry, args=(conn,),
                name="vipc-server-conn", daemon=True)
            t.start()

    def _client_entry(self, conn: socket.socket) -> None:
        fsock = FrameSocket(conn)
        try:
            hello, session_id = server_handshake(
                fsock, self.secret, self.handshake_timeout)
            self.on_session(fsock, session_id, hello)
        except (AuthError, TransportError, ConnectionClosed, IPCError) as e:
            self.log(f"[transport] 会话异常断开：{type(e).__name__}: {e}")
        except Exception as e:
            self.log(f"[transport] 业务层异常（连接必断）：{type(e).__name__}: {e}")
        finally:
            fsock.close()

    def on_session(self, fsock: FrameSocket, session_id: str,
                   hello: Message) -> None:
        """业务层实现：会话主循环。"""
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════
#  令牌防重放表（daemon 侧）
# ═══════════════════════════════════════════════════════════════

class TokenReplayGuard:
    """已用 token 记录：token → 使用时间；超 REPLAY_WINDOW_SEC 滚动清理。

    线程安全。校验顺序（由调用方保证）：签名 → 新鲜度 → 防重放。
    """

    def __init__(self, window_sec: float = REPLAY_WINDOW_SEC):
        self.window_sec = window_sec
        self._used: Dict[str, float] = {}
        self._lock = threading.Lock()

    def check_and_mark(self, token: str, ts: int) -> bool:
        """首次使用返回 True 并记录；重复使用返回 False。"""
        now = time.time()
        with self._lock:
            # 滚动清理过期记录（每次最多遍历一遍，量小无压力）
            expired = [k for k, v in self._used.items()
                       if now - v > self.window_sec]
            for k in expired:
                del self._used[k]
            if token in self._used:
                return False
            self._used[token] = now
            return True


# ═══════════════════════════════════════════════════════════════
#  便捷函数：open_client（lock 文件 → 连接）
# ═══════════════════════════════════════════════════════════════

def load_lock_file(app_dir: str, lock_fname: str = "vision_daemon.lock"
                   ) -> Optional[Dict[str, Any]]:
    """读 daemon lock 文件（JSON）。不存在/损坏返回 None。"""
    import os
    path = os.path.join(app_dir, lock_fname)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("port") and data.get("secret"):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def connect_via_lock(app_dir: str, *, role: str = "plugin-host",
                     lock_fname: str = "vision_daemon.lock"
                     ) -> Tuple[Optional[VipcClient], Optional[str]]:
    """按 lock 文件连接 daemon：返回 (client, error)。

    不负责拉起 daemon（拉起逻辑在插件桥 / daemon 管理侧），
    只做「读 lock → 连接 → 握手」。失败返回 (None, 原因)。
    """
    lock = load_lock_file(app_dir, lock_fname)
    if lock is None:
        return None, f"未找到 daemon lock 文件（{app_dir}/{lock_fname}），daemon 可能未运行"
    try:
        client = VipcClient(
            "127.0.0.1", int(lock["port"]), str(lock["secret"]), role=role)
        client.connect()
        return client, None
    except Exception as e:
        return None, f"连接 daemon 失败：{e}"

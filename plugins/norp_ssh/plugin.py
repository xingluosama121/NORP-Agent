# -*- coding: utf-8 -*-
"""
NORP SSH 插件（对齐 DeepSeek Harness 的 dsh-ssh）
=================================================
远程服务器运维能力，全部基于系统自带 OpenSSH（ssh.exe / scp.exe）实现，零第三方依赖：

· 主机管理    —— 主机配置持久化到 app_dir/norp_ssh/hosts.json（版本化 + 原子写入），
                 可导入 ~/.ssh/config（跳过通配符 Host，报告 skipped）
· ssh_list    —— 列出主机（含 keyReady / environment / location / 标签）
· ssh_test    —— 连通性测试（延迟测量）
· ssh_exec    —— 在远程主机执行命令（密钥 / 密码认证，stdout/stderr 分离）
· ssh_upload  —— 上传本地文件到远程（单文件）
· ssh_download—— 下载远程文件到本地（单文件）
· ssh_tunnel  —— 本地端口转发（访问远程数据库 / 内网服务）
· ssh_cluster —— 多主机并发执行同一命令（可按别名 / 环境 / 标签过滤，可设并发上限）
· 认证        —— 密钥认证（默认，OpenSSH）；密码认证（PuTTY plink/pscp）
· passphrase  —— 带口令的密钥：plink 以 -pw 兼作密钥口令；OpenSSH 需 ssh-agent
· 跳板机      —— 支持 ProxyJump（OpenSSH -J），跳板链用逗号分隔（多级）
· 持久连接池  —— OpenSSH ControlMaster=auto + ControlPersist（空闲 30 分钟自动断开），
                失败自动回退到普通连接（不影响可用性）

安全说明：
  1. 密码 / 密钥口令以明文存在 app_dir 下私有文件（POSIX 权限 0600），请勿公开该目录。
  2. 命令输出原样返回，可能包含敏感信息；请勿在日志中留存密钥。
  3. 上传/下载/执行会消耗真实远程资源，执行前请先确认。
"""

PLUGIN_NAME = "NORP SSH"
PLUGIN_PUBLISHER = "norp-community"
PLUGIN_VERSION = "1.1.0"
PLUGIN_DESCRIPTION = (
    "远程服务器 SSH 运维：主机管理（含 ~/.ssh/config 导入）、连通性测试、远程执行、"
    "上传下载、端口转发隧道、集群并发执行；支持密钥/密码认证、passphrase 密钥、"
    "ProxyJump 跳板链，以及 OpenSSH ControlMaster 持久连接池"
)

import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------
DEFAULT_TIMEOUT = 60
# 持久连接池：ControlMaster 复用连接，空闲 30 分钟自动断开（与 dsh-ssh 的池语义一致）
USE_CONTROL_MASTER = True
CONTROL_PERSIST = 1800
# 连接池失败回退的判定关键字（OpenSSH 报 mux / 未知选项时回退普通连接）
_CONTROL_ERR_HINTS = ("controlpath", "controlmaster", "mux", "bad configuration",
                      "unknown option")

# ----------------------------------------------------------------------
# 可执行文件定位（零依赖，优先系统 OpenSSH）
# ----------------------------------------------------------------------
def _find_exe(name):
    try:
        return shutil.which(name)
    except Exception:
        return None


def _resolve_ssh_bin():
    return _find_exe("ssh") or "ssh"


def _resolve_scp_bin():
    return _find_exe("scp") or "scp"


# ----------------------------------------------------------------------
# 模块级状态：隧道管理
# ----------------------------------------------------------------------
_TUNNELS = {}
_TUNNEL_LOCK = threading.Lock()
_TUNNEL_SEQ = 0
_REAPER_STARTED = False


def _ensure_reaper():
    """后台线程：定期回收已退出的隧道。"""
    global _REAPER_STARTED
    if _REAPER_STARTED:
        return
    _REAPER_STARTED = True

    def _loop():
        while True:
            time.sleep(15)
            dead = []
            with _TUNNEL_LOCK:
                for tid, t in list(_TUNNELS.items()):
                    if t["proc"].poll() is not None:
                        dead.append(tid)
                for tid in dead:
                    _TUNNELS.pop(tid, None)

    t = threading.Thread(target=_loop, daemon=True, name="norp-ssh-reaper")
    t.start()


# ----------------------------------------------------------------------
# 路径 / 配置工具
# ----------------------------------------------------------------------
def _expand(path):
    if not path:
        return ""
    return os.path.normpath(os.path.expandvars(os.path.expanduser(path)))


def _now_ms():
    return int(time.time() * 1000)


def _config_dir(context):
    d = os.path.join(context.app_dir, "norp_ssh")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _hosts_path(context):
    return os.path.join(_config_dir(context), "hosts.json")


def _sockets_dir(context):
    d = os.path.join(_config_dir(context), "sockets")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return ""
    return d


def _safe_alias(alias):
    return re.sub(r"[^A-Za-z0-9._-]", "_", alias or "") or "host"


def _control_path(context, host):
    d = _sockets_dir(context)
    if not d:
        return ""
    path = os.path.join(d, _safe_alias(host.get("alias", "")) + ".sock")
    return path.replace("\\", "/")


# ----------------------------------------------------------------------
# 存储层（版本化 JSON + 原子写入 + 损坏隔离）
# ----------------------------------------------------------------------
def _normalize_hosts(hosts):
    """把旧版/手改的条目规范化：tags 统一为 list，jump 统一为逗号字符串。"""
    out = []
    for h in hosts:
        if not isinstance(h, dict):
            continue
        tags = h.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        if not isinstance(tags, list):
            tags = []
        h["tags"] = tags
        if isinstance(h.get("jump"), list):
            h["jump"] = ",".join(str(x) for x in h["jump"])
        out.append(h)
    return out


def _load_hosts(context):
    p = _hosts_path(context)
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            hosts = data.get("hosts", [])
        elif isinstance(data, list):
            hosts = data  # 兼容旧格式（裸列表）
        else:
            hosts = []
        if not isinstance(hosts, list):
            hosts = []
        return _normalize_hosts(hosts)
    except Exception:
        # 损坏文件：改名隔离，避免被下一次保存静默覆盖
        try:
            os.replace(p, "%s.corrupt-%d" % (p, int(time.time())))
        except Exception:
            pass
        return []


def _save_hosts(context, hosts):
    p = _hosts_path(context)
    try:
        d = os.path.dirname(p)
        os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        payload = {"version": 1, "hosts": hosts}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        if os.name != "nt":
            try:
                os.chmod(tmp, 0o600)  # 密钥/口令文件仅属主可读
            except Exception:
                pass
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def _find_host(context, alias):
    for h in _load_hosts(context):
        if h.get("alias") == alias:
            return h
    return None


# ----------------------------------------------------------------------
# 校验
# ----------------------------------------------------------------------
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_alias(alias):
    if not _ALIAS_RE.match(alias):
        return "❌ 别名只能包含字母、数字、点、连字符、下划线，且以字母或数字开头"
    return None


def _validate_port(port):
    try:
        port = int(port)
    except Exception:
        return "❌ port 必须是 1..65535 之间的整数"
    if not (1 <= port <= 65535):
        return "❌ port 必须在 1..65535 之间"
    return None


# ----------------------------------------------------------------------
# 子进程执行
# ----------------------------------------------------------------------
def _hidden_flags():
    """Windows 下隐藏子进程控制台窗口。"""
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return startupinfo, creationflags


def _run(cmd, timeout=60):
    """执行命令，返回 (returncode, stdout, stderr)。"""
    startupinfo, creationflags = _hidden_flags()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            startupinfo=startupinfo, creationflags=creationflags)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "命令超时（%ds）" % timeout
    except FileNotFoundError:
        return -1, "", "可执行文件不存在：%s" % (cmd[0] if cmd else "?")
    except Exception as e:
        return -1, "", "执行异常：%s" % e


def _truncate(s, n=8000):
    if not s:
        return s
    if len(s) > n:
        return s[:n] + "\n…（输出过长，已截断到 %d 字符）" % n
    return s


# ----------------------------------------------------------------------
# 认证 / 引擎 / 命令构建
# ----------------------------------------------------------------------
def _pick_engine(host):
    """密码认证走 PuTTY；带口令的密钥（有 plink 时）也走 PuTTY；其余走 OpenSSH。"""
    if host.get("auth") == "password":
        return "plink"
    if host.get("passphrase") and _find_exe("plink"):
        return "plink"
    return "ssh"


def _plink_pw(host):
    """plink -pw 的值：密码认证取 password，带口令密钥取 passphrase。"""
    if host.get("auth") == "password":
        return host.get("password") or ""
    return host.get("passphrase") or ""


def _resolve_jump(context, jump):
    """jump 可以是已配置主机的别名，或 user@host[:port] 原始串。"""
    h = _find_host(context, jump)
    if h:
        spec = ""
        if h.get("user"):
            spec += h["user"] + "@"
        spec += h.get("host", "")
        port = h.get("port")
        if port and str(port) not in ("22", ""):
            spec += ":" + str(port)
        return spec
    return jump


def _jump_hops(host):
    """把 jump 字段拆成跳板链（逗号分隔，多级）。"""
    jump = host.get("jump")
    if not jump:
        return []
    return [x.strip() for x in str(jump).split(",") if x.strip()]


def _common_opts(context, host, timeout):
    """OpenSSH 基础选项（不含连接池选项，连接池由 _run_with_pool 追加并支持回退）。"""
    opts = [
        "-o", "ConnectTimeout=%d" % timeout,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ServerAliveInterval=15",
    ]
    ident = host.get("identity_file")
    if ident:
        opts += ["-i", _expand(ident)]
    for hop in _jump_hops(host):
        opts += ["-J", _resolve_jump(context, hop)]
    if host.get("auth") != "password":
        # 密钥 / 默认：强制非交互，避免卡在密码提示
        opts += [
            "-o", "BatchMode=yes",
            "-o", "PasswordAuthentication=no",
            "-o", "PubkeyAuthentication=yes",
        ]
    return opts


def _pool_opts(context, host):
    """ControlMaster 连接池选项（best-effort）。"""
    if not USE_CONTROL_MASTER or host.get("auth") == "password":
        return []
    sock = _control_path(context, host)
    if not sock:
        return []
    return [
        "-o", "ControlMaster=auto",
        "-o", "ControlPath=%s" % sock,
        "-o", "ControlPersist=%d" % CONTROL_PERSIST,
    ]


def _run_with_pool(context, host, base_cmd, timeout):
    """执行；若启用连接池且失败疑似 mux 配置错误，回退到无 ControlMaster 重试。"""
    pool = _pool_opts(context, host)
    if not pool:
        return _run(base_cmd, timeout)
    full = base_cmd[:1] + pool + base_cmd[1:]
    rc, out, err = _run(full, timeout)
    if rc != 0 and any(h in (err or "").lower() for h in _CONTROL_ERR_HINTS):
        rc, out, err = _run(base_cmd, timeout)
    return rc, out, err


def _target(host):
    user = host.get("user", "")
    h = host.get("host", "")
    if not h:
        return ""
    return ("%s@%s" % (user, h)) if user else h


def _port(host):
    try:
        return int(host.get("port", 22) or 22)
    except Exception:
        return 22


def _key_ready(host):
    """密钥认证时，检查 identity_file 是否存在（未指定则视为走默认密钥/agent）。"""
    ident = host.get("identity_file")
    if not ident:
        return True
    return os.path.exists(_expand(ident))


def _ssh_exec(context, host, command, timeout):
    """返回 (rc, out, err)。"""
    if _pick_engine(host) == "plink":
        return _plink_exec(host, command, timeout)
    target = _target(host)
    if not target:
        return -1, "", "主机配置缺少 host 字段"
    cmd = [_resolve_ssh_bin()] + _common_opts(context, host, timeout) \
        + ["-p", str(_port(host)), target, command]
    return _run_with_pool(context, host, cmd, timeout)


def _plink_exec(host, command, timeout):
    plink = _find_exe("plink")
    if not plink:
        return -1, "", "密码/口令认证需要 PuTTY 的 plink.exe（未找到）。请安装 PuTTY 或改用密钥认证。"
    target = _target(host)
    if not target:
        return -1, "", "主机配置缺少 host 字段"
    cmd = [plink, "-batch", "-ssh"]
    pw = _plink_pw(host)
    if pw:
        cmd += ["-pw", pw]
    ident = host.get("identity_file")
    if ident:
        cmd += ["-i", _expand(ident)]
    cmd += ["-P", str(_port(host)), target, command]
    return _run(cmd, timeout)


def _scp(context, host, src, dst, direction, timeout):
    """direction: 'upload' | 'download'。返回 (rc, out, err)。"""
    if _pick_engine(host) == "plink":
        pscp = _find_exe("pscp")
        if not pscp:
            return -1, "", "密码/口令认证需要 PuTTY 的 pscp.exe（未找到）。请安装 PuTTY 或改用密钥认证。"
        target = _target(host)
        if not target:
            return -1, "", "主机配置缺少 host 字段"
        cmd = [pscp, "-batch"]
        pw = _plink_pw(host)
        if pw:
            cmd += ["-pw", pw]
        ident = host.get("identity_file")
        if ident:
            cmd += ["-i", _expand(ident)]
        cmd += ["-P", str(_port(host))]
        if direction == "upload":
            cmd += [src, "%s:%s" % (target, dst)]
        else:
            cmd += ["%s:%s" % (target, dst), src]
        return _run(cmd, timeout)

    target = _target(host)
    if not target:
        return -1, "", "主机配置缺少 host 字段"
    if direction == "upload":
        cmd = [_resolve_scp_bin()] + _common_opts(context, host, timeout) \
            + ["-P", str(_port(host)), src, "%s:%s" % (target, dst)]
    else:
        cmd = [_resolve_scp_bin()] + _common_opts(context, host, timeout) \
            + ["-P", str(_port(host)), "%s:%s" % (target, dst), src]
    return _run_with_pool(context, host, cmd, timeout)


# ----------------------------------------------------------------------
# 隧道
# ----------------------------------------------------------------------
def _start_tunnel(context, alias, local_port, remote_host, remote_port, timeout=10):
    host = _find_host(context, alias)
    if not host:
        return False, "❌ 未找到主机：%s" % alias, None
    target = _target(host)
    if not target:
        return False, "❌ 主机配置缺少 host 字段", None

    # 隧道自身就是一条长连接（ssh -N -L），不叠加 ControlMaster，
    # 避免主连接被停掉时连带断开其它隧道。
    if _pick_engine(host) == "plink":
        plink = _find_exe("plink")
        if not plink:
            return False, "❌ 密码/口令认证需要 plink.exe（未找到）", None
        cmd = [plink, "-batch", "-ssh", "-N",
               "-L", "%d:%s:%d" % (local_port, remote_host, remote_port)]
        pw = _plink_pw(host)
        if pw:
            cmd += ["-pw", pw]
        ident = host.get("identity_file")
        if ident:
            cmd += ["-i", _expand(ident)]
        cmd += ["-P", str(_port(host)), target]
    else:
        cmd = [_resolve_ssh_bin()] + _common_opts(context, host, timeout) \
            + ["-N", "-L", "%d:%s:%d" % (local_port, remote_host, remote_port),
               "-p", str(_port(host)), target]

    startupinfo, creationflags = _hidden_flags()
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            startupinfo=startupinfo, creationflags=creationflags)
    except Exception as e:
        return False, "❌ 启动隧道失败：%s" % e, None

    time.sleep(1.0)
    if proc.poll() is not None:
        err = ""
        try:
            err = proc.stderr.read() or ""
        except Exception:
            pass
        return False, "❌ 隧道启动后立即退出：%s" % _truncate(err.strip(), 300), None

    global _TUNNEL_SEQ
    with _TUNNEL_LOCK:
        _TUNNEL_SEQ += 1
        tid = "tun-%d" % _TUNNEL_SEQ
        _TUNNELS[tid] = {
            "id": tid, "proc": proc, "alias": alias,
            "local_port": local_port,
            "remote": "%s:%d" % (remote_host, remote_port),
            "created": time.time(),
        }
    _ensure_reaper()
    return True, "✅ 隧道已建立：127.0.0.1:%d → %s:%d（ID: %s）" % (
        local_port, remote_host, remote_port, tid), tid


def _stop_tunnel(tid):
    with _TUNNEL_LOCK:
        t = _TUNNELS.get(tid)
    if not t:
        return "❌ 隧道不存在：%s" % tid
    proc = t["proc"]
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    except Exception:
        pass
    with _TUNNEL_LOCK:
        _TUNNELS.pop(tid, None)
    return "✅ 隧道已关闭：%s" % tid


def _list_tunnels():
    with _TUNNEL_LOCK:
        items = list(_TUNNELS.values())
    if not items:
        return "🔌 当前没有活动的隧道。"
    lines = ["🔌 **活动隧道**（%d 条）\n" % len(items)]
    for t in items:
        alive = t["proc"].poll() is None
        lines.append("  %s  %s  | 127.0.0.1:%d → %s | %s" % (
            "🟢" if alive else "🔴", t["id"], t["local_port"], t["remote"],
            "运行中" if alive else "已退出"))
    return "\n".join(lines)


def _stop_all_tunnels():
    with _TUNNEL_LOCK:
        tids = list(_TUNNELS.keys())
    for tid in tids:
        _stop_tunnel(tid)
    return "✅ 已关闭全部隧道（%d 条）" % len(tids)


# ----------------------------------------------------------------------
# 连接池清理
# ----------------------------------------------------------------------
def _close_master(context, host):
    if not USE_CONTROL_MASTER or host.get("auth") == "password":
        return
    sock = _control_path(context, host)
    if not sock or not os.path.exists(sock):
        return
    target = _target(host)
    if not target:
        return
    cmd = [_resolve_ssh_bin(), "-S", sock, "-O", "exit",
           "-p", str(_port(host)), target]
    _run(cmd, timeout=10)


def _close_all_masters(context):
    for h in _load_hosts(context):
        _close_master(context, h)


# ----------------------------------------------------------------------
# 工具定义
# ----------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ssh_list",
            "description": "列出已配置的 SSH 主机（可按关键字过滤别名/描述/主机名/标签/位置）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "可选，模糊匹配别名/描述/主机名/标签/位置"
                    }
                },
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_add_host",
            "description": "新增或更新一台 SSH 主机配置。",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {"type": "string", "description": "主机别名（唯一标识，如 prod-web；仅限字母数字点连字符下划线）"},
                    "host": {"type": "string", "description": "主机地址（IP 或域名）"},
                    "user": {"type": "string", "description": "登录用户名，如 root"},
                    "port": {"type": "integer", "description": "SSH 端口，默认 22"},
                    "auth": {"type": "string", "enum": ["key", "password"], "description": "认证方式：key=密钥（默认）/ password=密码"},
                    "password": {"type": "string", "description": "密码（仅 auth=password 时使用，明文存储）"},
                    "identity_file": {"type": "string", "description": "私钥路径，如 ~/.ssh/id_rsa"},
                    "passphrase": {"type": "string", "description": "密钥口令（加密私钥用；走 plink 非交互提供，或先用 ssh-add 加入 agent）"},
                    "jump": {"type": "string", "description": "跳板机：已配置主机的别名或 user@host[:port]，多级用逗号分隔"},
                    "environment": {"type": "string", "description": "环境标签，如 production/staging"},
                    "tags": {"type": "string", "description": "逗号分隔的标签，如 web,docker"},
                    "location": {"type": "string", "description": "物理位置备注，如 阿里云-华东1"},
                    "description": {"type": "string", "description": "备注说明"}
                },
                "required": ["alias", "host"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_remove_host",
            "description": "删除一台已配置的 SSH 主机。",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {"type": "string", "description": "要删除的主机别名"}
                },
                "required": ["alias"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_import_config",
            "description": "从 ~/.ssh/config 导入主机（Host/HostName/User/Port/IdentityFile/ProxyJump 等；通配符 Host 自动跳过）。",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_test",
            "description": "测试到一台已配置主机的 SSH 连通性并测量延迟。",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {"type": "string", "description": "主机别名（见 ssh_list）"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 12"}
                },
                "required": ["alias"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_exec",
            "description": "在一台远程主机上执行 shell 命令并返回输出。适合查状态、看日志、部署操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {"type": "string", "description": "主机别名（见 ssh_list）"},
                    "command": {"type": "string", "description": "要执行的 shell 命令"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 60"}
                },
                "required": ["alias", "command"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_upload",
            "description": "上传一个本地文件到远程主机（单文件；目录需先打包）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {"type": "string", "description": "主机别名"},
                    "local_path": {"type": "string", "description": "本地文件路径（绝对路径）"},
                    "remote_path": {"type": "string", "description": "远程目标路径"}
                },
                "required": ["alias", "local_path", "remote_path"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_download",
            "description": "从远程主机下载一个文件到本地（单文件）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {"type": "string", "description": "主机别名"},
                    "remote_path": {"type": "string", "description": "远程文件路径"},
                    "local_path": {"type": "string", "description": "本地目标路径（绝对路径）"}
                },
                "required": ["alias", "remote_path", "local_path"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_tunnel",
            "description": "管理本地端口转发隧道（访问远程数据库/内网服务）。action: start/list/stop/stop-all。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["start", "list", "stop", "stop-all"], "description": "操作类型"},
                    "alias": {"type": "string", "description": "主机别名（start 必填）"},
                    "remote_host": {"type": "string", "description": "要转发的远程主机，默认 127.0.0.1"},
                    "remote_port": {"type": "integer", "description": "远程端口（start 必填）"},
                    "local_port": {"type": "integer", "description": "本地监听端口，缺省自动分配"},
                    "tunnel_id": {"type": "string", "description": "隧道 ID（stop 必填）"}
                },
                "required": ["action"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_cluster",
            "description": "在多台主机上并发执行同一命令（可按别名/环境/标签过滤，可设并发上限）。适合批量运维。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要在每台主机执行的命令"},
                    "aliases": {"type": "string", "description": "逗号分隔的主机别名列表；缺省为全部主机"},
                    "environment": {"type": "string", "description": "只对指定环境标签的主机执行"},
                    "tags": {"type": "string", "description": "逗号分隔的标签，只对包含全部这些标签的主机执行"},
                    "timeout": {"type": "integer", "description": "每台主机超时秒数，默认 60"},
                    "maxWorkers": {"type": "integer", "description": "并发上限，默认 8（1..32）"}
                },
                "required": ["command"],
                "additionalProperties": False
            }
        }
    }
]


# ----------------------------------------------------------------------
# 工具调度
# ----------------------------------------------------------------------
def execute(tool_name, args, context):
    try:
        if tool_name == "ssh_list":
            return _cmd_list(context, args.get("query"))
        if tool_name == "ssh_add_host":
            return _cmd_add_host(context, args)
        if tool_name == "ssh_remove_host":
            return _cmd_remove_host(context, args.get("alias"))
        if tool_name == "ssh_import_config":
            return _cmd_import_config(context)
        if tool_name == "ssh_test":
            return _cmd_test(context, args)
        if tool_name == "ssh_exec":
            return _cmd_exec(context, args)
        if tool_name == "ssh_upload":
            return _cmd_upload(context, args)
        if tool_name == "ssh_download":
            return _cmd_download(context, args)
        if tool_name == "ssh_tunnel":
            return _cmd_tunnel(context, args)
        if tool_name == "ssh_cluster":
            return _cmd_cluster(context, args)
        return "Unknown tool: %s" % tool_name
    except Exception as e:
        return "❌ 工具执行异常：%s" % e


# ----------------------------------------------------------------------
# 各工具实现
# ----------------------------------------------------------------------
def _cmd_list(context, query):
    hosts = _load_hosts(context)
    if query:
        q = query.lower()
        hosts = [h for h in hosts if
                 q in (h.get("alias", "") or "").lower()
                 or q in (h.get("host", "") or "").lower()
                 or q in (h.get("description", "") or "").lower()
                 or q in (h.get("location", "") or "").lower()
                 or q in " ".join(h.get("tags", []) if isinstance(h.get("tags"), list) else str(h.get("tags", "")).split(",")).lower()]
    if not hosts:
        return "📭 没有配置的主机。用 ssh_add_host 添加，或用 ssh_import_config 从 ~/.ssh/config 导入。"
    lines = ["🖥️ **已配置主机**（%d 台）\n" % len(hosts)]
    for h in hosts:
        auth = "🔑密钥" if h.get("auth") != "password" else "🔒密码"
        key_state = ""
        if h.get("auth") != "password" and h.get("identity_file"):
            key_state = " 密钥✅" if _key_ready(h) else " 密钥⚠️缺失"
        jump = " | 🪜跳板:%s" % h["jump"] if h.get("jump") else ""
        env = " | 🌐%s" % h["environment"] if h.get("environment") else ""
        loc = " | 📍%s" % h["location"] if h.get("location") else ""
        tags = "".join(" #%s" % t for t in (h.get("tags", []) if isinstance(h.get("tags"), list) else [x.strip() for x in str(h.get("tags", "")).split(",") if x.strip()]))
        lines.append("  • **%s** — %s@%s:%s (%s%s)%s%s%s%s" % (
            h.get("alias"), h.get("user") or "?", h.get("host"),
            h.get("port", 22), auth, key_state, jump, env, loc, tags))
        if h.get("description"):
            lines.append("      └ %s" % h["description"])
    return "\n".join(lines)


def _cmd_add_host(context, args):
    alias = (args.get("alias") or "").strip()
    host = (args.get("host") or "").strip()
    if not alias or not host:
        return "❌ alias 和 host 均为必填"
    err = _validate_alias(alias)
    if err:
        return err
    port = args.get("port") or 22
    err = _validate_port(port)
    if err:
        return err
    port = int(port)

    hosts = _load_hosts(context)
    now = _now_ms()
    created_at = now
    for h in hosts:
        if h.get("alias") == alias:
            created_at = h.get("created_at", now)
            break
    tags = [t.strip() for t in (args.get("tags") or "").split(",") if t.strip()]
    entry = {
        "alias": alias,
        "host": host,
        "user": (args.get("user") or "").strip(),
        "port": port,
        "auth": args.get("auth") or "key",
        "password": args.get("password") or "",
        "identity_file": (args.get("identity_file") or "").strip(),
        "passphrase": args.get("passphrase") or "",
        "jump": (args.get("jump") or "").strip(),
        "environment": (args.get("environment") or "").strip(),
        "tags": tags,
        "location": (args.get("location") or "").strip(),
        "description": (args.get("description") or "").strip(),
        "created_at": created_at,
        "updated_at": now,
    }
    replaced = False
    for i, h in enumerate(hosts):
        if h.get("alias") == alias:
            hosts[i] = entry
            replaced = True
            break
    if not replaced:
        hosts.append(entry)
    if _save_hosts(context, hosts):
        return "✅ 主机 **%s** 已%s（%s@%s:%s）" % (
            alias, "更新" if replaced else "添加", entry["user"] or "?",
            entry["host"], entry["port"])
    return "❌ 保存主机配置失败"


def _cmd_remove_host(context, alias):
    if not alias:
        return "❌ 请指定 alias"
    hosts = _load_hosts(context)
    new_hosts = [h for h in hosts if h.get("alias") != alias]
    if len(new_hosts) == len(hosts):
        return "❌ 未找到主机：%s" % alias
    _save_hosts(context, new_hosts)
    return "✅ 已删除主机：%s" % alias


def _cmd_import_config(context):
    p = os.path.expanduser("~/.ssh/config")
    if not os.path.exists(p):
        return "❌ 未找到 ~/.ssh/config"
    hosts = _load_hosts(context)
    existing = {h.get("alias") for h in hosts}

    blocks = []
    cur = None
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                key = parts[0].lower()
                val = parts[1].strip() if len(parts) > 1 else ""
                if key == "host":
                    cur = {"pattern": val, "props": {}}
                    blocks.append(cur)
                elif cur is not None:
                    cur["props"][key] = val
    except Exception as e:
        return "❌ 解析 ~/.ssh/config 失败：%s" % e

    added, skipped = [], []
    for blk in blocks:
        pattern = blk["pattern"].split()[0] if blk["pattern"].strip() else ""
        if not pattern or "*" in pattern or "?" in pattern:
            if pattern and pattern not in skipped:
                skipped.append(pattern)
            continue
        props = blk["props"]
        hostname = (props.get("hostname") or "").strip()
        if not hostname:
            if pattern not in skipped:
                skipped.append(pattern)
            continue
        if pattern in existing:
            if pattern not in skipped:
                skipped.append(pattern)
            continue

        port = 22
        if "port" in props:
            try:
                port = int(props["port"])
            except Exception:
                port = 22
        if not (1 <= port <= 65535):
            port = 22

        entry = {
            "alias": pattern,
            "host": hostname,
            "user": props.get("user", "").strip(),
            "port": port,
            "auth": "key",
            "password": props.get("password", ""),
            "identity_file": props.get("identityfile", "").strip(),
            "passphrase": "",
            "jump": (props.get("proxyjump") or "").strip(),
            "environment": (props.get("environment") or "").strip(),
            "tags": [t.strip() for t in (props.get("tags") or "").split(",") if t.strip()],
            "location": (props.get("location") or "").strip(),
            "description": (props.get("description") or "").strip(),
            "created_at": _now_ms(),
            "updated_at": _now_ms(),
        }
        hosts.append(entry)
        existing.add(pattern)
        added.append(pattern)

    _save_hosts(context, hosts)
    if not added:
        return "⚠️ 没有新的主机可导入（解析 %d 个 Host 块；跳过 %d：%s）" % (
            len(blocks), len(skipped), ", ".join(skipped[:10]) or "无")
    tail = ""
    if skipped:
        tail = "；跳过 %d：%s" % (len(skipped), ", ".join(skipped[:8]))
    return "✅ 从 ~/.ssh/config 导入了 %d 台主机：%s%s" % (
        len(added), ", ".join(added), tail)


def _cmd_test(context, args):
    alias = (args.get("alias") or "").strip()
    timeout = int(args.get("timeout") or 12)
    host = _find_host(context, alias)
    if not host:
        return "❌ 未找到主机：%s（用 ssh_list 查看已配置主机）" % alias
    target = _target(host)
    if not target:
        return "❌ 主机配置缺少 host 字段"

    if _pick_engine(host) == "plink":
        plink = _find_exe("plink")
        if not plink:
            return "❌ 密码/口令认证需要 plink.exe（未找到）"
        cmd = [plink, "-batch", "-ssh"]
        pw = _plink_pw(host)
        if pw:
            cmd += ["-pw", pw]
        ident = host.get("identity_file")
        if ident:
            cmd += ["-i", _expand(ident)]
        cmd += ["-P", str(_port(host)), target, "exit 0"]
    else:
        cmd = [_resolve_ssh_bin()] + _common_opts(context, host, timeout) \
            + ["-p", str(_port(host)), target, "exit 0"]

    t0 = time.time()
    rc, out, err = _run(cmd, timeout=max(timeout, 5))
    ms = int((time.time() - t0) * 1000)
    if rc == 0:
        return "✅ 连接 **%s** 成功（延迟约 %d ms）" % (alias, ms)
    return "❌ 连接 **%s** 失败（exit %s，%d ms）：%s" % (
        alias, rc, ms, _truncate((out or err).strip(), 300))


def _cmd_exec(context, args):
    alias = (args.get("alias") or "").strip()
    command = args.get("command") or ""
    timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)
    host = _find_host(context, alias)
    if not host:
        return "❌ 未找到主机：%s（用 ssh_list 查看已配置主机）" % alias
    rc, out, err = _ssh_exec(context, host, command, timeout)
    if rc == 0:
        body = _truncate(out) if out else "(无输出)"
        return "✅ 在 **%s** 执行成功\n```\n%s\n```" % (alias, body)
    detail = _truncate((out or "") + ("\n" + err if err else "")) or "(无错误信息)"
    return "❌ 在 **%s** 执行失败（exit %s）\n```\n%s\n```" % (alias, rc, detail)


def _cmd_upload(context, args):
    alias = (args.get("alias") or "").strip()
    local = _expand(args.get("local_path") or "")
    remote = args.get("remote_path") or ""
    host = _find_host(context, alias)
    if not host:
        return "❌ 未找到主机：%s" % alias
    if not os.path.exists(local):
        return "❌ 本地文件不存在：%s" % local
    rc, out, err = _scp(context, host, local, remote, "upload", 120)
    if rc == 0:
        return "✅ 已上传 %s → %s:%s" % (local, alias, remote)
    return "❌ 上传失败：%s" % _truncate((out or "") + ("\n" + err if err else ""), 500)


def _cmd_download(context, args):
    alias = (args.get("alias") or "").strip()
    remote = args.get("remote_path") or ""
    local = _expand(args.get("local_path") or "")
    host = _find_host(context, alias)
    if not host:
        return "❌ 未找到主机：%s" % alias
    rc, out, err = _scp(context, host, remote, local, "download", 120)
    if rc == 0:
        return "✅ 已下载 %s:%s → %s" % (alias, remote, local)
    return "❌ 下载失败：%s" % _truncate((out or "") + ("\n" + err if err else ""), 500)


def _cmd_tunnel(context, args):
    action = args.get("action") or "list"
    if action == "list":
        return _list_tunnels()
    if action == "stop":
        return _stop_tunnel(args.get("tunnel_id"))
    if action == "stop-all":
        return _stop_all_tunnels()
    if action == "start":
        alias = (args.get("alias") or "").strip()
        remote_host = args.get("remote_host") or "127.0.0.1"
        remote_port = int(args.get("remote_port") or 0)
        if not alias or not remote_port:
            return "❌ start 需要 alias 和 remote_port"
        local_port = int(args.get("local_port") or 0)
        if not local_port:
            local_port = _pick_free_port()
        ok, msg, _ = _start_tunnel(context, alias, local_port, remote_host, remote_port)
        return msg
    return "❌ 未知 action：%s" % action


def _pick_free_port():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    except Exception:
        return 0
    finally:
        s.close()


def _cmd_cluster(context, args):
    command = args.get("command") or ""
    aliases = [a.strip() for a in (args.get("aliases") or "").split(",") if a.strip()]
    environment = (args.get("environment") or "").strip()
    tags = [t.strip() for t in (args.get("tags") or "").split(",") if t.strip()]
    timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)
    max_workers = int(args.get("maxWorkers") or 8)
    max_workers = max(1, min(max_workers, 32))

    hosts = _load_hosts(context)
    matched = []
    if aliases:
        for a in aliases:
            h = _find_host(context, a)
            matched.append(h if h else {"alias": a, "host": a, "user": "",
                                        "port": 22, "auth": "key"})
    else:
        matched = hosts

    if environment:
        matched = [h for h in matched if h.get("environment") == environment]
    if tags:
        matched = [h for h in matched if
                   all(t in (h.get("tags", []) if isinstance(h.get("tags"), list) else []) for t in tags)]
    if not matched:
        return "❌ 没有匹配的主机"

    results = {}

    def _one(h):
        alias = h.get("alias", h.get("host"))
        rc, out, err = _ssh_exec(context, h, command, timeout)
        text = (out + ("\n[stderr] " + err if err else "")).strip()
        return alias, {"rc": rc, "output": _truncate(text, 600)}

    with ThreadPoolExecutor(max_workers=min(len(matched), max_workers)) as ex:
        futs = [ex.submit(_one, h) for h in matched]
        for f in as_completed(futs):
            try:
                alias, res = f.result()
                results[alias] = res
            except Exception:
                pass

    lines = ["🖥️ **集群执行结果**（%d 台主机）\n" % len(matched)]
    for h in matched:
        alias = h.get("alias", h.get("host"))
        r = results.get(alias)
        if not r:
            lines.append("  ⚪ %s：无结果" % alias)
            continue
        icon = "✅" if r["rc"] == 0 else "❌"
        one_line = r["output"][:200].replace("\n", " ")
        lines.append("  %s %s (exit %s)：%s" % (icon, alias, r["rc"], one_line))
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 钩子
# ----------------------------------------------------------------------
def on_agent_init(context):
    context.storage.setdefault("norp_ssh_ready", True)
    n = len(_load_hosts(context))
    context.logger.info("NORP SSH 就绪，已配置 %d 台主机" % n)


def on_agent_shutdown(context):
    _stop_all_tunnels()
    _close_all_masters(context)
    context.logger.info("NORP SSH 已关闭全部隧道与连接池")

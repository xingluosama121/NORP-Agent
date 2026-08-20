# -*- coding: utf-8 -*-
"""
ssh_service.py — SSH 运维引擎（GUI 面板与 Agent 插件共用同一套逻辑/同一份主机配置）

对齐 DeepSeek Harness dsh-ssh 的宿主进程内 SSH 引擎。零第三方依赖，
全部基于系统自带 OpenSSH（ssh.exe / scp.exe）；密码/口令认证走 PuTTY plink/pscp。

主机配置存 `<app_dir>/norp_ssh/hosts.json`，与 norp_ssh 插件共享同一文件，
因此「GUI 面板加的主机」与「Agent 工具加的主机」互相可见。

所有对外方法只返回 JSON 可序列化对象（dict / list / str / int / bool / None）。
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_TIMEOUT = 60
USE_CONTROL_MASTER = True
CONTROL_PERSIST = 1800
_CONTROL_ERR_HINTS = ("controlpath", "controlmaster", "mux", "bad configuration",
                      "unknown option")

_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _find_exe(name):
    try:
        return shutil.which(name)
    except Exception:
        return None


def _expand(path):
    if not path:
        return ""
    return os.path.normpath(os.path.expandvars(os.path.expanduser(path)))


def _now_ms():
    return int(time.time() * 1000)


def _hidden_flags():
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return startupinfo, creationflags


def _truncate(s, n=8000):
    if not s:
        return s
    if len(s) > n:
        return s[:n] + "\n…（输出过长，已截断）"
    return s


class SshService:
    def __init__(self, app_dir: str):
        self.app_dir = app_dir
        self._tunnels = {}
        self._tunnel_lock = threading.Lock()
        self._tunnel_seq = 0
        self._terminals = {}
        self._term_lock = threading.Lock()
        self._term_seq = 0

    # ------------------------------------------------------------------
    # 主机存储（与 norp_ssh 插件共享 hosts.json）
    # ------------------------------------------------------------------
    def _hosts_path(self):
        return os.path.join(self.app_dir, "norp_ssh", "hosts.json")

    def _normalize_hosts(self, hosts):
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

    def _load_hosts(self):
        p = self._hosts_path()
        if not os.path.exists(p):
            return []
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                hosts = data.get("hosts", [])
            elif isinstance(data, list):
                hosts = data
            else:
                hosts = []
            if not isinstance(hosts, list):
                hosts = []
            return self._normalize_hosts(hosts)
        except Exception:
            try:
                os.replace(p, "%s.corrupt-%d" % (p, int(time.time())))
            except Exception:
                pass
            return []

    def _save_hosts(self, hosts):
        p = self._hosts_path()
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "hosts": hosts}, f, ensure_ascii=False, indent=2)
                f.write("\n")
            if os.name != "nt":
                try:
                    os.chmod(tmp, 0o600)
                except Exception:
                    pass
            os.replace(tmp, p)
            return True
        except Exception:
            return False

    def _find_host(self, alias):
        for h in self._load_hosts():
            if h.get("alias") == alias:
                return h
        return None

    # ------------------------------------------------------------------
    # 主机管理（对外）
    # ------------------------------------------------------------------
    def list_hosts(self, query=None):
        hosts = self._load_hosts()
        if query:
            q = str(query).lower()
            hosts = [h for h in hosts if
                     q in (h.get("alias", "") or "").lower()
                     or q in (h.get("host", "") or "").lower()
                     or q in (h.get("description", "") or "").lower()
                     or q in (h.get("location", "") or "").lower()
                     or q in " ".join(h.get("tags", []) if isinstance(h.get("tags"), list) else []).lower()]
        result = []
        for h in hosts:
            result.append({
                "alias": h.get("alias", ""),
                "host": h.get("host", ""),
                "user": h.get("user", ""),
                "port": h.get("port", 22),
                "auth": h.get("auth", "key"),
                "key_ready": self._key_ready(h),
                "jump": h.get("jump", ""),
                "environment": h.get("environment", ""),
                "tags": h.get("tags", []) if isinstance(h.get("tags"), list) else [],
                "location": h.get("location", ""),
                "description": h.get("description", ""),
                "created_at": h.get("created_at", 0),
                "updated_at": h.get("updated_at", 0),
            })
        return result

    def add_host(self, entry):
        entry = entry or {}
        alias = (entry.get("alias") or "").strip()
        host = (entry.get("host") or "").strip()
        if not alias or not host:
            return {"ok": False, "message": "alias 和 host 均为必填"}
        if not _ALIAS_RE.match(alias):
            return {"ok": False, "message": "别名只能包含字母、数字、点、连字符、下划线"}
        try:
            port = int(entry.get("port") or 22)
        except Exception:
            port = 22
        if not (1 <= port <= 65535):
            return {"ok": False, "message": "port 必须在 1..65535 之间"}

        hosts = self._load_hosts()
        now = _now_ms()
        created_at = now
        for h in hosts:
            if h.get("alias") == alias:
                created_at = h.get("created_at", now)
                break
        tags = entry.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        record = {
            "alias": alias, "host": host,
            "user": (entry.get("user") or "").strip(),
            "port": port,
            "auth": entry.get("auth") or "key",
            "password": entry.get("password") or "",
            "identity_file": (entry.get("identity_file") or "").strip(),
            "passphrase": entry.get("passphrase") or "",
            "jump": (entry.get("jump") or "").strip(),
            "environment": (entry.get("environment") or "").strip(),
            "tags": tags,
            "location": (entry.get("location") or "").strip(),
            "description": (entry.get("description") or "").strip(),
            "created_at": created_at,
            "updated_at": now,
        }
        replaced = False
        for i, h in enumerate(hosts):
            if h.get("alias") == alias:
                hosts[i] = record
                replaced = True
                break
        if not replaced:
            hosts.append(record)
        if not self._save_hosts(hosts):
            return {"ok": False, "message": "保存主机配置失败"}
        return {"ok": True, "message": ("已更新" if replaced else "已添加") + "主机 %s" % alias}

    def remove_host(self, alias):
        if not alias:
            return {"ok": False, "message": "请指定 alias"}
        hosts = self._load_hosts()
        new_hosts = [h for h in hosts if h.get("alias") != alias]
        if len(new_hosts) == len(hosts):
            return {"ok": False, "message": "未找到主机：%s" % alias}
        self._save_hosts(new_hosts)
        return {"ok": True, "message": "已删除主机：%s" % alias}

    def import_config(self):
        p = os.path.expanduser("~/.ssh/config")
        if not os.path.exists(p):
            return {"ok": False, "message": "未找到 ~/.ssh/config"}
        hosts = self._load_hosts()
        existing = {h.get("alias") for h in hosts}
        blocks, cur = [], None
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
            return {"ok": False, "message": "解析失败：%s" % e}
        added, skipped = [], []
        for blk in blocks:
            pattern = blk["pattern"].split()[0] if blk["pattern"].strip() else ""
            if not pattern or "*" in pattern or "?" in pattern:
                if pattern and pattern not in skipped:
                    skipped.append(pattern)
                continue
            props = blk["props"]
            hostname = (props.get("hostname") or "").strip()
            if not hostname or pattern in existing:
                if pattern and pattern not in skipped:
                    skipped.append(pattern)
                continue
            port = 22
            if "port" in props:
                try:
                    port = int(props["port"])
                except Exception:
                    port = 22
            hosts.append({
                "alias": pattern, "host": hostname,
                "user": props.get("user", "").strip(), "port": port,
                "auth": "key", "password": props.get("password", ""),
                "identity_file": props.get("identityfile", "").strip(),
                "passphrase": "", "jump": (props.get("proxyjump") or "").strip(),
                "environment": (props.get("environment") or "").strip(),
                "tags": [t.strip() for t in (props.get("tags") or "").split(",") if t.strip()],
                "location": (props.get("location") or "").strip(),
                "description": (props.get("description") or "").strip(),
                "created_at": _now_ms(), "updated_at": _now_ms(),
            })
            existing.add(pattern)
            added.append(pattern)
        self._save_hosts(hosts)
        msg = "导入了 %d 台：%s" % (len(added), ", ".join(added)) if added else "没有新的主机可导入"
        if skipped:
            msg += "；跳过 %d：%s" % (len(skipped), ", ".join(skipped[:8]))
        return {"ok": bool(added), "message": msg}

    # ------------------------------------------------------------------
    # 认证 / 引擎 / 命令构建
    # ------------------------------------------------------------------
    def _pick_engine(self, host):
        if host.get("auth") == "password":
            return "plink"
        if host.get("passphrase") and _find_exe("plink"):
            return "plink"
        return "ssh"

    def _plink_pw(self, host):
        if host.get("auth") == "password":
            return host.get("password") or ""
        return host.get("passphrase") or ""

    def _resolve_jump(self, jump):
        h = self._find_host(jump)
        if h:
            spec = (h.get("user") or "") + ("@" if h.get("user") else "") + (h.get("host") or "")
            port = h.get("port")
            if port and str(port) not in ("22", ""):
                spec += ":" + str(port)
            return spec
        return jump

    def _jump_hops(self, host):
        jump = host.get("jump")
        if not jump:
            return []
        return [x.strip() for x in str(jump).split(",") if x.strip()]

    def _common_opts(self, host, timeout):
        opts = ["-o", "ConnectTimeout=%d" % timeout,
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ServerAliveInterval=15"]
        ident = host.get("identity_file")
        if ident:
            opts += ["-i", _expand(ident)]
        for hop in self._jump_hops(host):
            opts += ["-J", self._resolve_jump(hop)]
        if host.get("auth") != "password":
            opts += ["-o", "BatchMode=yes", "-o", "PasswordAuthentication=no",
                     "-o", "PubkeyAuthentication=yes"]
        return opts

    def _sockets_dir(self):
        d = os.path.join(self.app_dir, "norp_ssh", "sockets")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            return ""
        return d

    def _control_path(self, host):
        d = self._sockets_dir()
        if not d:
            return ""
        alias = re.sub(r"[^A-Za-z0-9._-]", "_", host.get("alias", "") or "") or "host"
        return os.path.join(d, alias + ".sock").replace("\\", "/")

    def _pool_opts(self, host):
        if not USE_CONTROL_MASTER or host.get("auth") == "password":
            return []
        sock = self._control_path(host)
        if not sock:
            return []
        return ["-o", "ControlMaster=auto", "-o", "ControlPath=%s" % sock,
                "-o", "ControlPersist=%d" % CONTROL_PERSIST]

    def _run(self, cmd, timeout=60):
        startupinfo, creationflags = _hidden_flags()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                  startupinfo=startupinfo, creationflags=creationflags)
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired:
            return -1, "", "命令超时（%ds）" % timeout
        except FileNotFoundError:
            return -1, "", "可执行文件不存在：%s" % (cmd[0] if cmd else "?")
        except Exception as e:
            return -1, "", "执行异常：%s" % e

    def _run_with_pool(self, host, base_cmd, timeout):
        pool = self._pool_opts(host)
        if not pool:
            return self._run(base_cmd, timeout)
        full = base_cmd[:1] + pool + base_cmd[1:]
        rc, out, err = self._run(full, timeout)
        if rc != 0 and any(h in (err or "").lower() for h in _CONTROL_ERR_HINTS):
            rc, out, err = self._run(base_cmd, timeout)
        return rc, out, err

    def _target(self, host):
        h = host.get("host", "")
        if not h:
            return ""
        return ("%s@%s" % (host.get("user", ""), h)) if host.get("user") else h

    def _port(self, host):
        try:
            return int(host.get("port", 22) or 22)
        except Exception:
            return 22

    def _key_ready(self, host):
        ident = host.get("identity_file")
        if not ident:
            return True
        return os.path.exists(_expand(ident))

    # ------------------------------------------------------------------
    # 连通性测试 / 执行 / 传输
    # ------------------------------------------------------------------
    def test(self, alias, timeout=12):
        host = self._find_host(alias)
        if not host:
            return {"ok": False, "message": "未找到主机：%s" % alias}
        target = self._target(host)
        if not target:
            return {"ok": False, "message": "主机缺少 host 字段"}
        if self._pick_engine(host) == "plink":
            plink = _find_exe("plink")
            if not plink:
                return {"ok": False, "message": "密码/口令认证需要 plink.exe（未安装）"}
            cmd = [plink, "-batch", "-ssh"]
            pw = self._plink_pw(host)
            if pw:
                cmd += ["-pw", pw]
            if host.get("identity_file"):
                cmd += ["-i", _expand(host["identity_file"])]
            cmd += ["-P", str(self._port(host)), target, "exit 0"]
        else:
            cmd = [self._ssh_bin()] + self._common_opts(host, timeout) \
                + ["-p", str(self._port(host)), target, "exit 0"]
        t0 = time.time()
        rc, out, err = self._run(cmd, max(timeout, 5))
        ms = int((time.time() - t0) * 1000)
        if rc == 0:
            return {"ok": True, "message": "连接成功（%d ms）" % ms, "latency_ms": ms}
        return {"ok": False, "message": _truncate((out or err).strip(), 300), "latency_ms": ms}

    def exec_cmd(self, alias, command, timeout=DEFAULT_TIMEOUT):
        host = self._find_host(alias)
        if not host:
            return {"rc": -1, "stdout": "", "stderr": "未找到主机：%s" % alias}
        rc, out, err = self._exec(host, command or "", int(timeout or DEFAULT_TIMEOUT))
        return {"rc": rc, "stdout": out, "stderr": err}

    def upload(self, alias, local_path, remote_path):
        host = self._find_host(alias)
        if not host:
            return {"ok": False, "message": "未找到主机：%s" % alias}
        local = _expand(local_path or "")
        if not os.path.exists(local):
            return {"ok": False, "message": "本地文件不存在：%s" % local}
        rc, out, err = self._scp(host, local, remote_path or "", "upload", 120)
        if rc == 0:
            return {"ok": True, "message": "已上传 → %s:%s" % (alias, remote_path)}
        return {"ok": False, "message": _truncate((out or "") + ("\n" + err if err else ""), 500)}

    def download(self, alias, remote_path, local_path):
        host = self._find_host(alias)
        if not host:
            return {"ok": False, "message": "未找到主机：%s" % alias}
        rc, out, err = self._scp(host, remote_path or "", _expand(local_path or ""), "download", 120)
        if rc == 0:
            return {"ok": True, "message": "已下载 → %s" % local_path}
        return {"ok": False, "message": _truncate((out or "") + ("\n" + err if err else ""), 500)}

    def _ssh_bin(self):
        return _find_exe("ssh") or "ssh"

    def _scp_bin(self):
        return _find_exe("scp") or "scp"

    def _exec(self, host, command, timeout):
        if self._pick_engine(host) == "plink":
            plink = _find_exe("plink")
            if not plink:
                return -1, "", "密码/口令认证需要 plink.exe（未安装）"
            target = self._target(host)
            cmd = [plink, "-batch", "-ssh"]
            pw = self._plink_pw(host)
            if pw:
                cmd += ["-pw", pw]
            if host.get("identity_file"):
                cmd += ["-i", _expand(host["identity_file"])]
            cmd += ["-P", str(self._port(host)), target, command]
            return self._run(cmd, timeout)
        target = self._target(host)
        if not target:
            return -1, "", "主机缺少 host 字段"
        cmd = [self._ssh_bin()] + self._common_opts(host, timeout) \
            + ["-p", str(self._port(host)), target, command]
        return self._run_with_pool(host, cmd, timeout)

    def _scp(self, host, src, dst, direction, timeout):
        if self._pick_engine(host) == "plink":
            pscp = _find_exe("pscp")
            if not pscp:
                return -1, "", "密码/口令认证需要 pscp.exe（未安装）"
            target = self._target(host)
            cmd = [pscp, "-batch"]
            pw = self._plink_pw(host)
            if pw:
                cmd += ["-pw", pw]
            if host.get("identity_file"):
                cmd += ["-i", _expand(host["identity_file"])]
            cmd += ["-P", str(self._port(host))]
            if direction == "upload":
                cmd += [src, "%s:%s" % (target, dst)]
            else:
                cmd += ["%s:%s" % (target, dst), src]
            return self._run(cmd, timeout)
        target = self._target(host)
        if not target:
            return -1, "", "主机缺少 host 字段"
        if direction == "upload":
            cmd = [self._scp_bin()] + self._common_opts(host, timeout) \
                + ["-P", str(self._port(host)), src, "%s:%s" % (target, dst)]
        else:
            cmd = [self._scp_bin()] + self._common_opts(host, timeout) \
                + ["-P", str(self._port(host)), "%s:%s" % (target, dst), src]
        return self._run_with_pool(host, cmd, timeout)

    # ------------------------------------------------------------------
    # 隧道
    # ------------------------------------------------------------------
    def tunnel_start(self, alias, remote_port, remote_host="127.0.0.1", local_port=0, timeout=10):
        host = self._find_host(alias)
        if not host:
            return {"ok": False, "message": "未找到主机：%s" % alias}
        target = self._target(host)
        if not target:
            return {"ok": False, "message": "主机缺少 host 字段"}
        if not local_port:
            local_port = self._pick_free_port()
        remote_host = remote_host or "127.0.0.1"
        if self._pick_engine(host) == "plink":
            plink = _find_exe("plink")
            if not plink:
                return {"ok": False, "message": "密码/口令认证需要 plink.exe（未安装）"}
            cmd = [plink, "-batch", "-ssh", "-N",
                   "-L", "%d:%s:%d" % (local_port, remote_host, remote_port)]
            pw = self._plink_pw(host)
            if pw:
                cmd += ["-pw", pw]
            if host.get("identity_file"):
                cmd += ["-i", _expand(host["identity_file"])]
            cmd += ["-P", str(self._port(host)), target]
        else:
            cmd = [self._ssh_bin()] + self._common_opts(host, timeout) \
                + ["-N", "-L", "%d:%s:%d" % (local_port, remote_host, remote_port),
                   "-p", str(self._port(host)), target]
        startupinfo, creationflags = _hidden_flags()
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    text=True, startupinfo=startupinfo, creationflags=creationflags)
        except Exception as e:
            return {"ok": False, "message": "启动隧道失败：%s" % e}
        time.sleep(1.0)
        if proc.poll() is not None:
            err = ""
            try:
                err = proc.stderr.read() or ""
            except Exception:
                pass
            return {"ok": False, "message": "隧道启动后立即退出：%s" % _truncate(err.strip(), 300)}
        with self._tunnel_lock:
            self._tunnel_seq += 1
            tid = "tun-%d" % self._tunnel_seq
            self._tunnels[tid] = {
                "id": tid, "proc": proc, "alias": alias, "local_port": local_port,
                "remote": "%s:%d" % (remote_host, remote_port), "created": time.time(),
            }
        return {"ok": True, "tunnel_id": tid,
                "message": "隧道已建立 127.0.0.1:%d → %s:%d（%s）" % (local_port, remote_host, remote_port, tid)}

    def tunnel_list(self):
        with self._tunnel_lock:
            items = list(self._tunnels.values())
        return [{"id": t["id"], "alias": t["alias"], "local_port": t["local_port"],
                 "remote": t["remote"], "alive": t["proc"].poll() is None} for t in items]

    def tunnel_stop(self, tunnel_id):
        with self._tunnel_lock:
            t = self._tunnels.get(tunnel_id)
        if not t:
            return {"ok": False, "message": "隧道不存在：%s" % tunnel_id}
        proc = t["proc"]
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        except Exception:
            pass
        with self._tunnel_lock:
            self._tunnels.pop(tunnel_id, None)
        return {"ok": True, "message": "已关闭隧道：%s" % tunnel_id}

    def tunnel_stop_all(self):
        with self._tunnel_lock:
            tids = list(self._tunnels.keys())
        for tid in tids:
            self.tunnel_stop(tid)
        return {"ok": True, "message": "已关闭全部隧道（%d 条）" % len(tids)}

    def _pick_free_port(self):
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
        except Exception:
            return 0
        finally:
            s.close()

    # ------------------------------------------------------------------
    # 集群
    # ------------------------------------------------------------------
    def cluster(self, command, aliases=None, environment=None, tags=None,
                timeout=DEFAULT_TIMEOUT, max_workers=8):
        hosts = self._load_hosts()
        matched = []
        if aliases:
            for a in aliases:
                h = self._find_host(a)
                matched.append(h if h else {"alias": a, "host": a, "user": "", "port": 22, "auth": "key"})
        else:
            matched = hosts
        if environment:
            matched = [h for h in matched if h.get("environment") == environment]
        if tags:
            matched = [h for h in matched if
                       all(t in (h.get("tags", []) if isinstance(h.get("tags"), list) else []) for t in tags)]
        if not matched:
            return []
        results = {}

        def _one(h):
            alias = h.get("alias", h.get("host"))
            rc, out, err = self._exec(h, command or "", int(timeout or DEFAULT_TIMEOUT))
            text = (out + ("\n[stderr] " + err if err else "")).strip()
            return alias, {"rc": rc, "output": _truncate(text, 600)}

        mw = max(1, min(int(max_workers or 8), 32))
        with ThreadPoolExecutor(max_workers=min(len(matched), mw)) as ex:
            futs = [ex.submit(_one, h) for h in matched]
            for f in as_completed(futs):
                try:
                    alias, res = f.result()
                    results[alias] = res
                except Exception:
                    pass
        out = []
        for h in matched:
            alias = h.get("alias", h.get("host"))
            r = results.get(alias)
            out.append({"alias": alias, "rc": r["rc"] if r else -1,
                        "output": r["output"] if r else "(无结果)"})
        return out

    # ------------------------------------------------------------------
    # 终端（简化版：ssh -tt 管道双向，非本地 PTY；全屏程序如 vim/htop 效果受限）
    # ------------------------------------------------------------------
    def terminal_open(self, alias):
        host = self._find_host(alias)
        if not host:
            return {"ok": False, "message": "未找到主机：%s" % alias}
        target = self._target(host)
        if not target:
            return {"ok": False, "message": "主机缺少 host 字段"}
        if self._pick_engine(host) == "plink":
            plink = _find_exe("plink")
            if not plink:
                return {"ok": False, "message": "密码/口令认证需要 plink.exe"}
            cmd = [plink, "-ssh", "-tt"]
            pw = self._plink_pw(host)
            if pw:
                cmd += ["-pw", pw]
            if host.get("identity_file"):
                cmd += ["-i", _expand(host["identity_file"])]
            cmd += ["-P", str(self._port(host)), target]
        else:
            cmd = [self._ssh_bin()] + self._common_opts(host, 15) \
                + ["-tt", "-p", str(self._port(host)), target]
        startupinfo, creationflags = _hidden_flags()
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, bufsize=0,
                                    startupinfo=startupinfo, creationflags=creationflags)
        except Exception as e:
            return {"ok": False, "message": "启动失败：%s" % e}
        with self._term_lock:
            self._term_seq += 1
            tid = "term-%d" % self._term_seq
            self._terminals[tid] = {"id": tid, "proc": proc, "alias": alias,
                                    "buf": bytearray(), "lock": threading.Lock()}
        t = self._terminals[tid]

        def _reader():
            p = proc
            try:
                while True:
                    chunk = p.stdout.read(4096)
                    if not chunk:
                        break
                    with t["lock"]:
                        t["buf"].extend(chunk)
            except Exception:
                pass

        threading.Thread(target=_reader, daemon=True).start()
        return {"ok": True, "terminal_id": tid, "message": "终端已连接"}

    def terminal_read(self, terminal_id):
        t = self._terminals.get(terminal_id)
        if not t:
            return {"ok": False, "data": "", "closed": True}
        with t["lock"]:
            data = bytes(t["buf"])
            t["buf"] = bytearray()
        closed = t["proc"].poll() is not None
        return {"ok": True, "data": data.decode("utf-8", errors="replace"), "closed": closed}

    def terminal_write(self, terminal_id, data):
        t = self._terminals.get(terminal_id)
        if not t:
            return {"ok": False, "message": "终端不存在"}
        try:
            t["proc"].stdin.write((data or "").encode("utf-8", errors="replace"))
            t["proc"].stdin.flush()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "message": str(e)}

    def terminal_close(self, terminal_id):
        t = self._terminals.pop(terminal_id, None)
        if not t:
            return {"ok": False, "message": "终端不存在"}
        try:
            t["proc"].terminate()
        except Exception:
            pass
        return {"ok": True}

    def terminal_list(self):
        return [{"id": t["id"], "alias": t["alias"], "closed": t["proc"].poll() is not None}
                for t in self._terminals.values()]

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------
    def shutdown(self):
        self.tunnel_stop_all()
        for tid in list(self._terminals.keys()):
            self.terminal_close(tid)
        for h in self._load_hosts():
            self._close_master(h)

    def _close_master(self, host):
        if not USE_CONTROL_MASTER or host.get("auth") == "password":
            return
        sock = self._control_path(host)
        if not sock or not os.path.exists(sock):
            return
        target = self._target(host)
        if not target:
            return
        self._run([self._ssh_bin(), "-S", sock, "-O", "exit",
                   "-p", str(self._port(host)), target], timeout=10)

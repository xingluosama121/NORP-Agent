# -*- coding: utf-8 -*-
"""
remote_server.py — 移动端远程控制（极简 HTTP 服务）

NORP 是 pywebview 桌面应用，pywebview 内置 HTTP 服务写死绑定 127.0.0.1 且无 host 参数，
因此这里用 Python 标准库自带一个极简 HTTP 服务，绑定地址可配置：

· 默认 host = "127.0.0.1"（仅本机可访问，手机连不上）——即「默认不启用」。
· 用户在设置里把 host 改成 "0.0.0.0"（或内网穿透的公网地址）后重启，手机即可通过
  局域网 / 公网扫码或打开链接，获得一个简单的移动端控制页。

注意：默认端口用 8090，避开 DeepSeek Harness 的 3080。

移动端页面：看回复 / 回复 Agent / 停止任务，并带「会话下拉」手动切换。
通过轮询 get_initial_messages（完整会话历史）同步，不消费事件队列，
因此与桌面端的 get_next_event 流式拉取互不干扰。
"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------------------------------------------------
# 极简移动端页面：会话选择 + 看回复 / 回复 Agent / 停止任务
# ----------------------------------------------------------------------
MOBILE_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>NORP 远程</title>
<style>
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body { margin:0; font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
         background:#0f1115; color:#e6e6e6; height:100vh; height:100dvh; display:flex; flex-direction:column; }
  header { padding:10px 14px; background:#161a22; border-bottom:1px solid #222;
           display:flex; align-items:center; gap:8px; flex:0 0 auto; }
  .dot { width:8px; height:8px; border-radius:50%; background:#2ecc71; display:inline-block; flex:0 0 auto; }
  #sess-select { flex:1; background:#1e232d; color:#e6e6e6; border:1px solid #333;
                 border-radius:8px; padding:8px 6px; font-size:13px; max-width:70%; }
  #stop { background:#b71c1c; }
  #log { flex:1; overflow-y:auto; padding:12px 14px; }
  .msg { margin:8px 0; max-width:88%; white-space:pre-wrap; word-break:break-word;
         padding:9px 12px; border-radius:12px; font-size:15px; line-height:1.55; }
  .msg.user { background:#007aff; color:#fff; margin-left:auto; border-bottom-right-radius:4px; }
  .msg.assistant { background:#1e232d; color:#e6e6e6; border-bottom-left-radius:4px; }
  .msg.sys { background:transparent; color:#888; font-size:12px; text-align:center; margin:6px auto; }
  #inputrow { display:flex; gap:8px; padding:10px 14px; padding-bottom:max(10px, env(safe-area-inset-bottom));
              background:#161a22; border-top:1px solid #222; flex:0 0 auto; }
  #input { flex:1; background:#1e232d; color:#e6e6e6; border:1px solid #333; border-radius:10px;
           padding:11px 12px; font-size:15px; }
  #input:focus { outline:none; border-color:#007aff; }
  button { background:#007aff; color:#fff; border:none; border-radius:10px; padding:11px 16px;
           font-size:15px; cursor:pointer; flex:0 0 auto; }
  button:active { opacity:.85; }
</style>
</head>
<body>
  <header>
    <span class="dot" id="dot"></span>
    <select id="sess-select"></select>
    <button id="stop">停止</button>
  </header>
  <div id="log"></div>
  <div id="inputrow">
    <input id="input" placeholder="安排任务…" autocomplete="off" onkeydown="if(event.key==='Enter')send()">
    <button id="send">发送</button>
  </div>
<script>
var sid = '';
var pollTimer = null;
var renderedCount = 0;

function api(method) {
  var args = Array.prototype.slice.call(arguments, 1);
  return fetch('/api/' + method, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({args: args})
  }).then(function(r){ return r.json(); });
}
function appendMsg(text, cls) {
  if (!text) return;
  var d = document.getElementById('log');
  var el = document.createElement('div');
  el.className = 'msg ' + (cls || 'sys');
  el.textContent = text;
  d.appendChild(el);
  d.scrollTop = d.scrollHeight;
}
function setDot(on) { document.getElementById('dot').style.background = on ? '#2ecc71' : '#888'; }
function renderMessages(msgs) {
  msgs = msgs || [];
  for (var i = renderedCount; i < msgs.length; i++) {
    var m = msgs[i];
    appendMsg(m && m.content, (m && m.role === 'user') ? 'user' : 'assistant');
  }
  renderedCount = msgs.length;
}
function switchSession(newSid) {
  if (!newSid) return;
  sid = newSid;
  renderedCount = 0;
  document.getElementById('log').innerHTML = '';
  var sel = document.getElementById('sess-select');
  if (sel) sel.value = newSid;
}
async function loadSession(newSid) {
  switchSession(newSid);
  try {
    var msgs = await api('get_initial_messages', sid);
    renderMessages(msgs);
    if (!(msgs && msgs.length)) appendMsg('还没有消息，输入任务开始吧', 'sys');
  } catch(e) { appendMsg('加载失败: ' + e); }
}
async function init() {
  try {
    var ss = await api('get_sessions');
    var sel = document.getElementById('sess-select');
    sel.innerHTML = '';
    (ss || []).forEach(function(s) {
      var o = document.createElement('option');
      o.value = s.id;
      var label = s.title || s.id;
      if (s.workspace) label += ' · ' + s.workspace;
      label += ' [' + (s.message_count || 0) + '条]';
      o.textContent = label;
      sel.appendChild(o);
    });
    // 默认优先选有工作区的会话，其次有消息的，其次第一个
    var pick = (ss || []).filter(function(s){ return s.workspace; })[0]
            || (ss || []).filter(function(s){ return s.message_count > 0; })[0]
            || (ss || [])[0];
    await loadSession(pick ? pick.id : '');
  } catch(e) { appendMsg('连接失败: ' + e); }
  pollTimer = setInterval(poll, 1000);
}
async function poll() {
  if (!sid) return;
  try {
    var msgs = await api('get_initial_messages', sid);
    renderMessages(msgs);
    var ss = await api('get_sessions');
    var s = (ss || []).filter(function(x){ return x.id === sid; })[0];
    setDot(!(s && s.has_task));
  } catch(e) {}
}
async function send() {
  var input = document.getElementById('input');
  var text = input.value.trim();
  if (!text || !sid) return;
  input.value = '';
  try { await api('send_message', sid, text); } catch(e) { appendMsg('发送失败: ' + e); }
  await poll();
}
async function stop() {
  if (!sid) return;
  try { await api('stop_task', sid); appendMsg('已请求停止', 'sys'); } catch(e) {}
}
document.getElementById('sess-select').addEventListener('change', function(){ loadSession(this.value); });
document.getElementById('send').addEventListener('click', send);
document.getElementById('stop').addEventListener('click', stop);
init();
</script>
</body>
</html>
"""


def lan_ips():
    """返回本机局域网 IPv4 列表。"""
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


class RemoteServer:
    """极简 HTTP 服务：`/` 提供移动端页面，`/api/<method>` 代理到 AgentAPI。"""

    def __init__(self, api, host="127.0.0.1", port=8090):
        self.api = api
        self.host = host
        self.port = int(port or 8090)
        self._httpd = None
        self._thread = None

    def start(self):
        api = self.api

        class Handler(BaseHTTPRequestHandler):
            def _write(self, data: bytes, ctype: str, code: int = 200):
                self.send_response(code)
                self.send_header("Content-Type", ctype + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.end_headers()
                self.wfile.write(data)

            def _json(self, obj, code=200):
                self._write(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                            "application/json", code)

            def do_GET(self):
                if self.path in ("/", "/index.html", "/mobile"):
                    self._write(MOBILE_PAGE.encode("utf-8"), "text/html")
                elif self.path == "/api/lan_ips":
                    self._json(lan_ips())
                else:
                    self._write("<h1>404 Not Found</h1>".encode("utf-8"), "text/html", 404)

            def do_POST(self):
                if not self.path.startswith("/api/"):
                    self._json({"error": "not found"}, 404)
                    return
                method = self.path[len("/api/"):]
                try:
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    args = body.get("args", []) if isinstance(body, dict) else []
                except Exception:
                    args = []
                try:
                    result = getattr(api, method)(*args)
                    self._json(result)
                except Exception as e:
                    self._json({"error": str(e)}, 500)

            def log_message(self, *a):
                pass  # 静默，不刷屏

        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError:
            return False
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            self._httpd = None

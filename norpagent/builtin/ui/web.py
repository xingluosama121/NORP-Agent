# Copyright (c) 2026 xingluosama121, MIT Licensed
"""Web UI 适配器：零依赖 HTTP + SSE 服务（标准库 http.server）。

Agent 的「界面」是可插拔的：本适配器实现 UIAdapter 协议，
与内核完全解耦：

- 订阅 EventBus，把全部 Agent 事件实时推送到浏览器（Server-Sent Events）；
- ``POST /chat`` 提交任务（后台线程执行，不阻塞 HTTP）；
- ``ask_user``：人工审批 / 澄清问题推送到页面，等待用户作答
  （超时回落 default，保证自动化场景不被卡死）；
- ``notify``：非阻塞通知；
- 页面：默认服务 front.html（多宿主前端，pywebview 桌面与浏览器
  双宿主共用，见 front_src/bridge.js）；资源缺失时回落到内置
  简易页面；构造参数 ``html`` 可挂载自定义主页面（文件路径或
  HTML 内容，strip 后以 "<" 开头视为内容），替换 / 路由默认页面，
  无需物理覆盖库文件；
- REST API（供 front.html 的浏览器桥使用）：
  /api/sessions 会话 CRUD、/api/config 配置、/api/models 模型、
  /api/plugins* 插件、/api/security 安全、/api/health 健康、
  /api/usage 用量、/api/upload 文件上传、/api/quit 退出等。

用法（宿主应用 / CLI 集成）::

    ui = WebUI(port=8787)
    ui.set_handler(lambda prompt, session_id, task_params: agent.run(...))
    ui.attach_runtime(agent)
    ui.start()          # 后台线程启动 HTTP 服务
    ...                 # 打开 http://127.0.0.1:8787/
    ui.shutdown()

用 AgentRuntime 挂载时：``AgentRuntime(reg, preset, ui=ui)``，
运行时自动把 ui.on_event 订阅到事件总线。
"""

from __future__ import annotations

import base64
import errno
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

_ASSET_DIR = os.path.join(os.path.dirname(__file__), "assets")
_FRONT_HTML_PATH = os.path.join(_ASSET_DIR, "front.html")
# 独立分类页面：模块流程编排（FLOW，类似 ComfyUI 的钩子级可视化流程）。
# 它不覆盖 WebUI 主页面，是独立入口。
_FLOW_HTML_PATH = os.path.join(_ASSET_DIR, "norp-flow.html")
_logger = logging.getLogger("norpagent.ui.web")

# 客户端断开类异常（Windows: WinError 10053/10054；POSIX: EPIPE/ECONNRESET）。
# 这类异常是浏览器刷新 / 关闭标签页 / curl 中断的常态，必须静默处理，
# 绝不允许 traceback 打满控制台。
_CLIENT_GONE_ERRORS = (
    BrokenPipeError,
    ConnectionAbortedError,
    ConnectionResetError,
    ConnectionError,
    TimeoutError,
)


class _RobustHTTPServer(ThreadingHTTPServer):
    """稳健版 ThreadingHTTPServer（pip 库友好）。

    - ``handle_error`` 覆盖：socketserver 默认对每个连接线程的
      未捕获异常调用 ``traceback.print_exc()``，客户端断连噪声
      （如 WinError 10053）会直接打进用户控制台。这里改为：
      断连静默，其余记 DEBUG 日志；
    - ``daemon_threads``：请求线程为守护线程，进程退出不挂起；
    - ``allow_reuse_address``：重启后立即复用端口（避开 TIME_WAIT）。
    """

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def handle_error(self, request: Any, client_address: Any) -> None:
        import sys as _sys

        exc = _sys.exc_info()[1]
        if isinstance(exc, _CLIENT_GONE_ERRORS):
            return
        _logger.debug(
            "http request error from %s: %s", client_address, exc, exc_info=True
        )


def default_project_root() -> str:
    """按操作系统给出默认项目根目录（Windows / macOS / Linux 各有约定）。

    - Windows：``%USERPROFILE%\\Documents\\NORP-Agent``
    - macOS：``~/Documents/NORP-Agent``
    - Linux/其它：``~/norpagent-workspace``
    """
    import sys as _sys

    home = os.path.expanduser("~") or ""
    if _sys.platform == "win32":
        base = os.environ.get("USERPROFILE") or home
        return os.path.join(base, "Documents", "NORP-Agent")
    if _sys.platform == "darwin":
        return os.path.join(home, "Documents", "NORP-Agent")
    return os.path.join(home, "norpagent-workspace")


def _default_config_path() -> str:
    """Web UI 配置持久化文件路径（环境变量可覆盖）。

    浏览器前端的全部设置（模型 / API Key / 语言 / 插件目录等）
    保存在这里：刷新页面、重启 np() 进程都不会丢。
    默认 ``~/.norpagent/webui_config.json``；测试可传入临时路径。
    """
    env = os.environ.get("NORPAGENT_WEBUI_CONFIG")
    if env:
        return str(env)
    return os.path.join(os.path.expanduser("~"), ".norpagent",
                        "webui_config.json")


def _default_flow_graph_path() -> str:
    """模块流程自动保存文件的路径（环境变量可覆盖）。

    /flow 页面每次改动画布都会自动保存到这里（含「应用到智能体」
    开关状态），刷新 / 重启进程后自动恢复；激活状态下 front 主界面
    的聊天任务会按该流程执行。默认 ``~/.norpagent/flow_graph.json``。
    """
    env = os.environ.get("NORPAGENT_FLOW_GRAPH")
    if env:
        return str(env)
    return os.path.join(os.path.expanduser("~"), ".norpagent",
                        "flow_graph.json")


# 前端「推理强度」选项 → reasoning_effort 参数。
# 注意：DeepSeek V4 仅接受 low / high / max，值在适配器层统一规范化
# （medium → high，见 openai_compat.normalize_effort）；「关」= none，
# 由适配器转译为 DeepSeek V4 的 thinking=disabled。
_THINK_LEVEL_MAP = {
    "关": "none",
    "低": "low",
    "中": "medium",
    "高": "high",
}

# 简易回落页面：assets/front.html 缺失时使用（保持零依赖可运行）
_HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>norpagent Web UI</title>
<style>
  body { font-family: "Segoe UI", system-ui, sans-serif; margin: 0; display: flex;
         height: 100vh; background: #111827; color: #e5e7eb; }
  #main { flex: 1; display: flex; flex-direction: column; max-width: 900px;
          margin: 0 auto; width: 100%; }
  #events { flex: 1; overflow-y: auto; padding: 16px; }
  .ev { margin: 4px 0; padding: 6px 10px; border-radius: 6px; font-size: 13px;
        white-space: pre-wrap; word-break: break-all; }
  .ev-user { background: #1f2937; }
  .ev-content { background: #065f46; }
  .ev-tool { background: #1e3a5f; }
  .ev-meta { background: #374151; color: #9ca3af; }
  .ev-error { background: #7f1d1d; }
  #input-bar { display: flex; padding: 12px; gap: 8px; background: #0b1220; }
  #prompt { flex: 1; padding: 10px; border-radius: 8px; border: 1px solid #374151;
            background: #111827; color: #e5e7eb; font-size: 14px; }
  button { padding: 10px 20px; border-radius: 8px; border: 0; background: #2563eb;
           color: white; cursor: pointer; font-size: 14px; }
  button:disabled { background: #374151; cursor: wait; }
  h3 { margin: 0 16px; color: #9ca3af; font-weight: normal; font-size: 13px; }
</style>
</head>
<body>
<div id="main">
  <h3>norpagent Web UI &middot; 事件流实时推送（SSE）</h3>
  <div id="events"></div>
  <div id="input-bar">
    <input id="prompt" placeholder="输入任务，回车发送" autofocus>
    <button id="send">发送</button>
  </div>
</div>
<script>
const events = document.getElementById('events');
const promptEl = document.getElementById('prompt');
const sendBtn = document.getElementById('send');
let sessionId = null;

function addLine(cls, text) {
  const div = document.createElement('div');
  div.className = 'ev ' + cls;
  div.textContent = text;
  events.appendChild(div);
  events.scrollTop = events.scrollHeight;
  while (events.childNodes.length > 400) events.removeChild(events.firstChild);
}

async function send() {
  const prompt = promptEl.value.trim();
  if (!prompt) return;
  addLine('ev-user', 'User: ' + prompt);
  promptEl.value = '';
  sendBtn.disabled = true;
  try {
    const resp = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, session_id: sessionId })
    });
    const data = await resp.json();
    if (data.session_id) sessionId = data.session_id;
    if (!data.ok) addLine('ev-error', '[failed] ' + (data.error || data.status));
  } catch (e) {
    addLine('ev-error', '[request failed] ' + e);
  } finally {
    sendBtn.disabled = false;
    promptEl.focus();
  }
}

sendBtn.onclick = send;
promptEl.addEventListener('keydown', e => { if (e.key === 'Enter') send(); });

const es = new EventSource('/events');
es.onmessage = (msg) => {
  try {
    const ev = JSON.parse(msg.data);
    if (ev.type === 'on_content') {
      addLine('ev-content', ev.content);
    } else if (ev.type === 'on_task_start') {
      addLine('ev-meta', '[task start] ' + ev.task_id + ' input: ' + ev.user_input);
    } else if (ev.type === 'before_tool_call') {
      addLine('ev-tool', '[tool] ' + ev.tool_name + ' ' + JSON.stringify(ev.args || {}));
    } else if (ev.type === 'after_tool_call') {
      addLine('ev-tool', '[tool result] ' + ev.tool_name + ' -> ' +
        (String(ev.result || '').slice(0, 300)));
    } else if (ev.type === 'on_task_done') {
      addLine('ev-meta', '[task done] steps=' + (ev.steps || '?'));
    } else if (ev.type === 'on_task_error' || ev.type === 'on_task_timeout') {
      addLine('ev-error', '[task error] ' + (ev.error || ev.timeout));
    } else if (ev.type === 'on_usage_update') {
      addLine('ev-meta', '[usage] in=' + ev.input + ' out=' + ev.output);
    } else if (ev.type === 'question') {
      addLine('ev-error', '[question] ' + ev.question);
      const answer = window.prompt(ev.question, '');
      fetch('/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question_id: ev.question_id, answer: answer || '' })
      });
    } else if (ev.type === 'notify') {
      addLine('ev-meta', '[notify] ' + ev.message);
    }
  } catch (e) { /* 忽略非 JSON 行 */ }
};
es.onerror = () => addLine('ev-error', '[event stream interrupted, reconnecting...]');
</script>
</body>
</html>
"""

# 默认配置：与 front.html 设置面板字段对齐
DEFAULT_CONFIG: Dict[str, Any] = {
    "language": "en",                     # 界面语言（np(language=...) 覆盖）
    "model": "",                          # 模型（缺省 = 引擎预设模型）
    "api_base": "https://api.deepseek.com",
    "api_key": "",
    "remote_models": [],                  # 最近一次拉取成功的远端模型列表（flow 模块坞展示）
    "project_root": default_project_root(),  # 默认工作区（按操作系统）
    "plugin_dirs": [],
    "norp_safe_enabled": True,
    "plugins_enabled": True,
    "close_button_behavior": "minimize_to_tray",
    "use_responses_api": False,
    "queue_max_size": 200,
    "max_steps": 128,
    "task_timeout": 0,
    "api_request_timeout": 180,
    "enable_web_search": False,
    "native_confirm_enabled": True,
    "native_confirm_write": True,
    "native_confirm_delete": True,
    "native_confirm_exec": True,
    "think_level": "高",
    "temperature": 1.0,
    "max_tokens": 32767,
    "memory": True,
    "memory_mode": "full",
    "max_rounds": 10,
    "custom_system_prompt_enabled": False,
    "custom_system_prompt": "",
    "custom_system_prompt_file": "",
    "jailbreak_guard_enabled": True,
    "jailbreak_guard_action": "block",
    "vision_enabled": False,
    "vision_service_url": "",
    "plugin_security_audit": "block",
    "plugin_security_import_restrict": "strict",
    "plugin_security_require_permissions": True,
    "plugin_security_resource_limit": False,
    "plugin_signature_verify": True,
    "plugin_trusted_keys": [],
    "plugin_isolation": "auto",
    "plugin_network_policy": "deny",
    "plugin_network_url_allowlist": [],
    "plugin_network_domain_allowlist": [],
    "approval_enabled": True,
    "flow_modules_dir": "",               # 模块流程「文件即模块」落盘目录（空 = 默认 ~/.norpagent/flow_modules）
    # front 智能体工具挂载（文件即模块 → 模型自动调用）：
    "agent_tools": [],                    # 显式工具全集（explicit=True 时生效；空 + 非显式 = 预设默认集）
    "agent_tools_explicit": False,        # True = agent_tools 为智能体的精确工具集（含空集）
    "_initialized": False,                # 是否完成过首次配置
}

_MAX_JSON = 1_000_000
_MAX_UPLOAD_JSON = 64_000_000
_MAX_UPLOAD_FILE = 10 * 1024 * 1024

# DeepSeek 已于 2026-07-24 停用 deepseek-chat / deepseek-reasoner：
# 旧版拉取缓存或第三方镜像若仍返回这两个名字，一律从远端模型列表过滤。
RETIRED_REMOTE_MODELS = {"deepseek-chat", "deepseek-reasoner"}


def filter_remote_models(models: Any) -> List[str]:
    """过滤已停用的远端模型名（deepseek-chat / deepseek-reasoner 等）。"""
    if not isinstance(models, (list, tuple, set)):
        return []
    return [str(m) for m in models
            if str(m).strip().lower() not in RETIRED_REMOTE_MODELS]


def json_safe(obj: Any, depth: int = 0) -> Any:
    """把任意对象递归转成 JSON 可序列化结构（不可序列化的转字符串）。

    修复：SSE 推送含 ChatMessage / RunContext 等对象时
    json.dumps 抛 TypeError 导致事件流整体断流的问题。
    """
    if depth > 8:
        return str(obj)
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): json_safe(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [json_safe(v, depth + 1) for v in obj]
    try:
        if hasattr(obj, "to_dict") and callable(obj.to_dict):
            return json_safe(obj.to_dict(), depth + 1)
    except Exception:  # noqa: BLE001
        pass
    return str(obj)


class WebUI:
    """Web UI 适配器（HTTP + SSE，零第三方依赖，页面 = front.html）。"""

    ui_id = "web"

    def __init__(
        self,
        port: int = 8787,
        host: str = "127.0.0.1",
        ask_timeout: float = 300.0,
        history_limit: int = 2000,
        language: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        config_path: Optional[str] = None,
        html: Optional[str] = None,
    ) -> None:
        self.port = int(port)
        self.host = host
        self.ask_timeout = float(ask_timeout)
        self.history_limit = int(history_limit)
        self._language = language or "en"
        # 自定义主页面（槽位挂载参数）：/ 路由的页面字节。
        # 解析规则：None = 库内置 front.html；
        #   strip 后以 "<" 开头 → 直接作为 HTML 内容；
        #   否则 → 视为文件路径（不存在抛 ValueError，快速失败）。
        self._html_override: Optional[bytes] = self._resolve_html(html)
        # 配置持久化路径：None 表示不落盘（纯内存，测试/嵌入式场景）
        self._config_path = (
            config_path if config_path is not None else _default_config_path()
        )
        self._config: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self._config["language"] = self._language
        self._load_config_from_disk()
        # 优先级：显式参数 > 磁盘持久化值 > 默认值
        if language is not None:
            self._config["language"] = language
        if config:
            self._config.update(config)
        self._handler_fn: Optional[Callable] = None
        self._agent: Any = None
        # 预设默认工具集快照（attach_runtime 时捕获；agent_tools 回退基准）
        self._agent_base_tools: List[str] = []
        self._config_apply: Optional[Callable[[Dict[str, Any]], None]] = None
        self._quit_callback: Optional[Callable[[], None]] = None
        self._engine_state_fn: Optional[Callable[[], str]] = None
        self._lock = threading.RLock()
        self._subscribers: List[queue.Queue] = []
        self._history: List[dict] = []
        self._questions: Dict[str, Any] = {}
        self._question_sessions: Dict[str, str] = {}
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._task_session: Dict[str, str] = {}
        self._running_sessions: Dict[str, str] = {}
        self._stop_requests: set = set()
        self._session_meta: Dict[str, Dict[str, Any]] = {}
        self._usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0,
                                       "tool_call_tokens": 0}
        self._flow_runs: Dict[str, Any] = {}
        self._flow_ws: Any = None
        # FE 前端模块（文件即前端）：拖入 .html/.js/.ts 注册后托管到
        # /fe/<safe_name>，「新标签页打开」即访问该 URL。
        # 每个 FE 拥有独立配置作用域（互不干扰），默认值取自全局配置。
        self._frontend_modules: Dict[str, Dict[str, Any]] = {}
        self._fe_scanned = False
        self._fe_config_dir = os.path.join(
            os.path.expanduser("~"), ".norpagent", "fe_configs")
        self._fe_configs: Dict[str, Dict[str, Any]] = {}
        self._load_fe_configs()
        # 模块流程自动保存：画布图 + 「应用到智能体」激活开关。
        # 激活时 front 主界面聊天任务改为按该流程执行（行为热切换）。
        self._flow_graph: Optional[Dict[str, Any]] = None
        self._flow_active: bool = False
        self._flow_graph_path = _default_flow_graph_path()
        # sid/task_id -> 聊天会话正在运行的 FlowRunner（支持 STOP）
        self._chat_flow_runs: Dict[str, Any] = {}
        self._load_flow_graph_from_disk()
        self._tlocal = threading.local()
        self._start_ts = time.time()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._closed = False

    @staticmethod
    def _resolve_html(html: Optional[str]) -> Optional[bytes]:
        """解析 html 挂载参数为页面字节。

        - None / 空 → 未挂载（None，回落库内置 front.html）；
        - strip 后以 "<" 开头 → HTML 内容（UTF-8 编码）；
        - 否则 → 文件路径：存在则读取内容；
          不存在抛 ValueError（快速失败，不静默回落默认页面，
          避免用户以为挂载生效、实际看到的还是内置页面）。
        """
        if html is None:
            return None
        src = str(html).strip()
        if not src:
            return None
        if src.startswith("<"):
            return src.encode("utf-8")
        if not os.path.isfile(src):
            raise ValueError(
                f"WebUI html 挂载参数既不是 HTML 内容（以 '<' 开头）"
                f"也不是存在的文件: {src!r}"
            )
        with open(src, "rb") as f:
            return f.read()

    # ── 宿主集成 ────────────────────────────────────────

    def set_handler(self, fn: Callable) -> None:
        """设置任务执行回调：fn(prompt, session_id, task_params) -> RunResult 类似对象。"""
        self._handler_fn = fn

    def attach_runtime(self, agent: Any) -> None:
        """绑定 Agent 运行时（会话 REST API / 插件列表 / 调试信息的数据源）。"""
        self._agent = agent
        if agent is not None:
            preset = getattr(agent, "preset", None)
            if preset is not None and not self._config.get("model"):
                self._config["model"] = getattr(preset, "model", "") or ""
            # 预设默认工具集快照（agent_tools 非显式时的回退基准）
            self._agent_base_tools = list(
                getattr(preset, "tools", ()) or ())
            # np(workspace_root=...) 显式指定时覆盖平台默认工作区
            params = getattr(agent, "params", None) or {}
            if params.get("workspace_root"):
                self._config["project_root"] = str(params["workspace_root"])

    def set_config_apply(self, cb: Callable[[Dict[str, Any]], None]) -> None:
        """配置保存后的应用回调（WebFrontend 据此重注册模型/插件/安全）。"""
        self._config_apply = cb

    def set_quit_callback(self, cb: Callable[[], None]) -> None:
        self._quit_callback = cb

    def set_engine_state_fn(self, fn: Callable[[], str]) -> None:
        self._engine_state_fn = fn

    # ── 服务生命周期 ────────────────────────────────────

    def start(self) -> "WebUI":
        """后台线程启动 HTTP 服务（非阻塞）。

        端口被占用时自动向后顺延尝试（最多 10 个端口），
        以实际绑定端口为准（``self.port`` 会被更新），
        彻底绑定失败时抛出带清晰信息的 RuntimeError（不刷 traceback）。
        """
        if self._server is not None:
            return self
        ui = self

        class _Handler(BaseHTTPRequestHandler):
            server_version = "norpagent-webui/0.4"
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):  # 静默访问日志
                pass

            # ── 连接健壮性 ────────────────────────────

            def handle(self):  # noqa: N802
                """覆盖 BaseHTTPRequestHandler.handle：客户端断开不刷 traceback。

                浏览器刷新 / 关闭标签页 / curl 中断都会让读写抛
                ConnectionAbortedError / ConnectionResetError /
                BrokenPipeError，此前一路冒泡到 socketserver，
                控制台被打满 traceback。这里统一吞掉断连噪声；
                真正的内部错误记 DEBUG 日志并尝试返回 500。
                """
                try:
                    super().handle()
                except _CLIENT_GONE_ERRORS:
                    pass
                except Exception as exc:  # noqa: BLE001 — 防御性兜底
                    self.close_connection = True
                    _logger.debug(
                        "request %s failed: %s", getattr(self, "path", "?"),
                        exc, exc_info=True,
                    )
                    try:
                        self._json(500, {"error": "internal server error"})
                    except Exception:  # noqa: BLE001
                        pass

            def finish(self):  # noqa: N802
                """wfile flush 在客户端断开时也会抛异常，同样静默。"""
                try:
                    super().finish()
                except (OSError, _CLIENT_GONE_ERRORS):  # noqa: BLE001
                    pass

            def _json(self, code: int, obj: dict) -> None:
                body = json.dumps(json_safe(obj), ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self, limit: int = _MAX_JSON) -> dict:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    length = 0
                if length > limit:
                    # 超大请求体：直接拒收并关闭连接，避免残留未读字节
                    # 让 keep-alive 连接协议错位（后续请求解析出垃圾）。
                    self.close_connection = True
                    return {}
                if length < 0:
                    length = 0  # 负数 Content-Length：按无请求体处理
                raw = self.rfile.read(length) if length else b""
                try:
                    data = json.loads(raw.decode("utf-8"))
                    return data if isinstance(data, dict) else {}
                except Exception:
                    return {}

            def _html(self, code: int, body: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                # 页面禁止浏览器缓存：每次刷新都取最新 front.html，
                # 否则修复后的前端（如思维链「思考」块翻译）会被旧缓存遮蔽
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                query = parse_qs(parsed.query)
                if path in ("/", "/index.html"):
                    self._html(200, ui.page_bytes())
                elif path in ("/flow", "/flow.html", "/norp-flow.html"):
                    # 独立分类「模块流程」：钩子级可视化编排（拖拽模块 / beam 连线）
                    self._html(200, ui.page_bytes("flow"))
                elif path == "/favicon.ico":
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif path == "/events":
                    self._handle_sse()
                elif path == "/api/status":
                    self._json(200, ui.stats())
                elif path == "/api/sessions":
                    self._json(200, {"sessions": ui.list_sessions()})
                elif path.startswith("/api/sessions/"):
                    self._handle_session_get(path)
                elif path == "/api/config":
                    self._json(200, ui.get_config())
                elif path == "/api/first_run":
                    self._json(200, {"first_run": ui.first_run()})
                elif path == "/api/models":
                    self._json(200, ui.list_models(query.get("base_url", [""])[0]))
                elif path == "/api/fe/config":
                    self._json(200, ui.fe_load_config(
                        str(query.get("fe_id", [""])[0])))
                elif path == "/api/plugins":
                    self._json(200, {"plugins": ui.list_plugins()})
                elif path == "/api/plugins/dirs":
                    self._json(200, {"dirs": ui.get_plugin_dirs()})
                elif path == "/api/security":
                    self._json(200, ui.get_security())
                elif path == "/api/health":
                    self._json(200, ui.health())
                elif path == "/api/usage":
                    self._json(200, ui.usage())
                elif path == "/api/balance":
                    self._json(200, {"balance": None, "error": None})
                elif path == "/api/debug":
                    self._json(200, ui.debug_info())
                elif path == "/api/flow/snapshot":
                    self._json(200, ui.flow_snapshot())
                elif path == "/api/flow/load":
                    self._json(200, ui.flow_load())
                elif path.startswith("/fe/"):
                    # FE 前端模块托管：「新标签页打开」访问的独立前端
                    fname = path[len("/fe/"):]
                    body, mime = ui.fe_read_file(fname)
                    if body is None:
                        self._json(404, {"error": "frontend module not found"})
                    else:
                        self.send_response(200)
                        self.send_header("Content-Type", mime + "; charset=utf-8")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                elif path == "/api/memory":
                    self._json(200, {"content": None})
                elif path == "/api/fs/list":
                    q = parse_qs(parsed.query)
                    self._json(200, ui.list_fs(
                        q.get("path", [""])[0],
                        include_files=q.get("files", ["0"])[0] == "1",
                    ))
                elif path == "/api/fs/read":
                    q = parse_qs(parsed.query)
                    self._json(200, ui.read_fs_file(q.get("path", [""])[0]))
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                if path == "/chat":
                    data = self._read_json()
                    prompt = str(data.get("prompt") or "").strip()
                    if not prompt:
                        self._json(400, {"ok": False, "error": "prompt 为空"})
                        return
                    task_id = ui.submit(
                        prompt, str(data.get("session_id") or "") or None
                    )
                    self._json(200, {
                        "ok": True, "task_id": task_id,
                        "session_id": ui._tasks.get(task_id, {}).get("session_id"),
                    })
                elif path == "/answer":
                    data = self._read_json()
                    ui.answer(
                        str(data.get("question_id") or ""),
                        str(data.get("answer") or ""),
                        str(data.get("session_id") or "") or None,
                    )
                    self._json(200, {"ok": True})
                elif path == "/stop":
                    data = self._read_json()
                    ui.stop_task(str(data.get("session_id") or "") or None)
                    self._json(200, {"ok": True})
                elif path == "/api/sessions":
                    data = self._read_json()
                    try:
                        sess = ui.create_session(
                            title=str(data.get("title") or ""),
                            workspace=str(data.get("workspace") or ""),
                        )
                        self._json(200, sess)
                    except Exception as exc:  # noqa: BLE001
                        self._json(500, {"error": str(exc)})
                elif path.startswith("/api/sessions/"):
                    self._handle_session_post(path, data=self._read_json())
                elif path == "/api/config":
                    data = self._read_json()
                    self._json(200, {"ok": True, "config": ui.save_config(
                        data.get("config") or {})})
                elif path == "/api/models":
                    # 「拉取模型列表」：直接用表单里当前的 Key/Base（即时应用，
                    # 无需先保存）；成功时更新远端模型缓存（flow 模块坞展示）
                    data = self._read_json()
                    self._json(200, ui.list_models(
                        str(data.get("base_url") or ""),
                        str(data.get("api_key") or "") or None,
                    ))
                elif path == "/api/fe/config":
                    data = self._read_json()
                    self._json(200, ui.fe_save_config(
                        str(data.get("fe_id") or ""),
                        data.get("config") or {},
                    ))
                elif path == "/api/config/reset":
                    self._json(200, {"ok": True, "config": ui.reset_config()})
                elif path == "/api/key":
                    data = self._read_json()
                    self._json(200, ui.set_api_key(str(data.get("api_key") or "")))
                elif path == "/api/key/validate":
                    data = self._read_json()
                    self._json(200, ui.validate_api_key(
                        str(data.get("api_key") or ""),
                        str(data.get("base_url") or ""),
                    ))
                elif path == "/api/upload":
                    data = self._read_json(limit=_MAX_UPLOAD_JSON)
                    files = data.get("files")
                    if not isinstance(files, list):
                        self._json(400, {"error": "files 必须为列表"})
                        return
                    self._json(200, {"files": ui.upload_files(files)})
                elif path == "/api/plugins/dirs":
                    data = self._read_json()
                    self._json(200, {"ok": True, "dirs": ui.add_plugin_dir(
                        str(data.get("path") or ""))})
                elif path == "/api/plugins/reload":
                    self._json(200, {"ok": True, "plugins": ui.reload_plugins()})
                elif path == "/api/security":
                    data = self._read_json()
                    self._json(200, ui.set_security(data))
                elif path == "/api/memory/clear":
                    data = self._read_json()
                    self._json(200, {"ok": True})
                elif path == "/api/log":
                    data = self._read_json()
                    _logger.info("frontend: %s", str(data.get("message") or "")[:2000])
                    self._json(200, {"ok": True})
                elif path == "/api/quit":
                    self._json(200, {"ok": True})
                    ui.request_quit()
                elif path == "/api/flow/run":
                    data = self._read_json(limit=_MAX_JSON)
                    self._json(200, ui.flow_run(data))
                elif path == "/api/flow/save":
                    data = self._read_json(limit=_MAX_JSON)
                    self._json(200, ui.flow_save(data))
                elif path == "/api/flow/stop":
                    data = self._read_json()
                    self._json(200, ui.flow_stop(
                        str(data.get("flow_id") or "")))
                elif path == "/api/flow/register":
                    data = self._read_json(limit=_MAX_UPLOAD_JSON)
                    self._json(200, ui.flow_register(
                        str(data.get("name") or ""),
                        str(data.get("content") or "")))
                elif path == "/api/agent/tools":
                    data = self._read_json()
                    self._json(200, ui.set_agent_tools(data))
                else:
                    self._json(404, {"error": "not found"})

            def do_DELETE(self):  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                if path.startswith("/api/sessions/"):
                    sid = path[len("/api/sessions/"):].strip("/")
                    self._json(200, ui.close_session(sid))
                elif path == "/api/plugins/dirs":
                    data = self._read_json()
                    self._json(200, {"ok": True, "dirs": ui.remove_plugin_dir(
                        str(data.get("path") or ""))})
                else:
                    self._json(404, {"error": "not found"})

            # ── 子路由 ──────────────────────────────────

            def _handle_session_get(self, path: str) -> None:
                rest = path[len("/api/sessions/"):].strip("/")
                parts = rest.split("/")
                sid = parts[0] if parts else ""
                if not sid:
                    self._json(404, {"error": "session id 缺失"})
                    return
                if len(parts) == 1:
                    self._json(200, {"session": ui.session_info(sid)})
                elif parts[1] == "messages":
                    self._json(200, {"messages": ui.session_messages(sid)})
                else:
                    self._json(404, {"error": "not found"})

            def _handle_session_post(self, path: str, data: dict) -> None:
                rest = path[len("/api/sessions/"):].strip("/")
                parts = rest.split("/")
                sid = parts[0] if parts else ""
                if len(parts) >= 2:
                    if parts[1] == "title":
                        ui.set_session_title(sid, str(data.get("title") or ""))
                        self._json(200, {"ok": True})
                        return
                    if parts[1] == "workspace":
                        ui.set_session_workspace(sid, str(data.get("workspace") or ""))
                        self._json(200, {"ok": True})
                        return
                self._json(404, {"error": "not found"})

            def _handle_sse(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q: "queue.Queue[dict]" = queue.Queue()
                ui._register_subscriber(q)
                try:
                    # 先补发近期历史
                    for item in ui._recent_history():
                        self.wfile.write(
                            f"data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n".encode("utf-8")
                        )
                    self.wfile.flush()
                    while True:
                        try:
                            item = q.get(timeout=15.0)
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                            continue
                        self.wfile.write(
                            f"data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n".encode("utf-8")
                        )
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    ui._unregister_subscriber(q)

        self._server = self._bind(_Handler)
        # port=0 或端口顺延时以实际绑定结果为准（打印 listening on 用）
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._serve, daemon=True, name="norpagent-webui"
        )
        self._thread.start()
        return self

    def _bind(self, handler_cls: type) -> "_RobustHTTPServer":
        """绑定监听端口：占用时顺延重试，失败抛出清晰错误。"""
        in_use_errnos = (
            getattr(errno, "EADDRINUSE", -1),       # POSIX / Windows 10048
            getattr(errno, "WSAEADDRINUSE", -1),    # Windows 10048
            getattr(errno, "EACCES", -1),           # Linux 特权/保留端口
            getattr(errno, "WSAEACCES", -1),        # Windows 10013：端口被监听占用
        )
        last_exc: Optional[OSError] = None
        for offset in range(10):
            candidate = self.port + offset
            try:
                return _RobustHTTPServer((self.host, candidate), handler_cls)
            except OSError as exc:
                last_exc = exc
                if exc.errno not in in_use_errnos or offset >= 9:
                    break
                _logger.warning(
                    "port %s 已被占用，尝试 %s", candidate, candidate + 1
                )
        raise RuntimeError(
            f"无法启动 Web UI（{self.host}:{self.port} 绑定失败）: {last_exc}"
        ) from last_exc

    def _serve(self) -> None:
        """serve_forever 包装：守护线程自身兜底，异常退出不刷控制台。"""
        server = self._server
        if server is None:
            return
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:  # pragma: no cover — 防御
            pass
        except Exception:  # noqa: BLE001
            _logger.exception("web server 异常退出")
        finally:
            with self._lock:
                if self._server is server:
                    self._server = None

    def page_bytes(self, page: str = "front") -> bytes:
        """返回页面字节：front=主聊天界面（front.html），
        flow=模块流程编排（norp-flow.html）。资源缺失时回落内置简易页面。

        front 页面优先返回 html 挂载参数指定的自定义内容
        （文件路径或 HTML 内容，构造时已解析缓存），
        未挂载时才读库内置 front.html。
        """
        if page == "front" and self._html_override is not None:
            return self._html_override
        paths = {
            "front": _FRONT_HTML_PATH,
            "flow": _FLOW_HTML_PATH,
        }
        try:
            with open(paths.get(page, _FRONT_HTML_PATH), "rb") as f:
                return f.read()
        except OSError:
            return _HTML_PAGE.encode("utf-8")

    def request_quit(self) -> None:
        """请求宿主应用退出（非阻塞）。"""
        cb = self._quit_callback
        if cb is not None:
            threading.Thread(target=cb, daemon=True, name="norpagent-webui-quit").start()

    # ── 任务执行 ────────────────────────────────────────

    def _task_defaults(self) -> Dict[str, Any]:
        """把配置面板的采样参数翻译为任务级模型参数。

        - 推理强度（think_level）→ reasoning_effort（关=不传，交给温度）；
        - 温度（推理开启时由适配器省略）；
        - max_tokens。
        通过 task_params 注入后，AgentRuntime 会原样传给模型适配器。
        """
        with self._lock:
            think = str(self._config.get("think_level") or "高")
            temperature = self._config.get("temperature")
            max_tokens = self._config.get("max_tokens")
        effort = _THINK_LEVEL_MAP.get(think, "high")
        defaults: Dict[str, Any] = {}
        if effort != "none":
            defaults["reasoning_effort"] = effort
        else:
            try:
                defaults["temperature"] = float(temperature) if temperature is not None else 1.0
            except (TypeError, ValueError):
                defaults["temperature"] = 1.0
        if max_tokens:
            try:
                defaults["max_tokens"] = int(max_tokens)
            except (TypeError, ValueError):
                pass
        return defaults

    def submit(self, prompt: str, session_id: Optional[str],
               task_params: Optional[Dict[str, Any]] = None) -> str:
        """提交一个任务，后台线程执行（不阻塞 HTTP）。"""
        sid = session_id or ""
        task_id = uuid.uuid4().hex[:12]
        record = {
            "task_id": task_id,
            "status": "running",
            "prompt": prompt,
            "session_id": sid,
            "result": None,
            "error": "",
        }
        with self._lock:
            self._tasks[task_id] = record
            self._task_session[task_id] = sid
            if sid:
                self._running_sessions[sid] = task_id
            self._prune_tasks()
        self._publish({
            "type": "notify",
            "level": "info",
            "message": f"Task {task_id} submitted",
            "ts": time.time(),
            "sid": sid or None,
        })

        def worker() -> None:
            self._tlocal.session_id = sid
            try:
                active = self._active_chat_flow()
                if active is not None:
                    # 应用到智能体已激活：聊天任务按保存的流程执行
                    result = self._run_flow_task(active, prompt, sid, task_id)
                else:
                    if self._handler_fn is None:
                        raise RuntimeError("WebUI 未设置执行回调（ui.set_handler(...)）")
                    tp = dict(task_params or {})
                    # 配置面板的采样参数注入任务（调用方可显式覆盖）
                    for key, value in self._task_defaults().items():
                        tp.setdefault(key, value)
                    tp.setdefault("_stop_check", lambda: sid in self._stop_requests)
                    meta = self._session_meta.get(sid)
                    if meta and meta.get("workspace") and "workspace_root" not in tp:
                        tp["workspace_root"] = meta["workspace"]
                    result = self._invoke_handler(self._handler_fn, prompt, sid, tp)
                status = getattr(result, "status", "done")
                content = getattr(result, "final_content", "") or ""
                error = getattr(result, "error", "") or ""
                record["status"] = status
                record["result"] = content
                record["error"] = error
                record["session_id"] = getattr(result, "session_id", "") or sid
                self._publish({
                    "type": "notify",
                    "level": "info" if status == "done" else "error",
                    "message": f"Task {task_id} finished ({status})",
                    "ts": time.time(),
                    "sid": getattr(result, "session_id", "") or sid or None,
                })
            except Exception as exc:  # noqa: BLE001
                record["status"] = "error"
                record["error"] = str(exc)
                self._publish({
                    "type": "notify",
                    "level": "error",
                    "message": f"Task {task_id} failed: {exc}",
                    "ts": time.time(),
                    "sid": sid or None,
                })
            finally:
                self._tlocal.session_id = None
                with self._lock:
                    self._task_session.pop(task_id, None)
                    if sid:
                        self._running_sessions.pop(sid, None)
                    self._stop_requests.discard(sid)

        threading.Thread(
            target=worker, daemon=True, name=f"norpagent-webui-task-{task_id}"
        ).start()
        return task_id

    def stop_task(self, session_id: Optional[str]) -> None:
        """请求停止某会话正在运行的任务（在步骤边界生效）。"""
        sid = session_id or ""
        runner = None
        with self._lock:
            if sid and sid in self._running_sessions:
                self._stop_requests.add(sid)
            runner = self._chat_flow_runs.get(sid)
        # 激活流程执行中的任务：直接给 FlowRunner 发停止信号
        # （与 /api/flow/stop 相同的节点边界安全收尾语义）
        if runner is not None:
            try:
                runner.request_stop()
            except Exception:  # noqa: BLE001
                pass

    # 任务记录历史上限：长期运行的 WebUI 不能让 _tasks 无限增长
    _TASKS_HISTORY_LIMIT = 200

    def _prune_tasks(self) -> None:
        """裁剪已完成的历史任务记录（须持锁调用）。

        只删非 running 的最旧记录；运行中任务绝不裁剪，
        保证 /api/tasks 状态查询与 SSE 补发不受影响。
        """
        if len(self._tasks) <= self._TASKS_HISTORY_LIMIT:
            return
        overflow = len(self._tasks) - self._TASKS_HISTORY_LIMIT
        finished = [
            tid for tid, rec in self._tasks.items()
            if rec.get("status") != "running"
        ]
        for tid in finished[:overflow]:
            self._tasks.pop(tid, None)

    @staticmethod
    def _invoke_handler(fn: Callable, prompt: str, session_id: str,
                        task_params: Dict[str, Any]) -> Any:
        """按处理器签名调用：声明了 task_params 则传任务参数，否则两参调用。"""
        try:
            import inspect

            sig = inspect.signature(fn)
            accepts = (
                any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values())
                or "task_params" in sig.parameters
            )
        except (TypeError, ValueError):
            accepts = False
        if accepts:
            return fn(prompt, session_id, task_params)
        return fn(prompt, session_id)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            tasks = list(self._tasks.values())
        return {
            "ui": self.ui_id,
            "port": self.port,
            "subscribers": len(self._subscribers),
            "history": len(self._history),
            "tasks_total": len(tasks),
            "tasks_running": len(self._running_sessions),
            "language": self._config.get("language", "en"),
            "tasks": tasks[-20:],
        }

    # ── UIAdapter 协议 ─────────────────────────────────

    def on_event(self, event: Any) -> None:
        """接收 AgentEvent：推送给所有 SSE 订阅者并记录历史。

        payload 先经 json_safe 清洗（ChatMessage 等对象安全落网），
        并为每条事件附上会话 id（sid），供前端按标签页路由。

        sid 解析优先级：submit() 登记的 task_id → 原始浏览器会话 id
        （最高优先，防止内核因会话漂移另开新会话导致事件错投）；
        其次才是 payload 自带的 session_id。
        """
        raw_payload = getattr(event, "payload", {}) or {}
        payload = json_safe(raw_payload)
        task_id = payload.get("task_id")
        sid = None
        if task_id:
            with self._lock:
                sid = self._task_session.get(task_id)
        if not sid:
            sid = payload.get("session_id")
        if not sid:
            sid = getattr(self._tlocal, "session_id", None) or ""
        if task_id and sid:
            with self._lock:
                self._task_session.setdefault(task_id, sid)
        item = {
            "type": getattr(event, "type", "?"),
            "payload": payload,
            "ts": getattr(event, "ts", time.time()),
            "sid": sid or None,
        }
        # 展平常用字段，便于前端直接读取
        for key in ("content", "tool_name", "args", "result", "task_id",
                    "user_input", "error", "steps", "timeout", "stream",
                    "input", "output", "total", "session_id", "question",
                    "reason", "reasoning", "tool_call_tokens"):
            if key in payload:
                item[key] = payload[key]
        # 用量累计
        if item["type"] == "on_usage_update":
            try:
                with self._lock:
                    self._usage["input_tokens"] += int(payload.get("input") or 0)
                    self._usage["output_tokens"] += int(payload.get("output") or 0)
                    self._usage["tool_call_tokens"] += int(payload.get("tool_call_tokens") or 0)
            except (TypeError, ValueError):
                pass
        self._publish(item)

    def ask_user(self, question: str, default: str = "") -> str:
        """向用户提问（人工审批 / 澄清）。等待用户在页面作答，
        超时返回 default，保证自动化场景不被卡死。
        """
        question_id = uuid.uuid4().hex[:12]
        box = {"answer": None, "event": threading.Event()}
        sid = getattr(self._tlocal, "session_id", None) or ""
        if not sid:
            # 兜底：非任务线程（或线程上下文丢失）时，唯一运行中的会话即归属
            with self._lock:
                running = list(self._running_sessions.keys())
                if len(running) == 1:
                    sid = running[0]
        with self._lock:
            self._questions[question_id] = box
            if sid:
                self._question_sessions[sid] = question_id
        self._publish({
            "type": "question", "question": question,
            "question_id": question_id, "ts": time.time(),
            "sid": sid or None,
        })
        box["event"].wait(self.ask_timeout)
        with self._lock:
            self._questions.pop(question_id, None)
            if sid:
                self._question_sessions.pop(sid, None)
        answer = box["answer"]
        if answer is None:
            return default
        return str(answer)

    def answer(self, question_id: str, answer: str,
               session_id: Optional[str] = None) -> None:
        with self._lock:
            box = self._questions.get(question_id)
            if box is None and session_id:
                qid = self._question_sessions.get(session_id)
                box = self._questions.get(qid) if qid else None
        if box is not None:
            box["answer"] = answer
            box["event"].set()

    def notify(self, message: str, level: str = "info") -> None:
        self._publish({
            "type": "notify", "message": message,
            "level": level, "ts": time.time(),
            "sid": getattr(self._tlocal, "session_id", None),
        })

    # ── 会话 REST ──────────────────────────────────────

    def _session_manager(self) -> Any:
        if self._agent is None:
            raise RuntimeError("runtime 未绑定（attach_runtime）")
        return self._agent.session_manager

    def create_session(self, title: str = "", workspace: str = "") -> Dict[str, Any]:
        sm = self._session_manager()
        sess = sm.create_session(title=title or "")
        with self._lock:
            self._session_meta[sess.id] = {
                "title": title or sess.title or sess.id[:8],
                "workspace": workspace or "",
                "created_at": getattr(sess, "created_at", time.time()),
            }
        return self.session_info(sess.id)

    def session_info(self, sid: str) -> Dict[str, Any]:
        sm = self._session_manager()
        sess = sm.get_session(sid)
        with self._lock:
            meta = self._session_meta.get(sid) or {}
        if sess is None and not meta:
            return {"id": sid, "exists": False}
        return {
            "id": sid,
            "title": meta.get("title") or (getattr(sess, "title", "") or sid[:8]),
            "workspace": meta.get("workspace") or "",
            "created_at": meta.get("created_at") or getattr(sess, "created_at", 0.0),
            "exists": sess is not None,
        }

    def list_sessions(self) -> List[Dict[str, Any]]:
        sm = self._session_manager()
        sessions = sm.list_sessions()
        with self._lock:
            meta_map = dict(self._session_meta)
        out = []
        for sess in sessions:
            meta = meta_map.get(sess.id) or {}
            out.append({
                "id": sess.id,
                "title": meta.get("title") or getattr(sess, "title", "") or sess.id[:8],
                "workspace": meta.get("workspace") or "",
                "created_at": meta.get("created_at")
                or getattr(sess, "created_at", time.time()),
            })
        return out

    def session_messages(self, sid: str) -> List[Dict[str, str]]:
        sm = self._session_manager()
        messages = []
        for m in sm.history(sid):
            role = getattr(m, "role", "") or ""
            if role == "tool":
                continue  # 工具消息属于内部过程，不进入聊天面板
            messages.append({
                "role": role,
                "content": getattr(m, "content", "") or "",
            })
        return messages

    def close_session(self, sid: str) -> Dict[str, Any]:
        self.stop_task(sid)
        try:
            sm = self._session_manager()
            sm.delete_session(sid)
        except Exception:  # noqa: BLE001 — 运行时未绑定等情况
            pass
        with self._lock:
            self._session_meta.pop(sid, None)
        return {"ok": True}

    def set_session_title(self, sid: str, title: str) -> None:
        with self._lock:
            meta = self._session_meta.setdefault(sid, {})
            meta["title"] = title
        try:
            sm = self._session_manager()
            sess = sm.get_session(sid)
            if sess is not None:
                sess.title = title
        except Exception:  # noqa: BLE001
            pass

    def set_session_workspace(self, sid: str, workspace: str) -> None:
        with self._lock:
            meta = self._session_meta.setdefault(sid, {})
            meta["workspace"] = workspace

    # ── 配置 ───────────────────────────────────────────

    def _needs_key(self) -> bool:
        model = str(self._config.get("model") or "")
        return model in ("openai_compat", "anthropic")

    def first_run(self) -> bool:
        with self._lock:
            initialized = bool(self._config.get("_initialized"))
            has_key = bool(self._config.get("api_key"))
            needs = self._needs_key()
        return (not initialized) and needs and (not has_key)

    def get_config(self) -> Dict[str, Any]:
        with self._lock:
            cfg = dict(self._config)
        cfg["has_api_key"] = bool(cfg.get("api_key")) or not self._needs_key()
        cfg["first_run"] = self.first_run()
        # 工具挂载信息：注册表全部工具（原生 / 模块）+ 智能体当前生效集 + 预设默认集
        cfg["tools_info"] = self.tools_info()
        cfg["agent_effective_tools"] = self.agent_effective_tools()
        cfg["agent_base_tools"] = list(self._agent_base_tools)
        return cfg

    # ── 智能体工具挂载（文件即模块 → front 自动调用） ──

    def agent_effective_tools(self) -> List[str]:
        """front 智能体当前生效的工具集（preset.tools 已被 config apply 改写）。"""
        agent = self._agent
        preset = getattr(agent, "preset", None) if agent is not None else None
        tools = getattr(preset, "tools", None) if preset is not None else None
        if tools is None:
            tools = list(self._agent_base_tools)
        return [str(t) for t in tools]

    def tools_info(self) -> List[Dict[str, Any]]:
        """注册表全部工具清单：{name, description, source, plugin}。

        source = native（内置原生工具）/ plugin（插件与「文件即模块」
        注册的工具，二者都经 PluginLoader 进入注册表）。
        """
        reg = self._registry()
        if reg is None:
            return []
        tool_plugin: Dict[str, str] = {}
        try:
            plugins = getattr(reg, "_plugins", {}) or {}
            for pname, p in plugins.items():
                get_tools = getattr(p, "get_tools", None)
                for t in (get_tools() if callable(get_tools) else ()) or ():
                    name = getattr(t, "name", "") or ""
                    if name:
                        tool_plugin[name] = pname
        except Exception:  # noqa: BLE001 — 清单必须永不抛出
            pass
        out: List[Dict[str, Any]] = []
        for name in reg.list_tools():
            desc = ""
            try:
                schema = reg.resolve_tool(name).schema() or {}
                func = schema.get("function", schema) if isinstance(schema, dict) else {}
                desc = str(func.get("description", "") or "").strip()
            except Exception:  # noqa: BLE001
                pass
            out.append({
                "name": str(name),
                "description": desc[:200],
                "source": "plugin" if name in tool_plugin else "native",
                "plugin": tool_plugin.get(name, ""),
            })
        return out

    def set_agent_tools(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """设置 front 智能体可用工具集（文件即模块工具的挂载 / 卸载入口）。

        ``tools`` = 显式工具全集；与预设默认集一致时自动回落为非显式
        （跟随预设演进）。经 _apply_config 热应用到运行中的 agent，
        下一次 front 聊天即生效（模型 tool calling 直接可用）。
        """
        tools = data.get("tools") if isinstance(data, dict) else None
        if not isinstance(tools, list):
            return {"ok": False, "error": "tools 必须为字符串列表"}
        explicit = bool(data.get("explicit", True))
        reg = self._registry()
        valid = set(reg.list_tools()) if reg is not None else set()
        cleaned = sorted({str(t) for t in tools if str(t) in valid})
        with self._lock:
            base_sorted = sorted(self._agent_base_tools)
            if cleaned == base_sorted and not data.get("force_explicit"):
                # 与预设默认一致：回落非显式，让工具集跟随预设演进
                explicit = False
                cleaned = []
            self._config["agent_tools"] = cleaned
            self._config["agent_tools_explicit"] = bool(explicit)
            cfg = dict(self._config)
        self._save_config_to_disk(cfg)
        self._apply_config(cfg)
        return {
            "ok": True,
            "agent_tools": self.agent_effective_tools(),
            "explicit": bool(explicit),
            "dropped": sorted(
                {str(t) for t in tools if str(t) not in valid}),
        }

    def save_config(self, incoming: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            for key, value in (incoming or {}).items():
                if key == "_initialized":
                    continue
                self._config[key] = value
            self._config["_initialized"] = True
            cfg = dict(self._config)
        self._save_config_to_disk(cfg)
        self._apply_config(cfg)
        return self.get_config()

    def reset_config(self) -> Dict[str, Any]:
        with self._lock:
            model = self._config.get("model") or ""
            language = self._language
            self._config = dict(DEFAULT_CONFIG)
            self._config["language"] = language
            if model:
                self._config["model"] = model
            cfg = dict(self._config)
        self._save_config_to_disk(cfg)
        self._apply_config(cfg)
        return self.get_config()

    def set_api_key(self, api_key: str) -> Dict[str, Any]:
        with self._lock:
            self._config["api_key"] = api_key or ""
            self._config["_initialized"] = True
            cfg = dict(self._config)
        self._save_config_to_disk(cfg)
        self._apply_config(cfg)
        return {"ok": True, "config": self.get_config()}

    def validate_api_key(self, api_key: str, base_url: str = "") -> Dict[str, Any]:
        if not api_key:
            return {"ok": False, "error": "API Key 为空"}
        try:
            import openai  # noqa: F401  可选依赖
        except ImportError:
            return {"ok": False, "error": "未安装 openai SDK（pip install norpagent[openai]），无法在线验证"}
        try:
            from openai import OpenAI

            client = OpenAI(
                base_url=base_url or self._config.get("api_base")
                or "https://api.deepseek.com",
                api_key=api_key,
                timeout=15,
            )
            client.models.list()
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"invalid api key: {exc}"}

    def _apply_config(self, cfg: Dict[str, Any]) -> None:
        cb = self._config_apply
        if cb is not None:
            try:
                cb(cfg)
            except Exception:  # noqa: BLE001 — 配置应用失败不能拖垮 HTTP
                _logger.exception("config apply 失败")

    # ── 配置持久化（浏览器前端设置 / API Key 跨进程保留） ──

    def _load_config_from_disk(self) -> None:
        """启动时从磁盘加载上次保存的配置（文件缺失 / 损坏时静默忽略）。

        只接受 DEFAULT_CONFIG 中声明的键 + ``_initialized``，
        未知键一律丢弃（防外部写入注入陌生配置项）。
        """
        path = self._config_path
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        allowed = set(DEFAULT_CONFIG) | {"_initialized"}
        for key, value in data.items():
            if key in allowed:
                self._config[key] = value

    def _save_config_to_disk(self, cfg: Dict[str, Any]) -> None:
        """把配置原子写入磁盘（失败只记日志，不拖垮保存流程）。

        - 只落盘 DEFAULT_CONFIG 中的键 + ``_initialized``；
        - 临时文件 + os.replace 原子替换，避免写一半损坏；
        - POSIX 下收紧权限 0600（含 API Key）。
        """
        path = self._config_path
        if not path:
            return
        try:
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
            allowed = set(DEFAULT_CONFIG) | {"_initialized"}
            data = {k: v for k, v in cfg.items() if k in allowed}
            tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(tmp, 0o600)
            except OSError:  # Windows 权限位有限，忽略
                pass
            # Windows 上目标文件可能被瞬时锁定（杀软扫描等），失败重试
            last_exc: Optional[OSError] = None
            for _ in range(3):
                try:
                    os.replace(tmp, path)
                    break
                except OSError as exc:  # noqa: BLE001
                    last_exc = exc
                    time.sleep(0.05)
            else:
                raise last_exc  # type: ignore[misc]
        except OSError as exc:  # noqa: BLE001
            _logger.warning("webui 配置写入 %s 失败: %s", path, exc)

    # ── 模型 / 插件 / 安全 / 统计 ───────────────────────

    def _registry(self) -> Any:
        if self._agent is None:
            return None
        return getattr(self._agent, "registry", None)

    def list_models(self, base_url: str = "",
                    api_key: Optional[str] = None) -> Dict[str, Any]:
        """列出模型：注册表模型 + （提供了 base_url 与 Key 时）远端模型。

        ``api_key`` 显式传入时优先使用（前端「拉取模型列表」直接把
        输入框里当前填写的 Key 送过来，无需先保存到配置即可拉取）。
        拉取成功后把远端模型列表缓存到配置（``remote_models``），
        供 flow 快照的模块坞「模型」分组实时展示。
        """
        reg = self._registry()
        names = sorted(reg.list_models()) if reg is not None else []
        remote: List[str] = []
        error: Optional[str] = None
        used_key = api_key or self._config.get("api_key")
        if base_url and used_key:
            try:
                from openai import OpenAI  # noqa: F401  可选依赖
            except ImportError:
                error = "未安装 openai SDK（pip install norpagent[openai]）"
            else:
                try:
                    client = OpenAI(
                        base_url=base_url,
                        api_key=used_key,
                        timeout=15,
                    )
                    remote = [m.id for m in client.models.list()][:200]
                    remote = filter_remote_models(remote)
                except Exception as exc:  # noqa: BLE001
                    error = f"{exc}"
        if remote:
            # 远端模型列表缓存：更新内存 + 落盘配置（失败不阻塞）
            with self._lock:
                self._config["remote_models"] = list(remote)
                cfg = dict(self._config)
            self._save_config_to_disk(cfg)
            return {"models": remote, "error": None}
        return {"models": names, "error": error}

    def get_plugin_dirs(self) -> List[str]:
        with self._lock:
            dirs = self._config.get("plugin_dirs") or []
        return list(dirs)

    def list_plugins(self) -> List[Dict[str, Any]]:
        reg = self._registry()
        if reg is None:
            return []
        plugins = getattr(reg, "_plugins", {}) or {}
        out = []
        for name in sorted(plugins):
            p = plugins[name]
            tools = []
            try:
                tools = [getattr(t, "name", "") or "" for t in p.get_tools()]
            except Exception:  # noqa: BLE001
                tools = []
            hooks = []
            try:
                hooks = list((p.get_hooks() or {}).keys())
            except Exception:  # noqa: BLE001
                hooks = []
            out.append({
                "name": name,
                "version": str(getattr(p, "version", "") or ""),
                "publisher": str(getattr(p, "publisher", "") or ""),
                "description": str(getattr(p, "description", "") or ""),
                "enabled": True,
                "error": "",
                "tools": tools,
                "hooks": hooks,
                "tool_count": len(tools),
                "hook_count": len(hooks),
                "audit_critical": 0,
                "audit_warning": 0,
                "audit_info": 0,
                "signature_status": "unknown",
                "isolation": "inproc",
            })
        return out

    def add_plugin_dir(self, path: str) -> List[str]:
        path = (path or "").strip()
        with self._lock:
            dirs = list(self._config.get("plugin_dirs") or [])
            if path and path not in dirs:
                dirs.append(path)
                self._config["plugin_dirs"] = dirs
                cfg = dict(self._config)
        self._apply_config(cfg)
        return dirs

    def remove_plugin_dir(self, path: str) -> List[str]:
        path = (path or "").strip()
        with self._lock:
            dirs = [d for d in (self._config.get("plugin_dirs") or []) if d != path]
            self._config["plugin_dirs"] = dirs
            cfg = dict(self._config)
        self._apply_config(cfg)
        return dirs

    def reload_plugins(self) -> List[Dict[str, Any]]:
        with self._lock:
            cfg = dict(self._config)
        self._apply_config(cfg)
        return self.list_plugins()

    def get_security(self) -> Dict[str, Any]:
        with self._lock:
            cfg = dict(self._config)
        # 与桌面前端 openPluginPanel 期望的扁平结构对齐
        return {
            "norp_safe_enabled": cfg.get("norp_safe_enabled", True),
            "plugins_enabled": cfg.get("plugins_enabled", True),
            "audit": cfg.get("plugin_security_audit", "block"),
            "import_restrict": cfg.get("plugin_security_import_restrict", "strict"),
            "require_permissions": cfg.get("plugin_security_require_permissions", True),
            "resource_limit": cfg.get("plugin_security_resource_limit", False),
            "signature_verify": cfg.get("plugin_signature_verify", True),
            "trusted_keys": list(cfg.get("plugin_trusted_keys") or []),
            "isolation": cfg.get("plugin_isolation", "auto"),
            "network_policy": cfg.get("plugin_network_policy", "deny"),
            "network_url_allowlist": list(cfg.get("plugin_network_url_allowlist") or []),
            "network_domain_allowlist": list(cfg.get("plugin_network_domain_allowlist") or []),
            "approval_enabled": cfg.get("approval_enabled", True),
        }

    # 桌面前端 set_plugin_security_config 的 12 个位置参数
    _SECURITY_ARG_KEYS = (
        "plugin_security_audit",            # 0
        "plugin_security_import_restrict",  # 1
        "plugin_security_require_permissions",  # 2
        "plugin_security_resource_limit",   # 3
        "plugin_isolation",                 # 4
        "plugin_signature_verify",          # 5
        "plugin_trusted_keys",              # 6
        "plugin_network_policy",            # 7
        "plugin_network_url_allowlist",     # 8
        "plugin_network_domain_allowlist",  # 9
        "approval_enabled",                 # 10
        "plugins_enabled",                  # 11
    )

    def set_security(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if "norp_safe_enabled" in data:
                self._config["norp_safe_enabled"] = bool(data["norp_safe_enabled"])
            sec = data.get("security")
            if isinstance(sec, dict):
                for key, value in sec.items():
                    if key.startswith("plugin_") or key in ("norp_safe_enabled",):
                        self._config[key] = value
            args = data.get("security_args")
            if isinstance(args, list):
                for idx, value in enumerate(args):
                    if idx < len(self._SECURITY_ARG_KEYS):
                        self._config[self._SECURITY_ARG_KEYS[idx]] = value
            cfg = dict(self._config)
        self._apply_config(cfg)
        return self.get_security()

    def health(self) -> Dict[str, Any]:
        state = "unknown"
        if self._engine_state_fn is not None:
            try:
                state = self._engine_state_fn() or "unknown"
            except Exception:  # noqa: BLE001
                pass
        engine_ok = state in ("running", "starting")
        running = len(self._running_sessions)
        checks = [
            {
                "name": "HTTP Service",
                "passed": True,
                "severity": "info",
                "message": f"listening on http://{self.host}:{self.port}/",
            },
            {
                "name": "Engine",
                "passed": engine_ok,
                "severity": "info" if engine_ok else "error",
                "message": f"engine state: {state}",
            },
            {
                "name": "Running Tasks",
                "passed": True,
                "severity": "info",
                "message": f"{running} task(s) running",
            },
            {
                "name": "SSE Subscribers",
                "passed": True,
                "severity": "info",
                "message": f"{len(self._subscribers)} subscriber(s)",
            },
        ]
        fatal = 0 if engine_ok else 1
        return {
            "ok": engine_ok,
            "status": "healthy" if engine_ok else "degraded",
            "overall_healthy": engine_ok,
            "fatal_count": fatal,
            "error_count": 0 if engine_ok else 1,
            "warning_count": 0,
            "environment_type": "normal",
            "engine_state": state,
            "tasks_running": running,
            "subscribers": len(self._subscribers),
            "uptime": round(time.time() - self._start_ts, 1),
            "checks": checks,
        }

    def usage(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._usage)

    def debug_info(self) -> Dict[str, Any]:
        reg = self._registry()
        return {
            "version": _package_version(),
            "frontend": "web",
            "language": self._config.get("language", "en"),
            "presets": sorted(reg.list_presets()) if reg is not None else [],
            "models": sorted(reg.list_models()) if reg is not None else [],
            "tools": sorted(reg.list_tools()) if reg is not None else [],
            "plugins": sorted(reg.list_plugins()) if reg is not None else [],
            "sessions": len(self.list_sessions()) if self._agent is not None else 0,
            "tasks_total": len(self._tasks),
        }

    # ── 文件系统浏览（浏览器宿主的「目录/文件选择框」） ──

    def list_fs(self, path: str = "", include_files: bool = False) -> Dict[str, Any]:
        """列出一个目录的子目录（可选文件），供浏览器端目录选择框导航。

        path 为空时返回主目录，Windows 下附带盘符列表。
        这是纯本地 UI 能力：服务仅监听 127.0.0.1，且只做只读列举。
        """
        import sys as _sys

        home = os.path.expanduser("~") or ""
        raw = (path or "").strip()
        target = os.path.abspath(os.path.expanduser(raw or home))
        result: Dict[str, Any] = {
            "ok": True,
            "path": target,
            "parent": "",
            "dirs": [],
            "files": [],
            "home": home,
        }
        if _sys.platform == "win32" and not raw:
            drives: List[Dict[str, str]] = []
            try:
                import string as _string

                for letter in _string.ascii_uppercase:
                    root = f"{letter}:\\"
                    if os.path.exists(root):
                        drives.append({"name": root, "path": root})
            except OSError:  # pragma: no cover — 防御
                pass
            result["drives"] = drives
        try:
            entries = sorted(os.scandir(target), key=lambda e: e.name.lower())
        except OSError as exc:
            if not os.path.exists(target):
                # 目录尚不存在（如平台默认工作区）：回退到最近的已存在上级
                ancestor = target
                while ancestor and not os.path.exists(ancestor):
                    parent = os.path.dirname(ancestor)
                    if parent == ancestor:
                        break
                    ancestor = parent
                if ancestor and os.path.isdir(ancestor) and ancestor != target:
                    return self.list_fs(ancestor, include_files=include_files)
            result["ok"] = False
            result["error"] = str(exc)
            return result
        dirs: List[Dict[str, str]] = []
        files: List[Dict[str, str]] = []
        for entry in entries:
            if entry.name.startswith("."):
                continue  # 隐藏条目不进入选择框
            try:
                if entry.is_dir():
                    dirs.append({"name": entry.name, "path": entry.path})
                elif include_files and entry.is_file():
                    files.append({"name": entry.name, "path": entry.path})
            except OSError:
                continue
            if len(dirs) + len(files) >= 500:
                break
        parent = os.path.dirname(target)
        if parent and parent != target:
            result["parent"] = parent
        result["dirs"] = dirs
        result["files"] = files
        return result

    def read_fs_file(self, path: str) -> Dict[str, Any]:
        """读取本地文本文件内容（浏览器宿主的 pick_file 配套能力）。"""
        p = os.path.abspath(os.path.expanduser(path or ""))
        try:
            if os.path.isdir(p):
                return {"ok": False, "error": "目标是一个目录"}
            if os.path.getsize(p) > 2 * 1024 * 1024:
                return {"ok": False, "error": "文件过大（最大 2MB）"}
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                return {"ok": True, "content": f.read()}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    # ── 文件上传 ───────────────────────────────────────

    def upload_files(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把前端 dataURL 文件解码为文本内容（二进制不支持）。"""
        out: List[Dict[str, Any]] = []
        for f in files or []:
            name = str(f.get("name") or "file")
            ftype = str(f.get("type") or "")
            data = str(f.get("data") or "")
            try:
                if "," in data:
                    data = data.split(",", 1)[1]
                raw = base64.b64decode(data)
                if len(raw) > _MAX_UPLOAD_FILE:
                    out.append({"name": name, "type": ftype,
                                "error": "file too large (max 10MB)"})
                    continue
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    out.append({"name": name, "type": ftype,
                                "error": "binary file not supported"})
                    continue
                out.append({"name": name, "type": ftype, "content": text})
            except Exception as exc:  # noqa: BLE001
                out.append({"name": name, "type": ftype, "error": str(exc)})
        return out

    # ── 模块流程（FLOW：/flow 页面的真实后端） ──────────────

    def _flow_workspace(self) -> Any:
        """流程模块工作区（惰性创建，复用插件安全配置）。"""
        if self._flow_ws is None:
            from norpagent.flows import ModuleWorkspace, default_modules_dir

            with self._lock:
                cfg = dict(self._config)
            module_dir = str(cfg.get("flow_modules_dir") or "") or None
            self._flow_ws = ModuleWorkspace(
                module_dir or default_modules_dir(),
                config={
                    "plugin_security_audit": cfg.get("plugin_security_audit", "block"),
                    "plugin_security_import_restrict":
                        cfg.get("plugin_security_import_restrict", "strict"),
                    "plugin_security_require_permissions":
                        cfg.get("plugin_security_require_permissions", True),
                    "plugin_signature_verify":
                        cfg.get("plugin_signature_verify", True),
                    "plugin_trusted_keys":
                        list(cfg.get("plugin_trusted_keys") or []),
                    "plugin_isolation": cfg.get("plugin_isolation", "auto"),
                    "plugin_network_policy":
                        cfg.get("plugin_network_policy", "deny"),
                    "approval_enabled": cfg.get("approval_enabled", True),
                },
            )
        return self._flow_ws

    def flow_snapshot(self) -> Dict[str, Any]:
        """注册表快照：驱动 /flow 页面的模块坞与实例选择。"""
        try:
            from norpagent.flows import build_snapshot

            reg = self._registry()
            if reg is None:
                return {"ok": False, "error": "运行时未绑定（attach_runtime）"}
            state = "unknown"
            if self._engine_state_fn is not None:
                try:
                    state = self._engine_state_fn() or "unknown"
                except Exception:  # noqa: BLE001
                    pass
            snap = build_snapshot(reg, self._agent, engine_state=state)
            # 远端模型列表（最近一次「拉取模型列表」的缓存）与 FE 前端模块
            self._scan_fe_modules()
            with self._lock:
                # 过滤已停用模型名（deepseek-chat / deepseek-reasoner 等历史缓存）
                remote = filter_remote_models(self._config.get("remote_models"))
                fe_mods = [dict(v) for v in self._frontend_modules.values()]
            groups = snap.get("groups") or {}
            groups["remote_models"] = remote
            groups["frontends"] = fe_mods
            snap["groups"] = groups
            # 智能体工具挂载：预设默认集（回退基准）+ 当前生效集
            snap["agent_base_tools"] = list(self._agent_base_tools)
            snap["agent_tools"] = self.agent_effective_tools()
            return snap
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def flow_run(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """启动一次画布图执行（后台线程，进度经 SSE 推送）。

        graph = {nodes: [...], links: [...], prompt: "..."}，
        节点含 config（实例选择）与 inputs（输入面板内容）。
        """
        try:
            from norpagent.flows import FlowRunner, normalize_graph

            reg = self._registry()
            if reg is None:
                return {"ok": False, "error": "运行时未绑定（attach_runtime）"}
            graph = data.get("graph")
            if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
                return {"ok": False, "error": "graph 格式无效（缺少 nodes 列表）"}
            prompt = str(data.get("prompt") or graph.get("prompt") or "")
            graph = normalize_graph(graph)
            graph["prompt"] = prompt

            runner = FlowRunner(reg, self._agent, publish=self._publish)
            flow_id = runner.flow_id
            with self._lock:
                self._flow_runs[flow_id] = runner
            self._publish({
                "type": "notify", "level": "info",
                "message": f"Flow {flow_id} submitted",
                "ts": time.time(), "sid": None,
            })

            def worker() -> None:
                try:
                    result = runner.run(graph)
                    self._publish({
                        "type": "flow.log",
                        "payload": {"flow_id": flow_id, "level": "info",
                                    "message": f"流程结束 status={result.get('status')} "
                                               f"errors={result.get('errors')}"},
                        "ts": time.time(),
                    })
                except Exception as exc:  # noqa: BLE001
                    self._publish({
                        "type": "flow.done",
                        "payload": {"flow_id": flow_id, "status": "error",
                                    "error": f"{type(exc).__name__}: {exc}"},
                        "ts": time.time(),
                    })
                finally:
                    with self._lock:
                        self._flow_runs.pop(flow_id, None)

            threading.Thread(
                target=worker, daemon=True,
                name=f"norpagent-flow-{flow_id[:8]}",
            ).start()
            return {"ok": True, "flow_id": flow_id}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def flow_stop(self, flow_id: str) -> Dict[str, Any]:
        """停止一个运行中的流程（在节点边界生效）。"""
        with self._lock:
            runner = self._flow_runs.get(flow_id)
        if runner is None:
            return {"ok": False, "error": "流程不存在或已结束"}
        try:
            runner.request_stop()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def flow_register(self, name: str, content: str) -> Dict[str, Any]:
        """「文件即模块」真实注册（.py 插件安全管线 / .json/.yaml 描述 /
        .html/.js/.ts 前端模块 FE）。"""
        try:
            reg = self._registry()
            if reg is None:
                return {"ok": False, "error": "运行时未绑定（attach_runtime）"}
            if not name:
                return {"ok": False, "error": "缺少文件名"}
            result = self._flow_workspace().register(reg, name, content)
            module = result.get("module") if isinstance(result, dict) else None
            if module and module.get("kind") == "frontend":
                # 前端模块登记：flow 快照的 frontends 分组 + /fe/<name> 托管
                fname = os.path.basename(str(module.get("url") or f"/fe/{name}"))
                with self._lock:
                    self._frontend_modules[fname] = {
                        "name": str(module.get("name") or ""),
                        "format": str(module.get("format") or "html"),
                        "url": str(module.get("url") or f"/fe/{fname}"),
                        "desc": str(module.get("desc") or ""),
                    }
            return result
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def fe_read_file(self, fname: str) -> Tuple[Optional[bytes], str]:
        """读取 FE 前端模块文件内容（.html/.js/.ts），返回 (bytes, mime)。"""
        safe = re.sub(r"[^\w.\-]", "", fname or "")
        if not safe or os.path.basename(safe) != safe:
            return None, "text/plain"
        ws = self._flow_workspace()
        path = os.path.join(str(getattr(ws, "directory", "")), safe)
        if not os.path.isfile(path):
            return None, "text/plain"
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        mime = {"html": "text/html", "htm": "text/html",
                "js": "application/javascript", "ts": "text/plain"}.get(ext, "text/plain")
        try:
            with open(path, "rb") as f:
                return f.read(), mime
        except OSError:
            return None, "text/plain"

    def _scan_fe_modules(self) -> None:
        """启动后扫描流程模块目录，恢复前端模块列表（重启不丢失）。"""
        if self._fe_scanned:
            return
        self._fe_scanned = True
        try:
            ws = self._flow_workspace()
            directory = str(getattr(ws, "directory", "") or "")
            if not directory or not os.path.isdir(directory):
                return
            for fname in os.listdir(directory):
                ext = os.path.splitext(fname)[1].lstrip(".").lower()
                if ext not in ("html", "htm", "js", "ts"):
                    continue
                with self._lock:
                    if fname in self._frontend_modules:
                        continue
                    self._frontend_modules[fname] = {
                        "name": os.path.splitext(fname)[0],
                        "format": ext,
                        "url": f"/fe/{fname}",
                        "desc": f"前端模块 · {os.path.splitext(fname)[0]}（.{ext}）",
                    }
        except OSError:  # noqa: BLE001
            pass

    # ── FE 独立配置（每个 FE 一个作用域，互不干扰） ──────

    @staticmethod
    def _safe_fe_id(fe_id: str) -> str:
        stem = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", str(fe_id or "fe"))[:64].strip("_")
        return stem or "fe"

    def _fe_config_path(self, fe_id: str) -> str:
        return os.path.join(self._fe_config_dir, f"{self._safe_fe_id(fe_id)}.json")

    def _load_fe_configs(self) -> None:
        try:
            if not os.path.isdir(self._fe_config_dir):
                return
            for fname in os.listdir(self._fe_config_dir):
                if not fname.endswith(".json"):
                    continue
                fe_id = fname[:-5]
                try:
                    with open(os.path.join(self._fe_config_dir, fname),
                              "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        self._fe_configs[fe_id] = data
                except (OSError, ValueError):
                    continue
        except OSError:  # noqa: BLE001
            pass

    def fe_load_config(self, fe_id: str) -> Dict[str, Any]:
        """读取某 FE 的独立配置；无记录时返回全局配置的副本（默认值来源）。"""
        key = self._safe_fe_id(fe_id)
        with self._lock:
            saved = self._fe_configs.get(key)
            if saved is None:
                saved = dict(self._config)
        return {"ok": True, "fe_id": key, "config": json_safe(saved)}

    def fe_save_config(self, fe_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """保存某 FE 的独立配置（原子落盘，互不干扰）。"""
        key = self._safe_fe_id(fe_id)
        clean = {str(k): v for k, v in (config or {}).items()}
        with self._lock:
            self._fe_configs[key] = clean
        try:
            os.makedirs(self._fe_config_dir, exist_ok=True)
            path = self._fe_config_path(key)
            tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(clean, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except OSError as exc:  # noqa: BLE001
            _logger.warning("FE 配置写入 %s 失败: %s", key, exc)
        return {"ok": True, "fe_id": key, "config": json_safe(clean)}

    # ── 流程自动保存 / 应用到智能体 ───────────────────────

    def flow_save(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """保存画布图（自动保存入口），可选激活「应用到智能体」。

        请求体 ``{graph, active}``：
        - graph = {nodes, links, prompt, ...}（前端导出格式）；
        - active = True 时，front 主界面（/chat）的任务改为按该
          流程执行——图就是智能体的行为定义；False 仅落盘。
        """
        graph = data.get("graph") if isinstance(data, dict) else None
        if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
            return {"ok": False, "error": "graph 格式无效（缺少 nodes 列表）"}
        active = bool(data.get("active"))
        nodes_n = len(graph.get("nodes") or [])
        beams_n = len(graph.get("links") or [])
        with self._lock:
            self._flow_graph = dict(graph)
            self._flow_active = active
        self._save_flow_graph_to_disk()
        self._publish({
            "type": "notify", "level": "info",
            "message": ("Flow 已保存并激活应用到智能体 · "
                        f"{nodes_n} 节点 / {beams_n} beam"
                        if active else
                        f"Flow 已保存 · {nodes_n} 节点 / {beams_n} beam"),
            "ts": time.time(), "sid": None,
        })
        return {"ok": True, "active": active,
                "nodes": nodes_n, "beams": beams_n}

    def flow_load(self) -> Dict[str, Any]:
        """返回上次自动保存的画布图与激活状态（页面刷新后恢复）。"""
        with self._lock:
            graph = dict(self._flow_graph) if self._flow_graph else None
            active = self._flow_active
        return {"ok": True, "active": active, "graph": graph}

    def _load_flow_graph_from_disk(self) -> None:
        """启动时恢复上次保存的流程（文件缺失 / 损坏时静默忽略）。"""
        path = self._flow_graph_path
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        graph = data.get("graph")
        if isinstance(graph, dict) and isinstance(graph.get("nodes"), list):
            self._flow_graph = dict(graph)
            self._flow_active = bool(data.get("active"))
            _logger.info("flow graph 已恢复（active=%s, nodes=%s）",
                         self._flow_active, len(graph["nodes"]))

    def _save_flow_graph_to_disk(self) -> None:
        """把当前流程原子写入磁盘（失败只记日志，不拖垮保存流程）。"""
        path = self._flow_graph_path
        if not path:
            return
        try:
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
            with self._lock:
                payload = {
                    "active": self._flow_active,
                    "graph": self._flow_graph,
                    "saved_at": time.time(),
                }
            tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(tmp, 0o600)
            except OSError:  # Windows 权限位有限，忽略
                pass
            os.replace(tmp, path)
        except OSError as exc:  # noqa: BLE001
            _logger.warning("flow graph 写入 %s 失败: %s", path, exc)

    # ── 聊天任务按激活流程执行（行为热切换） ───────────────

    def _active_chat_flow(self) -> Optional[Dict[str, Any]]:
        """当前「应用到智能体」激活的流程（未激活返回 None）。"""
        with self._lock:
            if not self._flow_active or not self._flow_graph:
                return None
            return dict(self._flow_graph)

    def _run_flow_task(self, graph: Dict[str, Any], prompt: str,
                       sid: Optional[str], task_id: str) -> Any:
        """front 主界面的聊天任务按激活流程执行。

        流程最终输出作为助手回复发布（on_content），节点级进度
        以 flow.* 事件同步推送（/flow 页面可实时看到），完成后
        把本轮对话写入会话历史，保持上下文连续。
        """
        from norpagent.flows import FlowRunner, normalize_graph
        from norpagent.kernel.agent import RunResult

        key = sid or task_id
        try:
            reg = self._registry()
            if reg is None:
                raise RuntimeError("运行时未绑定（attach_runtime）")
            g = normalize_graph(graph)
            g["prompt"] = prompt
            runner = FlowRunner(reg, self._agent, publish=self._publish)
            with self._lock:
                self._chat_flow_runs[key] = runner
            self._publish({
                "type": "notify", "level": "info",
                "message": f"FLOW 模式执行 · {len(g.get('nodes') or [])} 节点",
                "ts": time.time(), "sid": sid or None,
            })
            result = runner.run(g)
            status = str(result.get("status") or "done")
            content = str(result.get("final_output") or "").strip()
            error = ""
            if status == "stopped":
                error = "流程被停止"
            elif result.get("errors"):
                error = f"流程完成 · 节点错误 {result.get('errors')} 个"
            self._publish({
                "type": "on_content", "content": content, "stream": False,
                "ts": time.time(), "sid": sid or None,
            })
            self._publish({
                "type": "on_task_done", "ts": time.time(), "sid": sid or None,
            })
            self._append_flow_history(sid, prompt, content)
            return RunResult(
                task_id=task_id, session_id=sid or "", preset_name="flow",
                status="done" if status == "done" else status,
                final_content=content, error=error,
            )
        except Exception as exc:  # noqa: BLE001 — 流程异常按任务错误处理
            self._publish({
                "type": "on_task_error", "error": f"{type(exc).__name__}: {exc}",
                "ts": time.time(), "sid": sid or None,
            })
            return RunResult(
                task_id=task_id, session_id=sid or "", preset_name="flow",
                status="error", final_content="",
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            with self._lock:
                self._chat_flow_runs.pop(key, None)

    def _append_flow_history(self, sid: Optional[str], prompt: str,
                             content: str) -> None:
        """把流程执行的本轮对话写入会话历史（与普通任务一致）。"""
        if not sid:
            return
        agent = self._agent
        sm = getattr(agent, "session_manager", None) if agent is not None else None
        if sm is None:
            return
        try:
            sess = sm.get_session(sid)
            if sess is None:
                return
            from norpagent.protocols.model import ChatMessage

            sm.append_message(sess.id, ChatMessage(role="user", content=prompt))
            if content:
                sm.append_message(
                    sess.id, ChatMessage(role="assistant", content=content))
        except Exception:  # noqa: BLE001 — 历史写入失败不影响任务结果
            pass

    # ── 内部 ───────────────────────────────────────────

    def _publish(self, item: dict) -> None:
        with self._lock:
            self._history.append(item)
            if len(self._history) > self.history_limit:
                self._history = self._history[-self.history_limit:]
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(item)
            except queue.Full:
                pass

    def _register_subscriber(self, q: "queue.Queue[dict]") -> None:
        with self._lock:
            self._subscribers.append(q)

    def _unregister_subscriber(self, q: "queue.Queue[dict]") -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _recent_history(self) -> List[dict]:
        with self._lock:
            return list(self._history[-200:])

    def shutdown(self) -> None:
        """停止 HTTP 服务并断开全部订阅者（幂等，可跨线程调用）。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for q in self._subscribers:
                try:
                    q.put_nowait({"type": "notify", "message": "server closed",
                                  "ts": time.time(), "sid": None})
                except queue.Full:
                    pass
            self._subscribers.clear()
            server = self._server
            self._server = None
        if server is None:
            return
        # server.shutdown() 必须在 serve_forever 线程之外调用，
        # 否则死锁（防御：同线程时只关底层 socket）。
        if self._thread is not None and threading.current_thread() is self._thread:
            try:
                server.server_close()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001 — 可能已被服务线程关闭
            pass
        try:
            server.server_close()
        except Exception:  # noqa: BLE001
            pass


def _package_version() -> str:
    try:
        import norpagent

        return getattr(norpagent, "__version__", "?")
    except Exception:  # noqa: BLE001
        return "?"


__all__ = ["WebUI", "json_safe"]

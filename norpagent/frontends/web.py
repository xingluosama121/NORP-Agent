# Copyright (c) 2026 xingluosama121, MIT Licensed
"""Web 前端：HTTP + SSE 界面（front.html 多宿主前端，零依赖）。

默认前端（norpagent() 不带参数时自动使用）：
- 启动一个零依赖 HTTP 服务，控制台打印 listening on 地址；
- 浏览器打开 http://127.0.0.1:<port>/ 即为完整聊天界面
  （front.html：多标签会话 / 流式渲染 / 设置 / 插件面板）；
- 任务经 /chat 提交、事件经 SSE 推送（事件翻译由页面内 bridge 完成）。

常用参数（np() 关键字或 config={"web": {...}}）：
    port         端口（默认 8787）
    host         监听地址（默认 127.0.0.1）
    open_browser 是否自动打开浏览器（默认 False）
    language     界面语言（默认 "en"，如 "zh_CN"）
    html         自定义主页面（槽位挂载参数）：文件路径或 HTML 内容
                 （strip 后以 "<" 开头视为内容，否则视为文件路径；
                  文件不存在时 WebUI 构造抛 ValueError）。替换 / 路由
                  默认的 front.html，无需物理覆盖库文件。四种传入途径：
                   1. 构造函数   WebFrontend(html="/path/to/my.html")
                   2. 地址子句   np(frontend="norpagent.frontends.web:WebFrontend;html=/path/to/my.html")
                   3. 配置字典   np(config={"web": {"html": "<html>...</html>"}})
                   4. 运行时参数 np(html="/path/to/my.html")

停止方式：页面「退出程序」按钮、np.stop() 轮询生命周期、
或 np.shutdown() / 引擎 request_stop()。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from norpagent.frontends.base import Frontend


class WebFrontend:
    """HTTP + SSE 前端外壳（页面 = front.html）。"""

    frontend_id = "web"

    def __init__(
        self,
        port: int = 8787,
        host: str = "127.0.0.1",
        open_browser: bool = False,
        html: Optional[str] = None,
        sse_queue_size: Optional[int] = None,
        sse_queue_policy: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        # 架构层工厂可能注入 config dict（如 np(config={"web": {...}}）
        # 或地址子句 np(frontend="...:WebFrontend;html=...")）
        cfg = kwargs.get("config") or {}
        if isinstance(cfg, dict):
            port = cfg.get("port", port)
            host = cfg.get("host", host)
            open_browser = bool(cfg.get("open_browser", open_browser))
            html = cfg.get("html", html)
            if sse_queue_size is None:
                sse_queue_size = cfg.get("sse_queue_size")
            if sse_queue_policy is None:
                sse_queue_policy = cfg.get("sse_queue_policy")
        self.port = int(port)
        self.host = str(host)
        self.open_browser = bool(open_browser)
        # SSE 背压（超高并发）：None 时由 WebUI 用默认 / 环境变量
        self._sse_queue_size: Optional[int] = (
            int(sse_queue_size) if sse_queue_size is not None else None
        )
        self._sse_queue_policy: Optional[str] = sse_queue_policy
        # 自定义主页面（None = 库内置 front.html）。
        # 最终解析（内容 / 文件路径）在 WebUI 构造时进行：文件不存在
        # 会抛 ValueError 快速失败，而不是静默回落默认页面。
        self._html: Optional[str] = html if html else None
        self._engine: Optional[Any] = None
        self._ui: Optional[Any] = None
        self._base_tools: List[str] = []  # 预设默认工具集快照
        self._gate = threading.Lock()

    def attach(self, engine: Any) -> None:
        from norpagent.builtin.ui.web import WebUI

        self._engine = engine
        # 预设默认工具集快照：agent_tools 非显式时的回退基准。
        # 必须在任何 _apply_config 之前捕获（apply 会改写 preset.tools）。
        agent = engine.agent
        if agent is not None:
            preset = getattr(agent, "preset", None)
            if preset is not None:
                self._base_tools = list(getattr(preset, "tools", ()) or ())
        # 运行时参数透传（np(port=..., language=..., html=...) 等非槽位键）
        params: Dict[str, Any] = dict(getattr(engine, "params", None) or {})
        self.port = int(params.get("port", self.port))
        self.host = str(params.get("host", self.host))
        if "open_browser" in params:
            self.open_browser = bool(params["open_browser"])
        # SSE 背压运行时参数透传（np(sse_queue_size=..., sse_queue_policy=...)）
        if params.get("sse_queue_size") is not None:
            self._sse_queue_size = int(params["sse_queue_size"])
        if params.get("sse_queue_policy"):
            self._sse_queue_policy = str(params["sse_queue_policy"])
        language = str(params.get("language") or "en")
        # np(html=...) 透传优先于构造时配置（与 port/host 一致）。
        # 值为空字符串视为未指定，回落构造值。
        if params.get("html"):
            self._html = str(params["html"])

        self._ui = WebUI(
            port=self.port, host=self.host, language=language,
            html=self._html,
            sse_queue_size=self._sse_queue_size,
            sse_queue_policy=self._sse_queue_policy,
        )
        self._ui.set_handler(self._handle_task)
        self._ui.attach_runtime(engine.agent)
        self._ui.set_config_apply(self._apply_config)
        self._ui.set_quit_callback(
            lambda: engine.request_stop()
        )
        self._ui.set_engine_state_fn(lambda: getattr(engine, "state", None).value)

        # 让 Agent 运行时的 ctx.ui 指向本实例：人工审批 / 澄清提问
        # 才能经 SSE 推送到浏览器（否则会落到注册表单例 WebUI 上，
        # 提问永远无人应答）。替换时退订旧的静默监听。
        agent = engine.agent
        if agent is not None:
            old_listener = getattr(agent, "_ui_listener", None)
            if old_listener is not None and old_listener is not self._ui.on_event:
                try:
                    engine._bus.unsubscribe(old_listener)
                except Exception:  # noqa: BLE001
                    pass
            agent.ui = self._ui
            agent._ui_listener = self._ui.on_event
            engine._bus.subscribe(self._ui.on_event)
        engine.subscribe_ui(self._ui)

        # 启动即恢复持久化的智能体工具集（上次保存的 agent_tools 配置）
        try:
            saved = self._ui.get_config()
            self._apply_agent_tools(saved or {})
        except Exception:  # noqa: BLE001 — 恢复失败不阻塞启动
            pass

    def reattach_agent(self, engine: Any) -> None:
        """运行中热挂载：AgentRuntime 热重建后重绑渲染器与数据源。

        不重启 HTTP 服务（端口不变、页面不断连），只把 UI 的数据源
        指向新运行时，并把人工审批 / 澄清提问等交互通道切到新运行时
        的渲染器（ctx.ui）。
        """
        self._engine = engine
        agent = getattr(engine, "agent", None)
        if agent is None or self._ui is None:
            return
        # UI 数据源重绑（会话 REST / 插件列表 / 调试信息等读 agent）
        self._ui.attach_runtime(agent)
        # ctx.ui 指向本实例：审批 / 提问经 SSE 推送到浏览器
        old_listener = getattr(agent, "_ui_listener", None)
        if old_listener is not None and old_listener is not self._ui.on_event:
            try:
                engine._bus.unsubscribe(old_listener)
            except Exception:  # noqa: BLE001
                pass
        agent.ui = self._ui
        agent._ui_listener = self._ui.on_event
        engine._bus.subscribe(self._ui.on_event)
        engine.subscribe_ui(self._ui)

    def _handle_task(self, prompt_text: str, session_id: Optional[str],
                     task_params: Optional[Dict[str, Any]] = None) -> Any:
        # 同一运行时串行执行任务
        with self._gate:
            return self._engine.submit(
                prompt_text, session_id=session_id, task_params=task_params
            )

    def _apply_config(self, cfg: Dict[str, Any]) -> None:
        """页面保存配置后应用：模式 / 模型 / 远端地址 / API Key / 插件目录 / 安全。"""
        engine = self._engine
        if engine is None or cfg is None:
            return
        reg = engine.registry

        # ── 模式：注册表预设热切换（front「模式」选择器） ──
        # remount(preset=...) 会热重建 AgentRuntime（停旧沙箱 → 建新
        # 运行时 → 前端重绑），因此任务执行中（gate 占用）跳过，等
        # 下一次配置应用或重启生效；未变化同样跳过。
        # 注意：槽位覆盖时装配层会把衍生预设命名为 {base}_arch，
        # 比较统一用基础名，避免每次保存配置都误判为「模式变化」。
        preset_name = str(cfg.get("preset_name") or "")
        agent = engine.agent
        current_name = ""
        if agent is not None:
            preset = getattr(agent, "preset", None)
            raw = str(getattr(preset, "name", "") or "")
            current_name = raw[:-5] if raw.endswith("_arch") else raw
        if preset_name and preset_name != current_name:
            if not self._gate.locked():
                try:
                    engine.remount(preset=preset_name)
                except Exception:  # noqa: BLE001 — 预设名无效时保留当前模式
                    pass
                agent = engine.agent
                if agent is not None:
                    preset = getattr(agent, "preset", None)
                    if preset is not None:
                        # 预设热切换后刷新默认工具集快照（apply_agent_tools
                        # 回退基准跟随新预设，而非旧预设的工具集）
                        self._base_tools = list(getattr(preset, "tools", ()) or ())

        # ── 模型：注册表适配器名 / 远端模型名 两种语义 ──
        agent = engine.agent
        model = str(cfg.get("model") or "")
        api_base = str(cfg.get("api_base") or "") or None
        api_key = str(cfg.get("api_key") or "") or None

        if model in reg.list_models():
            if model in ("openai_compat", "anthropic"):
                try:
                    if model == "openai_compat":
                        from norpagent.builtin.models.openai_compat import OpenAICompatProvider

                        reg.register_model("openai_compat", OpenAICompatProvider(
                            model_name=None, base_url=api_base, api_key=api_key,
                        ))
                    else:
                        from norpagent.builtin.models.anthropic import AnthropicProvider

                        reg.register_model("anthropic", AnthropicProvider(
                            model_name=None, api_key=api_key,
                        ))
                except Exception:  # noqa: BLE001 — 参数不完整时保留原 provider
                    pass
            # 其它已注册适配器名：无需动作
        elif model:
            # 远端模型名：挂到 openai_compat 适配器上
            try:
                from norpagent.builtin.models.openai_compat import OpenAICompatProvider

                reg.register_model("openai_compat", OpenAICompatProvider(
                    model_name=model, base_url=api_base, api_key=api_key,
                ))
            except Exception:  # noqa: BLE001
                pass
            model = "openai_compat"

        # 让运行时下一次 run() 使用新模型
        if agent is not None and model:
            preset = getattr(agent, "preset", None)
            if preset is not None:
                try:
                    preset.model = model
                except Exception:  # noqa: BLE001
                    pass

        # ── 智能体工具集（文件即模块 → front 自动调用） ──
        self._apply_agent_tools(cfg)

        # ── NORP 安全：开启时安装（一句话开启全套安全） ──
        if cfg.get("norp_safe_enabled") and getattr(reg, "security", None) is None:
            try:
                from norpagent import safe

                safe(reg, level="standard")
            except Exception:  # noqa: BLE001
                pass

        # ── 插件目录：重新安装（签名→审计→导入限制管线） ──
        dirs = cfg.get("plugin_dirs") or []
        if dirs:
            try:
                from norpagent.plugins import install_plugin_dirs

                install_plugin_dirs(reg, [str(d) for d in dirs], config={
                    "plugin_security_audit": cfg.get("plugin_security_audit") or "warn",
                    "plugin_signature_verify": True,
                })
            except Exception:  # noqa: BLE001
                pass

    def _apply_agent_tools(self, cfg: Dict[str, Any]) -> None:
        """把配置中的智能体工具集热应用到运行中的 agent。

        - ``agent_tools_explicit`` 为 True：``agent_tools`` 即精确工具集
          （未注册的工具名自动过滤，模块卸载后不会报错）；
        - 否则回退为预设默认工具集（``self._base_tools`` 快照）。
        下一次 run() 读取 preset.tools 生成 tool schemas，模型即可
        自动调用（tool calling）。文件即模块注册的工具与原生工具
        在这里被一视同仁地挂载。
        """
        engine = self._engine
        if engine is None or not isinstance(cfg, dict):
            return
        agent = engine.agent
        if agent is None:
            return
        preset = getattr(agent, "preset", None)
        if preset is None:
            return
        try:
            registered = set(engine.registry.list_tools())
        except Exception:  # noqa: BLE001
            registered = set()
        if cfg.get("agent_tools_explicit"):
            preset.tools = [
                str(t) for t in (cfg.get("agent_tools") or [])
                if str(t) in registered
            ]
        else:
            preset.tools = list(self._base_tools)

    def start(self) -> None:
        if self._ui is not None:
            self._ui.start()
            self.port = int(self._ui.port)
            print(f"[norpagent] listening on http://{self.host}:{self.port}/")
            if self.open_browser:
                try:
                    import webbrowser

                    threading.Thread(
                        target=lambda: webbrowser.open(
                            f"http://{self.host}:{self.port}/"
                        ),
                        daemon=True,
                        name="norpagent-webui-browser",
                    ).start()
                except Exception:  # noqa: BLE001 — 无图形环境静默
                    pass

    def stop(self) -> None:
        if self._ui is not None:
            try:
                self._ui.shutdown()
            except Exception:  # noqa: BLE001
                pass

    def is_alive(self) -> bool:
        return bool(self._ui is not None and getattr(self._ui, "_running", True))


__all__ = ["WebFrontend"]

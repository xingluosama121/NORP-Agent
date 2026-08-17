# Copyright (c) 2026 xingluosama121, MIT Licensed
"""Agent 运行时：框架内核中唯一的「循环」。

循环只依赖注册表与协议接口，不 import 任何具体模型 SDK 或工具实现：

    输入(L3) -> 会话与历史(L4) -> 消息组装(L5) -> [ 步骤(L6) ->
    模型调用(L7) -> 工具调用(L8) ]* -> 结果定型(L9)

**每一个执行结构都暴露为 API 且可被钩子干预**：

- 钩子：``runtime.hooks``（9 层 29 钩子，见 norpagent.hooks），
  可变钩子可改写数据流，``HookVeto`` 一票否决；
- 方法：``prepare_input / create_session / append_message /
  build_messages / call_model / execute_tool_call / finalize_result``
  全部是公共方法，子类可直接覆写，无需改动循环本体。

安全系统通过 ``registry.security``（norpagent.safe() 安装）注入，
本文件不再直接依赖 norpagent.security —— 安全已整体剥离。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from norpagent.hooks.core import HookVeto
from norpagent.kernel.context import RunContext
from norpagent.kernel.events import EventBus
from norpagent.kernel.presets import Preset
from norpagent.kernel.registry import ComponentError, Registry
from norpagent.loops.cancel import cancel_requested, current_cancel_event
from norpagent.protocols.model import ChatMessage, ModelUsage, ToolCallSpec
from norpagent.protocols.tool import ToolResult, tool_error

if TYPE_CHECKING:
    from norpagent.protocols.sandbox import Sandbox
    from norpagent.protocols.scheduler import TaskScheduler
    from norpagent.protocols.session import SessionManager
    from norpagent.protocols.ui import UIAdapter

# 连续空输出达到该次数则中断任务，防止模型退化导致死循环
_EMPTY_OUTPUT_LIMIT = 3


class ModelCallTimeout(Exception):
    """模型单次调用超过 call_timeout 的硬超时。

    硬中断语义：主循环立即放弃等待并返回 timeout 结果；
    后台请求线程被标记取消（params["_cancel_event"]），
    适配器流式循环据此尽早退出，SDK 自身的连接超时兜底。
    """

    def __init__(self, timeout: float) -> None:
        super().__init__(f"模型调用超过 {timeout}s 未完成（已硬中断，后台请求被放弃）")
        self.timeout = timeout


@dataclass
class RunResult:
    """一次 run() 的完整结果。"""

    task_id: str = ""
    session_id: str = ""
    preset_name: str = ""
    status: str = "done"  # done | stopped | error | timeout
    steps: int = 0
    tool_call_count: int = 0
    usage: ModelUsage = field(default_factory=ModelUsage)
    final_content: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "done"


class AgentRuntime:
    """通用 Agent 运行时。

    用法::

        reg = Registry(); install_defaults(reg); register_all_presets(reg)
        agent = AgentRuntime(reg, preset="minimal")
        result = agent.run("你好")

    ``session_manager`` / ``sandbox`` / ``scheduler`` / ``ui`` 均可从外部
    传入以覆盖预设声明（便于测试与 A/B 对比，也是基准测试的入口）。
    ``components`` 同理：{kind: instance}，覆盖预设声明的组件装配。
    """

    def __init__(
        self,
        registry: Registry,
        preset: Preset | str,
        session_manager: Optional["SessionManager"] = None,
        sandbox: Optional["Sandbox"] = None,
        scheduler: Optional["TaskScheduler"] = None,
        ui: Optional["UIAdapter"] = None,
        task_params: Optional[Dict[str, Any]] = None,
        components: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.registry = registry
        self.bus: EventBus = registry.bus
        self.hooks = registry.hooks  # 9 层钩子体系（HookSystem）
        self.preset = registry.resolve_preset(preset) if isinstance(preset, str) else preset

        # 组件齐备性校验：缺失时给出可操作的报错
        missing, missing_tools = registry.validate_preset(self.preset)
        if missing or missing_tools:
            parts = missing + [f"tools={t}" for t in missing_tools]
            raise ComponentError(
                f"预设模式 '{self.preset.name}' 缺少组件: {', '.join(parts)}。"
                f"可用模型 {registry.list_models()} / 工具 {registry.list_tools()}。"
                "部分模式所需的组件随可选依赖提供（如 norpagent[openai]），见 README。"
            )

        self.params = self.preset.merged_params(task_params)
        self.session_manager = session_manager or registry.build_session(self.preset.session)
        self.sandbox = sandbox if sandbox is not None else registry.build_sandbox(self.preset.sandbox)
        self.scheduler = scheduler if scheduler is not None else registry.build_scheduler(self.preset.scheduler)
        self.ui = ui if ui is not None else registry.resolve_ui(self.preset.ui)

        # 通用组件装配：预设声明 components={kind: name}，运行时按名构建。
        # 组件实例在整个运行时生命周期内共享（上下文存储 / 项目管理等）。
        # 外部注入优先（测试 / 依赖注入场景）。
        if components is not None:
            self.components: Dict[str, Any] = dict(components)
        else:
            workspace_hint = self.params.get("workspace_root") or None
            self.components = {
                kind: registry.build_component(kind, name, workspace_root=workspace_hint)
                for kind, name in (self.preset.components or {}).items()
            }

        # 挂载 UI 为事件订阅者（shutdown 时退订，避免共享总线上的重复输出）
        self._ui_listener: Optional[Callable] = None
        if self.ui is not None and hasattr(self.ui, "on_event"):
            self._ui_listener = self.ui.on_event
            self.bus.subscribe(self._ui_listener)

        # 因 call_timeout 硬中断而残留的后台线程（daemon），shutdown 时回收
        self._orphan_threads: List[threading.Thread] = []

        self.hooks.on_agent_init.emit(preset=self.preset.name)

    # ══════════════════════════════════════════════════════
    #  主循环
    # ══════════════════════════════════════════════════════

    def run(
        self,
        user_input: str,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        task_params: Optional[Dict[str, Any]] = None,
    ) -> RunResult:
        """执行一次用户任务。

        - 输入经 L3 管线（可改写 / 可否决 / 安全扫描）
        - 新建或续接会话（L4），把用户输入写入历史
        - 最多 ``max_steps`` 轮 L6-L8 循环
        - 达到 ``task_timeout`` 触发 on_task_timeout 并停止
        - 所有路径统一经 L9 结果定型钩子后返回
        """
        task_id = task_id or uuid.uuid4().hex[:12]
        # 构造级 task_params 作为运行时默认参数，任务级 task_params 覆盖之
        params = dict(self.params)
        if task_params:
            params.update(task_params)
        # Ctrl+C / 引擎停止的取消事件注入 params：
        # 模型流式循环（call_timeout=0 时同样生效）与工具经
        # params["_cancel_event"] / cancel_requested() 检查并尽早退出。
        cancel_event = params.get("_cancel_event")
        if not isinstance(cancel_event, threading.Event):
            cancel_event = current_cancel_event()
        if isinstance(cancel_event, threading.Event):
            params["_cancel_event"] = cancel_event
        # 剔除已退出的超时孤儿线程引用，防止长期运行内存累积
        if self._orphan_threads:
            self._orphan_threads = [
                t for t in self._orphan_threads if t.is_alive()
            ]
        result = RunResult(task_id=task_id, preset_name=self.preset.name)
        start_ts = time.time()

        # ── L3 输入管线：before_input 可改写 / 一票否决 ──
        try:
            user_input = self.prepare_input(
                user_input, task_id=task_id, session_id=session_id, params=params
            )
        except HookVeto as veto:
            result.status = "stopped"
            result.error = str(veto)
            self.hooks.on_task_stopped.emit(task_id=task_id, reason=str(veto))
            return self.finalize_result(result, task_id)

        # ── 越狱/注入防护（params["jailbreak_guard"] 显式路径；
        #    norpagent.safe() 安装的钩子路径在 before_input 处已完成） ──
        guard_config = params.get("jailbreak_guard")
        if guard_config:
            from norpagent.security.guard import scan_message

            blocked, reason, _ = scan_message(user_input)
            if blocked:
                result.status = "stopped"
                result.error = reason or "输入被安全防护拦截"
                self.hooks.on_task_stopped.emit(
                    task_id=task_id,
                    reason="jailbreak_guard", detail=reason,
                )
                return self.finalize_result(result, task_id)

        # ── 系统提示词加固（params 显式路径） ──
        system_prompt = params.get("system_prompt", "") or ""
        if params.get("harden_prompt"):
            from norpagent.security.guard import harden_system_prompt

            system_prompt = harden_system_prompt(
                system_prompt, self.preset.tools
            )

        # ── L4 会话准备 ──
        try:
            sess = None
            if session_id:
                sess = self.session_manager.get_session(session_id)
            if sess is None:
                sess = self.create_session(
                    candidate_id=session_id,
                    title=user_input[:40],
                    params=params,
                    task_id=task_id,
                )
            result.session_id = sess.id
            self.append_message(
                self.session_manager, sess.id,
                ChatMessage(role="user", content=user_input),
                task_id,
            )
        except HookVeto as veto:
            result.status = "stopped"
            result.error = str(veto)
            self.hooks.on_task_stopped.emit(task_id=task_id, reason=str(veto))
            return self.finalize_result(result, task_id)

        ctx = RunContext(
            registry=self.registry,
            session_manager=self.session_manager,
            session_id=sess.id,
            sandbox=self.sandbox,
            scheduler=self.scheduler,
            ui=self.ui,
            params=params,
            task_id=task_id,
            preset_name=self.preset.name,
            components=self.components,
        )

        self.hooks.on_task_start.emit(
            task_id=task_id,
            session_id=sess.id,
            preset=self.preset.name,
            user_input=user_input,
        )

        max_steps = int(params.get("max_steps", 32))
        task_timeout = float(params.get("task_timeout", 0) or 0)
        model_provider = self.registry.resolve_model(self.preset.model)
        tool_schemas = self.registry.tool_schemas(self.preset.tools)

        # 用户停止检查（Web 前端「停止」按钮等注入的可调用对象）：
        # 在每轮边界检查，返回 True 即以 stopped 收尾。
        stop_check = params.get("_stop_check")

        empty_streak = 0
        try:
            for step in range(1, max_steps + 1):
                # Ctrl+C / 引擎停止：取消事件置位 → 本轮边界立即收尾
                # （阻塞中的模型调用 / 沙箱命令由各自的取消检查中断）
                if cancel_requested():
                    result.status = "stopped"
                    result.error = "任务被中断（Ctrl+C）"
                    self.hooks.on_task_stopped.emit(
                        task_id=task_id, reason=result.error,
                    )
                    break
                if callable(stop_check):
                    try:
                        should_stop = bool(stop_check())
                    except Exception:  # noqa: BLE001 — 检查器自身出错不终止任务
                        should_stop = False
                    if should_stop:
                        result.status = "stopped"
                        result.error = "任务被用户停止"
                        self.hooks.on_task_stopped.emit(
                            task_id=task_id, reason=result.error,
                        )
                        break
                # 任务级超时检查位于轮次边界。单次模型调用阻塞期间的
                # 硬中断由 call_timeout 参数控制（见 call_model）。
                if task_timeout and (time.time() - start_ts) > task_timeout:
                    result.status = "timeout"
                    result.error = f"任务超过 {task_timeout}s 未完成"
                    self.hooks.on_task_timeout.emit(
                        task_id=task_id, timeout=task_timeout,
                        kind="task_timeout",
                    )
                    break

                # ── L5 消息组装 ──
                history = self.build_messages(
                    system_prompt, sess.id, step=step, task_id=task_id,
                    tool_names=list(self.preset.tools),
                )

                # ── L6 before_step：可改写本轮消息 / 跳过本轮 ──
                self.hooks.before_step.emit(
                    task_id=task_id, step=step,
                    context=ctx, params=params,
                )
                try:
                    modified = self.hooks.before_step.intercept(
                        task_id=task_id, step=step,
                        messages=history, context=ctx, params=params,
                    )
                except HookVeto:
                    continue  # 钩子否决本轮：跳过模型调用
                if isinstance(modified, list):
                    history = modified

                # ── L7 模型调用 ──
                output = self.call_model(
                    model_provider, history, tool_schemas, params,
                    task_id, result, step,
                )

                result.steps = step
                content = (output.content or "").strip()
                tool_calls = output.tool_calls or []

                if content:
                    empty_streak = 0
                    self.hooks.on_content.emit(
                        task_id=task_id, content=content,
                        stream=False, final=(not tool_calls),
                    )

                if tool_calls:
                    self.hooks.after_step.emit(
                        task_id=task_id, step=step,
                        content=content, tool_calls=len(tool_calls),
                    )
                    # 记录 assistant 消息（含工具调用意图与思维链原文；
                    # DeepSeek V4 等推理端点要求工具轮次原样回传 reasoning_content）
                    self.append_message(
                        self.session_manager, sess.id,
                        ChatMessage(
                            role="assistant", content=content,
                            tool_calls=tool_calls,
                            reasoning=getattr(output, "reasoning", "") or "",
                            has_reasoning=bool(getattr(output, "has_reasoning", False)),
                        ),
                        task_id,
                    )
                    for spec in tool_calls:
                        tool_result = self.execute_tool_call(spec, ctx, task_id)
                        result.tool_call_count += 1
                        self.append_message(
                            self.session_manager, sess.id,
                            ChatMessage(
                                role="tool",
                                content=str(tool_result),
                                tool_call_id=spec.id,
                                name=spec.name,
                            ),
                            task_id,
                        )
                    continue  # 继续下一轮，等待模型消化工具结果

                # 无工具调用：把模型输出作为最终回复
                empty_streak = empty_streak + 1 if not content else 0
                if not content and empty_streak >= _EMPTY_OUTPUT_LIMIT:
                    result.status = "stopped"
                    result.error = "模型连续多轮无输出，任务中断"
                    self.hooks.on_task_stopped.emit(
                        task_id=task_id, reason=result.error,
                    )
                    break
                if not content:
                    continue

                result.final_content = content
                self.append_message(
                    self.session_manager, sess.id,
                    ChatMessage(
                        role="assistant", content=content,
                        reasoning=getattr(output, "reasoning", "") or "",
                        has_reasoning=bool(getattr(output, "has_reasoning", False)),
                    ),
                    task_id,
                )
                self.hooks.on_task_done.emit(
                    task_id=task_id, session_id=sess.id,
                    content=content, steps=step, context=ctx,
                )
                return self.finalize_result(result, task_id)

            # 循环耗尽（step 超限）
            if result.status not in ("timeout", "stopped"):
                result.status = "stopped"
                result.error = f"达到最大步数上限 max_steps={max_steps}"
                self.hooks.on_task_stopped.emit(
                    task_id=task_id, reason=result.error,
                )
        except HookVeto as veto:
            # 钩子在中段否决（before_model_call / before_session_create 等）
            result.status = "stopped"
            result.error = str(veto)
            self.hooks.on_task_stopped.emit(task_id=task_id, reason=str(veto))
        except ModelCallTimeout as exc:
            # 模型调用硬超时：任务立即终止
            result.status = "timeout"
            result.error = str(exc)
            self.hooks.on_task_timeout.emit(
                task_id=task_id, timeout=exc.timeout,
                kind="call_timeout",
            )
        except Exception as exc:  # noqa: BLE001 — 任务级兜底，保证事件闭环
            result.status = "error"
            result.error = f"{type(exc).__name__}: {exc}"
            self.hooks.on_task_error.emit(task_id=task_id, error=result.error)

        if result.final_content:
            self.hooks.on_task_done.emit(
                task_id=task_id, content=result.final_content,
            )
        return self.finalize_result(result, task_id)

    # ══════════════════════════════════════════════════════
    #  执行结构 API（每个步骤独立可覆写 / 可挂钩子）
    # ══════════════════════════════════════════════════════

    # ── L3 输入 ──────────────────────────────────────────

    def prepare_input(
        self,
        user_input: str,
        *,
        task_id: str,
        session_id: Optional[str],
        params: Dict[str, Any],
    ) -> str:
        """L3 输入处理：before_input 可变钩子 + after_input 观测。

        返回 str = 最终输入；抛 HookVeto = 任务以 stopped 收尾。
        """
        modified = self.hooks.before_input.intercept(
            task_id=task_id, user_input=user_input,
            session_id=session_id, params=params,
        )
        if isinstance(modified, str):
            user_input = modified
        self.hooks.after_input.emit(
            task_id=task_id, user_input=user_input, session_id=session_id,
        )
        return user_input

    # ── L4 会话与历史 ────────────────────────────────────

    def create_session(
        self,
        *,
        candidate_id: Optional[str],
        title: str,
        params: Dict[str, Any],
        task_id: str,
    ) -> Any:
        """L4 会话创建：before/after_session_create 钩子，标题可改写。

        ``candidate_id`` 存在时优先复用（幂等续接）：浏览器/宿主传入的
        会话 id 与内核最终使用的 id 保持一致，事件流（思考 / 回复 /
        任务结束）才能按前端标签页正确路由，不再漂移出第二个会话。
        """
        modified = self.hooks.before_session_create.intercept(
            session_id=candidate_id, title=title, params=params, task_id=task_id,
        )
        if isinstance(modified, str):
            title = modified
        elif isinstance(modified, dict) and isinstance(modified.get("title"), str):
            title = modified["title"]
        if candidate_id:
            existing = self.session_manager.get_session(candidate_id)
            if existing is not None:
                sess = existing
            else:
                sess = self.session_manager.create_session(
                    title=title, session_id=candidate_id
                )
        else:
            sess = self.session_manager.create_session(title=title)
        self.hooks.after_session_create.emit(
            session_id=sess.id, title=title, task_id=task_id,
        )
        return sess

    def append_message(
        self,
        session_manager: Any,
        session_id: str,
        message: ChatMessage,
        task_id: str,
    ) -> bool:
        """L4 消息落库：before_message_append 可替换/丢弃，after 可观测。

        返回是否实际落库。钩子返回 False 或抛 HookVeto = 丢弃该条。
        """
        try:
            modified = self.hooks.before_message_append.intercept(
                session_id=session_id, message=message, task_id=task_id,
            )
        except HookVeto:
            return False
        if modified is False:
            return False
        if modified is not None and getattr(modified, "role", None):
            message = modified
        session_manager.append_message(session_id, message)
        self.hooks.after_message_append.emit(
            session_id=session_id, message=message, task_id=task_id,
        )
        return True

    # ── L5 消息组装 ──────────────────────────────────────

    def build_messages(
        self,
        system_prompt: str,
        session_id: str,
        *,
        step: int,
        task_id: str,
        tool_names: Optional[List[str]] = None,
    ) -> List[ChatMessage]:
        """L5 消息组装：系统提示词与历史合并，两端钩子均可改写。"""
        try:
            modified = self.hooks.before_build_messages.intercept(
                system_prompt=system_prompt, session_id=session_id,
                step=step, task_id=task_id,
                tool_names=list(tool_names or []),
            )
        except HookVeto:
            modified = None
        if isinstance(modified, str):
            system_prompt = modified
        elif isinstance(modified, dict) and isinstance(modified.get("system_prompt"), str):
            system_prompt = modified["system_prompt"]

        history = list(self.session_manager.history(session_id))
        messages = (
            [ChatMessage(role="system", content=system_prompt)] + history
            if system_prompt else history
        )
        try:
            modified2 = self.hooks.after_build_messages.intercept(
                messages=messages, system_prompt=system_prompt,
                step=step, task_id=task_id,
            )
        except HookVeto:
            modified2 = None
        if isinstance(modified2, list):
            messages = modified2
        return messages

    # ── L7 模型调用 ──────────────────────────────────────

    def call_model(
        self,
        model_provider: Any,
        history: List[ChatMessage],
        tool_schemas: List[Dict[str, Any]],
        params: Dict[str, Any],
        task_id: str,
        result: RunResult,
        step: int,
    ):
        """L7 模型调用：before/after_model_call 钩子 + call_timeout 硬中断。"""
        try:
            modified = self.hooks.before_model_call.intercept(
                task_id=task_id, step=step,
                messages=history, tool_schemas=tool_schemas, params=params,
            )
        except HookVeto as veto:
            raise veto  # 主循环捕获后按 stopped 收尾
        if isinstance(modified, dict):
            if isinstance(modified.get("messages"), list):
                history = modified["messages"]
            if isinstance(modified.get("params"), dict):
                params = modified["params"]

        output = self._call_model_with_timeout(
            model_provider, history, tool_schemas, params, task_id, result, step
        )

        try:
            modified2 = self.hooks.after_model_call.intercept(
                task_id=task_id, step=step, output=output,
            )
        except HookVeto:
            modified2 = None
        if modified2 is not None and hasattr(modified2, "content"):
            output = modified2
        return output

    def _call_model_with_timeout(
        self,
        model_provider: Any,
        history: List[ChatMessage],
        tool_schemas: List[Dict[str, Any]],
        params: Dict[str, Any],
        task_id: str,
        result: RunResult,
        step: int,
    ):
        """call_timeout 阻塞硬中断调度（详见 ModelCallTimeout）。"""
        call_timeout = float(params.get("call_timeout", 0) or 0)
        if call_timeout <= 0:
            # 无超时要求：同步调用（零线程开销）。
            # 取消事件（Ctrl+C / 引擎停止）仍然生效：模型流式循环
            # 检查 _cancel_event 后尽快退出，不等 SDK 超时兜底。
            cancel_ev = params.get("_cancel_event")
            return self._call_model_impl(
                model_provider, history, tool_schemas, params, task_id, result,
                cancel_ev if isinstance(cancel_ev, threading.Event) else None,
            )

        cancel_event = threading.Event()
        call_params = dict(params)
        call_params["_cancel_event"] = cancel_event
        box: Dict[str, Any] = {}

        def worker() -> None:
            try:
                box["output"] = self._call_model_impl(
                    model_provider, history, tool_schemas, call_params,
                    task_id, result, cancel_event,
                )
            except Exception as exc:  # noqa: BLE001 — 异常传回主线程
                box["error"] = exc

        thread = threading.Thread(
            target=worker, daemon=True, name=f"norpagent-model-{task_id[:8]}"
        )
        thread.start()
        thread.join(call_timeout)
        if thread.is_alive():
            cancel_event.set()
            self._orphan_threads.append(thread)
            raise ModelCallTimeout(call_timeout)
        if "error" in box:
            raise box["error"]
        return box["output"]

    def _call_model_impl(
        self,
        model_provider: Any,
        history: List[ChatMessage],
        tool_schemas: List[Dict[str, Any]],
        params: Dict[str, Any],
        task_id: str,
        result: RunResult,
        cancel_event: Optional[threading.Event],
    ):
        """模型调用的实际执行体（优先流式；聚合增量并逐段广播）。

        ``cancel_event`` 非 None 时，被置位即停止产出（不再 emit 事件），
        使超时后的后台线程尽快静默退出，避免孤儿输出污染 UI。
        """

        def _abandoned() -> bool:
            return cancel_event is not None and cancel_event.is_set()

        from norpagent.protocols.model import ModelOutput

        stream = getattr(model_provider, "stream", None)
        if stream is not None:
            content_parts: List[str] = []
            reasoning_parts: List[str] = []
            has_reasoning = False
            tool_map: Dict[str, ToolCallSpec] = {}
            usage = ModelUsage()
            finish = ""
            for chunk in stream(history, tool_schemas or None, params):
                if _abandoned():
                    return ModelOutput()
                if chunk.has_reasoning:
                    has_reasoning = True
                if chunk.reasoning:
                    reasoning_parts.append(chunk.reasoning)
                    self.hooks.on_reasoning.emit(
                        task_id=task_id, content=chunk.reasoning, stream=True,
                    )
                if chunk.delta_content:
                    content_parts.append(chunk.delta_content)
                    self.hooks.on_content.emit(
                        task_id=task_id, content=chunk.delta_content,
                        stream=True, final=False,
                    )
                if chunk.tool_call_delta:
                    tool_map[chunk.tool_call_delta.id] = chunk.tool_call_delta
                if chunk.usage:
                    usage = chunk.usage
                if chunk.finish_reason:
                    finish = chunk.finish_reason
            if _abandoned():
                return ModelOutput()
            self._accumulate_usage(result, usage, task_id)
            return ModelOutput(
                content="".join(content_parts),
                reasoning="".join(reasoning_parts),
                has_reasoning=has_reasoning,
                tool_calls=list(tool_map.values()) if tool_map else None,
                usage=usage,
                finish_reason=finish or ("tool_calls" if tool_map else "stop"),
            )
        output = model_provider.generate(history, tool_schemas or None, params)
        if _abandoned():
            return ModelOutput()
        if getattr(output, "reasoning", ""):
            # 非流式输出：整段思维链作为一次增量广播
            self.hooks.on_reasoning.emit(
                task_id=task_id, content=output.reasoning, stream=False,
            )
        self._accumulate_usage(result, output.usage, task_id)
        return output

    def _accumulate_usage(self, result: RunResult, usage: Any, task_id: str) -> None:
        if not usage:
            return
        result.usage.input_tokens += usage.input_tokens or 0
        result.usage.output_tokens += usage.output_tokens or 0
        result.usage.total_tokens += usage.total_tokens or (
            usage.input_tokens or 0
        ) + (usage.output_tokens or 0)
        self.hooks.on_usage_update.emit(
            task_id=task_id,
            input=result.usage.input_tokens,
            output=result.usage.output_tokens,
            total=result.usage.total_tokens,
        )

    # ── L8 工具调用 ──────────────────────────────────────

    def execute_tool_call(
        self, spec: ToolCallSpec, ctx: RunContext, task_id: str
    ) -> ToolResult:
        """L8 工具调用：参数改写 / 否决 / 审批 / 执行 / 结果改写。

        所有路径（包括被阻止、被否决、被审批拒绝）统一流经
        after_tool_call 钩子——「执行结构」无论结果如何都过钩子。
        """
        self.hooks.before_tool_call.emit(
            task_id=task_id,
            tool_name=spec.name, args=spec.arguments, context=ctx,
        )
        result: Optional[ToolResult] = None

        # 可变钩子：修改参数（dict）、阻止调用（False）、一票否决（HookVeto）
        try:
            modified = self.hooks.before_tool_call.intercept(
                task_id=task_id,
                tool_name=spec.name, args=spec.arguments, context=ctx,
            )
        except HookVeto as veto:
            result = ToolResult(
                output=f"工具调用被钩子否决: {veto.reason}",
                success=False,
                error=f"blocked_by_hook: {veto.reason}",
            )
        if result is None:
            if modified is False:
                result = ToolResult(
                    output="工具调用被插件钩子（before_tool_call）阻止。",
                    success=False,
                    error="blocked_by_hook",
                )
            elif isinstance(modified, dict):
                spec.arguments = modified

        # 人工审批（params 显式策略优先，其次 registry.security）
        if result is None:
            result = self._check_approval(spec, ctx)

        if result is None:
            try:
                tool = self.registry.resolve_tool(spec.name)
                result = tool.run(spec.arguments or {}, ctx)
                if not isinstance(result, ToolResult):
                    result = ToolResult(output=str(result))
            except Exception as exc:  # noqa: BLE001
                result = tool_error(spec.name, exc)
                self.hooks.on_tool_error.emit(
                    task_id=task_id, tool_name=spec.name,
                    error=str(exc), args=spec.arguments,
                )

        # 可变钩子：外部插件可改写工具结果（str / ToolResult 生效）
        try:
            modified_result = self.hooks.after_tool_call.intercept(
                task_id=task_id,
                tool_name=spec.name, args=spec.arguments,
                result=result, success=result.success, context=ctx,
            )
        except HookVeto:
            modified_result = None
        if isinstance(modified_result, ToolResult):
            result = modified_result
        elif isinstance(modified_result, str) and modified_result != str(result):
            result = ToolResult(output=modified_result, success=result.success)

        self.hooks.after_tool_call.emit(
            task_id=task_id,
            tool_name=spec.name, args=spec.arguments,
            result=result, success=result.success, context=ctx,
        )
        return result

    def _check_approval(self, spec: ToolCallSpec, ctx: RunContext) -> Any:
        """人工审批拦截（未配置返回 None = 放行）。

        审批策略来源（优先级从高到低）：
        params["approval_policy"] / params["approval_config"]
        → registry.security（norpagent.safe() 安装）。
        审批通过 UI 的 ask_user 完成；用户否定或 UI 无交互
        （返回 default 非肯定值）时阻止调用。
        """
        policy = ctx.params.get("approval_policy")
        if policy is None and isinstance(ctx.params.get("approval_config"), dict):
            from norpagent.security.approval import ApprovalPolicy

            policy = ApprovalPolicy(ctx.params["approval_config"])
        if policy is None:
            security = getattr(self.registry, "security", None)
            approval_config = getattr(security, "approval_config", None) \
                if security is not None else None
            if isinstance(approval_config, dict):
                from norpagent.security.approval import ApprovalPolicy

                policy = ApprovalPolicy(approval_config)
        if policy is None:
            return None
        try:
            requires, level = policy.requires_approval(spec.name, is_plugin=False)
        except Exception:
            return None
        if not requires:
            return None
        answer = ctx.ask_user(
            f"工具 {spec.name} 调用需要人工审批（级别 {level.value}），是否继续？"
            f"\n参数: {spec.arguments}\n[y/n]",
            default="n",
        ).strip().lower()
        if answer in ("y", "yes", "是", "确认", "ok"):
            return None
        return ToolResult(
            output=f"用户拒绝了工具 {spec.name} 的审批请求，调用已取消。",
            success=False,
            error="approval_denied",
        )

    # ── L9 结果定型 ──────────────────────────────────────

    def finalize_result(self, result: RunResult, task_id: str) -> RunResult:
        """L9 结果定型：before_result / after_result 可变钩子。"""
        try:
            modified = self.hooks.before_result.intercept(
                task_id=task_id, result=result,
            )
            if isinstance(modified, RunResult):
                result = modified
        except HookVeto:
            pass
        try:
            modified2 = self.hooks.after_result.intercept(
                task_id=task_id, result=result,
            )
            if isinstance(modified2, RunResult):
                result = modified2
        except HookVeto:
            pass
        return result

    # ══════════════════════════════════════════════════════
    #  任务协作 / 多智能体编排
    # ══════════════════════════════════════════════════════

    def run_task(self, task: Any) -> Any:
        """执行一个 AgentTask（调度器 drain 回调的标准实现）。

        长周期任务协作与多智能体编排的统一入口：

        - 调度器（simple / persistent）把 AgentTask 交给此方法执行；
        - 子任务可通过 ``task.preset_name`` 指定不同模式（子 Agent），
          与父任务共享同一注册表 / 会话存储 / 调度器；
        - 任何异常都转成 TaskResult，不向调度器冒泡。
        """
        from norpagent.protocols.scheduler import TaskResult

        try:
            preset_name = task.preset_name or self.preset.name
            if preset_name != self.preset.name:
                # 子任务指定了不同模式：以同一注册表派生子运行时执行
                # （多智能体编排：子 Agent = 不同预设 + 共享组件仓库）
                child = AgentRuntime(
                    self.registry,
                    preset_name,
                    session_manager=self.session_manager,
                    scheduler=self.scheduler,
                    ui=self.ui,
                    task_params=self.params,
                )
                try:
                    run_result = child.run(
                        task.user_input,
                        session_id=task.session_id,
                        task_id=task.id,
                        task_params=task.params,
                    )
                finally:
                    child.shutdown()
            else:
                run_result = self.run(
                    task.user_input,
                    session_id=task.session_id,
                    task_id=task.id,
                    task_params=task.params,
                )
            status = "done" if run_result.ok else run_result.status
            return TaskResult(
                task_id=task.id, status=status,
                error=run_result.error, run_result=run_result,
            )
        except Exception as exc:  # noqa: BLE001
            return TaskResult(task_id=task.id, status="failed", error=str(exc))

    def task_runner(self) -> Any:
        """返回适合传给 ``scheduler.drain`` 的回调（以本运行时为执行器）。"""
        return self.run_task

    # ── 兼容别名（P1/P2 私有方法名，老签名兼容）──────────

    def _build_messages(self, system_prompt: str, session_id: str,
                        step: int = 0, task_id: str = "") -> List[ChatMessage]:
        return self.build_messages(
            system_prompt, session_id, step=step, task_id=task_id,
            tool_names=list(self.preset.tools),
        )

    def _call_model(self, model_provider: Any, history: List[ChatMessage],
                    tool_schemas: List[Dict[str, Any]], params: Dict[str, Any],
                    task_id: str, result: RunResult, step: int = 0):
        return self.call_model(
            model_provider, history, tool_schemas, params, task_id, result, step,
        )

    def _execute_tool_call(self, spec: ToolCallSpec, ctx: RunContext,
                           task_id: str) -> ToolResult:
        return self.execute_tool_call(spec, ctx, task_id)

    def shutdown(self) -> None:
        """关闭运行时：回收硬超时残留线程、广播关闭事件、释放沙箱与组件。"""
        try:
            if self.sandbox is not None and hasattr(self.sandbox, "close"):
                self.sandbox.close()
        finally:
            if self._ui_listener is not None:
                try:
                    self.bus.unsubscribe(self._ui_listener)
                except Exception:
                    pass
                self._ui_listener = None
            for component in self.components.values():
                closer = getattr(component, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        pass
            for thread in self._orphan_threads:
                if thread.is_alive():
                    thread.join(0.5)  # 给后台线程短促的自退窗口（daemon，不阻塞退出）
            self._orphan_threads.clear()
            self.hooks.on_agent_shutdown.emit(preset=self.preset.name)


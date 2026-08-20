# Vibe Coding Agent - 异步Agent循环 (Async Agent Loop)
# 从同步架构重构为异步架构
# Copyright (c) 2026 xingluosama

import nasync_io
import json
import os
import re
import time
import threading
from typing import List, Dict, Optional

from openai import OpenAI
from anthropic import Anthropic as AnthropicClient

from event_queue import EventQueue
from async_executor import AsyncToolExecutor
from debug_logger import get_debug_logger
from lifecycle_manager import LifecycleManager, TaskLifecycle, get_lifecycle_manager
from sandbox_pool import get_sandbox_pool
from file_io_queue import get_file_io_queue
from agent_shared import (
    build_system_prompt,
    build_full_messages,
    build_tools_openai,
    build_tools_anthropic,
    build_responses_tools,
    build_responses_input,
    get_thinking_extra_body,
    get_reasoning_effort,
    convert_openai_messages_to_anthropic,
    is_loopback_url,
    plugin_has_tool,
)
from plugin_system.approval import ApprovalPolicy

CHARS_PER_TOKEN = 3

# 本地部署模式（Ollama 等）的上下文窗口上限与保底值
LOCAL_NUM_CTX_MAX = 65536
LOCAL_NUM_CTX_MIN = 8192

# ★ 僵尸进程防护：单次 API 请求最大等待时间（秒）
# 超过此时间仍未收到任何 chunk，视为网络挂起，触发超时退出
# 默认 180s（3 分钟）；可由用户在 30s ~ 3600s（60 分钟）之间配置
API_REQUEST_TIMEOUT_DEFAULT = 180.0


def format_api_timeout(seconds: float) -> str:
    """将 API 超时秒数格式化为可读字符串（如 "3 min" / "30s"）。"""
    t = int(seconds)
    if t >= 60 and t % 60 == 0:
        return f"{t // 60} min"
    return f"{t}s"


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


class AsyncAgentLoop:
    """异步 Agent 循环。

    关键改动（vs 同步版 AgentLoop）：
    1. run() 改为 async，内部所有 I/O 操作异步化
    2. 工具执行通过 AsyncToolExecutor（集成沙箱池/文件IO队列等）
    3. 生命周期绑定：任务启动/停止通过 LifecycleManager 管理进程组
    4. 停止机制：使用 nasync_io.Event 替代 threading.Event
    5. API 调用在线程池中执行（OpenAI SDK 是同步的），避免阻塞事件循环
    """

    def __init__(
        self,
        api_key: str,
        project_root: str,
        event_queue: EventQueue,
        app_dir: str = "",
        model: str = "deepseek-v4-pro",
        base_url: str = "https://api.deepseek.com",
        max_steps: int = 128,
        enable_web_search: bool = False,
        confirm_write_delete: bool = True,
        approval_config: dict = None,
        temperature: float = 1.0,
        think_level: str = "高",
        max_tokens: int = 32767,
        task_timeout: int = 0,
        api_request_timeout: float = 180.0,
        plugin_manager=None,
        use_responses_api: bool = False,
        allow_full_read_large_files: bool = False,
        custom_system_prompt: str = "",
    ):
        self.api_key = api_key
        self.use_responses_api = use_responses_api
        self.base_url = base_url
        self.project_root = project_root
        self.app_dir = app_dir
        self.model = model
        self.max_steps = max_steps
        self.enable_web_search = enable_web_search
        self.confirm_write_delete = confirm_write_delete
        # ★ P0-8 人工审批安全层：分级审批策略（write / delete / exec）
        self.approval = ApprovalPolicy(approval_config)
        self.temperature = temperature
        self.think_level = think_level
        self.max_tokens = max_tokens
        self.task_timeout = task_timeout
        self.api_request_timeout = api_request_timeout
        self.custom_system_prompt = custom_system_prompt

        # 异步事件
        self._stop_event = nasync_io.Event()
        self._user_reply_event = nasync_io.Event()
        self._user_reply_value = ""
        # ★ 停止竞态防护：stop() 可能在 run() 协程启动前被调用（用户极快
        # 点击停止），此标记保证 run() 不把已置位的 stop_event 清掉。
        self._stop_requested = False

        # Token 统计
        self._last_usage = None
        self._total_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_call_tokens": 0,
        }
        self._step_count = 0

        # 对话历史
        self._conversation_history = []
        self._messages = []
        self._memory_content = ""

        # 计时
        self._task_start_time = 0.0
        self._pause_start_time = 0.0
        self._total_pause_duration = 0.0

        # 日志路径
        if app_dir:
            self.tool_log_path = os.path.join(app_dir, "tool_calls.jsonl")
        else:
            self.tool_log_path = ""

        # 生命周期管理器
        self.lifecycle_manager = get_lifecycle_manager()
        self._task_lifecycle: Optional[TaskLifecycle] = None

        # 事件循环引用（线程安全停止用）
        self._agent_loop: Optional[nasync_io.AbstractEventLoop] = None

        # ── 本地部署模式检测 ──
        # API Base URL 指向本机回环地址（localhost / 127.x / ::1）时，
        # 判定为本地部署模式（Ollama / LM Studio / vLLM 等本地模型服务），
        # 自动启用针对本地推理的系列优化（见 _call_openai_stream）。
        self._is_local_mode = is_loopback_url(base_url)

        # DeepSeek 官方端点检测
        self._is_deepseek_official = (
            base_url.rstrip('/') == "https://api.deepseek.com"
            and not self._is_local_mode
        )

        # OpenAI 官方端点检测
        self._is_openai_official = (
            base_url.rstrip('/') == "https://api.openai.com"
            and not self._is_local_mode
        )

        # Responses API：仅 OpenAI 端点；本地模式强制关闭
        # （本地服务几乎都只兼容 OpenAI Chat Completions 协议）
        self._use_responses_api = (
            use_responses_api
            and self._is_openai_official
        )

        # Anthropic 兼容搜索：仅 DeepSeek 官方端点；本地模式强制关闭
        self._use_anthropic_search = (
            enable_web_search
            and not self._is_local_mode
            and base_url in ("https://api.deepseek.com", "https://api.deepseek.com/")
            and not self._use_responses_api
        )

        # Ollama 特有探测：本地模式下通过 /api/tags 端点识别（Ollama 独有）
        self._is_ollama = self._is_local_mode and self._detect_ollama(base_url, api_key)

        # 本地部署模式优化：
        # 1. API Key 占位 — Ollama 等本地服务无需认证，但 OpenAI SDK 要求非空
        # 2. 模型自动匹配 — 配置的云端模型名在本地不存在时，自动选择第一个可用模型
        if self._is_local_mode:
            if not api_key:
                api_key = "ollama"  # 本地服务占位 key，不参与真实鉴权
            self._resolve_local_model()

        # OpenAI Client（在线程池中调用）
        self.client = OpenAI(api_key=api_key, base_url=base_url)

        if self._use_anthropic_search:
            self.anthropic_client = AnthropicClient(
                api_key=api_key,
                base_url="https://api.deepseek.com/anthropic"
            )
        else:
            self.anthropic_client = None

        # 异步工具执行器
        self.executor = AsyncToolExecutor(
            project_root=project_root,
            app_dir=app_dir,
            task_id=f"task_{id(self)}",
            allow_full_read_large_files=allow_full_read_large_files,
        )

        # 事件队列
        self.event_queue = event_queue

        # 调试收集器（延迟到 run() 时初始化，避免 app_dir 未就绪）
        self._debug_logger = None

        # 插件
        self.plugin_manager = plugin_manager

    # ═══════════════════════════════════════════════════════════════
    #  本地部署模式：Ollama 探测 / 模型自动匹配
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _detect_ollama(base_url: str, api_key: str) -> bool:
        """探测目标是否为 Ollama 服务。

        Ollama 的 OpenAI 兼容层暴露独有的 /api/tags 端点（返回 {"models": [...]}），
        而 LM Studio / vLLM / llama.cpp 等均无此端点，可用于可靠识别。
        探测失败（服务未启动 / 超时）时静默返回 False，不影响主流程。
        """
        if not base_url:
            return False
        try:
            import requests
            url = base_url.rstrip("/") + "/api/tags"
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = requests.get(url, headers=headers, timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                return isinstance(data, dict) and "models" in data
        except Exception:
            pass
        return False

    def _resolve_local_model(self):
        """本地模式下自动匹配可用模型。

        若配置的模型名（如 deepseek-v4-pro）不在本地服务的模型列表中，
        自动切换为第一个可用模型，避免 Chat Completions 报 model not found。

        对于非 Ollama 本地服务（Qwen / llama.cpp / vLLM 等），如果 /v1/models
        端点不可用，尝试智能回退以避免 400 错误。
        """
        try:
            models = self.client.models.list()
            local_ids = [m.id for m in getattr(models, "data", [])]
            if local_ids:
                if self.model in local_ids:
                    return
                # 配置的模型不存在 → 自动选择第一个可用模型
                fallback = local_ids[0]
                print(f"[LocalMode] model '{self.model}' not found locally, "
                      f"fallback to '{fallback}' (available: {local_ids[:5]})")
                self.model = fallback
                return
            # models.data 为空 — 非 Ollama 本地服务可能不支持 /v1/models
            # 尝试智能回退：如果模型名明显是云端专属（deepseek / gpt / claude），
            # 替换为常见本地模型名，避免 400 model not found
            _cloud_prefixes = ("deepseek", "gpt-", "claude", "o1", "o3", "gemini")
            if self._is_local_mode and not self._is_ollama:
                if any(self.model.lower().startswith(p) for p in _cloud_prefixes):
                    _fallbacks = ["gpt-3.5-turbo", "qwen", "local-model", "default"]
                    for fb in _fallbacks:
                        if fb not in (self.model,):
                            print(f"[LocalMode] /v1/models unavailable, "
                                  f"fallback model '{self.model}' → '{fb}' "
                                  f"(set correct model name in Settings if needed)")
                            self.model = fb
                            return
        except Exception:
            # /v1/models 调用异常 — 同上，尝试智能回退
            _cloud_prefixes = ("deepseek", "gpt-", "claude", "o1", "o3", "gemini")
            if self._is_local_mode and not self._is_ollama:
                if any(self.model.lower().startswith(p) for p in _cloud_prefixes):
                    _fallbacks = ["gpt-3.5-turbo", "qwen", "local-model", "default"]
                    for fb in _fallbacks:
                        if fb not in (self.model,):
                            print(f"[LocalMode] /v1/models error, "
                                  f"fallback model '{self.model}' → '{fb}'")
                            self.model = fb
                            return
            # 其他异常静默降级：保持原模型配置

    def get_local_mode_info(self) -> dict:
        """返回本地部署模式信息（供 API 层 / 前端展示）。"""
        return {
            "local_mode": self._is_local_mode,
            "is_ollama": self._is_ollama,
            "model": self.model,
            "base_url": self.base_url,
        }

    # ═══════════════════════════════════════════════════════════════
    #  停止 / 用户交互
    # ═══════════════════════════════════════════════════════════════

    def stop(self):
        """停止任务（同步接口，供 API 层调用）。

        可从任意线程调用（pywebview JS 桥接线程）。

        ★ 即时停止（自研架构核心改进）：
        标准 asyncio 没有从外部线程强制取消主任务的入口，停止只能等
        当前 await 自然结束（工具最长 300s / 插件 120s / API 180s），
        表现为「点停止后迟迟不停」。自研 EventLoop.abort_main() 直接
        向主协程注入 CancelledError，立即中断当前 await，毫秒级退出。

        停止分四层（从快到慢，层层兜底）：
        1. abort_main()          — 取消主任务，立即展开协程栈
        2. call_soon_threadsafe  — 置位 stop/user_reply 事件，唤醒等待中的协程
        3. 关闭 HTTP 传输层     — 中断线程池中阻塞的流式请求
        4. 生命周期杀进程组     — 终止 exec_cmd 等子进程树

        ★ 死锁修复：nasync_io.Event 不是线程安全的。
        从外部线程直接 set() 时，Future.set_result 内部走 loop.call_soon
        （非 call_soon_threadsafe），不会写入自管道唤醒信号——
        若事件循环正阻塞在 selector.select() 上（例如 agent 正在
        _wait_for_user_input() 中 await），回调会滞留在 _ready 队列
        无人处理，导致 wait() 永久挂起（表现为"一直等待回复"）。
        必须通过 call_soon_threadsafe 把 set() 调度到 agent 事件循环线程，
        借助自管道唤醒机制立即生效。
        """
        # 停止请求标记：防 stop 与 run 启动竞态（run 不清理已置位的事件）
        self._stop_requested = True

        # 第 2 层：事件置位（循环线程内执行，自管道唤醒）
        if self._agent_loop and not self._agent_loop.is_closed():
            # 调度到事件循环线程执行 set()，唤醒阻塞中的协程
            self._agent_loop.call_soon_threadsafe(self._stop_event.set)
            self._agent_loop.call_soon_threadsafe(self._user_reply_event.set)
        else:
            self._stop_event.set()
            self._user_reply_event.set()

        # 第 1 层：即时取消主任务（若事件循环已启动）
        # 置位事件后仍要取消主任务：事件只影响「检查点」，取消才能打断
        # 正在 await 的工具/API 操作。
        if self._agent_loop and not self._agent_loop.is_closed():
            try:
                self._agent_loop.abort_main()
            except Exception:
                pass

        # 第 3 层：关闭 HTTP 客户端传输层
        # OpenAI SDK 底层使用 httpx.Client，关闭其传输层会中断所有进行中的请求
        # 这能让阻塞在 _sync_stream 线程中的 HTTP 流式读取立即抛出异常
        try:
            if hasattr(self.client, 'close'):
                self.client.close()
        except Exception:
            pass
        try:
            if hasattr(self, 'anthropic_client') and self.anthropic_client:
                if hasattr(self.anthropic_client, 'close'):
                    self.anthropic_client.close()
        except Exception:
            pass

        # 第 4 层：生命周期杀进程组
        if self._task_lifecycle:
            self.lifecycle_manager.stop_task(
                self._task_lifecycle.task_id, reason="user_stop"
            )
        # 线程安全：将清理调度到 agent 专属事件循环
        if self._agent_loop and not self._agent_loop.is_closed():
            self._agent_loop.call_soon_threadsafe(
                lambda: nasync_io.ensure_future(self.executor.cleanup())
            )
        # Plugin cleanup (fire shutdown hooks + reap zombie threads)
        if self.plugin_manager:
            try:
                self.plugin_manager.fire_agent_shutdown()
            except Exception:
                pass
            try:
                self.plugin_manager.shutdown()
            except Exception:
                pass

    def provide_user_input(self, text: str):
        """提供用户输入（线程安全，可从任意线程调用）。

        ★ 死锁修复：不能在外部线程直接调用 nasync_io.Event.set()。
        事件循环阻塞在 selector 上时收不到唤醒信号，_wait_for_user_input()
        会挂起至 30 分钟硬超时。必须通过 call_soon_threadsafe 调度到
        agent 事件循环线程执行（先写值、再 set，保证读取到的必是新值）。
        """
        if self._agent_loop and not self._agent_loop.is_closed():
            self._agent_loop.call_soon_threadsafe(
                self._set_user_reply, text
            )
        else:
            self._user_reply_value = text
            self._user_reply_event.set()

    def _set_user_reply(self, text: str):
        """在 agent 事件循环线程内设置用户回复（仅由 call_soon_threadsafe 调用）。

        先写 _user_reply_value 再 set 事件，确保 _wait_for_user_input()
        被唤醒后读取到的必然是最新的用户输入。
        """
        self._user_reply_value = text
        self._user_reply_event.set()

    async def _wait_for_user_input(self) -> str:
        """异步等待用户输入。

        防御措施：
        1. 将任务状态切换为 WAITING_USER，防止僵尸扫描器误杀
        2. 暂停超时计时器，用户思考时间不计入任务超时
        3. try/except 包裹所有生命周期操作，防止静默失败
        4. 30 分钟硬超时兜底：即使用户不响应也不会永久挂起
        """
        self._pause_timer()
        self._user_reply_event.clear()

        # ── 生命周期：标记为等待用户，暂停超时 ──
        if self._task_lifecycle:
            try:
                self.lifecycle_manager.set_waiting_user(self._task_lifecycle.task_id)
                self.lifecycle_manager.pause_timeout(self._task_lifecycle.task_id)
            except Exception:
                pass  # 防御：即使状态更新失败也不影响主流程

        try:
            # 30分钟硬超时：防止前端崩溃导致永久挂起
            await nasync_io.wait_for(
                self._user_reply_event.wait(),
                timeout=1800.0  # 30 minutes
            )
        except nasync_io.TimeoutError:
            # 用户交互超时：视为停止任务
            self._stop_event.set()
            if self._task_lifecycle:
                try:
                    self.lifecycle_manager.timeout_task(self._task_lifecycle.task_id)
                except Exception:
                    pass
            # ★ 关闭 HTTP 传输层，确保后续 stop 能立即生效
            try:
                if hasattr(self.client, 'close'):
                    self.client.close()
            except Exception:
                pass
            try:
                if hasattr(self, 'anthropic_client') and self.anthropic_client:
                    if hasattr(self.anthropic_client, 'close'):
                        self.anthropic_client.close()
            except Exception:
                pass
            self.event_queue.put("E:User interaction timeout (30 min) — task aborted")
            return ""

        # ── 生命周期：恢复运行状态，恢复超时 ──
        if self._task_lifecycle:
            try:
                self.lifecycle_manager.clear_waiting_user(self._task_lifecycle.task_id)
                self.lifecycle_manager.resume_timeout(self._task_lifecycle.task_id)
            except Exception:
                pass

        self._resume_timer()

        if self._stop_event.is_set():
            return ""
        return self._user_reply_value

    async def _confirm_write_delete(self, tool_name: str, tool_args: dict,
                                    is_plugin: bool = False) -> bool:
        """弹出人工审批确认对话框（P0-8 分级审批统一入口）。

        is_plugin 为 True 时表示该工具来自插件系统（插件控制面板审批），
        前端可根据该标记在确认框中展示「插件工具」标识。

        特殊返回值约定：
        - ``__confirm__``        ：确认放行
        - ``__confirm_no_more__``：确认放行 + 持久化关闭「原生工具确认」
          （对应弹窗上的「不再显示」按钮，仅原生工具弹窗可见）
        - 其它任何输入           ：视为取消
        """
        confirm_data = json.dumps({
            "tool": tool_name,
            "path": tool_args.get("path", ""),
            "command": tool_args.get("command", tool_args.get("cmd", "")),
            "is_plugin": is_plugin,
        }, ensure_ascii=False)
        self.event_queue.put(f"WC:{confirm_data}")
        reply = await self._wait_for_user_input()
        if self._stop_event.is_set():
            return False
        reply = reply.strip()
        if reply == "__confirm_no_more__":
            # 「不再显示」：本次放行；若是原生工具（非插件），
            # 持久化关闭原生工具确认总开关，后续弹窗不再出现。
            if not is_plugin:
                self._disable_native_confirm()
            return True
        return reply == "__confirm__"

    def _refresh_approval(self):
        """每次确认弹窗前重新读取配置，保证开关即时生效。

        修复：此前 ApprovalPolicy 仅在会话创建时快照一次配置，
        单次对话中修改「原生工具确认 / 插件审批」开关不会生效，
        必须等下一个完整工作流才会重新检测。现在每次检查前都从
        config.json 重读并重建策略，开关改动（含「不再显示」）
        下一次工具调用立即生效。
        """
        try:
            cfg_path = os.path.join(self.app_dir, "config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            else:
                cfg = {}
        except Exception:
            cfg = {}
        try:
            hints = {}
            if self.plugin_manager:
                hints = self.plugin_manager.get_tool_approval_hints()
            self.approval = ApprovalPolicy(cfg, tool_hints=hints)
        except Exception:
            pass

    # ── 视觉 delegate 让渡状态（跨进程共享，主进程只读）──
    _VISION_STATE_FNAME = "vision_state.json"

    def _read_vision_delegate_state(self) -> Optional[dict]:
        """读取视觉外挂插件的让渡状态（app_dir/vision_state.json）。

        状态文件由视觉插件子进程原子写入，主进程审批层在此只读判断：
        delegate 覆盖范围内的 L2 视觉工具调用可跳过审批弹窗
        （用户已通过 vision_delegate 工具主动让渡确认权）。
        读不到文件 / 格式损坏 → 返回 None（按需审批，安全方向不变）。
        """
        try:
            state_path = os.path.join(self.app_dir, self._VISION_STATE_FNAME)
            if not os.path.exists(state_path):
                return None
            # 文件可能正在被插件原子替换，宽松重试一次
            for _ in range(2):
                try:
                    with open(state_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    break
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.05)
            else:
                return None
            delegate = data.get("delegate")
            if not isinstance(delegate, dict):
                return None
            return delegate
        except Exception:
            return None

    def _vision_delegate_covers(self, tool_name: str, tool_args: dict) -> bool:
        """判断某次视觉工具调用是否处于 delegate 让渡免确认范围内。"""
        delegate = self._read_vision_delegate_state()
        if not delegate:
            return False
        try:
            # 有效期
            expires_at = float(delegate.get("expires_at", 0.0))
            if expires_at <= time.time():
                return False
            # 工具风险级（取插件 APPROVAL_HINTS 中声明的 risk）
            hints = {}
            if self.plugin_manager:
                hints = self.plugin_manager.get_tool_approval_hints()
            hint = hints.get(tool_name) or {}
            tool_risk = str(hint.get("risk", "")).upper()
            if not tool_risk or tool_risk not in ("L0", "L1", "L2", "L3"):
                return False
            max_risk = str(delegate.get("max_risk", "L2")).upper()
            if max_risk not in ("L0", "L1", "L2", "L3"):
                return False
            rank = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
            if rank.get(tool_risk, 99) > rank.get(max_risk, 0):
                return False
            # 范围
            scope = str(delegate.get("scope", "window"))
            if scope == "session" or scope == "app":
                return True
            if scope == "window":
                target = str(delegate.get("target_window", "") or "")
                if not target:
                    return False
                hwnd = str(tool_args.get("hwnd", "") or "")
                return hwnd and hwnd == target
            return False
        except Exception:
            return False

    def _disable_native_confirm(self):
        """「不再显示」持久化：关闭原生工具确认总开关（设置面板配置）。"""
        try:
            cfg_path = os.path.join(self.app_dir, "config.json")
            cfg = {}
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["native_confirm_enabled"] = False
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    #  计时
    # ═══════════════════════════════════════════════════════════════

    def _get_elapsed(self) -> float:
        if self._pause_start_time > 0:
            current_pause = time.time() - self._pause_start_time
        else:
            current_pause = 0.0
        return time.time() - self._task_start_time - self._total_pause_duration - current_pause

    def _check_timeout(self) -> bool:
        if self.task_timeout <= 0:
            return False
        return self._get_elapsed() > self.task_timeout

    def _pause_timer(self):
        if self.task_timeout > 0:
            self._pause_start_time = time.time()

    def _resume_timer(self):
        if self.task_timeout > 0 and self._pause_start_time > 0:
            self._total_pause_duration += time.time() - self._pause_start_time
            self._pause_start_time = 0.0

    # ═══════════════════════════════════════════════════════════════
    #  Token 统计
    # ═══════════════════════════════════════════════════════════════

    def get_last_usage(self) -> dict:
        if self._last_usage:
            return self._last_usage.copy()
        return {}

    def get_total_usage(self) -> dict:
        return self._total_usage.copy()

    def get_conversation_history(self) -> list:
        return self._conversation_history.copy()

    def _update_usage(self, input_tokens: int, output_tokens: int):
        self._last_usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens
        }
        self._total_usage["input_tokens"] += input_tokens
        self._total_usage["output_tokens"] += output_tokens
        self._send_usage_event()

    def _add_tool_tokens(self, tool_name: str, tool_result: str):
        est = estimate_tokens(tool_result)
        self._total_usage["tool_call_tokens"] += est
        self._send_usage_event()

    def _send_usage_event(self):
        usage_event = json.dumps({
            "input_tokens": self._total_usage["input_tokens"],
            "output_tokens": self._total_usage["output_tokens"],
            "tool_call_tokens": self._total_usage["tool_call_tokens"]
        }, ensure_ascii=False)
        self.event_queue.put(f"U:{usage_event}")
        # ── 调试收集器：更新 token / 事件队列快照 ──
        try:
            if self._debug_logger:
                self._debug_logger.update_tokens(self._total_usage)
                self._debug_logger.update_event_queue_size(self.event_queue.size)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    #  主循环
    # ═══════════════════════════════════════════════════════════════

    async def run(self, user_message: str, history: Optional[List[Dict]] = None,
                  memory_content: str = "") -> str:
        """异步执行 Agent 主循环（对外入口 + 取消收口）。

        ★ 即时停止收口（自研架构）：
        abort_main() 注入的 CancelledError 可能中断任意深度的 await
        （API 流 / 工具执行 / 用户输入等待），无论从哪一层展开，
        最终都在此统一捕获并返回 "stopped"，同时向事件队列发出
        终止信号，前端轮询立即结束，不留悬挂事件。
        """
        try:
            result = await self._run_impl(user_message, history, memory_content)
        except nasync_io.CancelledError:
            # 用户即时停止：取消注入中断当前 await，统一收口
            self.event_queue.put("E:Task stopped by user")
            self.event_queue.signal_finish()
            result = "stopped"
        except Exception as e:
            result = f"__ERROR__:{str(e)}"
        finally:
            # 生命周期：标记任务完成（正常/异常/取消均走此路径）
            if self._task_lifecycle:
                try:
                    self.lifecycle_manager.stop_task(
                        self._task_lifecycle.task_id, reason="completed"
                    )
                except Exception:
                    pass

        # ── 以下为纯同步收尾（无 await，不会被取消中断）──
        # 构建对话历史
        conv = []
        for m in self._messages[2:]:
            role = m.get("role", "")
            if role == "assistant":
                msg = {"role": "assistant", "content": m.get("content", "")}
                if m.get("reasoning_content"):
                    msg["reasoning_content"] = m["reasoning_content"]
                if m.get("tool_calls"):
                    msg["tool_calls"] = m["tool_calls"]
                if m.get("web_search_calls"):
                    msg["web_search_calls"] = m["web_search_calls"]
                conv.append(msg)
            elif role == "tool":
                conv.append({
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "content": m.get("content", "")
                })
        self._conversation_history = conv

        # ── 调试收集器：任务结束，写入 "{日期+时间}_debug.json" ──
        try:
            if self._debug_logger:
                self._debug_logger.update_tokens(self._total_usage)
                self._debug_logger.update_event_queue_size(self.event_queue.size)
                self._debug_logger.finish_task(result)
        except Exception:
            pass

        return result

    async def _run_impl(self, user_message: str,
                        history: Optional[List[Dict]] = None,
                        memory_content: str = "") -> str:
        """run() 的实际执行体：状态初始化 + 三条 API 路径分派。

        注意：本方法内部不再捕获 CancelledError——取消统一由 run()
        收口。即使取消发生在初始化阶段（run 启动瞬间用户点击停止），
        CancelledError 也会穿透本方法回到 run() 处理。
        """
        self._step_count = 0
        self._last_usage = None
        self._total_usage = {"input_tokens": 0, "output_tokens": 0, "tool_call_tokens": 0}
        self._messages = []
        self._memory_content = memory_content

        self._task_start_time = time.time()
        self._total_pause_duration = 0.0
        self._pause_start_time = 0.0

        # ★ 停止竞态修复：stop() 可能先于本协程启动（用户极快点击停止）。
        # 此时 stop_event 已被置位，若照常 clear() 会吞掉停止请求，
        # 导致任务在已停止状态下继续运行。_stop_requested 标记保证
        # 已置位的停止事件不被清掉，首个循环检查即退出。
        if not self._stop_requested:
            self._stop_event.clear()

        # ── 调试收集器：开始记录任务 ──
        try:
            self._debug_logger = get_debug_logger(self.app_dir)
            self._debug_logger.start_task(
                task_id=getattr(self.executor, "task_id", ""),
                user_message=user_message,
                project_root=self.project_root,
            )
            self._debug_logger.update_tokens(self._total_usage)
            self._debug_logger.update_event_queue_size(self.event_queue.size)
        except Exception:
            self._debug_logger = None

        # 记录事件循环引用，供 stop() 线程安全调度
        self._agent_loop = nasync_io.get_running_loop()

        # 生命周期：创建任务
        self._task_lifecycle = self.lifecycle_manager.create_task(
            task_id=f"agent_{id(self)}_{int(time.time())}",
            timeout=self.task_timeout,
        )
        self.lifecycle_manager.start_task(self._task_lifecycle.task_id)

        if self._use_responses_api:
            return await self._run_responses(user_message, history)
        elif self._use_anthropic_search:
            return await self._run_anthropic(user_message, history)
        return await self._run_openai(user_message, history)

    # ═══════════════════════════════════════════════════════════════
    #  API 调用（在线程池中执行同步 OpenAI SDK）
    # ═══════════════════════════════════════════════════════════════

    async def _run_openai(self, user_message: str,
                          history: Optional[List[Dict]] = None) -> str:
        """异步版 OpenAI Chat Completions 路径。"""
        self.event_queue.reset()

        messages = self._build_full_messages(user_message, history,
                                             memory_content=self._memory_content)
        self._messages = messages

        active_tools = self._build_tools_openai()
        thinking_extra_body = self._get_thinking_extra_body()
        reasoning_effort = self._get_reasoning_effort()

        for step in range(self.max_steps):
            if self._stop_event.is_set():
                self.event_queue.put("E:Task stopped by user")
                self.event_queue.signal_finish()
                return "stopped"

            if self._check_timeout():
                elapsed = int(self._get_elapsed())
                self.event_queue.put(f"E:Task timeout after {elapsed}s (limit: {self.task_timeout}s)")
                self.event_queue.signal_finish()
                return "timeout"

            self._step_count = step + 1

            # ── 调试收集器：标记当前步骤 ──
            try:
                if self._debug_logger:
                    self._debug_logger.set_current_step(self._step_count)
            except Exception:
                pass

            # ── Hook: before_step（与同步版对齐，供 context_retriever 注入索引状态）──
            if self.plugin_manager:
                messages = self.plugin_manager.fire_before_step(
                    self._step_count, messages)

            # 在线程池中调用同步 API
            result = await self._call_openai_stream(
                messages=messages,
                tools=active_tools,
                thinking_extra_body=thinking_extra_body,
                reasoning_effort=reasoning_effort,
            )

            if result is None:
                return "stopped"

            full_reasoning = result["reasoning"]
            full_content = result["content"]
            tool_calls_list = result["tool_calls"]
            stream_usage = result.get("usage")

            if stream_usage:
                self._update_usage(stream_usage["input_tokens"], stream_usage["output_tokens"])
            else:
                estimated_input = sum(len(str(m.get("content", ""))) for m in messages) // 4
                estimated_output = len(full_content) // 4
                self._update_usage(estimated_input, estimated_output)

            assistant_msg = {"role": "assistant", "content": full_content}
            if full_reasoning:
                assistant_msg["reasoning_content"] = full_reasoning

            if tool_calls_list:
                assistant_msg["tool_calls"] = tool_calls_list
                for tc in tool_calls_list:
                    cmd_info = json.dumps({
                        "tool": tc["function"]["name"],
                        "args": json.loads(tc["function"]["arguments"])
                    }, ensure_ascii=False)
                    self.event_queue.put(f"C:{cmd_info}")

            messages.append(assistant_msg)

            # ── 调试收集器：记录 ReAct 步骤（思考 + 行动，观察稍后追加）──
            try:
                if self._debug_logger:
                    tc_formatted = [
                        {"name": tc["function"]["name"],
                         "arguments": tc["function"]["arguments"]}
                        for tc in tool_calls_list
                    ]
                    self._debug_logger.record_react_step(
                        step=self._step_count,
                        reasoning=full_reasoning,
                        tool_calls=tc_formatted,
                        observations=[],
                    )
            except Exception:
                pass

            if not tool_calls_list:
                if full_content:
                    self.event_queue.put(f"D:{full_content}")
                self.event_queue.signal_finish()
                return full_content

            status = await self._process_tool_calls_async(messages, tool_calls_list, step)
            if status == "stopped":
                return "stopped"

        self.event_queue.put("E:Max steps reached, task incomplete")
        self.event_queue.signal_finish()
        return "max_steps"

    async def _call_openai_stream(self, messages: list, tools: list,
                                  thinking_extra_body: dict,
                                  reasoning_effort: Optional[str]) -> Optional[dict]:
        """在线程池中调用 OpenAI 流式 API。

        本地部署模式（_is_local_mode）下自动执行以下优化：
        1. 移除 DeepSeek 专属参数（thinking extra_body / reasoning_effort），
           本地服务（Ollama / LM Studio / vLLM）不识别这些字段；
        2. temperature 始终生效（本地服务无 thinking 约束）；
        3. Ollama 专用：keep_alive 保持模型常驻内存 + options.num_ctx
           扩展上下文窗口，max_tokens 收敛到上下文范围内；
        4. 不发送 stream_options.include_usage（部分本地服务不支持）。
        """
        api_params = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": True,
            "max_tokens": self.max_tokens,
        }
        if not self._is_local_mode:
            # 云端模式：DeepSeek 官方端点才支持 usage 统计
            if self._is_deepseek_official:
                api_params["stream_options"] = {"include_usage": True}
            if reasoning_effort is not None:
                api_params["reasoning_effort"] = reasoning_effort
            api_params["extra_body"] = thinking_extra_body
        else:
            # ── 本地部署模式优化 ──
            # temperature 始终发送（本地模型无 thinking/temperature 互斥约束）
            api_params["temperature"] = self.temperature
            if self._is_ollama:
                # Ollama 专用：保持模型常驻 + 扩展上下文窗口
                num_ctx = min(
                    max(self.max_tokens + 4096, LOCAL_NUM_CTX_MIN),
                    LOCAL_NUM_CTX_MAX,
                )
                api_params["extra_body"] = {
                    "keep_alive": "30m",          # 模型常驻内存，避免重复加载
                    "options": {"num_ctx": num_ctx},  # 上下文窗口对齐 max_tokens
                }
                # Ollama 的 max_tokens → num_predict，不能超过 num_ctx
                api_params["max_tokens"] = min(self.max_tokens, num_ctx - 1024)
        if self.think_level == "关" and not self._is_local_mode:
            api_params["temperature"] = self.temperature

        # 在线程池中运行同步流式调用
        loop = nasync_io.get_running_loop()

        def _sync_stream():
            reasoning_parts = []
            content_parts = []
            tool_calls_accum = {}
            stream_usage = None
            _output_started = False
            _thinking_event_buf = ""  # 批量缓冲 T: 事件

            try:
                stream = self.client.chat.completions.create(**api_params)
                for chunk in stream:
                    if self._stop_event.is_set():
                        break

                    if hasattr(chunk, 'usage') and chunk.usage:
                        stream_usage = {
                            "input_tokens": chunk.usage.prompt_tokens or 0,
                            "output_tokens": chunk.usage.completion_tokens or 0
                        }

                    delta = chunk.choices[0].delta

                    if hasattr(delta, 'tool_calls') and delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_accum:
                                tool_calls_accum[idx] = {
                                    "id": tc.id or "", "name": "", "arguments": ""
                                }
                            if tc.id:
                                tool_calls_accum[idx]["id"] = tc.id
                            if tc.function and tc.function.name:
                                tool_calls_accum[idx]["name"] = tc.function.name
                            if tc.function and tc.function.arguments:
                                tool_calls_accum[idx]["arguments"] += tc.function.arguments

                    # ★ 修复：直接调用 EventQueue.put()（线程安全），
                    # 不再使用 call_soon_threadsafe 间接调度。
                    # call_soon_threadsafe 导致事件入队顺序与流式产生顺序
                    # 不一致，工具调用后思考过程显示破碎。
                    if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                        reasoning_parts.append(delta.reasoning_content)
                        _thinking_event_buf += delta.reasoning_content
                        if len(_thinking_event_buf) >= 80:
                            self.event_queue.put(f"T:{_thinking_event_buf}")
                            _thinking_event_buf = ""
                    if hasattr(delta, 'content') and delta.content:
                        if not _output_started:
                            _output_started = True
                            if _thinking_event_buf:
                                self.event_queue.put(f"T:{_thinking_event_buf}")
                                _thinking_event_buf = ""
                            self.event_queue.put("F:")
                        content_parts.append(delta.content)
                        self.event_queue.put(f"R:{delta.content}")
            except Exception as e:
                # 用户主动停止时 HTTP 传输层被关闭，静默处理连接中断错误
                if not self._stop_event.is_set():
                    self.event_queue.put(f"E:API call failed: {str(e)}")

            # Flush 残留的 thinking 事件缓冲
            if _thinking_event_buf:
                self.event_queue.put(f"T:{_thinking_event_buf}")

            tool_calls_list = []
            for idx in sorted(tool_calls_accum.keys()):
                tc = tool_calls_accum[idx]
                if tc["id"] and tc["name"]:
                    tool_calls_list.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"]
                        }
                    })

            return {
                "reasoning": "".join(reasoning_parts),
                "content": "".join(content_parts),
                "tool_calls": tool_calls_list,
                "usage": stream_usage,
            }

        # ★ 僵尸进程防护：nasync_io.wait_for 硬超时
        # 即使线程池中的同步函数阻塞（网络挂起），事件循环也不会永久等待
        try:
            return await nasync_io.wait_for(
                loop.run_in_executor(None, _sync_stream),
                timeout=self.api_request_timeout,
            )
        except nasync_io.TimeoutError:
            self.event_queue.put(f"E:API request timeout ({format_api_timeout(self.api_request_timeout)}) — network may be unreachable")
            self._stop_event.set()
            return {
                "reasoning": "",
                "content": "",
                "tool_calls": [],
                "usage": None,
            }

    # ═══════════════════════════════════════════════════════════════
    #  工具调用处理（异步）
    # ═══════════════════════════════════════════════════════════════

    # 插件工具执行超时（秒），防止插件阻塞事件循环导致挂起
    PLUGIN_TOOL_TIMEOUT = 120.0

    def _is_plugin_tool(self, tool_name: str) -> bool:
        """判断工具是否由插件提供（插件工具不经 async_executor 执行）。"""
        if not self.plugin_manager:
            return False
        try:
            plugin_names = {t["function"]["name"] for t in self.plugin_manager.get_tools()}
            return tool_name in plugin_names
        except Exception:
            return False

    async def _execute_tool_async(self, tool_name: str, tool_args: dict) -> str:
        """Execute a tool, routing to plugin or built-in executor (async-safe).

        ★ 关键修复：插件 execute() 是同步函数（如 web_fetcher 用 requests 发 HTTP、
        stress_tester 跑 subprocess、doc_reader 读大文件）。直接在 async 上下文中
        调用会阻塞事件循环，导致前端轮询停止、思考过程显示中断、整个 UI 挂起。

        修复方案：插件工具一律通过 run_in_executor 在线程池中执行，并设置 120s 硬超时。
        """
        # Check plugin tools first
        if self.plugin_manager:
            plugin_tools = self.plugin_manager.get_tools()
            plugin_names = {t["function"]["name"] for t in plugin_tools}
            if tool_name in plugin_names:
                loop = nasync_io.get_running_loop()
                try:
                    return await nasync_io.wait_for(
                        loop.run_in_executor(
                            None, self.plugin_manager.execute, tool_name, tool_args
                        ),
                        timeout=self.PLUGIN_TOOL_TIMEOUT,
                    )
                except nasync_io.TimeoutError:
                    return (
                        f"Error: plugin tool '{tool_name}' timed out "
                        f"after {self.PLUGIN_TOOL_TIMEOUT:.0f}s"
                    )
        # Fall back to built-in executor
        return await self.executor.execute(tool_name, tool_args)

    async def _process_tool_calls_async(self, messages: list,
                                        tool_calls_list: list,
                                        step: int) -> Optional[str]:
        """异步处理工具调用。

        与同步版 _process_tool_calls_openai() 行为对齐：
        1. before_tool_call / after_tool_call 插件钩子
        2. 插件工具优先路由到 PluginManager（修复 unknown tool bug）
        3. ask_user 交互、写/删文件确认
        """
        for tc in tool_calls_list:
            tool_name = tc["function"]["name"]
            tool_args = json.loads(tc["function"]["arguments"])

            # ── Hook: before_tool_call ──
            if self.plugin_manager:
                modified_args = self.plugin_manager.fire_before_tool_call(
                    tool_name, tool_args)
                if modified_args is None:
                    # Blocked by plugin
                    blocked_msg = "Tool call blocked by plugin."
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": blocked_msg
                    })
                    self.executor.log_tool_call(step + 1, tool_name, tool_args, blocked_msg)
                    continue
                tool_args = modified_args

            if tool_name == "ask_user":
                question = tool_args.get("question", "")
                # ── Hook: user_input_required ──
                if self.plugin_manager:
                    self.plugin_manager.fire_user_input_required(question)
                self.event_queue.put(f"Q:{json.dumps(question, ensure_ascii=False)}")
                reply = await self._wait_for_user_input()
                if self._stop_event.is_set():
                    self.event_queue.put("E:Task stopped by user")
                    self.event_queue.signal_finish()
                    return "stopped"
                # ask_user 也是工具调用：必须返回 role=tool 消息（带 tool_call_id），
                # 否则 Responses API 报 "No tool output found for tool call"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": reply
                })
                self.executor.log_tool_call(step + 1, tool_name, tool_args,
                                            f"user replied: {reply[:200]}")
                self._add_tool_tokens(tool_name, reply)
                # ── 调试收集器：记录观察结果 ──
                try:
                    if self._debug_logger:
                        self._debug_logger.append_observation(step + 1, tool_name, reply)
                except Exception:
                    pass
            else:
                # ★ P0-8 审批拆分：插件工具走「插件工具调用审批」，原生工具走「原生工具确认」
                # ★ 每次检查前刷新审批策略：开关改动（含「不再显示」）即时生效
                self._refresh_approval()
                is_plugin = plugin_has_tool(self.plugin_manager, tool_name)
                needs_approval, _approval_level = self.approval.requires_approval(
                    tool_name, is_plugin=is_plugin)
                # ★ 视觉 delegate 让渡：用户已通过 vision_delegate 主动预授权，
                #   覆盖范围内的视觉工具调用跳过审批弹窗（让渡即免逐次确认）。
                if needs_approval and is_plugin and \
                        self._vision_delegate_covers(tool_name, tool_args):
                    needs_approval = False
                if needs_approval:
                    if not await self._confirm_write_delete(tool_name, tool_args, is_plugin=is_plugin):
                        if self._stop_event.is_set():
                            self.event_queue.put("E:Task stopped by user")
                            self.event_queue.signal_finish()
                            return "stopped"
                        cancel_msg = "User cancelled the operation."
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": cancel_msg
                        })
                        self.executor.log_tool_call(step + 1, tool_name, tool_args, cancel_msg)
                        continue

                # ★ 异步执行工具（先路由插件，再回退内置执行器）
                _tool_start = time.time()
                result = await self._execute_tool_async(tool_name, tool_args)
                _tool_elapsed_ms = (time.time() - _tool_start) * 1000

                # ── Hook: after_tool_call ──
                if self.plugin_manager:
                    result = self.plugin_manager.fire_after_tool_call(
                        tool_name, tool_args, result)

                # ── 调试收集器：记录观察结果 + 插件工具耗时 ──
                try:
                    if self._debug_logger:
                        self._debug_logger.append_observation(step + 1, tool_name, result)
                        # 插件工具不经 async_executor（其内部已记录内置工具），
                        # 这里补记插件工具的耗时诊断卡。
                        if self._is_plugin_tool(tool_name):
                            self._debug_logger.record_tool_call(
                                tool=tool_name, args=tool_args, result=result,
                                elapsed_ms=_tool_elapsed_ms, step=step + 1)
                except Exception:
                    pass

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result
                })
                self.executor.log_tool_call(step + 1, tool_name, tool_args, result)
                self._add_tool_tokens(tool_name, result)

        return None

    # ═══════════════════════════════════════════════════════════════
    #  Responses API 路径（异步）
    # ═══════════════════════════════════════════════════════════════

    async def _run_responses(self, user_message: str,
                             history: Optional[List[Dict]] = None) -> str:
        """异步 Responses API 路径。"""
        self.event_queue.reset()

        messages = self._build_full_messages(user_message, None,
                                             memory_content=self._memory_content)
        self._messages = messages

        tools = self._build_responses_tools()
        reasoning_effort = self._get_reasoning_effort()

        for step in range(self.max_steps):
            if self._stop_event.is_set():
                self.event_queue.put("E:Task stopped by user")
                self.event_queue.signal_finish()
                return "stopped"

            if self._check_timeout():
                elapsed = int(self._get_elapsed())
                self.event_queue.put(f"E:Task timeout after {elapsed}s (limit: {self.task_timeout}s)")
                self.event_queue.signal_finish()
                return "timeout"

            self._step_count = step + 1

            # ── 调试收集器：标记当前步骤 ──
            try:
                if self._debug_logger:
                    self._debug_logger.set_current_step(self._step_count)
            except Exception:
                pass

            # ── Hook: before_step（与同步版对齐，供 context_retriever 注入索引状态）──
            if self.plugin_manager:
                messages = self.plugin_manager.fire_before_step(
                    self._step_count, messages)

            input_items = self._build_responses_input(messages)

            api_params = {
                "model": self.model,
                "input": input_items,
                "tools": tools,
                "stream": True,
                "max_output_tokens": self.max_tokens,
            }
            if self.think_level == "关":
                api_params["temperature"] = self.temperature
            elif reasoning_effort is not None:
                api_params["reasoning"] = {"effort": reasoning_effort}

            result = await self._call_responses_stream(api_params)

            if result is None:
                return "stopped"

            full_reasoning = result["reasoning"]
            full_content = result["content"]
            tool_calls_list = result["tool_calls"]
            web_search_calls = result.get("web_search_calls", [])
            stream_usage = result.get("usage")

            if stream_usage:
                self._update_usage(stream_usage["input_tokens"], stream_usage["output_tokens"])
            else:
                estimated_input = sum(len(str(m.get("content", ""))) for m in messages) // 4
                estimated_output = len(full_content) // 4
                self._update_usage(estimated_input, estimated_output)

            assistant_msg = {"role": "assistant", "content": full_content}
            if full_reasoning:
                assistant_msg["reasoning_content"] = full_reasoning

            if web_search_calls:
                for wc in web_search_calls:
                    query = getattr(wc, "query", "") or ""
                    cmd_info = json.dumps({
                        "tool": "web_search (native Responses API)",
                        "args": {"query": query}
                    }, ensure_ascii=False)
                    self.event_queue.put(f"C:{cmd_info}")
                assistant_msg["web_search_calls"] = [
                    {
                        "id": getattr(wc, "id", "") or "",
                        "status": getattr(wc, "status", "completed") or "completed",
                        "query": getattr(wc, "query", "") or ""
                    }
                    for wc in web_search_calls
                ]

            if tool_calls_list:
                assistant_msg["tool_calls"] = tool_calls_list
                for tc in tool_calls_list:
                    cmd_info = json.dumps({
                        "tool": tc["function"]["name"],
                        "args": json.loads(tc["function"]["arguments"])
                    }, ensure_ascii=False)
                    self.event_queue.put(f"C:{cmd_info}")

            messages.append(assistant_msg)

            # ── 调试收集器：记录 ReAct 步骤（思考 + 行动，观察稍后追加）──
            try:
                if self._debug_logger:
                    tc_formatted = [
                        {"name": tc["function"]["name"],
                         "arguments": tc["function"]["arguments"]}
                        for tc in tool_calls_list
                    ]
                    self._debug_logger.record_react_step(
                        step=self._step_count,
                        reasoning=full_reasoning,
                        tool_calls=tc_formatted,
                        observations=[],
                    )
            except Exception:
                pass

            if not tool_calls_list:
                if full_content:
                    self.event_queue.put(f"D:{full_content}")
                self.event_queue.signal_finish()
                return full_content

            status = await self._process_tool_calls_async(messages, tool_calls_list, step)
            if status == "stopped":
                return "stopped"

        self.event_queue.put("E:Max steps reached, task incomplete")
        self.event_queue.signal_finish()
        return "max_steps"

    async def _call_responses_stream(self, api_params: dict) -> Optional[dict]:
        """在线程池中调用 Responses API 流式。"""
        loop = nasync_io.get_running_loop()

        def _sync_responses():
            reasoning_parts = []
            content_parts = []
            tool_calls_accum = {}
            web_search_calls = []
            stream_usage = None
            _output_started = False
            _thinking_event_buf = ""  # 批量缓冲 T: 事件

            try:
                stream = self.client.responses.create(**api_params)
                for event in stream:
                    if self._stop_event.is_set():
                        break

                    et = event.type

                    if et == "response.reasoning_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        reasoning_parts.append(delta)
                        _thinking_event_buf += delta
                        if len(_thinking_event_buf) >= 80:
                            self.event_queue.put(f"T:{_thinking_event_buf}")
                            _thinking_event_buf = ""
                    elif et == "response.output_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        if not _output_started:
                            _output_started = True
                            if _thinking_event_buf:
                                self.event_queue.put(f"T:{_thinking_event_buf}")
                                _thinking_event_buf = ""
                            self.event_queue.put("F:")
                        content_parts.append(delta)
                        self.event_queue.put(f"R:{delta}")
                    elif et == "response.function_call_arguments.delta":
                        item_id = getattr(event, "item_id", "") or ""
                        delta = getattr(event, "delta", "") or ""
                        acc = tool_calls_accum.setdefault(
                            item_id, {"call_id": "", "name": "", "arguments": ""})
                        acc["arguments"] += delta
                    elif et == "response.output_item.done":
                        item = getattr(event, "item", None)
                        if item is not None and getattr(item, "type", "") == "function_call":
                            item_id = getattr(item, "id", "") or ""
                            acc = tool_calls_accum.setdefault(
                                item_id, {"call_id": "", "name": "", "arguments": ""})
                            acc["call_id"] = getattr(item, "call_id", "") or acc["call_id"]
                            acc["name"] = getattr(item, "name", "") or acc["name"]
                            if getattr(item, "arguments", None):
                                acc["arguments"] = item.arguments
                        elif item is not None and getattr(item, "type", "") == "web_search_call":
                            web_search_calls.append(item)
                    elif et == "response.completed":
                        resp = getattr(event, "response", None)
                        if resp is not None and getattr(resp, "usage", None):
                            u = resp.usage
                            stream_usage = {
                                "input_tokens": getattr(u, "input_tokens", 0) or 0,
                                "output_tokens": getattr(u, "output_tokens", 0) or 0
                            }
            except Exception as e:
                # 用户主动停止时 HTTP 传输层被关闭，静默处理连接中断错误
                if not self._stop_event.is_set():
                    self.event_queue.put(f"E:Responses API call failed: {str(e)}")

            # Flush 残留的 thinking 事件缓冲
            if _thinking_event_buf:
                self.event_queue.put(f"T:{_thinking_event_buf}")

            tool_calls_list = []
            for item_id in sorted(tool_calls_accum.keys()):
                acc = tool_calls_accum[item_id]
                if acc["call_id"] and acc["name"]:
                    tool_calls_list.append({
                        "id": acc["call_id"],
                        "type": "function",
                        "function": {
                            "name": acc["name"],
                            "arguments": acc["arguments"]
                        }
                    })

            return {
                "reasoning": "".join(reasoning_parts),
                "content": "".join(content_parts),
                "tool_calls": tool_calls_list,
                "web_search_calls": web_search_calls,
                "usage": stream_usage,
            }

        try:
            return await nasync_io.wait_for(
                loop.run_in_executor(None, _sync_responses),
                timeout=self.api_request_timeout,
            )
        except nasync_io.TimeoutError:
            self.event_queue.put(f"E:Responses API request timeout ({format_api_timeout(self.api_request_timeout)}) — network may be unreachable")
            self._stop_event.set()
            return {
                "reasoning": "",
                "content": "",
                "tool_calls": [],
                "web_search_calls": [],
                "usage": None,
            }

    # ═══════════════════════════════════════════════════════════════
    #  Anthropic 路径（异步）
    # ═══════════════════════════════════════════════════════════════

    async def _run_anthropic(self, user_message: str,
                             history: Optional[List[Dict]] = None) -> str:
        """异步 Anthropic 兼容路径。"""
        self.event_queue.reset()

        system_prompt = self._build_system_prompt()
        if self._memory_content:
            system_prompt += "\n\n" + self._memory_content

        openai_messages = self._build_full_messages(user_message, history,
                                                    memory_content=self._memory_content)
        self._messages = openai_messages

        anthropic_messages = self._convert_openai_messages_to_anthropic(openai_messages)
        all_tools = self._build_tools_anthropic()

        effort_map = {"低": "low", "中": "medium", "高": "max"}
        reasoning_effort = effort_map.get(self.think_level, "max") if self.think_level != "关" else None
        thinking_param = {"type": "enabled"} if reasoning_effort is not None else None
        output_config_param = {"effort": reasoning_effort} if reasoning_effort is not None else None

        for step in range(self.max_steps):
            if self._stop_event.is_set():
                self.event_queue.put("E:Task stopped by user")
                self.event_queue.signal_finish()
                return "stopped"

            if self._check_timeout():
                elapsed = int(self._get_elapsed())
                self.event_queue.put(f"E:Task timeout after {elapsed}s (limit: {self.task_timeout}s)")
                self.event_queue.signal_finish()
                return "timeout"

            self._step_count = step + 1

            # ── 调试收集器：标记当前步骤 ──
            try:
                if self._debug_logger:
                    self._debug_logger.set_current_step(self._step_count)
            except Exception:
                pass

            # ── Hook: before_step（与同步版对齐；Anthropic 格式消息无 system 角色，
            #   注入型钩子会自行跳过，返回值在此路径被忽略）──
            if self.plugin_manager:
                self.plugin_manager.fire_before_step(
                    self._step_count, anthropic_messages)

            result = await self._call_anthropic_stream_async(
                messages=anthropic_messages,
                system_prompt=system_prompt,
                tools=all_tools,
                thinking=thinking_param,
                output_config=output_config_param,
            )

            if result is None:
                self.event_queue.put("E:Task stopped by user")
                self.event_queue.signal_finish()
                return "stopped"

            full_reasoning = result["reasoning"]
            full_content = result["content"]
            thinking_blocks = result["thinking_blocks"]
            tool_uses = result["tool_uses"]
            usage = result.get("usage")

            if usage:
                self._update_usage(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
            else:
                estimated_input = sum(len(str(m.get("content", ""))) for m in anthropic_messages) // 4
                estimated_output = len(full_content) // 4
                self._update_usage(estimated_input, estimated_output)

            # ── 调试收集器：记录 ReAct 步骤（思考 + 行动，观察稍后追加）──
            try:
                if self._debug_logger:
                    tu_formatted = [
                        {"name": tu["name"], "arguments": json.dumps(tu["input"], ensure_ascii=False)}
                        for tu in tool_uses
                    ]
                    self._debug_logger.record_react_step(
                        step=self._step_count,
                        reasoning=full_reasoning,
                        tool_calls=tu_formatted,
                        observations=[],
                    )
            except Exception:
                pass

            if not tool_uses:
                if full_content:
                    self.event_queue.put(f"D:{full_content}")
                self.event_queue.signal_finish()
                return full_content

            assistant_content = []
            for tb in thinking_blocks:
                assistant_content.append(tb)
            if full_content:
                assistant_content.append({"type": "text", "text": full_content})
            for tu in tool_uses:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tu["id"],
                    "name": tu["name"],
                    "input": tu["input"]
                })
            anthropic_messages.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for tu in tool_uses:
                tool_name = tu["name"]
                tool_input = tu["input"]
                tool_id = tu["id"]

                cmd_info = json.dumps({
                    "tool": tool_name,
                    "args": tool_input
                }, ensure_ascii=False)
                self.event_queue.put(f"C:{cmd_info}")

                # ── Hook: before_tool_call ──
                if self.plugin_manager:
                    modified_input = self.plugin_manager.fire_before_tool_call(
                        tool_name, tool_input)
                    if modified_input is None:
                        # Blocked by plugin
                        blocked_msg = "Tool call blocked by plugin."
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": blocked_msg
                        })
                        continue
                    tool_input = modified_input

                if tool_name == "ask_user":
                    question = tool_input.get("question", "")
                    # ── Hook: user_input_required ──
                    if self.plugin_manager:
                        self.plugin_manager.fire_user_input_required(question)
                    self.event_queue.put(f"Q:{json.dumps(question, ensure_ascii=False)}")
                    reply = await self._wait_for_user_input()
                    if self._stop_event.is_set():
                        return "stopped"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": reply
                    })
                    self._add_tool_tokens(tool_name, reply)
                    # ── 调试收集器：记录观察结果 ──
                    try:
                        if self._debug_logger:
                            self._debug_logger.append_observation(self._step_count, tool_name, reply)
                    except Exception:
                        pass
                else:
                    # ★ P0-8 审批拆分：插件工具走「插件工具调用审批」，原生工具走「原生工具确认」
                    # ★ 每次检查前刷新审批策略：开关改动（含「不再显示」）即时生效
                    self._refresh_approval()
                    is_plugin = plugin_has_tool(self.plugin_manager, tool_name)
                    needs_approval, _approval_level = self.approval.requires_approval(
                        tool_name, is_plugin=is_plugin)
                    # ★ 视觉 delegate 让渡：用户已通过 vision_delegate 主动预授权，
                    #   覆盖范围内的视觉工具调用跳过审批弹窗（让渡即免逐次确认）。
                    if needs_approval and is_plugin and \
                            self._vision_delegate_covers(tool_name, tool_input):
                        needs_approval = False
                    if needs_approval:
                        if not await self._confirm_write_delete(tool_name, tool_input, is_plugin=is_plugin):
                            if self._stop_event.is_set():
                                return "stopped"
                            cancel_msg = "User cancelled the operation."
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": cancel_msg
                            })
                            continue

                    # ★ 异步执行工具（先路由插件，再回退内置执行器）
                    _tool_start = time.time()
                    result_text = await self._execute_tool_async(tool_name, tool_input)
                    _tool_elapsed_ms = (time.time() - _tool_start) * 1000

                    # ── Hook: after_tool_call ──
                    if self.plugin_manager:
                        result_text = self.plugin_manager.fire_after_tool_call(
                            tool_name, tool_input, result_text)

                    # ── 调试收集器：记录观察结果 + 插件工具耗时 ──
                    try:
                        if self._debug_logger:
                            self._debug_logger.append_observation(self._step_count, tool_name, result_text)
                            if self._is_plugin_tool(tool_name):
                                self._debug_logger.record_tool_call(
                                    tool=tool_name, args=tool_input, result=result_text,
                                    elapsed_ms=_tool_elapsed_ms, step=self._step_count)
                    except Exception:
                        pass

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result_text
                    })
                    self._add_tool_tokens(tool_name, result_text)

            if tool_results:
                anthropic_messages.append({"role": "user", "content": tool_results})

        self.event_queue.put("E:Max steps reached, task incomplete")
        self.event_queue.signal_finish()
        return "max_steps"

    async def _call_anthropic_stream_async(self, messages: list, system_prompt: str,
                                           tools: list, thinking=None,
                                           output_config=None) -> Optional[dict]:
        """在线程池中调用 Anthropic 流式 API。"""
        loop = nasync_io.get_running_loop()

        def _sync_anthropic():
            reasoning_parts = []
            thinking_blocks = []
            content_parts = []
            tool_uses = []
            usage = None
            _output_started = False
            _thinking_event_buf = ""  # 批量缓冲：避免碎片化 T: 事件直达前端

            call_params = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": system_prompt.strip(),
                "messages": messages,
                "tools": tools,
            }
            if thinking is not None:
                call_params["thinking"] = thinking
            if output_config is not None:
                call_params["output_config"] = output_config
            if self.think_level == "关":
                call_params["temperature"] = self.temperature

            try:
                with self.anthropic_client.messages.stream(**call_params) as stream:
                    for event in stream:
                        if self._stop_event.is_set():
                            try:
                                stream.close()
                            except Exception:
                                pass
                            return None

                        if event.type == "content_block_start":
                            cb = event.content_block
                            if cb.type == "thinking":
                                pass  # tracked via content_block_stop
                        elif event.type == "content_block_delta":
                            delta = event.delta
                            if hasattr(delta, 'thinking') and delta.thinking:
                                reasoning_parts.append(delta.thinking)
                                _thinking_event_buf += delta.thinking
                                # 累积到 80 字符再发送，避免前端收到碎片化的 T: 事件
                                if len(_thinking_event_buf) >= 80:
                                    self.event_queue.put(f"T:{_thinking_event_buf}")
                                    _thinking_event_buf = ""
                            if hasattr(delta, 'text') and delta.text:
                                if not _output_started:
                                    _output_started = True
                                    # Flush 残留 thinking 并结束思考块
                                    if _thinking_event_buf:
                                        self.event_queue.put(f"T:{_thinking_event_buf}")
                                        _thinking_event_buf = ""
                                    self.event_queue.put("F:")
                                content_parts.append(delta.text)
                                self.event_queue.put(f"R:{delta.text}")
                        elif event.type == "content_block_stop":
                            if hasattr(event, 'content_block') and event.content_block:
                                cb = event.content_block
                                if cb.type == "thinking":
                                    # Flush 残留的 thinking 缓冲区
                                    if _thinking_event_buf:
                                        self.event_queue.put(f"T:{_thinking_event_buf}")
                                        _thinking_event_buf = ""
                                    thinking_blocks.append({
                                        "type": "thinking",
                                        "thinking": getattr(cb, 'thinking', ''),
                                        "signature": getattr(cb, 'signature', '')
                                    })
                                elif cb.type == "tool_use":
                                    tool_uses.append({
                                        "id": cb.id,
                                        "name": cb.name,
                                        "input": cb.input
                                    })
                        elif event.type == "message_stop":
                            if hasattr(event, 'message') and event.message:
                                msg_usage = getattr(event.message, 'usage', None)
                                if msg_usage:
                                    usage = {
                                        "input_tokens": getattr(msg_usage, 'input_tokens', 0) or 0,
                                        "output_tokens": getattr(msg_usage, 'output_tokens', 0) or 0
                                    }
            except Exception as e:
                # 用户主动停止时 HTTP 传输层被关闭，静默处理连接中断错误
                if not self._stop_event.is_set():
                    self.event_queue.put(f"E:Anthropic API call failed: {str(e)}")

            # Flush 残留的 thinking 事件缓冲
            if _thinking_event_buf:
                self.event_queue.put(f"T:{_thinking_event_buf}")

            return {
                "reasoning": "".join(reasoning_parts),
                "content": "".join(content_parts),
                "thinking_blocks": thinking_blocks,
                "tool_uses": tool_uses,
                "usage": usage,
            }

        try:
            return await nasync_io.wait_for(
                loop.run_in_executor(None, _sync_anthropic),
                timeout=self.api_request_timeout,
            )
        except nasync_io.TimeoutError:
            self.event_queue.put(f"E:Anthropic API request timeout ({format_api_timeout(self.api_request_timeout)}) — network may be unreachable")
            self._stop_event.set()
            return {
                "reasoning": "",
                "content": "",
                "thinking_blocks": [],
                "tool_uses": [],
                "usage": None,
            }

    # ═══════════════════════════════════════════════════════════════
    #  消息构建（复用原版逻辑）
    # ═══════════════════════════════════════════════════════════════

    _TIMESTAMP_RE = re.compile(
        r'^\[SystemInfo\]当前系统时间：\d{4}年\d{2}月\d{2}日 \d{2}:\d{2}:\d{2}\n'
    )

    def _build_full_messages(self, user_message: str, history: Optional[List[Dict]] = None,
                              memory_content: str = "") -> list:
        """构建完整的消息列表（委托给共享模块）。"""
        plugin_tool_names = None
        if self.plugin_manager:
            pt = self.plugin_manager.get_tools()
            if pt:
                plugin_tool_names = [t["function"]["name"] for t in pt]
        return build_full_messages(
            user_message, self.project_root, self.enable_web_search,
            history=history, memory_content=memory_content,
            has_context_retriever=True,
            has_file_searcher=True,
            has_file_surgeon=True,
            plugin_tool_names=plugin_tool_names)

    def _build_system_prompt(self) -> str:
        """构建系统提示词（委托给共享模块）。

        动态检测插件工具并注入对应的使用指南：
        - context_retriever → search_context / index_context
        - file_searcher → search_large_file / search_files / index_workspace
        - file_surgeon → surgical_scan / surgical_replace

        模型是提示词驱动的，仅提供工具 schema 不足以让它主动使用插件工具，
        必须在提示词中说明使用时机和优先规则。
        """
        plugin_tool_names = None
        if self.plugin_manager:
            pt = self.plugin_manager.get_tools()
            if pt:
                plugin_tool_names = [t["function"]["name"] for t in pt]
        return build_system_prompt(
            self.project_root, self.enable_web_search,
            has_context_retriever=True,
            has_file_searcher=True,
            has_file_surgeon=True,
            plugin_tool_names=plugin_tool_names,
            custom_prompt=self.custom_system_prompt)

    # ═══════════════════════════════════════════════════════════════
    #  工具构建
    # ═══════════════════════════════════════════════════════════════

    def _build_tools_openai(self) -> list:
        """构建 OpenAI 格式工具列表（委托给共享模块）。"""
        return build_tools_openai(self.plugin_manager, self.enable_web_search)

    def _build_tools_anthropic(self) -> list:
        """构建 Anthropic 格式工具列表（委托给共享模块）。"""
        return build_tools_anthropic(self.plugin_manager, self.enable_web_search)

    def _build_responses_tools(self) -> list:
        """构建 Responses API 工具列表（委托给共享模块）。"""
        return build_responses_tools(self.plugin_manager, self.enable_web_search)

    def _build_responses_input(self, messages: list) -> list:
        """将 OpenAI 格式 messages 转换为 Responses API 的 input items（委托给共享模块）。"""
        return build_responses_input(messages)

    def _get_thinking_extra_body(self) -> dict:
        """返回 thinking extra_body 配置（委托给共享模块）。"""
        return get_thinking_extra_body(self.think_level)

    def _get_reasoning_effort(self) -> Optional[str]:
        """返回 reasoning_effort 值（委托给共享模块）。"""
        return get_reasoning_effort(self.think_level)

    def _convert_openai_messages_to_anthropic(self, messages: list) -> list:
        """将 OpenAI 格式消息转换为 Anthropic 格式（委托给共享模块）。"""
        return convert_openai_messages_to_anthropic(messages)

# Vibe Coding Agent - 核心Agent循环
# Copyright (c) 2026 xingluosama

import json
import os
import re
import time
import threading
from datetime import datetime
from typing import List, Dict, Optional

from openai import OpenAI
from anthropic import Anthropic as AnthropicClient

from event_queue import EventQueue
from executor import ToolExecutor
from plugin_system.manager import PluginManager
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

CHARS_PER_TOKEN = 3

# 本地部署模式（Ollama 等）的上下文窗口上限与保底值
LOCAL_NUM_CTX_MAX = 65536
LOCAL_NUM_CTX_MIN = 8192


def estimate_tokens(text: str) -> int:
    """估算一段文本的 token 数量。"""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


class AgentLoop:

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
        temperature: float = 1.0,
        think_level: str = "高",
        max_tokens: int = 32767,
        task_timeout: int = 0,
        plugin_manager: Optional[PluginManager] = None,
        use_responses_api: bool = False,
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
        self.temperature = temperature
        self.think_level = think_level
        self.max_tokens = max_tokens
        self.task_timeout = task_timeout 
        self.custom_system_prompt = custom_system_prompt

       
        self._task_start_time = 0.0
        self._pause_start_time = 0.0
        self._total_pause_duration = 0.0

        
        self._last_usage = None      
        self._total_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_call_tokens": 0     
        }
        self._step_count = 0          

        
        self._conversation_history = []

       
        if app_dir:
            self.tool_log_path = os.path.join(app_dir, "tool_calls.jsonl")
        else:
            self.tool_log_path = ""

        
        # ── 本地部署模式检测（BaseURL 指向回环地址 → Ollama 等本地服务）──
        self._is_local_mode = is_loopback_url(base_url)

        self._is_deepseek_official = (
            base_url.rstrip('/') == "https://api.deepseek.com"
            and not self._is_local_mode
        )

        # OpenAI 官方端点检测
        self._is_openai_official = (
            base_url.rstrip('/') == "https://api.openai.com"
            and not self._is_local_mode
        )

        # ── Responses API 原生支持（仅 OpenAI 端点）──
        # 设置开关 use_responses_api 默认关闭：
        # - DeepSeek 官方文档声明此接口无状态，开启后每次请求必须携带完整 input 历史，否则多轮对话将丢失上下文
        # - OpenAI 官方将其 Responses API 定位为 Chat Completions API 的演进和升级
        # - 本地部署模式强制关闭（本地服务只兼容 Chat Completions）
        self._use_responses_api = (
            use_responses_api
            and self._is_openai_official
        )

        # Anthropic 兼容搜索模式仅在未启用 Responses API 时使用
        # （本地部署模式强制关闭）
        self._use_anthropic_search = (
            enable_web_search
            and not self._is_local_mode
            and base_url in ("https://api.deepseek.com", "https://api.deepseek.com/")
            and not self._use_responses_api
        )

        # Ollama 特有探测 + 本地模式优化（占位 key / 模型自动匹配）
        self._is_ollama = self._is_local_mode and self._detect_ollama(base_url, api_key)
        if self._is_local_mode:
            if not api_key:
                api_key = "ollama"  # 本地服务无需鉴权，OpenAI SDK 要求非空
            self._resolve_local_model()

        self.client = OpenAI(api_key=api_key, base_url=base_url)

        if self._use_anthropic_search:
            self.anthropic_client = AnthropicClient(
                api_key=api_key,
                base_url="https://api.deepseek.com/anthropic"
            )
        else:
            self.anthropic_client = None

        try:
            from executor import DockerSandbox
            self._sandbox = DockerSandbox(project_root)
            self._sandbox.start()
            self.executor = ToolExecutor(project_root, sandbox=self._sandbox, app_dir=app_dir)
        except Exception:
            self._sandbox = None
            self.executor = ToolExecutor(project_root, app_dir=app_dir)

        self.event_queue = event_queue
        self._stop_event = threading.Event()
        self._user_reply_event = threading.Event()
        self._user_reply_value = ""

        # ── Plugin system ──
        self.plugin_manager = plugin_manager or PluginManager(
            [], app_dir, project_root, config={})
        self.plugin_manager.update_config_snapshot({
            "project_root": project_root,
            "app_dir": app_dir,
            "model": model,
            "base_url": base_url,
            "max_steps": max_steps,
            "enable_web_search": enable_web_search,
            "confirm_write_delete": confirm_write_delete,
            "temperature": temperature,
            "think_level": think_level,
            "max_tokens": max_tokens,
            "task_timeout": task_timeout,
        })
        self.plugin_manager.fire_agent_init()

    # ═══════════════════════════════════════════════════════════════
    #  本地部署模式：Ollama 探测 / 模型自动匹配
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _detect_ollama(base_url: str, api_key: str) -> bool:
        """探测目标是否为 Ollama 服务（Ollama 独有的 /api/tags 端点）。"""
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
        """本地模式下自动匹配可用模型（配置模型不存在时回退第一个）。"""
        try:
            models = self.client.models.list()
            local_ids = [m.id for m in getattr(models, "data", [])]
            if not local_ids:
                return
            if self.model in local_ids:
                return
            fallback = local_ids[0]
            print(f"[LocalMode] model '{self.model}' not found locally, "
                  f"fallback to '{fallback}' (available: {local_ids[:5]})")
            self.model = fallback
        except Exception:
            pass  # 静默降级：保持原模型配置

    def get_local_mode_info(self) -> dict:
        """返回本地部署模式信息（供 API 层 / 前端展示）。"""
        return {
            "local_mode": self._is_local_mode,
            "is_ollama": self._is_ollama,
            "model": self.model,
            "base_url": self.base_url,
        }

    def _get_elapsed(self) -> float:
        """获取已用时间（扣除暂停时间）。"""
        if self._pause_start_time > 0:
            current_pause = time.time() - self._pause_start_time
        else:
            current_pause = 0.0
        return time.time() - self._task_start_time - self._total_pause_duration - current_pause

    def _check_timeout(self) -> bool:
        """检查是否超时。返回 True 表示已超时。"""
        if self.task_timeout <= 0:
            return False
        return self._get_elapsed() > self.task_timeout

    def _pause_timer(self):
        """暂停超时计时器（进入等待用户输入状态时调用）。"""
        if self.task_timeout > 0:
            self._pause_start_time = time.time()

    def _resume_timer(self):
        """恢复超时计时器（用户输入完成后调用）。"""
        if self.task_timeout > 0 and self._pause_start_time > 0:
            self._total_pause_duration += time.time() - self._pause_start_time
            self._pause_start_time = 0.0


    def _build_tools_openai(self) -> list:
        """构建 OpenAI 格式工具列表（委托给共享模块）。"""
        return build_tools_openai(self.plugin_manager, self.enable_web_search)

    def _build_tools_anthropic(self) -> list:
        """构建 Anthropic 格式工具列表（委托给共享模块）。"""
        return build_tools_anthropic(self.plugin_manager, self.enable_web_search)

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
            plugin_tool_names=plugin_tool_names
        )


    _TIMESTAMP_RE = re.compile(
        r'^\[SystemInfo\]当前系统时间：\d{4}年\d{2}月\d{2}日 \d{2}:\d{2}:\d{2}\n'
    )

    def _strip_timestamp_prefix(self, content: str) -> str:
        """剥离 _build_full_messages 注入的 [SystemInfo] 时间戳前缀。
        确保存储的会话历史是干净的原始内容，避免前缀累积。
        """
        return self._TIMESTAMP_RE.sub('', content, count=1)


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


    def stop(self):
        self._stop_event.set()
        self._user_reply_event.set()

        # ★ 僵尸进程防护：关闭 HTTP 客户端传输层
        # 中断阻塞中的流式 API 请求，让线程能从网络等待中恢复
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

        if self.plugin_manager:
            self.plugin_manager.fire_agent_shutdown()
            self.plugin_manager.shutdown()  # reap abandoned hook threads
        if self._sandbox:
            self._sandbox.stop()

    def provide_user_input(self, text: str):
        self._user_reply_value = text
        self._user_reply_event.set()

    def _wait_for_user_input(self) -> str:
        """等待用户输入。期间冻结超时计时器。

        ★ 死锁修复：threading.Event.wait() 原本无超时，若前端事件丢失
        （确认框/输入框未弹出）会导致永久挂起，表现为"一直等待回复"。
        加入 30 分钟硬超时兜底；超时视为停止任务，与异步版行为对齐。
        """
        self._pause_timer()
        self._user_reply_event.clear()
        got_reply = self._user_reply_event.wait(timeout=1800.0)  # 30 分钟硬超时
        self._resume_timer()
        if self._stop_event.is_set():
            return ""
        if not got_reply:
            # 用户交互超时：视为停止任务
            self._stop_event.set()
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
        return self._user_reply_value


    def _confirm_write_delete(self, tool_name: str, tool_args: dict) -> bool:
        """弹出确认对话框，返回 True 表示用户确认，False 表示取消/停止。

        兼容「不再显示」令牌（__confirm_no_more__）：视为确认。
        （loop.py 为遗留同步路径，持久化关闭由 async_loop 负责。）
        """
        confirm_data = json.dumps({
            "tool": tool_name,
            "path": tool_args.get("path", "")
        }, ensure_ascii=False)
        self.event_queue.put(f"WC:{confirm_data}")
        reply = self._wait_for_user_input()
        if self._stop_event.is_set():
            return False
        return reply.strip() in ("__confirm__", "__confirm_no_more__")


    def get_last_usage(self) -> dict:
        """返回最近一次 API 调用的 token 用量。"""
        if self._last_usage:
            return self._last_usage.copy()
        return {}

    def get_total_usage(self) -> dict:
        """返回累计 token 用量（含工具调用估算）。"""
        return self._total_usage.copy()

    def get_conversation_history(self) -> list:
        """返回当前会话的对话历史（tools 操作链，不含用户提问）。
        用于多轮对话上下文回传。包含 assistant（含 tool_calls、reasoning_content）
        和 tool 消息，不包含 user 消息。
        """
        return self._conversation_history.copy()

    def _update_usage(self, input_tokens: int, output_tokens: int):
        """更新 API token 用量并发送事件到前端。"""
        self._last_usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens
        }
        self._total_usage["input_tokens"] += input_tokens
        self._total_usage["output_tokens"] += output_tokens
        self._send_usage_event()

    def _add_tool_tokens(self, tool_name: str, tool_result: str):
        """估算工具调用返回结果的 token 消耗并累加。
        这些内容会在下一轮 API 调用中作为 input 消耗 token。
        """
        est = estimate_tokens(tool_result)
        self._total_usage["tool_call_tokens"] += est
        self._send_usage_event()

    def _send_usage_event(self):
        """发送 token 用量事件到前端。"""
        usage_event = json.dumps({
            "input_tokens": self._total_usage["input_tokens"],
            "output_tokens": self._total_usage["output_tokens"],
            "tool_call_tokens": self._total_usage["tool_call_tokens"]
        }, ensure_ascii=False)
        self.event_queue.put(f"U:{usage_event}")
        if self.plugin_manager:
            self.plugin_manager.fire_usage_update(self._total_usage.copy())

    # 插件工具执行超时（秒）
    PLUGIN_TOOL_TIMEOUT = 120.0

    def _execute_tool(self, tool_name: str, tool_args: dict) -> str:
        """Execute a tool, routing to plugin or built-in executor.

        ★ 插件 execute() 是同步函数，可能在网络 IO / 文件读写 / 子进程上阻塞。
        用 concurrent.futures 包装，加上硬超时防止永久挂起。
        """
        # Check if it's a plugin tool first
        if self.plugin_manager:
            plugin_tools = self.plugin_manager.get_tools()
            plugin_names = {t["function"]["name"] for t in plugin_tools}
            if tool_name in plugin_names:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        self.plugin_manager.execute, tool_name, tool_args
                    )
                    try:
                        return future.result(timeout=self.PLUGIN_TOOL_TIMEOUT)
                    except concurrent.futures.TimeoutError:
                        return (
                            f"Error: plugin tool '{tool_name}' timed out "
                            f"after {self.PLUGIN_TOOL_TIMEOUT:.0f}s"
                        )
        return self.executor.execute(tool_name, tool_args)


    def _log_tool_call(self, step: int, tool_name: str, args: dict, result: str):
        """将工具调用记录保存为 JSONL 格式（与 config.json 同目录）。"""
        if not self.tool_log_path:
            return
        try:
            result_summary = result[:500] + "..." if len(result) > 500 else result
            record = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "step": step,
                "tool": tool_name,
                "args": args,
                "result_length": len(result),
                "tokens_estimate": estimate_tokens(result),
                "result_summary": result_summary
            }
            with open(self.tool_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass  


    def _get_thinking_extra_body(self) -> dict:
        """返回 thinking extra_body 配置（委托给共享模块）。"""
        return get_thinking_extra_body(self.think_level)

    def _get_reasoning_effort(self) -> Optional[str]:
        """返回 reasoning_effort 值（委托给共享模块）。"""
        return get_reasoning_effort(self.think_level)


    def run(self, user_message: str, history: Optional[List[Dict]] = None,
            memory_content: str = "") -> str:
        self._step_count = 0
        self._last_usage = None
        self._total_usage = {"input_tokens": 0, "output_tokens": 0, "tool_call_tokens": 0}
        self._messages = []
        self._memory_content = memory_content  

        self._task_start_time = time.time()
        self._total_pause_duration = 0.0
        self._pause_start_time = 0.0

        # ── Hook: task started ──
        if self.plugin_manager:
            self.plugin_manager.fire_task_start(user_message)

        try:
            if self._use_responses_api:
                # DeepSeek V4 Flash 原生 Responses API
                result = self._run_responses(user_message, history)
            elif self._use_anthropic_search:
                result = self._run_anthropic(user_message, history)
            else:
                result = self._run_openai(user_message, history)
        except Exception as e:
            if self.plugin_manager:
                self.plugin_manager.fire_task_error(str(e))
            raise

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

        # ── Hook: task done / stopped / timeout ──
        if self.plugin_manager:
            if result == "stopped":
                self.plugin_manager.fire_task_stopped()
            elif result == "timeout":
                self.plugin_manager.fire_task_timeout(self._get_elapsed())
            elif result == "max_steps":
                pass  # neither done nor error
            else:
                self.plugin_manager.fire_task_done(
                    summary=result[:200] if result else "",
                    final_reply=result or "")

        return result


    def _run_openai(self, user_message: str, history: Optional[List[Dict]] = None) -> str:
        """使用 OpenAI SDK 调用 DeepSeek API。

        关键规则（来自 DeepSeek 官方文档）：
        1. 流式处理时 reasoning_content 和 content 互斥，使用 if-else 处理
        2. 工具调用场景下，每轮子请求都必须回传 reasoning_content
        3. reasoning_effort 作为顶级参数传递，thinking 放在 extra_body 中
        4. 无工具调用的最终轮次，reasoning_content 无需回传（下一轮会被忽略）
        5. API 是无状态的，每次请求必须携带完整上下文（messages）
        """
        self._stop_event.clear()
        self.event_queue.reset()

        memory_content = getattr(self, '_memory_content', '')
        messages = self._build_full_messages(user_message, history, memory_content=memory_content)
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

            # ── Hook: before_step ──
            if self.plugin_manager:
                messages = self.plugin_manager.fire_before_step(
                    self._step_count, messages)

            api_params = {
                "model": self.model,
                "messages": messages,
                "tools": active_tools,
                "stream": True,
                "max_tokens": self.max_tokens
            }

            if self._is_deepseek_official and not self._is_local_mode:
                api_params["stream_options"] = {"include_usage": True}

            if reasoning_effort is not None and not self._is_local_mode:
                api_params["reasoning_effort"] = reasoning_effort

            if not self._is_local_mode:
                api_params["extra_body"] = thinking_extra_body
            else:
                # ── 本地部署模式优化（Ollama 等）──
                api_params["temperature"] = self.temperature
                if self._is_ollama:
                    # Ollama 专用：保持模型常驻 + 扩展上下文窗口
                    num_ctx = min(
                        max(self.max_tokens + 4096, LOCAL_NUM_CTX_MIN),
                        LOCAL_NUM_CTX_MAX,
                    )
                    api_params["extra_body"] = {
                        "keep_alive": "30m",
                        "options": {"num_ctx": num_ctx},
                    }
                    api_params["max_tokens"] = min(self.max_tokens, num_ctx - 1024)

            if self.think_level == "关" and not self._is_local_mode:
                api_params["temperature"] = self.temperature

            stream = self.client.chat.completions.create(**api_params)

            reasoning_parts = []
            content_parts = []
            tool_calls_accum = {}
            stream_usage = None

            # Throttle counters for streaming hooks (every ~100ms)
            _last_ts_fire = 0.0
            _reasoning_buf = ""
            _content_buf = ""
            _thinking_event_buf = ""  # 批量缓冲 T: 事件，避免碎片化
            _output_started = False  # track when output starts to flush thinking

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
                                "id": tc.id or "",
                                "name": "",
                                "arguments": ""
                            }
                        if tc.id:
                            tool_calls_accum[idx]["id"] = tc.id
                        if tc.function and tc.function.name:
                            tool_calls_accum[idx]["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            tool_calls_accum[idx]["arguments"] += tc.function.arguments

                if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    reasoning_parts.append(delta.reasoning_content)
                    _reasoning_buf += delta.reasoning_content
                    _thinking_event_buf += delta.reasoning_content
                    if len(_thinking_event_buf) >= 80:
                        self.event_queue.put(f"T:{_thinking_event_buf}")
                        _thinking_event_buf = ""
                if hasattr(delta, 'content') and delta.content:
                    # First output chunk → tell front-end to finalize thinking block
                    if not _output_started:
                        _output_started = True
                        if _thinking_event_buf:
                            self.event_queue.put(f"T:{_thinking_event_buf}")
                            _thinking_event_buf = ""
                        self.event_queue.put("F:")
                    content_parts.append(delta.content)
                    self.event_queue.put(f"R:{delta.content}")
                    _content_buf += delta.content

                # Throttled streaming hooks
                now = time.time()
                if self.plugin_manager and now - _last_ts_fire > 0.1:
                    if _reasoning_buf:
                        self.plugin_manager.fire_reasoning(_reasoning_buf)
                        _reasoning_buf = ""
                    if _content_buf:
                        self.plugin_manager.fire_content(_content_buf)
                        _content_buf = ""
                    _last_ts_fire = now

            if _thinking_event_buf:
                self.event_queue.put(f"T:{_thinking_event_buf}")
            # Flush remaining streaming tokens
            if self.plugin_manager:
                if _reasoning_buf:
                    self.plugin_manager.fire_reasoning(_reasoning_buf)
                if _content_buf:
                    self.plugin_manager.fire_content(_content_buf)

            if self._stop_event.is_set():
                self.event_queue.put("E:Task stopped by user")
                self.event_queue.signal_finish()
                return "stopped"

            if stream_usage:
                self._update_usage(
                    stream_usage["input_tokens"],
                    stream_usage["output_tokens"]
                )
            else:
                estimated_input = sum(len(str(m.get("content", ""))) for m in messages) // 4
                estimated_output = len("".join(content_parts)) // 4
                self._update_usage(estimated_input, estimated_output)

            full_reasoning = "".join(reasoning_parts)
            full_content = "".join(content_parts)

            # ── Hook: after_step ──
            if self.plugin_manager:
                self.plugin_manager.fire_after_step(
                    self._step_count, full_reasoning, full_content,
                    tool_calls_accum)

            assistant_msg = {"role": "assistant", "content": full_content}

            if full_reasoning:
                assistant_msg["reasoning_content"] = full_reasoning

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

            if tool_calls_list:
                assistant_msg["tool_calls"] = tool_calls_list
                for tc in tool_calls_list:
                    cmd_info = json.dumps({
                        "tool": tc["function"]["name"],
                        "args": json.loads(tc["function"]["arguments"])
                    }, ensure_ascii=False)
                    self.event_queue.put(f"C:{cmd_info}")

            messages.append(assistant_msg)

            if not tool_calls_list:
                if full_content:
                    self.event_queue.put(f"D:{full_content}")

                self.event_queue.signal_finish()
                return full_content

            status = self._process_tool_calls_openai(messages, tool_calls_list, step)
            if status == "stopped":
                return "stopped"

        self.event_queue.put("E:Max steps reached, task incomplete")
        self.event_queue.signal_finish()
        return "max_steps"


    def _process_tool_calls_openai(self, messages: list, tool_calls_list: list,
                                   step: int) -> Optional[str]:
        """执行 OpenAI 格式的 tool_calls 并把结果回传到 messages。

        Chat Completions 与 Responses API 两条路径共用：
        - 插件钩子（before/after_tool_call、user_input_required）
        - ask_user 交互（等待用户输入）
        - 写/删文件确认
        - 工具执行、日志记录、token 估算
        返回 "stopped" 表示用户停止任务，None 表示正常完成。
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
                    self._log_tool_call(step + 1, tool_name, tool_args, blocked_msg)
                    continue
                tool_args = modified_args

            if tool_name == "ask_user":
                question = tool_args.get("question", "")
                # ── Hook: user_input_required ──
                if self.plugin_manager:
                    self.plugin_manager.fire_user_input_required(question)
                self.event_queue.put(f"Q:{json.dumps(question, ensure_ascii=False)}")
                reply = self._wait_for_user_input()
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
                self._log_tool_call(step + 1, tool_name, tool_args, f"user replied: {reply[:200]}")
                self._add_tool_tokens(tool_name, reply)
            else:
                if (tool_name in ("write_file", "delete_file", "replace_in_file")
                        and self.confirm_write_delete):
                    if not self._confirm_write_delete(tool_name, tool_args):
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
                        self._log_tool_call(step + 1, tool_name, tool_args, cancel_msg)
                        continue

                result = self._execute_tool(tool_name, tool_args)

                # ── Hook: after_tool_call ──
                if self.plugin_manager:
                    result = self.plugin_manager.fire_after_tool_call(
                        tool_name, tool_args, result)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result
                })
                self._log_tool_call(step + 1, tool_name, tool_args, result)
                self._add_tool_tokens(tool_name, result)

        return None


    def _build_responses_tools(self) -> list:
        """构建 Responses API 工具列表（委托给共享模块）。"""
        return build_responses_tools(self.plugin_manager, self.enable_web_search)

    def _build_responses_input(self, messages: list) -> list:
        """将 OpenAI 格式 messages 转换为 Responses API 的 input items（委托给共享模块）。"""
        return build_responses_input(messages)


    def _run_responses(self, user_message: str, history: Optional[List[Dict]] = None) -> str:
        """使用 OpenAI SDK 的 Responses API 原生调用 DeepSeek V4 Flash。

        DeepSeek V4 Flash 正式版原生支持 Responses API：
        1. 语义化流式事件：response.reasoning_text.delta / response.output_text.delta
           / response.function_call_arguments.delta / response.output_item.done
           / response.completed（携带 usage）
        2. 推理控制使用顶层 reasoning={"effort": ...} 参数（取代 extra_body.thinking）
        3. web_search 为服务端原生工具（开启联网搜索时自动启用，客户端无需执行）
        4. 无状态 API：每次请求必须携带完整上下文（input items）
        5. 不回传历史：每次请求仅携带当前用户消息和系统提示，不附带会话历史
        """
        self._stop_event.clear()
        self.event_queue.reset()

        memory_content = getattr(self, '_memory_content', '')
        # 不回传历史：history 参数被忽略，确保 Responses API 每次调用都是独立的
        messages = self._build_full_messages(user_message, None, memory_content=memory_content)
        self._messages = messages

        # 工具列表：Responses API 模式下 web_search 使用服务端原生工具
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

            # ── Hook: before_step ──
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

            stream = self.client.responses.create(**api_params)

            reasoning_parts = []
            content_parts = []
            tool_calls_accum = {}   # item_id -> {call_id, name, arguments}
            web_search_calls = []   # 服务端原生 web_search 调用记录
            stream_usage = None

            # Throttle counters for streaming hooks (every ~100ms)
            _last_ts_fire = 0.0
            _reasoning_buf = ""
            _content_buf = ""
            _thinking_event_buf = ""  # 批量缓冲 T: 事件，避免碎片化
            _output_started = False  # track when output starts to flush thinking

            for event in stream:
                if self._stop_event.is_set():
                    break

                et = event.type

                if et == "response.reasoning_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    reasoning_parts.append(delta)
                    _reasoning_buf += delta
                    _thinking_event_buf += delta
                    if len(_thinking_event_buf) >= 80:
                        self.event_queue.put(f"T:{_thinking_event_buf}")
                        _thinking_event_buf = ""
                elif et == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    # First output chunk → tell front-end to finalize thinking block
                    if not _output_started:
                        _output_started = True
                        if _thinking_event_buf:
                            self.event_queue.put(f"T:{_thinking_event_buf}")
                            _thinking_event_buf = ""
                        self.event_queue.put("F:")
                    content_parts.append(delta)
                    self.event_queue.put(f"R:{delta}")
                    _content_buf += delta
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

                # Throttled streaming hooks
                now = time.time()
                if self.plugin_manager and now - _last_ts_fire > 0.1:
                    if _reasoning_buf:
                        self.plugin_manager.fire_reasoning(_reasoning_buf)
                        _reasoning_buf = ""
                    if _content_buf:
                        self.plugin_manager.fire_content(_content_buf)
                        _content_buf = ""
                    _last_ts_fire = now

            if _thinking_event_buf:
                self.event_queue.put(f"T:{_thinking_event_buf}")
            # Flush remaining streaming tokens
            if self.plugin_manager:
                if _reasoning_buf:
                    self.plugin_manager.fire_reasoning(_reasoning_buf)
                if _content_buf:
                    self.plugin_manager.fire_content(_content_buf)

            if self._stop_event.is_set():
                self.event_queue.put("E:Task stopped by user")
                self.event_queue.signal_finish()
                return "stopped"

            if stream_usage:
                self._update_usage(
                    stream_usage["input_tokens"],
                    stream_usage["output_tokens"]
                )
            else:
                estimated_input = sum(len(str(m.get("content", ""))) for m in messages) // 4
                estimated_output = len("".join(content_parts)) // 4
                self._update_usage(estimated_input, estimated_output)

            full_reasoning = "".join(reasoning_parts)
            full_content = "".join(content_parts)

            # ── Hook: after_step ──
            if self.plugin_manager:
                self.plugin_manager.fire_after_step(
                    self._step_count, full_reasoning, full_content,
                    tool_calls_accum)

            assistant_msg = {"role": "assistant", "content": full_content}

            if full_reasoning:
                assistant_msg["reasoning_content"] = full_reasoning

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

            # 服务端原生 web_search 调用：记录到前端日志，并回传上下文
            for wc in web_search_calls:
                query = getattr(wc, "query", "") or ""
                cmd_info = json.dumps({
                    "tool": "web_search (native Responses API)",
                    "args": {"query": query}
                }, ensure_ascii=False)
                self.event_queue.put(f"C:{cmd_info}")
            if web_search_calls:
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

            if not tool_calls_list:
                if full_content:
                    self.event_queue.put(f"D:{full_content}")

                self.event_queue.signal_finish()
                return full_content

            status = self._process_tool_calls_openai(messages, tool_calls_list, step)
            if status == "stopped":
                return "stopped"

        self.event_queue.put("E:Max steps reached, task incomplete")
        self.event_queue.signal_finish()
        return "max_steps"


    def _run_anthropic(self, user_message: str, history: Optional[List[Dict]] = None) -> str:
        """使用 Anthropic SDK 调用 DeepSeek Anthropic 兼容端点。
        web_search 作为 Anthropic 原生工具由 API 端自动处理，
        其他自定义工具在客户端执行。
        """
        self._stop_event.clear()
        self.event_queue.reset()

        system_prompt = self._build_system_prompt()

        memory_content = getattr(self, '_memory_content', '')
        if memory_content:
            system_prompt += "\n\n" + memory_content

        openai_messages = self._build_full_messages(user_message, history, memory_content=memory_content)
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

            # ── Hook: before_step ──
            if self.plugin_manager:
                self.plugin_manager.fire_before_step(self._step_count, anthropic_messages)

            result = self._call_anthropic_stream(
                messages=anthropic_messages,
                system_prompt=system_prompt,
                tools=all_tools,
                thinking=thinking_param,
                output_config=output_config_param
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
                self._update_usage(
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0)
                )
            else:
                estimated_input = sum(len(str(m.get("content", ""))) for m in anthropic_messages) // 4
                estimated_output = len(full_content) // 4
                self._update_usage(estimated_input, estimated_output)

            # ── Hook: after_step ──
            if self.plugin_manager:
                self.plugin_manager.fire_after_step(
                    self._step_count, full_reasoning, full_content,
                    tool_uses)

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
                        blocked_msg = "Tool call blocked by plugin."
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": blocked_msg
                        })
                        self._log_tool_call(step + 1, tool_name, tool_input, blocked_msg)
                        continue
                    tool_input = modified_input

                if tool_name == "ask_user":
                    question = tool_input.get("question", "")
                    # ── Hook: user_input_required ──
                    if self.plugin_manager:
                        self.plugin_manager.fire_user_input_required(question)
                    self.event_queue.put(f"Q:{json.dumps(question, ensure_ascii=False)}")
                    reply = self._wait_for_user_input()
                    if self._stop_event.is_set():
                        self.event_queue.put("E:Task stopped by user")
                        self.event_queue.signal_finish()
                        return "stopped"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": reply
                    })
                    self._log_tool_call(step + 1, tool_name, tool_input, f"user replied: {reply[:200]}")
                    self._add_tool_tokens(tool_name, reply)
                else:
                    if (tool_name in ("write_file", "delete_file", "replace_in_file")
                            and self.confirm_write_delete):
                        if not self._confirm_write_delete(tool_name, tool_input):
                            if self._stop_event.is_set():
                                self.event_queue.put("E:Task stopped by user")
                                self.event_queue.signal_finish()
                                return "stopped"
                            cancel_msg = "User cancelled the operation."
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": cancel_msg
                            })
                            self._log_tool_call(step + 1, tool_name, tool_input, cancel_msg)
                            continue

                    result_text = self._execute_tool(tool_name, tool_input)

                    # ── Hook: after_tool_call ──
                    if self.plugin_manager:
                        result_text = self.plugin_manager.fire_after_tool_call(
                            tool_name, tool_input, result_text)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result_text
                    })
                    self._log_tool_call(step + 1, tool_name, tool_input, result_text)
                    self._add_tool_tokens(tool_name, result_text)

            if tool_results:
                anthropic_messages.append({
                    "role": "user",
                    "content": tool_results
                })

        self.event_queue.put("E:Max steps reached, task incomplete")
        self.event_queue.signal_finish()
        return "max_steps"

    def _call_anthropic_stream(
        self,
        messages: list,
        system_prompt: str,
        tools: list,
        thinking=None,
        output_config=None
    ) -> Optional[dict]:
        reasoning_parts = []       
        thinking_blocks = []       
        content_parts = []         
        tool_uses = []              
        usage = None                

        _current_thinking_text = ""  
        _current_thinking_sig = ""

        _output_started = False  # track when output starts to flush thinking

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
                # Throttle for streaming hooks
                _last_ts_fire = 0.0
                _reasoning_buf = ""
                _content_buf = ""
                _thinking_event_buf = ""  # 独立缓冲区：批量发送 T: 事件，避免碎片化

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
                            _current_thinking_text = getattr(cb, 'thinking', '') or ''
                            _current_thinking_sig = getattr(cb, 'signature', '') or ''
                        elif cb.type == "redacted_thinking":
                            thinking_blocks.append({
                                "type": "redacted_thinking",
                                "data": getattr(cb, 'data', '') or ''
                            })

                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if hasattr(delta, 'thinking') and delta.thinking:
                            reasoning_parts.append(delta.thinking)
                            _current_thinking_text += delta.thinking
                            _reasoning_buf += delta.thinking
                            _thinking_event_buf += delta.thinking
                            # 本地缓冲：累积到 80 字符再发送，避免前端收到碎片化的 T: 事件
                            # （Anthropic delta.thinking 粒度可能极细，一个词就一个 delta）
                            if len(_thinking_event_buf) >= 80:
                                self.event_queue.put(f"T:{_thinking_event_buf}")
                                _thinking_event_buf = ""
                        if hasattr(delta, 'signature') and delta.signature:
                            _current_thinking_sig = delta.signature
                        if hasattr(delta, 'text') and delta.text:
                            # First output chunk → flush thinking buffer & finalize thinking block
                            if not _output_started:
                                _output_started = True
                                if _thinking_event_buf:
                                    self.event_queue.put(f"T:{_thinking_event_buf}")
                                    _thinking_event_buf = ""
                                self.event_queue.put("F:")
                            content_parts.append(delta.text)
                            self.event_queue.put(f"R:{delta.text}")
                            _content_buf += delta.text

                        # Throttled streaming hooks
                        now = time.time()
                        if self.plugin_manager and now - _last_ts_fire > 0.1:
                            if _reasoning_buf:
                                self.plugin_manager.fire_reasoning(_reasoning_buf)
                                _reasoning_buf = ""
                            if _content_buf:
                                self.plugin_manager.fire_content(_content_buf)
                                _content_buf = ""
                            _last_ts_fire = now

                    elif event.type == "content_block_stop":
                        if hasattr(event, 'content_block') and event.content_block:
                            cb = event.content_block
                            if cb.type == "thinking":
                                # Flush 残留的 thinking 缓冲区
                                if _thinking_event_buf:
                                    self.event_queue.put(f"T:{_thinking_event_buf}")
                                    _thinking_event_buf = ""
                                final_text = getattr(cb, 'thinking', '') or _current_thinking_text
                                final_sig = getattr(cb, 'signature', '') or _current_thinking_sig
                                thinking_blocks.append({
                                    "type": "thinking",
                                    "thinking": final_text,
                                    "signature": final_sig
                                })
                                _current_thinking_text = ""
                                _current_thinking_sig = ""
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

                # Flush remaining streaming tokens
                if _thinking_event_buf:
                    self.event_queue.put(f"T:{_thinking_event_buf}")
                if self.plugin_manager:
                    if _reasoning_buf:
                        self.plugin_manager.fire_reasoning(_reasoning_buf)
                    if _content_buf:
                        self.plugin_manager.fire_content(_content_buf)

        except Exception as e:
            self.event_queue.put(f"E:Anthropic API call failed: {str(e)}")
            return {
                "reasoning": "",
                "content": "",
                "thinking_blocks": [],
                "tool_uses": [],
                "usage": None
            }

        full_reasoning = "".join(reasoning_parts)
        full_content = "".join(content_parts)

        return {
            "reasoning": full_reasoning,
            "content": full_content,
            "thinking_blocks": thinking_blocks,
            "tool_uses": tool_uses,
            "usage": usage
        }

    def _convert_openai_messages_to_anthropic(self, messages: list) -> list:
        """将 OpenAI 格式消息转换为 Anthropic 格式（委托给共享模块）。"""
        return convert_openai_messages_to_anthropic(messages)

    def _convert_history_to_anthropic(self, history: List[Dict]) -> list:
        result = []
        i = 0
        while i < len(history):
            msg = history[i]
            role = msg.get("role", "")

            if role == "system":
                i += 1
                continue

            elif role == "user":
                result.append({"role": "user", "content": msg.get("content", "")})
                i += 1

            elif role == "assistant":
                content_blocks = []


                text = msg.get("content", "")
                if text:
                    content_blocks.append({"type": "text", "text": text})

                tool_calls = msg.get("tool_calls", [])
                i += 1

                tool_result_ids = set()
                j = i
                while j < len(history) and history[j].get("role") == "tool":
                    tool_result_ids.add(history[j].get("tool_call_id", ""))
                    j += 1

                valid_tool_calls = []
                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    if tc_id in tool_result_ids:
                        valid_tool_calls.append(tc)

                for tc in valid_tool_calls:
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except (json.JSONDecodeError, KeyError):
                        args = {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": tc["function"]["name"],
                        "input": args
                    })

                result.append({"role": "assistant", "content": content_blocks})

                tool_results = []
                while i < len(history) and history[i].get("role") == "tool":
                    tm = history[i]
                    tc_id = tm.get("tool_call_id", "")
                    if tc_id in tool_result_ids:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc_id,
                            "content": tm.get("content", "")
                        })
                    i += 1

                if tool_results:
                    result.append({"role": "user", "content": tool_results})

            else:
                i += 1

        return result

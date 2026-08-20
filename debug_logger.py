# Vibe Coding Agent - 调试数据收集器 (Debug Logger)
# 实时采集 Agent 运行时的 ReAct 时间线 / 工具调用详情 / 钩子触发 / 性能快照，
# 任务结束后写入 "{日期+时间}_debug.json"，供前端「调试」面板展示。
# Copyright (c) 2026 xingluosama

import copy
import json
import os
import threading
import time
from datetime import datetime


# ── 插件钩子分层映射（4 层 16 钩，与 plugin_system/manager.py HOOK_NAMES 对齐）──
HOOK_LAYERS = {
    # L1 – Lifecycle（生命周期层）
    "on_agent_init": "L1",
    "on_agent_shutdown": "L1",
    # L2 – Task（任务层）
    "on_task_start": "L2",
    "on_task_done": "L2",
    "on_task_error": "L2",
    "on_task_stopped": "L2",
    "on_task_timeout": "L2",
    # L3 – Step（步骤层）
    "before_step": "L3",
    "after_step": "L3",
    "before_tool_call": "L3",
    "after_tool_call": "L3",
    "on_user_input_required": "L3",
    # L4 – Streaming（流式事件层）
    "on_reasoning": "L4",
    "on_content": "L4",
    "on_event": "L4",
    "on_usage_update": "L4",
}

# 各列表的上限，防止长任务撑爆内存
MAX_REACT_STEPS = 500
MAX_TOOL_CALLS = 2000
MAX_HOOK_EVENTS = 3000


def _truncate(obj, limit=5000):
    """将对象转成字符串并截断，避免超大内容撑爆日志文件。"""
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    if len(s) > limit:
        return s[:limit] + f"... (truncated, total {len(s)} chars)"
    return obj  # 返回原对象（dict/list），保留结构


class DebugLogger:
    """线程安全的调试数据收集器（全局单例）。

    由于 Agent 循环在后台线程运行、API 层在主线程查询，所有读写都用
    threading.Lock 保护。数据在内存中累积，任务结束时 flush 到 JSON 文件。
    """

    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def get(cls, app_dir: str = "") -> "DebugLogger":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = DebugLogger(app_dir)
            elif app_dir and not cls._instance.app_dir:
                cls._instance.app_dir = app_dir
            return cls._instance

    def __init__(self, app_dir: str = ""):
        self.app_dir = app_dir
        self._lock = threading.Lock()
        self._current_step = 0
        self._start_time = 0.0
        self._reset()

    def _reset(self):
        self._data = {
            "task_id": "",
            "user_message": "",
            "project_root": "",
            "started_at": "",
            "finished_at": "",
            "final_result": "",
            "react_steps": [],
            "tool_calls": [],
            "hook_events": [],
            "snapshot": {
                "tokens": {"input_tokens": 0, "output_tokens": 0, "tool_call_tokens": 0},
                "event_queue_size": 0,
                "sandbox_pool": {},
                "file_io_queue": {},
            },
        }

    # ═══════════════════════════════════════════════════════════════
    #  任务生命周期
    # ═══════════════════════════════════════════════════════════════

    def start_task(self, task_id: str, user_message: str, project_root: str):
        with self._lock:
            self._reset()
            self._data["task_id"] = task_id
            self._data["user_message"] = (user_message or "")[:500]
            self._data["project_root"] = project_root or ""
            self._data["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._start_time = time.time()
            self._current_step = 0

    def set_current_step(self, step: int):
        with self._lock:
            self._current_step = step

    def finish_task(self, result: str):
        with self._lock:
            self._data["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._data["final_result"] = (result or "")[:500]
        self.flush_to_file()

    # ═══════════════════════════════════════════════════════════════
    #  模块 1：ReAct 循环时间线
    # ═══════════════════════════════════════════════════════════════

    def record_react_step(self, step: int, reasoning: str, tool_calls: list,
                          observations: list):
        """记录一步 ReAct「思考-行动-观察」闭环。

        Args:
            step: 第几步 ReAct 循环（从 1 开始）
            reasoning: LLM 的思考链（reasoning_content）
            tool_calls: 本步 LLM 决定的工具调用 [{"name":..., "arguments":...}]
            observations: 本步工具执行后的观察结果 [{"tool":..., "result":...}]
        """
        with self._lock:
            elapsed_ms = int((time.time() - self._start_time) * 1000) if self._start_time else 0
            record = {
                "step": step,
                "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "elapsed_ms": elapsed_ms,
                "reasoning": (reasoning or "")[:8000],
                "tool_calls": tool_calls,
                "observations": [
                    {"tool": o.get("tool", ""), "result": (o.get("result", "") or "")[:4000]}
                    for o in (observations or [])
                ],
            }
            self._data["react_steps"].append(record)
            if len(self._data["react_steps"]) > MAX_REACT_STEPS:
                self._data["react_steps"] = self._data["react_steps"][-MAX_REACT_STEPS:]

    def append_observation(self, step: int, tool: str, result: str):
        """向指定 ReAct 步骤追加一条观察结果（工具执行返回）。

        从后往前找第一条 step 匹配的记录（step 单调递增，最后一条即最新）。
        """
        with self._lock:
            for record in reversed(self._data["react_steps"]):
                if record.get("step") == step:
                    record["observations"].append({
                        "tool": tool,
                        "result": (result or "")[:4000],
                    })
                    return

    # ═══════════════════════════════════════════════════════════════
    #  模块 2：工具调用详情（诊断报告卡）
    # ═══════════════════════════════════════════════════════════════

    def record_tool_call(self, tool: str, args: dict, result: str,
                         elapsed_ms: float, sandbox_paths: dict = None,
                         step: int = None, blocked: bool = False,
                         blocked_reason: str = ""):
        """记录一次工具调用的完整诊断信息。

        Args:
            tool: 工具名称
            args: 调用入参
            result: 返回的原始结果
            elapsed_ms: 执行耗时（毫秒，可带小数）
            sandbox_paths: 宿主路径 ↔ 沙箱路径映射 {"host": ..., "sandbox": ...}
            step: 所属 ReAct 步骤（缺省用当前步骤）
            blocked: 是否被安全/插件拦截
            blocked_reason: 拦截原因
        """
        with self._lock:
            if step is None:
                step = self._current_step
            record = {
                "step": step,
                "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "tool": tool,
                "args": _truncate(args, 4000),
                "result": (result or "")[:6000],
                "elapsed_ms": round(elapsed_ms, 3),
                "sandbox_paths": sandbox_paths or {},
                "blocked": blocked,
                "blocked_reason": blocked_reason,
            }
            self._data["tool_calls"].append(record)
            if len(self._data["tool_calls"]) > MAX_TOOL_CALLS:
                self._data["tool_calls"] = self._data["tool_calls"][-MAX_TOOL_CALLS:]

    # ═══════════════════════════════════════════════════════════════
    #  模块 4：插件钩子触发记录
    # ═══════════════════════════════════════════════════════════════

    def record_hook(self, hook_name: str, plugin_name: str, action: str = "fired",
                    before: dict = None, after: dict = None):
        """记录一次插件钩子触发。

        Args:
            hook_name: 钩子名（如 before_tool_call）
            plugin_name: 插件名
            action: 动作类型（fired=触发 / mutated=修改了数据 / blocked=拦截）
            before: 数据变形前的值（可变钩子）
            after: 数据变形后的值（可变钩子）
        """
        with self._lock:
            layer = HOOK_LAYERS.get(hook_name, "?")
            record = {
                "step": self._current_step,
                "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "layer": layer,
                "hook": hook_name,
                "plugin": plugin_name,
                "action": action,
            }
            if before is not None or after is not None:
                record["before"] = _truncate(before)
                record["after"] = _truncate(after)
            self._data["hook_events"].append(record)
            if len(self._data["hook_events"]) > MAX_HOOK_EVENTS:
                self._data["hook_events"] = self._data["hook_events"][-MAX_HOOK_EVENTS:]

    # ═══════════════════════════════════════════════════════════════
    #  模块 5：性能与状态快照
    # ═══════════════════════════════════════════════════════════════

    def update_tokens(self, usage: dict):
        with self._lock:
            self._data["snapshot"]["tokens"] = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "tool_call_tokens": usage.get("tool_call_tokens", 0),
            }

    def update_event_queue_size(self, size: int):
        with self._lock:
            self._data["snapshot"]["event_queue_size"] = size

    def _collect_runtime_snapshot(self) -> dict:
        """实时采集沙箱池 / 文件 I/O 队列状态（全局单例，随时可读）。"""
        snapshot = {}
        try:
            from sandbox_pool import get_sandbox_pool
            snapshot["sandbox_pool"] = get_sandbox_pool().get_stats()
        except Exception:
            snapshot["sandbox_pool"] = {}
        try:
            from file_io_queue import get_file_io_queue
            snapshot["file_io_queue"] = get_file_io_queue().get_stats()
        except Exception:
            snapshot["file_io_queue"] = {}
        return snapshot

    def _read_security_events(self) -> list:
        """复用 NORP 安全系统已有的拦截日志（NORPsafe.json）。"""
        try:
            from norp_safe import get_norp_safe
            nsp = get_norp_safe(self.app_dir)
            return nsp.get_logs(limit=200)
        except Exception:
            return []

    # ═══════════════════════════════════════════════════════════════
    #  输出
    # ═══════════════════════════════════════════════════════════════

    def get_debug_data(self) -> dict:
        """返回当前内存中的调试数据（供 API 实时查询）。"""
        with self._lock:
            data = copy.deepcopy(self._data)
        # 附加安全拦截日志与实时运行时快照
        data["security_events"] = self._read_security_events()
        runtime = self._collect_runtime_snapshot()
        data["snapshot"]["sandbox_pool"] = runtime.get("sandbox_pool", {})
        data["snapshot"]["file_io_queue"] = runtime.get("file_io_queue", {})
        data["log_dir"] = self.app_dir
        return data

    def flush_to_file(self) -> str:
        """将当前调试数据写入 "{日期+时间}_debug.json"，返回文件路径。"""
        if not self.app_dir:
            return ""
        data = self.get_debug_data()
        filename = datetime.now().strftime("%Y%m%d_%H%M%S") + "_debug.json"
        path = os.path.join(self.app_dir, filename)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            return path
        except Exception:
            return ""


# 便捷函数
def get_debug_logger(app_dir: str = "") -> DebugLogger:
    return DebugLogger.get(app_dir)

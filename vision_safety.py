# Vibe Coding Agent - 视觉/操作安全裁决器（新安全系统，从零设计）
# Copyright (c) 2026 xingluosama
#
# 与现有 LLM 内容安全系统（norp_safe / permission_cascade / jailbreak_guard /
# runtime_check）是两套完全不同的东西，本模块**不复用**它们的任何代码/类/状态机/阈值。
#
# 本模块管的是「物理副作用级」安全：键鼠输入、窗口操作、屏幕信息外泄。
# 设计原则（见 docs/vision_agent_design.md）：
#   - 裁决权唯一归属：Agent 只能「申请」，不能「裁决自己」。
#   - 失效安全 > 失效可控 > 失效危险。
#   - 纯 Python、零副作用、可 dry-run 测试（时钟可注入）。

import time
from dataclasses import dataclass, field
from enum import IntEnum, Enum
from typing import Any, Callable, Dict, List, Optional


class RiskLevel(IntEnum):
    """操作分级（数值越大风险越高，支持比较）。"""
    L0 = 0  # 只读：读控件文本/值、截图、视觉描述
    L1 = 1  # 无副作用点击：切换 tab、展开菜单、移动鼠标
    L2 = 2  # 有副作用写入：填表单、点「删除/发送」
    L3 = 3  # 破坏性/不可逆：清空、提交、关闭应用、写注册表


class CircuitState(Enum):
    CLOSED = "CLOSED"          # 正常放行
    OPEN = "OPEN"              # 熔断：拒绝一切 L1+，只保留 L0 读
    HALF_OPEN = "HALF_OPEN"    # 试探：允许单个 L1，成功→CLOSED，失败→OPEN


@dataclass
class Decision:
    """裁决结果。allowed=False 时看 requires_confirmation / reason。"""
    allowed: bool
    reason: str
    risk: RiskLevel
    op: str
    requires_confirmation: bool = False


@dataclass
class AuditRecord:
    """审计记录（一等公民，与熔断器同等重要）。"""
    ts: float
    op: str
    risk: str
    target_window: Optional[str]
    decision: str          # approved / rejected / requires_confirmation
    reason: str
    user_confirmed: bool


class SafetyArbiter:
    """安全裁决器：裁决权唯一归属。

    只做「决策」，不弹 UI。上层流程：
        1. evaluate(op, risk, ..., user_confirmed=False)
        2. 若返回 requires_confirmation=True → 弹窗问用户
        3. 用户批准后 evaluate(..., user_confirmed=True)
        4. 若 allowed=True → 执行；否则按 reason 停手
    """

    def __init__(
        self,
        *,
        consecutive_vetoes_to_open: int = 3,   # 连续被否决 N 次 → 熔断 OPEN
        cooldown_sec: float = 60.0,            # 熔断冷却时间（冷却期内不可复位）
        idle_timeout_sec: float = 150.0,       # 用户空闲多久判定「离开」
        idle_allow_operate: bool = False,      # 空闲后是否允许 Agent 继续操作（默认锁死）
        delegate_window_sec: float = 300.0,    # 让渡（预授权）时长
        delegate_scope: str = "window",        # 让渡范围：window | app | session
        delegate_max_risk: str = "L2",         # 让渡最多覆盖到哪一级（L3 永不免确认）
        auto_tighten_threshold: int = 3,       # 连续被否决 N 次 → 自动收紧回「事事确认」
        cooldown_after_veto: float = 60.0,     # 被否决后冷却多久才允许再次申请
        max_failures: int = 3,                 # 同一操作连续失败 N 次 → 停手上报
        clock: Callable[[], float] = time.time,
    ):
        self.consecutive_vetoes_to_open = consecutive_vetoes_to_open
        self.cooldown_sec = cooldown_sec
        self.idle_timeout_sec = idle_timeout_sec
        self.idle_allow_operate = idle_allow_operate
        self.delegate_window_sec = delegate_window_sec
        self.delegate_scope = delegate_scope
        self.delegate_max_risk = delegate_max_risk
        self.auto_tighten_threshold = auto_tighten_threshold
        self.cooldown_after_veto = cooldown_after_veto
        self.max_failures = max_failures
        self._clock = clock

        # ── 熔断机状态 ──
        self._circuit: CircuitState = CircuitState.CLOSED
        self._consecutive_vetoes: int = 0
        self._circuit_opened_at: Optional[float] = None
        self._half_open_trial_done: bool = False

        # ── 控制权 / 确认权 ──
        self._override_active: bool = False        # override = 用户接管（Agent 挂起）
        self._agent_operating: bool = False        # Agent 是否正在执行操作序列
        self._delegate: Optional[Dict[str, Any]] = None  # delegate 让渡（预授权）
        self._last_veto_at: Optional[float] = None  # 最近一次被否决时间

        # ── 在场检测 / 介入检测 ──
        self._last_user_input_ts: float = clock()  # 最近用户键鼠输入时间

        # ── 失败收敛（动作-验证-收敛：3 次失败停手） ──
        self._failure_counts: Dict[str, int] = {}

        # ── 审计日志（一等公民） ──
        self._audit: List[AuditRecord] = []

    # ═══════════════════════════════════════════════════════════════
    #  核心裁决
    # ═══════════════════════════════════════════════════════════════

    def evaluate(
        self,
        op: str,
        risk: "RiskLevel",
        target_window: Optional[str] = None,
        user_confirmed: bool = False,
    ) -> Decision:
        """裁决一个操作请求。纯读状态，不改变状态（除 HALF-OPEN 试探标记）。"""
        if isinstance(risk, str):
            risk = RiskLevel[risk]

        decision = self._decide(op, risk, target_window, user_confirmed)
        self._audit.append(AuditRecord(
            ts=self._clock(),
            op=op,
            risk=risk.name,
            target_window=target_window,
            decision=("approved" if decision.allowed
                      else ("requires_confirmation" if decision.requires_confirmation
                            else "rejected")),
            reason=decision.reason,
            user_confirmed=user_confirmed,
        ))
        return decision

    def _decide(self, op: str, risk: RiskLevel, target_window, user_confirmed) -> Decision:
        # 0. 失败收敛：同一操作已连续失败 >= max_failures → 停手上报
        key = f"{op}@{target_window or ''}"
        if self._failure_counts.get(key, 0) >= self.max_failures:
            return Decision(False, f"已失败 {self.max_failures} 次，停手上报等待用户命令", risk, op)

        # 1. override 用户接管 → Agent 挂起（只保留 L0 读）
        if self._override_active and risk is not RiskLevel.L0:
            return Decision(False, "用户接管中（override），Agent 已挂起", risk, op)

        # 2. 在场检测：用户空闲超时且不允许后台 → 锁死（只保留 L0 读）
        if risk is not RiskLevel.L0 and self._idle_locked():
            return Decision(False, "用户空闲超时，已锁死（可在设置中开启 idle_allow_operate）", risk, op)

        # 3. 熔断机
        if self._circuit is CircuitState.OPEN:
            if risk is RiskLevel.L0:
                return Decision(True, "L0 只读，熔断中仍放行", risk, op)
            return Decision(False, "熔断中（OPEN），拒绝一切 L1+ 操作", risk, op)

        if self._circuit is CircuitState.HALF_OPEN:
            if risk is RiskLevel.L0:
                return Decision(True, "L0 只读，试探中仍放行", risk, op)
            if risk is RiskLevel.L1 and not self._half_open_trial_done:
                self._half_open_trial_done = True  # 消耗掉这唯一一次试探
                return Decision(True, "HALF-OPEN 试探：放行单个 L1", risk, op)
            return Decision(False, "熔断试探中（HALF-OPEN），仅允许单个 L1 试探", risk, op)

        # 4. 分级确认（CLOSED 态）
        if risk is RiskLevel.L0:
            return Decision(True, "L0 只读，放行", risk, op)

        if risk is RiskLevel.L3:
            # 永不免确认（delegate 不覆盖 L3），但用户显式双因子确认后可放行
            if user_confirmed:
                return Decision(True, "L3 已用户双因子确认，放行", risk, op)
            return Decision(False, "L3 破坏性操作，需用户双因子确认", risk, op,
                            requires_confirmation=True)

        if risk is RiskLevel.L2:
            if self._delegate_covers(risk, target_window):
                return Decision(True, "delegate 让渡范围内，免确认", risk, op)
            if user_confirmed:
                return Decision(True, "用户已显式确认", risk, op)
            return Decision(False, "L2 有副作用，需用户显式确认", risk, op,
                            requires_confirmation=True)

        if risk is RiskLevel.L1:
            return Decision(True, "L1 无副作用，静默放行", risk, op)

        return Decision(False, "未知风险等级", risk, op)

    # ═══════════════════════════════════════════════════════════════
    #  熔断机状态转移
    # ═══════════════════════════════════════════════════════════════

    def user_veto(self) -> None:
        """用户否决当前操作（L1/L2 静默确认被用户打断也算）。"""
        self._consecutive_vetoes += 1
        self._last_veto_at = self._clock()
        if self._consecutive_vetoes >= self.consecutive_vetoes_to_open:
            self._circuit = CircuitState.OPEN
            self._circuit_opened_at = self._clock()
            self._consecutive_vetoes = 0
            self._half_open_trial_done = False

    def hotkey_emergency_stop(self) -> None:
        """Ctrl+End 物理熔断：最高优先级旁路，立即 OPEN。"""
        self._circuit = CircuitState.OPEN
        self._circuit_opened_at = self._clock()
        self._half_open_trial_done = False

    def manual_reset(self) -> Decision:
        """用户手动复位熔断：冷却期过后 OPEN → HALF-OPEN（试探）。"""
        if self._circuit is not CircuitState.OPEN:
            return Decision(True, "当前非熔断态，无需复位", RiskLevel.L0, "manual_reset")
        elapsed = self._clock() - (self._circuit_opened_at or 0)
        if elapsed < self.cooldown_sec:
            return Decision(False,
                            f"熔断冷却中，还需 {self.cooldown_sec - elapsed:.0f} 秒才可复位",
                            RiskLevel.L0, "manual_reset")
        self._circuit = CircuitState.HALF_OPEN
        self._half_open_trial_done = False
        return Decision(True, "已复位至 HALF-OPEN（试探态）", RiskLevel.L0, "manual_reset")

    def report_trial_outcome(self, success: bool) -> None:
        """HALF-OPEN 试探结果：成功 → CLOSED，失败 → OPEN。"""
        if self._circuit is CircuitState.HALF_OPEN:
            if success:
                self._circuit = CircuitState.CLOSED
                self._consecutive_vetoes = 0
            else:
                self._circuit = CircuitState.OPEN
                self._circuit_opened_at = self._clock()

    # ═══════════════════════════════════════════════════════════════
    #  控制权（override 用户接管）/ 确认权（delegate 让渡）
    # ═══════════════════════════════════════════════════════════════

    def begin_operation(self) -> None:
        """Agent 开始一个操作序列（执行层在动作前调用）。"""
        self._agent_operating = True

    def end_operation(self) -> None:
        """Agent 结束操作序列（执行层在动作完成后调用）。"""
        self._agent_operating = False

    def notify_user_input(self) -> None:
        """用户有键鼠输入：刷新在场检测时间戳；若 Agent 正在操作 → 触发 override 接管。"""
        self._last_user_input_ts = self._clock()
        if self._agent_operating:
            self._override_active = True

    def touch_presence(self, last_input_ts: Optional[float] = None) -> None:
        """刷新「用户最后键鼠活动时间」（不触发 override）。

        供插件宿主等没有输入事件源的运行环境使用：上层周期读取系统
        空闲时长（如 GetLastInputInfo），用本方法把「用户最后活动时刻」
        喂给在场检测。与 notify_user_input 的区别：本方法绝不触发
        override 接管（那需要真实的用户输入事件）。
        """
        ts = self._clock() if last_input_ts is None else float(last_input_ts)
        # 只在「新时间戳更新」时刷新，避免时钟抖动把在场时间拉回过去
        if ts > self._last_user_input_ts:
            self._last_user_input_ts = ts

    def resume_from_override(self) -> None:
        """用户明确指示恢复，Agent 从挂起中恢复。"""
        self._override_active = False
        self._agent_operating = False

    def grant_delegate(self, scope: str, max_risk: str, window_sec: float,
                       target_window: Optional[str] = None) -> None:
        """用户主动让渡确认权（预授权）：接下来 scope 范围内、不超过 max_risk 的操作免确认。"""
        self._delegate = {
            "scope": scope,
            "max_risk": max_risk,
            "target_window": target_window,
            "expires_at": self._clock() + window_sec,
        }

    def revoke_delegate(self) -> None:
        """撤销让渡，立即回到「事事确认」。"""
        self._delegate = None

    def _delegate_covers(self, risk: RiskLevel, target_window) -> bool:
        d = self._delegate
        if d is None:
            return False
        if self._clock() > d["expires_at"]:
            self._delegate = None  # 到期自动复位
            return False
        if risk > RiskLevel[d["max_risk"]]:
            return False
        scope = d["scope"]
        if scope == "session":
            return True
        if scope == "window":
            return target_window is not None and target_window == d["target_window"]
        if scope == "app":
            # 简化：app 级让渡暂按 session 处理，后续按窗口所属进程细化
            return True
        return False

    # ═══════════════════════════════════════════════════════════════
    #  失败收敛 / 在场检测 / 审计
    # ═══════════════════════════════════════════════════════════════

    def record_failure(self, op: str, target_window: Optional[str] = None) -> int:
        """动作-验证-收敛：记录一次失败，返回累计次数。"""
        key = f"{op}@{target_window or ''}"
        self._failure_counts[key] = self._failure_counts.get(key, 0) + 1
        return self._failure_counts[key]

    def record_success(self, op: str, target_window: Optional[str] = None) -> None:
        """操作成功，重置该操作的失败计数。"""
        key = f"{op}@{target_window or ''}"
        self._failure_counts.pop(key, None)

    def _idle_locked(self) -> bool:
        if self.idle_allow_operate:
            return False
        return (self._clock() - self._last_user_input_ts) > self.idle_timeout_sec

    # ── 只读查询 ──

    @property
    def circuit(self) -> CircuitState:
        return self._circuit

    @property
    def override_active(self) -> bool:
        return self._override_active

    @property
    def agent_operating(self) -> bool:
        """Agent 是否正在执行操作序列（横幅「操作中」状态用）。"""
        return self._agent_operating

    @property
    def idle_locked(self) -> bool:
        """在场检测：用户空闲超时且未开启 idle_allow_operate（锁死中）。"""
        return self._idle_locked()

    def audit_log(self) -> List[AuditRecord]:
        return list(self._audit)

    # ═══════════════════════════════════════════════════════════
    #  状态持久化（自研 JSON 序列化，零外部依赖）
    # ═══════════════════════════════════════════════════════════
    #
    # 用途：裁决器可能运行在插件宿主子进程里，宿主空闲超时会退出重启。
    # 熔断 / 失败收敛 / delegate 是安全状态，**绝不能随进程重启而清零**。
    # 因此上层（插件 / 服务宿主）应在每次执行前后调用 import_state /
    # export_state，把状态落到磁盘（原子写），重启后恢复。
    #
    # 注意：审计日志（audit_log）属于主架构审计通道，不在此处持久化，
    # 以免状态文件无限膨胀；若需持久审计请由上层单独落盘。

    def export_state(self) -> dict:
        """导出全部安全状态（熔断机 / 控制权 / 失败收敛 / 时间戳）。

        仅含 JSON 可序列化字段，可直接 json.dump。
        """
        delegate = None
        if self._delegate:
            delegate = dict(self._delegate)
        return {
            "version": 1,
            "circuit": self._circuit.value,
            "consecutive_vetoes": int(self._consecutive_vetoes),
            "circuit_opened_at": self._circuit_opened_at,
            "half_open_trial_done": bool(self._half_open_trial_done),
            "override_active": bool(self._override_active),
            "agent_operating": bool(self._agent_operating),
            "delegate": delegate,
            "last_veto_at": self._last_veto_at,
            "last_user_input_ts": self._last_user_input_ts,
            "failure_counts": {
                str(k): int(v) for k, v in self._failure_counts.items()
            },
            "exported_at": self._clock(),
        }

    def import_state(self, state: dict) -> None:
        """从 export_state() 的输出恢复状态。

        规则：
          - 缺失 / 非法字段保持当前值不变（向前兼容、防文件损坏）。
          - version 不匹配时拒绝导入（避免旧格式误恢复）。
          - 「更保守」优先：无法判定的字段一律按更安全的方向处理。
        """
        if not isinstance(state, dict):
            return
        if state.get("version") != 1:
            return  # 未知版本，拒绝导入（保持现状，不猜）

        # ── 熔断机（导入时若时钟异常导致冷却无限延长，下次裁决仍走 OPEN 拒绝，安全） ──
        if state.get("circuit") in ("CLOSED", "OPEN", "HALF_OPEN"):
            self._circuit = CircuitState(state["circuit"])
        if isinstance(state.get("consecutive_vetoes"), int) and state["consecutive_vetoes"] >= 0:
            self._consecutive_vetoes = state["consecutive_vetoes"]
        if isinstance(state.get("circuit_opened_at"), (int, float)):
            self._circuit_opened_at = float(state["circuit_opened_at"])
        if isinstance(state.get("half_open_trial_done"), bool):
            self._half_open_trial_done = state["half_open_trial_done"]

        # ── 控制权 / 确认权 ──
        if isinstance(state.get("override_active"), bool):
            # override 恢复为 True 时保留（用户接管状态不丢）；
            # 恢复为 False 且当前为 True 时也保留（宁可保守挂起）。
            if state["override_active"] or self._override_active:
                self._override_active = True
        if isinstance(state.get("agent_operating"), bool):
            # 进程重启后不可能还在「操作中」，强制复位
            self._agent_operating = False
        d = state.get("delegate")
        if isinstance(d, dict):
            self._delegate = {
                "scope": str(d.get("scope", "window")),
                "max_risk": str(d.get("max_risk", "L2")),
                "target_window": d.get("target_window"),
                "expires_at": float(d.get("expires_at", 0.0)),
            }
        if isinstance(state.get("last_veto_at"), (int, float)):
            self._last_veto_at = float(state["last_veto_at"])
        if isinstance(state.get("last_user_input_ts"), (int, float)):
            # 导入旧时间戳意味着「用户很久没动」→ 更倾向锁死，安全方向正确
            self._last_user_input_ts = float(state["last_user_input_ts"])

        # ── 失败收敛 ──
        fc = state.get("failure_counts")
        if isinstance(fc, dict):
            restored = {}
            for k, v in fc.items():
                if isinstance(k, str) and isinstance(v, int) and v >= 0:
                    restored[k] = v
            self._failure_counts = restored

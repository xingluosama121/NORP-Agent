# Vibe Coding Agent - 视觉/操作外挂：动作-验证-收敛 协调器（coordinator）
# Copyright (c) 2026 xingluosama
#
# 职责：把三个既有模块串成「真能用」的闭环：
#   A. 操作执行层  vision_actions.py   （SendInput 键鼠注入 + 坐标闭环）
#   B. 窗口捕获层  vision_capture.py   （Graphics Capture 单窗口取帧）
#   C. 安全裁决器  vision_safety.py    （SafetyArbiter：分级 L0~L3 + 三态熔断 + 失败收敛）
#
# 三元组（唯一执行范式，见 docs/vision_agent_design.md 5.2）：
#   ① 动作：裁决 → 执行（L1/L2，先过裁决器）
#   ② 验证：重新捕获窗口 → 比对（L0 只读，零副作用）
#   ③ 收敛：预期变化发生 → 记录成功；否则重试，最多 3 次失败 → 停手上报，等待用户命令
#
# 安全铁律：
#   - 裁决权唯一归属 SafetyArbiter，本模块绝不绕过裁决器直接执行任何键鼠操作。
#   - 本模块自身是同步接口；上层调用方（plugin / 工具层）应把它放进线程池
#     （async_executor）执行，禁止在主事件循环里直接阻塞调用。
#   - 所有「验证」路径均为 L0 只读（重捕获 + 像素/视觉比对），零物理副作用。

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from vision_safety import SafetyArbiter, RiskLevel


class CoordinatorError(Exception):
    """协调器执行错误（捕获/执行/验证抛出的异常统一包装）。"""


class Verdict:
    """run() 返回的终态枚举（字符串，便于序列化/审计）。"""
    EXECUTED = "executed"                    # 已执行且验证通过
    NEEDS_CONFIRMATION = "needs_confirmation"  # 需用户显式确认（L2/L3），但未注入确认回调
    REJECTED = "rejected"                    # 被裁决器拒绝（熔断/override/空闲锁死/失败次数耗尽）
    EXHAUSTED = "exhausted"                  # 连续 3 次失败，停手上报，等待用户命令
    ERROR = "error"                          # 执行/验证过程中抛异常
    CANCELLED = "cancelled"                  # 用户拒绝确认


@dataclass
class ActionSpec:
    """一次操作请求（= 主架构发来的「带风险等级的指令」）。

    op            操作类型：click / move / type / key / scroll
    risk          风险等级 "L0"~"L3"（必填，调用方必须显式分级，不猜）
    payload       业务数据（坐标/文本/按键），随 op 不同：
                    click  : {"x":.., "y":.., "button":"left", "img_w":.., "img_h":..}
                    move   : {"x":.., "y":..}
                    type   : {"text":".."}
                    key    : {"key":"ENTER"} 或 {"mods":["CTRL"], "key":"C"}
                    scroll : {"x":.., "y":.., "clicks":-3}
    target_window 目标窗口标识（标题/名称），供 delegate 让渡匹配与审计；可选。
    """
    op: str
    risk: str
    payload: Dict[str, Any]
    target_window: Optional[str] = None


@dataclass
class VerificationResult:
    """一次验证的结论。success=False 时 detail 必须说明「看到了什么 / 为什么不确定」。"""
    success: bool
    detail: str = ""
    confidence: float = 0.0


@dataclass
class ActionResult:
    """run() 的完整返回：足以让主架构「记录每一次操作（含被否决的）」。"""
    op: str
    risk: str
    verdict: str
    reason: str
    attempts: int = 0
    allowed: bool = False
    verification: Optional[VerificationResult] = None
    target_window: Optional[str] = None

    def to_report(self) -> str:
        """生成「我点了什么 / 看到了什么 / 为什么失败」的人类可读上报文本。"""
        lines = [
            f"[视觉操作] op={self.op} risk={self.risk} verdict={self.verdict}",
            f"  结果: {self.reason}",
            f"  尝试次数: {self.attempts}",
        ]
        if self.target_window:
            lines.append(f"  目标窗口: {self.target_window}")
        if self.verification is not None:
            lines.append(
                f"  验证: success={self.verification.success} "
                f"confidence={self.verification.confidence:.4f} detail={self.verification.detail}"
            )
        return "\n".join(lines)

    def to_result_xml(self) -> str:
        """转成 IPC 结果/审计消息（<vision-result>），与 vision_ipc 对齐。"""
        from vision_ipc import build_result, ResultStatus

        status = {
            Verdict.EXECUTED: ResultStatus.APPROVED,
            Verdict.NEEDS_CONFIRMATION: "requires_confirmation",
            Verdict.REJECTED: ResultStatus.REJECTED,
            Verdict.EXHAUSTED: ResultStatus.REJECTED,
            Verdict.ERROR: ResultStatus.REJECTED,
            Verdict.CANCELLED: ResultStatus.VETOED,
        }.get(self.verdict, "rejected")
        payload = {
            "reason": self.reason,
            "attempts": self.attempts,
            "verdict": self.verdict,
            "target_window": self.target_window,
            "verification_detail": self.verification.detail if self.verification else "",
        }
        return build_result(self.op, status, payload, risk=self.risk)


# ═══════════════════════════════════════════════════════════════
#  默认执行器：SendInput 键鼠注入（懒加载 vision_actions，避免模块加载即依赖 Win32）
# ═══════════════════════════════════════════════════════════════

# 虚拟键码（硬编码标准 Win32 VK，避免在模块加载期 import vision_actions）
_KEY_MAP = {
    "ENTER": 0x0D, "RETURN": 0x0D,
    "TAB": 0x09,
    "ESC": 0x1B, "ESCAPE": 0x1B,
    "BACKSPACE": 0x08, "DELETE": 0x2E, "DEL": 0x2E,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "HOME": 0x24, "END": 0x23, "SPACE": 0x20,
}
_MOD_MAP = {
    "CTRL": 0x11, "CONTROL": 0x11,
    "SHIFT": 0x10,
    "ALT": 0x12, "MENU": 0x12,
    "WIN": 0x5B, "LWIN": 0x5B,
}


class SendInputExecutor:
    """默认操作执行器：把 ActionSpec 落到真实键鼠（必须在裁决器放行后调用）。"""

    def __init__(self, hwnd: int):
        self.hwnd = int(hwnd)

    def execute(self, spec: ActionSpec, before) -> None:
        import vision_actions as va

        p = spec.payload or {}
        op = spec.op

        if op == "click":
            sx, sy = self._img_to_screen(p, before, va)
            va.click(sx, sy, button=p.get("button", "left"))
        elif op == "double_click":
            sx, sy = self._img_to_screen(p, before, va)
            va.double_click(sx, sy, button=p.get("button", "left"))
        elif op == "move":
            sx, sy = self._img_to_screen(p, before, va)
            va.move_mouse(sx, sy)
        elif op == "type":
            va.type_text(str(p.get("text", "")))
        elif op == "key":
            self._do_key(p, va)
        elif op == "scroll":
            sx, sy = self._img_to_screen(p, before, va)
            va.scroll(sx, sy, int(p.get("clicks", -3)))
        else:
            raise CoordinatorError(f"未知操作 op={op!r}")

    def _img_to_screen(self, p: dict, before, va):
        x = float(p["x"])
        y = float(p["y"])
        img_w = int(p.get("img_w", before.width))
        img_h = int(p.get("img_h", before.height))
        # 反缩放 + ClientToScreen（进程 Per-Monitor-Aware-V2 → 物理像素一致）
        return va.image_to_screen(
            self.hwnd, x, y, img_w, img_h, before.width, before.height)

    def _do_key(self, p: dict, va) -> None:
        key = str(p.get("key", ""))
        mods = [str(m) for m in (p.get("mods") or [])]
        try:
            mod_vks = tuple(_MOD_MAP[m.upper()] for m in mods)
        except KeyError as e:
            raise CoordinatorError(f"未知修饰键：{e}")

        if key.upper() in _KEY_MAP:
            vk = _KEY_MAP[key.upper()]
        elif len(key) == 1:
            vk = ord(key.upper())
        else:
            raise CoordinatorError(f"未知按键：{key!r}")

        if mod_vks:
            va.key_combo(mod_vks, vk)
        else:
            va.key_press(vk)


# ═══════════════════════════════════════════════════════════════
#  验证器（L0 只读，零副作用）
# ═══════════════════════════════════════════════════════════════

class PixelDiffVerifier:
    """默认验证器：比对「动作前 / 动作后」两帧的像素差异。

    判定「画面变了」≈「操作生效」。无需任何视觉 API，可离线跑，
    是「动作-验证-收敛」离线自纠偏的最小可用实现。
    阈值需在标定台/影子模式下按实际场景调优。
    """

    def __init__(self, *, changed_threshold: float = 0.02,
                 pixel_epsilon: int = 30, sample_size: int = 64):
        self.changed_threshold = changed_threshold
        self.pixel_epsilon = pixel_epsilon
        self.sample_size = sample_size

    def verify(self, spec: ActionSpec, before, after) -> VerificationResult:
        from PIL import Image
        import io

        a = Image.open(io.BytesIO(before.png)).convert("L")
        b = Image.open(io.BytesIO(after.png)).convert("L")
        n = self.sample_size
        a = a.resize((n, n))
        b = b.resize((n, n))
        pa = a.getdata()
        pb = b.getdata()
        changed = sum(
            1 for x, y in zip(pa, pb) if abs(x - y) > self.pixel_epsilon)
        frac = changed / (n * n)
        success = frac > self.changed_threshold
        return VerificationResult(
            success=success,
            confidence=frac,
            detail=f"像素变化比例 {frac:.4f}（阈值 {self.changed_threshold}）",
        )


class CursorPosVerifier:
    """光标位置验证器：比对「动作后」鼠标实际位置与预期屏幕坐标。

    用于 move 这类「不改变画面内容」的操作——像素比对会因画面无变化
    而误判失败。本验证器全程 L0 只读（GetCursorPos），零物理副作用。
    坐标换算复刻 SendInputExecutor._img_to_screen 的同一公式，
    容差默认 ±5 物理像素（发送延迟/取整误差内）。
    """

    def __init__(self, *, tolerance: int = 5):
        self.tolerance = int(tolerance)

    def verify(self, spec: ActionSpec, before, after) -> VerificationResult:
        import vision_actions as va

        p = spec.payload or {}
        x = float(p.get("x", 0.0))
        y = float(p.get("y", 0.0))
        img_w = int(p.get("img_w", before.width))
        img_h = int(p.get("img_h", before.height))
        hwnd = int(getattr(before, "hwnd", 0) or 0)
        expected = va.image_to_screen(
            hwnd, x, y,
            img_w, img_h, before.width, before.height,
        )
        actual = va.get_cursor_pos()
        dx, dy = actual[0] - expected[0], actual[1] - expected[1]
        ok = abs(dx) <= self.tolerance and abs(dy) <= self.tolerance
        return VerificationResult(
            success=ok,
            confidence=1.0 if ok else 0.0,
            detail=(
                f"光标预期 {expected}，实际 {actual}，偏差 ({dx},{dy})"
                f"（容差 ±{self.tolerance}）"
            ),
        )


class VisionModelVerifier:
    """语义验证器：把「动作后」帧交给视觉模型，问「预期变化是否已发生」。

    适合像素比对无法判断的场景（如 type 后文本是否出现在输入框）。
    需要已配置视觉 provider（config 里有 vision_provider 等），否则抛错。
    """

    def __init__(self, config: dict, expected_desc: Optional[str] = None):
        self.config = config or {}
        self.expected_desc = expected_desc

    def verify(self, spec: ActionSpec, before, after) -> VerificationResult:
        from vision import process_visual

        desc = self.expected_desc or spec.payload.get("expect") \
            or f"操作 {spec.op} 已完成，界面出现了预期变化"
        prompt = (
            f"请只看这张截图，判断：{desc} 是否已经发生？"
            f"只回答「是」或「否」，不要解释。"
        )
        try:
            answer = process_visual(after.png, "png", self.config)
        except Exception as e:
            raise CoordinatorError(f"视觉验证调用失败：{e}")

        yes = _is_affirmative(answer)
        return VerificationResult(
            success=yes,
            confidence=1.0 if yes else 0.0,
            detail=f"模型判断：{answer}",
        )


def _is_affirmative(text: Optional[str]) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    # 首字优先：模型通常按「是/否」开头
    if t[0] in ("是", "已", "有", "完", "成", "y", "t"):
        return True
    if t[0] in ("否", "不", "没", "未", "n", "f"):
        return False
    for w in ("是", "已", "完成", "成功", "出现", "发生", "yes", "true"):
        if w in t:
            return True
    return False


# ═══════════════════════════════════════════════════════════════
#  协调器：动作 → 重捕获验证 → 收敛
# ═══════════════════════════════════════════════════════════════

class VisionCoordinator:
    """把 A（操作）+ B（捕获）+ C（裁决器）串成一个闭环。

    用法（调用方需把它放进线程池，避免阻塞主线程）：

        arbiter = SafetyArbiter(...)
        coord = VisionCoordinator(hwnd=..., arbiter=arbiter, confirm_fn=ui_confirm)
        result = coord.run(ActionSpec("click", "L2",
                                      {"x": 320, "y": 180}, target_window="计算器"))
        print(result.to_report())       # 完整上报
        print(result.to_result_xml())   # 或转 IPC 审计消息
    """

    def __init__(
        self,
        hwnd: int,
        *,
        config: Optional[dict] = None,
        arbiter: Optional[SafetyArbiter] = None,
        executor: Any = None,
        verifier: Any = None,
        capture_fn: Optional[Callable[[int], Any]] = None,
        confirm_fn: Optional[Callable[[ActionSpec, Any], bool]] = None,
        settle_delay: float = 0.3,
        frame_source: bool = False,
    ):
        self.hwnd = int(hwnd)
        self.config = config or {}
        self._arbiter = arbiter or SafetyArbiter()
        self._executor = executor if executor is not None else SendInputExecutor(self.hwnd)
        self._verifier = verifier if verifier is not None else PixelDiffVerifier()
        self._confirm_fn = confirm_fn
        self._settle_delay = settle_delay
        # ── 捕获器：frame_source=True 时用 capture_worker 驻留模式（高频循环），
        #    否则用注入的 capture_fn 或默认单帧冷启动。驻留模式实例由本对象
        #    管理生命周期（close() 释放 capture_worker 进程）。
        self._frame_source: Optional[FrameSourceCapture] = None
        if frame_source:
            self._frame_source = FrameSourceCapture(self.hwnd)
            self._frame_source.start()
            self._capture_fn = self._frame_source.capture
        else:
            self._capture_fn = capture_fn or _default_capture

    # ── 只读访问 ──

    @property
    def arbiter(self) -> SafetyArbiter:
        return self._arbiter

    def audit_log(self):
        return self._arbiter.audit_log()

    def close(self) -> None:
        """释放驻留捕获资源（frame_source=True 时务必调用）。"""
        if self._frame_source is not None:
            self._frame_source.close()
            self._frame_source = None

    def __enter__(self) -> "VisionCoordinator":
        return self

    def __exit__(self, *args) -> bool:
        self.close()
        return False

    # ── 核心：动作-验证-收敛 ──

    def run(self, spec: ActionSpec) -> ActionResult:
        risk = RiskLevel[spec.risk] if isinstance(spec.risk, str) else spec.risk
        user_confirmed = False
        self._arbiter.begin_operation()
        try:
            # ── 阶段一：裁决 + 显式确认（L2/L3） ──
            decision = self._arbiter.evaluate(
                spec.op, risk, spec.target_window, user_confirmed=False)
            if decision.requires_confirmation:
                if self._confirm_fn is None:
                    return ActionResult(
                        op=spec.op, risk=spec.risk,
                        verdict=Verdict.NEEDS_CONFIRMATION,
                        reason=decision.reason, allowed=False,
                        target_window=spec.target_window)
                try:
                    approved = bool(self._confirm_fn(spec, decision))
                except Exception as e:
                    return ActionResult(
                        op=spec.op, risk=spec.risk, verdict=Verdict.ERROR,
                        reason=f"确认回调异常：{e}", allowed=False,
                        target_window=spec.target_window)
                if not approved:
                    self._arbiter.user_veto()  # 用户否决 → 可能触发熔断
                    return ActionResult(
                        op=spec.op, risk=spec.risk, verdict=Verdict.CANCELLED,
                        reason="用户拒绝确认", allowed=False,
                        target_window=spec.target_window)
                user_confirmed = True
            elif not decision.allowed:
                return ActionResult(
                    op=spec.op, risk=spec.risk, verdict=Verdict.REJECTED,
                    reason=decision.reason, allowed=False,
                    target_window=spec.target_window)

            # ── 阶段二：动作 → 重捕获验证 → 收敛（最多 max_failures 次） ──
            attempts = 0
            last_verification: Optional[VerificationResult] = None
            last_error: Optional[str] = None

            while attempts < self._arbiter.max_failures:
                # 每次尝试前重新裁决：熔断/override/空闲锁死在尝试间隙可能变化，
                # 让裁决器成为唯一事实来源。
                decision = self._arbiter.evaluate(
                    spec.op, risk, spec.target_window, user_confirmed=user_confirmed)
                if not decision.allowed:
                    return ActionResult(
                        op=spec.op, risk=spec.risk, verdict=Verdict.REJECTED,
                        reason=decision.reason, allowed=False, attempts=attempts,
                        verification=last_verification, target_window=spec.target_window)

                # ① 动作
                try:
                    before = self._capture_fn(self.hwnd)
                    self._executor.execute(spec, before)
                except Exception as e:
                    attempts += 1
                    last_error = f"执行失败：{e}"
                    self._arbiter.record_failure(spec.op, spec.target_window)
                    continue

                time.sleep(self._settle_delay)  # 等目标应用渲染出变化

                # ② 验证（L0 只读）
                try:
                    after = self._capture_fn(self.hwnd)
                    last_verification = self._verifier.verify(spec, before, after)
                except Exception as e:
                    attempts += 1
                    last_error = f"验证失败：{e}"
                    self._arbiter.record_failure(spec.op, spec.target_window)
                    continue

                attempts += 1

                # ③ 收敛
                if last_verification.success:
                    self._arbiter.record_success(spec.op, spec.target_window)
                    return ActionResult(
                        op=spec.op, risk=spec.risk, verdict=Verdict.EXECUTED,
                        reason=f"第 {attempts} 次尝试验证通过", allowed=True,
                        attempts=attempts, verification=last_verification,
                        target_window=spec.target_window)

                self._arbiter.record_failure(spec.op, spec.target_window)
                # 循环继续，直到耗尽 max_failures

            reason = f"连续失败 {attempts} 次，停手上报，等待用户命令"
            if last_error:
                reason += f"；最后错误：{last_error}"
            return ActionResult(
                op=spec.op, risk=spec.risk, verdict=Verdict.EXHAUSTED,
                reason=reason, allowed=False, attempts=attempts,
                verification=last_verification, target_window=spec.target_window)

        finally:
            self._arbiter.end_operation()


def _default_capture(hwnd: int):
    """默认捕获：单帧冷启动 capture_worker（稳健；高频循环可注入 FrameSource 版）。"""
    from vision_capture import capture_window
    return capture_window(hwnd)


class FrameSourceCapture:
    """capture_worker 驻留模式（--serve）捕获器：协调器高频循环用。

    相比 _default_capture 每次冷启动进程，驻留模式只启动一次
    capture_worker，之后每帧只需「shot 命令 + 读长度 + 读数据」，
    「动作-验证-收敛」里 before/after 两次重捕获的延迟显著下降。

    用法：
        fs = FrameSourceCapture(hwnd)
        fs.start()
        coord = VisionCoordinator(hwnd=..., capture_fn=fs.capture, ...)
        ...
        fs.close()

    注意：驻留进程持续占用 capture_worker（约一个 D3D11 会话），
    不再使用时务必 close()；也可用 with 语句管理生命周期。
    """

    def __init__(self, hwnd: int, ready_timeout: float = 10.0):
        self.hwnd = int(hwnd)
        self.ready_timeout = ready_timeout
        self._source = None

    @property
    def active(self) -> bool:
        return self._source is not None

    def start(self) -> None:
        if self._source is not None:
            return
        from vision_capture import FrameSource
        self._source = FrameSource(self.hwnd, ready_timeout=self.ready_timeout)
        self._source.start()

    def capture(self, hwnd: int):
        """取最新一帧，返回与 capture_window 相同形状的 CaptureResult。"""
        if self._source is None:
            raise CoordinatorError("FrameSource 未启动（先调用 start()）")
        from vision_capture import bmp_to_capture_result
        bmp = self._source.shot_ready()
        return bmp_to_capture_result(bmp, hwnd)

    def close(self) -> None:
        if self._source is not None:
            try:
                self._source.close()
            finally:
                self._source = None

    def __enter__(self) -> "FrameSourceCapture":
        self.start()
        return self

    def __exit__(self, *args) -> bool:
        self.close()
        return False

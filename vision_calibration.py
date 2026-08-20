# Vibe Coding Agent - 视觉外挂标定台（独立 dry-run 调试工具）
# Copyright (c) 2026 xingluosama
#
# 定位（docs/vision_agent_design.md 7.3）：delegate / override 接管 / 熔断 /
# 坐标换算全部脱离 Agent 链路，在独立「标定台」里 dry-run 调试。
# 全程零物理副作用：不碰真实窗口、不注入键鼠、不发网络请求。
#
# 核心价值：「delegate 逻辑」与「真实操作」在调试阶段彻底分离；
# 调参不经过视觉/捕获/输入重模块。标定台 = 假 Agent（脚本驱动），
# 裁决逻辑与真实运行完全同一套（SafetyArbiter）。
#
# 自研约束：仅标准库（argparse/json/time），无任何第三方依赖。
#
# ── 三种模式 ────────────────────────────────────────────────────
# 1) 脚本模式  python vision_calibration.py --script ops.jsonl
#      输入：操作脚本（JSONL 或 JSON 数组），模拟 Agent 会发出的操作序列
#      输出：每步裁决结果 + 误放行/误拦截统计 + 状态快照
# 2) 演示模式  python vision_calibration.py --demo circuit|delegate|idle
#      内置场景：熔断机状态转移 / delegate 覆盖 / 在场检测锁死
# 3) 坐标模式  python vision_calibration.py --coords
#      --img 1920 1080 --phys 1536 864 --client-offset 100 200
#      --points "960,540 320,180"
#      喂假坐标看「反缩放 + ClientToScreen 偏移」换算结果，不真点。
#
# ── 脚本条目字段 ────────────────────────────────────────────────
#  普通操作（走 evaluate 裁决）：
#    {"op": "click", "risk": "L2", "target_window": "calc",
#     "user_confirmed": true, "expect": "allowed"}
#  控制动作（action 字段，驱动状态机）：
#    {"action": "veto"}                      用户否决一次
#    {"action": "hotkey_stop"}               Ctrl+End 物理熔断
#    {"action": "manual_reset"}              手动复位（受冷却期约束）
#    {"action": "trial_outcome", "success": true}   HALF-OPEN 试探结果上报
#    {"action": "advance_clock", "seconds": 70}     推进假时钟（过冷却/让渡期）
#    {"action": "user_input"}                模拟用户键鼠输入（刷新在场/触发 override）
#    {"action": "grant_delegate", "scope": "window", "max_risk": "L2",
#     "window_sec": 300, "target_window": "calc"}
#    {"action": "revoke_delegate"}           撤销让渡
#    {"action": "begin_op"} / {"action": "end_op"}   操作序列开始/结束
#  断言字段：
#    expect = allowed | rejected | requires_confirmation（可选，比对裁决结果）
#    expect_circuit = CLOSED | OPEN | HALF_OPEN（可选，比对动作后熔断态）

import argparse
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from vision_safety import SafetyArbiter, RiskLevel, CircuitState

SCRIPT_VERSION = "1.0"


class FakeClock:
    """假时钟：标定台可推进时间（冷却期/让渡期/空闲超时全靠它模拟）。"""

    def __init__(self, start: float = 1000000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += max(0.0, float(seconds))


class CalibrationHarness:
    """标定台执行器：脚本 → 裁决器 → 结果与统计。"""

    def __init__(self, config: dict = None):
        cfg = config or {}
        self.clock = FakeClock()
        self.arbiter = SafetyArbiter(
            consecutive_vetoes_to_open=int(cfg.get("consecutive_vetoes_to_open", 3)),
            cooldown_sec=float(cfg.get("cooldown_sec", 60.0)),
            idle_timeout_sec=float(cfg.get("idle_timeout_sec", 150.0)),
            idle_allow_operate=bool(cfg.get("idle_allow_operate", False)),
            delegate_window_sec=float(cfg.get("delegate_window_sec", 300.0)),
            delegate_scope=str(cfg.get("delegate_scope", "window")),
            delegate_max_risk=str(cfg.get("delegate_max_risk", "L2")),
            auto_tighten_threshold=int(cfg.get("auto_tighten_threshold", 3)),
            cooldown_after_veto=float(cfg.get("cooldown_after_veto", 60.0)),
            max_failures=int(cfg.get("max_failures", 3)),
            clock=self.clock,
        )
        self.stats = {"steps": 0, "assert_ok": 0, "assert_fail": 0,
                      "false_allow": 0, "false_block": 0}

    # ── 裁决断言 ──

    def _check_expect(self, entry: dict, decision) -> None:
        expect = entry.get("expect")
        if not expect:
            return
        if decision.requires_confirmation:
            actual = "requires_confirmation"
        else:
            actual = "allowed" if decision.allowed else "rejected"
        ok = actual == expect
        # 误放行：预期拒绝却放行；误拦截：预期放行却拒绝
        if not ok:
            if expect == "rejected" and actual == "allowed":
                self.stats["false_allow"] += 1
            elif expect == "allowed" and actual in ("rejected", "requires_confirmation"):
                self.stats["false_block"] += 1
        self._record_assert(ok, f"expect={expect} actual={actual}")

    def _check_circuit(self, entry: dict) -> None:
        expect = entry.get("expect_circuit")
        if not expect:
            return
        self._record_assert(
            self.arbiter.circuit.value == expect,
            f"expect_circuit={expect} actual={self.arbiter.circuit.value}")

    def _record_assert(self, ok: bool, detail: str) -> None:
        if ok:
            self.stats["assert_ok"] += 1
            print(f"      [断言通过] {detail}")
        else:
            self.stats["assert_fail"] += 1
            print(f"      [断言失败] {detail}")

    # ── 单步执行 ──

    def step(self, entry: dict) -> None:
        self.stats["steps"] += 1
        action = entry.get("action")
        idx = self.stats["steps"]

        if action:
            self._do_control(idx, entry)
            self._check_circuit(entry)
            return

        op = str(entry.get("op", "click"))
        risk = str(entry.get("risk", "L1")).upper()
        if risk not in ("L0", "L1", "L2", "L3"):
            print(f"[{idx}] 非法风险级 {risk}（op={op}），跳过")
            return
        target = entry.get("target_window")
        user_confirmed = bool(entry.get("user_confirmed", False))

        decision = self.arbiter.evaluate(
            op, RiskLevel[risk], target, user_confirmed=user_confirmed)
        verdict = ("requires_confirmation" if decision.requires_confirmation
                   else ("allowed" if decision.allowed else "rejected"))
        print(f"[{idx}] op={op} risk={risk} target={target or '-'} "
              f"confirmed={user_confirmed} → {verdict}")
        print(f"      原因: {decision.reason}")
        self._check_expect(entry, decision)
        self._check_circuit(entry)

    # ── 控制动作 ──

    def _do_control(self, idx: int, entry: dict) -> None:
        a = str(entry["action"])
        if a == "veto":
            self.arbiter.user_veto()
            print(f"[{idx}] 用户否决一次（连续否决={self.arbiter._consecutive_vetoes}）")
        elif a == "hotkey_stop":
            self.arbiter.hotkey_emergency_stop()
            print(f"[{idx}] Ctrl+End 物理熔断 → {self.arbiter.circuit.value}")
        elif a == "manual_reset":
            d = self.arbiter.manual_reset()
            print(f"[{idx}] 手动复位 → {d.reason}（当前 {self.arbiter.circuit.value}）")
        elif a == "trial_outcome":
            ok = bool(entry.get("success", False))
            self.arbiter.report_trial_outcome(ok)
            print(f"[{idx}] HALF-OPEN 试探结果上报 success={ok} → {self.arbiter.circuit.value}")
        elif a == "advance_clock":
            sec = float(entry.get("seconds", 0))
            self.clock.advance(sec)
            print(f"[{idx}] 假时钟推进 {sec}s → t={self.clock.t:.0f}")
        elif a == "user_input":
            self.arbiter.notify_user_input()
            print(f"[{idx}] 用户键鼠输入（在场刷新；若 Agent 操作中则触发 override="
                  f"{self.arbiter.override_active}）")
        elif a == "grant_delegate":
            self.arbiter.grant_delegate(
                scope=str(entry.get("scope", "window")),
                max_risk=str(entry.get("max_risk", "L2")),
                window_sec=float(entry.get("window_sec", 300.0)),
                target_window=entry.get("target_window"),
            )
            print(f"[{idx}] delegate 让渡：{entry.get('scope', 'window')} / "
                  f"{entry.get('max_risk', 'L2')} / {entry.get('window_sec', 300)}s")
        elif a == "revoke_delegate":
            self.arbiter.revoke_delegate()
            print(f"[{idx}] 撤销 delegate 让渡")
        elif a == "begin_op":
            self.arbiter.begin_operation()
            print(f"[{idx}] 操作序列开始（agent_operating=True）")
        elif a == "end_op":
            self.arbiter.end_operation()
            print(f"[{idx}] 操作序列结束")
        elif a == "override_resume":
            self.arbiter.resume_from_override()
            print(f"[{idx}] 用户指示恢复（override 解除）")
        else:
            print(f"[{idx}] 未知控制动作 {a}，跳过")

    # ── 汇总 ──

    def summary(self) -> None:
        s = self.stats
        print("\n════════ 标定汇总 ════════")
        print(f"  总步数: {s['steps']}")
        print(f"  断言: 通过 {s['assert_ok']} / 失败 {s['assert_fail']}")
        print(f"  误放行: {s['false_allow']}   误拦截: {s['false_block']}")
        print(f"  终态: 熔断机={self.arbiter.circuit.value} "
              f"override={self.arbiter.override_active} "
              f"delegate={'有' if self.arbiter._delegate else '无'}")
        audit = self.arbiter.audit_log()
        print(f"  审计记录: {len(audit)} 条")
        if s["assert_fail"] or s["false_allow"] or s["false_block"]:
            print("  ⚠ 存在断言失败或误判，请检查脚本预期或裁决参数。")
        else:
            print("  ✓ 全部断言通过，无误放行 / 误拦截。")


# ═══════════════════════════════════════════════════════════════
#  脚本加载
# ═══════════════════════════════════════════════════════════════

def load_script(path: str) -> list:
    """加载操作脚本：支持 JSON 数组或 JSONL（每行一个 JSON）。"""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        return []
    # JSONL：逐行解析（首字符 '{' 且整行是对象）
    lines = [l for l in text.splitlines() if l.strip()]
    if lines and lines[0].lstrip().startswith("{"):
        entries = []
        for ln in lines:
            try:
                entries.append(json.loads(ln))
            except json.JSONDecodeError as e:
                raise ValueError(f"脚本行解析失败：{e}") from e
        return entries
    # JSON 数组
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("脚本必须是 JSON 数组或 JSONL")
    return data


# ═══════════════════════════════════════════════════════════════
#  内置演示场景
# ═══════════════════════════════════════════════════════════════

DEMO_CIRCUIT = [
    {"op": "click", "risk": "L1", "expect": "allowed"},
    {"action": "veto"}, {"action": "veto"}, {"action": "veto",
     "expect_circuit": "OPEN"},
    {"op": "click", "risk": "L1", "expect": "rejected"},
    {"op": "look", "risk": "L0", "expect": "allowed",
     "comment": "熔断中 L0 只读仍放行"},
    {"action": "manual_reset", "expect_circuit": "OPEN",
     "comment": "冷却未过，复位被拒"},
    {"action": "advance_clock", "seconds": 61},
    {"action": "manual_reset", "expect_circuit": "HALF_OPEN"},
    {"op": "click", "risk": "L1", "expect": "allowed",
     "comment": "试探态放行单个 L1"},
    {"op": "click", "risk": "L1", "expect": "rejected",
     "comment": "试探已消耗"},
    {"action": "trial_outcome", "success": True, "expect_circuit": "CLOSED"},
    {"op": "click", "risk": "L1", "expect": "allowed"},
]

DEMO_DELEGATE = [
    {"op": "type", "risk": "L2", "target_window": "calc",
     "expect": "requires_confirmation", "comment": "无让渡：L2 需确认"},
    {"op": "type", "risk": "L2", "target_window": "calc", "user_confirmed": True,
     "expect": "allowed", "comment": "用户显式确认后放行"},
    {"action": "grant_delegate", "scope": "window", "max_risk": "L2",
     "window_sec": 300, "target_window": "calc"},
    {"op": "type", "risk": "L2", "target_window": "calc",
     "expect": "allowed", "comment": "让渡范围内免确认"},
    {"op": "type", "risk": "L2", "target_window": "notepad",
     "expect": "requires_confirmation", "comment": "窗口不匹配：仍需确认"},
    {"op": "delete_all", "risk": "L3", "target_window": "calc",
     "user_confirmed": True, "expect": "allowed",
     "comment": "L3 双因子确认放行（delegate 不覆盖 L3 但用户确认可放行）"},
    {"op": "delete_all", "risk": "L3", "target_window": "calc",
     "expect": "requires_confirmation", "comment": "L3 永不免确认"},
    {"action": "revoke_delegate"},
    {"op": "type", "risk": "L2", "target_window": "calc",
     "expect": "requires_confirmation", "comment": "撤销后回到事事确认"},
]

DEMO_IDLE = [
    {"op": "click", "risk": "L1", "expect": "allowed"},
    {"action": "advance_clock", "seconds": 200},
    {"op": "click", "risk": "L1", "expect": "rejected",
     "comment": "空闲 200s > 阈值 150s → 锁死"},
    {"op": "look", "risk": "L0", "expect": "allowed", "comment": "L0 只读仍放行"},
    {"action": "user_input"},
    {"op": "click", "risk": "L1", "expect": "allowed", "comment": "用户回归后放行"},
]

DEMOS = {
    "circuit": ("熔断机状态转移（CLOSED→OPEN→HALF-OPEN→CLOSED）", DEMO_CIRCUIT),
    "delegate": ("delegate 让渡覆盖与 L3 永不免确认", DEMO_DELEGATE),
    "idle": ("在场检测：空闲锁死与回归恢复", DEMO_IDLE),
}


# ═══════════════════════════════════════════════════════════════
#  坐标标定模式
# ═══════════════════════════════════════════════════════════════

def run_coords(args) -> int:
    """坐标换算 dry-run：反缩放 + 模拟 ClientToScreen 偏移，不真点。"""
    from vision_actions import unscale_coordinates

    img_w, img_h = args.img_w, args.img_h
    phys_w, phys_h = args.phys_w, args.phys_h
    off_x, off_y = args.client_offset[0], args.client_offset[1]
    points = []
    for tok in args.points.split():
        x, y = tok.split(",")
        points.append((float(x), float(y)))

    print("════════ 坐标标定（dry-run，零点击）════════")
    print(f"  发给模型的图片: {img_w}x{img_h}")
    print(f"  窗口物理帧:     {phys_w}x{phys_h}")
    print(f"  缩放比: x={phys_w / img_w:.4f}  y={phys_h / img_h:.4f}")
    print(f"  模拟客户区屏幕偏移: (+{off_x}, +{off_y})")
    print()
    for x, y in points:
        try:
            px, py = unscale_coordinates(x, y, img_w, img_h, phys_w, phys_h)
            sx, sy = round(px) + off_x, round(py) + off_y
            print(f"  图片({x:g}, {y:g}) → 物理({px:.2f}, {py:.2f}) "
                  f"→ 屏幕({sx}, {sy})")
        except Exception as e:
            print(f"  图片({x}, {y}) → 换算失败：{e}")
    print("\n  注意：真实运行中第②步由 ClientToScreen(hwnd) 完成（自动含窗口"
          "位置/标题栏/边框偏移），此处仅用固定偏移量验证第①步反缩放公式。")
    return 0


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main() -> int:
    p = argparse.ArgumentParser(
        description="视觉外挂标定台：裁决器 / 熔断 / delegate / 坐标换算 dry-run 调试")
    p.add_argument("--script", help="操作脚本文件（JSONL 或 JSON 数组）")
    p.add_argument("--demo", choices=sorted(DEMOS), help="内置演示场景")
    p.add_argument("--list-demos", action="store_true", help="列出内置演示场景")
    # 安全参数覆盖（调参用，不写盘、不碰真实配置）
    p.add_argument("--cooldown", type=float, help="熔断冷却秒数（覆盖默认 60）")
    p.add_argument("--idle-timeout", type=float, help="空闲锁死阈值秒数（覆盖默认 150）")
    p.add_argument("--idle-allow", action="store_true", help="空闲后允许继续操作")
    p.add_argument("--delegate-window", type=float, help="delegate 时长秒数（覆盖默认 300）")
    # 坐标标定模式
    p.add_argument("--coords", action="store_true", help="进入坐标标定模式")
    p.add_argument("--img-w", type=int, default=1920)
    p.add_argument("--img-h", type=int, default=1080)
    p.add_argument("--phys-w", type=int, default=1536)
    p.add_argument("--phys-h", type=int, default=864)
    p.add_argument("--client-offset", type=int, nargs=2, default=[100, 200],
                   metavar=("DX", "DY"))
    p.add_argument("--points", default="960,540 320,180",
                   help="待标定点列表，空格分隔的 x,y")
    args = p.parse_args()

    if args.list_demos:
        for name, (desc, _) in DEMOS.items():
            print(f"  {name:<10} {desc}")
        return 0

    if args.coords:
        return run_coords(args)

    if args.demo and args.script:
        print("错误：--demo 与 --script 互斥")
        return 2
    if args.demo:
        desc, entries = DEMOS[args.demo]
        print(f"════════ 演示场景：{desc} ════════")
    elif args.script:
        entries = load_script(args.script)
        print(f"════════ 脚本模式：{args.script}（{len(entries)} 条）════════")
    else:
        p.print_help()
        return 0

    overrides = {}
    if args.cooldown is not None:
        overrides["cooldown_sec"] = args.cooldown
    if args.idle_timeout is not None:
        overrides["idle_timeout_sec"] = args.idle_timeout
    if args.idle_allow:
        overrides["idle_allow_operate"] = True
    if args.delegate_window is not None:
        overrides["delegate_window_sec"] = args.delegate_window

    harness = CalibrationHarness(overrides)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        comment = entry.get("comment")
        if comment:
            print(f"    （{comment}）")
        harness.step(entry)
    harness.summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())

# ──────────────────────────────────────────────────────────────
# Plugin: Stress Tester
# Publisher: xingluosama
# Version: 1.0.0
# Description: 代码压力测试/性能基准测试插件。支持多语言代码的
#   执行时间测量、并发压力测试、内存分析、多次迭代统计等。
# ──────────────────────────────────────────────────────────────

PLUGIN_NAME = "Stress Tester"
PLUGIN_PUBLISHER = "xingluosama"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "代码压力测试：执行时间测量、并发测试、内存分析、多次迭代统计。"

import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ── 1. 工具注册 ────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "stress_test",
            "description": (
                "对指定的代码或命令执行压力测试/性能基准测试。"
                "支持：Python 代码执行时间测量、多次迭代统计（平均值/中位数/P95/P99）、"
                "并发压力测试（模拟多用户同时请求）、可选内存分析。"
                "适用于测试函数性能、API 端点吞吐量、算法效率对比等场景。"
                "⚠️ 仅支持 Python 代码的直接执行；其他语言代码请使用 exec_cmd 调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "要测试的 Python 代码。代码将在临时文件中执行。"
                            "如果是函数调用，建议包含 import 语句和函数定义。"
                            "例如：'import time; time.sleep(0.01)' 或一个完整的函数定义加调用。"
                        )
                    },
                    "setup_code": {
                        "type": "string",
                        "description": (
                            "在压力测试前执行的准备代码（不计入测试时间）。"
                            "用于 import 模块、定义函数、准备数据等。"
                            "例如：'import hashlib\\ndata = b\"test\" * 1000'"
                        )
                    },
                    "iterations": {
                        "type": "integer",
                        "description": "迭代次数，默认 10，范围 1-1000。越多结果越稳定但耗时越长。",
                        "default": 10
                    },
                    "concurrency": {
                        "type": "integer",
                        "description": "并发线程数，默认 1（串行）。大于 1 时模拟并发压力测试。",
                        "default": 1
                    },
                    "warmup_iterations": {
                        "type": "integer",
                        "description": "预热迭代次数（不计入统计），默认 2。用于 JIT 预热和缓存填充。",
                        "default": 2
                    },
                    "track_memory": {
                        "type": "boolean",
                        "description": "是否追踪内存使用（使用 tracemalloc），默认 false。",
                        "default": False
                    },
                    "timeout_per_iteration": {
                        "type": "number",
                        "description": "单次迭代超时秒数，默认 30。超过则标记为失败。",
                        "default": 30
                    }
                },
                "required": ["code"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "benchmark_compare",
            "description": (
                "对比两段代码的性能。分别对两段代码进行压力测试并生成对比报告。"
                "适用于算法选型、优化前后对比、不同实现方案的性能评估。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code_a": {
                        "type": "string",
                        "description": "方案 A 的 Python 代码"
                    },
                    "code_b": {
                        "type": "string",
                        "description": "方案 B 的 Python 代码"
                    },
                    "label_a": {
                        "type": "string",
                        "description": "方案 A 的标签（如 '递归实现'），默认 '方案 A'",
                        "default": "方案 A"
                    },
                    "label_b": {
                        "type": "string",
                        "description": "方案 B 的标签（如 '迭代实现'），默认 '方案 B'",
                        "default": "方案 B"
                    },
                    "setup_code": {
                        "type": "string",
                        "description": "两段代码共用的准备代码（不计入测试时间）"
                    },
                    "iterations": {
                        "type": "integer",
                        "description": "每段代码的迭代次数，默认 10",
                        "default": 10
                    },
                    "warmup_iterations": {
                        "type": "integer",
                        "description": "预热迭代次数，默认 2",
                        "default": 2
                    },
                    "timeout_per_iteration": {
                        "type": "number",
                        "description": "单次迭代超时秒数，默认 30",
                        "default": 30
                    }
                },
                "required": ["code_a", "code_b"],
                "additionalProperties": False
            }
        }
    }
]

# ── 2. 核心压力测试逻辑 ────────────────────────────────────────


def _run_single_iteration(
    code: str,
    setup_code: str,
    timeout: float,
    track_memory: bool,
    global_ns: dict,
) -> dict:
    """
    在子线程中执行单次迭代，捕获时间、异常和可选的内存信息。
    返回 {"time": float_seconds, "error": str|None, "memory_delta": int|None}
    """
    result = {"time": 0.0, "error": None, "memory_delta": None}
    error_ref = []  # 用列表在闭包中传递异常

    def _target():
        try:
            # 复制全局命名空间到本地
            local_ns = dict(global_ns)

            if track_memory:
                tracemalloc.start()
                snapshot_before = tracemalloc.take_snapshot()

            t_start = time.perf_counter()
            exec(code, local_ns)
            t_end = time.perf_counter()

            if track_memory:
                snapshot_after = tracemalloc.take_snapshot()
                stats = snapshot_after.compare_to(snapshot_before, 'lineno')
                # 累积内存增量（正值为分配，负值为释放）
                total_diff = sum(s.size_diff for s in stats)
                result["memory_delta"] = total_diff
                tracemalloc.stop()

            result["time"] = t_end - t_start

        except Exception as e:
            error_ref.append(str(e))

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        # 超时 — 无法强制 kill daemon 线程，但标记为超时
        result["error"] = f"超时（>{timeout}秒）"
    elif error_ref:
        result["error"] = error_ref[0]

    return result


def _compute_statistics(times: list[float]) -> dict:
    """计算时间序列的统计指标。"""
    if not times:
        return {
            "count": 0, "min": 0, "max": 0, "mean": 0,
            "median": 0, "p95": 0, "p99": 0,
            "stddev": 0, "total": 0,
        }

    n = len(times)
    sorted_times = sorted(times)
    mean = sum(times) / n

    # 标准差
    variance = sum((t - mean) ** 2 for t in times) / n
    stddev = math.sqrt(variance)

    # 百分位数
    def _percentile(data: list[float], p: float) -> float:
        if not data:
            return 0.0
        k = (len(data) - 1) * p / 100.0
        f = int(k)
        c = k - f
        if f + 1 < len(data):
            return data[f] + c * (data[f + 1] - data[f])
        return data[f]

    return {
        "count": n,
        "min": min(times),
        "max": max(times),
        "mean": mean,
        "median": _percentile(sorted_times, 50),
        "p95": _percentile(sorted_times, 95),
        "p99": _percentile(sorted_times, 99),
        "stddev": stddev,
        "total": sum(times),
    }


def _format_time(seconds: float) -> str:
    """智能格式化时间。"""
    if seconds < 1e-6:
        return f"{seconds * 1e9:.2f} ns"
    elif seconds < 1e-3:
        return f"{seconds * 1e6:.2f} µs"
    elif seconds < 1:
        return f"{seconds * 1e3:.2f} ms"
    elif seconds < 60:
        return f"{seconds:.3f} s"
    else:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.1f}s"


def _format_memory(bytes_val: int) -> str:
    """智能格式化内存大小。"""
    if abs(bytes_val) < 1024:
        return f"{bytes_val} B"
    elif abs(bytes_val) < 1024 ** 2:
        return f"{bytes_val / 1024:.2f} KB"
    elif abs(bytes_val) < 1024 ** 3:
        return f"{bytes_val / (1024**2):.2f} MB"
    else:
        return f"{bytes_val / (1024**3):.2f} GB"


def _build_ascii_histogram(times: list[float], bins: int = 10) -> str:
    """构建 ASCII 直方图展示时间分布。"""
    if not times or len(times) < 2:
        return ""

    min_t = min(times)
    max_t = max(times)
    if min_t == max_t:
        return ""

    bin_width = (max_t - min_t) / bins
    if bin_width == 0:
        return ""

    counts = [0] * bins
    for t in times:
        idx = min(int((t - min_t) / bin_width), bins - 1)
        counts[idx] += 1

    max_count = max(counts)
    bar_max_width = 30
    scale = bar_max_width / max_count if max_count > 0 else 1

    lines = ["```"]
    for i in range(bins):
        low = min_t + i * bin_width
        high = low + bin_width
        bar = "█" * max(1, int(counts[i] * scale))
        lines.append(f"  {_format_time(low):>8} - {_format_time(high):<8} | {bar} {counts[i]}")
    lines.append("```")
    return "\n".join(lines)


def _run_stress_test(
    code: str,
    setup_code: str,
    iterations: int,
    concurrency: int,
    warmup_iterations: int,
    track_memory: bool,
    timeout: float,
) -> str:
    """核心压力测试执行逻辑。"""

    # ── 1. 准备阶段：执行 setup_code ──
    global_ns = {}
    if setup_code.strip():
        try:
            exec(setup_code, global_ns)
        except Exception as e:
            return f"❌ **准备代码执行失败**:\n```\n{e}\n```"

    # ── 2. 预热阶段 ──
    warmup_times = []
    warmup_errors = 0
    if warmup_iterations > 0:
        for i in range(warmup_iterations):
            r = _run_single_iteration(code, setup_code, timeout, False, global_ns)
            if r["error"]:
                warmup_errors += 1
            else:
                warmup_times.append(r["time"])

    # ── 3. 执行阶段 ──
    all_results = []
    start_wall = time.perf_counter()

    if concurrency <= 1:
        # 串行执行
        for i in range(iterations):
            r = _run_single_iteration(code, setup_code, timeout, track_memory, global_ns)
            all_results.append(r)
    else:
        # 并发执行 — 使用线程池
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = []
            for i in range(iterations):
                f = pool.submit(
                    _run_single_iteration,
                    code, setup_code, timeout, track_memory, global_ns
                )
                futures.append(f)

            for f in as_completed(futures):
                try:
                    r = f.result(timeout=timeout + 5)
                    all_results.append(r)
                except Exception as e:
                    all_results.append({"time": 0, "error": str(e), "memory_delta": None})

    end_wall = time.perf_counter()
    wall_time = end_wall - start_wall

    # ── 4. 统计 ──
    success_times = [r["time"] for r in all_results if not r["error"]]
    error_count = sum(1 for r in all_results if r["error"])
    errors_list = [r["error"] for r in all_results if r["error"]]

    stats = _compute_statistics(success_times)

    # 内存统计
    memory_deltas = [r["memory_delta"] for r in all_results if r["memory_delta"] is not None]
    avg_memory = sum(memory_deltas) / len(memory_deltas) if memory_deltas else None

    # ── 5. 构建报告 ──
    report_parts = []

    # 标题
    report_parts.append(f"## 🔥 压力测试报告")
    report_parts.append(f"")
    report_parts.append(f"**执行时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_parts.append(f"")

    # 测试参数
    report_parts.append(f"### ⚙️ 测试配置")
    report_parts.append(f"| 参数 | 值 |")
    report_parts.append(f"|------|-----|")
    report_parts.append(f"| 迭代次数 | {iterations} |")
    report_parts.append(f"| 并发线程 | {concurrency} |")
    report_parts.append(f"| 预热迭代 | {warmup_iterations} |")
    report_parts.append(f"| 内存追踪 | {'开启' if track_memory else '关闭'} |")
    report_parts.append(f"| 单次超时 | {timeout}s |")
    report_parts.append(f"")

    # 预热结果
    if warmup_iterations > 0:
        if warmup_errors > 0:
            report_parts.append(f"⚠️ 预热阶段 {warmup_errors}/{warmup_iterations} 次失败")
        elif warmup_times:
            report_parts.append(
                f"🔥 预热完成（{len(warmup_times)} 次）："
                f"均值 {_format_time(sum(warmup_times)/len(warmup_times))}"
            )
        report_parts.append(f"")

    # 执行时间统计
    report_parts.append(f"### ⏱️ 执行时间统计")
    report_parts.append(f"")
    if success_times:
        report_parts.append(f"| 指标 | 值 |")
        report_parts.append(f"|------|-----|")
        report_parts.append(f"| 成功 / 失败 | {stats['count']} / {error_count} |")
        report_parts.append(f"| 总耗时（墙钟） | {_format_time(wall_time)} |")
        report_parts.append(f"| 总 CPU 时间 | {_format_time(stats['total'])} |")
        report_parts.append(f"| 最小值 | {_format_time(stats['min'])} |")
        report_parts.append(f"| 最大值 | {_format_time(stats['max'])} |")
        report_parts.append(f"| 平均值 (Mean) | {_format_time(stats['mean'])} |")
        report_parts.append(f"| 中位数 (P50) | {_format_time(stats['median'])} |")
        report_parts.append(f"| P95 | {_format_time(stats['p95'])} |")
        report_parts.append(f"| P99 | {_format_time(stats['p99'])} |")
        report_parts.append(f"| 标准差 | {_format_time(stats['stddev'])} |")
        if concurrency > 1 and wall_time > 0:
            throughput = stats["count"] / wall_time
            report_parts.append(f"| 吞吐量 | {throughput:.1f} ops/s |")
        report_parts.append(f"")

        # 稳定性评级
        if stats["mean"] > 0:
            cv = stats["stddev"] / stats["mean"]  # 变异系数
            if cv < 0.05:
                stability = "🟢 非常稳定（CV < 5%）"
            elif cv < 0.15:
                stability = "🟡 较稳定（CV < 15%）"
            elif cv < 0.30:
                stability = "🟠 波动较大（CV < 30%）"
            else:
                stability = "🔴 不稳定（CV ≥ 30%）"
            report_parts.append(f"📊 **稳定性**: {stability}（变异系数 {cv * 100:.1f}%）")
            report_parts.append(f"")
    else:
        report_parts.append(f"❌ 所有迭代均失败！")
        report_parts.append(f"")

    # 错误详情
    if error_count > 0:
        report_parts.append(f"### ❌ 错误详情")
        unique_errors = {}
        for e in errors_list:
            unique_errors[e] = unique_errors.get(e, 0) + 1
        for err, cnt in unique_errors.items():
            report_parts.append(f"- [{cnt}×] `{err[:200]}`")
        report_parts.append(f"")

    # 内存分析
    if track_memory and avg_memory is not None:
        report_parts.append(f"### 🧠 内存分析")
        report_parts.append(f"| 指标 | 值 |")
        report_parts.append(f"|------|-----|")
        report_parts.append(f"| 平均内存变化 | {_format_memory(int(avg_memory))} |")
        if memory_deltas:
            report_parts.append(f"| 最大内存增量 | {_format_memory(max(memory_deltas))} |")
            report_parts.append(f"| 最小内存增量 | {_format_memory(min(memory_deltas))} |")
        report_parts.append(f"")

    # 分布直方图
    if len(success_times) >= 5:
        report_parts.append(f"### 📊 时间分布")
        report_parts.append(f"")
        report_parts.append(_build_ascii_histogram(success_times))
        report_parts.append(f"")

    # 原始数据摘要
    if len(success_times) <= 20:
        report_parts.append(f"### 📋 原始数据")
        report_parts.append(f"```")
        for i, t in enumerate(success_times, 1):
            report_parts.append(f"  [{i:>3}] {_format_time(t)}")
        report_parts.append(f"```")
        report_parts.append(f"")

    # 建议
    report_parts.append(f"### 💡 分析建议")
    if error_count > iterations * 0.5:
        report_parts.append("- 🔴 失败率过高，请检查代码逻辑或减少并发数")
    elif error_count > 0:
        report_parts.append("- 🟡 存在少量失败，建议检查超时设置或错误处理")
    if concurrency > 1 and wall_time > 0:
        report_parts.append(f"- 📈 并发 {concurrency} 线程下吞吐量为 {stats['count'] / wall_time:.1f} ops/s")
    if stats.get("p99", 0) > stats.get("mean", 1) * 3:
        report_parts.append("- ⚠️ P99 延迟显著高于平均值，存在长尾延迟问题")
    if track_memory and avg_memory and avg_memory > 10 * 1024 * 1024:
        report_parts.append("- 🧠 内存增长较大，可能存在内存泄漏风险")
    if not track_memory:
        report_parts.append("- 💡 开启 `track_memory` 可分析内存使用情况")
    report_parts.append("")

    return "\n".join(report_parts)


def _run_benchmark_compare(
    code_a: str,
    code_b: str,
    label_a: str,
    label_b: str,
    setup_code: str,
    iterations: int,
    warmup_iterations: int,
    timeout: float,
) -> str:
    """对比两段代码的性能。"""

    # 分别测试
    report_parts = []
    report_parts.append(f"## ⚡ 性能对比测试")
    report_parts.append(f"")
    report_parts.append(f"**{label_a}** vs **{label_b}**")
    report_parts.append(f" 迭代次数: {iterations} | 预热: {warmup_iterations}")
    report_parts.append(f"")

    # 测试 A
    report_parts.append(f"---")
    report_parts.append(f"")
    report_parts.append(f"### 📌 {label_a}")
    result_a = _run_stress_test(
        code_a, setup_code, iterations, 1, warmup_iterations, False, timeout
    )
    # 提取关键指标
    global_ns = {}
    if setup_code.strip():
        try:
            exec(setup_code, global_ns)
        except Exception:
            pass

    times_a = []
    for _ in range(iterations):
        r = _run_single_iteration(code_a, setup_code, timeout, False, global_ns)
        if not r["error"]:
            times_a.append(r["time"])

    stats_a = _compute_statistics(times_a)

    # 测试 B
    report_parts.append(f"### 📌 {label_b}")
    global_ns = {}
    if setup_code.strip():
        try:
            exec(setup_code, global_ns)
        except Exception:
            pass

    times_b = []
    for _ in range(iterations):
        r = _run_single_iteration(code_b, setup_code, timeout, False, global_ns)
        if not r["error"]:
            times_b.append(r["time"])

    stats_b = _compute_statistics(times_b)

    # ── 对比表 ──
    report_parts.append(f"---")
    report_parts.append(f"")
    report_parts.append(f"### 📊 对比结果")
    report_parts.append(f"")
    report_parts.append(f"| 指标 | {label_a} | {label_b} | 差异 |")
    report_parts.append(f"|------|-----------|-----------|------|")

    def _compare_row(name: str, key: str, lower_is_better: bool = True):
        va = stats_a.get(key, 0)
        vb = stats_b.get(key, 0)
        if va == 0 and vb == 0:
            diff_str = "—"
        elif va == 0:
            diff_str = "N/A"
        elif vb == 0:
            diff_str = "N/A"
        else:
            ratio = vb / va
            if lower_is_better:
                if ratio < 0.95:
                    diff_str = f"🟢 B 快 {1/ratio:.1f}×"
                elif ratio > 1.05:
                    diff_str = f"🔴 B 慢 {ratio:.1f}×"
                else:
                    diff_str = f"≈ 持平 ({ratio:.2f}×)"
            else:
                if ratio > 1.05:
                    diff_str = f"🟢 B 高 {ratio:.1f}×"
                elif ratio < 0.95:
                    diff_str = f"🔴 B 低 {ratio:.1f}×"
                else:
                    diff_str = f"≈ 持平 ({ratio:.2f}×)"

        return f"| {name} | {_format_time(va)} | {_format_time(vb)} | {diff_str} |"

    report_parts.append(_compare_row("平均值", "mean"))
    report_parts.append(_compare_row("中位数", "median"))
    report_parts.append(_compare_row("最小值", "min"))
    report_parts.append(_compare_row("最大值", "max"))
    report_parts.append(_compare_row("P95", "p95"))
    report_parts.append(_compare_row("P99", "p99"))
    report_parts.append(_compare_row("标准差", "stddev"))
    report_parts.append(f"")

    # 胜出者
    if stats_a["mean"] and stats_b["mean"]:
        if stats_b["mean"] < stats_a["mean"] * 0.95:
            winner = f"🏆 **{label_b}** 更快！（平均快 {stats_a['mean']/stats_b['mean']:.1f}×）"
        elif stats_a["mean"] < stats_b["mean"] * 0.95:
            winner = f"🏆 **{label_a}** 更快！（平均快 {stats_b['mean']/stats_a['mean']:.1f}×）"
        else:
            winner = "🤝 两者性能接近，无明显差异"
        report_parts.append(winner)
        report_parts.append(f"")

    # 稳定性对比
    if stats_a["mean"] and stats_b["mean"]:
        cv_a = stats_a["stddev"] / stats_a["mean"]
        cv_b = stats_b["stddev"] / stats_b["mean"]
        report_parts.append(f"📊 **稳定性对比**: ")
        report_parts.append(f"  - {label_a}: CV = {cv_a*100:.1f}%")
        report_parts.append(f"  - {label_b}: CV = {cv_b*100:.1f}%")
        if cv_b < cv_a * 0.8:
            report_parts.append(f"  - {label_b} 更稳定 ✅")
        elif cv_a < cv_b * 0.8:
            report_parts.append(f"  - {label_a} 更稳定 ✅")
        report_parts.append(f"")

    return "\n".join(report_parts)


# ── 3. 工具分发 ────────────────────────────────────────────────

def execute(tool_name: str, args: dict, context) -> str:
    if tool_name == "stress_test":
        code = args.get("code", "")
        if not code.strip():
            return "❌ 请提供要测试的代码"

        setup_code = args.get("setup_code", "")
        iterations = max(1, min(args.get("iterations", 10), 1000))
        concurrency = max(1, min(args.get("concurrency", 1), 100))
        warmup_iterations = max(0, min(args.get("warmup_iterations", 2), 100))
        track_memory = args.get("track_memory", False)
        timeout = max(1, min(args.get("timeout_per_iteration", 30), 300))

        context.logger.info(
            f"Stress test: {iterations} iters × {concurrency} concurrency, "
            f"warmup={warmup_iterations}, mem={track_memory}"
        )

        # 更新统计
        s = context.storage
        s["stress_tests_count"] = s.get("stress_tests_count", 0) + 1

        return _run_stress_test(
            code, setup_code, iterations, concurrency,
            warmup_iterations, track_memory, timeout
        )

    if tool_name == "benchmark_compare":
        code_a = args.get("code_a", "")
        code_b = args.get("code_b", "")
        if not code_a.strip() or not code_b.strip():
            return "❌ 请提供两段要对比的代码"

        label_a = args.get("label_a", "方案 A")
        label_b = args.get("label_b", "方案 B")
        setup_code = args.get("setup_code", "")
        iterations = max(1, min(args.get("iterations", 10), 500))
        warmup_iterations = max(0, min(args.get("warmup_iterations", 2), 50))
        timeout = max(1, min(args.get("timeout_per_iteration", 30), 300))

        context.logger.info(
            f"Benchmark compare: '{label_a}' vs '{label_b}', {iterations} iters"
        )

        s = context.storage
        s["benchmarks_count"] = s.get("benchmarks_count", 0) + 1

        return _run_benchmark_compare(
            code_a, code_b, label_a, label_b,
            setup_code, iterations, warmup_iterations, timeout
        )

    return f"Unknown tool: {tool_name}"


# ── 4. 钩子 ────────────────────────────────────────────────────

def on_agent_init(context):
    """初始化计数器。"""
    context.storage["stress_tests_count"] = 0
    context.storage["benchmarks_count"] = 0
    context.storage["plugin_started"] = datetime.now().isoformat()
    context.logger.info("🔥 Stress Tester plugin loaded — ready to benchmark!")


def on_agent_shutdown(context):
    """会话结束时输出统计。"""
    stress = context.storage.get("stress_tests_count", 0)
    bench = context.storage.get("benchmarks_count", 0)
    total = stress + bench
    if total > 0:
        context.logger.info(
            f"Stress Tester: {stress} stress test(s) + {bench} benchmark(s) "
            f"= {total} total this session"
        )


def on_task_start(task_text: str, context):
    """检测用户是否要进行压力测试。"""
    keywords = [
        "压力测试", "压力", "stress test", "benchmark",
        "性能测试", "性能对比", "测一下性能", "跑分",
        "压测", "并发测试", "测速", "对比性能"
    ]
    if any(kw in task_text.lower() for kw in keywords):
        context.logger.info(f"Stress test task detected: {task_text[:80]}")

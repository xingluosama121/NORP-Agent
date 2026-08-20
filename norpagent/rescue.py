# Copyright (c) 2026 xingluosama121, MIT Licensed
"""崩溃救援 CLI：主程序无法启动时也能回退快照（纯标准库，零框架依赖）。

入口：``norpagent-rescue``（console_scripts）或 ``python -m norpagent.rescue``。

本模块刻意**不 import 框架任何其它模块**——配置错误、插件损坏导致
norpagent 完全无法启动时，救援工具仍然可用（它只读 / 写快照 JSON
与 WebUI 设置文件）。

用法::

    norpagent-rescue list                     # 时间线（★ = 最后一次正常工作的快照）
    norpagent-rescue show <id>                # 查看某快照内容（敏感键已脱敏）
    norpagent-rescue rollback <id>            # 回退：写回退目标 + 恢复 WebUI 设置
    norpagent-rescue rollback --last-good     # 一键回退到最后一次正常快照
    norpagent-rescue mark-good <id>           # 手动标记「正常」
    norpagent-rescue prune --keep 50          # 只保留最近 N 个快照

回退完成后，下一次 ``norpagent`` / ``np()`` 启动会自动消费回退目标
（rollback_target.json），无需再手动搬配置。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

# 快照目录：与运行时同源（环境变量 > 默认 ~/.norpagent/snapshots）。
# 不 import norpagent.recovery.store —— 救援工具连 recovery 包都不能
# 依赖？不：store 是纯标准库，可以直接 import（与主程序隔离的边界
# 是「不 import 框架运行时/插件/内核」）。
from norpagent.recovery import store
from norpagent.recovery.capture import restore_files


def _fmt_time(ts: Any) -> str:
    import time

    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return str(ts)


def _print_item(item: Dict[str, Any], current: bool, last_good: bool) -> None:
    star = "★" if last_good else " "
    cur = ">" if current else " "
    good = "good" if item.get("good") else ""
    src = str(item.get("source") or "engine")[:4]
    sess = "+会话" if item.get("sessions") else ""
    print(
        f" {star}{cur} {item.get('id')}  {_fmt_time(item.get('created_at'))}"
        f"  [{str(item.get('tag')):<8}] {src} {good} {sess}"
    )
    desc = str(item.get("description") or "")
    if desc:
        print(f"         {desc}")


def cmd_list(_args: Any) -> int:
    items = store.list_snapshots()
    if not items:
        print("（无快照）快照目录:", store.get_snapshot_dir())
        return 0
    current = store.current_index()
    last_good = store.last_good_id()
    print(f"快照目录: {store.get_snapshot_dir()}")
    print(f"共 {len(items)} 个快照（当前指针 #{current}）\n")
    for item in items:
        _print_item(item, item.get("index") == current,
                    item.get("id") == last_good)
    print()
    if last_good:
        print(f"★ 最后一次正常工作的快照: {last_good}")
        print(f"  → 一键恢复: norpagent-rescue rollback --last-good")
    else:
        print("（还没有标记为「正常」的快照；启动成功 30 秒或首个任务")
        print(" 完成后会自动标记，也可用 mark-good <id> 手动标记）")
    return 0


def _find(snap_id: Optional[str], by_last_good: bool) -> Optional[Dict[str, Any]]:
    if by_last_good:
        snap_id = store.last_good_id()
        if not snap_id:
            print("[错误] 没有标记为「正常」的快照", file=sys.stderr)
            return None
    if not snap_id:
        print("[错误] 需要指定快照 id（norpagent-rescue list 查看）",
              file=sys.stderr)
        return None
    payload = store.read_snapshot(snap_id)
    if payload is None:
        print(f"[错误] 快照不存在: {snap_id}", file=sys.stderr)
        return None
    return payload


def cmd_show(args: Any) -> int:
    payload = _find(args.id, args.last_good)
    if payload is None:
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_rollback(args: Any) -> int:
    payload = _find(args.id, args.last_good)
    if payload is None:
        return 1
    data = payload.get("data") or {}
    snap_id = payload.get("id") or ""
    # 文件级恢复（WebUI 设置 + 会话文件）
    restored = restore_files(data, snap_id=snap_id)
    # 写回退目标：下次启动自动消费
    target = store.write_rollback_target(payload)
    print(f"[OK] 已回退快照: {snap_id}")
    print(f"     说明: {payload.get('description') or ''}")
    print(f"     WebUI 设置已恢复: {restored.get('webui')}")
    print(f"     会话文件已恢复: {restored.get('sessions')} 个")
    print(f"     回退目标: {target}")
    print("\n下次启动 norpagent（或调用 np()）时会自动应用该快照的")
    print("槽位配置（用户本次显式给出的参数优先）。")
    return 0


def cmd_mark_good(args: Any) -> int:
    payload = _find(args.id, args.last_good)
    if payload is None:
        return 1
    info = store.mark_good(payload.get("id") or "")
    if info is None:
        print("[错误] 标记失败", file=sys.stderr)
        return 1
    print(f"[OK] 已标记为「正常」: {info.get('id')}")
    return 0


def cmd_prune(args: Any) -> int:
    keep = int(getattr(args, "keep", 50) or 50)
    removed = store.prune(keep)
    print(f"[OK] 保留最近 {keep} 个快照，删除 {removed} 个")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="norpagent-rescue",
        description="norpagent 崩溃救援：主程序无法启动时回退快照（纯标准库）",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("list", help="列出全部快照（★ = 最后一次正常工作）")
    p_show = sub.add_parser("show", help="查看快照内容")
    p_show.add_argument("id")
    p_show.add_argument("--last-good", action="store_true")
    p_rb = sub.add_parser("rollback", help="回退到指定快照")
    p_rb.add_argument("id", nargs="?")
    p_rb.add_argument("--last-good", action="store_true",
                      help="回退到最后一次正常工作的快照")
    p_mg = sub.add_parser("mark-good", help="标记快照为「正常」")
    p_mg.add_argument("id", nargs="?")
    p_mg.add_argument("--last-good", action="store_true")
    p_pr = sub.add_parser("prune", help="只保留最近 N 个快照")
    p_pr.add_argument("--keep", type=int, default=50)

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0
    handlers = {
        "list": cmd_list,
        "show": cmd_show,
        "rollback": cmd_rollback,
        "mark-good": cmd_mark_good,
        "prune": cmd_prune,
    }
    fn = handlers.get(args.cmd)
    if fn is None:
        parser.print_help()
        return 1
    try:
        return fn(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())

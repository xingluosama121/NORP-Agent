# Copyright (c) 2026 xingluosama121, MIT Licensed
"""Crash-rescue CLI: roll back snapshots even when the main program cannot start (pure standard library, zero framework dependencies).

Entry: ``norpagent-rescue`` (console_scripts) or ``python -m norpagent.rescue``.

This module deliberately does **not import any other framework module at module
level** — when norpagent cannot start at all due to config errors or broken
plugins, the snapshot commands still work (they only read / write snapshot JSON
and the WebUI settings file). The human-takeover commands (``tools`` /
``tool-call`` / ``manual`` / ``serve``) lazily import the framework inside the
command functions: they need a real tool environment, so they require a working
installation, but they never touch the broken main process.

Usage::

    # snapshot rescue (pure standard library)
    norpagent-rescue list                     # timeline (★ = last known-good snapshot)
    norpagent-rescue show <id>                # inspect a snapshot (sensitive keys redacted)
    norpagent-rescue rollback <id>            # roll back: write the rollback target + restore WebUI settings
    norpagent-rescue rollback --last-good     # one-step rollback to the last known-good snapshot
    norpagent-rescue mark-good <id>           # manually mark "known good"
    norpagent-rescue prune --keep 50          # keep only the most recent N snapshots

    # human takeover (model down -> drive the tools by hand; needs the framework)
    norpagent-rescue tools                              # inventory of all 20 built-in tools
    norpagent-rescue tool-call echo --args '{"text":"ping"}'
    norpagent-rescue tool-call exec_cmd --args '{"command":"git status"}' --timeout 30
    norpagent-rescue manual                             # interactive manual tool console
    norpagent-rescue serve --port 8799                  # HTTP API + operator page (127.0.0.1)
    norpagent-rescue serve --token my-secret            # ...with bearer-token auth

After a rollback, the next ``norpagent`` / ``np()`` startup automatically consumes
the rollback target (rollback_target.json); no manual config moving is needed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

# snapshot directory: same source as the runtime (env var > default ~/.norpagent/snapshots).
# We do not import norpagent.recovery.store? No — store is pure standard library and
# can be imported directly (the isolation boundary with the main program is "do not
# import the framework runtime / plugins / kernel").
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
    sess = "+session" if item.get("sessions") else ""
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
        print("(no snapshots) snapshot dir:", store.get_snapshot_dir())
        return 0
    current = store.current_index()
    last_good = store.last_good_id()
    print(f"snapshot dir: {store.get_snapshot_dir()}")
    print(f"{len(items)} snapshots (current pointer #{current})\n")
    for item in items:
        _print_item(item, item.get("index") == current,
                    item.get("id") == last_good)
    print()
    if last_good:
        print(f"★ last known-good snapshot: {last_good}")
        print(f"  → one-step restore: norpagent-rescue rollback --last-good")
    else:
        print("(no snapshot marked \"known good\" yet; 30 seconds after a successful")
        print("startup or after the first task, one is marked automatically; you can")
        print("also mark one manually with mark-good <id>)")
    return 0


def _find(snap_id: Optional[str], by_last_good: bool) -> Optional[Dict[str, Any]]:
    if by_last_good:
        snap_id = store.last_good_id()
        if not snap_id:
            print("[error] no snapshot marked \"known good\"", file=sys.stderr)
            return None
    if not snap_id:
        print("[error] a snapshot id is required (see norpagent-rescue list)",
              file=sys.stderr)
        return None
    payload = store.read_snapshot(snap_id)
    if payload is None:
        print(f"[error] snapshot does not exist: {snap_id}", file=sys.stderr)
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
    # file-level restore (WebUI settings + session files)
    restored = restore_files(data, snap_id=snap_id)
    # write the rollback target: consumed automatically at next startup
    target = store.write_rollback_target(payload)
    print(f"[OK] rolled back snapshot: {snap_id}")
    print(f"     description: {payload.get('description') or ''}")
    print(f"     WebUI settings restored: {restored.get('webui')}")
    print(f"     session files restored: {restored.get('sessions')}")
    print(f"     rollback target: {target}")
    print("\nThe next norpagent startup (or np() call) will automatically apply")
    print("that snapshot's slot configuration (parameters the user explicitly")
    print("gave this time take priority).")
    return 0


def cmd_mark_good(args: Any) -> int:
    payload = _find(args.id, args.last_good)
    if payload is None:
        return 1
    info = store.mark_good(payload.get("id") or "")
    if info is None:
        print("[error] marking failed", file=sys.stderr)
        return 1
    print(f"[OK] marked as \"known good\": {info.get('id')}")
    return 0


def cmd_prune(args: Any) -> int:
    keep = int(getattr(args, "keep", 50) or 50)
    removed = store.prune(keep)
    print(f"[OK] keeping the most recent {keep} snapshots; removed {removed}")
    return 0


# ══════════════════════════════════════════════════════
#  human takeover: manual tool calls when the model is down
#  (lazily imports the framework; see norpagent.rescue_api)
# ══════════════════════════════════════════════════════

def _load_rescue_env(args: Any) -> Any:
    """Build the standalone rescue tool environment (lazy framework import)."""
    try:
        from norpagent.rescue_api import RescueToolEnvironment
    except Exception as exc:  # noqa: BLE001 — the framework itself may be broken
        print("[error] cannot load the tool environment (is norpagent installed correctly?)",
              file=sys.stderr)
        print(f"       {exc}", file=sys.stderr)
        sys.exit(1)
    return RescueToolEnvironment(
        workspace_root=getattr(args, "workspace", None) or None,
        sandbox=getattr(args, "sandbox", None) or "subprocess",
        session=getattr(args, "session", None) or "memory",
        scheduler=getattr(args, "scheduler", None) or "persistent",
        context_db=getattr(args, "context_db", None) or None,
    )


def cmd_tools(args: Any) -> int:
    env = _load_rescue_env(args)
    items = env.inventory()
    print(f"rescue tool inventory ({len(items)} tools):")
    for item in items:
        desc = (item.get("description") or "").replace("\n", " ")
        if len(desc) > 78:
            desc = desc[:78] + "..."
        req = ",".join(item.get("required") or [])
        suffix = f"  (required: {req})" if req else ""
        print(f"  {item['category']:<8} {item['name']:<18} {desc}{suffix}")
    print("\nmanual invocation:")
    print('  norpagent-rescue tool-call <name> --args \'{"...": "..."}\' [--timeout N]')
    return 0


def _print_call_result(result: Dict[str, Any]) -> None:
    status = "TIMED OUT" if result.get("timed_out") else ("OK" if result.get("ok") else "FAILED")
    print(f"[{status}] {result.get('tool')}  "
          f"(task {result.get('task_id')}, {result.get('duration_ms', 0)} ms)")
    if result.get("output"):
        print(result["output"])
    if result.get("error"):
        print(f"[error] {result['error']}")


def cmd_tool_call(args: Any) -> int:
    env = _load_rescue_env(args)
    raw = getattr(args, "args", "{}") or "{}"
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        print(f"[error] --args must be valid JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        print("[error] --args must be a JSON object", file=sys.stderr)
        return 1
    result = env.call_tool(
        args.tool, payload,
        timeout=getattr(args, "timeout", None) or None,
    )
    _print_call_result(result)
    return 0 if result.get("ok") else 1


def cmd_manual(args: Any) -> int:
    env = _load_rescue_env(args)
    print("norpagent rescue manual tool console (human as the model).")
    print("Type <tool_name> <json args> to call a tool; /tools, /help, /exit for commands.")
    print(f"workspace: {env.params.get('workspace_root') or '(current directory)'}")
    while True:
        try:
            line = input("rescue> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/exit", "/quit", "exit", "quit"):
            break
        if line in ("/help", "help"):
            print("  echo {\"text\": \"ping\"}   call a tool with JSON args")
            print("  {\"tool\": \"file_list\", \"args\": {}}   JSON-object form")
            print("  /tools            list all tools")
            print("  /exit             quit")
            continue
        if line in ("/tools", "tools"):
            names = [item["name"] for item in env.inventory()]
            print("available tools:", ", ".join(names))
            continue
        # parse: either {"tool": ..., "args": {...}} or "<name> <json args>"
        name: Optional[str] = None
        raw_args = "{}"
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and isinstance(obj.get("tool"), str):
                    name = obj["tool"]
                    raw_args = json.dumps(obj.get("args") or {})
                else:
                    print("[error] JSON-object form needs a string 'tool' field")
                    continue
            except ValueError as exc:
                print(f"[error] invalid JSON: {exc}")
                continue
        else:
            parts = line.split(None, 1)
            name = parts[0]
            if len(parts) > 1:
                raw_args = parts[1]
        try:
            payload = json.loads(raw_args)
        except ValueError as exc:
            print(f"[error] args must be valid JSON: {exc}")
            continue
        if not isinstance(payload, dict):
            print("[error] args must be a JSON object")
            continue
        result = env.call_tool(name, payload)
        _print_call_result(result)
    return 0


def cmd_serve(args: Any) -> int:
    env = _load_rescue_env(args)
    try:
        from norpagent.rescue_api import RescueToolAPI
    except Exception as exc:  # noqa: BLE001
        print(f"[error] cannot start the rescue tool API: {exc}", file=sys.stderr)
        return 1
    token = getattr(args, "token", None) or None
    api = RescueToolAPI(
        env,
        port=int(getattr(args, "port", None) or 8799),
        host=getattr(args, "host", None) or "127.0.0.1",
        token=token,
    )
    try:
        port = api.start()
    except OSError as exc:
        print(f"[error] cannot bind {api.host}:{api.port}: {exc}", file=sys.stderr)
        return 1
    print(f"[rescue] manual tool API listening on http://{api.host}:{port}/", flush=True)
    print(f"         {len(env.registry.list_tools())} tools exposed for manual calls", flush=True)
    if token:
        print(f"         auth: Authorization: Bearer <token> (--token)", flush=True)
    print("         warning: this endpoint executes real commands / writes real files;", flush=True)
    print("         never bind it beyond localhost.", flush=True)
    print("         press Ctrl+C to stop", flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        api.shutdown()
        print("[rescue] tool API stopped")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="norpagent-rescue",
        description="norpagent crash rescue: roll back snapshots when the main program cannot start (pure standard library); manual tool takeover when the model is down",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("list", help="list all snapshots (★ = last known good)")
    p_show = sub.add_parser("show", help="show snapshot content")
    p_show.add_argument("id")
    p_show.add_argument("--last-good", action="store_true")
    p_rb = sub.add_parser("rollback", help="roll back to the given snapshot")
    p_rb.add_argument("id", nargs="?")
    p_rb.add_argument("--last-good", action="store_true",
                      help="roll back to the last known-good snapshot")
    p_mg = sub.add_parser("mark-good", help="mark a snapshot as \"known good\"")
    p_mg.add_argument("id", nargs="?")
    p_mg.add_argument("--last-good", action="store_true")
    p_pr = sub.add_parser("prune", help="keep only the most recent N snapshots")
    p_pr.add_argument("--keep", type=int, default=50)
    # ── human takeover: manual tool calls (lazy framework import) ──
    p_tools = sub.add_parser("tools", help="list all tools available for manual rescue calls")
    p_tools.add_argument("--workspace", help="workspace root for file tools (default: current directory)")
    p_tools.add_argument("--sandbox", help="sandbox backend (default: subprocess)")
    p_tools.add_argument("--session", help="session backend (default: memory)")
    p_tools.add_argument("--scheduler", help="scheduler backend (default: persistent)")
    p_tools.add_argument("--context-db", help="context store database path (default: shared ~/.norpagent/context.db)")
    p_tc = sub.add_parser("tool-call", help="one-shot manual tool call")
    p_tc.add_argument("tool", help="tool name (see 'tools')")
    p_tc.add_argument("--args", default="{}",
                      help="tool arguments as a JSON object, e.g. '{\"text\": \"ping\"}'")
    p_tc.add_argument("--timeout", type=float, default=None,
                      help="hard timeout in seconds (default 300)")
    p_tc.add_argument("--workspace", help="workspace root for file tools")
    p_tc.add_argument("--context-db", help="context store database path")
    p_man = sub.add_parser("manual", help="interactive manual tool console (human as the model)")
    p_man.add_argument("--workspace", help="workspace root for file tools")
    p_man.add_argument("--context-db", help="context store database path")
    p_srv = sub.add_parser("serve", help="HTTP API + operator page for manual tool calls")
    p_srv.add_argument("--port", type=int, default=8799, help="listen port (default 8799)")
    p_srv.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    p_srv.add_argument("--token", default=None, help="optional bearer token for /api/* requests")
    p_srv.add_argument("--workspace", help="workspace root for file tools")
    p_srv.add_argument("--context-db", help="context store database path")

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
        "tools": cmd_tools,
        "tool-call": cmd_tool_call,
        "manual": cmd_manual,
        "serve": cmd_serve,
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

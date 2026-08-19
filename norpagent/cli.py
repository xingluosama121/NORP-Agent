# Copyright (c) 2026 xingluosama121, MIT Licensed
"""命令行入口：norpagent。

用法::

    norpagent --list-modes
    norpagent --mode minimal                       # 交互 REPL
    norpagent --mode ptc --prompt "..."            # 单次任务
    norpagent --mode-file my_mode.py               # 创造模式：加载自定义模式文件
    norpagent --mode standard --ui web --port 8787 # Web UI（HTTP + SSE）
    norpagent --mode standard --plugin-dir ./my_plugins   # 加载外部插件
    norpagent --safe-mode                                 # 安全模式：只加载最小化内核
    norpagent plugin-sign --gen                    # 生成插件签名密钥对
    norpagent plugin-sign my_plugin.py --key <私钥hex>

崩溃救援（主程序无法启动时回退快照，纯标准库独立工具）::

    norpagent-rescue list
    norpagent-rescue rollback --last-good

支持 P2 起的模型选项（--model 覆盖预设默认模型）。
"""

from __future__ import annotations

import argparse
import sys
import threading
from typing import List, Optional

from norpagent.builtin import install_defaults
from norpagent.kernel.agent import AgentRuntime
from norpagent.kernel.presets import load_preset_file
from norpagent.kernel.registry import Registry, ComponentError
from norpagent.modes import register_all_presets


def _build_registry(args: Any) -> Registry:
    """构建注册表（内置组件 + 预设 + 安全策略 + 外部插件目录）。"""
    reg = Registry()
    install_defaults(reg)
    register_all_presets(reg)
    # norpagent.safe()：一句话开启全套安全（basic/standard/high）
    # 默认钩子零干预：只挂运行态策略，不挂钩子；--safe-hooks 才显式挂钩子。
    safe_level = getattr(args, "safe", None)
    if safe_level:
        from norpagent import safe

        kit = safe(reg, level=safe_level, hooks=bool(args.safe_hooks))
        mode = "钩子干预已开启" if args.safe_hooks else "钩子零干预（仅运行态策略）"
        print(f"[安全] norpagent.safe() 已开启（级别: {safe_level}，{mode}）")
        for key, val in kit.context.to_dict().items():
            print(f"       {key}={val}")
    # 安全模式：跳过全部插件（插件就是最可能的启动失败源）
    if getattr(args, "safe_mode", False):
        print("[安全模式] 只加载最小化内核：已跳过全部插件目录")
        return reg
    plugin_dirs = list(getattr(args, "plugin_dir", None) or [])
    if plugin_dirs:
        from norpagent.plugins import install_plugin_dirs

        loader = install_plugin_dirs(reg, plugin_dirs, config={
            "plugin_security_audit": "warn",
            "plugin_security_import_restrict": "off",
            "plugin_signature_verify": True,
            "plugin_network_policy": "deny",
            "plugin_isolation": getattr(args, "plugin_isolation", "auto"),
        })
        for info in loader.plugins:
            mark = "OK " if info.enabled else "FAIL"
            print(f"[插件] {mark} {info.name} v{info.version}"
                  f"（签名: {info.signature_status}，工具: {len(info.tools)}）")
            if not info.enabled and info.error:
                print(f"       {info.error.splitlines()[0] if info.error else ''}")
    return reg


def _apply_model_options(reg: Registry, args: Any) -> None:
    """按 CLI 选项重注册模型实例（覆盖 install_defaults 的默认实例）。"""
    if args.model_name or args.base_url or args.api_key:
        from norpagent.builtin.models.openai_compat import OpenAICompatProvider

        reg.register_model(
            "openai_compat",
            OpenAICompatProvider(
                model_name=args.model_name or None,
                base_url=args.base_url or None,
                api_key=args.api_key or None,
            ),
        )
    if args.api_key or args.model_name:
        from norpagent.builtin.models.anthropic import AnthropicProvider

        reg.register_model(
            "anthropic",
            AnthropicProvider(
                model_name=args.model_name or None,
                api_key=args.api_key or None,
            ),
        )


def _list_modes(reg: Registry, stream=None) -> None:
    out = stream or sys.stdout
    out.write("内置预设模式:\n")
    for name in reg.list_presets():
        p = reg.resolve_preset(name)
        out.write(f"  {p.name:<10} {p.description}\n")
        out.write(f"      model={p.model} tools={p.tools}\n")
        if p.components:
            out.write(f"      components={p.components}\n")


def _make_runtime(
    reg: Registry,
    mode: str,
    args: Any,
) -> AgentRuntime:
    from norpagent.kernel.presets import Preset

    preset = reg.resolve_preset(mode)
    model = getattr(args, "model", None)
    session = getattr(args, "session", None)
    ui_name = getattr(args, "ui", None) or preset.ui

    ui = None
    if ui_name == "web":
        from norpagent.builtin.ui.web import WebUI

        ui = WebUI(port=int(getattr(args, "port", None) or 8787))

    if model or session or ui is not None:
        preset = Preset(
            name=preset.name,
            description=preset.description,
            model=model or preset.model,
            tools=list(preset.tools),
            session=session or preset.session,
            sandbox=preset.sandbox,
            scheduler=preset.scheduler,
            ui=ui_name,
            mode=preset.mode,
            params=dict(preset.params),
            components=dict(preset.components),
        )
    return AgentRuntime(reg, preset, ui=ui)


def _run_web(reg: Registry, mode: str, args: Any, prompt: Optional[str]) -> int:
    """Web UI 模式：启动 HTTP + SSE 服务（front.html），任务经 /chat 提交。"""
    from norpagent.builtin.ui.web import WebUI

    # 安全模式：不读 WebUI 设置文件（坏配置可能就是启动失败的原因）。
    # config_path="" = 关闭磁盘读写（None 表示用默认路径）。
    ui = WebUI(port=int(getattr(args, "port", None) or 8787),
               config_path="" if getattr(args, "safe_mode", False) else None)
    from norpagent.kernel.presets import Preset

    preset = reg.resolve_preset(mode)
    if getattr(args, "model", None) or getattr(args, "session", None):
        preset = Preset(
            name=preset.name, description=preset.description,
            model=getattr(args, "model") or preset.model,
            tools=list(preset.tools),
            session=getattr(args, "session") or preset.session,
            sandbox=preset.sandbox, scheduler=preset.scheduler, ui="web",
            mode=preset.mode, params=dict(preset.params),
            components=dict(preset.components),
        )
    agent = AgentRuntime(reg, preset, ui=ui)
    gate = threading.Lock()

    def handler(prompt_text: str, session_id: Optional[str],
                task_params: Optional[dict] = None):
        with gate:  # 同一运行时串行执行任务
            return agent.run(prompt_text, session_id=session_id,
                             task_params=task_params)

    ui.set_handler(handler)
    ui.attach_runtime(agent)
    ui.start()
    print(f"[norpagent] frontend web listening on 127.0.0.1:{ui.port}（/exit 退出）")
    try:
        from norpagent.builtin import list_loaded_lazy_modules

        lazy_loaded = list_loaded_lazy_modules()
        if lazy_loaded:
            print("[norpagent] lazy-loaded modules: " + ", ".join(lazy_loaded))
    except Exception:  # noqa: BLE001 — 缺包环境静默
        pass
    try:
        if prompt:
            task_id = ui.submit(prompt, None)
            print(f"[norpagent] 任务已提交: {task_id}")
        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                break
            if line.strip() in ("/exit", "/quit", "quit", "exit"):
                break
    finally:
        ui.shutdown()
        agent.shutdown()
    return 0


def _run_once(reg: Registry, mode: str, args: Any) -> int:
    try:
        agent = _make_runtime(reg, mode, args)
    except ComponentError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1
    try:
        result = agent.run(args.prompt)
    finally:
        agent.shutdown()
    if not result.ok:
        print(f"\n[失败] {result.status}: {result.error}", file=sys.stderr)
        return 1
    return 0


def _repl(reg: Registry, mode: str, args: Any) -> int:
    try:
        agent = _make_runtime(reg, mode, args)
    except ComponentError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1
    print(f"norpagent 交互模式 [{mode}]。输入 /help 查看命令，/exit 退出。")
    session_id = None
    try:
        while True:
            try:
                line = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in ("/exit", "/quit"):
                break
            if line == "/help":
                print("  /exit     退出\n  /modes    列出预设模式\n  /tools    列出可用工具\n  /reset    开启新会话")
                continue
            if line == "/modes":
                _list_modes(reg)
                continue
            if line == "/tools":
                print("可用工具:", reg.list_tools())
                continue
            if line == "/reset":
                session_id = None
                print("已开启新会话")
                continue
            result = agent.run(line, session_id=session_id)
            session_id = result.session_id
            if result.status == "error":
                print(f"[失败] {result.error}", file=sys.stderr)
    finally:
        agent.shutdown()
    return 0


def _plugin_sign_cmd(args: Any) -> int:
    from norpagent.security.signature import generate_keypair, sign_plugin_file

    if args.gen:
        pair = generate_keypair()
        if pair is None:
            print("[错误] 需要 cryptography 库: pip install norpagent[security]",
                  file=sys.stderr)
            return 1
        pub, priv = pair
        print(f"PUBLIC_KEY = {pub}")
        print(f"PRIVATE_KEY = {priv}")
        print("\n将 PUBLIC_KEY 加入 config → plugin_trusted_keys；"
              "PRIVATE_KEY 请妥善保管，切勿提交到代码库。")
        return 0
    if not args.path or not args.key:
        print("[错误] 需要指定插件文件与 --key（或使用 --gen 生成密钥）", file=sys.stderr)
        return 1
    try:
        block = sign_plugin_file(args.path, args.key)
        print(f"[OK] 已签名: {args.path} -> {args.path}.sig")
        print(f"     public_key = {block['public_key']}")
    except RuntimeError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="norpagent", description="norpagent Agent 框架 CLI")
    parser.add_argument("--list-modes", action="store_true", help="列出全部预设模式")
    parser.add_argument("--mode", "-m", help="预设模式名（minimal/standard/ptc/creative 或自定义）")
    parser.add_argument("--mode-file", "-f", help="创造模式：从 .py 文件加载自定义模式（模块级 PRESET）")
    parser.add_argument("--prompt", "-p", help="单次任务输入（缺省进入交互 REPL）")
    parser.add_argument("--model", help="覆盖预设默认模型（需已注册，如 mock/openai_compat/anthropic）")
    parser.add_argument("--model-name", help="远端模型名（如 deepseek-v4-flash / claude-sonnet-4-5）")
    parser.add_argument("--base-url", help="OpenAI 兼容服务端点（如 https://api.deepseek.com/v1）")
    parser.add_argument("--api-key", help="API Key（缺省读 OPENAI_API_KEY / ANTHROPIC_API_KEY 环境变量）")
    parser.add_argument("--session", help="会话存储后端（memory/sqlite）")
    parser.add_argument("--call-timeout", type=float, default=None, help="单次模型调用硬超时秒数（0=不限）")
    parser.add_argument("--ui", help="UI 适配器覆盖（console/web）")
    parser.add_argument("--port", type=int, default=None, help="Web UI 端口（--ui web 时生效，默认 8787）")
    parser.add_argument("--plugin-dir", action="append", default=None,
                        help="外部插件目录（可重复指定，安全管线：签名→审计→导入限制）")
    parser.add_argument("--plugin-isolation", default="auto",
                        choices=["auto", "inproc", "process"],
                        help="插件隔离模式（auto=按插件 ISOLATION 声明；process=强制进程级隔离）")
    parser.add_argument("--safe", default=None, choices=["basic", "standard", "high"],
                        help="norpagent.safe() 安全级别（运行态策略：审批/审计/签名，默认不挂钩子）")
    parser.add_argument("--safe-hooks", action="store_true",
                        help="配合 --safe：显式开启钩子干预（before_input 越狱拦截 + 提示词加固）")
    parser.add_argument("--safe-mode", action="store_true",
                        help="安全模式：只加载最小化内核（跳过全部插件、不读 WebUI 设置），保留核心回退能力")
    # 插件签名子命令
    sub = parser.add_subparsers(dest="subcmd")
    psign = sub.add_parser("plugin-sign", help="插件签名工具（NORP 插件签名协议 v1）")
    psign.add_argument("path", nargs="?", help="插件入口文件（.py）")
    psign.add_argument("--key", help="Ed25519 私钥 hex（签名用）")
    psign.add_argument("--gen", action="store_true", help="生成一对签名密钥")

    args = parser.parse_args(argv)

    if args.subcmd == "plugin-sign":
        return _plugin_sign_cmd(args)

    # 崩溃救援：消费上次 norpagent-rescue 留下的回退目标——
    # 文件级恢复（WebUI 设置 / 会话文件）已执行，这里把快照里的
    # CLI 参数合并进本次启动（命令行显式给出的参数优先）。
    try:
        from norpagent.recovery import apply_pending_rollback_cli

        pending = apply_pending_rollback_cli()
    except Exception:  # noqa: BLE001
        pending = None
    if pending and isinstance(pending, dict):
        cli_part = pending.get("cli") or {}
        if cli_part.get("mode") and not args.mode and not args.mode_file:
            args.mode = cli_part["mode"]
        if cli_part.get("plugin_dir") and not args.plugin_dir:
            args.plugin_dir = list(cli_part["plugin_dir"])
        if cli_part.get("model") and not args.model:
            args.model = cli_part["model"]
        if cli_part.get("ui") and not args.ui:
            args.ui = cli_part["ui"]
        if cli_part.get("port") and not args.port:
            args.port = cli_part["port"]
        print(f"[norpagent] 已应用崩溃救援回退快照: "
              f"{pending.get('description') or pending.get('snapshot_id') or ''}")

    # 安全模式：强制 minimal 预设（用户指定了别的模式也覆盖——
    # 安全模式的职责就是绕过一切可疑配置）
    if args.safe_mode:
        args.mode = "minimal"
        args.mode_file = None

    try:
        reg = _build_registry(args)
        _apply_model_options(reg, args)
    except Exception as exc:  # noqa: BLE001 — 启动失败：给出自救指引
        print(f"[错误] 启动失败: {exc}", file=sys.stderr)
        _print_rescue_hints()
        return 1

    if args.mode_file:
        preset = load_preset_file(args.mode_file)
        reg.register_preset(preset)
    mode = args.mode or (preset.name if args.mode_file else None)

    if args.list_modes:
        _list_modes(reg)
        return 0
    if not mode:
        parser.print_help()
        print("\n提示: 使用 --list-modes 查看可用模式")
        return 1
    if mode not in reg.list_presets():
        print(f"未知模式 '{mode}'。可用: {reg.list_presets()}", file=sys.stderr)
        return 1
    if args.call_timeout is not None:
        preset = reg.resolve_preset(mode)
        preset.params = dict(preset.params)
        preset.params["call_timeout"] = args.call_timeout

    # 工作回退：CLI 启动基线快照（失败不阻塞启动）
    try:
        from norpagent.recovery import snapshot_cli

        snapshot_cli({
            "mode": mode,
            "model": getattr(args, "model", None),
            "model_name": getattr(args, "model_name", None),
            "base_url": getattr(args, "base_url", None),
            "session": getattr(args, "session", None),
            "ui": getattr(args, "ui", None),
            "port": getattr(args, "port", None),
            "plugin_dir": list(getattr(args, "plugin_dir", None) or []),
            "safe": getattr(args, "safe", None),
            "safe_mode": bool(getattr(args, "safe_mode", False)),
        }, description="CLI 启动基线" + ("（安全模式）" if args.safe_mode else ""),
            tag="baseline")
    except Exception:  # noqa: BLE001
        pass

    try:
        if (args.ui or reg.resolve_preset(mode).ui) == "web":
            return _run_web(reg, mode, args, args.prompt)
        if args.prompt is not None:
            return _run_once(reg, mode, args)
        return _repl(reg, mode, args)
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 运行失败: {exc}", file=sys.stderr)
        _print_rescue_hints()
        return 1


def _print_rescue_hints() -> None:
    """启动 / 运行失败时的自救指引（安全模式 + 崩溃救援）。"""
    print("\n自救指引：", file=sys.stderr)
    print("  1. 安全模式启动（只加载最小化内核，跳过全部插件）：", file=sys.stderr)
    print("       norpagent --safe-mode", file=sys.stderr)
    print("       （代码内：np(safemode='on')）", file=sys.stderr)
    print("  2. 崩溃救援：回退到最后一次正常工作的快照：", file=sys.stderr)
    print("       norpagent-rescue list", file=sys.stderr)
    print("       norpagent-rescue rollback --last-good", file=sys.stderr)
    print("  3. 回退后重新启动即可（回退目标自动消费）。", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())

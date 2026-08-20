# Copyright (c) 2026 xingluosama121, MIT Licensed
"""模块流程编排内核（FLOW）：把可视化画布真实对接注册表。

「模块流程」（/flow）不是动画演示，而是用真实注册组件执行画布图：

1. ``build_snapshot(registry, agent)``
   注册表快照：模型 / 工具 / 会话 / 沙箱 / 调度器 / 插件 / 预设 / 钩子。
   前端「核心模块坞」据此按真实组件渲染卡片（每个已注册组件一张卡）。

2. ``ModuleWorkspace.register(...)``
   「文件即模块」的真实注册：拖入 .py 走完整安全管线
   （签名校验 -> AST 审计 -> 导入限制 -> 注册进注册表）；
   .json 注册为纯描述模块（直通节点）；其余类型明确报错，
   由前端回退到官方模块。注册成功的插件钩子会逐个成为
   画布上的钩子节点（一个钩子 = 一个节点）。

3. ``FlowRunner``
   按画布图（节点 + beam）拓扑执行。每个节点独立 try/except：
   单节点失败记录 error 但不中断整条链路（零中断语义）。
   进度经 publish 回调以 ``flow.*`` 事件推送，Web UI 复用
   SSE 通道（/events）实时送达浏览器。

节点执行语义（type -> 真实动作）：

- trigger  读取 prompt 输入，产出 start 信号；
- model    调用注册表真实模型（默认引擎预设模型，可逐节点覆盖）；
            tools 端口 = 容器挂载的工具集（自动解析 schema 传给 provider）；
            system_prompt 端口 = 系统提示词（beam 值 > 输入面板 > 节点
            配置 > 引擎预设参数，空值不注入 system 消息）；
- tool     调用注册表真实工具：每个输入端口 = 一个 schema 参数
            （不再是全局 query/result 黑箱）；
- toolbox  工具容器：输入端口 = 成员工具参数的并集（按端口名扇出投递），
            输出端口 = 每个成员的「工具名.端口名」限定名 + tools 打包端口；
- sandbox  在注册表真实沙箱里执行 code（子进程隔离）；
- security 对 payload 做越狱/注入扫描（norpagent.security.guard）；
- session  读写会话管理器（默认引擎会话存储）；
- plugin   插件容器（members = 工具+钩子成员，端口并集语义）或
            独立插件工具执行；
- hook     触发插件的单个钩子（一个钩子 = 一个节点；可变钩子
            走 intercept，返回值成为节点输出）；
- other    直通（payload 原样转发）；
- output   汇总最终结果；
- path     路径模块：产出经过公共路径安全校验（拒绝绝对路径 /
           .. 穿越）的相对路径值，beam 到任何工具的 path 输入端口；
           空值 = 工作区根目录 "."；
- file     文件模块：已注册为插件则按插件执行，否则直通。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from norpagent.kernel.context import RunContext
from norpagent.kernel.registry import ComponentError

# 插件钩子名 -> 旧格式钩子签名的业务参数键（与 plugins/loader.py 对齐，
# 用于把钩子节点的输入 payload 映射为钩子函数实参）。
HOOK_ARG_KEYS: Dict[str, Tuple[str, ...]] = {
    "on_agent_init": (),
    "on_agent_shutdown": (),
    "on_task_start": ("user_input",),
    "on_task_done": ("content",),
    "on_task_error": ("error",),
    "on_task_stopped": ("reason",),
    "on_task_timeout": ("timeout",),
    "before_step": ("step", "messages"),
    "after_step": ("step", "content", "tool_calls"),
    "before_tool_call": ("tool_name", "args"),
    "after_tool_call": ("tool_name", "args"),
    "on_user_input_required": ("question",),
    "on_reasoning": ("content",),
    "on_content": ("content",),
    "on_event": ("event_type", "data"),
    "on_usage_update": ("total",),
}

_MUTATING_HOOKS = {"before_step", "before_tool_call", "after_tool_call"}

# 画布节点类型与注册表组件种类的映射（前端模块坞分组 -> 执行语义）
NODE_KIND_GROUP = {
    "model": "models",
    "tool": "tools",
    "session": "sessions",
    "sandbox": "sandboxes",
    "scheduler": "schedulers",
    "plugin": "plugins",
    "preset": "presets",
    "hook": "plugins",
}

_MAX_MODULE_SIZE = 200 * 1024  # 单文件模块上限 200KB
_MAX_OUTPUT_CHARS = 4000       # 单条事件输出截断长度


def default_modules_dir() -> str:
    """流程模块落盘目录（文件即模块的 .py / .json 存放处）。

    环境变量 NORPAGENT_FLOW_MODULES 可覆盖；默认 ~/.norpagent/flow_modules。
    """
    env = os.environ.get("NORPAGENT_FLOW_MODULES")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".norpagent", "flow_modules")


def _truncate(text: Any, limit: int = _MAX_OUTPUT_CHARS) -> str:
    s = "" if text is None else str(text)
    if len(s) > limit:
        return s[:limit] + f"\n... [截断 {len(s) - limit} 字符]"
    return s


def _pick_color(name: str) -> str:
    palette = ["#c084fc", "#22d3ee", "#fb7185", "#a3e635", "#38bdf8",
               "#facc15", "#f472b6", "#4ade80", "#ffd166", "#94a3b8"]
    h = 0
    for ch in name or "module":
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return palette[h % len(palette)]


def _extract_ports(text: str, tag: str) -> List[str]:
    """从模块源码注释 / JSON 中提取 @in / @out 端口标记。"""
    arr = re.search(tag + r'["\s:=]*\[([^\]]*)\]', text)
    if arr:
        names = re.findall(r'["\']?([\w-]{1,16})["\']?', arr.group(1))
        return [n for n in names if n][:4]
    return [m.group(1) for m in re.finditer(
        tag + r'\s*:?\s*[\'"\[]?([\w-]{1,16})', text)][:4]


def _extract_module_name(text: str, fallback: str) -> str:
    m = re.search(r'@module["\'\s:=]*["\']?([\w\u4e00-\u9fa5-]{1,32})', text)
    return (m.group(1) if m else fallback).strip()


def _norm_tool_schema(schema: Any, name: str = "unknown") -> Dict[str, Any]:
    """把工具 schema 规范成 OpenAI ``{"type":"function","function":{...}}`` 形态。

    OpenAI 兼容端点要求 ``tools[].type`` 字段。此前直接把 function 包装
    剥掉后传给 provider，内层 dict 缺 ``type``，远端直接 400
    （missing field `type`）。三种输入统一处理：

    - 已带 function 包装：保留 function，补上 type；
    - 裸 schema：整体包进 function；
    - 空 / 非 dict：给最小合法定义（name 兜底）。
    """
    if isinstance(schema, dict):
        fn = schema.get("function")
        inner = dict(fn) if isinstance(fn, dict) else dict(schema)
        if not inner.get("name"):
            inner["name"] = str(name or "unknown")
        return {"type": "function", "function": inner}
    return {"type": "function", "function": {"name": str(name or "unknown")}}


def _tool_ports(schema: Any) -> Tuple[List[str], List[str]]:
    """从 OpenAI function schema 提取工具的真实钩子端口。

    - 输入端口 = schema 参数名（必填参数排前，最多 8 个）；
    - 输出端口 = result / success。
    解析失败或 schema 为空时回退到 query / result。
    """
    try:
        func = schema.get("function", schema) if isinstance(schema, dict) else {}
        params = func.get("parameters") or {}
        props = params.get("properties") or {}
        if isinstance(props, dict) and props:
            required = list(params.get("required") or ())
            names = [str(k) for k in props.keys()][:8]
            names = sorted(names, key=lambda k: (k not in required, names.index(k)))
            return names, ["result", "success"]
    except Exception:  # noqa: BLE001
        pass
    return ["query"], ["result"]


# ── 注册表快照 ────────────────────────────────────────────


def build_snapshot(registry: Any, agent: Any = None,
                   engine_state: str = "unknown") -> Dict[str, Any]:
    """注册表 -> 模块清单快照（驱动前端模块坞 / 节点实例选择）。"""
    try:
        from norpagent import __version__
    except Exception:  # noqa: BLE001
        __version__ = "?"

    tool_plugin: Dict[str, str] = {}
    plugins: Dict[str, Any] = {}
    try:
        for pname, plugin in sorted((getattr(registry, "_plugins", {}) or {}).items()):
            tools = [getattr(t, "name", "") for t in plugin.get_tools()
                     if getattr(t, "name", "")]
            hooks = list((plugin.get_hooks() or {}).keys())
            for t in tools:
                tool_plugin[t] = pname
            plugins[pname] = {
                "name": pname,
                "version": str(getattr(plugin, "version", "") or ""),
                "publisher": str(getattr(plugin, "publisher", "") or ""),
                "description": str(getattr(plugin, "description", "") or ""),
                "tools": tools,
                "hooks": [{"name": h, "args": list(HOOK_ARG_KEYS.get(h, ()))}
                          for h in hooks],
            }
    except Exception:  # noqa: BLE001 — 快照必须永不抛出
        pass

    tools: List[Dict[str, Any]] = []
    for name in registry.list_tools():
        desc = ""
        ins, outs = ["query"], ["result"]
        try:
            schema = registry.resolve_tool(name).schema() or {}
            func = schema.get("function", schema) if isinstance(schema, dict) else {}
            desc = str(func.get("description", "") or "").strip()
            ins, outs = _tool_ports(schema)
        except Exception:  # noqa: BLE001
            pass
        tools.append({
            "name": name,
            "description": desc[:160],
            "plugin": tool_plugin.get(name, ""),
            "ins": ins,
            "outs": outs,
        })

    presets: List[Dict[str, Any]] = []
    try:
        for pname in registry.list_presets():
            preset = registry.resolve_preset(pname)
            presets.append({
                "name": pname,
                "model": getattr(preset, "model", "") or "",
                "tools": list(getattr(preset, "tools", ()) or ()),
                "mode": getattr(preset, "mode", "") or "",
                "description": (getattr(preset, "description", "") or "")[:120],
            })
    except Exception:  # noqa: BLE001
        pass

    security: Dict[str, Any] = {"installed": False}
    sec = getattr(registry, "security", None)
    if sec is not None:
        security = {
            "installed": True,
            "level": str(getattr(sec, "level", "") or ""),
            "audit_level": str(getattr(sec, "audit_level", "") or ""),
            "import_restrict": str(getattr(sec, "import_restrict", "") or ""),
        }

    hook_names: List[str] = []
    try:
        hook_names = registry.hooks.list_hook_names()
    except Exception:  # noqa: BLE001
        pass

    preset_name = ""
    model_name = ""
    agent_tools: List[str] = []
    if agent is not None:
        preset_name = getattr(getattr(agent, "preset", None), "name", "") or ""
        model_name = getattr(getattr(agent, "preset", None), "model", "") or ""
        agent_tools = list(
            getattr(getattr(agent, "preset", None), "tools", ()) or ())
    model_names = registry.list_models()
    default_model = model_name or (model_names[0] if model_names else "")

    # 工作区根目录（路径模块的基准目录，与文件类工具 pathsafe 取值一致）
    workspace_root = os.getcwd()
    try:
        params = getattr(agent, "params", None) or {}
        if isinstance(params, dict) and str(params.get("workspace_root") or ""):
            workspace_root = os.path.abspath(
                os.path.expanduser(str(params["workspace_root"])))
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True,
        "version": __version__,
        "engine_state": engine_state,
        "preset": preset_name,
        "default_model": default_model,
        "agent_tools": agent_tools,
        "security": security,
        "workspace_root": workspace_root,
        "groups": {
            "models": [
                {"name": n, "default": n == default_model}
                for n in model_names
            ],
            "tools": tools,
            "sessions": [{"name": n} for n in registry.list_sessions()],
            "sandboxes": [{"name": n} for n in registry.list_sandboxes()],
            "schedulers": [{"name": n} for n in registry.list_schedulers()],
            "plugins": list(plugins.values()),
            "presets": presets,
        },
        "hooks": hook_names,
        "modules_dir": default_modules_dir(),
    }


# ── 文件即模块：真实注册 ───────────────────────────────────


class ModuleWorkspace:
    """流程模块工作区：落盘 + 安全管线注册 + 幂等去重。

    同一文件内容重复拖入不会重复订阅钩子（幂等）；
    .py 走 PluginLoader 完整管线；.json 为纯描述模块。
    """

    def __init__(self, directory: Optional[str] = None,
                 config: Optional[Dict[str, Any]] = None) -> None:
        self.directory = os.path.abspath(
            directory or default_modules_dir()
        )
        os.makedirs(self.directory, exist_ok=True)
        self.config = dict(config or {})
        self._loader: Any = None
        self._content_hash: Dict[str, str] = {}
        self._defs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _get_loader(self) -> Any:
        if self._loader is None:
            from norpagent.plugins.loader import PluginLoader

            self._loader = PluginLoader([self.directory], config=self.config)
        return self._loader

    @staticmethod
    def _safe_filename(name: str, ext: str) -> str:
        stem = os.path.splitext(name)[0]
        stem = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", stem).strip("_")
        return f"{stem or 'module'}.{ext}"

    @staticmethod
    def _parse_py_meta(content: str) -> Dict[str, Any]:
        """解析 .py 文件的模块头声明（模块级变量）。

        支持：``__norpagent_type__`` / ``__norpagent_name__`` /
        ``__norpagent_desc__`` / ``__norpagent_params__`` /
        ``__norpagent_hooks__``。未声明时 type 回退 other。
        """
        meta: Dict[str, Any] = {"mtype": "", "name": "", "desc": "",
                                "params": None, "hooks": None}
        for key, raw in re.findall(
                r"^__norpagent_(type|name|desc|params|hooks)__\s*=\s*(.+)$",
                content, re.MULTILINE):
            val = raw.strip().rstrip(",").strip()
            out_key = "mtype" if key == "type" else key
            if key in ("params", "hooks"):
                try:
                    import ast
                    meta[out_key] = ast.literal_eval(val)
                except Exception:  # noqa: BLE001
                    meta[out_key] = None
                continue
            m = re.match(r"^([\"'])(.*)\1$", val)
            meta[out_key] = m.group(2) if m else val
        return meta

    def register(self, registry: Any, name: str,
                 content: str) -> Dict[str, Any]:
        """注册一个拖入的模块文件，返回 {ok, module|reason}。

        - .py：完整插件安全管线注册；模块头声明决定前端路由类型；
        - .json / .yaml / .yml：描述模块（``type`` 字段声明路由类型）；
        - .html / .htm / .js / .ts：前端模块（FE），落盘并托管到
          ``/fe/<safe_name>`` 供「新标签页打开」。
        """
        content = content or ""
        if len(content.encode("utf-8", errors="replace")) > _MAX_MODULE_SIZE:
            return {"ok": False, "reason": "模块文件过大（上限 200KB）"}
        ext = (os.path.splitext(name)[1] or "").lstrip(".").lower()
        with self._lock:
            if ext == "py":
                return self._register_python(registry, name, content)
            if ext == "json":
                return self._register_json(name, content)
            if ext in ("yaml", "yml"):
                return self._register_yaml(name, content)
            if ext in ("html", "htm", "js", "ts"):
                return self._register_frontend(name, content, ext)
            if ext in ("md", "txt"):
                return {
                    "ok": False,
                    "reason": (
                        "后端无法执行 .%s 文件——真实注册仅支持 "
                        ".py 插件（完整安全管线）与 .json/.yaml 模块描述、"
                        ".html/.js/.ts 前端模块" % ext
                    ),
                }
            return {"ok": False, "reason": f"不支持的扩展名 .{ext or '?'}"}

    def _register_python(self, registry: Any, name: str,
                         content: str) -> Dict[str, Any]:
        fname = self._safe_filename(name, "py")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if self._content_hash.get(fname) == digest and fname in self._defs:
            return {"ok": True, "module": dict(self._defs[fname]),
                    "cached": True}

        path = os.path.join(self.directory, fname)
        tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)

        loader = self._get_loader()
        info = self._load_single(registry, loader, fname, path)
        if info is None:
            return {"ok": False, "reason": "插件文件未被加载（目录扫描失败）"}
        if not getattr(info, "enabled", True):
            return {"ok": False, "reason": str(getattr(info, "error", "") or "插件加载失败")}

        module_name = getattr(info, "name", "") or os.path.splitext(fname)[0]
        meta = self._parse_py_meta(content)
        ins = _extract_ports(content, "@in") or [
            h for h in getattr(info, "hook_names", ()) or ()][:4] or ["input"]
        outs = _extract_ports(content, "@out") or list(
            getattr(info, "tools", ()) or ())[:4] or ["output"]
        color = _pick_color(module_name)
        mcolor = re.search(r'@color["\'\s:=]*["\']?(#[0-9a-fA-F]{3,6})', content)
        if mcolor:
            color = mcolor.group(1)

        module_def = {
            "name": meta.get("name") or module_name,
            "desc": (meta.get("desc") or getattr(info, "description", "")
                     or f"插件模块 · {module_name}")[:160],
            "color": color,
            "kind": "file",
            "mtype": str(meta.get("mtype") or "other")[:32],
            "params": meta.get("params"),
            "declared_hooks": meta.get("hooks"),
            "plugin": module_name,
            "tools": list(getattr(info, "tools", ()) or ()),
            "hooks": [{"name": h, "args": list(HOOK_ARG_KEYS.get(h, ()))}
                      for h in (getattr(info, "hook_names", ()) or ())],
            "signature_status": getattr(info, "signature_status", "") or "",
            "audit_issues": len(getattr(info, "audit_issues", ()) or ()),
            "ins": ins,
            "outs": outs,
        }
        self._content_hash[fname] = digest
        self._defs[fname] = dict(module_def)
        return {"ok": True, "module": module_def}

    @staticmethod
    def _load_single(registry: Any, loader: Any, fname: str,
                     path: str) -> Any:
        """加载单个插件文件（复用 PluginLoader 安全管线）。"""
        before = len(loader.plugins)
        loader._load_from_file(registry, os.path.splitext(fname)[0], path,
                               manifest=None)  # noqa: SLF001 — 单文件管线入口
        return loader.plugins[before] if len(loader.plugins) > before else None

    def _register_json(self, name: str, content: str) -> Dict[str, Any]:
        try:
            data = json.loads(content)
        except ValueError as exc:
            return {"ok": False, "reason": f"JSON 解析失败: {exc}"}
        if not isinstance(data, dict):
            return {"ok": False, "reason": "模块描述必须为 JSON 对象"}
        if not (data.get("@module") or data.get("type")):
            return {"ok": False, "reason": "缺少 @module 或 type 声明（不是有效的模块描述）"}
        return self._register_described(name, data)

    def _register_yaml(self, name: str, content: str) -> Dict[str, Any]:
        """.yaml/.yml 描述模块（零依赖解析，与 JSON 同构）。"""
        try:
            import yaml  # noqa: F401  可选依赖
        except ImportError:
            return {"ok": False, "reason": "未安装 PyYAML（pip install pyyaml），无法解析 .yaml 描述"}
        try:
            data = yaml.safe_load(content)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"YAML 解析失败: {exc}"}
        if not isinstance(data, dict):
            return {"ok": False, "reason": "模块描述必须为 YAML 对象"}
        if not (data.get("@module") or data.get("type")):
            return {"ok": False, "reason": "缺少 @module 或 type 声明（不是有效的模块描述）"}
        return self._register_described(name, data)

    def _register_described(self, name: str,
                            data: Dict[str, Any]) -> Dict[str, Any]:
        """描述类模块（.json/.yaml）统一注册：type 字段 = 前端路由类型。"""
        mtype = str(data.get("type") or data.get("@type") or "other")[:32]
        mname = str(data.get("name") or data.get("@module"))[:32]
        ins = [str(k) for k in (data.get("@in") or data.get("ins") or ["input"]) if k][:4]
        outs = [str(k) for k in (data.get("@out") or data.get("outs") or ["output"]) if k][:4]
        color = str(data.get("@color") or data.get("color") or _pick_color(mname))
        module_def = {
            "name": mname,
            "desc": str(data.get("desc") or data.get("@desc")
                        or f"描述模块 · {mname}")[:160],
            "color": color,
            "kind": "file",
            "mtype": mtype,
            "params": data.get("params"),
            "declared_hooks": data.get("hooks"),
            "plugin": "",
            "tools": [],
            "hooks": [],
            "signature_status": "",
            "audit_issues": 0,
            "ins": ins or ["input"],
            "outs": outs or ["output"],
        }
        fname = self._safe_filename(name, "json")
        self._defs[fname] = dict(module_def)
        return {"ok": True, "module": module_def}

    def _register_frontend(self, name: str, content: str,
                           ext: str) -> Dict[str, Any]:
        """前端模块（.html/.htm/.js/.ts）：落盘并托管到 /fe/<safe_name>。

        浏览器「新标签页打开」即访问该 URL；module_def.kind=frontend
        让前端把拖入的文件路由成 FE 节点（设置端口 = 该 FE 自己的配置项）。
        """
        fname = self._safe_filename(name, ext)
        path = os.path.join(self.directory, fname)
        tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp, path)
        except OSError as exc:
            return {"ok": False, "reason": f"前端模块落盘失败: {exc}"}
        stem = os.path.splitext(fname)[0]
        module_def = {
            "name": stem,
            "desc": f"前端模块 · {stem}（.{ext}）",
            "color": "#4ade80",
            "kind": "frontend",
            "mtype": "frontend",
            "format": ext,
            "url": f"/fe/{fname}",
            "params": None,
            "declared_hooks": None,
            "plugin": "",
            "tools": [],
            "hooks": [],
            "ins": [],
            "outs": [],
        }
        self._defs[fname] = dict(module_def)
        return {"ok": True, "module": module_def}


# ── 流程执行器 ─────────────────────────────────────────────


def normalize_graph(graph: Dict[str, Any]) -> Dict[str, Any]:
    """把前端保存 / 导出的图规范化为 FlowRunner 执行格式。

    前端「自动保存」「导出流程」共用同一序列化格式（含 x/y 坐标、
    container / members 展示字段）；画布 RUN 时 buildGraph 会额外
    写入 config.delegate_to（容器成员委托）与 config.members /
    config.tools（容器成员清单）。这里补齐这些运行时字段，
    保证「应用到智能体」的聊天任务与画布 RUN 的语义完全一致。
    对 buildGraph 输出幂等（字段已存在时不动）。
    """
    g = dict(graph or {})
    nodes = [dict(n) for n in (g.get("nodes") or [])
             if isinstance(n, dict)]
    by_id: Dict[str, Dict[str, Any]] = {}
    for n in nodes:
        nid = str(n.get("id") or "")
        if nid:
            by_id[nid] = n
    for n in nodes:
        cfg = n.get("config")
        if not isinstance(cfg, dict):
            cfg = {}
            n["config"] = cfg
        container = n.get("container")
        if container and not cfg.get("delegate_to"):
            cfg["delegate_to"] = str(container)
        member_ids = n.get("members")
        if isinstance(member_ids, list) and member_ids \
                and "members" not in cfg:
            items: List[Dict[str, str]] = []
            tool_names: List[str] = []
            for mid in member_ids:
                m = by_id.get(str(mid))
                if not m:
                    continue
                kind = str(m.get("type") or "")
                mcfg = m.get("config") if isinstance(m.get("config"), dict) else {}
                name = str(mcfg.get("tool") or mcfg.get("hook")
                           or m.get("label") or "")
                if not name:
                    continue
                items.append({"kind": kind, "name": name,
                              "plugin": str(mcfg.get("plugin") or "")})
                if kind == "tool":
                    tool_names.append(name)
            cfg["members"] = items
            cfg["tools"] = tool_names
    g["nodes"] = nodes
    return g


class FlowRunner:
    """按画布图拓扑执行真实组件。

    publish(item) 回调接收 {type, payload, ts} 事件字典：
    flow.node_start / flow.node_output / flow.node_done /
    flow.node_error / flow.done / flow.log。
    """

    def __init__(self, registry: Any, agent: Any = None,
                 publish: Optional[Callable[[Dict[str, Any]], None]] = None,
                 flow_id: Optional[str] = None,
                 workspace: Optional[ModuleWorkspace] = None) -> None:
        self.registry = registry
        self.agent = agent
        self.publish = publish or (lambda item: None)
        self.flow_id = flow_id or uuid.uuid4().hex[:12]
        self.workspace = workspace
        self._stop = threading.Event()

    # ── 事件 ──────────────────────────────────────────────

    def _pub(self, etype: str, **payload: Any) -> None:
        try:
            self.publish({
                "type": etype,
                "payload": {"flow_id": self.flow_id, **payload},
                "ts": time.time(),
            })
        except Exception:  # noqa: BLE001 — 事件失败不能中断流程
            pass

    def request_stop(self) -> None:
        self._stop.set()

    # ── 主流程 ────────────────────────────────────────────

    def run(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        """执行画布图，返回 {status, final_output, errors, interrupts, nodes}。"""
        raw_nodes = graph.get("nodes") or []
        links = graph.get("links") or []
        top_prompt = str(graph.get("prompt") or "")

        node_by_id: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for n in raw_nodes:
            nid = str(n.get("id") or f"n{len(order)}")
            if nid in node_by_id:
                continue
            node = dict(n)
            node["id"] = nid
            node.setdefault("type", "other")
            node.setdefault("config", {})
            node.setdefault("inputs", {})
            node_by_id[nid] = node
            order.append(nid)

        # 入度 / 邻接（按节点粒度拓扑）
        indeg = {nid: 0 for nid in order}
        adj: Dict[str, List[str]] = {nid: [] for nid in order}
        for l in links:
            f, t = (l.get("from") or {}).get("id"), (l.get("to") or {}).get("id")
            if f in node_by_id and t in node_by_id and f != t:
                indeg[t] += 1
                adj[f].append(t)

        queue = [nid for nid in order if indeg[nid] == 0]
        outputs: Dict[str, Dict[str, str]] = {}
        errors = 0
        done: Set[str] = set()

        self._pub("flow.log", level="info",
                  message=f"拓扑解析完成 · {len(order)} 节点 / {len(links)} beam")
        if not queue:
            # 全环（画布侧已拒绝环路，这里防御）：退化为声明顺序
            queue = list(order)

        while queue:
            if self._stop.is_set():
                break
            nid = queue.pop(0)
            if nid in done or nid not in node_by_id:
                continue
            done.add(nid)
            node = node_by_id[nid]

            # 汇集上游输入：{port: value}
            payload: Dict[str, str] = {}
            for l in links:
                f, t = (l.get("from") or {}), (l.get("to") or {})
                if t.get("id") == nid and f.get("id") in outputs:
                    payload[t.get("port") or "any"] = \
                        outputs[f["id"]].get(f.get("port") or "", "")

            # 容器成员委托：属于某容器且没有「容器外」输入时跳过个体执行，
            # 由容器节点（toolbox / plugin 容器）按端口并集统一执行，
            # 避免成员工具被重复调用（画布上成员只是可视化的钩子视图）。
            # 例外：成员被外部连线直接消费（有容器外的下游 beam）时，
            # 单独执行一次，保证下游不悬挂。
            delegate_to = str((node.get("config") or {}).get("delegate_to") or "")
            if delegate_to:
                incoming = [l for l in links
                            if (l.get("to") or {}).get("id") == nid
                            and (l.get("from") or {}).get("id") in node_by_id]
                from_inside = all(
                    str((node_by_id.get(l.get("from", {}).get("id"), {})
                         .get("config") or {}).get("delegate_to") or "")
                    == delegate_to
                    for l in incoming
                )
                wants_output = any(
                    (l.get("from") or {}).get("id") == nid
                    and (l.get("to") or {}).get("id") != delegate_to
                    and str((node_by_id.get((l.get("to") or {}).get("id"), {})
                             .get("config") or {}).get("delegate_to") or "")
                        != delegate_to
                    for l in links
                )
                if (not incoming or from_inside) and not wants_output:
                    self._pub("flow.node_done", node_id=nid,
                              label=str(node.get("label") or nid),
                              status="boxed")
                    outputs[nid] = {}
                    for m in adj[nid]:
                        indeg[m] -= 1
                        if indeg[m] == 0 and m not in done:
                            queue.append(m)
                    continue

            self._pub("flow.node_start", node_id=nid,
                      label=str(node.get("label") or nid),
                      type=str(node.get("type") or "other"))
            try:
                result = self._execute_node(node, payload, top_prompt)
            except Exception as exc:  # noqa: BLE001 — 单节点失败不中断整链
                errors += 1
                self._pub("flow.node_error", node_id=nid,
                          label=str(node.get("label") or nid),
                          error=f"{type(exc).__name__}: {exc}")
                outputs[nid] = {}
                self._pub("flow.node_done", node_id=nid,
                          label=str(node.get("label") or nid),
                          status="error")
            else:
                outs = {str(k): str(v) for k, v in (result or {}).items()}
                outputs[nid] = outs
                for port, value in outs.items():
                    self._pub("flow.node_output", node_id=nid, port=port,
                              output=_truncate(value), stream=False)
                self._pub("flow.node_done", node_id=nid,
                          label=str(node.get("label") or nid),
                          status="done", outputs={
                              k: _truncate(v) for k, v in outs.items()})

            for m in adj[nid]:
                indeg[m] -= 1
                if indeg[m] == 0 and m not in done:
                    queue.append(m)

        # 不可达节点（缺上游）标记等待，不中断
        for nid in order:
            if nid not in done:
                self._pub("flow.node_done", node_id=nid,
                          label=str(node_by_id[nid].get("label") or nid),
                          status="wait")

        final = self._final_output(node_by_id, outputs)
        status = "stopped" if self._stop.is_set() else "done"
        interrupts = 1 if self._stop.is_set() else 0
        self._pub("flow.done", status=status, final_output=_truncate(final),
                  errors=errors, interrupts=interrupts,
                  nodes=len(order), steps=len(done))
        return {
            "status": status,
            "final_output": final,
            "errors": errors,
            "interrupts": interrupts,
            "nodes": len(order),
        }

    @staticmethod
    def _final_output(node_by_id: Dict[str, Any],
                      outputs: Dict[str, Dict[str, str]]) -> str:
        for nid, node in node_by_id.items():
            if node.get("type") == "output":
                out = outputs.get(nid) or {}
                return out.get("final") or " / ".join(
                    v for v in out.values() if v) or ""
        for nid in reversed(list(node_by_id)):
            out = outputs.get(nid) or {}
            if out:
                return " / ".join(v for v in out.values() if v) or ""
        return ""

    # ── 节点执行 ──────────────────────────────────────────

    def _execute_node(self, node: Dict[str, Any],
                      payload: Dict[str, str], top_prompt: str) -> Dict[str, str]:
        ntype = str(node.get("type") or "other")
        config = node.get("config") or {}
        inputs = node.get("inputs") or {}
        text = self._primary_input(node, payload, inputs)
        ctx = self._make_ctx(node)

        if ntype == "trigger":
            prompt = str(inputs.get("prompt") or top_prompt or "")
            self._pub("flow.log", level="info",
                      message=f"触发信号 · prompt 长度 {len(prompt)}")
            return {"start": prompt}
        if ntype == "model":
            return self._exec_model(node, text, config, payload)
        if ntype == "tool":
            return self._exec_tool(node, text, config, ctx, payload)
        if ntype == "toolbox":
            # 工具容器：按端口并集批量执行全部成员工具
            return self._exec_toolbox(node, payload, config, ctx)
        if ntype == "sandbox":
            return self._exec_sandbox(node, text, config)
        if ntype == "security":
            return self._exec_security(text)
        if ntype == "session":
            return self._exec_session(text, config)
        if ntype == "plugin":
            if config.get("members") is not None:
                # 插件容器：与工具容器相同的端口并集执行语义
                return self._exec_toolbox(node, payload, config, ctx)
            return self._exec_plugin(node, text, config, ctx)
        if ntype == "hook":
            return self._exec_hook(text, config, ctx, inputs)
        if ntype == "output":
            return {"final": text}
        if ntype == "path":
            # 路径模块：产出安全校验后的相对路径值
            return self._exec_path(node, payload, inputs)
        if ntype == "scheduler":
            return self._exec_scheduler(text, config)
        if ntype == "context_store":
            return self._exec_context_store(payload, inputs, ctx)
        if ntype == "project_manager":
            return self._exec_project_manager(text, ctx)
        if ntype == "preset":
            return self._exec_preset(config)
        if ntype in ("frontend", "settings"):
            # FE 前端节点 / 全局设置节点：设置项端口（beam 值 > 面板值）
            # scope=global（settings 节点）时写入全局配置
            return self._exec_frontend(node, payload, inputs, ntype == "settings")
        if ntype == "ui":
            return self._exec_ui(text)
        if ntype == "async_loop":
            # 事件循环节点：任务提交直通（真实循环由引擎持有）
            return {"result": text}
        if ntype == "agent_runtime":
            return self._exec_agent_runtime(text, config)
        if ntype in ("file", "file:module"):
            if config.get("plugin"):
                return self._exec_plugin(node, text, config, ctx)
            return {"output": text}
        # other / 未知类型：直通
        return {"any": text}

    @staticmethod
    def _primary_input(node: Dict[str, Any], payload: Dict[str, str],
                       inputs: Dict[str, Any]) -> str:
        ntype = str(node.get("type") or "other")
        own = {
            "trigger": "prompt", "model": "prompt", "tool": "query",
            "sandbox": "code", "security": "payload", "session": "context",
            "plugin": "query", "hook": "payload", "output": "final",
            "other": "any", "file": "any", "toolbox": "", "path": "value",
        }.get(ntype, "any")
        if ntype == "tool":
            # 工具：每个输入端口 = 一个 schema 参数，由 _exec_tool 直接消费；
            # 这里只提供未连线时的回退文本
            for port, value in (payload or {}).items():
                if port == "query" and value:
                    return str(value)
            return str(inputs.get("query") or inputs.get("value") or "")
        if ntype == "model":
            # 模型：prompt + context；tools 端口属于工具集挂载，
            # 绝不混入提示词文本
            values: List[str] = []
            for port, value in (payload or {}).items():
                if port in ("prompt", "context") and value:
                    if port == "prompt":
                        values.insert(0, str(value))
                    else:
                        values.append(str(value))
            if values:
                return "\n".join(values)
            return str(inputs.get("value") or inputs.get("prompt") or "")
        values = []
        for port, value in (payload or {}).items():
            if value is None:
                continue
            if port == own:
                values.insert(0, str(value))
            else:
                values.append(str(value))
        if values:
            return "\n".join(v for v in values if v is not None)
        if ntype == "sandbox":
            return str(inputs.get("code") or "echo NORP-FLOW-SANDBOX")
        return str(inputs.get("value") or inputs.get(own) or "")

    def _make_ctx(self, node: Dict[str, Any]) -> RunContext:
        agent = self.agent
        config = (node.get("config") or {}).get("params") or {}
        params: Dict[str, Any] = {}
        if agent is not None:
            params.update(getattr(agent, "params", None) or {})
        if isinstance(config, dict):
            params.update(config)
        return RunContext(
            registry=self.registry,
            session_manager=getattr(agent, "session_manager", None) or None,
            session_id=getattr(self, "_flow_session_id", None) or "",
            sandbox=getattr(agent, "sandbox", None) or None,
            scheduler=getattr(agent, "scheduler", None) or None,
            ui=getattr(agent, "ui", None) or None,
            params=params,
            task_id=self.flow_id,
            preset_name=getattr(getattr(agent, "preset", None), "name", "") or "",
            components=getattr(agent, "components", None) or {},
        )

    # ── 新类型节点（调度器 / 上下文库 / 项目管理 / 预设 / FE / UI / Agent 循环） ──

    def _exec_scheduler(self, text: str,
                        config: Dict[str, Any]) -> Dict[str, str]:
        """调度器节点：向引擎调度器提交一个长周期任务。"""
        sched = getattr(self.agent, "scheduler", None) if self.agent is not None else None
        if sched is None or not text:
            return {"submitted": ""}
        try:
            task = sched.submit(text)
            tid = str(getattr(task, "id", "") or task)
            self._pub("flow.log", level="info",
                      message=f"调度器提交任务 <b>{tid or '?'}</b>")
            return {"submitted": tid}
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"调度器提交失败: {exc}") from exc

    def _exec_context_store(self, payload: Optional[Dict[str, str]],
                            inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, str]:
        """上下文库节点：op=add/search/list/delete + query。"""
        comp = ctx.context_store
        if comp is None:
            comp = (getattr(self.agent, "components", None) or {}).get("context_store")
        op = str((payload or {}).get("op") or inputs.get("op") or "search").strip()
        query = str((payload or {}).get("query") or inputs.get("query") or "").strip()
        if comp is None:
            raise RuntimeError("引擎未装配 context_store 组件")
        try:
            if op == "add":
                if query and hasattr(comp, "add"):
                    comp.add(query)
                return {"result": "added"}
            if op == "list":
                items = comp.list() if hasattr(comp, "list") else []
                return {"result": json.dumps(items, ensure_ascii=False)[:_MAX_OUTPUT_CHARS]}
            if op == "delete":
                if query and hasattr(comp, "delete"):
                    comp.delete(query)
                return {"result": "deleted"}
            res = comp.search(query) if hasattr(comp, "search") else []
            return {"result": json.dumps(res, ensure_ascii=False)[:_MAX_OUTPUT_CHARS]}
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"上下文库操作失败: {exc}") from exc

    def _exec_project_manager(self, text: str, ctx: RunContext) -> Dict[str, str]:
        """项目管理节点：query → 项目状态。"""
        comp = ctx.project_manager
        if comp is None:
            comp = (getattr(self.agent, "components", None) or {}).get("project_manager")
        if comp is None:
            raise RuntimeError("引擎未装配 project_manager 组件")
        try:
            status = getattr(comp, "status", None)
            if status is not None:
                res = status()
            else:
                res = {"project": text or ""}
            if isinstance(res, str):
                return {"status": res}
            return {"status": json.dumps(res, ensure_ascii=False, default=str)[:_MAX_OUTPUT_CHARS]}
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"项目管理查询失败: {exc}") from exc

    def _exec_preset(self, config: Dict[str, Any]) -> Dict[str, str]:
        """预设节点：输出预设名（可 beam 到模型节点的 preset 选择）。"""
        name = str(config.get("preset") or "")
        return {"preset": name}

    def _exec_ui(self, text: str) -> Dict[str, str]:
        """UI 渲染节点：内容经引擎 UI 适配器渲染/通知，输出 rendered。"""
        ui = getattr(self.agent, "ui", None) if self.agent is not None else None
        if ui is not None and text:
            notify = getattr(ui, "notify", None)
            if notify is not None:
                try:
                    notify(text)
                except Exception:  # noqa: BLE001 — 渲染失败不阻塞流程
                    pass
        return {"rendered": text}

    def _exec_frontend(self, node: Dict[str, Any],
                       payload: Optional[Dict[str, str]],
                       inputs: Dict[str, Any],
                       global_scope: bool = False) -> Dict[str, str]:
        """FE 前端节点：设置项端口（beam 值 > 面板值）。

        执行 = 把合并后的设置项写入该 FE 的独立配置（经 agent.ui 的
        fe_save_config 落盘；scope=global 时写全局配置），输出 = 各设置项
        当前值，供就近连线（模型节点的 api_key / api_base 端口）使用。
        """
        cfg: Dict[str, str] = {}
        scope = str(node.get("config") or {}).get("scope") or ""
        if global_scope:
            scope = "global"
        for port in node.get("ins") or []:
            key = port if isinstance(port, str) else (port.get("key") or "")
            if not key:
                continue
            val = str((payload or {}).get(key) or "").strip()
            if not val:
                val = str(inputs.get(key) or "").strip()
            cfg[key] = val
        ui = getattr(self.agent, "ui", None) if self.agent is not None else None
        if ui is not None:
            if scope == "global":
                saver = getattr(ui, "save_config", None)
                if saver is not None:
                    try:
                        saver({k: v for k, v in cfg.items() if v})
                    except Exception:  # noqa: BLE001
                        pass
            else:
                saver = getattr(ui, "fe_save_config", None)
                if saver is not None:
                    try:
                        saver(str(node.get("id") or node.get("label") or ""), cfg)
                    except Exception:  # noqa: BLE001
                        pass
        self._pub("flow.log", level="info",
                  message=f"FE <b>{node.get('label')}</b> 配置应用（"
                          f"{'global' if scope == 'global' else 'fe'}） · {len(cfg)} 项")
        return cfg

    def _exec_agent_runtime(self, text: str,
                            config: Dict[str, Any]) -> Dict[str, str]:
        """Agent 循环节点：把输入提交到引擎完整 Agent 循环执行。"""
        if self.agent is None or not text:
            return {"result": ""}
        run = getattr(self.agent, "run", None)
        if run is None:
            raise RuntimeError("agent_runtime 节点需要引擎 Agent 循环")
        result = run(text)
        content = str(getattr(result, "final_content", "") or "")
        return {"result": content}

    # ── model ─────────────────────────────────────────────

    def _exec_model(self, node: Dict[str, Any], text: str,
                    config: Dict[str, Any],
                    payload: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        model_name = self._resolve_model_name(config)
        provider = self.registry.resolve_model(model_name) \
            if self._model_registered(model_name) else None
        params_override: Dict[str, Any] = {}
        if provider is None:
            # 远端模型名（如 deepseek-v4-flash）：自动挂到 openai_compat
            # 适配器上执行（与「连接设置」保存远端模型的行为一致）
            provider = self._fallback_provider(model_name)
            if provider is not None:
                params_override["model_name"] = model_name
        if provider is None:
            raise ComponentError(
                f"模型 '{model_name}' 未注册，且无 openai_compat 适配器可承载")
        inputs = node.get("inputs") or {}
        context = str(inputs.get("context") or "")
        # FE 就近连线：上游前端节点的 api_key / api_base 端口直接覆盖本次调用
        for port, value in (payload or {}).items():
            if port in ("api_key", "api_base") and value:
                params_override[port] = str(value)
        # 工具集：容器通过 tools 端口挂载（JSON 列表），config.tools 为静态声明
        tool_names: List[str] = [str(x) for x in (config.get("tools") or [])]
        for port, value in (payload or {}).items():
            if port != "tools" or not value:
                continue
            try:
                parsed = json.loads(str(value))
                if isinstance(parsed, list):
                    tool_names = [str(x) for x in parsed]
            except (ValueError, TypeError):
                tool_names = [x.strip() for x in str(value).split(",")
                              if x.strip()]
        tool_schemas: List[Dict[str, Any]] = []
        for tname in tool_names:
            try:
                sch = self.registry.resolve_tool(tname).schema() or {}
                tool_schemas.append(_norm_tool_schema(sch, tname))
            except Exception:  # noqa: BLE001 — 工具缺失不阻塞模型
                self._pub("flow.log", level="warn",
                          message=f"工具 {tname} 解析失败，已跳过")
        # system prompt 取值优先级：beam 端口 > 节点输入面板 > 节点配置 >
        # 引擎预设参数（全部为空时才回落）
        system = (str((payload or {}).get("system_prompt") or "").strip()
                  or str(inputs.get("system_prompt") or "").strip()
                  or str(config.get("system_prompt") or "").strip()
                  or str((self.agent and getattr(getattr(self.agent, "preset", None),
                                                 "params", {}) or {}).get("system_prompt", "") or "").strip())
        from norpagent.protocols.model import ChatMessage

        messages = []
        if system:
            messages.append(ChatMessage(role="system", content=system))
        user_content = text if text else str(inputs.get("prompt") or "")
        if context:
            user_content = f"{user_content}\n\n[上下文]\n{context}" if user_content else context
        messages.append(ChatMessage(role="user", content=user_content or "（空输入）"))

        params: Dict[str, Any] = {}
        if self.agent is not None:
            params.update(getattr(self.agent, "params", None) or {})
        if isinstance(config.get("params"), dict):
            params.update(config["params"])
        params.update(params_override)

        self._pub("flow.log", level="info",
                  message=f"调用模型 <b>{model_name}</b> · 输入 {len(user_content or '')} 字符"
                          + (f" · 工具集 {len(tool_schemas)} 个" if tool_schemas else ""))
        stream = getattr(provider, "stream", None)
        if stream is not None and config.get("stream") is not False:
            return self._stream_model(provider, messages, tool_schemas, params,
                                      model_name)
        output = provider.generate(messages, tool_schemas or None, params)
        content = (getattr(output, "content", "") or "").strip()
        self._pub("flow.node_output", node_id=node.get("id"), port="inference",
                  output=_truncate(content), stream=False)
        return {
            "inference": content,
            "reasoning": str(getattr(output, "reasoning", "") or ""),
            "model": model_name,
        }

    def _model_registered(self, name: str) -> bool:
        try:
            self.registry.resolve_model(name)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _fallback_provider(self, model_name: str) -> Any:
        """远端模型名 → openai_compat 适配器（连接设置保存远端模型时同理）。"""
        for adapter in ("openai_compat",):
            try:
                provider = self.registry.resolve_model(adapter)
                self._pub("flow.log", level="info",
                          message=f"远端模型 <b>{model_name}</b> 挂载到 "
                                  f"<b>{adapter}</b> 适配器执行")
                return provider
            except Exception:  # noqa: BLE001
                continue
        return None

    def _stream_model(self, provider: Any, messages: List[Any],
                      tool_schemas: Optional[List[Dict[str, Any]]],
                      params: Dict[str, Any], model_name: str) -> Dict[str, str]:
        parts: List[str] = []
        reasoning: List[str] = []
        for chunk in provider.stream(messages, tool_schemas or None, params):
            if self._stop.is_set():
                break
            if getattr(chunk, "reasoning", ""):
                reasoning.append(chunk.reasoning)
                self._pub("flow.node_output", node_id=None, port="reasoning",
                          output=_truncate(chunk.reasoning), stream=True)
            if getattr(chunk, "delta_content", ""):
                parts.append(chunk.delta_content)
                self._pub("flow.node_output", port="inference",
                          output=chunk.delta_content, stream=True)
        return {
            "inference": "".join(parts).strip(),
            "reasoning": "".join(reasoning),
            "model": model_name,
        }

    def _resolve_model_name(self, config: Dict[str, Any]) -> str:
        if config.get("model"):
            return str(config["model"])
        if config.get("preset"):
            try:
                preset = self.registry.resolve_preset(str(config["preset"]))
                return getattr(preset, "model", "") or ""
            except Exception:  # noqa: BLE001
                pass
        if self.agent is not None:
            name = getattr(getattr(self.agent, "preset", None), "model", "") or ""
            if name:
                return name
        models = self.registry.list_models()
        if models:
            return models[0]
        raise RuntimeError("注册表中没有任何模型")

    # ── tool / sandbox / security / session ───────────────

    def _exec_tool(self, node: Dict[str, Any], text: str,
                   config: Dict[str, Any], ctx: RunContext,
                   payload: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        tool_name = str(config.get("tool") or "")
        if not tool_name:
            raise RuntimeError("工具节点未指定 tool（在节点配置里选择已注册工具）")
        tool = self.registry.resolve_tool(tool_name)
        # 每个输入端口 = 一个 schema 参数（不再是全局 query/result 黑箱）
        args: Dict[str, Any] = {}
        for port, value in (payload or {}).items():
            if value is not None and str(value):
                args[str(port)] = str(value)
        if not args and text:
            try:
                props = ((tool.schema() or {}).get("function", {})
                         .get("parameters", {}).get("properties", {}))
                first = next(iter(props), "query")
            except Exception:  # noqa: BLE001
                first = "query"
            args[str(first)] = text
        if isinstance(config.get("args"), dict):
            for k, v in config["args"].items():
                if v not in (None, "") and k not in args:
                    args[k] = v
        self._pub("flow.log", level="info",
                  message=f"工具调用 <b>{tool_name}</b> · 参数 {sorted(args)}")
        result = tool.run(args, ctx)
        output = getattr(result, "output", None)
        if output is None:
            output = str(result)
        return {
            "result": str(output),
            "success": str(bool(getattr(result, "success", True))),
            "tool": tool_name,
        }

    def _exec_toolbox(self, node: Dict[str, Any],
                      payload: Dict[str, str], config: Dict[str, Any],
                      ctx: RunContext) -> Dict[str, str]:
        """工具/插件容器：按端口并集批量执行全部成员。

        - 容器输入端口 = 成员钩子参数的并集；值按端口名投递给
          「拥有该参数」的每个成员（扇出语义）；
        - 容器输出端口 = 每个成员的 ``成员名.端口名`` 限定名
          （并集，杜绝命名冲突）+ 容器自身端口（如 ``tools`` 打包端口）；
        - 成员可以是 tool / hook / session / sandbox / model / security /
          scheduler / preset 等任意模块：逐成员独立容错，
          单个成员失败不中断容器与其他成员。
        """
        members: List[Dict[str, Any]] = [
            dict(m) for m in (config.get("members") or [])
            if isinstance(m, dict) and m.get("kind") and m.get("name")
        ]
        if not members:
            # 兼容旧图：只有 tools 列表时按工具成员执行
            for tname in (config.get("tools") or []):
                if tname:
                    members.append({"kind": "tool", "name": str(tname)})
        results: Dict[str, str] = {}
        for item in members:
            kind = str(item.get("kind") or "tool")
            name = str(item.get("name") or "")
            if not name:
                continue
            if kind == "tool":
                self._run_tool_member(name, payload, ctx, results)
                continue
            # 非工具成员：构造成员伪节点，走统一执行器
            pseudo: Dict[str, Any] = {
                "type": kind,
                "label": name,
                "config": {kind: name},
                "inputs": {},
            }
            if kind == "hook":
                pseudo["config"] = {
                    "plugin": str(item.get("plugin") or ""),
                    "hook": name,
                }
            elif kind == "preset":
                pseudo = {
                    "type": "model",
                    "label": name,
                    "config": {"preset": name},
                    "inputs": {},
                }
            self._pub("flow.log", level="info",
                      message=f"容器成员 <b>{kind}</b> <b>{name}</b> 执行")
            try:
                r = self._execute_node(pseudo, payload, "")
            except Exception as exc:  # noqa: BLE001 — 成员失败不中断容器
                results[f"{name}.result"] = f"{type(exc).__name__}: {exc}"
                results[f"{name}.success"] = "false"
                continue
            r = r or {}
            primary = next((v for v in r.values()
                            if v not in (None, "")), "")
            results[f"{name}.result"] = str(primary)
            results[f"{name}.success"] = "true"
        tool_names = [str(m.get("name") or "")
                      for m in members if m.get("kind") == "tool"]
        results["tools"] = json.dumps(tool_names, ensure_ascii=False)
        results["count"] = str(len(members))
        return results

    def _run_tool_member(self, tname: str, payload: Dict[str, str],
                         ctx: RunContext, results: Dict[str, str]) -> None:
        """容器内单个工具成员的扇出执行（成员名限定输出端口）。"""
        try:
            tool = self.registry.resolve_tool(tname)
        except Exception as exc:  # noqa: BLE001
            results[f"{tname}.result"] = f"(未注册: {exc})"
            results[f"{tname}.success"] = "false"
            return
        args: Dict[str, Any] = {}
        for port, value in (payload or {}).items():
            if value is not None and str(value):
                args[str(port)] = str(value)
        # 只保留该工具 schema 内声明的参数（其余端口属于别的成员）
        try:
            schema = tool.schema() or {}
            props = (schema.get("function", schema)
                     .get("parameters", {}).get("properties", {})
                     if isinstance(schema, dict) else {})
            if isinstance(props, dict) and props:
                args = {k: v for k, v in args.items() if k in props}
        except Exception:  # noqa: BLE001
            pass
        if not args:
            args = {"query": (payload or {}).get("query") or ""}
        self._pub("flow.log", level="info",
                  message=f"容器成员工具 <b>{tname}</b> 执行 · 参数 {sorted(args)}")
        try:
            result = tool.run(args, ctx)
        except Exception as exc:  # noqa: BLE001 — 成员失败不中断容器
            results[f"{tname}.result"] = f"{type(exc).__name__}: {exc}"
            results[f"{tname}.success"] = "false"
            return
        output = getattr(result, "output", None)
        results[f"{tname}.result"] = str(
            output if output is not None else result)
        results[f"{tname}.success"] = str(
            bool(getattr(result, "success", True)))

    def _exec_sandbox(self, node: Dict[str, Any], text: str,
                      config: Dict[str, Any]) -> Dict[str, str]:
        name = str(config.get("sandbox") or "")
        if not name and self.agent is not None:
            name = getattr(getattr(self.agent, "preset", None), "sandbox", "") or ""
        name = name or "subprocess"
        sb = self.registry.build_sandbox(name)
        try:
            timeout = float(config.get("timeout") or 60)
        except (TypeError, ValueError):
            timeout = 60.0
        self._pub("flow.log", level="info",
                  message=f"沙箱 <b>{name}</b> 隔离执行 · 代码 {len(text or '')} 字符")
        try:
            result = sb.run_shell(text or "echo NORP-FLOW-SANDBOX", timeout=timeout)
            stdout = str(getattr(result, "stdout", "") or "")
            stderr = str(getattr(result, "stderr", "") or "")
            output = (stdout + ("\n[stderr]\n" + stderr if stderr else "")).strip()
            return {
                "output": output,
                "exit_code": str(getattr(result, "exit_code", 0)),
                "sandbox": name,
            }
        finally:
            try:
                sb.close()
            except Exception:  # noqa: BLE001
                pass

    def _exec_path(self, node: Dict[str, Any],
                   payload: Dict[str, str],
                   inputs: Dict[str, Any]) -> Dict[str, str]:
        """路径模块：产出经过公共路径安全校验的相对路径值。

        取值优先级：节点配置输入的 path 值 > 上游 beam 投递的
        path / base 端口值 > 工作区根目录 "."。校验复用文件类工具的
        公共防护层（pathsafe）：拒绝绝对路径与 .. 穿越，保证产出的
        值 beam 到任何工具的 path 端口都不会触发越界。
        """
        value = str(inputs.get("value") or "").strip()
        if not value:
            for port in ("path", "base"):
                up = str((payload or {}).get(port) or "").strip()
                if up:
                    value = up
                    break
        value = value or "."
        try:
            from norpagent.builtin.tools.pathsafe import resolve_safe_path

            ctx = self._make_ctx(node)
            resolved = resolve_safe_path(ctx, value)
            display = str(resolved)
        except Exception as exc:  # noqa: BLE001 — 校验失败即拒绝节点
            raise RuntimeError(f"路径非法: {exc}") from exc
        self._pub("flow.log", level="info",
                  message=f"路径模块 <b>{value}</b> → <b>{display}</b>（已通过安全校验）")
        return {"path": value, "resolved": display}

    def _exec_security(self, text: str) -> Dict[str, str]:
        from norpagent.security.guard import scan_message

        blocked, reason, _matches = scan_message(text or "")
        self._pub("flow.log", level="warn" if blocked else "info",
                  message="安全扫描：%s" % ("拦截" if blocked else "通过"))
        return {
            "audited": text or "",
            "blocked": "true" if blocked else "false",
            "reason": reason or "",
        }

    def _exec_session(self, text: str,
                      config: Dict[str, Any]) -> Dict[str, str]:
        sm = None
        if self.agent is not None:
            sm = getattr(self.agent, "session_manager", None)
        if sm is None:
            name = str(config.get("session") or "memory")
            sm = self.registry.build_session(name)
        sid = str(config.get("session_id") or "")
        sess = None
        if sid:
            try:
                sess = sm.get_session(sid)
            except Exception:  # noqa: BLE001
                sess = None
        if sess is None:
            sess = sm.create_session(title=f"flow-{self.flow_id[:8]}")
        self._flow_session_id = str(sess.id)
        if text:
            from norpagent.protocols.model import ChatMessage

            try:
                sm.append_message(sess.id, ChatMessage(role="user", content=text))
            except Exception:  # noqa: BLE001
                pass
        history = []
        try:
            history = list(sm.history(sess.id))
        except Exception:  # noqa: BLE001
            pass
        self._pub("flow.log", level="info",
                  message=f"会话 <b>{sess.id[:8]}</b> · 历史 {len(history)} 条")
        return {"session_id": str(sess.id), "messages": str(len(history)),
                "state": f"session {sess.id[:8]} ({len(history)} messages)"}

    # ── plugin / hook ─────────────────────────────────────

    def _exec_plugin(self, node: Dict[str, Any], text: str,
                     config: Dict[str, Any], ctx: RunContext) -> Dict[str, str]:
        tool_name = str(config.get("tool") or "")
        if not tool_name:
            tools = self._plugin_tools(str(config.get("plugin") or ""))
            tool_name = tools[0] if tools else ""
        if tool_name:
            tool = self.registry.resolve_tool(tool_name)
            args: Dict[str, Any] = {"query": text}
            if isinstance(config.get("args"), dict):
                args.update(config["args"])
            self._pub("flow.log", level="info",
                      message=f"插件工具 <b>{tool_name}</b> 执行")
            result = tool.run(args, ctx)
            output = getattr(result, "output", None)
            return {"result": str(output if output is not None else result),
                    "tool": tool_name}
        # 无工具插件：在总线上发布一个自定义事件，插件钩子可响应
        self.registry.bus.emit("on_event", event_type=f"flow.plugin.{config.get('plugin')}",
                               data=text, context=ctx)
        return {"result": text, "tool": ""}

    def _plugin_tools(self, plugin_name: str) -> List[str]:
        plugins = getattr(self.registry, "_plugins", {}) or {}
        plugin = plugins.get(plugin_name)
        if plugin is None:
            return []
        return [getattr(t, "name", "") for t in plugin.get_tools()
                if getattr(t, "name", "")]

    def _exec_hook(self, text: str, config: Dict[str, Any],
                   ctx: RunContext, inputs: Dict[str, Any]) -> Dict[str, str]:
        hook_name = str(config.get("hook") or "")
        if not hook_name:
            raise RuntimeError("钩子节点未指定 hook")
        arg_keys = HOOK_ARG_KEYS.get(hook_name, ())
        payload: Dict[str, Any] = {"data": text, "context": ctx}
        for key in arg_keys:
            payload[key] = inputs.get(key) if inputs.get(key) is not None else text
        self._pub("flow.log", level="info",
                  message=f"钩子 <b>{hook_name}</b> 触发（插件 {config.get('plugin')}）")
        if hook_name in _MUTATING_HOOKS:
            try:
                result = self.registry.bus.intercept(hook_name, **payload)
            except Exception as exc:  # noqa: BLE001 — 钩子否决/异常不中断
                self._pub("flow.log", level="warn",
                          message=f"钩子 {hook_name} 被否决/异常: {exc}")
                result = None
            return {"result": str(result) if result is not None else text}
        self.registry.bus.emit(hook_name, **payload)
        return {"result": text, "hook": hook_name}


__all__ = [
    "build_snapshot",
    "ModuleWorkspace",
    "FlowRunner",
    "normalize_graph",
    "default_modules_dir",
    "HOOK_ARG_KEYS",
]

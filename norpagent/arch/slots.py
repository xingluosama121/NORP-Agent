# Copyright (c) 2026 xingluosama121, MIT Licensed
"""架构槽位定义表（Architecture Slot Specifications）。

一个 Agent 应用的每一个组成部分都是一个「槽位」：

- 每个槽位有名字、协议、默认实现、工厂上下文约定与文档；
- 槽位不填地址 → 走库内置默认逻辑；
- 槽位填了地址（字符串 / 工厂 / 实例）→ 按地址把实现接上去；
- 除「底层运行必需」的最小内核外，**全部**组件都是槽位，
  包括事件循环（async_loop）、Agent 循环本身（agent_runtime）、
  前端（frontend）、渲染器（ui）等。

底层运行必需（不可替换的最小内核，仅四样）：
    1. ArchLayer 本身（槽位连接器，norpagent.arch.layer）
    2. 地址解析器（norpagent.arch.address）
    3. 注册表（norpagent.kernel.registry.Registry）
    4. 事件总线（norpagent.kernel.events.EventBus）

其余全部是槽位。给每个槽位填上地址，就可以把整个 Agent 应用
换成完全不同的实现拼装体，而不需要修改任何核心代码。

**槽位表本身也是热插拔的（v0.9）**：除 18 个内置槽位（框架结构
契约，受保护不可注销 / 覆盖规格）外，第三方可运行时注册全新
自定义槽位（``register_slot``）——注册即接入完整管线：``np()``
参数校验、ArchLayer 装配、``np.remount()`` 热替换、
``layer.describe()`` 清单。自定义槽位通过 ``SlotSpec.applier``
声明装配逻辑（挂到注册表 / 预设 / 引擎），详见模块底部 API
与开发手册 3.8 节。
"""

from __future__ import annotations

import keyword
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass(frozen=True)
class SlotSpec:
    """一个架构槽位的规格说明。

    字段：
        name: 槽位名（np() 的关键字参数名）。
        protocol: 该槽位实现必须满足的接口约定（文档文本）。
        default_address: 默认实现的地址（None 表示默认逻辑内联在装配器里）。
        string_semantics: 字符串值的语义：
            "address"          -> 字符串是模块地址（"pkg.mod[:attr]"）；
            "name"             -> 字符串是注册表中的组件名（原样透传）；
            "name_or_address"  -> 先按组件名、再按模块地址解析；
            "literal"          -> 字符串是字面值（级别 / 路径等）。
        factory_kwargs: 工厂上下文注入的键说明（{键: 说明}），
            工厂按需声明同名参数即可接收对应上下文。
        description: 槽位职责描述。
        examples: 用法示例（列表）。
        defer_factory: True 表示该槽位的工厂**推迟到引擎装配期**调用
            （registry / preset 上下文就绪后，由 NorpEngine._build_agent
            按签名裁剪注入）；架构层 connect 时只解析地址、不实例化。
            用于 agent_runtime 这类需要完整上下文的槽位。
        applier: 自定义槽位的装配逻辑（内置槽位为 None，框架已内置
            处理）。签名 ``applier(reg, layer, value, params, ctx)``：
            槽位值非空时，由 runtime.mount.apply_slot_overrides 在
            装配期与热挂载期调用。**同一注册表可能重复调用**
            （np.remount 热挂载），applier 必须重入安全（重复执行
            不叠加副作用；可利用 ctx["meta"] 记录待退订对象）。
            ``value`` 是解析后的槽位值：address 语义为已实例化的
            实现（可经 layer.subconfig(slot) 取 ``;key=value`` 子
            配置），name / name_or_address / literal 语义为原值。
            ``ctx`` 为四个可变容器：
            - components: 最终预设的组件声明 {kind: name}——
              applier 可 register_component 后在此登记；
            - extras: 引擎附加对象字典（自定义对象交由此处，
              引擎消费 extras[槽位名]）；
            - overrides: 预设字段覆盖（model/tools/... 的最终值），
              与内置槽位共用同一字典；
            - meta: 注册表架构元数据（runtime.mount.arch_meta，
              记录挂上去的可退订对象）。
        remount_rebuild_agent: True 表示热挂载该槽位后需要**热重建
            AgentRuntime**（自定义「装配型」槽位——applier 向预设
            components 登记通用组件的槽位应置 True）；False（默认）
            表示下一次 run() 生效或仅更新 extras，无需重建运行时。
    """

    name: str
    description: str
    protocol: str
    default_address: Optional[str] = None
    string_semantics: str = "address"
    factory_kwargs: Dict[str, str] = field(default_factory=dict)
    examples: List[str] = field(default_factory=list)
    defer_factory: bool = False
    applier: Optional[Callable[..., None]] = None
    remount_rebuild_agent: bool = False

    def format_help(self) -> str:
        lines = [
            f"[{self.name}] {self.description}",
            f"  协议: {self.protocol}",
            f"  默认: {self.default_address or '装配器内置逻辑'}",
            f"  字符串语义: {self.string_semantics}",
        ]
        for key, doc in self.factory_kwargs.items():
            lines.append(f"  工厂参数 {key}: {doc}")
        for ex in self.examples:
            lines.append(f"  示例: {ex}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
#  槽位全集
# ═══════════════════════════════════════════════════════════

# 槽位表线程安全：装配 / 热挂载都可能与注册发生在不同线程。
_LOCK = threading.RLock()

# 字符串语义全集（register_slot 校验用）
_STRING_SEMANTICS = ("address", "name", "name_or_address", "literal")

# launch() 的特殊键（槽位拆分前被弹出），不可作为槽位名
_RESERVED_SLOT_NAMES = frozenset({"prompt", "config"})


class SlotError(ValueError):
    """槽位表热插拔非法操作（注册 / 注销 / 规格非法）。"""


SLOT_SPECS: Dict[str, SlotSpec] = {}


def _slot(spec: SlotSpec) -> SlotSpec:
    with _LOCK:
        SLOT_SPECS[spec.name] = spec
    return spec


# ── 执行引擎层 ────────────────────────────────────────────

_slot(SlotSpec(
    name="async_loop",
    description="事件循环系统：Agent 运行所在的异步调度核心。"
                "等价于架构函数 norpagent.nasyncio()。"
                "默认实现运行库内置的自研 nasyncio 事件循环核心"
                "（norpagent.nasyncio，不依赖标准 asyncio）。",
    protocol=(
        "LoopRuntime 协议（norpagent.loops.base.LoopRuntime）："
        "start() 启动循环；stop() 请求停止；is_running()；join() 收尾；"
        "submit(fn, *a, **kw) 在循环上下文中执行同步函数并返回结果。"
    ),
    default_address="norpagent.loops.nasyncio:NasyncioLoopRuntime",
    factory_kwargs={"layer": "所在架构层", "config": "该槽位的附加配置 dict"},
    examples=[
        "np(async_loop='norpagent.loops.nasyncio:NasyncioLoopRuntime')  # 显式默认（自研 nasyncio 核心）",
        "np(async_loop='norpagent.loops.std_asyncio:StdLoopRuntime')   # 0.7 旧地址（兼容垫片，同指默认）",
        "np(async_loop='myapp.nasync_loop:create')                     # 自研循环",
    ],
))

_slot(SlotSpec(
    name="agent_runtime",
    description="Agent 循环本体：输入→会话→消息→模型→工具→结果的执行循环。",
    protocol=(
        "具备 run(user_input, session_id=None, task_id=None, task_params=None)"
        " 方法并返回带 .final_content / .status 的结果对象；"
        "具备 shutdown() 释放资源。"
    ),
    default_address=None,  # 默认逻辑 = kernel.agent.AgentRuntime
    defer_factory=True,    # 工厂推迟到引擎装配期调用（需 registry/preset 上下文）
    factory_kwargs={
        "registry": "已装配的注册表",
        "preset": "解析后的预设对象",
        "layer": "所在架构层",
        "config": "该槽位的附加配置 dict",
    },
    examples=[
        "np(agent_runtime='myapp.agent:MyAgentRuntime')  # 替换 Agent 循环本体",
    ],
))

# ── 能力组件层 ────────────────────────────────────────────

_slot(SlotSpec(
    name="model",
    description="模型提供者（Agent 的「大脑」）。",
    protocol="ModelProvider 协议（norpagent.protocols.model.ModelProvider）："
             "generate(messages, tool_schemas, params) / 可选 stream(...)。",
    default_address=None,  # 默认逻辑 = 使用预设声明的模型
    string_semantics="name_or_address",
    factory_kwargs={"layer": "所在架构层", "config": "附加配置 dict"},
    examples=[
        "np(model=MockModelProvider())                    # 实例直接接入",
        "np(model='myapp.model:create')                   # 工厂地址接入",
        "np(model='openai_compat')                        # 用已注册模型名",
    ],
))

_slot(SlotSpec(
    name="tools",
    description="工具集（Agent 的「手」）。",
    protocol="Tool 协议（norpagent.protocols.tool.Tool）：name / schema() / run(args, ctx)。"
             "槽位值允许：名字列表（引用注册表）、{名字: Tool} 映射、Tool 实例列表。",
    default_address=None,  # 默认逻辑 = 使用预设声明的工具集
    string_semantics="name",
    examples=[
        "np(tools=['echo', 'get_time'])                   # 只装两个工具",
        "np(tools={'my_tool': MyTool()})                  # 自研工具注册并启用",
    ],
))

_slot(SlotSpec(
    name="session",
    description="会话管理（Agent 的「记忆」）。",
    protocol="SessionManager 协议（norpagent.protocols.session.SessionManager）："
             "create_session / get_session / append_message / history。",
    default_address=None,  # 默认逻辑 = 使用预设声明的会话组件
    string_semantics="name_or_address",
    examples=[
        "np(session='sqlite')                             # 切到 SQLite 持久化",
        "np(session=MySessionManager())                   # 自研会话实例",
    ],
))

_slot(SlotSpec(
    name="sandbox",
    description="沙箱环境（工具执行的隔离边界）。",
    protocol="Sandbox 协议（norpagent.protocols.sandbox.Sandbox）：run / close。",
    default_address=None,  # 默认逻辑 = 使用预设声明的沙箱
    string_semantics="name_or_address",
    examples=[
        "np(sandbox='pooled')                             # 池化沙箱",
        "np(sandbox='myapp.docker_sandbox:create')        # 容器沙箱",
    ],
))

_slot(SlotSpec(
    name="scheduler",
    description="任务调度器（长周期任务协作 / 多智能体编排的底座）。",
    protocol="TaskScheduler 协议（norpagent.protocols.scheduler.TaskScheduler）："
             "submit / drain / cancel。",
    default_address=None,  # 默认逻辑 = 使用预设声明的调度器
    string_semantics="name_or_address",
    examples=[
        "np(scheduler='persistent')                       # 崩溃可续跑的调度器",
    ],
))

_slot(SlotSpec(
    name="context_store",
    description="上下文库（跨会话长期记忆，FTS5 全文检索）。",
    protocol="具备 add / search / list / delete 接口；close() 释放资源。",
    default_address=None,  # 默认逻辑 = 使用预设声明的上下文组件
    examples=[
        "np(context_store='norpagent.builtin.context:FTS5ContextStore')",
    ],
))

_slot(SlotSpec(
    name="project_manager",
    description="项目管理（项目元数据、目录扫描、git 感知）。",
    protocol="具备 status / scan 等只读接口；close() 释放资源。",
    default_address=None,  # 默认逻辑 = 使用预设声明的项目管理组件
    examples=[
        "np(project_manager='myapp.pm:create')",
    ],
))

# ── 横切关注层 ────────────────────────────────────────────

_slot(SlotSpec(
    name="hooks",
    description="钩子体系扩展：9 层 29 钩子之外的自定义订阅。",
    protocol="{钩子名: 回调} 映射，或 callable(registry) 返回订阅配置。",
    default_address=None,  # 默认逻辑 = 标准 9 层钩子，无额外订阅
    string_semantics="literal",
    examples=[
        "np(hooks={'before_model_call': my_guard})",
    ],
))

_slot(SlotSpec(
    name="security",
    description="安全系统（norpagent.safe() 的策略来源，默认钩子零干预）。",
    protocol="字符串级别（basic/standard/high）、配置 dict（level/hooks/config）、"
             "SecurityContext 实例、或 callable(registry) 完成安全装配。"
             "字符串与 dict 默认不挂钩子，hooks=True 才挂载钩子干预。",
    default_address=None,  # 默认逻辑 = 不开启额外安全
    string_semantics="literal",
    examples=[
        "np(security='high')",
        "np(security={'level': 'high', 'hooks': True})",
        "np(security=lambda reg: norpagent.safe(reg, level='standard', hooks=True))",
    ],
))

_slot(SlotSpec(
    name="plugins",
    description="外部插件（签名→审计→导入限制→注册的完整管线）。",
    protocol="目录路径列表，或 callable(registry) 返回插件加载结果。",
    default_address=None,  # 默认逻辑 = 不加载外部插件
    string_semantics="literal",
    examples=[
        "np(plugins=['./my_plugins'])",
    ],
))

# ── 表现层 ────────────────────────────────────────────────

_slot(SlotSpec(
    name="frontend",
    description="前端：面向用户的输入/输出外壳。前端必须高度可变——"
                "console / headless / web / 任意自定义前端都是同一槽位。",
    protocol=(
        "Frontend 协议（norpagent.frontends.base.Frontend）："
        "attach(engine) 绑定引擎；start() 启动（后台线程）；stop() 停止；"
        "is_alive() 存活查询。"
    ),
    default_address="norpagent.frontends.web:WebFrontend",
    factory_kwargs={"layer": "所在架构层", "config": "附加配置 dict"},
    examples=[
        "np()                                                       # 默认 Web 前端",
        "np(frontend='norpagent.frontends.headless:HeadlessFrontend')  # 纯 API",
        "np(frontend='norpagent.frontends.console:ConsoleFrontend')    # 命令行",
        "np(frontend='myapp.my_ui:create')                             # 完全自定义",
        "np(frontend='norpagent.frontends.web:WebFrontend;html=/path/to/my.html')"
        "  # Web 前端 + 自定义主页面（槽位挂载参数，替换 / 路由页面）",
    ],
))

_slot(SlotSpec(
    name="ui",
    description="事件渲染适配器：前端之下的渲染层，把事件总线的事件"
                "渲染成文本 / 界面元素（ConsoleUI / WebUI / 自定义）。",
    protocol="UIAdapter 协议（norpagent.protocols.ui.UIAdapter）："
             "on_event / ask_user / notify。",
    default_address=None,  # 默认逻辑 = 使用预设声明的 UI 适配器
    string_semantics="name",
    examples=[
        "np(ui=MyRenderer())                              # 自定义渲染器实例",
        "np(ui='web')                                     # 已注册渲染器名",
    ],
))

_slot(SlotSpec(
    name="preset",
    description="预设模式：一整套组件的开箱即用组合声明。",
    protocol="预设名（str，需已注册）或 Preset 实例。",
    default_address=None,  # 默认逻辑 = 'standard' 预设
    string_semantics="name",
    examples=[
        "np(preset='standard')                            # 标准模式",
        "np(preset='ptc')                                 # PTC 模式",
        "np(preset=Preset(name='mine', ...))               # 自定义预设",
    ],
))

# ── 基础服务层 ────────────────────────────────────────────

_slot(SlotSpec(
    name="logger",
    description="日志记录器。",
    protocol="logging.Logger 协议：debug / info / warning / error。",
    default_address=None,  # 默认逻辑 = logging.getLogger('norpagent')
    string_semantics="literal",
    examples=[
        "np(logger=logging.getLogger('my.app'))",
    ],
))

_slot(SlotSpec(
    name="storage",
    description="持久化存储（会话数据库、任务状态等落盘位置）。",
    protocol="str（目录路径）或具备 .root / .path 属性的存储对象。",
    default_address=None,  # 默认逻辑 = ~/.norpagent
    string_semantics="literal",
    examples=[
        "np(storage='./my_data')",
    ],
))

_slot(SlotSpec(
    name="error_handler",
    description="错误处理：任务级异常的最后防线。",
    protocol="callable(error, engine) -> None。",
    default_address=None,  # 默认逻辑 = 记录日志并置引擎状态
    string_semantics="literal",
    examples=[
        "np(error_handler=lambda exc, eng: print(exc))",
    ],
))


# 内置槽位名快照：这些名字是框架结构契约（引擎 / 前端 / 文档引用），
# 受保护——不可注销、不可覆盖规格；它们的「值」仍然随时热替换
# （np.remount）。自定义槽位名不受此限制。
_BUILTIN_SLOT_NAMES: frozenset = frozenset(SLOT_SPECS)


def get_slot(name: str) -> SlotSpec:
    """按名取槽位规格，未知槽位抛 KeyError。"""
    with _LOCK:
        return SLOT_SPECS[name]


def all_slot_names() -> List[str]:
    """全部槽位名（按定义顺序，含运行时注册的自定义槽位）。"""
    with _LOCK:
        return list(SLOT_SPECS.keys())


def snapshot_slots() -> Dict[str, SlotSpec]:
    """槽位表快照：注册 / 注销期间的稳定视图。

    装配循环（ArchLayer.connect / describe / launch 参数拆分 /
    热挂载校验）使用快照迭代，避免与 register_slot /
    unregister_slot 并发时 dict 迭代报错。
    """
    with _LOCK:
        return dict(SLOT_SPECS)


def is_builtin_slot(name: str) -> bool:
    """该槽位名是否为受保护的内置槽位（18 个框架结构契约）。"""
    return name in _BUILTIN_SLOT_NAMES


def register_slot(spec: SlotSpec | Dict[str, Any], *,
                  replace: bool = False) -> SlotSpec:
    """注册一个自定义槽位——槽位表热插拔（v0.9）。

    - ``spec``：SlotSpec 实例或字段字典（dict 自动构造）；
    - 注册后立即生效：``np(newslot=...)`` 参数校验、ArchLayer 装配、
      ``np.remount(newslot=...)`` 热替换、``layer.describe()`` 清单
      全部认识新槽位，无需重启进程；
    - ``replace=True``：热替换同名**自定义**槽位的规格（默认地址 /
      字符串语义 / applier / 重建标志）。已装配的实现保持原状，
      下一次 ``np.remount(slot=...)`` 按新规格重新解析。

    保护规则：
    - 18 个内置槽位名不可注册 / 覆盖 / 注销（框架结构契约，
      其值可随时用 ``np.remount`` 热替换）；
    - 槽位名必须是合法 Python 标识符（``np()`` 的关键字参数），
      且不能是 Python 关键字、不能是 ``prompt`` / ``config``
      （launch 的特殊键）；
    - ``applier`` 为 None 或 callable；``string_semantics`` 必须是
      address / name / name_or_address / literal 之一。

    装配语义：
    - ``applier(reg, layer, value, params, ctx)`` 在装配期与
      热挂载期由 runtime.mount.apply_slot_overrides 调用（槽位值
      非空时）。同一注册表可能重复调用（热挂载），applier 须
      重入安全；ctx 提供 components / extras / overrides / meta
      四个可变容器（详见 SlotSpec.applier 字段文档）；
    - ``remount_rebuild_agent=True``：热挂载后热重建 AgentRuntime
      （applier 向预设 components 登记通用组件的槽位应置 True）；
    - 字符串语义与内置槽位相同：address（模块地址）/ name /
      name_or_address / literal（原样透传给 applier）。
    """
    if isinstance(spec, dict):
        spec = SlotSpec(**spec)
    _validate_slot_spec(spec)
    with _LOCK:
        if spec.name in _BUILTIN_SLOT_NAMES:
            raise SlotError(
                f"槽位 '{spec.name}' 是内置槽位（框架结构契约），"
                "不可注册 / 覆盖规格；内置槽位的值可随时用 "
                "np.remount 热替换"
            )
        exists = spec.name in SLOT_SPECS
        if exists and not replace:
            raise SlotError(
                f"槽位 '{spec.name}' 已注册；replace=True 可热替换其规格"
            )
        SLOT_SPECS[spec.name] = spec
        return spec


def unregister_slot(name: str) -> SlotSpec:
    """注销一个自定义槽位，返回被移除的规格。

    - 内置槽位名受保护，注销抛 SlotError；
    - 已装配到架构层的实现保持原状（不再出现在 describe /
      槽位校验中）；重新注册同名槽位后，np.remount 可重新解析。
    """
    with _LOCK:
        if name in _BUILTIN_SLOT_NAMES:
            raise SlotError(
                f"槽位 '{name}' 是内置槽位（框架结构契约），不可注销"
            )
        spec = SLOT_SPECS.pop(name, None)
        if spec is None:
            raise SlotError(f"槽位 '{name}' 不存在（不可注销）")
        return spec


def _validate_slot_spec(spec: SlotSpec) -> None:
    if not isinstance(spec, SlotSpec):
        raise SlotError(f"spec 必须是 SlotSpec 实例，收到 {type(spec)}")
    name = spec.name
    if (not isinstance(name, str) or not name.isidentifier()
            or keyword.iskeyword(name)):
        raise SlotError(
            f"槽位名 {name!r} 非法：必须是合法 Python 标识符"
            "（np() 关键字参数名），且不能是 Python 关键字"
        )
    if name in _RESERVED_SLOT_NAMES:
        raise SlotError(
            f"槽位名 {name!r} 是 launch 特殊键（prompt / config），不可注册"
        )
    if spec.string_semantics not in _STRING_SEMANTICS:
        raise SlotError(
            f"槽位 '{name}' 的字符串语义 {spec.string_semantics!r} 非法；"
            f"必须是 {list(_STRING_SEMANTICS)} 之一"
        )
    if spec.applier is not None and not callable(spec.applier):
        raise SlotError(f"槽位 '{name}' 的 applier 必须是 callable 或 None")


__all__ = [
    "SlotSpec",
    "SlotError",
    "SLOT_SPECS",
    "get_slot",
    "all_slot_names",
    "snapshot_slots",
    "is_builtin_slot",
    "register_slot",
    "unregister_slot",
]

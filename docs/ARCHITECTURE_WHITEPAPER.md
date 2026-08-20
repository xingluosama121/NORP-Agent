# NORP Agent 技术架构白皮书

> **可插拔积木式 Agent 框架的技术架构与技术分享**
>
> 版本：0.9.1 ｜ 日期：2026-08 ｜ 许可：Copyright (c) 2026 xingluosama121, MIT Licensed
>
> 2026-08 修订：3.5 remount 在途任务竞态与两阶段排水 ｜ 4.2 EventBus 基准口径 ｜ 5.5 守护工作池边界 ｜ 13 章口径与盲区 ｜ ADR-11
>
> 配套文档：`DEVELOPER_MANUAL.md`（开发手册，操作向）｜ 本文为架构思想与设计决策分享（原理向）

---

## 摘要

NORP Agent 是一个「可插拔积木式」的通用 Agent 框架。它的核心命题只有一个：

> **换零件只填「地址」，核心代码零修改；运行中也能换零件（热挂载），无需重启进程。**

围绕这个命题，框架建立了四条设计主线：

1. **地址函数（Address Function）**：除底层最小内核外，一切组件都是槽位，槽位值就是「地址」——不填走默认，填了接入；
2. **最小内核（Minimal Kernel）**：全框架只有四样东西不可替换——槽位连接器、地址解析器、注册表、事件总线；
3. **暴露面原则（Everything is an API）**：每一个执行结构（输入、会话、消息组装、模型调用、工具调用、结果定型）都是独立 API，可订阅、可改写、可否决；
4. **自控底层（Self-Controlled Foundation）**：不依赖标准 asyncio，自研异步调度核心；Web UI 零第三方依赖（HTTP + SSE 标准库实现）；核心包零第三方依赖。

代码规模：96 个 Python 文件、约 2.1 万行；核心包纯 Python 标准库即可运行。

```
┌──────────────────────────────────────────────────────────────┐
│  NORP Agent 架构一句话                                        │
│  最小内核（4 样） + 槽位表（18 内置 + N 自定义） + 地址函数      │
│  装配 = 填地址 → ArchLayer 连接 → Registry 注册 → 引擎运行     │
└──────────────────────────────────────────────────────────────┘
```

---

## 目录

1. [设计哲学：五个核心思想](#1-设计哲学五个核心思想)
2. [分层架构全景](#2-分层架构全景)
3. [槽位系统：可插拔的积木盘](#3-槽位系统可插拔的积木盘)
4. [内核设计：注册表 / 事件总线 / Agent 循环](#4-内核设计注册表--事件总线--agent-循环)
5. [自研异步核心 nasyncio](#5-自研异步核心-nasyncio)
6. [钩子系统：9 层 29 钩子](#6-钩子系统9-层-29-钩子)
7. [前端体系与 Web UI](#7-前端体系与-web-ui)
8. [安全架构](#8-安全架构)
9. [插件系统](#9-插件系统)
10. [可靠性设计：工作回退](#10-可靠性设计工作回退)
11. [FLOW 模块流程编排](#11-flow-模块流程编排)
12. [嵌入式与超高并发](#12-嵌入式与超高并发)
13. [性能设计与实测](#13-性能设计与实测)
14. [关键架构决策记录（ADR）](#14-关键架构决策记录adr)
15. [总结与演进方向](#15-总结与演进方向)

---

## 1. 设计哲学：五个核心思想

### 1.1 模块即入口（Module as Entry）

`import norpagent as np` 之后，`np()` 本身就是启动函数。实现手法：把模块对象的 `__class__` 替换为 `_NorpAgentModule`（继承 `types.ModuleType`），为模块挂上 `__call__` 与便捷方法。模块属性查找优先于类方法，因此 `np.stop()`、`np.nasyncio()` 等既有模块级属性天然遮蔽同名方法——**用最小的元编程代价，让模块同时是「库」与「入口」**，无需额外引入 CLI 包装或工厂函数。

```python
sys.modules[__name__].__class__ = _NorpAgentModule   # 一行完成模块可调用化
```

### 1.2 地址函数（Address Function）

这是整个框架最核心的抽象。一个「地址」的语义是：

| 地址形态 | 含义 |
|---|---|
| `None` | 使用该槽位的默认实现（库内置逻辑） |
| `"pkg.mod"` | 导入模块，优先取约定的工厂属性 `create` / `build` / `default`，都没有则整个模块即实现 |
| `"pkg.mod:attr"` | 导入模块并取具名属性作为实现 |
| `"pkg.mod:create;timeout=5"` | 地址 + 附加配置子句（`;key=value`），子句注入工厂的 `config` 参数 |
| `callable` | 直接作为工厂，按签名裁剪注入上下文 |
| 其它对象 | 直接作为实现（实例 / 值） |

「不填 = 默认，填了 = 接入」让框架拥有乐高式的互换性：**替换组件不是改代码，而是改数据**。这使装配过程可序列化、可快照、可回放（工作回退系统正是依赖这一性质）。

### 1.3 最小内核（Minimal Kernel）

框架刻意保持「内核小到可以审计」：

| # | 内核模块 | 职责 | 为什么不可替换 |
|---|---|---|---|
| 1 | `arch.layer.ArchLayer` | 槽位连接器 | 负责「装配」这个动作本身 |
| 2 | `arch.address` | 地址解析器 | 提供「地址」语义 |
| 3 | `kernel.registry.Registry` | 注册表 | 名字 → 组件的映射中心 |
| 4 | `kernel.events.EventBus` | 事件总线 | 组件间的事件传递通道 |

**其余一切都是槽位**——包括事件循环（`async_loop`）、Agent 循环本体（`agent_runtime`）、前端（`frontend`）、渲染器（`ui`）。内核小意味着：安全审计面小、测试面小、心智负担小。

### 1.4 一切皆注册项（Everything is Registered）

模型、工具、会话、沙箱、调度器、UI、插件、预设，全部按名字注册、按名字解析。`AgentRuntime` 只与注册表交互，因此替换任意部件都不需要改动核心代码。注册表还内置一个「通用组件命名空间」（`kind -> {name: factory}`），上下文存储、项目管理、任务存储等一切附加能力都走这里——**框架无需改内核就能扩展新的组件种类**。

### 1.5 每个执行结构都是 API（Exposed Structures）

AgentRuntime 的每一次输入、会话创建、消息落库、消息组装、步骤、模型调用、工具调用、结果定型，都经过一个具名钩子；可变钩子可改写数据流，`HookVeto` 可一票否决。同时这些方法（`prepare_input` / `create_session` / `call_model` / `execute_tool_call` / `finalize_result` …）全部是公共方法，子类可直接覆写——**钩子干预与继承覆写双通道**，覆盖不同层级的扩展需求。

---

## 2. 分层架构全景

```
┌─────────────────────────────────────────────────────────────┐
│  你的应用                                                    │
│  np() / np.stop() / np.nasyncio() / np.current().submit()   │
└───────────────────────────┬─────────────────────────────────┘
                            │ 模块入口（norpagent/__init__.py 可调用）
┌───────────────────────────▼─────────────────────────────────┐
│  运行时层 runtime/                                           │
│  launch → ArchLayer.connect → mount.build_registry          │
│  → NorpEngine（生命周期状态机 + 后台循环线程 + 前端线程）      │
└──────┬──────────────────┬───────────────────┬───────────────┘
       │                  │                   │
┌──────▼──────┐   ┌───────▼────────┐  ┌───────▼───────────────┐
│ 架构层 arch/ │   │ 循环系统 loops/ │  │ 前端 frontends/       │
│ 槽位定义      │   │ LoopRuntime    │  │ Frontend 协议        │
│ 地址解析      │   │ 协议 + 默认实现 │  │ console/headless/web │
│ ArchLayer    │   │ = nasyncio()   │  └───────────────────────┘
└──────┬──────┘   └───────┬────────┘
       │ 按槽位装配          │ 按协议驱动
┌──────▼────────────────────▼─────────────────────────────────┐
│  内核 kernel/                                                │
│  Registry（注册表）── EventBus（事件总线）── AgentRuntime     │
│  （Agent 循环本体，本身也是可替换槽位 agent_runtime）          │
└──┬──────────┬──────────┬──────────┬──────────┬──────────────┘
   │          │          │          │          │
┌──▼───┐ ┌────▼────┐ ┌───▼────┐ ┌───▼─────┐ ┌──▼────────────┐
│模型   │ │工具     │ │会话     │ │沙箱      │ │调度器/上下文/  │
│model  │ │tools    │ │session  │ │sandbox   │ │项目/安全/插件 │
└───────┘ └─────────┘ └─────────┘ └──────────┘ └───────────────┘
   以上全部通过注册表按名字解析，全部是架构槽位（可替换）
```

**依赖方向单向下行**：上层可以 import 下层，下层不得反向依赖上层。安全系统通过 `registry.security` 注入，`kernel.agent` 不再直接 import `norpagent.security`——安全已整体剥离为可选能力。

### 2.1 一次任务的数据流（数据如何穿过系统）

```
前端线程 input() 拿到文本
  → engine.submit(text)                  （NorpEngine，线程编排）
  → loop.submit(fn)                      （事件循环，可替换）
  → AgentRuntime.run(text)               （内核循环，可替换）
      → L3 prepare_input                （before_input / after_input 钩子）
      → L4 create_session / append_message（会话与历史）
      → L5 build_messages               （系统提示词 + 历史合并）
      → L6 before_step                  （步骤边界）
      → L7 call_model                   （模型 = model 槽位解析出的提供者）
      → L8 execute_tool_call            （工具 = tools 槽位，沙箱 = sandbox 槽位）
      → （多轮直到模型给出最终答案）
      → L9 finalize_result              （结果定型）
  ← RunResult（final_content / status / usage ...）
事件总线全程广播（on_task_start / on_content / after_tool_call ...）
  → ui 渲染器（UIAdapter）订阅事件，把流式输出渲染给用户
```

### 2.2 模块地图（96 个文件的分工）

| 目录 | 职责 | 关键文件 |
|---|---|---|
| `arch/` | 槽位规格、地址解析、ArchLayer 连接器 | `slots.py`（18 槽位表 + 热插拔）、`address.py`、`layer.py` |
| `kernel/` | 最小内核：注册表 / 事件总线 / Agent 循环 / 预设 | `registry.py`、`events.py`、`agent.py`、`presets.py` |
| `nasyncio.py` | 自研异步 IO 核心（1067 行，零 asyncio 依赖） | EventLoop / Future / Task / 同步原语 / 子进程 |
| `loops/` | 事件循环架构函数与运行时适配 | `nasyncio.py`（默认实现）、`std_asyncio.py`（兼容垫片）、`cancel.py` |
| `runtime/` | np() 启动、生命周期、热挂载 | `engine.py`（NorpEngine 状态机）、`mount.py`（装配器）、`remount.py` |
| `recovery/` | 工作回退：快照 / Undo / Redo / Rollback | `__init__.py`（回放逻辑）、`capture.py`、`store.py` |
| `rescue.py` | 崩溃救援 CLI（纯标准库） | `norpagent-rescue` |
| `hooks/` | 9 层 29 钩子体系 | `core.py`（Hook/HookLayer/HookSystem）、`standard.py`（标准钩子） |
| `security/` | 安全管线（审批/审计/签名/防护/网络策略） | `approval.py`、`audit.py`、`guard.py`、`signature.py`、`network_policy.py` |
| `safe.py` | `norpagent.safe()` 一句话挂载安全套件 | SafetyKit |
| `plugins/` | 插件系统（签名→审计→导入限制→注册） | `loader.py`（管线）、`isolation.py`（进程级隔离）、`host.py`（宿主进程）、`manager.py` |
| `models/`、`tools/`、`sessions/`、`sandboxes/`、`frontends/` | 各协议与内置实现 | 见 `builtin/` |
| `builtin/` | 全部内置组件（懒加载） | `ui/web.py`（WebUI 2596 行）、`models/`、`tools/`（21 个工具）、`sessions/`、`sandboxes/`、`scheduler/`、`context/fts5.py`、`projects/basic.py` |
| `flows/` | FLOW 流程编排内核（1535 行） | `ModuleWorkspace`（文件即模块）、`FlowRunner`（拓扑执行） |
| `modes/` | 六种预设模式 | `standard` / `minimal` / `ptc` / `creative` / `longrun` / `embedded` |
| `protocols/` | 组件协议（接口契约） | `model.py`、`tool.py`、`session.py`、`sandbox.py`、`scheduler.py`、`ui.py`、`plugin.py` |
| `cli.py` | 命令行入口 | `norpagent` / `plugin-sign` |
| `__init__.py` | 模块即入口 | `np()` 可调用模块 |

---

## 3. 槽位系统：可插拔的积木盘

### 3.1 18 个内置槽位

| 槽位 | 字符串语义 | 默认 | 一句话职责 |
|---|---|---|---|
| `async_loop` | address | NasyncioLoopRuntime | 事件循环系统（Agent 的调度核心） |
| `agent_runtime` | address | AgentRuntime | Agent 循环本体（defer_factory 槽位） |
| `model` | name_or_address | 预设声明 | 模型提供者（大脑） |
| `tools` | name | 预设声明 | 工具集（手） |
| `session` | name_or_address | 预设声明 | 会话管理（记忆） |
| `sandbox` | name_or_address | 预设声明 | 沙箱环境（执行隔离边界） |
| `scheduler` | name_or_address | 预设声明 | 任务调度器 |
| `context_store` | address | 预设声明 | 上下文知识库 |
| `project_manager` | address | 预设声明 | 项目管理（git 感知） |
| `hooks` | literal | 标准 9 层 | 钩子批量订阅 |
| `security` | literal | 不开启 | 安全级别 |
| `plugins` | literal | 不加载 | 插件目录 |
| `frontend` | address | web | 前端外壳 |
| `ui` | name | 预设声明 | UI 渲染器 |
| `preset` | name | standard | 预设模式 |
| `logger` | literal | `logging.getLogger("norpagent")` | 日志器 |
| `storage` | literal | `~/.norpagent` | 存储根目录 |
| `error_handler` | literal | 记录日志 | 错误处理 |

### 3.2 字符串语义：一个字符串，四种读法

槽位对「字符串值」的解读由 `SlotSpec.string_semantics` 声明：

- **`address`**：字符串 = 模块地址（`"pkg.mod[:attr]"`）；
- **`name`**：字符串 = 注册表组件名（原样透传，由装配器查表）；
- **`name_or_address`**：先按注册表名、查不到再按模块地址；
- **`literal`**：字符串 = 字面值（级别 / 路径等）。

**v0.9.1 起全部槽位支持按地址加载**：`literal` 槽位（security / storage / hooks / plugins / logger / error_handler）与 `name` 槽位的字符串值若**形如纯地址**（含 `.` 或 `:` 的点分标识符），一律按地址加载——`np(security="high")` 仍是级别、`np(storage="./data")` 仍是路径，但 `np(security="myapp.policy:high")` 就是地址加载。任何槽位的 **dict 键值对的值**支持纯地址解析（`tools={"my_tool": "myapp.tools:create"}`），解析失败抛 `AddressError`（严格报错，不静默回落）。

### 3.3 工厂调用约定：签名裁剪注入

架构层调用工厂时按**签名裁剪**注入上下文：工厂声明什么参数就注入什么，不声明的键自动忽略。

```python
def create_loop(layer, config): ...      # 只收 layer 和 config
def create_tool(layer): ...              # 只收 layer
def make_agent(registry, preset, layer, config): ...   # defer_factory 槽位
```

这样同一个槽位既能接「文件级模块实现」，也能接「带上下文的工厂函数」，还能接「现成实例」——**任意风格的实现都能接入**。

### 3.4 槽位表热插拔（v0.9）

槽位表本身也是可插拔的：`register_slot()` 运行时注册自定义槽位，注册即接入完整管线：

```python
register_slot(SlotSpec(
    name="vector_store",
    description="向量检索组件（自定义装配槽位）",
    protocol="任意实现（注册为 vector_store 通用组件）",
    string_semantics="literal",
    applier=apply_vector_store,     # 装配逻辑：挂到注册表 / 预设 / 引擎
    remount_rebuild_agent=True,     # 热替换后热重建 AgentRuntime
))
```

- 注册后：`np(vector_store=...)` 参数校验、装配、`np.remount()` 热替换、`layer.describe()` 清单全管线自动生效；
- 内置 18 槽位是**框架结构契约**，受保护不可注册 / 覆盖 / 注销（其值仍可随时 `np.remount` 热替换）；
- `applier` 必须**重入安全**（热挂载会重复调用），可用 `ctx["meta"]` 记录可退订对象防止副作用叠加。

### 3.5 运行中热挂载（Hot Remount）

`np.remount(slot=value)` 在运行中替换任意槽位：

- 字符串地址会先**失效模块缓存**（删 `.pyc` + 弹出 `sys.modules`），因此「改模块文件 → remount」就是热重载；
- Web 页面热替换：`np.remount(html="/path/new.html")` / `np.remount(flow_html=...)`，HTTP 服务不重启、端口不变；
- 装配面 `apply_slot_overrides` 可对运行中的注册表重复执行，旧的架构级订阅（钩子 / 安全套件 / 插件）先退订再重挂，**不叠加重复订阅**；
- `remount_rebuild_agent=True` 的槽位热替换后，AgentRuntime 会热重建，组件立即生效。

**装配槽位重建的在途任务竞态（已知边界，如实记录）**：`session /
sandbox / scheduler / ui / agent_runtime / preset / context_store /
project_manager` 槽位热替换走「停旧运行时 → 按当前装配建新运行时 →
前端重绑渲染器」三步，**均不等待工作池中在途的 `agent.run` 任务**。
若重建恰逢任务执行中：旧沙箱已关闭导致在途 run 的下一次工具调用失败；
渲染器退订 / 重订窗口期内事件丢失、重绑后旧 run 残余事件与新 run
交错；在途 run 的结果仍返回给其调用方（一个来自已死亡运行时的结果）。

**建议的两阶段热挂载（drain）**：① 阻断新任务入队（drain 标志）；
② `loop.interrupt()` 置位全部在途任务取消事件 + 工作池 join 宽限；
③ 超时强弃（沙箱关闭语义兜底）；④ 再替换装配槽位。当前版本未内置
drain，属业务侧职责（`interrupt()` 基础设施已存在，实现成本低）。

---

## 4. 内核设计：注册表 / 事件总线 / Agent 循环

### 4.1 Registry：一切皆注册项

```python
class Registry:
    _models / _tools / _sessions / _sandboxes / _schedulers / _uis
    _plugins / _presets
    _components: Dict[str, Dict[str, Callable]]   # 通用组件命名空间
    security        # 安全上下文（safe() 安装，Agent 循环据此读取策略）
    hooks           # 同一总线上的 9 层钩子视图（懒加载）
```

`AgentRuntime` 构造时通过 `registry.validate_preset(preset)` 做组件齐备性校验，缺失时给出可操作的报错（提示可用模型 / 工具清单与可选依赖）——**快速失败，不静默降级**。

### 4.2 EventBus：写时复制（Copy-on-Write）

高并发场景下事件总线的核心优化：

- 订阅者表是**不可变快照**：subscribe / unsubscribe 在锁内创建新列表并替换引用，绝不原地修改；
- emit / intercept 只在锁内取一次引用，随后**无锁直接迭代**——高频事件（如流式 `on_content` 逐 token 推送）省去每条事件的列表复制开销；
- 并发写者替换的是新列表对象，读者持有的旧快照不会被修改，线程安全性不变。

实测 >160 万事件/秒（详见第 13 章）。

**口径与边界（如实标注）**：该实测为**单机单线程发布**场景（单一发布
线程、订阅表静态、无并发 subscribe / remount）。「无锁迭代 ≠ 无锁
emit」——每次 emit 仍在锁内取一次订阅表快照引用，随后才无锁迭代。
单线程发布下锁无竞争；动态热挂载并发下，写者持锁复制列表期间 emit
短暂等待（µs 级，订阅者规模小时可忽略）；「先订阅后发布」的旧表
可见性是 COW 的线性化语义而非缺陷。彻底消除 emit 锁获取（读引用不拿
锁）技术上可行，属可优化项，当前实现保守保留读锁。

### 4.3 AgentRuntime：内核中唯一的循环

```
输入(L3) -> 会话与历史(L4) -> 消息组装(L5) -> [ 步骤(L6) ->
模型调用(L7) -> 工具调用(L8) ]* -> 结果定型(L9)
```

- 循环只依赖注册表与协议接口，不 import 任何具体模型 SDK 或工具实现；
- 每个执行结构暴露为公共方法（可覆写）+ 具名钩子（可订阅/改写/否决），双通道干预；
- 连续空输出达到 `_EMPTY_OUTPUT_LIMIT=3` 次则中断任务，防止模型退化死循环；
- `ModelCallTimeout` 硬超时：主循环立即放弃等待并返回 timeout 结果，后台请求线程被标记取消，适配器流式循环据此尽早退出；
- 任务取消：`params["_cancel_event"]`（`threading.Event`）贯穿模型流式循环与工具执行，Ctrl+C / 引擎停止可尽早中断；
- 安全扫描（jailbreak_guard / harden_prompt）走 `params` 显式路径；`norpagent.safe()` 的钩子路径仅在用户显式开启时生效（默认零干预）。

### 4.4 Preset：声明式装配

```python
Preset(name, model, tools, session, sandbox, scheduler, ui,
       components={kind: name}, params={...}, mode=...)
```

预设是「默认逻辑」的载体：槽位不填 = 预设说了算；槽位填了 = 覆盖预设。`merged_params(task_params)` 合并出最终运行参数（构造级 → 任务级覆盖）。支持从 `.py` 文件加载自定义预设（创造模式）。

---

## 5. 自研异步核心 nasyncio

### 5.1 为什么不用标准 asyncio

这是本框架最激进也最重要的技术决策。`norpagent.nasyncio`（原 nasync_io，v2.0.0，1067 行）是打包进库的自研异步 IO 库，底层只依赖标准库的非 asyncio 模块（`threading` / `selectors` / `socket` / `heapq` 等）。原因：

1. **调度 / 取消 / 跨线程唤醒语义完全自控**——修复标准 asyncio 的公认坑：`Task.cancel()` 非线程安全、done 回调不写自管道导致等待方挂起、没有对外的「取消主任务」入口；
2. **依赖面压缩到可审计**——事件循环全部行为是库内自己写的代码，不引入标准 asyncio 的内部实现细节与版本差异；
3. **退出语义可控**——不用会被解释器强制 join 的 `ThreadPoolExecutor`，Ctrl+C 后进程即刻收尾；
4. **API 语义对齐、迁移零成本**——`EventLoop` / `Future` / `Task` / `Lock` / `Condition` / `sleep` / `wait_for` 等与 asyncio 同名对应，`import asyncio` 换成 `import norpagent.nasyncio` 即可移植；
5. **核心独立可用**——`norpagent.nasyncio` 本身是独立微型异步库，可脱离框架单独使用。

### 5.2 线程模型

```
一个 EventLoop 绑定一个线程（与框架一致：每个会话一个循环线程）
run_forever 在哪个线程调用，循环就属于哪个线程
跨线程提交（call_soon_threadsafe / Event.set / Future 完成通知 /
Task.cancel / run_coroutine_threadsafe）
  → 通过「线程安全队列 + socketpair 自管道」唤醒阻塞在 select 上的循环线程
```

### 5.3 取消语义（与 asyncio 的关键差异）

| 场景 | 标准 asyncio | norpagent.nasyncio |
|---|---|---|
| `Task.cancel()` 线程安全 | 否（仅限循环线程） | 是（跨线程安全） |
| done 回调写自管道 | 不写，等待方可能挂起 | 写，等待方立即被唤醒 |
| 对外「取消主任务」入口 | 无 | `loop.interrupt()`（取消全部在途 submit 任务） |
| 异常类型 | `asyncio.CancelledError` | `CancelledError(BaseException)`——不会被普通 `except Exception` 吞掉，取消语义一定穿透 |

### 5.4 在框架中的位置

```
np.nasyncio()              → LoopRuntime（架构函数，默认实现）
np.nasyncio.EventLoop      → 自研事件循环类（可直接使用）
np.nasyncio("myapp.loop:create") → 地址指向的自定义循环
async_loop 槽位默认        → norpagent.loops.nasyncio:NasyncioLoopRuntime
0.7 旧地址                 → norpagent.loops.std_asyncio:StdLoopRuntime（兼容垫片，不 import asyncio）
```

### 5.5 守护工作池：无界队列与协作式取消（边界如实说明）

`NasyncioLoopRuntime.submit` 走守护工作池（`_DaemonPool`），语义边界：

- **无界队列、无拒绝策略**：内部 `queue.Queue()` 无 maxsize，
  `put_nowait` 永不失败——池打满时新任务无限堆积，调用方在
  `poll_interval` 轮询中无限等待，不快速失败；
- **协作式取消**：取消事件仅在执行体主动检查的边界生效
  （`cancel_requested()`，见手册 4.6.2）。C 扩展纯计算（正则回溯等）
  与裸 subprocess 阻塞不可打断；沙箱路径有分片检查 + 强杀进程树兜底；
- **无任务时间预算**：worker 一次只跑一个任务，单任务卡死 → 占满
  工作池 → 后续 submit 全部堆积（业务吞吐趋零，无额外线程垫底）。
  模型调用超时（弃置线程）与引擎停止（`join(timeout)`）已有「时间
  预算」先例；推广到工作池（deadline 看门狗 + 弃置 + 有界队列快速
  失败）列为演进方向，当前未实现。

---

## 6. 钩子系统：9 层 29 钩子

### 6.1 分层结构

```
L1 运行时生命周期   on_agent_init / on_agent_shutdown
L2 任务生命周期     on_task_start / done / error / stopped / timeout
L3 输入管线         before_input* / after_input / on_user_input_required
L4 会话与历史       before/after_session_create、before/after_message_append
L5 消息组装         before/after_build_messages
L6 步骤             before_step* / after_step
L7 模型调用         before/after_model_call* + on_reasoning/on_content/on_event/on_usage_update
L8 工具调用         before/after_tool_call* + on_tool_error
L9 结果定型         before_result* / after_result*
（带 * 为可变钩子：可改写数据流或抛 HookVeto 一票否决）
```

### 6.2 三个设计要点

**① 每个钩子都是独立模块级 API**

```python
from norpagent.hooks import before_model_call
before_model_call.subscribe(fn, system=registry)     # 模块级独立 API
agent.hooks.before_model_call.subscribe(fn)          # 运行时绑定视图（等价）
```

**② HookVeto 一票否决**：可变钩子抛 `HookVeto(reason)` 后，运行时按执行点语义安全收尾——`before_input` → 任务 stopped；`before_tool_call` → 阻止调用（回填 blocked_by_hook）；`before_model_call` → 拒绝本轮调用；`before_message_append` → 消息不落库。EventBus.intercept 对 HookVeto **不捕获**，保证否决语义一定传递到内核。

**③ 自定义层 / 自定义钩子 / 零定义触发**

```python
network_layer = HookLayer("L10_network", order=100, ...)
agent.hooks.install_layer(network_layer)             # 自定义层
agent.hooks.define_hook("after_cache_hit", ...)      # 直接定义
agent.hooks.hook("my_custom_event").emit(data=42)    # 零定义触发（自动成为 dynamic 层钩子）
```

### 6.3 与 EventBus 的关系

钩子体系是 EventBus 的**结构化视图**：订阅 / 发布最终落在 EventBus 上，旧插件（15 个 hook 名）与新钩子体系无缝共存。事件名与早期 `plugin_system` 完全对齐，历史插件无需修改。

---

## 7. 前端体系与 Web UI

### 7.1 两层结构：Frontend（外壳）与 UIAdapter（渲染器）

- **Frontend**：控制「输入从哪里来」——console 读键盘、headless 走 API、web 开 HTTP 服务；
- **UIAdapter**：控制「输出到哪里去」——订阅事件总线，把流式输出渲染给用户。

两者解耦后：Web 前端可以换 console 渲染器，console 前端也可以换任意自定义渲染器。

### 7.2 Web UI：零第三方依赖的 HTTP + SSE

`builtin/ui/web.py`（2596 行）是框架最大的单个文件，用纯标准库实现了完整聊天应用：

- **HTTP 服务**：`_RobustHTTPServer`（ThreadingHTTPServer 调优版）——断连噪声静默、守护线程、端口复用、监听积压 256、`block_on_close=False` 快速停机；
- **SSE 通道**：`/events` 长连接推送全部事件（`on_content` 逐段流式输出、`on_reasoning` 思维链、工具调用过程等）；
- **有界背压**：每个 SSE 连接一个 `_SSESubscriber`（有界双端队列 + 条件变量），三种策略——`drop_oldest`（默认）/ `drop_newest` / `unlimited`，**运行中可热改变**（`ui.set_sse_queue()` / `POST /api/streams`）；
- **页面热替换**：`mount_page("flow", html)` 运行中换页，HTTP 不重启、端口不变；
- **REST API 全集**：任务提交 / 会话管理 / 配置面板 / 插件管理 / 安全设置 / 流程编排 / 文件浏览 / 健康检查（`/api/health`、`/api/usage`、`/api/debug_info`）等数十个端点；
- **前端模块（FE）**：`/flow` 可视化画布 + FE 前端模块（.html/.js/.ts 独立配置，重启不丢失）。

### 7.3 三种内置前端

| 前端 | 适用场景 | 特点 |
|---|---|---|
| `console` | 命令行 REPL | Python REPL 中自动切换同步阻塞模式 |
| `headless` | 嵌入式 / 服务化 / 测试 | 纯 API，输出打印 stdout，不监听端口 |
| `web` | 默认 | HTTP + SSE，零依赖，浏览器访问即用 |

---

## 8. 安全架构

### 8.1 核心思想：安全系统整体剥离，默认钩子零干预

`norpagent.safe()` 默认只挂运行态策略（人工审批 / 网络策略 / 插件加载策略），**不订阅任何钩子**——钩子管线保持纯净，Agent 循环的每一次执行不被「隐形」的安全代码干预。干预钩子的权力完全交给用户：

```python
from norpagent.safe import safe
kit = safe(reg, level="high", hooks=True)   # 安装时即挂钩子（可选）
kit = safe(reg, level="high")               # 默认：零干预
kit.install_hooks(reg) / kit.uninstall_hooks(reg) / kit.hooks_installed(reg)
```

防护能力保留为独立 API（`kit.scan_input()` / `kit.harden()` 等），用户可在自己的钩子订阅者中自由调用。

### 8.2 安全组件矩阵

| 组件 | 职责 | 关键点 |
|---|---|---|
| `ApprovalPolicy` | 人工审批 | 工具 → 级别映射（file_write→WRITE、file_delete→DELETE、exec_cmd→EXEC、插件→PLUGIN） |
| `NetworkPolicy` | SSRF 防护 | 四粒度（deny / audited_public / public_only / allow_all），非 allow_all 一律拒绝私网 / 环回 / 链路本地 / 云元数据地址 |
| `SourceAuditor` | 静态审计 | AST 分析：危险调用、动态调用、可疑 base、权限检查 |
| `SignatureVerifier` | Ed25519 验签 | NORP 插件签名协议 v1，官方公钥 / 自建密钥，cryptography 可选 |
| `guard` | 越狱 / 注入防护 | 关键模式扫描、零宽字符混淆、Base64 载荷、提示词加固 |

### 8.3 三档级别

| 级别 | 内容 |
|---|---|
| `basic` | 基础防护：输入扫描（显式路径） |
| `standard` | 标准：审批 + 网络策略 + 插件加载策略 |
| `high` | 严格：标准 + 审计 + 签名要求（配合受信任密钥） |

---

## 9. 插件系统

### 9.1 完整安全管线

```
签名 → 审计 → 导入限制 → 注册
  │      │        │         │
  │      │        │         └─ 工具进工具表、钩子订阅事件总线
  │      │        └─ _ImportBlocker：插件 import 拦截（危险模块拒绝）
  │      └─ SourceAuditor：AST 静态审计（危险调用 / 权限）
  └─ SignatureVerifier：Ed25519 验签（信任密钥 / 官方密钥）
```

### 9.2 三种运行形态

| 形态 | 实现 | 适用场景 |
|---|---|---|
| 进程内 | `PluginLoader._load_from_file` | 默认，性能最好 |
| 进程级隔离 | `ProcessIsolationManager` + `PluginHostProcess` | 不可信插件：子进程 RPC（load / call_tool / fire_hook），超时 + 重试 + 僵尸回收 |
| 库化门面 | `PluginSystem` | 完整生命周期 + 状态 + 热重载 |

### 9.3 兼容性设计

- 旧插件钩子桥接：15 个 hook 名对齐，`_LegacyPluginAdapter` / `_LegacyToolAdapter` 无缝映射；
- 插件格式兼容：单 `.py` 文件或 `manifest.json` 目录（`entry` 字段）；
- 插件钩子成为 FLOW 画布上的钩子节点（一个钩子 = 一个节点）。

---

## 10. 可靠性设计：工作回退

### 10.1 四个层次

| 层次 | 能力 | 生效范围 |
|---|---|---|
| Undo / Redo | 撤销 / 恢复最近操作 | 进程内即时 |
| Rollback | 回退到任意历史快照 | 进程内即时 |
| Crash Rescue | 独立 CLI 回退快照 | 主程序起不来也能用（纯标准库） |
| Safe Mode | 只加载最小化内核 | 跳过全部插件，保留回退能力 |

### 10.2 快照内容与时机

- 内容：架构层全部槽位配置（模式 / 模型 / 工具 / 会话 / 沙箱 / 前端 / 插件目录 / 安全级别…）、引擎运行时参数（敏感键脱敏）、WebUI 设置文件、自定义提供者数据（`register_snapshot_provider` 扩展）；
- 时机：启动基线 → 每次系统状态变更（remount / 设置保存 / 插件安装 / 模式切换）→ 手动 `np.snapshot_system("说明")`；
- 「最后正常快照」：引擎启动成功后 30 秒健康期（或首个任务完成）自动 mark-good，救援 CLI 据此提示一键恢复目标；
- 存储：`~/.norpagent/snapshots/`（`NORPAGENT_SNAPSHOT_DIR` 可覆盖），自动修剪保留 200 个。

### 10.3 设计要点

- 回放复用 remount 管线（HTTP 端口不变）——因为「装配 = 数据」，所以「回退 = 重放数据」；
- 回放进行中标记 `_applying`，防止回退本身再次触发自动快照覆盖 redo 分支；
- `norpagent-rescue` 与主程序完全解耦（纯标准库），rollback 落成 `rollback_target.json`，下次启动自动消费。

---

## 11. FLOW 模块流程编排

### 11.1 定位：可视化画布对接真实注册表

`/flow` 不是动画演示，而是用真实注册组件执行画布图：

```
build_snapshot(registry, agent)  注册表快照 → 前端模块坞按真实组件渲染卡片
ModuleWorkspace.register(...)    「文件即模块」：拖入 .py 走完整安全管线
FlowRunner                       按画布图（节点 + beam）拓扑执行
```

### 11.2 节点执行语义（type → 真实动作）

| 节点 | 真实动作 |
|---|---|
| `trigger` | 读取 prompt 输入，产出 start 信号 |
| `model` | 调用注册表真实模型（可逐节点覆盖模型 / 工具集 / 系统提示词） |
| `tool` | 调用注册表真实工具（每个输入端口 = 一个 schema 参数） |
| `toolbox` | 工具容器：成员工具参数并集扇出投递 |
| `sandbox` | 在注册表真实沙箱执行 code（子进程隔离） |
| `security` | 对 payload 做越狱 / 注入扫描（guard） |
| `session` | 读写会话管理器 |
| `plugin` | 插件容器（工具 + 钩子成员） |
| `hook` | 触发单个插件钩子（可变钩子走 intercept） |
| `path` | 产出经过公共路径安全校验的相对路径 |
| `file` | 已注册为插件则按插件执行，否则直通 |
| `other` / `output` | 直通 / 汇总最终结果 |

### 11.3 关键设计

- **零中断语义**：每个节点独立 try/except，单节点失败记录 error 但不中断整条链路；
- **进度经 SSE 推送**：`flow.*` 事件复用 `/events` 通道实时送达浏览器；
- **流程可应用为智能体**：`flow_save(activate=True)` 后，主界面聊天任务按激活流程执行（`_run_flow_task`），流程执行结果写入会话历史，与普通任务一致；
- **文件即模块**：单文件上限 200KB，`.py` 走插件安全管线（签名 → 审计 → 导入限制 → 注册），`.json` / `.yaml` 注册为纯描述模块。

---

## 12. 嵌入式与超高并发

### 12.1 嵌入式（依赖面最小化）

```python
from norpagent import Registry, AgentRuntime, install_core
from norpagent.modes import build_embedded_preset

reg = Registry()
install_core(reg)                       # 不导入 sqlite3 / http.server
reg.register_preset(build_embedded_preset())
agent = AgentRuntime(reg, preset="embedded")
result = agent.run("你好")
```

- `install_core()`：极简装配，连安装阶段都避开 sqlite3 / http.server 依赖；
- `embedded` 预设：纯内存（memory 会话 + subprocess 沙箱 + simple 调度器）、默认 headless、零磁盘依赖、不触网；未提供凭据时模型自动回落 mock。

### 12.2 超高并发三件套

1. **EventBus 写时复制**：emit 无锁迭代，省去每条事件的列表复制；
2. **SSE 有界背压**：慢客户端不再无限吃内存，默认丢最旧；支持运行中热改变；SSE 帧批量写出 + 断连 ≤1s 快速回收；
3. **HTTP 并发调优**：监听积压 256、反向代理缓冲禁用、守护线程快速停机。

---

## 13. 性能设计与实测

| 项目 | 数据 | 手段 |
|---|---|---|
| EventBus 发布 | 实测 >160 万事件/秒（静态订阅表 + 单线程发布口径） | 写时复制 + 无锁迭代 |
| SSE 推送 | 有界缓冲，慢客户端内存有上限 | 背压三策略 + 批量 flush |
| HTTP 服务 | 监听积压 256，高并发不丢 SYN | ThreadingHTTPServer 调优 |
| 停机 | Ctrl+C 进程即刻收尾 | 自研循环 + 守护线程 + 不 join 线程池 |
| 工具执行 | 沙箱池复用 + 超时强杀进程树 | PooledSandbox |
| 插件隔离 | 子进程 RPC 超时 + 重试 + 僵尸回收 | ProcessIsolationManager |

**口径与盲区（如实记录）**：EventBus 160 万/秒为单机单线程发布、
订阅表静态的基准；动态热挂载（并发 subscribe / remount）下的 P99
无专项实测，锁竞争代价为 µs 级（订阅者规模小时可忽略）。守护工作池
无任务时间预算，单任务卡死会使业务吞吐趋零——边界与演进建议见
5.5 节（手册 4.6.4 有兜底矩阵）。

> 验证脚本：`test/_verify_*.py` / `test/_smoke_*.py`（覆盖 nasyncio 迁移、取消语义、热挂载循环、槽位热插拔、嵌入式与并发、WebUI 冒烟、front.html 迁移等全部专项）。

---

## 14. 关键架构决策记录（ADR）

| # | 决策 | 备选 | 理由 |
|---|---|---|---|
| 1 | 自研事件循环，零 asyncio 依赖 | 使用标准 asyncio | 取消 / 跨线程唤醒语义自控；依赖面可审计；退出语义可控 |
| 2 | 安全系统整体剥离，默认零钩子干预 | 安全逻辑内嵌钩子 | 钩子管线保持纯净；防护能力作为独立 API 按需启用 |
| 3 | 地址函数取代工厂注册 API | 只提供 register/工厂 API | 「换零件 = 改数据」，可序列化 / 快照 / 回放 |
| 4 | 槽位表本身可热插拔 | 槽位表写死 | 第三方可扩展框架契约，注册即接入全管线 |
| 5 | 内核保持 4 模块最小 | 内核扩大 | 审计面小、测试面小、心智负担小 |
| 6 | Web UI 纯标准库实现 | 引入 Flask/FastAPI | 零第三方依赖；HTTP + SSE 足够 |
| 7 | 工作回退复用 remount 管线 | 独立回退逻辑 | 装配 = 数据 → 回退 = 重放数据，天然一致 |
| 8 | 模块即入口（可调用模块） | 独立 CLI / 工厂函数 | 最小元编程代价，`np()` 即入口 |
| 9 | 预设声明式装配 | 硬编码默认逻辑 | 槽位不填 = 预设说了算，六种模式一套机制 |
| 10 | 懒加载内置组件 | 全量 import | 启动快、嵌入式友好（`_LAZY_EXPORTS` + `__getattr__`） |
| 11 | 守护工作池采用无界队列 + 协作式取消（如实记录边界） | 有界队列 / 任务时间预算 / 快速失败 | 实现简单；取消已有组件级兜底（沙箱强杀进程 / 模型超时弃置）；无界队列避免复杂拒绝策略；已知边界入档（5.5），任务时间预算与有界化列为演进方向 |

---

## 15. 总结与演进方向

### 15.1 一句话总结

**NORP Agent 把「可插拔」从口号做成了机制**：地址函数让替换成为数据操作，最小内核让系统可审计，暴露面原则让每个执行结构都可干预，自研底层让调度语义完全自控。

### 15.2 值得借鉴的工程实践

1. 「换零件 = 改数据」的装配模型 → 天然支持快照、回放、热重载；
2. 「最小内核 + 一切皆槽位」→ 核心稳定，外围自由；
3. 「每个执行结构都是 API」→ 钩子干预与继承覆写双通道；
4. 「默认零干预」→ 安全能力在场但不对正常路径产生隐形开销；
5. 「兼容垫片」→ 0.7 旧地址保留，历史代码不失效。

### 15.3 演进方向

- **多 Agent 协作**：persistent 调度 + FLOW 画布已具备任务编排基础，可扩展多 Agent 拓扑；
- **自定义层钩子生态**：标准库已提供插件加载管线用例，可沉淀更多自定义层模式；
- **嵌入式部署打磨**：install_core + embedded 预设 + 资源调优环境变量，可进一步支持边缘场景；
- **观测性**：SSE 背压统计 / usage / debug_info 已内置，可扩展指标导出（Prometheus 等）；
- **高并发边界治理**：EventBus 零锁 emit（去掉 emit 的读锁获取）、守护工作池任务时间预算（deadline 看门狗 + 弃置线程）、装配槽位 remount 两阶段排水（drain）——三处边界已入档（4.2 / 5.5 / 3.5），落地为源码级能力是下一步；
- **更多模型适配器**：协议层已抽象（ModelProvider），可低成本接入新协议。

---

*NORP Agent 技术架构白皮书 · v0.9.2 · Copyright (c) 2026 xingluosama121, MIT Licensed*

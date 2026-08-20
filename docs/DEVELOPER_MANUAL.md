# NORP Agent 开发手册

> **版本**：0.9.4 ｜ **许可**：Copyright (c) 2026 xingluosama121, MIT Licensed
>
> NORP Agent初版发布于2026年7月29日，在8月16日正式上线PyPI，定位为“智能体元框架”。
> 2026-08 修订：第 24 章救援模式专章（底层循环控制 + 人类接管）｜ 内核修复：select 超时上限钳制（暴力压测发现，Windows 超远定时器崩溃）｜ 新增 35 项最小主异步循环暴力压测（test/stress_nasyncio_core.py）｜ 15.6 人类救援手动工具接管 API（v0.9.3，模型失效时手操全部工具：tools / tool-call / manual / serve）｜ 3.9 任务级槽位注入（submit(slot_overrides=...)）｜ 3.7 装配槽位热重建的在途任务竞态与排水建议 ｜ 4.6.4 守护工作池队列语义与卡死兜底矩阵 ｜ 23.1 EventBus 基准口径与锁竞争边界

---

## 目录

- [第 1 章　快速上手](#第-1-章快速上手)
- [第 2 章　总体架构：分层与数据流](#第-2-章总体架构分层与数据流)
- [第 3 章　架构层与地址函数](#第-3-章架构层与地址函数)
- [第 4 章　事件循环系统：norpagent.nasyncio()](#第-4-章事件循环系统norpagentnasyncio)
- [第 5 章　前端体系](#第-5-章前端体系)
- [第 6 章　np() 启动与生命周期](#第-6-章np-启动与生命周期)
- [第 7 章　模型与工具](#第-7-章模型与工具)
- [第 8 章　会话、沙箱、调度器、上下文与项目](#第-8-章会话沙箱调度器上下文与项目)
- [第 9 章　9 层 29 钩子](#第-9-章9-层-29-钩子)
- [第 10 章　安全系统：norpagent.safe()](#第-10-章安全系统norpagentsafe)
- [第 11 章　插件系统](#第-11-章插件系统)
- [第 12 章　预设模式](#第-12-章预设模式)
- [第 13 章　命令行入口](#第-13-章命令行入口)
- [第 14 章　嵌入式与超高并发部署](#第-14-章嵌入式与超高并发部署)
- [第 15 章　工作回退：快照 / Undo / Redo / 崩溃救援 / 安全模式](#第-15-章工作回退快照-undo--redo--崩溃救援--安全模式)
- [第 16 章　库集成示例](#第-16-章库集成示例)
- [第 17 章　测试与调试](#第-17-章测试与调试)
- [第 18 章　迁移指南](#第-18-章迁移指南)
- [第 19 章　常见问题（FAQ）](#第-19-章常见问题faq)
- [附录 A　架构槽位速查表](#附录-a架构槽位速查表)
- [附录 B　9 层钩子速查表](#附录-b9-层钩子速查表)
- [附录 C　公开 API 索引](#附录-c公开-api-索引)
- [第 20 章　模块流程编排（FLOW）](#第-20-章模块流程编排flow)
- [第 21 章　内置组件深度剖析](#第-21-章内置组件深度剖析)
- [第 22 章　Web UI 与前端深度解析](#第-22-章web-ui-与前端深度解析)
- [第 23 章　性能设计与基准测试](#第-23-章性能设计与基准测试)
- [第 24 章　救援模式：底层循环控制与人类接管](#第-24-章救援模式底层循环控制与人类接管)
- [附录 D　术语表](#附录-d术语表)
- [附录 E　29 钩子事件负载速查表](#附录-e29-钩子事件负载速查表)

---

## 第 1 章　快速上手

### 1.1 安装

```bash
pip install norpagent
```

核心包无第三方依赖，安装后可在纯 Python 环境运行（内置 mock 模型与工具）。
可选能力按需安装：

```bash
pip install norpagent[openai]       # OpenAI 兼容模型适配器（DeepSeek/OpenAI/Qwen/vLLM/Ollama）
pip install norpagent[anthropic]    # Anthropic 协议模型适配器
pip install norpagent[web]          # 联网检索（web_search / web_fetch 工具）
pip install norpagent[security]     # 插件 Ed25519 验签（cryptography）
pip install norpagent[all]          # 全部
```

### 1.2 第一个程序

```python
import norpagent as np

np()                    # 按默认配置启动（standard 预设 + Web 前端）
running = True
while running:
    if np.stop() == True:   # 生命周期函数：应用结束即退出
        running = False
```

保存为 `hello.py` 运行，控制台打印：

```
[norpagent] frontend web listening on 127.0.0.1:8787
[norpagent] lazy-loaded modules: ...   # 本次已加载的懒加载模块（如有）
```

浏览器访问该地址打开聊天界面。启动流程见第 6 章。两个要点：

1. **`np()` 是模块级调用**——`norpagent` 模块本身可调用，等价于 `norpagent.launch()`；
2. **`np.stop()` 是生命周期函数**——返回 `True` 表示 Agent 应用已结束，主循环应退出。

### 1.3 单次任务模式

```python
import norpagent as np

np(prompt="用一句话解释什么是地址函数")
running = True
while running:
    if np.stop() == True:
        running = False

engine = np.current()
print(engine.last_result.final_content)
```

传入 `prompt` 后：Agent 执行完这一条任务即自动停止（`np.stop()` 变为 `True`），
任务结果保存在 `np.current().last_result`。

### 1.4 替换前端示例

```python
import norpagent as np

np(prompt="hi", frontend="norpagent.frontends.headless:HeadlessFrontend")
while True:
    if np.stop():
        break
```

HeadlessFrontend 不读取键盘输入、不渲染界面，通过程序 API 驱动。
组件替换通过为槽位填入新地址完成，不修改框架核心代码。

### 1.5 本章用法速查

| 用法 | 写法 |
|---|---|
| 按默认配置启动 | `np()` |
| 判断应用是否结束 | `np.stop()` |
| 单次任务 | `np(prompt="...")` |
| 指定预设模式 | `np(preset="standard")` |
| 指定模型 | `np(model="openai_compat")` |
| 指定事件循环 | `np(async_loop="myapp.loop:create")` |
| 指定前端 | `np(frontend="myapp.ui:create")` |
| 指定会话存储 | `np(session="sqlite")` |
| 指定安全级别 | `np(security="high")` |
| Web 端口 / 语言 | `np(port=9000, language="zh_CN")` |
| 自定义主页面 | `np(html="/path/to/my.html")` |
| 自定义模块流程页 | `np(flow_html="/path/to/flow.html")` |
| 前端直挂 HTML 路径 | `np(frontend="/path/to/my.html")` |

---

## 第 2 章　总体架构：分层与数据流

### 2.1 架构说明

NorpAgent 的架构：除底层最小内核外，全部组件都是可替换槽位。
替换组件（模型、工具、会话、沙箱、调度器、前端、事件循环、
Agent 循环）时，为槽位填入新地址即可，无需修改框架核心代码。

### 2.2 分层图

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

### 2.3 底层最小内核（四个模块）

最小内核由以下四个模块组成：

| # | 模块 | 职责 | 不可替换原因 |
|---|---|---|---|
| 1 | `norpagent.arch.layer.ArchLayer` | 槽位连接器 | 负责装配动作 |
| 2 | `norpagent.arch.address` | 地址解析器 | 提供地址语义 |
| 3 | `norpagent.kernel.registry.Registry` | 注册表 | 名称到组件的映射中心 |
| 4 | `norpagent.kernel.events.EventBus` | 事件总线 | 组件间的事件传递通道 |

其余组件——事件循环、Agent 循环、模型、工具、会话、沙箱、调度器、
上下文库、项目管理、钩子扩展、安全、插件、前端、渲染器、预设、日志、
存储、错误处理——均为槽位。

### 2.4 一次任务的数据流

用户输入一行「帮我读取 readme.md 并总结」，一次 `np()` 启动的引擎内部发生：

```
前端线程 input() 拿到文本
  → engine.submit(text)                  （第 6 章）
  → loop.submit(fn)                      （第 4 章：循环系统，可替换）
  → AgentRuntime.run(text)               （内核循环，可替换：agent_runtime 槽位）
      → L3 prepare_input                （before_input / after_input 钩子）
      → L4 create_session / append_message
      → L5 build_messages               （系统提示词 + 历史合并）
      → L6 before_step
      → L7 call_model                   （模型 = model 槽位解析出的提供者）
      → L8 execute_tool_call            （工具 = tools 槽位，沙箱 = sandbox 槽位）
      → （多轮直到模型给出最终答案）
      → L9 finalize_result
  ← RunResult（final_content / status / usage ...）
事件总线全程广播（on_task_start / on_content / after_tool_call ...）
  → ui 渲染器（UIAdapter）订阅事件，把流渲染给用户
```

每一个带 L 编号的环节都是一层钩子（第 9 章），每一个零件都是一个槽位（第 3 章）。

### 2.5 模块地图：每个文件做什么

| 文件 | 职责 |
|---|---|
| `norpagent/__init__.py` | 模块即入口：`np()` / `np.stop()` / `np.nasyncio()` |
| `norpagent/arch/slots.py` | 18 个内置架构槽位的规格定义表 + 槽位表热插拔注册中心（register_slot / unregister_slot / replace=True 规格热替换） |
| `norpagent/arch/address.py` | 地址函数解析器（字符串 → 模块/对象） |
| `norpagent/arch/layer.py` | ArchLayer：槽位连接与工厂调用 |
| `norpagent/loops/base.py` | LoopRuntime 协议（事件循环契约） |
| `norpagent/loops/nasyncio.py` | 默认循环实现：NasyncioLoopRuntime（自研 nasyncio 核心适配器，零 asyncio 依赖） |
| `norpagent/loops/std_asyncio.py` | 0.7 旧模块名的兼容垫片（重导出 StdLoopRuntime，不 import asyncio） |
| `norpagent/loops/__init__.py` | `norpagent.nasyncio()` 架构函数 |
| `norpagent/nasyncio.py` | 自研异步 IO 核心（原 nasync_io，已打包进库）：事件循环 / Future / Task / 同步原语 / 子进程，不依赖标准 asyncio |
| `norpagent/frontends/base.py` | Frontend 协议（前端契约） |
| `norpagent/frontends/web.py` | 默认前端：Web（HTTP + SSE，页面 = front.html） |
| `norpagent/frontends/console.py` | 控制台前端：命令行 REPL（显式指定） |
| `norpagent/frontends/headless.py` | 无头前端：纯 API，输出打印 stdout |
| `norpagent/runtime/mount.py` | 槽位实现 → 注册表装配（默认逻辑登记） |
| `norpagent/runtime/engine.py` | NorpEngine：生命周期状态机 + 线程编排 |
| `norpagent/runtime/__init__.py` | launch / stop / current / submit / shutdown |
| `norpagent/kernel/agent.py` | AgentRuntime：Agent 循环本体（可替换） |
| `norpagent/kernel/registry.py` | 注册表（底层最小内核之一） |
| `norpagent/kernel/events.py` | 事件总线（底层最小内核之一） |
| `norpagent/kernel/presets.py` | Preset 声明式配置 |
| `norpagent/protocols/*` | 全部组件协议（接口契约） |
| `norpagent/hooks/*` | 9 层 29 钩子体系 |
| `norpagent/security/*` | 安全系统（norpagent.safe()） |
| `norpagent/plugins/*` | 插件系统（签名/审计/隔离） |
| `norpagent/builtin/*` | 内置组件（同样走注册表，可被任意替换） |
| `norpagent/modes/*` | 四种预设模式 |
| `norpagent/flows/` | FLOW 流程编排内核（注册表快照 / 文件即模块 / 拓扑执行） |
| `norpagent/cli.py` | `norpagent` 命令行入口 |

---

### 2.6 模块化约定：分层依赖与扩展点

#### 2.6.1 依赖方向：单向下行

全部模块的自底向上依赖关系（上层可以 import 下层，下层不得
反向依赖上层）：

| 层 | 模块 | 允许依赖 |
|---|---|---|
| L0 契约 | `protocols/*` | 仅其它 protocol（零框架依赖） |
| L0 核心 | `nasyncio.py` | 仅标准库（自研异步核心，自包含） |
| L0 最小内核 | `kernel/events.py`、`kernel/registry.py` | 标准库 + protocols（registry 引用 Plugin / Tool 协议） |
| L1 钩子 | `hooks/*` | 仅 `kernel.events`（总线结构化视图） |
| L1 安全 | `security/*` | 零框架依赖（纯决策模块 + 可选 cryptography） |
| L2 插件 | `plugins/*` | `protocols` + `security/*` + `hooks.core`（管线层） |
| L2 内核循环 | `kernel/agent.py` | `hooks.core` + `kernel/*` + `loops.cancel` + `protocols/*` |
| L2 内置 | `builtin/*` | `protocols/*`（+ 沙箱用 `loops.cancel`） |
| L2 模式 | `modes/*` | 仅 `kernel.presets` |
| L3 架构 | `arch/*` | 仅自身（address / slots / layer） |
| L3 循环 | `loops/*` | `arch` + `nasyncio` |
| L3 前端 | `frontends/*` | 仅 `frontends.base` |
| L4 装配 | `runtime/*` | arch + builtin + kernel + modes（唯一「知道一切」的装配点） |
| L4 入口 | `__init__.py` / `cli.py` / `__main__.py` | 全部 |

关键规则：

- **内核可裁剪**：`kernel` 模块级不 import `security` /
  `plugins` / `builtin`——安全以 `registry.security` 注入，
  防护扫描在任务级参数显式要求时才惰性 import guard；插件以
  `register_plugin` 注入。嵌入式场景（14.2）正是靠这条规则把
  sqlite3 / http.server 等全部挡在内核之外；
- **内置组件也是普通实现者**：`builtin/*` 只依赖 protocols，
  与第三方组件地位完全平等，都经注册表按名解析；
- **协议与实现分离**：组件之间只通过 `protocols/*` 的接口契约
  对话（模型 / 工具 / 会话 / 沙箱 / 调度器 / UI / 插件），
  任何实现满足协议即可接入；
- `runtime.mount` 是唯一的默认装配点：预设（modes）+ 内置
  组件（builtin）在这里按槽位表（arch/slots）组装成注册表；
- `safe.py` 与 `security/*` 一样处于低层：安全系统可以脱离
  框架单独测试 / 单独使用，内核只通过注入点感知它（第 10 章）。

#### 2.6.2 四类扩展点

按侵入性从低到高，**全部无需修改框架核心代码**：

| 扩展点 | 方式 | 章节 |
|---|---|---|
| 事件订阅 | `reg.hooks.*.subscribe` / `reg.bus.subscribe` | 第 9 章 |
| 组件替换 | 槽位地址（模型 / 工具 / 会话 / 沙箱 / 前端 / 循环…） | 第 3 章 |
| 通用组件 | `register_component(kind, name, factory)` + 预设 components | 2.6.3 |
| 全新槽位 | `register_slot(SlotSpec(...))` | 3.8 |
| 外部插件 | 独立文件 / manifest 包，安全管线加载 | 第 11 章 |
| 安全策略 | `safe()` 运行态策略 + 独立 API | 第 10 章 |
| 循环 / 执行结构 | 方法覆写或 agent_runtime 槽位 | 9.6 |

「框架核心代码零修改」是设计红线：所有扩展走槽位 / 钩子 /
注册表，这也是 2.3 节「最小内核只有四样」的推论——最小内核
之外的一切都有既定替换通道。

#### 2.6.3 通用组件命名空间

除 model / tool / session / sandbox / scheduler / ui 六个专用
命名空间外，Registry 提供开放的通用组件命名空间：

```python
reg.register_component("context_store", "my_store", lambda: MyStore())
reg.register_component("my_kind", "my_impl", factory)   # 种类本身开放

# 预设里声明引用
Preset(name="mine", ..., components={"context_store": "my_store",
                                     "my_kind": "my_impl"})

reg.build_component("my_kind", "my_impl")               # 构建（工厂调用）
reg.build_component("context_store", "my_store",
                    workspace_root=path)                # 按签名注入工作区根
reg.list_components()                                   # 列出全部种类
```

上下文存储 / 项目管理 / 任务存储等一切「附加能力」都走这里，
框架无需改内核即可扩展新的组件种类；工厂声明了
`workspace_root` 参数（或 **kwargs）时自动注入工作区根。

#### 2.6.4 新增组件模块的标准流程（五步）

以「新增一个会话实现」为例：

1. **写协议**（如无）：在 `protocols/` 定义接口契约；
2. **写实现**：新建模块，只依赖 protocols（参照
   builtin/sessions/ 的写法）；
3. **注册**：`reg.register_session("redis", factory)` 或由
   runtime.mount / 自己的装配代码登记；
4. **声明使用**：预设里 `session="redis"`，或启动时
   `np(session="redis")` / 地址字符串
   `np(session="myapp.redis:create")`；
5. **接钩子**（可选）：在实现内经 registry 发布 / 订阅事件。

等价的手工装配路径：`Registry() + register_* +
AgentRuntime(...)`（17.1 节），与 np() 装配完全同构——np()
只是把这五步自动化。

---

## 第 3 章　架构层与地址函数

替换任意组件的做法：为对应槽位填入新地址，不修改框架核心代码。

### 3.1 槽位（Slot）是什么

一个 Agent 应用的每个组成部分都是一个槽位（装配点）：

```python
from norpagent.arch.slots import SLOT_SPECS, all_slot_names

print(all_slot_names())
# ['async_loop', 'agent_runtime', 'model', 'tools', 'session', 'sandbox',
#  'scheduler', 'context_store', 'project_manager', 'hooks', 'security',
#  'plugins', 'frontend', 'ui', 'preset', 'logger', 'storage', 'error_handler']
```

共 **18 个内置槽位**（框架结构契约，受保护不可注销 / 覆盖规格）。
槽位表本身可热插拔：第三方可运行时 `register_slot()` 注册**自定义
槽位**（名字 / 语义 / 装配逻辑完全自定义），注册即接入完整管线——
详见 3.8 节。每个槽位都有规格说明（SlotSpec）：名称、职责、协议、
默认实现、字符串语义、工厂参数约定：

```python
from norpagent.arch.slots import get_slot

print(get_slot("async_loop").format_help())
# [async_loop] 事件循环系统：Agent 运行所在的异步调度核心。等价于架构函数 norpagent.nasyncio()。
#   协议: LoopRuntime 协议（norpagent.loops.base.LoopRuntime）：...
#   默认: norpagent.loops.nasyncio:NasyncioLoopRuntime
#   字符串语义: address
#   工厂参数 layer: 所在架构层
#   工厂参数 config: 该槽位的附加配置 dict
#   示例: np(async_loop='norpagent.loops.nasyncio:NasyncioLoopRuntime')
```

### 3.2 地址函数：不填 = 默认，填了 = 接入

槽位取值有四种形态：

| 形态 | 写法 | 语义 |
|---|---|---|
| 不填（None） | `np()` | 使用库内置默认逻辑 |
| 字符串地址 | `np(async_loop="pkg.mod:attr")` | 按地址加载文件，把实现接上去 |
| 可调用对象 | `np(async_loop=MyLoop)` | 工厂/类直接接入 |
| 实例/值 | `np(async_loop=loop_instance)` | 现成对象直接接入 |

字符串地址的解析规则（`norpagent.arch.address.resolve_address`）：

1. `"pkg.mod"` —— 加载该**文件（模块）**。优先取模块内约定的工厂属性
   `create` → `build` → `default`；都没有就把**整个模块**作为实现接上去；
2. `"pkg.mod:attr"` —— 加载该文件，取模块内的具名属性；
3. `"pkg.mod:attr;键=值;键=值"` —— 分号后附加配置，注入工厂的 `config` 参数
   （例如 `"norpagent.frontends.web:WebFrontend;port=9000"`）。地址解析前会先剥离
   分号子句，子配置不干扰模块路径解析。

地址字符串指向模块文件：架构层加载该文件（或文件内的对象）并接入槽位。

槽位挂载参数示例（frontend 槽位的 html 子句）：

```python
# 用自定义 HTML 文件替换 / 路由默认页面（不物理覆盖库文件）
np(frontend="norpagent.frontends.web:WebFrontend;html=/path/to/my.html")
```

### 3.3 字符串语义

有些槽位的字符串值不是地址而是「注册表组件名」。每个槽位在 SlotSpec
里声明了自己的字符串语义：

| 语义 | 含义 | 槽位 |
|---|---|---|
| `address` | 字符串 = 模块地址 | async_loop, agent_runtime, frontend, context_store, project_manager |
| `name` | 字符串 = 注册表组件名 | tools（容器语义，见下） |
| `name_or_address` | 先按名、再按地址 | model, session, sandbox, scheduler, ui, preset |
| `literal` | 字符串 = 字面值，地址优先 | security(级别), storage(路径), hooks, plugins, logger, error_handler |

其中：`np(model="mock")` 中的 `"mock"` 是注册表里的模型名；
`np(model="myapp.model:create")` 中的字符串是地址。
`np(session="sqlite")` 引用内置 SQLite 会话组件；
`np(session="myapp.sessions:create")` 按地址加载自定义会话实现。

**v0.9.1：全部槽位支持按地址加载 + 键值对的值支持纯地址解析**

1. `name` / `name_or_address` 槽位：字符串先查注册表名，查不到再按
   模块地址（`pkg.mod[:attr]`）加载——ui / preset 已从纯 `name`
   升级为 `name_or_address`：`np(ui="myapp.render:create")`、
   `np(preset="myapp.presets:build")` 直接按地址接上实现；
2. `literal` 槽位「地址优先」：字符串**形如纯地址**（含 `.` 或 `:`
   的点分标识符，结构判定见 `norpagent.arch.address.is_address_like`）
   即按地址加载（解析失败抛 `AddressError`，不静默回落）；其余保持
   字面值——`np(security="high")` 仍是级别、`np(storage="./data")`
   仍是路径，`np(security="myapp.sec:build_kit")` /
   `np(storage="myapp.store:create;root=./x")` 按地址加载；
3. **键值对的值支持纯地址解析**：任何槽位的 dict 形态值统一处理
   （tools 映射 / hooks 映射 / 自定义槽位 dict 值，嵌套 dict 递归）：
   值若是纯地址字符串就按地址解析为对象（解析失败抛 `AddressError`）
   ——`tools={"my_tool": "myapp.tools:create"}`、
   `hooks={"before_model_call": "myapp.guard:fn"}`。解析出的
   callable 按工厂约定调用（注入 layer / slot / config 上下文，
   `;key=value` 子句解析为工厂 config）；**hooks 槽位除外**——其
   值是「回调本身」，地址指向的回调函数原样保留不调用；
4. tools 槽位容器：列表元素与单个字符串同样支持地址——
   `tools=["myapp.tools:create"]`、`tools="myapp.tools:create;tag=x"`；
   其余字符串元素仍为已注册工具名引用（如 `tools=["echo"]`）。

### 3.4 工厂调用约定：签名裁剪注入

地址解析出一个**可调用对象**（类或函数）时，ArchLayer 按
`norpagent.arch.layer.call_factory` 的约定调用它：

1. 检查工厂签名；
2. 把工厂**声明了的**上下文键注入（`layer` / `slot` / `config` /
   以及槽位专属键，见附录 A）；
3. 工厂不声明的键**自动忽略**（包括完全不接受上下文的工厂，无参调用）；
4. 不可调用的对象（模块 / 实例 / 值）**原样使用**，不做任何调用。

以下三种写法等价：

```python
# 1. 类：不声明上下文 → 无参实例化
class MyLoop:
    def __init__(self):
        ...

np(async_loop=MyLoop)

# 2. 工厂函数：声明 config → 自动注入
def create(config=None, **kw):
    return MyLoop(timeout=float((config or {}).get("timeout", 0)))

np(async_loop=create)

# 3. 字符串地址 + 附加配置子句
np(async_loop="myapp.loop:create;timeout=5")
```

### 3.5 ArchLayer：装配清单可观测

每一次 `np()` 内部都会构建一个 ArchLayer 并 `connect()`。
装配结果可观测：

```python
eng = np.current()
print(eng.layer.describe())
```

输出示例：

```
== NorpAgent 架构层装配清单 ==
  async_loop       <- 默认逻辑                         => NasyncioLoopRuntime
  agent_runtime    <- 默认逻辑                         => type
  model            <- 默认逻辑                         => (未连接)
  ...
  frontend         <- 地址 'norpagent.frontends.headless:HeadlessFrontend' => HeadlessFrontend
  preset           <- 地址 'minimal'                 => str
```

`(未连接)` 表示该槽位未指定，由预设声明的默认逻辑处理。

### 3.6 示例：替换多个槽位

```python
import norpagent as np

# 模型为 openai_compat，会话为 sqlite，沙箱为 pooled，前端为 Web（端口 9000），
# 循环为自定义实现，安全级别 high。全部通过槽位参数指定。
np(
    preset="standard",
    model="openai_compat",              # 名称引用
    session="sqlite",                   # 名称引用
    sandbox="pooled",                   # 名称引用
    frontend="norpagent.frontends.web:WebFrontend;port=9000",
    async_loop="myapp.nasync_loop:create",
    security="high",
)

while True:
    if np.stop():
        break
```

### 3.7 运行中热挂载：任何槽位均可替换

`np()` 启动后**引擎保持运行**，随时替换任意槽位实现，无需重启：

```python
import norpagent as np

np()                                        # 启动（默认 Web 前端）
# ... 应用运行中 ...

np.remount(model="openai_compat")           # 换模型：下一次 run 生效
np.remount(tools=["echo", "get_time"])      # 换工具集：下一次 run 生效
np.remount(session="sqlite")                # 换会话存储：AgentRuntime 热重建
np.remount(security="high")                 # 换安全级别：旧防护钩子先退订
np.remount(frontend="norpagent.frontends.console:ConsoleFrontend")
np.remount(async_loop="myapp.loop:create")  # 换事件循环：停旧启新
np.remount(model="myapp.model:create")      # 运行中替换模块文件（热重载）
```

底层链路：`np.remount()` → `engine.remount()` → `ArchLayer.remount()`。
字符串地址在重新解析前会**失效模块缓存与 .pyc 字节码缓存**，
因此「修改模块文件 → remount」即可在运行中换上改动后的代码，
无需重启进程。

替换语义按槽位分组：

| 分组 | 槽位 | 生效方式 |
|---|---|---|
| 组件槽位 | model / tools / hooks / security / plugins | 重挂到注册表并重写最终预设；model / tools 下一次 run() 生效（Agent 循环每次 run 重新解析模型与工具 schema）；重复挂载的架构级订阅先退订再重挂，不叠加重复触发 |
| 装配槽位 | session / sandbox / scheduler / ui / agent_runtime / preset / context_store / project_manager | AgentRuntime 热重建：停旧运行时（释放沙箱/组件/退订渲染器）→ 按当前装配建新运行时 → 前端重绑渲染器（HTTP 端口不变）（在途任务竞态与排水建议见本节后文） |
| 基础设施槽位 | frontend / async_loop | 停旧实现、启新实现；新实现启动失败自动回滚旧实现。async_loop 替换时旧循环上在途任务会被放弃，建议无任务时替换 |
| 基础服务槽位 | logger / storage / error_handler | 直接更新引擎引用，立即生效 |

**装配槽位热重建的在途任务竞态（Drain 说明）**：装配槽位的 remount
走「停旧 → 建新 → 前端重绑」三步，**这三步都不等待工作池中在途的
`agent.run` 任务**。若热重建恰逢任务执行中：

1. `old.shutdown()` 已关闭旧沙箱，在途 run 的下一次工具调用会打到
   已关闭的沙箱上（失败或未定义行为）；
2. 旧渲染器退订到新渲染器订阅之间存在窗口期，在途 run 的事件丢失；
   重绑后新数据源会收到**旧 run 的残余事件**，与新 run 的事件交错；
3. 在途 run 的结果仍会返回给其 submit 调用方——一个来自「已死亡
   运行时」的结果；
4. 会话槽位不变时新旧 run 写同一个 session store（历史连续）；沙箱 /
   调度器 / ui 槽位换掉后，在途 run 引用的旧实例已被 close。

**建议（生产环境）：两阶段热挂载（drain）**——① 先阻断新任务入队
（业务侧自维护 drain 标志，`submit` 前检查）；② `loop.interrupt()`
置位全部在途任务取消事件，并给工作池一个 join 宽限期（如 2s）；
③ 超时未退的任务随沙箱关闭语义兜底（PTC / pooled 沙箱强杀进程树）；
④ 再执行装配槽位 remount。`interrupt()` 基础设施已存在
（`engine.request_stop` 第一步即调用），实现成本低。管理面操作
（改配置 / 换组件）建议放在任务空闲时执行；**框架当前版本未内置
drain**，属业务侧职责。

#### 3.7.2 热挂载前端页面（html / flow_html 参数）

`frontend` 是基础设施槽位，替换语义为「停旧启新」。配合 WebFrontend
的 `html` / `flow_html` 挂载参数，可在运行中换掉 `/` 路由页面或
`/flow` 模块流程页面——不用重启进程，刷新浏览器即见新页面：

```python
import norpagent as np

np(html="front.html")                       # 启动并挂载自定义主页面
# ... 修改 front.html 或换别的页面文件 ...
np.remount(frontend="norpagent.frontends.web:WebFrontend;html=front.html")
# 端口不变，浏览器刷新（或重开 http://127.0.0.1:8787/）即为新页面

np.remount(frontend="norpagent.frontends.web:WebFrontend;flow_html=flow.html")
# 换 /flow 模块流程编排页面（norp-flow.html 的官方挂载途径）
```

参数优先级：remount **显式给出**的键覆盖启动参数，**未显式给出**的键
（如 port）沿用启动参数——所以只换页面时浏览器 URL 不变。显式键的
判定：字符串地址取分句 `;key=value` 中的键；实例取构造参数中与默认值
不同的键（html / flow_html 以 `_html` / `_flow_html` 属性判定）。例如：

```python
np.remount(frontend="norpagent.frontends.web:WebFrontend;port=9000")  # 换端口（重启 HTTP 监听）
np.remount(frontend="norpagent.frontends.web:WebFrontend;html=")      # 重置主页面为库内置
np.remount(frontend="norpagent.frontends.web:WebFrontend;flow_html=") # 重置 /flow 为库内置
from norpagent.frontends.web import WebFrontend
np.remount(frontend=WebFrontend(html="front.html"))                   # 实例形式
np.remount(frontend=WebFrontend(flow_html="flow.html"))               # 实例形式
np.remount(frontend="front.html")     # HTML 路径直挂：等价于 WebFrontend;html=front.html
```

**remount 页面热替换键（v0.9，更简单的换页入口）**：`html` /
`flow_html` 本身不是槽位，而是 frontend 槽位的挂载参数——
`np.remount()` 直接接收这两个键，**不经过「停旧前端 / 启新前端」**，
经 `mount_page` 立即换页（HTTP 服务不重启、端口不变，刷新浏览器
即见新页面）：

```python
np.remount(flow_html="flow-v2.html")       # /flow 立即换页（HTTP 不重启）
np.remount(html="front-v2.html")           # / 主页面立即换页
np.remount(flow_html="<html>...</html>")   # HTML 内容直传（"<" 开头视为内容）
np.remount(flow_html=None)                 # 卸载挂载，回落库内置 norp-flow.html
np.remount(flow_html="", html="")          # "" 与 None 同语义（卸载）
np.remount(flow_html="flow-v2.html",
           frontend="norpagent.frontends.web:WebFrontend")  # 可组合：先落参数再换前端
```

语义细节：

1. 值先写入 `engine.params`（与 `np(html=...)` 启动透传同一条数据
   通路），后续 frontend 热挂载 / attach 沿用新值；
2. 当前前端是 Web 前端时经 `mount_page` 立即换页；非 Web 前端
   （console / headless）只更新参数、无副作用；
3. 坏路径**预校验**快速失败（`ValueError`），槽位变更与页面都不留
   半途状态；
4. 若用户用 `register_slot()` 注册了同名自定义槽位，槽位表优先
   （按槽位语义处理）；
5. `remount(port=...)` / `remount(host=...)` 等网络参数仍不是页面键，
   会报错并提示用地址子句形式（会重启 HTTP 监听）。

**frontend 槽位两种挂载方式（v0.9，等价共存）**：

1. 地址式：`np(frontend="norpagent.frontends.web:WebFrontend;html=...")`
   —— 模块地址 + 分句参数；
2. HTML 路径直挂：`np(frontend="front.html")` —— 槽位值本身是
   `.html/.htm` 文件路径（不含 `;` 子句）时，架构层不再按模块地址
   解析，装配器自动转换为 `WebFrontend(html=<该路径>)`。文件不存在
   抛 `ValueError` 快速失败（不静默回落默认前端）。

注意：HTML 路径直挂只作用于 `/` 主页面；换 `/flow` 页面请用
`;flow_html=...` 子句或 `WebFrontend(flow_html=...)`。

**运行中直接换页面（HTTP 服务不重启，端口不变）**：

```python
# 方式一：remount 页面热替换键（推荐，v0.9）
np.remount(flow_html="flow.html")           # /flow 立即换页
np.remount(html="front.html")               # / 主页面立即换页
np.remount(flow_html=None)                  # 卸载挂载，回落库内置

# 方式二：frontend 实例 API
eng.frontend.mount_page("flow", "flow.html")   # /flow 立即换页
eng.frontend.mount_page("flow", None)          # 卸载挂载，回落库内置
eng.frontend.mount_page("front", "<html>...</html>")  # 同理作用于 /
# 等价入口：WebUI.mount_page(page, html)
```

**物理替换库内 HTML 文件自动生效**：页面字节缓存按资源文件的
mtime/size 签名校验——直接覆盖
`norpagent/builtin/ui/assets/front.html` 或 `norp-flow.html`
后刷新浏览器即为新页面（无需 remount、无需重启）；命中缓存时
仅一次 stat 校验，无 open+read 磁盘 I/O。

注意事项：`np.remount()` 是**进程内 API**，需在启动了引擎的同一
Python 进程里调用（跨进程不生效）。cmd 中运行时，把 remount 放在
生命周期循环里、或起一个线程读 stdin 即可实现「敲命令换页面」。

注意事项：

1. 只允许在引擎 RUNNING 状态调用，否则抛 `EngineError`；槽位名非法同样抛错；
2. `remount(slot=None)` 清空该槽位配置（回落默认逻辑）；
3. 预设对象同一性：热挂载后 `registry.resolve_preset(name)` 与
   `engine.agent.preset` 仍是同一实例（前端对 preset.tools 的热改写依赖此约定）；
4. `agent_runtime` 是 `defer_factory` 槽位：工厂推迟到引擎装配期调用
   （registry / preset 上下文就绪后），地址子句 `;key=value` 经
   `ArchLayer.subconfig()` 注入工厂的 config。

### 3.8 槽位表热插拔：注册自定义槽位

`np.remount()` 换的是**槽位的实现**；槽位表本身（`SLOT_SPECS`）也
可热插拔——第三方库运行时注册**全新的自定义槽位**，注册即接入
完整管线（`np()` 参数校验、ArchLayer 装配、`np.remount()` 热替换、
`layer.describe()` 清单），无需修改框架源码、无需重启进程：

```python
from norpagent.arch import SlotSpec, register_slot, unregister_slot

# 自定义槽位 = 名字 + 字符串语义 + 应用逻辑（applier）
register_slot(SlotSpec(
    name="audit_tag",                # 槽位名 = np() 的关键字参数名
    description="审计标签",
    protocol="literal 字符串",
    string_semantics="literal",      # address / name / name_or_address / literal
    applier=_apply_audit_tag,        # 槽位值非空时由装配器调用
))
```

```python
import norpagent as np

np(audit_tag="release-1")            # 装配期即应用
np.remount(audit_tag="release-2")    # 运行中热替换（applier 重新执行）
```

`applier(reg, layer, value, params, ctx)` 的契约：

- `value` 是解析后的槽位值：`address` 语义为已实例化实现（子配置
  `;key=value` 经 `layer.subconfig(slot)` 取得），`name` /
  `name_or_address` / `literal` 语义为原值；
- `ctx` 提供四个可变容器：`components`（最终预设组件声明
  {kind: name}）、`extras`（引擎附加对象，`engine.extras[槽位名]`
  消费）、`overrides`（预设字段覆盖）、`meta`（注册表架构元数据，
  记录挂上去的可退订对象）；
- **同一注册表可能重复调用**（装配 + 每次 `np.remount`），applier
  必须重入安全：重复执行不叠加副作用——订阅事件总线的对象先按
  `ctx["meta"]` 的记录退订再重挂（参考内置 hooks / security /
  plugins 槽位的做法）；
- `remount_rebuild_agent=True`：热替换后**热重建 AgentRuntime**
  （applier 向预设 `components` 登记通用组件的「装配型」槽位应置
  True）；默认 False：下一次 run() 生效或仅更新 extras。

完整示例——注册一个通用组件型自定义槽位（与内置 context_store
同一条装配通道）：

```python
from norpagent.arch import SlotSpec, register_slot


def apply_vector_store(reg, layer, value, params, ctx):
    name = "_arch_vector"
    factory = value if callable(value) else (lambda v=value: v)
    reg.register_component("vector_store", name, factory)
    ctx["components"]["vector_store"] = name   # 预设声明组件
    ctx["extras"]["vector_store"] = value


register_slot(SlotSpec(
    name="vector_store",
    description="向量检索组件（自定义装配槽位）",
    protocol="任意实现（注册为 vector_store 通用组件）",
    string_semantics="literal",
    applier=apply_vector_store,
    remount_rebuild_agent=True,     # 热替换后热重建，组件立即生效
))

np(vector_store=MyVectorStore())    # 装配：engine.agent.components["vector_store"]
np.remount(vector_store=Other())    # 热替换：AgentRuntime 热重建
```

保护与校验规则：

| 规则 | 说明 |
|---|---|
| 内置 18 槽位受保护 | 不可注册 / 覆盖规格 / 注销（框架结构契约：引擎、前端、文档引用）。它们的**值**随时可 `np.remount` 热替换 |
| 槽位名合法性 | 合法 Python 标识符（`np()` 关键字参数），不能是关键字，不能是 `prompt` / `config`（launch 特殊键） |
| 重复名 | 抛 `SlotError`；`register_slot(spec, replace=True)` 热替换同名自定义槽位的规格（默认地址 / 语义 / applier / 重建标志） |
| 非法规格 | 非 callable applier、非法 `string_semantics` 抛 `SlotError`；失败的 replace 不破坏旧规格 |
| 注销 | `unregister_slot(name)` 注销自定义槽位并返回规格；此后 `np.remount(该槽位)` 报未知槽位，`np(该键=...)` 回落为任务参数；已装配实现保持原状 |
| 晚注册 | 引擎启动后注册的槽位：`layer.connect()` 幂等补齐（只连接缺失槽位），或直接 `np.remount(槽位=值)` 走全管线 |

顶层 API：`np.register_slot` / `np.unregister_slot` /
`np.SlotSpec` / `np.SLOT_SPECS` / `np.is_builtin_slot` /
`np.snapshot_slots`；槽位表操作线程安全（RLock 保护，装配 / 热挂载
按快照迭代）。

---

### 3.9 任务级槽位注入：submit(slot_overrides=...)

`np()` 启动装配与 `np.remount()` 热挂载都是**全局**维度：换一次
影响所有后续任务。任务级槽位注入是第三个维度——**单次任务**执行
期间临时覆盖任意槽位实现，不影响全局配置、不阻塞其他在途任务：

```python
import norpagent as np

engine = np(preset="standard")

# 单任务：临时换模型 + 换工具
r = engine.submit(
    "分析这段代码",
    slot_overrides={
        "model": "anthropic",
        "tools": ["run_python", "file_read", "echo"],
        "sandbox": "isolated_python",
        "max_steps": 64,          # 非槽位键：自动回落为任务参数
    },
)
```

#### 3.9.1 语法与键全集

`engine.submit(text, session_id=None, task_params=None, slot_overrides=None)`
（顶层 `np.submit(...)` 同样支持）。`slot_overrides` 的键与 `np()` 的
槽位参数完全一致（14 个可任务级覆盖的键）：

| 键 | 任务级语义 | 生效时机 |
|---|---|---|
| `model` | 换本次任务的模型提供者（已注册名 / 地址 / 实例） | 本次 run 的模型调用 |
| `tools` | 换本次任务的工具集（已注册名列表 / 地址 / Tool 实例 / {名: 实例} 映射） | 本次 run 的 schema 与工具执行 |
| `sandbox` | 独立临时沙箱（已注册名 / 地址 / 实例），任务结束即 close | 本次 run 的工具执行 |
| `session` | 独立临时会话存储（已注册名 / 地址 / 实例 / `{"name": ..., "persist": True}`），默认不污染全局会话表 | 本次 run 的 L4 会话 |
| `scheduler` | 独立临时调度器（已注册名 / 地址 / 实例），任务结束即 close | 本次 run 的子任务提交 |
| `hooks` | 任务期钩子订阅（{钩子名: 回调} 或 callable(registry)），任务结束退订 | 本次 run 全生命周期 |
| `security` | 任务期安全策略（级别 / dict / SecurityContext / callable），任务结束恢复原策略 | 本次 run 的审批 / 防护 |
| `agent_runtime` | 为本次任务启动独立 Runtime 实例，执行完即销毁（不影响引擎默认 Runtime） | 本次任务 |
| `context_store` / `project_manager` | 临时通用组件（已注册组件名 / 地址 / 实例），任务结束即 close | 本次 run 的 ctx.components |
| `async_loop` | 本次任务在独立临时事件循环上执行（不与主循环争抢工作池） | 本次任务 |
| `logger` / `storage` / `error_handler` | 注入本次任务的参数上下文（params），供组件工厂与钩子读取（见 3.9.5） | 本次 run |

值形态与 `np()` 槽位完全一致：已注册名引用 / 模块地址（`pkg.mod[:attr]`，
含 `;key=value` 子句）/ 工厂 / 实例，解析失败抛 `AddressError`。

**非槽位键自动回落为任务参数**：`slot_overrides` 里的键不在上述
14 键内时（如 `max_steps` / `task_timeout` / `mock_script`），自动并入
`task_params` 透传给 Agent 循环——与 `np()` 的「槽位键拆分、其余
透传参数」同一条数据通路，因此 `slot_overrides={"max_steps": 64}`
这样的写法开箱即用。

**不可任务级覆盖的键**：`frontend` / `ui` / `plugins` / `preset` 是
进程级或引擎级结构（输入输出外壳、渲染器、插件加载器、组件组合
基线），不属于单次任务的覆盖边界，传入会回落为任务参数（不会报错，
但也不产生槽位覆盖效果——请改用 `np.remount`）。

#### 3.9.2 优先级：任务级 > remount > 启动装配 > 预设

| 层级 | 来源 | 优先级 |
|---|---|---|
| 1 | `submit(slot_overrides=...)` | 最高 |
| 2 | `np.remount(slot=...)` | 次高 |
| 3 | 启动时 `np(slot=...)` | 次低 |
| 4 | 预设 Preset 声明 | 最低 |

任务级覆盖在 **submit() 时刻拍快照**，后续的全局 `np.remount` 不会
影响在途任务（3.9.3）。这与「Drain + remount」形成互补：不想等
排水就覆盖，不想覆盖就排水后再 remount。

#### 3.9.3 实现边界与隔离语义

实现边界（不改内核循环，只加一层解析）：

1. `AgentRuntime.run()` 入口接收 `slot_overrides` 字典；
2. 为本次任务创建槽位快照层（`_TaskSlotLayer`），存储在
   `TaskContext`（`ctx.task_slot_layer` / `ctx.slot_overrides`）中；
3. `run()` 生命周期内所有组件解析路径（model / tools / sandbox /
   session / scheduler / context_store / project_manager）**优先查
   任务层**，未覆盖时回落运行时默认；
4. 任务结束后任务层随 `RunResult` 一起释放（`finally` 兜底）：
   临时沙箱 / 会话 / 调度器 / 组件全部 `close()`，任务期钩子订阅
   退订，临时安全策略卸载并恢复原 `registry.security`——不留残余
   状态。

**与全局 remount 的关系（隔离语义）**：

| 场景 | 全局槽位 | 任务覆盖 | 该任务看到的 | 其他任务看到的 |
|---|---|---|---|---|
| 无覆盖 | model=A | — | model=A | model=A |
| 覆盖 model | model=A | model=B | model=B | model=A |
| 覆盖后热重建 | model=A | model=B | model=B（该任务仍然用自己的快照） | model=C（新任务） |
| 覆盖后取消 | model=A | 任务结束 | — | model=A |

关键语义：任务级覆盖在 submit() 时刻拍了一张槽位快照，后续全局
remount 不影响在途任务。

**agent_runtime 覆盖**：为本次任务启动一个独立的 Runtime 实例
（按与 `_build_agent` 相同的工厂调用约定构造：registry / preset /
ui / task_params / layer / config 按签名裁剪注入），执行完即销毁，
引擎的默认 Runtime 不受影响。注意：子运行时与默认运行时共享同一
事件总线与渲染器，任务事件可能被两者各自订阅的渲染器各渲染一次
（与 `run_task` 子 Agent 行为一致，属共享总线的既有特性）。

**session 覆盖**：本次任务的对话历史写入独立临时会话存储，不污染
全局会话表——除非显式 `persist=True`：

```python
# 临时会话：全局会话表不出现该会话
engine.submit("hi", slot_overrides={"session": "memory"}, session_id="s1")

# 持久化：任务结束后把本次历史回写全局会话表
engine.submit("hi", slot_overrides={
    "session": {"name": "memory", "persist": True},
}, session_id="s2")
```

注意：落盘型会话（如 `sqlite`）构建的是独立实例，但若未指定独立
库文件（地址式 `...;path=...`），记录仍写入默认库文件——需要完全
隔离请用地址式或实例值。

#### 3.9.4 与多智能体编排的关系

把多智能体理解为「多个任务独立配置不同角色」，任务级槽位注入就是
最干净的实现——三个任务并发，各自用不同的模型 + 工具，互不干扰：

```python
import threading

futures = []
for key, ov in [
    ("撰写方案", {"model": "claude", "tools": ["write"]}),
    ("代码审查", {"model": "deepseek", "tools": ["read"]}),
    ("执行部署", {"model": "mock", "tools": ["exec_cmd"]}),
]:
    t = threading.Thread(
        target=lambda k=k, o=ov: engine.submit(k, slot_overrides=o),
    )
    t.start()
    futures.append(t)
for t in futures:
    t.join()
```

每个任务在 submit 时刻独立拍快照：并发任务各自的 model / tools /
session 覆盖互不影响，也互不影响全局配置。

#### 3.9.5 并发与边界注意事项

- **零注册表污染**：任务级 model / 工具实例由快照层直接持有，不
  `register_*` 到注册表；任务结束引用即释放。并发任务同名工具各自
  持有自己的实例，互不覆盖。
- **hooks / security 是任务期临时状态**：任务开始订阅 / 安装，任务
  结束（含异常路径）退订 / 恢复。任务级 security 的 callable 形式
  由调用方自行管理 `registry.security`（框架无法追踪其内部行为）；
  字符串 / dict / SecurityContext 形式自动恢复。
- **logger / storage / error_handler 的语义是参数注入**：这三个键
  的任务级覆盖注入 `params`（组件工厂 ctx、钩子 payload 可见），
  **不替换引擎全局引用**——避免并发任务互踩引擎级对象。
- **并发覆盖安全**：快照层是每任务独立实例，线程安全；同一运行时
  上任意数量的并发任务各自带自己的槽位快照，互不影响。
- **提交失败语义**：任务级覆盖的解析（地址加载等）发生在 run()
  入口（工作线程内），解析失败抛 `AddressError` / `ComponentError`
  由 submit 阻塞返回处冒泡（与普通任务异常一致）。

---

## 第 4 章　事件循环系统：norpagent.nasyncio()

### 4.1 循环系统独立架构函数

事件循环决定任务调度方式：任务运行的线程、中断方式、唤醒方式。
NorpAgent 将循环系统提供为独立架构函数：

```python
import norpagent as np

loop = np.nasyncio()                       # 默认循环（自研 nasyncio 核心）
loop = np.nasyncio("myapp.loop:create")    # 自定义循环
```

它与槽位等价：

```python
np(async_loop="myapp.loop:create")   # 等价于 np.nasyncio("myapp.loop:create")
```

`np.nasyncio()` 的返回值是一个 **LoopRuntime**（协议见下）。
默认实现运行的调度核心是库内置的**自研 nasyncio 事件循环**
（`norpagent.nasyncio`，原 nasync_io，已打包进库）——**不依赖、
不 import 标准 asyncio**（声明与原因见 4.7）。如需使用其他事件
循环实现，实现 LoopRuntime 协议并为 `async_loop` 槽位填入地址
即可，无需修改框架核心代码。

> 顶层 `norpagent.nasyncio`（即 `np.nasyncio`）绑定的是自研核心
> **模块**（可调用）：`np.nasyncio()` 返回 LoopRuntime 默认实现；
> `np.nasyncio.EventLoop` / `Future` / `Task` 直接访问核心类型；
> `import norpagent.nasyncio` 得到同一个核心模块。架构函数本体在
> `norpagent.loops.nasyncio`。

### 4.2 LoopRuntime 协议

```python
class LoopRuntime(Protocol):
    name: str
    def start(self) -> None: ...          # 启动循环（通常内部专用线程 run_forever）
    def stop(self) -> None: ...           # 请求停止（线程安全）
    def is_running(self) -> bool: ...     # 是否仍在运行
    def join(self, timeout=None) -> None: ...   # 等待循环线程退出
    def submit(self, fn, *args, **kwargs) -> Any: ...
        # 在循环上下文中执行同步函数 fn 并阻塞返回其结果
```

引擎（`norpagent.runtime.engine.NorpEngine`）只通过这个协议与循环交互，
不 import 任何具体循环实现。

### 4.3 默认实现：NasyncioLoopRuntime（自研 nasyncio 核心）

默认实现基于库内置的**自研 nasyncio 事件循环**（不依赖标准
asyncio）：独立线程跑 `norpagent.nasyncio.EventLoop`（run_forever），
`submit()` 把同步函数交给**自有守护工作池**执行并等待结果（不用
标准线程池的原因见 4.6 与 4.7）。

配置项（嵌入式 / 高并发调参，详见第 14 章）：

| 配置 | 说明 | 默认 |
|---|---|---|
| `max_workers` | 守护工作池线程数 | `max(4, cpu_count)` |
| `poll_interval` | submit/run_async 完成轮询间隔（秒） | `0.05` |

经 `np(config={"loop": {"max_workers": 8}})` 传入（或环境变量
`NORPAGENT_MAX_WORKERS` / `NORPAGENT_SUBMIT_POLL`；等价写法
`np.nasyncio(max_workers=8)` 与 `np(async_loop="norpagent.loops.nasyncio:NasyncioLoopRuntime")`
构造时同源）。

```python
loop = np.nasyncio()
loop.start()
result = loop.submit(lambda: 1 + 1)   # → 2
loop.stop()
loop.join()
```

`run_async(coro)` 可选能力把协程经自研核心的
`run_coroutine_threadsafe` 提交到循环线程执行并阻塞返回结果
（引擎默认走 submit()，供自定义协程入口使用）。

### 4.4 自定义循环示例

```python
# myapp/simple_loop.py —— 循环实现示例（同步直跑，可用于测试或嵌入式场景）
class SimpleLoop:
    name = "simple"

    def __init__(self, **kw):
        self._running = False

    def start(self):
        self._running = True

    def stop(self):
        self._running = False

    def is_running(self):
        return self._running

    def join(self, timeout=None):
        pass

    def submit(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)   # 同步直接执行


def create(**kw):                    # 模块级工厂（地址 "myapp.simple_loop" 自动命中）
    return SimpleLoop(**kw)
```

接入：

```python
import norpagent as np

np(async_loop="myapp.simple_loop", prompt="hi",
   frontend="norpagent.frontends.headless:HeadlessFrontend")
while True:
    if np.stop():
        break
```

### 4.5 跨线程桥接注意事项（自研核心已内置修复）

**事项一（自研核心已修复）**：标准 asyncio 的 `Future.result()` 不是
线程安全的阻塞等待，跨线程 `add_done_callback()` 在 future 已完成时
走 `loop.call_soon`（只塞 `_ready` 队列不写自管道），循环阻塞在
selector 上就收不到唤醒、等待方挂起。库内置的自研核心
（`norpagent.nasyncio.Future`）把这两个坑都修掉了：完成通知在任意
线程发生都会走 `call_soon_threadsafe`（写自管道立即唤醒循环）。
默认运行时的 `submit()` 仍采用「执行线程写结果 +
threading.Event 置位」模式——submit 任务可能长时间阻塞，放在守护
工作池里与循环彻底解耦更稳（与是否 asyncio 无关）。

**事项二（run_async 的跨线程等待）**：`NasyncioLoopRuntime.run_async`
经自研核心的 `run_coroutine_threadsafe` 把协程提交到循环线程
（内部 call_soon_threadsafe + 自管道唤醒，无唤醒竞态），等待用
concurrent.futures.Future 的 done 回调 + 轮询 Event 置位（回调由
concurrent.futures 保证在结果写入线程内同步触发）。注意
`run_async` 不能在循环线程内调用（阻塞等待会卡死循环），此时
请直接 `await` 协程或改用 `submit()`。

规则：跨线程协调使用「执行线程写结果 + threading.Event 置位」，
循环线程不作为唤醒路径上的必要环节。

### 4.6 Ctrl+C 与任务取消语义

#### 4.6.1 问题：主线程不在事件循环入口里，Ctrl+C 为什么可能失灵

`np()` 启动后主线程只做生命周期轮询（`np.stop()`），真正的工作线程
在后台执行任务，调用方（如控制台 REPL 的主线程）阻塞在
`loop.submit()` 的等待上。两条信号链路上的坑：

1. **Windows 上一次性 `Event.wait()` 收不到 Ctrl+C**：SIGINT 以
   pending interrupt 的形式投递到主线程，只在**字节码边界**被检查；
   主线程阻塞在 `Event.wait()`（底层 `WaitForSingleObject` 无限等待）
   时永远不会回到字节码边界，Ctrl+C 形同虚设。
   **解法**：`NasyncioLoopRuntime.submit()` 改为**轮询等待**（每
   ≤`poll_interval` 秒经过一次字节码边界，默认 0.05s——0.9 起由
   0.2s 收紧，任务完成感知延迟更低；嵌入式可调大），Ctrl+C 即刻
   以 `KeyboardInterrupt` 冒出。
2. **卡在阻塞 I/O 里的工作线程杀不死，进程僵住**：SIGINT 只到主线程，
   工作线程若卡在沙箱 `subprocess` / HTTP 请求里，只能等它自己超时；
   更糟的是标准线程池（ThreadPoolExecutor，asyncio 的默认执行器
   也是如此）的工作线程被 CPython 登记在 `threading._threads_queues`，
   解释器退出时会被**强制 join**——任务不结束进程就退不出去。
   **解法**：工作池用裸守护线程（退出不 join）；同时把「取消」信号
   显式传给任务执行体（见 4.6.2），让它尽早自行退出。

#### 4.6.2 取消信号：contextvars + 取消事件

`NasyncioLoopRuntime.submit()` 给每个任务包进一个带取消事件的
contextvars 上下文（`norpagent.loops.cancel`），执行体内随时可查：

```python
from norpagent.loops.cancel import cancel_requested, current_cancel_event

def my_tool(args, ctx):
    for chunk in fetch_stream(...):      # 长任务 / 长流式读取
        if cancel_requested():           # Ctrl+C / 引擎停止 → True
            return ToolResult(output="任务已取消", success=False)
```

触发路径（置位取消事件）：

| 触发 | 时机 | 行为 |
|---|---|---|
| `KeyboardInterrupt` | submit 等待方收到 Ctrl+C | 置位本任务取消事件并冒泡异常 |
| `loop.interrupt()` | `engine.request_stop()` 第一步 | 置位全部在途任务取消事件 |
| `loop.stop()` | 停止循环 | 同上（内部调用 interrupt） |

内置组件对取消的响应（0.7.0 起）：

- **PTC 沙箱**（isolated_python）：执行循环每 ≤0.5s 检查一次，
  取消时立即强杀子进程并返回 `exit_code=-1`（stderr 注明「任务已取消」）；
- **池化沙箱**（pooled）：`run_shell` 分片等待（每 ≤0.5s 检查），
  取消时杀进程树并标记实例损坏；
- **模型调用**：`params["_cancel_event"]` 在 call_timeout=0 时同样注入
  （原先只有超时路径有），openai_compat 流式循环每 chunk 检查一次；
- **Agent 主循环**：每轮步骤边界检查 `cancel_requested()`，
  以 `stopped`（on_task_stopped）收尾。

取消事件仅「建议」执行体退出；若任务卡在不可中断的系统调用里
（如 DNS 解析、D 状态进程），最终兜底仍是各组件的自身超时
（SDK 连接超时 / call_timeout / ptc_timeout），进程退出则由
守护线程保证不被阻塞。**注意：守护工作池本身没有任务执行时间预算
与池级看门狗**，任务卡死会占满工作池——边界与兜底矩阵见 4.6.4。

#### 4.6.3 线程边界说明：什么进循环、什么不进

`norpagent.nasyncio()` 是 **async_loop 槽位的架构函数**；其默认实现
（NasyncioLoopRuntime）运行库内置的自研 nasyncio 事件循环。全库所有
任务调度（engine.submit → loop.submit）都经它走协议。剩下的裸线程
是**刻意的阻塞 I/O 泵**，不应进事件循环：

| 线程 | 职责 | 为什么不用循环线程 |
|---|---|---|
| `norpagent-loop-pool-*` | 执行 submit 的同步任务 | 任务本体可能阻塞（沙箱/HTTP），放循环线程会卡死整个循环 |
| `norpagent-nasync-loop` | 跑自研 nasyncio 事件循环 | 循环本体 |
| 沙箱管道 reader（PTC/pooled/插件宿主） | 读取子进程 stdout/stderr | 阻塞管道读取，不可中断 |
| `norpagent-webui` / 请求线程 | HTTP 服务与请求 | socketserver 自身的线程模型 |
| `norpagent-model-*` | call_timeout 硬中断看守 | 限时 join，超时即弃 |

规则：**计算与调度进循环（可替换），阻塞 I/O 用守护线程泵
（不可取消但也不阻塞退出）**。替换 `async_loop` 槽位即可整体换掉
循环系统（协议见 4.2），无需改动框架其他部分。

#### 4.6.4 守护工作池的队列语义与卡死兜底（边界如实说明）

`NasyncioLoopRuntime` 的守护工作池（`_DaemonPool`）语义边界：

- **队列无界、无拒绝策略**：内部是 `queue.Queue()`（无 maxsize），
  `submit_nowait` 用 `put_nowait` 入队——永不阻塞、永不抛 `Full`。
  池打满时新任务**无限堆积**在队列里，调用方的 `submit()` 在
  `done.wait(poll_interval)` 轮询中无限等待，延迟无上限（不快速失败）；
- **取消是协作式的**：取消事件只在执行体主动检查的边界生效（4.6.2），
  两类场景打断不了——① 卡在 C 扩展纯计算里（如 `re` 正则回溯），
  取消事件只在字节码边界可见；② 自定义工具直接 `Popen + communicate`
  （非沙箱路径）没有分片检查与杀进程树，卡住即卡住；
- **单任务卡死 → 吞吐归零**：每个 worker 一次只跑一个任务，无任务
  执行时间预算、无线程弃置、无看门狗；若某任务卡死占满工作池，后续
  submit 全部堆积，业务侧吞吐趋近于 0——没有额外线程垫底。

**现有兜底先例（「时间预算 + 弃置线程」模式）**：

| 场景 | 兜底机制 |
|---|---|
| 模型调用 | `_call_model_with_timeout`：worker 线程 + `join(timeout)`，超时弃置为孤儿线程（daemon，每 run 过滤回收） |
| 引擎停止 | `request_stop`：关闭任务 `t.join(timeout=5.0)`，超时在当前线程 `_close()` 兜底 |
| PTC / pooled 沙箱 | 执行循环每 ≤0.5s 分片检查取消事件，超时 / 取消强杀进程树 |
| 工作池任务（裸工具 / 用户任务） | **无**——无超时预算，卡死即占池 |

**演进建议（roadmap，当前未实现）**：池级引入「任务执行时间预算」——
worker 内设 deadline 看门狗，超时置位取消事件 + 标记任务弃置 + 强杀
关联沙箱进程（复用模型调用超时的弃置线程模式）；池级可选改为有界队列
或 submit 超时快速失败（拒绝新任务而非无限堆积）。嵌入式场景建议先
把任务拆小 + 收紧组件自身超时（call_timeout / ptc_timeout），不依赖
池级兜底。

### 4.7 明确声明：norpagent 不依赖标准 asyncio，调度核心拥抱自研 nasyncio

**声明**：0.8 起 norpagent 库内**零 `import asyncio`**。默认事件
循环核心是打包进库的自研异步 IO 库 `norpagent.nasyncio`（原
nasync_io，v2.0.0），底层只依赖 Python 标准库的**非 asyncio**
模块：`threading` / `queue` / `heapq` / `selectors` / `socket` /
`concurrent.futures` / `subprocess` / `os` / `time`。全库可用
`grep -R "import asyncio" norpagent/` 验证为空。

**为什么要拥抱自研 nasyncio、摆脱标准 asyncio**：

1. **调度、取消、跨线程唤醒语义完全由库内代码定义（掌控力）**。
   标准 asyncio 存在公认的语义坑，例如：
   - `Task.cancel()` 非线程安全（直接操作循环线程的 ready 队列）；
   - 跨线程对已完成 Future `add_done_callback()` 走 `call_soon`，
     不写自管道，循环阻塞在 selector 上收不到唤醒，等待方挂起；
   - 没有对外的「取消主任务」入口，外部线程无法强制中断正在
     await 的协程（停止延迟取决于当前操作，可达数分钟）。
   自研核心逐项修复：`Task.cancel()` 跨线程安全；Future 完成通知
   自动走 `call_soon_threadsafe` 写自管道；`EventLoop.abort_main()`
   提供线程安全的即时停止。
2. **依赖面压缩到可审计**。事件循环的全部行为（trampoline 调度、
   定时器堆、socketpair 自管道唤醒、取消穿透）都是库内自己写的
   代码，审计面 = 自研核心一个文件；不引入标准 asyncio 的
   内部实现细节与版本差异（各 Python 版本 selector 行为不一）。
3. **退出语义可控**。不用标准线程池执行 submit：ThreadPoolExecutor
   （含 asyncio 默认执行器）的工作线程被 CPython 登记在
   `threading._threads_queues`，解释器退出时被强制 join——任务卡在
   沙箱 subprocess / HTTP 里进程就僵住。默认运行时用裸守护线程池
   + 自管道唤醒，Ctrl+C 后进程即刻收尾（4.6.1）。
4. **API 语义对齐、迁移零成本**。自研核心提供与 asyncio 用法一一
   对应的同名 API（`EventLoop` / `Future` / `Task` / `Event` /
   `Lock` / `Condition` / `sleep` / `wait_for` / 子进程封装 /
   `run_coroutine_threadsafe`），熟悉 asyncio 的代码把
   `import asyncio` 换成 `import norpagent.nasyncio` 即可移植，
   且 `CancelledError` / `TimeoutError` 语义保持一致。
5. **自研核心独立可用**。`norpagent.nasyncio` 本身是一个可独立
   使用的微型异步库（原 nasync_io 打包进库），可以脱离
   norpagent 框架单独 import、单独跑循环；框架只是通过
   LoopRuntime 协议把它接进 `async_loop` 槽位。

**兼容性**：0.7 旧地址 `norpagent.loops.std_asyncio:StdLoopRuntime`
保留为兼容垫片（重导出同一实现，不 import asyncio），历史代码
不失效；新代码使用 `norpagent.loops.nasyncio:NasyncioLoopRuntime`
（见 4.3）。

```python
import norpagent as np
import norpagent.nasyncio as core  # 自研核心模块（可调用）

print(core.__version__)          # 2.0.0
loop_rt = np.nasyncio()          # LoopRuntime 默认实现（同 core()）
print(loop_rt.name)              # nasyncio
print(core.EventLoop)            # 自研事件循环类
```

---

## 第 5 章　前端体系

### 5.1 两层结构：frontend（外壳）与 ui（渲染器）

前端体系分两层：

| 层 | 槽位 | 协议 | 职责 |
|---|---|---|---|
| 外壳 | `frontend` | `Frontend` | 从哪里读输入、何时启动/停止、把输入交给引擎 |
| 渲染 | `ui` | `UIAdapter` | 订阅事件总线，把 Agent 事件渲染成文本/界面 |

一个 frontend 通常会携带一个 ui 渲染器；两者都可以被独立替换。

### 5.2 Frontend 协议

```python
class Frontend(Protocol):
    frontend_id: str
    def attach(self, engine) -> None: ...   # 绑定引擎（engine.submit / engine.request_stop）
    def start(self) -> None: ...            # 启动（内部自建后台线程，不得阻塞）
    def stop(self) -> None: ...             # 停止（线程安全）
    def is_alive(self) -> bool: ...         # 存活查询
```

### 5.3 内置前端

| 前端 | 地址 | 说明 |
|---|---|---|
| Web（默认） | `norpagent.frontends.web:WebFrontend` | HTTP + SSE，无第三方依赖；页面 = front.html（多标签会话/流式渲染/设置/插件面板），独立入口 `/flow` = norp-flow.html 模块流程编排；控制台打印 `listening on http://127.0.0.1:8787/`；可配 `;port=9000`、`;html=自定义主页`、`;flow_html=自定义流程页`（槽位挂载参数，见 5.4）或 `np(port=9000, language="zh_CN")`；frontend 槽位值可直接给 `.html` 路径（HTML 路径直挂，v0.9） |
| 控制台 REPL | `norpagent.frontends.console:ConsoleFrontend` | 显式指定；`/exit`（或 `exit`/`quit`/`exit()`）退出，`/reset` 新会话；在 Python 交互式解释器中自动切换同步模式 |
| 无头 | `norpagent.frontends.headless:HeadlessFrontend` | 纯 API；`prompt` 模式默认；输出（正文/工具/结果）打印到 stdout |

### 5.4 Web UI 行为与配置持久化

Web UI 的行为与配置项：

| 能力 | 说明 |
|---|---|
| 配置持久化 | 设置面板保存后落盘 `~/.norpagent/webui_config.json`（`NORPAGENT_WEBUI_CONFIG` 可覆盖；`WebUI(config_path=...)` 可指定，传 `None` 关闭）。磁盘加载只接受 `DEFAULT_CONFIG` 白名单键，未知键丢弃。0.9 起**磁盘加载延迟到 `start()`**：构造 WebUI 不再触发任何磁盘 I/O（嵌入式 / 只读根文件系统友好），显式构造参数 > 磁盘值 > 默认值的优先级不变 |
| 页面防缓存 | 页面响应带 `Cache-Control: no-store`，浏览器每次刷新获取最新 front.html；服务端页面字节读入内存缓存（0.9：每次 GET / 不再读盘），缓存按资源文件 mtime/size 签名校验——**物理替换库内 HTML 文件后刷新即自动生效**，命中缓存时仅一次 stat 校验 |
| 页面挂载（html / flow_html 参数） | `/` 路由默认页面与 `/flow` 模块流程页面都可整体替换：`html` / `flow_html` 接收**文件路径**或 **HTML 内容**（strip 后以 `<` 开头视为内容，否则视为文件路径）；文件不存在时构造抛 `ValueError`（快速失败，不静默回落）。无需物理覆盖 `norpagent/builtin/ui/assets/front.html` / `norp-flow.html`。运行中可用 `mount_page(page, html)` 直接换页（HTTP 服务不重启、端口不变），`mount_page(page, None)` 卸载回落 |
| 断连处理 | 客户端断连（WinError 10053 / EPIPE 等）静默处理，内部错误记录 DEBUG 日志；SSE 连接断开 ≤1s 内被非阻塞探测回收（0.9，防线程堆积） |
| SSE 背压（0.9） | 每连接有界事件缓冲 + 帧批量写出，慢客户端自动降级，超高并发下内存有上界——详见第 14.3 节 |
| 端口顺延 | 绑定失败（含 Windows 10013 监听占用）时向后顺延最多 10 个端口，以实际端口为准 |
| 请求体防护 | 负数 Content-Length 按无请求体处理；超过 1MB 拒收 |
| 关闭幂等 | `shutdown()` 幂等 + 同线程死锁防护，可跨线程调用；`block_on_close=False` 停机不等连接关闭（0.9） |
| 事件路由 | 事件 sid 解析优先 `submit()` 登记的原始浏览器会话 id；会话管理器支持指定 id 创建（`create_session(title=..., session_id=...)`），内核续接会话时 id 与浏览器标签页一致 |

```python
import norpagent as np
from norpagent.builtin.ui.web import WebUI
from norpagent.frontends.web import WebFrontend

# 自定义配置持久化位置（默认 ~/.norpagent/webui_config.json）
ui = WebUI(port=9000, config_path="./my_app/webui.json")
# config_path=None 关闭磁盘读写
ui2 = WebUI(port=9000, config_path=None)
```

**页面挂载（html / flow_html 参数）四种写法等价：**

```python
import norpagent as np

# 1. 槽位地址子句（;key=value，推荐）
np(frontend="norpagent.frontends.web:WebFrontend;html=/path/to/my.html")
np(frontend="norpagent.frontends.web:WebFrontend;flow_html=/path/to/flow.html")

# 2. 构造函数直接传（WebFrontend / WebUI 均支持）
np(frontend=WebFrontend(html="<html><body>我的界面</body></html>"))
np(frontend=WebFrontend(flow_html="/path/to/flow.html"))

# 3. 配置字典
np(config={"web": {"html": "/path/to/my.html", "flow_html": "/path/to/flow.html"}})

# 4. 运行时参数透传
np(html="/path/to/my.html", flow_html="/path/to/flow.html")

# 5. HTML 路径直挂（v0.9）：frontend 槽位值本身就是 .html 路径，
#    等价于写法 1 的 html= 子句
np(frontend="/path/to/my.html")

# 文件路径不存在时构造报错（快速失败，不静默回落默认页面）
# ValueError: WebUI html 挂载参数既不是 HTML 内容（以 '<' 开头）也不是存在的文件: ...
```

**运行中热替换页面（HTTP 服务不重启、端口不变）：**

```python
eng = np()                                  # 或 np.current() 取运行中的引擎

# 方式一：remount 页面热替换键（推荐，v0.9）
np.remount(flow_html="/path/to/flow.html")  # /flow 立即换页
np.remount(html="/path/to/front.html")      # / 主页面立即换页
np.remount(flow_html=None)                  # 卸载，回落库内置

# 方式二：frontend 实例 API
eng.frontend.mount_page("flow", "/path/to/flow.html")  # /flow 立即换页
eng.frontend.mount_page("flow", None)                  # 卸载，回落库内置
eng.frontend.mount_page("front", "<html>...</html>")   # / 路由同理
# 等价底层 API：WebUI.mount_page(page, html)
```

**模块流程官方前端 norp-flow.html**：`/flow` 独立入口（拖拽模块 /
beam 连线 / 后端真实执行 / 自动保存），随库发行于
`norpagent/builtin/ui/assets/norp-flow.html`；不挂载时即为官方页面，
挂载 `flow_html` 时整体替换。直接物理替换该文件同样自动生效
（见上「页面防缓存」）。

**仓库根目录 `front.html`（多宿主前端）**：pywebview 桌面协议的前端
已改造为多宿主传输桥架构——浏览器宿主下自动构造
`window.pywebview.api`（fetch + SSE 实现全部方法，并把库事件翻译为
文本事件协议 T:/R:/C:/U:/E:/Q:），桌面宿主原样兼容。可直接挂载：

```python
np(html="front.html")   # 相对工作目录，库按文件路径读取
```

挂载后聊天 / 会话 / 设置 / 插件面板 / 文件浏览全部走库的 REST API
（契约见 `norpagent/builtin/ui/web.py` 的 do_GET / do_POST）；SSH 远程
与移动端远程控制在库版已剥离，对应 UI 入口自动隐藏、桥方法占位降级。

输入框选择器（第一直觉设计）：

- **模式**：`/api/presets` 列出注册表全部预设（minimal / standard /
  ptc / creative / longrun / embedded），选择后经 config `preset_name` 热切换
  （`engine.remount(preset=...)`，AgentRuntime 热重建；任务运行中禁用，
  `*_arch` 衍生预设不对外展示）；
- **模型**：从 `api_base` 拉取远端模型列表（`/api/models`），选择即
  保存 `model` 并立即生效，附「从 URL 拉取 / 模型设置」入口；
- **推理强度**：点击循环切换 关 / 低 / 中 / 高，即时保存。

调试面板（设置 → Agent 调试）条目化展示版本 / 前端 / 预设 / 模型 /
工具 / 插件 / 会话等字段，原始 JSON 折叠在「原始数据」区。

### 5.5 自定义前端示例

```python
# myapp/tray_frontend.py —— 前端实现示例
import threading

class TrayFrontend:
    """托盘式前端：不读键盘，通过方法调用提交输入。"""

    frontend_id = "tray"

    def __init__(self, **kw):
        self.engine = None
        self.alive = False

    def attach(self, engine):
        self.engine = engine
        self.alive = True

    def start(self):
        self.alive = True

    def stop(self):
        self.alive = False

    def is_alive(self):
        return self.alive

    # 前端自定义能力：应用代码从这里把用户输入交给引擎
    def send(self, text):
        return self.engine.submit(text)
```

接入并驱动：

```python
import norpagent as np

np(frontend="myapp.tray_frontend:TrayFrontend", preset="standard")
fe = np.current().frontend
result = fe.send("你好")        # 引擎在后台循环里执行 Agent
print(result.final_content)
np.shutdown()
```

### 5.6 UIAdapter 渲染层

```python
class UIAdapter(Protocol):
    ui_id: str
    def on_event(self, event) -> None: ...            # 渲染一个 AgentEvent
    def ask_user(self, question, default="") -> str: ...  # 人工审批/澄清问答
    def notify(self, message, level="info") -> None: ...
```

换渲染器：`np(ui=MyRenderer())` 或 `np(ui="web")`（引用注册表已注册名）。

### 5.7 模块流程画布（FLOW）与 FE 前端模块

`/flow` 是独立前端分类「模块流程」：把 Agent 组装过程画成一张画布
（模块 = 方块，端口 = 注册钩子，beam 连线 = 执行链路），RUN 时图被
提交给后端用注册表真实组件拓扑执行。画布自动保存到
`~/.norpagent/flow_graph.json`，刷新 / 重启自动恢复；顶栏
「应用到智能体」开启后，front 聊天任务改按该流程执行（行为热切换）。
详见 `docs/flow.md`。

**FE 前端模块（文件即前端）**：把 `.html / .js / .ts` 文件拖入画布
即注册为前端模块（模块坞「前端 FE」分组），后端托管到
`/fe/<name>`（卡片「↗」新标签页打开）。每个 FE 拥有**独立配置
作用域**（互不干扰，默认值取自「连接设置」全局配置），配置经
`GET/POST /api/fe/config?fe_id=...` 读写，落盘到
`~/.norpagent/fe_configs/<fe_id>.json`。

FE 节点有 **1/2/3 三种形态**（卡片标题栏按钮切换）：

| 形态 | 含义 |
|---|---|
| 1 | 全局设置节点：配置写入「连接设置」（scope=global） |
| 2 | FE 即设置集合：独立配置作用域（scope=fe，默认形态） |
| 3 | 拆散成设置项子卡片：每个配置项一个成员行（可单独拖出 / 就近连线） |

**输入框体系（一切需要输入的地方都是输入框）**：

- FE / 全局设置节点卡片把每个配置项（api_key / api_base / model /
  project_root / plugin_dirs / temperature / max_tokens / max_steps /
  task_timeout / system_prompt / language）渲染成一行
  「IN 端口 · 标签 · 输入框 · OUT 端口」，值直接写在卡片上，
  改完 500ms 防抖自动保存；形态 3 的每个成员行同样是输入框；
- model / tool / sandbox / other 等节点卡片底部带**值输入框带**
  （context / query / code / value 直接编辑）；TR 卡 prompt 输入框、
  PATH 卡路径输入框保持内嵌；
- 模型字段一律是**可手输输入框 + datalist 提示**（flow 连接设置弹窗、
  WebUI 设置弹窗、model 节点实例字段）：远端模型列表拉取失败或
  模型不在列表里时，直接键入任意模型名即可（留空 = 引擎默认）；
- 卡片输入框与右侧节点面板双向同步；beam 连线到设置端口时连线动作
  本身立即生效（写入独立 / 全局配置）。

**画布管理三件套（0.6.8 新增，防画布乱局）**：

- `Alt+左键拖拽` 空白 = **框选**，与框相交的模块全部高亮，`Del`
  批量删除；
- `Ctrl+A` = **全选**，`Del` 批量删除；
- 顶栏 **「清空画布」** = 一键删除全部模块与 beam（确认弹窗
  防误触），确认后立即自动保存；清空后刷新保持空白（后端保存
  空图，加载不再回落示例模板）；
- **误触注入已移除**：双击画布空白注入、单击坞卡片快速注入两个
  入口彻底删除——此前误触产生的 other 节点会被自动保存固化，导致
  「一打开画布就冒出一大堆 other」。现在注入只有拖拽一条途径。

**DeepSeek 模型名**：`deepseek-chat` / `deepseek-reasoner` 已于
2026-07-24 被官方停用，现役为 `deepseek-v4-flash` / `deepseek-v4-pro`。
后端 `list_models` 缓存与 flow 快照（`filter_remote_models`）以及
前端提示列表 / 远端模型坞都会过滤停用名（`RETIRED_REMOTE_MODELS` /
`RETIRED_MODELS` / `RETIRED_REMOTE`），历史缓存不会再把旧名展示出来。

**连接设置弹窗**（flow 顶栏）：立即显示、不被远端模型拉取阻塞；
「拉取模型列表」用表单当前 Key/Base 即时请求（无需先保存）；点击
弹窗外部不关闭（Esc / × / 取消关闭）；输入框失焦自动保存应用。

**智能体工具挂载（0.6.10 新增，模块工具 → front 自动调用）**：

- **配置键**（`DEFAULT_CONFIG`）：`agent_tools`（显式工具全集列表）
  + `agent_tools_explicit`（bool，True = 显式；False = 跟随预设默认集）；
- **写入入口**：① `/flow` 模块坞工具卡的 `AGENT` 徽章（前端调用
  `POST /api/agent/tools {tools, explicit}`）；② WebUI 设置弹窗
  「🧰 智能体工具」勾选清单（经 `save_config` 落 `agent_tools`）。
  `GET /api/config` 返回 `tools_info`（全部工具 + 原生/模块来源）与
  `agent_effective_tools` / `agent_base_tools`，前端据此渲染；
- **热应用**：`WebFrontend._apply_agent_tools()` 把 `preset.tools`
  改写为显式集合（未注册工具名自动过滤）或预设默认集快照
  （`WebFrontend._base_tools`，attach 时捕获）；下一次 `run()` 的
  `registry.tool_schemas(preset.tools)` 即包含模块工具，模型按
  OpenAI function schema 自动调用；
- **重启恢复**：`WebFrontend.attach()` 结束时用已保存配置重新应用
  工具集（不改变既有模型配置行为）；
- **回落语义**：`set_agent_tools` 在显式集合与预设默认集一致时自动
  回落为非显式（`agent_tools=[]`），预设演进自动跟随；
- **快照字段**：`/api/flow/snapshot` 顶层返回 `agent_tools` /
  `agent_base_tools`，驱动 flow 页徽章状态。

---

## 第 6 章　np() 启动与生命周期

### 6.1 启动代码解读

```python
import norpagent as np
np()                    # ①
running = True
while running:
    if np.stop() == True:   # ②
        running = False
```

① `np()` —— `norpagent` 模块是可调用的（模块类替换技术）。它等价于
`norpagent.launch()`，内部依次完成：

1. **参数分拣**：关键字参数中与槽位表（18 个内置槽位 + 运行时
   register_slot 注册的自定义槽位）同名的键 → 槽位值；
   其余键 → 任务参数（如 `max_steps` / `task_timeout` / `workspace_root`）；
   特殊键 `prompt`（单次任务文本）与 `config`（字典形式的槽位赋值）；
2. **架构层装配**：`ArchLayer(config, **slots)` → `mount_defaults()`
   （登记各槽位的库内置默认逻辑）→ `layer.connect()`（解析地址、
   调用工厂，得到每个槽位的实现）；
3. **注册表装配**：`build_registry(layer)` 安装内置组件与预设，
   再把槽位覆盖写进最终预设；
4. **引擎启动**：`NorpEngine(layer, registry, preset, loop, frontend, ...)`
   → `engine.start()`：装配 Agent 运行时 → 前端绑定 → 循环线程启动 →
   前端线程启动 → 状态进入 RUNNING；
5. **单例语义**：已有运行中的引擎时，再次 `np()` 直接返回当前引擎。

② `np.stop()` —— 生命周期函数。返回 `True` 表示引擎进入 STOPPED
状态（应用已结束，主循环应退出）；没有引擎时也返回 `True`。

### 6.2 引擎生命周期状态机

```
STARTING ──start()──▶ RUNNING ──request_stop()──▶ STOPPING ──▶ STOPPED
```

| 状态 | 含义 | 进入条件 |
|---|---|---|
| STARTING | 装配中 | `np()` 内部 |
| RUNNING | 接受输入、执行任务 | `engine.start()` 完成 |
| STOPPING | 正在收尾 | `request_stop()` |
| STOPPED | 已结束 | 收尾完成（`np.stop()` 为 True） |

停止请求的收尾顺序（`NorpEngine.request_stop`）：

1. 前端停止（输入循环退出）；
2. Agent 关闭（释放沙箱/组件，广播 `on_agent_shutdown`——对应 L1 生命周期钩子）；
3. 退订引擎额外订阅的渲染器；
4. 停止循环线程并等待退出；
5. 状态置 STOPPED。

### 6.3 三种运行模式

**主循环模式**（默认 Web 前端）：

```python
np()                            # 默认前端 = Web（frontend web listening on 127.0.0.1:8787）
while True:
    if np.stop():
        break
```

浏览器访问打印的地址打开聊天界面（front.html）。使用其他前端时显式指定：
`np(frontend="norpagent.frontends.console:ConsoleFrontend")`。

**单次任务模式**（`prompt` 给出时自动使用 headless 前端，输出打印到 stdout）：

```python
np(prompt="总结 README", preset="standard")
while True:
    if np.stop():
        break
print(np.current().last_result.final_content)
```

**纯 API 模式**（无头 + 程序主动 submit）：

```python
np(preset="minimal", frontend="norpagent.frontends.headless:HeadlessFrontend")
eng = np.current()
result1 = eng.submit("第一个问题")
result2 = eng.submit("追问", session_id=result1.session_id)   # 同一会话续聊
eng.request_stop()
```

> **注意**：`np()` 不阻塞，引擎在后台线程运行。主线程应当用
> `np.stop()` 轮询等待（或调用 `np.current().wait()`）。主线程若直接
> 结束进程，daemon 引擎线程随之退出；库已注册 atexit 兜底清理。
>
> **特例**：显式使用**控制台前端**时，在 Python 交互式解释器
> （`>>>` REPL）里调用 `np()` 自动切换为**同步模式**——`np()` 阻塞到
> 用户退出（`/exit`、`exit()`、Ctrl+C 或 EOF），期间无需再写轮询循环。
> 同步模式下主线程独占 stdin。默认 Web 前端在 REPL 中同样适用（后台服务 +
> 页面交互，不阻塞解释器）。

### 6.4 np() 参数全集

```python
np(
    # ── 架构槽位（18 个内置，不填 = 默认逻辑；register_slot 注册的
    #    自定义槽位同样在此传参，见 3.8）──
    async_loop=..., agent_runtime=..., model=..., tools=...,
    session=..., sandbox=..., scheduler=..., context_store=...,
    project_manager=..., hooks=..., security=..., plugins=...,
    frontend=..., ui=..., preset=..., logger=..., storage=...,
    error_handler=...,
    # ── 特殊键 ──
    prompt="单次任务文本",          # 跑完自动停止
    config={"slot": value, ...},    # 字典形式的槽位赋值
    # ── 模型快捷参数（model 为内置适配器名时生效）──
    model_name="deepseek-v4-flash", # 远端模型名
    base_url="https://api.deepseek.com/v1",   # 远端服务地址
    api_key="sk-...",               # API Key（也读环境变量）
    # ── Web 前端运行时参数（frontend=web 时透传给 WebFrontend）──
    port=8787,                      # HTTP 端口（被占自动顺延 10 个）
    host="127.0.0.1",               # 监听地址
    open_browser=False,             # 是否自动打开浏览器
    language="zh_CN",               # 界面语言（en / zh_CN）
    html="/path/to/my.html",        # 自定义主页面：文件路径或 HTML 内容
                                    # （替换 / 路由默认页面，见 5.4 节）
    sse_queue_size=1024,            # SSE 每连接缓冲上限（0=不限，0.9）
    sse_queue_policy="drop_oldest", # drop_oldest / drop_newest / unlimited
    # ── 其余键 = 任务参数，透传 Agent 循环 ──
    max_steps=32, task_timeout=0, call_timeout=0,
    workspace_root=..., system_prompt=...,
)
```

`config` 字典的子键约定（0.9，嵌入式 / 高并发调参，详见第 14 章）：

```python
np(config={
    "loop": {"max_workers": 8, "poll_interval": 0.02},   # 循环工作池与轮询
    "web": {"port": 9000, "sse_queue_size": 2048,        # Web UI 与 SSE 背压
            "sse_queue_policy": "drop_oldest"},
    "preset": "embedded",                                 # 槽位赋值同关键字
})
```

> `preset="embedded"` 且未显式指定 frontend 时，默认前端自动为
> headless（不启动 HTTP 服务，纯 API 模式），见 12.1 与第 14 章。

### 6.5 生命周期与 L1 钩子的对应

引擎状态机与 9 层钩子体系的 L1 层对齐：

| 引擎事件 | 钩子/事件 |
|---|---|
| Agent 运行时构造完成 | `on_agent_init`（L1） |
| 任务提交 | `on_task_start` |
| 引擎停止 | `on_agent_shutdown`（L1） |

生命周期订阅写法：`np(hooks={"on_agent_init": fn, ...})`
（钩子体系见第 9 章）。

---

## 第 7 章　模型与工具

### 7.1 模型槽位

模型槽位接受：

```python
np(model="mock")                  # 注册表名（内置 mock / openai_compat / anthropic）
np(model=MyProvider())            # 实例
np(model="myapp.model:create")    # 地址（字符串不匹配任何已注册名时按地址解析）
```

ModelProvider 协议（`norpagent.protocols.model`）：

```python
class ModelProvider(Protocol):
    def generate(self, messages, tool_schemas, params) -> ModelOutput: ...
    def stream(self, messages, tool_schemas, params): ...   # 可选：增量产出
```

`stream` 存在时内核优先走流式路径（逐段广播 `on_content`），
否则一次性 `generate`。`params["_cancel_event"]` 是内核注入的
取消事件，适配器应据此尽早退出（硬超时配合）。

### 7.2 工具槽位

三种赋值形态：

```python
np(tools=["echo", "get_time"])           # 名字列表：引用注册表
np(tools={"my_tool": MyTool()})          # 映射：注册并启用
np(tools=[ToolA(), ToolB()])             # 实例列表：按 name 注册
```

Tool 协议（`norpagent.protocols.tool`）：

```python
class Tool(Protocol):
    name: str
    def schema(self) -> dict: ...        # OpenAI function schema
    def run(self, args: dict, ctx: RunContext) -> ToolResult: ...
```

内置工具清单（`install_defaults` 注册）：`echo`、`get_time`、
`run_python`（PTC 沙箱执行）、`file_read / file_write / file_list /
file_delete`（路径安全）、`exec_cmd`（沙箱协议）、`web_search /
web_fetch / web_extract_links`（SSRF 防护）、`context_add / search /
list / delete`（FTS5 上下文库）、`project_status`、`task_submit /
list / status / cancel`（长周期任务协作）。

### 7.3 示例：模型基准测试

minimal 预设使用确定性环境与最简工具集，可用于对比不同模型在
固定输入集上的输出：

```python
import norpagent as np

for model_name in ("mock", "openai_compat"):
    np(preset="minimal", model=model_name, prompt="1+1=?",
       frontend="norpagent.frontends.headless:HeadlessFrontend")
    while True:
        if np.stop():
            break
    r = np.current().last_result
    print(model_name, r.steps, r.usage.total_tokens, r.final_content[:40])
    np.shutdown()
```

---

## 第 8 章　会话、沙箱、调度器、上下文与项目

### 8.1 会话

```python
np(session="memory")     # 进程内（默认）
np(session="sqlite")     # 持久化到 ~/.norpagent/sessions.db
np(session=MySessionManager())          # 实例
np(session="myapp.sessions:create")     # 地址
```

SessionManager 协议：`create_session / get_session / append_message /
history`。跨会话续聊通过 `session_id`：

```python
eng = np.current()
r1 = eng.submit("记住：我最喜欢蓝色")
r2 = eng.submit("我最喜欢什么颜色？", session_id=r1.session_id)
```

### 8.2 沙箱

```python
np(sandbox="subprocess")   # 子进程（默认）
np(sandbox="pooled")       # 池化复用 + 并发上限 + 超时强杀进程树
np(sandbox="myapp.docker_sandbox:create")
```

Sandbox 协议：`run / close`。`exec_cmd` 工具通过沙箱协议执行，
替换容器/池化沙箱实现无需修改工具代码。

### 8.3 调度器

```python
np(scheduler="simple")       # 内存队列（默认）
np(scheduler="persistent")   # 持久化 + 崩溃 resume() 续跑
```

TaskScheduler 协议：`submit / drain / cancel`。`task_*` 工具族供模型
编排长周期任务；`agent.run_task()` 是多智能体编排入口
（子任务可用 `preset_name` 指定不同模式 = 不同子 Agent）。

### 8.4 上下文库与项目管理（通用组件命名空间）

```python
np(context_store="norpagent.builtin.context:FTS5ContextStore")
np(project_manager=MyProjectManager())
```

这两个槽位走**通用组件命名空间**（`registry.register_component`），
组件种类开放——可注册新种类组件并在预设里声明，无需修改内核。

### 8.5 基础服务槽位

```python
np(logger=logging.getLogger("my.app"))       # 日志
np(storage="./my_data")                       # 持久化根
np(error_handler=lambda exc, eng: print(exc))  # 错误最后防线
```

`error_handler` 在任务级异常兜底时被调用（签名 `(error, engine)`），
不填则记录到 logger。

---

## 第 9 章　9 层 29 钩子

> 设计原则：**每一个执行结构都必须暴露为 API，并且可以被钩子干预。**
> 每个钩子都是独立的模块级 API；支持自定义钩子、自定义层；零依赖，
> 标准库自带。本章与 `docs/hooks.md`（钩子体系独立文档）配套阅读。

### 9.1 钩子分层与 29 钩子全表

Agent 循环切为 9 层，每层引出钩子：

```
L1 运行时生命周期 ─ L2 任务 ─ L3 输入 ─ L4 会话与历史 ─ L5 消息组装
   ─ L6 步骤 ─ L7 模型调用 ─ L8 工具调用 ─ L9 结果定型
```

29 个钩子全部是 `norpagent.hooks` 下可导入的一等对象
（`from norpagent.hooks import before_model_call, ...`），全表如下：

| 层 | 钩子 | 可变 | 负载键（payload_keys） |
|---|---|---|---|
| L1 运行时 | `on_agent_init` | | preset |
| L1 运行时 | `on_agent_shutdown` | | preset |
| L2 任务 | `on_task_start` | | task_id, session_id, preset, user_input |
| L2 任务 | `on_task_done` | | task_id, session_id, content, steps, context |
| L2 任务 | `on_task_error` | | task_id, error |
| L2 任务 | `on_task_stopped` | | task_id, reason |
| L2 任务 | `on_task_timeout` | | task_id, timeout, kind |
| L3 输入 | `before_input` | ✓ | task_id, user_input, session_id, params |
| L3 输入 | `after_input` | | task_id, user_input, session_id |
| L3 输入 | `on_user_input_required` | | question, default |
| L4 会话 | `before_session_create` | ✓ | session_id, title, params, task_id |
| L4 会话 | `after_session_create` | | session_id, title, task_id |
| L4 会话 | `before_message_append` | ✓ | session_id, message, task_id |
| L4 会话 | `after_message_append` | | session_id, message, task_id |
| L5 组装 | `before_build_messages` | ✓ | system_prompt, session_id, step, task_id, tool_names |
| L5 组装 | `after_build_messages` | ✓ | messages, system_prompt, step, task_id |
| L6 步骤 | `before_step` | ✓ | task_id, step, messages, context, params |
| L6 步骤 | `after_step` | | task_id, step, content, tool_calls |
| L7 模型 | `before_model_call` | ✓ | task_id, step, messages, tool_schemas, params |
| L7 模型 | `after_model_call` | ✓ | task_id, step, output |
| L7 模型 | `on_reasoning` | | task_id, content, stream |
| L7 模型 | `on_content` | | task_id, content, stream, final |
| L7 模型 | `on_event` | | event_type, data, task_id |
| L7 模型 | `on_usage_update` | | task_id, input, output, total |
| L8 工具 | `before_tool_call` | ✓ | task_id, tool_name, args, context |
| L8 工具 | `after_tool_call` | ✓ | task_id, tool_name, args, result, success, context |
| L8 工具 | `on_tool_error` | | task_id, tool_name, error, args |
| L9 定型 | `before_result` | ✓ | task_id, result |
| L9 定型 | `after_result` | ✓ | task_id, result |

带 ✓ 为**可变钩子**（mutating=True）：订阅者可通过返回值改写
数据流，或抛 `HookVeto` 一票否决；其余为观测钩子（emit），
返回值被忽略。每个钩子的完整负载键以 `Hook.payload_keys` 为准
（与 `norpagent.hooks.standard` 中各钩子注释一致）。

### 9.2 三种用法与订阅目标解析

```python
from norpagent.hooks import before_model_call

# 1. 模块级独立 API：不传 system 时落在「进程默认钩子系统」
before_model_call.subscribe(log_request)                # 默认系统（私有总线）
before_model_call.subscribe(log_request, system=reg)    # 指定 Registry

# 2. 运行时视图：与 registry.hooks 同一总线（多实例推荐）
agent.hooks.before_model_call.subscribe(log_request)

# 3. 槽位批量订阅：np(hooks={"before_model_call": my_fn})
```

- 模块级 `Hook` 对象的 `subscribe / unsubscribe / emit / intercept`
  都需要一个 `system` 定位总线，可传 `HookSystem / EventBus /
  Registry / AgentRuntime`（`_resolve_bus` 统一解析）；
  **缺省时落在进程级默认系统**（`hooks.get_default_system()`，
  自带独立私有总线）——它与 `np()` 引擎的总线不是同一条。
  给独立 Registry 使用时**务必显式传 system**（每个 Registry
  自带私有总线，保证多实例隔离），否则订阅挂到默认系统上，
  收不到引擎事件；
- `agent.hooks.before_model_call` 返回 `BoundHook`（绑定到该
  引擎总线的钩子），四个方法无需再传 system；
- `np(hooks={...})` 槽位（literal 语义）：dict 的键是事件名、
  值是订阅者，装配期统一挂到引擎总线上；热挂载
  `np.remount(hooks=...)` 时先退订上次的架构级订阅再重挂，
  **不叠加**。槽位值也可以是 `callable(reg)` 工厂：先调用、
  返回 dict 后再订阅。

### 9.3 可变钩子的返回语义与 HookVeto

可变钩子经 `EventBus.intercept` 分发：**按订阅顺序依次调用，
返回第一个非 None 的返回值；全部返回 None 视为不干预。**
返回语义全表：

| 钩子 | 返回值 | 效果 |
|---|---|---|
| `before_input` | `str` | 替换用户输入 |
| | `HookVeto(reason)` | 任务以 stopped 收尾，reason 进入错误信息 |
| `before_session_create` | `str` / `{"title": str}` | 改写会话标题 |
| | `HookVeto` | 放弃创建（任务以 stopped 收尾） |
| `before_message_append` | `ChatMessage` | 替换消息 |
| | `False` / `HookVeto` | 丢弃该条消息（不落库） |
| `before_build_messages` | `str` / `{"system_prompt": str}` | 替换系统提示词 |
| `after_build_messages` | `List[ChatMessage]` | 替换整组消息 |
| `before_step` | `List[ChatMessage]` | 替换本轮消息 |
| | `HookVeto` | 跳过本轮模型调用（进入下一轮） |
| `before_model_call` | `{"messages": [...], "params": {...}}` | 按需替换请求 |
| | `HookVeto` | 拒绝本轮调用（任务以 stopped 收尾） |
| `after_model_call` | `ModelOutput` | 替换本次输出 |
| `before_tool_call` | `dict` | 替换工具参数 |
| | `False` / `HookVeto` | 阻止调用（回填 blocked_by_hook 结果） |
| `after_tool_call` | `str` / `ToolResult` | 替换工具结果 |
| `before_result` / `after_result` | `RunResult` | 替换最终结果 |
| 两者 | `HookVeto` | 保持原结果（否决被忽略） |

`HookVeto` 行为细节：

- 类型定义在 `norpagent.kernel.events`（经 `norpagent.hooks`
  再导出），是 `Exception` 子类，构造参数即否决原因；
- `EventBus.intercept` 对 HookVeto **不捕获**——否决语义必须
  送达内核，保证一票否决一定生效；普通订阅者异常被捕获记录
  （stderr，或 `bus.set_error_logger()` 指定记录器）后继续
  分发，单个订阅者永远拖不垮主循环；
- 各执行点的否决收尾语义不同（见上表）：before_input /
  before_model_call / before_session_create → 任务 stopped；
  before_step → 跳过本轮；before_tool_call → 回填
  blocked_by_hook；before_message_append → 丢弃该条；
  before_result / after_result → 忽略否决保持原结果；
- 订阅者分发顺序：全事件订阅者（`bus.subscribe(fn)` 不带事件名）
  先于具名订阅者；同组内按订阅先后；emit 逐个调用（异常隔离），
  intercept 逐个调用直到出现非 None 返回值。

### 9.4 自定义钩子与自定义层

三种扩展方式，与标准 29 钩子完全同权：

```python
from norpagent.hooks import HookLayer

# 方式一：自定义层 + 层内声明钩子（插件加载管线是标准库用例，11.4）
network_layer = HookLayer("L10_network", order=100, description="网络访问层")
before_net = network_layer.hook("before_network_call", mutating=True,
                                description="网络请求发出前（可改写 URL 或否决）")
agent.hooks.install_layer(network_layer)          # 安装后立即可用
agent.hooks.before_network_call.subscribe(monitor)

# 方式二：直接在钩子系统上定义钩子（不建层，归属 dynamic 层）
agent.hooks.define_hook("after_cache_hit", mutating=False,
                        description="缓存命中")

# 方式三：零定义触发——未注册的具名事件自动成为 dynamic 层钩子
agent.hooks.hook("my_custom_event").emit(data=42)
```

- `HookLayer(name, order, description)` 声明一层；`layer.hook()`
  返回 `Hook` 定义（即模块级 API），可提前导出供第三方
  `subscribe(fn, system=reg)` 使用；
- `install_layer` 按 `order` 排序层；**同名钩子已存在时保留原
  定义**（只登记层元数据），重复安装不覆盖既有订阅；
- `HookSystem` 查询 API：`list_hook_names()` / `list_hooks()` /
  `layers()` / `layer_of(name)` / `get(name)`；
- 自定义钩子照常支持 `subscribe / unsubscribe / emit / intercept`。

### 9.5 与 EventBus 的关系

钩子体系是事件总线的**结构化视图**，不是另一套机制：

- `HookSystem` 构造时把 9 层标准层装到某条 `EventBus` 上，
  订阅 / 发布最终都落在 `registry.bus`；
- `reg.hooks.before_step.subscribe(fn)` 与
  `reg.bus.subscribe(fn, "before_step")` **完全等价**，可混用；
- 因此替换循环系统（async_loop 槽位）后钩子照常工作——钩子
  挂在事件总线上，与循环实现无关（FAQ Q6）；
- 事件名与早期 plugin_system 的 15 个 hook 完全一致，旧插件 /
  旧代码无需修改（第 11 章插件钩子桥接即依赖这一点）；
- 性能（0.9）：EventBus 采用写时复制——subscribe / unsubscribe
  在锁内替换新列表，emit / intercept 取一次快照引用后无锁
  迭代，高频流式事件（on_content 逐 token）不产生每事件列表
  复制开销（第 14.3 节超高并发）。

### 9.6 每个执行结构都可覆写

除钩子外，`AgentRuntime` 的七个执行结构同时是公共方法，子类
覆写即可替换该环节，无需改动循环本体：

```python
from norpagent import AgentRuntime, ChatMessage

class MyRuntime(AgentRuntime):
    def build_messages(self, system_prompt, session_id, *, step,
                       task_id, tool_names=None):
        messages = super().build_messages(...)     # 先走 L5 钩子
        messages.append(ChatMessage(role="system", content="自定义注入"))
        return messages

    def call_model(self, provider, history, schemas, params,
                   task_id, result, step):
        ...                                         # 完全接管 L7
```

方法清单：`prepare_input`（L3）/ `create_session`、
`append_message`（L4）/ `build_messages`（L5）/ `call_model`
（L7）/ `execute_tool_call`（L8）/ `finalize_result`（L9）。
覆写与钩子的关系：默认实现**先走钩子再做默认逻辑**，覆写时可
保留 super() 调用（钩子继续生效）或完全接管（跳过钩子）；
也可以经 `agent_runtime` 槽位整体替换循环类（3.1 节）。

### 9.7 行为细节与最佳实践

- 被阻止 / 否决 / 审批拒绝的工具调用统一流经 `after_tool_call`
  ——「执行结构」无论结果如何都过钩子；
- `before_step` 否决本轮 → 跳过模型调用进入下一轮；`after_step`
  只在有工具调用时广播（无工具调用时直接走最终回复路径）；
- 钩子改写作用于**实际数据流**（消息 / 参数 / 结果），不是旁路
  通知；改写后的值继续参与后续管线；
- 重复订阅同一 fn 会执行多次：热挂载 hooks 槽位时框架先退订
  架构级订阅再重挂（不叠加）；自己订阅的请配对 unsubscribe
  （`EventBus.unsubscribe` 只移除首个相等元素）；
- 高频钩子（on_content / on_reasoning 流式逐 token）里避免重
  计算与阻塞 I/O；订阅者异常虽被隔离记录，但异常路径有开销；
- 观测钩子（emit）的返回值被忽略——要干预数据流必须用可变
  钩子（intercept）或方法覆写；
- 线程安全：HookSystem / EventBus 的注册表操作均加锁，运行中
  订阅 / 退订安全（3.7 节热挂载即依赖这一点）；
- 需要干预但不想全局挂订阅者：把逻辑写成独立函数，配合任务级
  params 显式开关（如 jailbreak_guard / harden_prompt，见
  10.4 节），或只用 np(hooks=...) 槽位在指定引擎上订阅。

---

## 第 10 章　安全系统：norpagent.safe()

> 一句话：`safe()` 把全套安全体系（越狱防护 / 提示词加固 /
> 人工审批 / 网络策略 / 源码审计 / 导入限制 / 签名信任 /
> 插件隔离策略）收敛为一个独立函数。配套文档 `docs/security.md`。

### 10.1 启用方式

```python
import norpagent as np

# 1. np() 槽位方式
np(security="high")                                  # 字符串：只挂运行态策略，钩子零干预
np(security={"level": "high", "hooks": True})        # dict：+ 显式钩子干预
np(security=lambda reg: safe(reg, config={...}))     # callable：完全自定义装配

# 2. safe() 直接方式
from norpagent import safe
kit = safe(registry, level="standard")               # basic / standard / high
kit = safe(registry, level="standard", hooks=True)

# 3. 两段式：先拿套件，稍后安装
kit = safe(level="high")
kit.install(registry)                                # 只挂运行态策略（默认不挂钩子）
kit.install_hooks(registry)                          # 需要干预时手动挂载
kit.uninstall_hooks(registry)                        # 随时卸下，恢复纯净钩子
```

设计要点——**安全系统整体剥离**：

- 内核模块级不 import 任何 `norpagent.security` 模块；防护 /
  加固 / 审批 / 审计 / 签名通过 `registry.security` 注入
  （任务级参数显式要求时内核才惰性 import guard，见 10.4）；
- **钩子零干预（默认）**：safe() 默认不订阅任何钩子——越狱
  防护与提示词加固不作为钩子订阅者自动挂到总线上，钩子管线
  保持纯净；需要干预时由用户显式开启（hooks=True /
  kit.install_hooks()）；
- 防护能力本身始终以**独立 API** 提供（kit.scan_input /
  kit.harden / ...，10.5 节），用户可在自己的钩子订阅者或
  方法覆写中自由调用；
- 运行态决策（人工审批 / 网络策略 / 插件加载策略）始终经
  `registry.security`（SecurityContext）生效，与钩子是否挂载
  无关；
- CLI 等价开关：`--safe basic|standard|high`（只挂运行态策略），
  `--safe-hooks` 才显式挂钩子。

### 10.2 三档级别

| 能力 | basic | standard | high |
|---|---|---|---|
| 输入越狱/注入防护（L3 钩子，需显式开启） | ✓ | ✓ | ✓ |
| 系统提示词加固（L5 钩子，需显式开启） | ✓ | ✓ | ✓ |
| 插件源码 AST 审计 | warn | warn | **block** |
| 插件导入限制 | off | safe | safe |
| 权限声明（manifest permissions） | | | ✓ |
| 插件网络策略 | allow_all | deny | deny |
| 插件工具人工审批 | | ✓ | ✓ |
| 强制受信任签名 | | | ✓ |

- `basic`：只做输入防护与提示词加固（且默认不挂钩子，按需
  显式开启），插件侧不设限制——适合信任来源的本地插件开发；
- `standard`（默认）：+ 插件导入限制 safe、网络 deny、插件
  工具审批，签名校验开启但不强制；
- `high`：+ 审计 block（critical 即拒绝）、要求 manifest 权限
  声明、强制受信任签名（未签名 / 不受信任一律拒绝加载）。

### 10.3 SecurityContext：运行态安全策略的唯一事实源

`registry.security` 是一个 `SecurityContext` 实例：AgentRuntime
在工具审批时读取它，插件加载器在 config 缺省时读取它的
`plugin_config()`。字段（safe(level=...) 预设后可用 config dict
逐项覆盖，键名与 norpagent.security / 插件加载器配置一致）：

| 字段 | 默认(standard) | 说明 |
|---|---|---|
| `level` | standard | basic / standard / high |
| `guard_enabled` | True | 输入防护总开关（钩子干预路径） |
| `harden_enabled` | True | 提示词加固总开关（钩子干预路径） |
| `audit_level` | warn | off / warn / block |
| `import_restrict` | safe | off / safe / strict |
| `require_permissions` | False | manifest.permissions 强制 |
| `signature_verify` | True | Ed25519 验签（invalid 拒绝） |
| `signature_required` | False | True 时仅 trusted 放行 |
| `trusted_keys` | [] | 受信任公钥 hex 列表 |
| `network_policy` | deny | deny / audited_public / public_only / allow_all |
| `approval_config` | None | 审批策略 dict（10.6） |
| `plugin_isolation` | auto | auto / inproc / process |
| `hook_intervention` | False | True = 安装时同步挂防护钩子 |
| `extra` | {} | 扩展字段 |

- `plugin_config()` 把本上下文转成插件加载器配置（config
  缺省时的兜底）；`to_dict()` 输出完整姿态（与
  `kit.describe()` 一致）；
- `SecurityContext` 实例可直接作 `np(security=ctx)` 槽位值；
- config 键名以插件加载器风格为主（plugin_security_audit /
  plugin_network_policy / plugin_trusted_keys 等），另有直白键
  guard_enabled / harden_enabled / hook_intervention / approval /
  approval_config；safe() 的 `_apply_config` 统一映射到
  SecurityContext。

### 10.4 钩子干预（显式开启）

`hooks=True` / `kit.install_hooks()` 挂载两个订阅者：

- **L3 输入防护** = `before_input` 订阅者：命中越狱 / 注入特征
  即抛 `HookVeto`（任务以 stopped 收尾）。任务级参数
  `params["jailbreak_guard"] = False` 可显式关闭该任务的钩子
  防护；`True`（或任意真值）走内核显式扫描路径——内核直接
  scan_message，**不依赖钩子挂载状态**；
- **L5 提示词加固** = `before_build_messages` 可变订阅者：把
  核心规则与工具清单注入系统提示词。任务级参数
  `params["harden_prompt"] = False` 可显式关闭该任务的加固；
  `True` 走内核显式加固路径。

挂载 / 卸载语义：

- `kit.install_hooks(reg)` 幂等：同一注册表重复调用不叠加；
- `kit.uninstall_hooks(reg)` 只移除**本套件自己的**订阅者，
  不动用户 / 插件的其它订阅，钩子管线恢复纯净；
- `kit.hooks_installed(reg)` 查询当前状态；
- `kit.uninstall(reg)` 退订钩子订阅并清除 `registry.security`。
  运行中热挂载 security 槽位（`np.remount(security=...)`）会
  先 uninstall 旧套件再安装新套件，防同一总线上防护钩子叠加；
- 热挂载后运行态决策立即生效；钩子干预对后续任务的
  before_input / before_build_messages 生效。

### 10.5 独立检查 API（不挂钩子也能用）

SafetyKit 代理 norpagent.security 各模块，全部可直接单独调用：

| 方法 | 对应能力 |
|---|---|
| `kit.scan_input(text)` → (blocked, reason, hits) | 越狱 / 注入扫描 |
| `kit.is_jailbreak_attempt(text)` → bool | 扫描结果布尔化 |
| `kit.harden(prompt, tool_names)` → str | 提示词加固 |
| `kit.audit_file(path)` / `kit.audit_source(src)` → issues | 源码 AST 审计 |
| `kit.verify_plugin(path, manifest)` → SignatureResult | 插件签名校验 |
| `kit.check_network(url)` → bool | 按当前网络策略裁决（SSRF） |
| `kit.approval_policy(hints)` → ApprovalPolicy | 审批策略实例 |
| `kit.network_policy()` → NetworkPolicy | 网络策略实例 |
| `kit.describe()` → dict | 当前安全姿态 |

在自己的钩子订阅者里调用独立 API 的示例：

```python
from norpagent.hooks import HookVeto

def my_guard(event):
    blocked, reason, _ = kit.scan_input(event.get("user_input") or "")
    if blocked:
        raise HookVeto(reason or "输入被安全防护拦截")
reg.hooks.before_input.subscribe(my_guard)
```

底层模块（`norpagent.security`，零框架依赖，可独立 import）：
`guard`（扫描 / 加固）、`approval`（审批决策）、`network_policy`
（SSRF 裁决）、`audit`（AST 审计）、`signature`（Ed25519，
需 `norpagent[security]` 提供的 cryptography）。

### 10.6 运行态决策点

**人工审批**（AgentRuntime 工具执行路径）：

- 策略来源优先级：`params["approval_policy"]` 实例 >
  `params["approval_config"]` dict > `registry.security.approval_config`；
- 原生工具按「工具名 → 级别」映射审批（file_write /
  file_delete / exec_cmd 等 WRITE / DELETE / EXEC 级，含旧
  工具名兼容）；插件工具走 `approval_enabled` 总开关 + 插件
  `APPROVAL_HINTS` 精细控制（approval="none" 免审批，第 11 章）；
- 交互由 UI 适配器经 `ctx.ask_user` 完成（随广播
  on_user_input_required 钩子）；用户否定 → 调用被取消
  （approval_denied），仍流经 after_tool_call。

**网络策略 / SSRF 防护**（norpagent.security.network_policy）：

- 四粒度：`deny`（默认）→ `audited_public`（须命中 URL /
  域名白名单）→ `public_only`（禁内网）→ `allow_all`；
- 除 allow_all 外，一律拒绝私网 / 环回 / 链路本地 / 保留段 /
  云元数据地址（169.254.169.254 等）；先文本级判断再 DNS
  解析复判（防 rebinding 常见路径）。

**插件加载策略**：`install_plugin_dirs` 未显式给 config 时
自动采用 `registry.security.plugin_config()`——安全系统剥离后，
插件加载默认继承全局安全姿态（11.8 节）。

### 10.7 安全不降级原则与深度防御

- 未安装 cryptography 时签名校验返回「不受信任」，**不放行**
  （安全姿态不降级）；`norpagent[security]` 提供验签能力；
- 插件加载顺序固定：签名校验 → AST 审计 → 权限声明 → 导入
  限制 → 注册（每阶段失败即拒绝，不进入下一阶段，11.3）；
- 导入限制双层：AST 静态预检（防 sys.modules 已缓存模块绕过
  meta_path）+ sys.meta_path 运行时拦截；
- 进程级隔离（high / 自定义 config）把不可信插件移出主进程，
  即便审计漏检，插件崩溃也不影响宿主（11.7）；
- 内核侧 params 显式开关（jailbreak_guard / harden_prompt）
  与 safe() 的钩子路径并存：只要有一条开启，防护就生效
  （不依赖钩子挂载状态）。

### 10.8 典型组合

```python
# 生产默认：standard + 显式钩子干预
np(security={"level": "standard", "hooks": True})

# 严格：high + 白名单网络 + 受信任密钥
kit = safe(level="high", config={
    "plugin_network_policy": "audited_public",
    "plugin_network_domain_allowlist": ["api.example.com"],
    "plugin_trusted_keys": ["<公钥hex>"],
})
np(security=kit.context)                     # 直接安装 SecurityContext

# 纯决策、零干预：只用审批与网络策略，防护逻辑自己接
np(security={"level": "standard",
             "config": {"guard_enabled": False, "harden_enabled": False}})
```

---

## 第 11 章　插件系统

> 外部插件以独立 `.py` 文件（或 manifest 包）分发，宿主经
> `norpagent.plugins` 加载器接入，自动获得签名校验 / AST 审计 /
> 导入限制 / 网络策略 / 人工审批全套安全防护。插件格式与现有
> 应用的 plugin_system 完全兼容，旧插件无需改代码即可迁移。
> 配套文档：`docs/plugins.md`（宿主侧）、`norpagent插件开发指南.md`
> （插件作者侧）。

### 11.1 两种 API 与 np() 槽位

```python
# 便捷入口：一次性加载目录
from norpagent.plugins import install_plugin_dirs
loader = install_plugin_dirs(reg, ["my_plugins"], config={...})

# 库化门面：完整生命周期 + 状态 + 热重载
from norpagent.plugins import PluginSystem
ps = PluginSystem(reg, ["my_plugins"], config={"plugin_isolation": "auto"})
infos = ps.load()        # 发现 → 安全加载 → 注册
ps.status()              # 插件清单 + 隔离宿主状态
ps.reload("my_tool")     # 开发期热重载单个插件
ps.shutdown()            # 释放进程隔离宿主子进程

# np() 槽位（literal 语义，目录列表）
np(plugins=["./my_plugins"])
# 运行中热替换：旧订阅自动退订，不叠加（3.7 节）
np.remount(plugins=["./my_plugins_v2"])
```

`np(plugins=[...])` 槽位以固定配置装配（audit=warn、验签开启，
**不读取** registry.security 的覆盖）；需要精细配置用
PluginSystem / install_plugin_dirs 直接操作 Registry（或
callable 槽位值，如 `np(plugins=lambda reg: ps.load())`）。

### 11.2 插件格式（与现有应用兼容）

**模块级接口**（单文件插件 `my_plugin.py`）：

| 名称 | 类型 | 说明 |
|---|---|---|
| `PLUGIN_NAME` | str | 插件显示名（必填） |
| `PLUGIN_VERSION` | str | 版本，默认 0.0.0 |
| `PLUGIN_PUBLISHER` | str | 发布者 |
| `PLUGIN_DESCRIPTION` | str | 描述 |
| `TOOLS` | list | OpenAI function schema 列表 |
| `execute(tool_name, args, ctx)` | callable | 工具统一入口，返回 str / None |
| `APPROVAL_HINTS` | dict | 工具 → 审批提示（11.8） |
| `ISOLATION` | str | `"process"` = 进程级隔离（AST 静态读取，不执行代码） |
| `__norpagent_type__` | str | 文件即模块类型声明（"tool" / "plugin"，FLOW 拖入用） |
| 15 个钩子函数 | callable | 与旧应用 hook 名完全对齐（11.5 桥接） |

```python
# my_plugin.py —— 最小插件
PLUGIN_NAME = "greet_plugin"
TOOLS = [{
    "type": "function",
    "function": {
        "name": "greet",
        "description": "向用户打招呼",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"}},
                       "additionalProperties": False},
    },
}]

def execute(tool_name, args, ctx):
    if tool_name == "greet":
        return f"你好，{args.get('name') or 'world'}！"
    return None
```

**manifest 包格式**：目录 + `manifest.json`（name / version /
publisher / description / entry 默认 plugin.py / permissions /
signature / isolation 字段）。

### 11.3 安全管线（加载全流程）

```
发现（目录扫描：*.py 单文件 / manifest 包）
  → before_plugin_load（HookVeto 可拒，11.4）
  → 1. 签名校验：invalid 直接拒绝；signature_required 时仅 trusted 放行
  → 2. 信任分级：受信任签名 → 审计放宽为 warn
  → 3. AST 审计：危险调用 / 危险导入 / getattr、__dict__ 反射绕过检测，
       block 级发现 critical 即拒绝
  → 4. 权限声明：require_permissions 时校验 manifest.permissions
  → 5. 隔离决策：process → 插件只在宿主子进程加载（11.7）
  → 6. 导入限制下加载模块（静态预检 + meta_path 拦截，11.6）
  → 7. 读取元数据（PLUGIN_NAME / TOOLS / 钩子 / APPROVAL_HINTS）
  → before_plugin_register（HookVeto 可拒）
  → 8. 适配为 Plugin 协议 → 注册进 Registry（工具入表、钩子订阅总线）
```

每阶段失败 → `PluginInfo(enabled=False, error=...)` 记录原因后
**继续扫描其它插件**，不中断整体加载。`PluginInfo` 携带
`name / path / version / publisher / description / enabled /
error / tools / hook_names / signature_status / trusted /
approval_hints / audit_issues`；调试时看
`loader.plugins[i].error` 与 `audit_issues`（含行号）。

### 11.4 加载管线钩子（自定义层的标准库用例）

PluginSystem 构造时把 `PLUGIN_PIPELINE_LAYER`（order=200 的
自定义 HookLayer，9.4 节能力的官方示例）装进 `registry.hooks`，
8 个管线钩子：

`before/after_plugin_discover`、`before/after_plugin_load`、
`before/after_plugin_audit`、`before/after_plugin_register`。

```python
from norpagent.hooks import HookVeto
from norpagent.plugins import before_plugin_load

def block_listed(event):
    if (event.get("name"), event.get("path")) in HOST_BLOCKLIST:
        raise HookVeto("该插件被宿主策略拒绝")
before_plugin_load.subscribe(block_listed, system=reg)
```

- 可变管线钩子（before_plugin_load / before_plugin_audit /
  before_plugin_register）抛 HookVeto = 拒绝该插件的加载 /
  注册（enabled=False + error 记录原因）；after_* 为观测钩子
  （after_plugin_audit 负载含 allowed / issues）；
- 管线钩子经 `registry.hooks.hook(name)` 动态注册，与插件模块
  级 hook 互不冲突。

### 11.5 旧插件钩子桥接（15 个 hook 对齐）

插件模块级定义的钩子函数（on_task_start / before_step /
before_tool_call / after_tool_call 等 15 个）由加载器包装成
EventBus 订阅者：

- 签名约定：**业务参数在前，PluginContext 在最后**（ctx 提供
  plugin_name / project_root / app_dir / config / current_step）；
- 可变钩子（before_step / before_tool_call / after_tool_call）
  的返回值经 intercept 透传给内核，其余钩子返回值忽略；
- 事件 payload → 旧参数列表的映射与现有应用 plugin_system
  派发逻辑完全一致（loader._HOOK_ARG_KEYS），旧插件零改动迁移；
- 进程隔离插件同样经此桥接：fire_hook RPC 转发（限时 5s）。

### 11.6 导入限制

- `off`：不限制（本地调试用；受信任签名只放宽审计，导入限制
  仍按配置执行）；
- `safe`（standard / high 默认）：阻断危险模块（subprocess /
  ctypes / cffi / socket / pickle / marshal / telnetlib / ftplib /
  smtplib；ctypes / cffi 无条件阻断），**双层防护**：AST 静态
  预检（防 sys.modules 已缓存模块绕过 meta_path）+ 加载期
  sys.meta_path 拦截（栈帧探测调用方是否为插件模块）；
- `strict`：仅允许安全模块白名单（json / re / datetime / math /
  random / collections / itertools / functools / typing / enum /
  pathlib / os.path / textwrap / string / hashlib / base64 /
  traceback / logging / warnings / copy / uuid / time /
  norpagent.protocols.tool 等），白名单外一律 ImportError。

插件模块以 `norpagent_ext_<name>` 模块名加载（命名空间统一），
限制器**只对插件模块生效**，不影响宿主代码。

### 11.7 进程级隔离

`ISOLATION = "process"`（模块常量，AST 静态读取，**不执行插件
代码**）/ manifest `isolation` 字段 / 宿主配置
`plugin_isolation`（auto 时取前两者；显式 inproc / process 时
强制）。隔离语义：

- 插件模块对象只存在于宿主子进程（`python -m
  norpagent.plugins.host`，JSON 行协议 RPC）；
- 工具执行经 RPC 回传；钩子经 fire_hook 转发，单次钩子限时
  5s（HOOK_TIMEOUT），超时放弃——插件钩子永不拖死主循环；
- 崩溃自愈：子进程死亡 → 自动重启 + 重载全部插件 → 重试一次；
- 工具错误不冒泡：远端异常转为失败 ToolResult；
- 导入限制在子进程内继续生效（纵深防御）。

### 11.8 与安全系统的联动

- `install_plugin_dirs(reg, dirs)` **不传 config** 时自动采用
  `registry.security.plugin_config()`——先 `safe(reg, ...)` 再
  装插件，插件加载即继承全局安全姿态（注意：np(plugins=...)
  槽位路径传固定 config，不读 registry.security）；
- `safe(level="high")` 对插件的影响：审计 block、强制权限
  声明、强制受信任签名（未签名 / 不受信任拒绝）；
- 信任机制：`python -m norpagent plugin-sign --gen` 生成密钥对，
  `plugin-sign my_plugin.py --key <私钥hex>` 生成签名；公钥加入
  `plugin_trusted_keys` 后该插件受信任 → 审计放宽为 warn
  （导入限制按配置执行，不受信任影响）；
- 网络访问由宿主 `plugin_network_policy` 裁决（默认 deny），
  插件自身无法绕过——策略执行于宿主进程（10.6 节）；
- 审批：插件工具默认走 `approval_enabled` 总开关；插件
  `APPROVAL_HINTS` 里 `{"approval": "none", "risk": "L0"}` 可
  对单个工具免审批，未声明的工具走总开关（向后兼容）。

### 11.9 配置键总表与生命周期

```python
config = {
    "plugin_security_audit": "warn",            # off / warn / block
    "plugin_security_import_restrict": "off",   # off / safe / strict
    "plugin_security_require_permissions": False,
    "plugin_signature_verify": True,
    "plugin_signature_required": False,         # True: 仅 trusted 放行
    "plugin_trusted_keys": ["<公钥hex>"],
    "plugin_network_policy": "deny",            # deny/audited_public/public_only/allow_all
    "plugin_network_url_allowlist": ["https://api.example.com/"],
    "plugin_network_domain_allowlist": ["api.example.com"],
    "approval_enabled": True,                   # 插件工具审批总开关
    "plugin_isolation": "auto",                 # auto / inproc / process
}
```

生命周期注意：

- `ps.load()` 可重复调用（先清空清单再重扫）；`ps.configure()`
  更新配置并使加载器失效重建；
- `ps.unload(name)` / `ps.reload(name)` 是开发期工具：旧实例的
  工具 / 钩子订阅仍留在 Registry（工具表为名字覆盖语义，钩子
  不支持按名整体退订），**生产环境建议重建 Registry 后重新
  加载**；
- 运行中整体替换用 `np.remount(plugins=[...])`：框架先退订旧
  架构级插件订阅再重装（防叠加，3.7 节）；
- `ps.shutdown()` / `loader.shutdown()` 释放进程隔离宿主子进程；
  热挂载 plugins 槽位（`np.remount(plugins=...)`）时框架自动先
  卸载旧加载器（退订钩子 + 清 sys.modules + 释放隔离宿主）。

## 第 12 章　预设模式

### 12.1 内置六模式

| 模式 | 用途 | 组件组合 |
|---|---|---|
| `minimal` | 模型基准测试 | mock + echo/get_time + memory |
| `standard` | 通用编码任务 | sqlite + pooled + persistent + fts5 + 全部内置工具 |
| `longrun` | 长周期复杂任务 | 同 standard；max_steps=512、不限时、分阶段规划提示词 |
| `ptc` | 代码编排工具调用 | run_python（沙箱执行） |
| `creative` | 自定义模式调试 | 模式文件加载（--mode-file） |
| `embedded` | 嵌入式 / 边缘 / 低资源（0.9） | 纯内存组件 + 最小工具集，**默认 headless 前端**，无磁盘 / 无联网依赖；无凭据时模型自动回落 mock。详见第 14.2 节 |

```python
np(preset="standard")
np(preset="ptc")
np(preset="embedded")                     # 默认 headless，纯 API 模式
np(preset=Preset(name="mine", model="mock", tools=["echo"], ...))
```

### 12.2 自定义预设

```python
from norpagent import Preset

my = Preset(
    name="mine",
    description="定制模式",
    model="mock",
    tools=["echo", "get_time"],
    session="sqlite",
    sandbox="pooled",
    scheduler="simple",
    ui="console",
    mode="single",
    params={"max_steps": 16},
    components={},
)
np(preset=my)
```

---

## 第 13 章　命令行入口

```bash
norpagent --list-modes
norpagent --mode minimal                          # 交互 REPL
norpagent --mode embedded                         # 嵌入式模式（无磁盘组件，0.9）
norpagent --mode ptc --prompt "..."               # 单次任务
norpagent --mode-file my_mode.py                  # 模式文件
norpagent --mode standard --ui web --port 8787    # Web UI
norpagent --mode standard --plugin-dir ./my_plugins
norpagent --mode standard --safe high               # 运行态安全策略（钩子零干预）
norpagent --mode standard --safe high --safe-hooks  # 同时开启钩子干预
norpagent plugin-sign --gen                       # 插件签名密钥
```

CLI 与 `np()` 等价：CLI 内部流程为
「安装默认组件 → 注册预设 → 应用安全 → 加载插件 → 构建运行时」。

---

## 第 14 章　嵌入式与超高并发部署

0.9 起，框架针对**嵌入式（低内存 / 低 CPU / 无磁盘 / 边缘设备）**与
**超高并发服务器**两大场景做了专项优化。本章说明优化内容、配置
入口与使用方式。

### 14.1 优化清单

**嵌入式场景（资源消耗最小化）：**

| 优化 | 内容 |
|---|---|
| `install_core()` 极简装配 | 只注册运行 Agent 最小闭环的组件：mock / openai_compat 模型、echo / get_time / run_python / file_* 工具、memory 会话、subprocess 沙箱、simple 调度器、console UI——**零磁盘依赖、无 HTTP 组件、组件命名空间为空**（不导入 sqlite3 / http.server） |
| builtin 包懒导入 | `import norpagent.builtin` 不再拉起 sqlite3 / http.server；FTS5 上下文库 / SQLite 会话 / 持久化调度器 / Web UI 全部改为 install_defaults 内按需导入 + 模块级 `__getattr__` 懒解析（`from norpagent.builtin import WebUI` 等写法兼容不变） |
| WebUI 构造零磁盘 I/O | 配置 / FE 配置 / flow 图三份磁盘状态的读取全部延迟到 `start()`（`_ensure_disk_loaded`）；只构造不启动不再读盘，只读根文件系统 / 无 HOME 环境安全 |
| 页面字节缓存 | `page_bytes()` 读入内存缓存：每次 GET / 不再读盘（此前每请求一次 open+read） |
| `embedded` 预设 | 内置第六模式：纯内存组件 + 最小工具集 + 默认 headless 前端（不监听端口），无凭据时模型回落 mock |
| 工作池收紧 | `NORPAGENT_MAX_WORKERS=1` 或 `config={"loop": {"max_workers": 1}}` 把守护工作线程压到最少；`NORPAGENT_SUBMIT_POLL=0.5` 调大轮询间隔省 CPU |

**超高并发服务器（吞吐与内存上界）：**

| 优化 | 内容 |
|---|---|
| EventBus 写时复制 | 订阅者表改为不可变快照：emit / intercept 在锁内只取引用、无锁迭代，**每条事件省去一次监听者列表复制**（流式逐 token 推送场景收益最大）。订阅 / 退订创建新列表替换引用，线程安全语义不变（实测 emit 吞吐 >150 万事件/秒） |
| SSE 有界背压 | 每连接一个 `_SSESubscriber` 有界缓冲（默认 1024 条）：慢客户端**丢最旧事件**（`drop_oldest`，默认）自动降级，可选 `drop_newest` / `unlimited`；内存占用有上界，与客户端数量解耦 |
| SSE 帧批量写出 | 攒满 32 条或 50ms 即一次 write+flush（`sse_batch` / `sse_batch_interval` 可配）：流式高频推送下系统调用次数大幅下降；单事件流延迟 ≤ 批间隔 |
| SSE 快速断连回收 | TCP 半关闭后第一次写不报错，靠心跳才发现会延迟至多 15s——空闲每 1s 非阻塞 select 探测连接可读性，断开后 ≤1s 释放线程与缓冲；心跳注释仍每 15s 一次，不增加网络负担 |
| HTTP 并发调优 | 监听积压 `request_queue_size=256`；`block_on_close=False` 停机不等连接关闭；`X-Accel-Buffering: no`（nginx 反代不攒批）；响应 keep-alive（HTTP/1.1） |
| submit 轮询收紧 | 完成轮询间隔 0.2s → 0.05s（默认）：阻塞等待任务的调用线程完成感知延迟上限 200ms → 50ms；可配 / 环境变量覆盖 |
| 循环内核微调 | `traceback` 提升模块级（回调异常路径零 import）；ready 队列快照批量执行保持防饥饿语义 |

### 14.2 嵌入式部署

**方式一：`install_core()` 自建注册表（依赖面最干净）：**

```python
from norpagent import Registry, AgentRuntime, install_core
from norpagent.modes import build_embedded_preset

reg = Registry()
install_core(reg)                       # 不导入 sqlite3 / http.server
reg.register_preset(build_embedded_preset())
agent = AgentRuntime(reg, preset="embedded")
result = agent.run("你好")
print(result.final_content)
```

注意：`install_core` 的注册表上没有 context_store / project_manager /
persistent 等组件——声明了这些组件的预设（standard / longrun /
creative 等）在此注册表上装配会被明确拒绝（报错列出缺失组件名）。

**方式二：`np(preset="embedded")`（开箱即用）：**

```python
import norpagent as np

np(preset="embedded")                   # 默认 headless：不启动 HTTP 服务
eng = np.current()
result = eng.submit("你好")             # 纯 API 提交
eng.request_stop()
```

embedded 预设的行为约定：

- **默认前端自动回落 headless**（装配器默认工厂判断 preset 名）；
  需要 Web 界面时显式
  `np(preset="embedded", frontend="norpagent.frontends.web:WebFrontend")`；
- 模型声明 `openai_compat`：提供任何凭据（参数 / 环境变量）即用真实
  模型，否则回落 mock（离线设备开箱可用）；
- 组件全部纯内存（memory / subprocess / simple），不声明通用组件——
  FTS5 / SQLite 不会被构建，不产生落盘文件。

**方式三（资源开关，与方式一/二叠加）：**

```python
# 工作线程压到 1；轮询放宽省 CPU
os.environ["NORPAGENT_MAX_WORKERS"] = "1"
os.environ["NORPAGENT_SUBMIT_POLL"] = "0.5"
# 或等价：
np(config={"loop": {"max_workers": 1, "poll_interval": 0.5}})
```

### 14.3 超高并发部署

**SSE 背压配置（启动参数 → 环境变量 → 运行中热改变）：**

```python
import norpagent as np

# 启动时传入
np(config={"web": {"sse_queue_size": 2048, "sse_queue_policy": "drop_oldest"}})
# 或运行时参数 / 环境变量
np(sse_queue_size=2048, sse_queue_policy="drop_oldest")
# NORPAGENT_SSE_QUEUE_SIZE=2048 NORPAGENT_SSE_QUEUE_POLICY=drop_oldest

# 运行中热改变（无需重启，对既有连接立即生效）
from norpagent.builtin.ui.web import WebUI
ui = np.current().frontend._ui      # 或直接持有 WebUI 实例
ui.set_sse_queue(sse_queue_size=4096, sse_queue_policy="drop_newest")
print(ui.streams_info())
```

REST 运维入口：

| 接口 | 说明 |
|---|---|
| `GET /api/streams` | 查询 SSE 背压配置与统计（订阅者数 / 丢弃事件数 / 各连接缓冲深度） |
| `POST /api/streams` | 热改变：`{"sse_queue_size": 2048, "sse_queue_policy": "drop_oldest"}` |
| `GET /api/status` | `sse_queue_size` / `sse_queue_policy` / `sse_dropped_total` 字段 |

背压策略语义：

| 策略 | 缓冲满时行为 | 适用 |
|---|---|---|
| `drop_oldest`（默认） | 丢最旧事件，客户端自动降级但**不掉线** | 展示型前端（聊天流） |
| `drop_newest` | 丢最新事件，保持旧状态 | 「状态同步」型消费者 |
| `unlimited` | 不限制（0.8 旧行为） | 明确知道客户端都会消费时 |

缓冲大小 `sse_queue_size=0` 表示不限制。`sse_batch`（默认 32 条）与
`sse_batch_interval`（默认 0.05s）控制帧批量写出粒度：越大系统调用
越少、单事件延迟越高，按流量特征权衡。

**反向代理注意**：SSE 响应已带 `X-Accel-Buffering: no`（nginx 不攒批）；
代理层超时（proxy_read_timeout）应 > 15s（库内心跳周期）。

**循环调参**：`config={"loop": {...}}` 与
`NORPAGENT_MAX_WORKERS` / `NORPAGENT_SUBMIT_POLL` 见 4.3 与 14.1。
任务完成感知延迟 = poll_interval（默认 50ms）；CPU 敏感环境调大，
延迟敏感环境调小（下限 1ms）。

**线程模型**：Web 前端每 SSE 连接一个 HTTP 线程（标准库
socketserver 模型）；任务经 `WebFrontend._gate` 串行进入引擎，由
循环工作池执行。事件发布路径 O(订阅者数) 且每订阅者摊销 O(1)
（有界 deque + 空→非空一次 notify），万级并发推送不放大锁争用。

**监控指标**（`GET /api/streams`）：`subscribers`（在线订阅者）、
`dropped_total`（累计背压丢弃——持续增长说明客户端过慢，应调大
缓冲或排查消费端）、`max_buffered`（各连接缓冲深度峰值）。

### 14.4 验证方式

库内验证脚本（`test/`）：

```bash
python test/_verify_embedded_concurrency.py   # 34 项：极简装配/懒导入/e2e/并发正确性/吞吐
python test/_smoke_webui_09.py                # WebUI：懒磁盘 I/O/页面缓存/背压热改变/HTTP 并发/SSE
python test/_smoke_embedded.py                # embedded 预设 e2e
```

覆盖要点：`install_core` 组件白名单与黑名单、`import norpagent.builtin`
不拉 sqlite3 / http.server、embedded 默认 headless + mock 回落、
环境变量收紧工作池、EventBus 写时复制并发订阅/退订正确性、
submit 中断唤醒、SSE 三策略与热改变、40 并发 HTTP、断连回收。

---

## 第 15 章　工作回退：快照 / Undo / Redo / 崩溃救援 / 安全模式

> 一句话：Agent 工作可回退——Web UI / 快捷键 / API 一键撤销与恢复
> （Undo / Redo）；浏览全部历史快照一键回退任意版本（Rollback）；
> 主程序完全无法启动时用独立 CLI 崩溃救援（并提示最后一次正常
> 工作的快照）；安全模式只加载最小化内核，保住核心回退能力。

### 15.1 概念与四个层次

| 层次 | 能力 | 入口 |
|---|---|---|
| Undo / Redo | 撤销 / 恢复最近一次操作，进程内即时生效 | Web UI 按钮 / Ctrl+Z / Ctrl+Shift+Z / `np.undo()` / `np.redo()` |
| Rollback | 浏览全部历史快照，回退到任意版本 | Web UI「回退」面板 / `np.rollback(id)` |
| Crash Rescue | 主程序无法启动时回退快照，提示最后正常快照 | `norpagent-rescue`（独立 CLI，纯标准库） |
| Safe Mode | 只加载最小化内核（跳过全部插件），保留核心回退能力 | `np(safemode="on")` / CLI `--safe-mode` |

快照内容（模式 A，默认）：架构层全部槽位配置（模式 / 模型 / 工具 /
会话 / 沙箱 / 前端 / 插件目录 / 安全级别…）+ 引擎运行时参数 + WebUI
设置文件内容 + 自定义提供者数据。敏感键（api_key / token 等）**脱敏
后**才落盘。不可序列化的值（实例 / 类 / 函数）记录类型标记，回放时
跳过并提示（诚实降级，不伪造状态）。

快照模式 B：`np(snapshot_sessions="on")` 额外把会话存储文件复制进
快照附件，回退时整文件恢复（会覆盖回退之后的对话记录）。

存储位置：默认 `~/.norpagent/snapshots/`（manifest.json 时间线 +
snap/ 每快照一个 JSON + attachments/ 会话附件 + rollback_target.json
救援回退目标）。可用环境变量 `NORPAGENT_SNAPSHOT_DIR` 或
`np(snapshot_dir=...)` 覆盖，运行中可用 `np.set_snapshot_dir()`
热切换存储目录（显式编程调用优先级最高）。自动快照默认开启
（`np(snapshots="off")` 关闭），自动修剪保留最近 200 个。

### 15.2 快照与 Undo / Redo

自动快照时机：启动基线、每次系统状态变更（`np.remount` / WebUI
设置保存 / 插件安装 / 模式切换）之后。手动快照：Web UI「回退」面板
「手动快照」按钮或 `np.snapshot_system("说明")`。

```python
import norpagent as np

np()                                          # 启动（自动打基线快照）
np.snapshot_system("装插件之前")               # 手动快照
# …做了几次变更（remount / 设置保存 / 装插件）…
np.undo()                                     # 撤销最近一次操作（进程内即时）
np.redo()                                     # 恢复撤销
np.rollback("20260818T230101_ab12cd")         # 回退到任意快照
np.rollback()                                 # 回退到最后一次正常快照
np.list_snapshots()                           # 时间线（is_current / is_last_good）
np.mark_good_snapshot("<id>")                 # 手动标记「正常」
```

语义要点：

1. **指针模型**：时间线 + 当前指针。Undo = 应用上一快照并前移指针，
   Redo = 应用下一快照；撤销后做新操作会**截断 redo 分支**（标准撤销
   语义）。
2. **进程内即时生效**：回放复用 remount 热挂载管线——组件槽位下一次
   run 生效、装配槽位热重建 AgentRuntime、HTTP 端口不变；与快照值
   相同的槽位跳过重挂（避免无谓的前端重启）。回放期间的变更**不会**
   再次自动快照（防撤销自拍覆盖 redo 分支）。
3. **Web UI**：左侧「回退」页 + 按钮 + 快捷键（Ctrl+Z / Ctrl+Shift+Z，
   输入框聚焦时不拦截）；后端 API `GET /api/snapshots`、
   `POST /api/snapshots {action: capture|undo|redo|rollback|mark_good}`。
4. **「最后正常」自动标记**：引擎启动成功后 30 秒健康期（或首个任务
   完成）自动 mark-good；也可手动标记。回退面板与救援 CLI 中 ★ 即
   最后正常快照。

### 15.3 自定义快照内容（钩子式扩展）

```python
from norpagent import recovery

# 采集钩子 + 恢复钩子（可选）
recovery.register_snapshot_provider(
    "my_state",
    capture=lambda engine: {"mark": 42},          # 任意 JSON 值
    restore=lambda engine, value: apply_mark(value),
)
# 注册即生效：后续所有快照都带 providers.my_state 段，
# 回放时调用 restore。重名覆盖；unregister_snapshot_provider 注销。
```

### 15.4 崩溃救援（独立 CLI，纯标准库）

`norpagent-rescue` 刻意**只依赖标准库**（读 / 写快照 JSON 与 WebUI
设置文件），主程序哪怕因配置错误或插件问题完全无法启动，救援工具
依然可用：

```bash
norpagent-rescue list                        # 时间线（★ = 最后正常）
norpagent-rescue show <id>                   # 查看快照内容（已脱敏）
norpagent-rescue rollback <id>               # 回退：恢复 WebUI 设置 + 写回退目标
norpagent-rescue rollback --last-good        # 一键回退到最后正常快照
norpagent-rescue mark-good <id>              # 手动标记「正常」
norpagent-rescue prune --keep 50             # 只保留最近 N 个
```

回退后下一次 `norpagent` / `np()` 启动**自动消费**回退目标
（rollback_target.json，消费后删除）：文件级恢复（WebUI 设置 /
会话文件）立即执行，快照槽位配置合并进本次启动——**本次显式给出
的参数优先**（救援是兜底，不覆盖用户的自觉选择）。启动失败时 CLI
与 np() 都会打印自救指引（安全模式 + 救援命令）。

### 15.5 安全模式（Safe Mode）

入口：`np(safemode="on")`、CLI `norpagent --safe-mode`。未填写默认
不进入安全模式（任何非 on 值都不触发）。

行为（只加载最小化内核）：

1. **跳过全部插件目录**（插件是最可能的启动失败源）；
2. **强制 minimal 预设**（忽略用户给的 preset / plugins / security
   槽位参数）；
3. **不读 WebUI 设置文件**（坏配置可能就是上次崩溃的原因；纯内存
   运行，保存时也不再落盘）；
4. **保留核心回退能力**：Web UI「回退」面板与 `/api/snapshots`、
   `norpagent-rescue` 全部可用——启动后即可回退到任意正常快照，
   修复后正常重启。

```python
import norpagent as np
np(safemode="on")          # 最小化内核 + Web 回退面板
```

```bash
norpagent --safe-mode      # CLI 等价入口
```

### 15.6 人类救援：手动工具接管 API（模型失效时）

**场景**：模型提供方宕机 / API Key 失效 / 模型输出损坏——Agent 的
「大脑」不可用，但它的「手」仍然是好的：工作区文件、沙箱、上下文
库、任务队列都还活着。人类救援（Human Rescue，v0.9.3）把**全部
20 个内置工具**以人工可操作的形式暴露出来：操作者手动传参
（传入），读取原始执行结果（传出），在模型恢复前继续推进工作。

**设计原则**：

1. **与模型走完全相同的执行路径**——手动调用与模型发起调用共用
   同一个 `tool.run(args, ctx)` 与 `RunContext`（registry / sandbox /
   session / scheduler / context_store / project_manager 一个不少），
   写入同一份状态：文件落在同一个 workspace、`context_add` 写入
   同一份上下文库、`task_submit` 进入同一张任务队列；
2. **不加载任何插件**（插件是最可能的故障源）；
3. **硬超时 + 取消信号**：每次调用在独立工作线程执行，超时即弃置
   （daemon 孤儿线程，与模型调用超时同一模式），并置位取消事件——
   沙箱强杀子进程树、流式循环尽早退出；
4. **默认仅监听 127.0.0.1**，可选 Bearer token——这是真实执行命令 /
   写文件的端点，绝不暴露到本机之外。

四种入口：

```bash
norpagent-rescue tools                              # 列出全部 20 个工具的清单
norpagent-rescue tool-call echo --args '{"text":"ping"}'
norpagent-rescue tool-call exec_cmd --args '{"command":"git status"}' --timeout 30
norpagent-rescue manual                             # 交互式手操台（人就是模型）
norpagent-rescue serve --port 8799                  # HTTP API + 操作员页面
norpagent-rescue serve --token my-secret            # 带 Bearer 认证
```

#### 15.6.1 交互式手操台（manual）

```text
rescue> echo {"text": "ping"}                        # <工具名> <JSON 参数>
rescue> {"tool": "file_list", "args": {}}            # JSON 对象形式
rescue> /tools                                       # 列出全部工具
rescue> /exit                                        # 退出
```

与 CLI 快照命令（list / rollback 等）的边界：`rescue.py` 模块顶层
**依然只依赖标准库**；`tools / tool-call / manual / serve` 在命令
函数内部惰性导入框架（`norpagent.rescue_api`）——主程序坏到完全起
不来时，快照回退仍然可用；框架能导入时，手操接管才可用。

#### 15.6.2 HTTP API 与操作员页面

`norpagent-rescue serve` 启动零依赖 HTTP 服务（stdlib
ThreadingHTTPServer），浏览器打开根路径即得操作员页面（内联 HTML/
JS，无任何外部资源）：下拉选工具 → 自动展示 schema 与必填参数 →
手填 JSON 参数与超时 → 调用 → 原始结果 + 历史记录。

| 端点 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 操作员页面（内联 HTML） |
| `/api/health` | GET | 状态 / 工具数 / workspace 根 |
| `/api/tools` | GET | 全量清单：`{tools:[{name,description,parameters,required,category}]}` |
| `/api/tools/<name>` | GET | 单个工具 schema |
| `/api/tools/call` | POST | body `{"tool":"echo","args":{...},"timeout":N,"params":{...}}` |
| `/api/tools/<name>/call` | POST | body `{"args":{...},"timeout":N}` |

```bash
curl -s http://127.0.0.1:8799/api/tools/call \
  -H "Content-Type: application/json" \
  -d '{"tool":"context_search","args":{"query":"marker"}}'
```

响应统一为：

```json
{
  "ok": true, "tool": "echo", "task_id": "1a2b3c4d5e6f",
  "output": "[echo] ping", "error": "", "success": true,
  "timed_out": false, "duration_ms": 0.4
}
```

语义：`ok=false` 但 HTTP 200 = 工具执行失败（参数错 / 业务失败）；
HTTP 404 = 工具未注册；400 = JSON 请求体非法；401 = token 缺失或
错误（仅当配置了 token）；413 = 请求体超过 1 MB。超时弃置返回
`timed_out=true`（HTTP 200，调用者无需再等）。

#### 15.6.3 环境组装与默认值

`RescueToolEnvironment` 用 `install_defaults()` 装配全量内置组件
（与 standard 预设同源），并在环境生命周期内共享：

| 组件 | 默认 | 说明 |
|---|---|---|
| 沙箱 | subprocess | 每条命令独立子进程；手动调用同样受路径安全约束 |
| 会话 | memory | 固定救援会话（rescue-manual） |
| 调度器 | **persistent** | task_* 全部工具可用；默认共享主程序任务库 `~/.norpagent/tasks.db`——可直接查看模型宕机前提交的任务 |
| 上下文库 | fts5 | 默认共享 `~/.norpagent/context.db`；`--context-db` 可隔离 |
| 项目管理 | basic | `project_status` 可用 |
| workspace | 当前目录 | `--workspace` 指定；file_* 的路径安全边界随之移动 |

注意：persistent 调度器意味着 `norpagent-rescue tools` 也会落盘
创建任务库（若不存在）。希望完全无盘时传 `--scheduler simple`
（task 查询类工具会如实返回「当前调度器不支持查询」）。

#### 15.6.4 隔离与安全边界

- **无模型、无插件、无钩子**：`before_tool_call` 等钩子不介入
  （救援环境不订阅任何钩子，操作者即最终审批人）；
- **路径安全照常生效**：`file_*` 的绝对路径 / `..` 穿越拒绝、
  `run_python` 的 AST 静态预检（禁 import / 禁魔法属性）照常；
- **超时双层兜底**：工具自带超时（exec_cmd 最大 300s、
  run_python 的 `ptc_timeout`）+ 环境级硬超时（默认 300s，HTTP
  body 或 `--timeout` 可调）——单次调用超时弃置线程并置位取消
  事件，不影响后续调用；
- **并发安全**：环境线程安全（组件内部均有锁），多个 HTTP 请求
  可并行手操；上下文库 / 任务库共用同一连接与锁。

#### 15.6.5 程序化嵌入

```python
from norpagent.rescue_api import RescueToolEnvironment, RescueToolAPI

env = RescueToolEnvironment(workspace_root=".", context_db="./rescue.db")
print(env.call_tool("echo", {"text": "ping"})["output"])

api = RescueToolAPI(env, port=0, token="secret")   # port=0 随机端口
port = api.start()                                 # 返回实际端口
api.shutdown()
```

应用侧也可在模型健康检查失败后自动拉起救援服务（例如把
`RescueToolAPI` 挂在现有进程里），供值班人员接管。

#### 15.6.6 与其他救援层的关系

| 层级 | 目标 | 入口 |
|---|---|---|
| 快照回退 | 配置/插件坏了，回退到正常状态 | `norpagent-rescue rollback --last-good` |
| 安全模式 | 起不来也要保留回退能力 | `np(safemode="on")` / `--safe-mode` |
| **人类救援** | **模型死了，人替模型干活** | `norpagent-rescue tools / tool-call / manual / serve` |

三者互补：先回退（或安全模式）救配置，再用手操接管推进工作，模型
恢复后由 Agent 从同一份状态（文件 / 上下文库 / 任务队列）继续。

---

## 第 16 章　库集成示例

### 16.1 FastAPI 集成

```python
import norpagent as np
from fastapi import FastAPI

np(preset="standard", frontend="norpagent.frontends.headless:HeadlessFrontend")
app = FastAPI()

@app.post("/chat")
def chat(text: str, session_id: str | None = None):
    result = np.current().submit(text, session_id=session_id)
    return {"content": result.final_content, "session_id": result.session_id,
            "status": result.status}
```

### 16.2 桌面应用集成（pywebview 风格）

```python
import norpagent as np

np(frontend="myapp.tray_frontend:TrayFrontend")
fe = np.current().frontend

# JS 桥接层把用户输入转发给 fe.send()；
# 事件总线订阅 on_content 把流式输出推回前端。
```

### 16.3 集成要点

1. **单例引擎**：运行中的引擎是单例，`np()` 幂等返回当前引擎；
2. **生命周期**：主循环轮询 `np.stop()`，进程退出有 atexit 兜底清理；
3. **装配观测**：`np.current().layer.describe()` 打印装配清单。

---

## 第 17 章　测试与调试

```bash
python tests/test_p1_smoke.py    # 内核/协议冒烟
python tests/test_p2_smoke.py    # 适配器/工具/会话
python tests/test_p3_smoke.py    # 上下文/调度/沙箱/安全/插件/Web
python tests/test_p4_smoke.py    # 钩子/安全/PTC/隔离
python tests/test_p5_arch.py     # 架构层/地址函数/np()/nasyncio
```

调试辅助：

```python
eng = np.current()
print(eng.state)              # 引擎状态
print(eng.layer.describe())   # 装配清单
print(eng.last_result)        # 最近任务结果
```

---

## 第 18 章　迁移指南

### 18.1 从旧版 norpagent（≤0.4）迁移

```python
# 旧写法：手工装配
reg = Registry(); install_defaults(reg); register_all_presets(reg)
agent = AgentRuntime(reg, preset="minimal")
result = agent.run("你好")

# 新写法：np() 装配（手工装配 API 保留可用）
import norpagent as np
np(preset="minimal", prompt="你好",
   frontend="norpagent.frontends.headless:HeadlessFrontend")
while True:
    if np.stop():
        break
result = np.current().last_result
```

手工装配 API（Registry / AgentRuntime / Preset）**保留可用**；
`np()` 是其声明式封装。

### 18.2 从旧桌面应用迁移

旧应用中的模块可按以下映射接入槽位：

| 旧应用模块 | 新槽位 | 接入方式 |
|---|---|---|
| `nasync_io`（自研事件循环） | `async_loop` | **已打包进库**：`norpagent.nasyncio` 即默认调度核心（原 nasync_io v2.0.0），无需自带文件；如需换实现再填地址 |
| `async_loop.AsyncAgentLoop` | `agent_runtime` | 实现 run/shutdown → 填地址 |
| FastAPI 后端 + 桌面 UI | `frontend` | 实现 Frontend 协议 → 填地址 |
| `plugin_system` | `plugins` | 目录列表直接传 |
| `sandbox_pool` | `sandbox` | `"pooled"` 或自研地址 |
| `config.json` 各开关 | 预设 params | 任务参数透传 |

### 18.3 版本兼容

- 协议模块（protocols）与内核（kernel）自 0.1 起向后兼容；
- 0.5 新增 arch / loops / frontends / runtime 四个包；
- 0.6 新增 FLOW 流程编排 / FE 前端模块 / 输入框体系 /
  智能体工具挂载（agent_tools）/ 画布管理三件套；DeepSeek
  `deepseek-chat` / `deepseek-reasoner` 已于 2026-07-24 被官方
  停用，适配器默认走 `deepseek-v4-flash`；
- 0.7 新增 Web 前端 `html` 槽位挂载参数（`;key=value` 地址子句 /
  构造函数 / 配置字典 / 运行时参数四种途径替换 `/` 路由页面），
  并修复 `;key=value` 地址子句的解析链路；新增**运行中热挂载**
  （`np.remount()` 任何槽位运行时替换，见 3.7 节）；
- 0.8 默认事件循环迁移到**自研 nasyncio 核心**（原 nasync_io
  打包进库为 `norpagent.nasyncio`）：库内**零 `import asyncio`**，
  不再依赖标准 asyncio（原因见 4.7）。默认地址改为
  `norpagent.loops.nasyncio:NasyncioLoopRuntime`；0.7 旧地址
  `norpagent.loops.std_asyncio:StdLoopRuntime` 保留为兼容垫片
  （同一实现，不 import asyncio），历史代码不失效；
- 0.9 嵌入式与超高并发专项优化（第 14 章）：`install_core()`
  极简装配与 builtin 包懒导入（`import norpagent.builtin` 不再拉
  sqlite3 / http.server）、`embedded` 预设（第六模式，默认 headless
  前端）、WebUI 构造零磁盘 I/O 与页面字节缓存、EventBus 写时复制、
  SSE 有界背压（默认 drop_oldest，可热改变）+ 帧批量写出 + 断连
  快速回收、HTTP 并发调优、submit 轮询默认收紧到 0.05s（可配）。
  行为兼容：SSE 默认缓冲上限 1024 条（慢客户端丢最旧，此前为
  无界）；`unlimited` 策略 + `sse_queue_size=0` 可还原旧行为；
- 0.9 槽位表热插拔（3.8 节）：`register_slot()` / `unregister_slot()`
  运行时注册 / 注销**自定义槽位**（`SlotSpec.applier` 声明装配逻辑，
  `remount_rebuild_agent` 声明热替换后是否热重建 AgentRuntime），
  注册即接入 `np()` 参数校验、ArchLayer 装配（connect 幂等补齐晚
  注册槽位）、`np.remount()` 热替换、`layer.describe()` 清单全管线；
  支持 `replace=True` 规格热替换；内置 18 槽位受保护（值仍可随时
  热替换）；槽位表操作线程安全。行为兼容：既有 18 槽位装配 / 热挂载
  语义完全不变；
- 0.9 工作回退（第 15 章）：快照时间线 + Undo / Redo / Rollback
  （进程内即时生效，复用 remount 热挂载管线）+ 独立崩溃救援 CLI
  `norpagent-rescue`（纯标准库，提示最后正常快照一键恢复，回退
  目标下次启动自动消费）+ 安全模式（`np(safemode="on")` / CLI
  `--safe-mode`，只加载最小化内核）；自动快照默认开启（remount /
  设置保存 / 插件安装后），敏感键脱敏落盘，支持自定义快照提供者
  与快照模式 B（含会话数据文件）；
- 删除性变更只会出现在大版本。

---

## 第 19 章　常见问题（FAQ）

**Q1：`np()` 会阻塞吗？**
不会。引擎在后台线程运行，主线程继续执行——这正是
`while running: if np.stop()` 模式存在的原因。

**Q2：`np.stop()` 什么时候变 True？**
引擎 STOPPED：单次任务跑完、前端 `/exit`、显式 `shutdown()`、
或任何 `request_stop()`。没有引擎时恒为 True。

**Q3：怎么传模型 API Key？**
```python
np(model="openai_compat", model_name="deepseek-v4-flash",
   base_url="https://api.deepseek.com/v1", api_key="sk-...")
```
`model_name / base_url / api_key` 是模型快捷参数：当模型是内置适配器名
时自动重新构造提供者（与 CLI 行为一致）；或直接设置环境变量
`OPENAI_API_KEY`；或传构造好的提供者实例 `np(model=MyProvider())`。

**Q4：地址字符串会不会造成任意代码执行？**
会。地址字符串指定要加载的模块，地址值由库的使用者在代码中传入。
外部插件加载经过签名校验、AST 审计与导入限制。

**Q5：我能同时跑两个不同的 Agent 吗？**
运行中的引擎是单例。需要多实例时直接用手工装配 API：
`Registry() + AgentRuntime(...)`，不受单例约束（第 17.1 节）。

**Q6：循环系统替换后钩子还工作吗？**
工作。钩子挂在事件总线（底层最小内核）上，与循环系统无关。

**Q7：`np(async_loop=...)` 和 `np.nasyncio(...)` 有什么区别？**
没有区别，同一条路径；前者是槽位写法，后者是架构函数写法。

**Q8：`/flow` 画布、FE 前端模块、输入框体系是什么？**
`/flow` 是独立前端分类「模块流程」：画布图自动保存并可用
「应用到智能体」热切换 front 聊天行为。FE = 拖入 `.html/.js/.ts`
文件注册的前端模块（托管 `/fe/<name>`，独立配置作用域）。输入框
体系 = 一切需要输入的地方都是输入框：FE / 全局设置节点卡片上每个
配置项一行输入框、model/tool/sandbox 等节点卡片底部带值输入框带、
所有模型字段均为可手输输入框 + datalist 提示（拉取失败也能手输）。
详见第 5.7 节与 `docs/flow.md`。

**Q9：画布怎么批量清理？deepseek-chat 还能用吗？**
画布：`Alt+拖拽` 框选 / `Ctrl+A` 全选后 `Del` 批量删除，顶栏
「清空画布」一键清空（确认后立即保存，刷新保持空白）；双击空白
注入与单击坞卡片快速注入已移除（防误触铺节点）。deepseek-chat /
deepseek-reasoner 已于 2026-07-24 被 DeepSeek 官方停用，现役为
deepseek-v4-flash / deepseek-v4-pro；旧名在提示列表、远端模型坞
与后端缓存中自动过滤（`RETIRED_REMOTE_MODELS`）。

**Q10：网页端原生工具太少，怎么接第三方自定义工具并让智能体自动调用？**
「文件即模块」接入：把声明 `__norpagent_type__ = "tool"` 的 `.py`
文件拖进 `/flow` 画布即真实注册（走插件安全管线），工具节点端口
自动来自 OpenAI function schema。要让 front 聊天里的智能体自动调用
（tool calling）：① `/flow` 模块坞工具卡点 `AGENT` 徽章挂载；
② WebUI 设置弹窗「🧰 智能体工具」清单勾选。二者都经
`POST /api/agent/tools`（配置键 `agent_tools` /
`agent_tools_explicit`）热应用 `preset.tools`，下一次 run() 即生效，
无需重启。详见第 5.7 节「智能体工具挂载」与 `docs/flow.md` 第 9 节。

**Q11：启动后能换模型 / 换前端 / 换模块吗？（运行中热挂载）**
能。`np.remount(slot=value)` 在引擎运行中替换任意槽位：组件槽位
（model / tools / hooks / security / plugins）下一次 run() 生效；
装配槽位（session / sandbox / scheduler / ui / agent_runtime /
preset / context_store / project_manager）触发 AgentRuntime 热重建；
frontend / async_loop 停旧启新；logger / storage / error_handler
即时更新。字符串地址在重挂载前自动失效模块缓存与 .pyc，
「改模块文件 → np.remount(model="myapp.model:create")」即热重载。
重复挂载的架构级订阅先退订再重挂，不叠加。详见第 3.7 节。

**Q12：Ctrl+C 为什么之前会失灵？现在怎么保证打断得了？**
两个根因（详见第 4.6 节）：① Windows 上主线程阻塞在一次性
`Event.wait()`（`WaitForSingleObject`）里收不到 SIGINT——pending
interrupt 只在字节码边界被检查；现在 `submit()` 改为轮询等待
（每 ≤poll_interval 秒一次边界，默认 0.05s，可配），Ctrl+C 即刻
冒出 `KeyboardInterrupt`。
② 卡在沙箱 `subprocess` / HTTP 里的工作线程杀不死，且标准线程池
（ThreadPoolExecutor，asyncio 的默认执行器也是它）在解释器退出时
被强制 join——任务不结束进程就僵住；现在工作池用裸守护线程
（退出不 join），且 Ctrl+C / 引擎停止会置位任务的取消事件：
PTC 沙箱立即强杀子进程、池化沙箱杀进程树、模型流式中断、
Agent 轮次边界以 stopped 收尾。任务执行体可用
`norpagent.loops.cancel.cancel_requested()` 主动响应取消。

**Q13：norpagent 依赖标准 asyncio 吗？**
不依赖。0.8 起库内**零 `import asyncio`**：默认调度核心是打包
进库的自研异步 IO 库 `norpagent.nasyncio`（原 nasync_io），底层
只用 threading / selectors / socket 等非 asyncio 标准模块。
声明、原因与验证方式详见第 4.7 节。

**Q14：嵌入式设备 / 超高并发服务器怎么部署？（0.9）**
- **嵌入式**：`install_core()` 自建注册表（不导入 sqlite3 /
  http.server）+ `build_embedded_preset()`，或直接
  `np(preset="embedded")`（默认 headless 前端、mock 回落）；工作
  线程数用 `NORPAGENT_MAX_WORKERS=1`（或 `config={"loop":
  {"max_workers": 1}}`）收紧，轮询用 `NORPAGENT_SUBMIT_POLL` 放宽。
- **超高并发**：SSE 每连接有界缓冲默认 1024 条、慢客户端丢最旧
  （`drop_oldest`），启动时 `np(config={"web": {"sse_queue_size":
  2048}})` 配置，运行中 `WebUI.set_sse_queue(...)` / `POST
  /api/streams` 热改变；帧批量写出（默认 32 条 / 50ms）降低系统
  调用；EventBus 写时复制免每事件列表复制。完整说明见第 14 章。

**Q15：框架没有我需要的槽位怎么办？（槽位表热插拔，0.9）**
自己注册一个：`register_slot(SlotSpec(name=..., string_semantics=...,
applier=...))`。注册即接入 `np()` 参数校验、装配、`np.remount()`
热替换、`layer.describe()` 清单全管线；applier 拿到解析后的槽位值
与 components / extras / overrides / meta 四个可变容器，可注册通用
组件（`remount_rebuild_agent=True` 时热替换后热重建 AgentRuntime）、
挂事件订阅（用 meta 记录退订，保证重入安全）、或向引擎提供附加
对象。内置 18 槽位受保护不可覆盖 / 注销，其值可随时 `np.remount`
热替换。完整契约见 3.8 节。

**Q16：怎么撤销一次配置变更 / 回退到之前的状态？（工作回退，0.9）**
三步走：进程内 `np.undo()` / `np.redo()`（Web UI Ctrl+Z /
Ctrl+Shift+Z 或「回退」面板按钮，即时生效）；回退任意版本
`np.rollback("<快照id>")`（`np.list_snapshots()` 浏览时间线，
`np.rollback()` 无参 = 最后正常快照）；主程序起不来了用
`norpagent-rescue rollback --last-good`（纯标准库 CLI，下次启动
自动应用），或 `norpagent --safe-mode` / `np(safemode="on")`
只加载最小化内核修配置。快照默认存 `~/.norpagent/snapshots/`，
敏感键脱敏，自动快照默认开启（可 `np(snapshots="off")` 关闭）。
完整语义见第 15 章。

---

## 附录 A　架构槽位速查表

| 槽位 | 字符串语义 | 默认 | 工厂上下文键 |
|---|---|---|---|
| async_loop | address | NasyncioLoopRuntime（自研 nasyncio 核心；config.loop 调 max_workers / poll_interval） | layer, config |
| agent_runtime | address | AgentRuntime | registry, preset, ui, task_params, layer, config |
| model | name_or_address | 预设声明 | layer, config |
| tools | name | 预设声明 | - |
| session | name_or_address | 预设声明 | - |
| sandbox | name_or_address | 预设声明 | - |
| scheduler | name_or_address | 预设声明 | - |
| context_store | address | 预设声明 | layer, config |
| project_manager | address | 预设声明 | layer, config |
| hooks | literal | 标准 9 层 | - |
| security | literal | 不开启 | - |
| plugins | literal | 不加载 | - |
| frontend | address | prompt / embedded→headless，否则 web | layer, config |
| ui | name | 预设声明 | - |
| preset | name | standard | - |
| logger | literal | logging.getLogger("norpagent") | - |
| storage | literal | ~/.norpagent | - |
| error_handler | literal | 记录日志 | - |

> 运行中热挂载（3.7）：所有槽位均可 `np.remount(slot=value)` 替换。
> `agent_runtime` 为 `defer_factory` 槽位（工厂推迟到引擎装配期调用）。
> 槽位表热插拔（3.8）：`register_slot()` 可注册自定义槽位加入本表。

## 附录 B　9 层钩子速查表

| 层 | 钩子 | 可变 | 关键参数 |
|---|---|---|---|
| L1 生命周期 | on_agent_init / on_agent_shutdown | - | preset |
| L2 任务 | on_task_start / on_task_done / on_task_stopped / on_task_error / on_task_timeout | - | task_id, session_id |
| L3 输入 | before_input / after_input / on_user_input_required | before | user_input, params, question |
| L4 会话 | before_session_create / after_session_create / before_message_append / after_message_append | before | title, message |
| L5 组装 | before_build_messages / after_build_messages | 两者 | system_prompt, messages |
| L6 步骤 | before_step / after_step | before | step, messages |
| L7 模型 | before_model_call / after_model_call / on_reasoning / on_content / on_event / on_usage_update | before/after_model_call | messages, output, params |
| L8 工具 | before_tool_call / after_tool_call / on_tool_error | 两者 | tool_name, args, result |
| L9 定型 | before_result / after_result | 两者 | result |

> 完整负载键与 29 钩子全表见 9.1 节；可变钩子的完整返回语义（HookVeto 收尾 / 改写规则）见 9.3 节。
> 插件加载管线另有 8 个钩子（PLUGIN_PIPELINE_LAYER），见 11.4 节。

## 附录 C　公开 API 索引

```python
# 模块入口
np()                      # launch()
np.stop()                 # 生命周期轮询
np.nasyncio(address=...)  # 事件循环架构函数（np.nasyncio 绑定自研核心模块，可调用）
np.current() / np.submit() / np.shutdown()
np.remount(model=..., ...)   # 运行中热挂载：任何槽位均可替换

# 工作回退（第 15 章）
from norpagent.recovery import (snapshot_system, undo, redo, rollback,
                                list_snapshots, mark_good, last_good_id,
                                register_snapshot_provider, set_snapshot_dir,
                                prune, RecoveryError)
np.snapshot_system("说明")        # 手动快照（顶层便捷入口）
np.undo() / np.redo()             # 撤销 / 恢复（进程内即时生效）
np.rollback("<id>")               # 回退到任意快照（缺省 = 最后正常）
np.mark_good_snapshot("<id>")     # 标记「正常」
np(safemode="on")                 # 安全模式：只加载最小化内核
np(snapshot_dir=..., snapshots="off", snapshot_sessions="on")  # 快照配置
# 崩溃救援：norpagent-rescue list|show|rollback|mark-good|prune
# 人类救援（15.6）：norpagent-rescue tools|tool-call|manual|serve
from norpagent.rescue_api import (RescueToolEnvironment, RescueToolAPI)
env = RescueToolEnvironment(workspace_root=".", context_db="./rescue.db")
env.call_tool("echo", {"text": "ping"})     # 手动传参 + 读取原始结果
api = RescueToolAPI(env, port=0, token=...) # HTTP API + 操作员页面
api.start() / api.shutdown()

# 架构层
from norpagent.arch import ArchLayer, SlotSpec, SLOT_SPECS
from norpagent.arch import resolve_address, call_factory, AddressError
layer.remount(slot, value)  # 架构层热挂载（模块缓存 + pyc 失效）
layer.subconfig(slot)       # 槽位附加子配置（";key=value"）

# 槽位表热插拔（3.8）
from norpagent.arch import (register_slot, unregister_slot, SlotError,
                            all_slot_names, snapshot_slots, is_builtin_slot)
register_slot(SlotSpec(name=..., string_semantics=..., applier=...,
                       remount_rebuild_agent=...))   # 注册自定义槽位
register_slot(spec, replace=True)   # 热替换自定义槽位规格
unregister_slot(name)               # 注销自定义槽位

# 循环系统
from norpagent.loops import (nasyncio, LoopRuntime,
                             NasyncioLoopRuntime, StdLoopRuntime)
from norpagent.loops.cancel import cancel_requested, current_cancel_event
loop.interrupt()   # 请求取消全部在途 submit 任务（引擎停止路径）

# 自研异步核心（已打包进库，不依赖标准 asyncio）
import norpagent.nasyncio as core
core.EventLoop / core.Future / core.Task        # 自研类型
core.sleep / core.wait_for / core.ensure_future # 工具协程
core.run_coroutine_threadsafe(coro, loop)       # 跨线程提交协程
core.Event / core.Lock / core.Condition         # 同步原语
# 救援级底层循环控制（第 24 章）：
#   loop.abort_main()        # 强停：向主任务注入 CancelledError（外部线程可调）
#   loop.call_soon_threadsafe(cb)  # 跨线程投递 + 自管道唤醒
#   loop.stop() / loop.run_forever() / loop.run_until_complete(coro)
#   loop.call_later(delay, cb)     # 定时回调（仅循环线程内调用，同 asyncio 契约）

# 前端
from norpagent.frontends import (Frontend, ConsoleFrontend,
                                 HeadlessFrontend, WebFrontend)

# 运行时
from norpagent.runtime import (launch, current, stop, submit,
                               shutdown, NorpEngine, EngineState, EngineError)
# 任务级槽位注入（3.9）：submit(text, slot_overrides={...})
#   engine.submit("任务", slot_overrides={"model": "anthropic", "tools": [...]})
#   np.submit("任务", slot_overrides={"session": {"name": "memory", "persist": True}})

# 内核（手工装配，等价保留）
from norpagent import (Registry, EventBus, Preset, AgentRuntime,
                       RunResult, install_defaults, install_core,
                       register_all_presets, build_embedded_preset)
# install_core(reg)：嵌入式极简装配（无 sqlite3 / http.server 依赖）
# build_embedded_preset()：嵌入式预设（第六模式）

# 安全 / 钩子 / 插件
from norpagent import safe, SafetyKit, SecurityContext
from norpagent import hooks                      # 钩子系统（HookSystem）
from norpagent.hooks import (Hook, BoundHook, HookLayer, HookSystem,
                             HookVeto, get_default_system,
                             before_input, before_model_call,
                             before_tool_call, after_tool_call, ...)  # 29 个标准钩子
from norpagent.plugins import (PluginSystem, PluginLoader, PluginInfo,
                               install_plugin_dirs, PLUGIN_PIPELINE_LAYER,
                               before_plugin_load, after_plugin_register, ...)
from norpagent.plugins.isolation import ProcessIsolationManager, ProcessPluginHost
from norpagent.security import (scan_message, harden_system_prompt,
                                ApprovalPolicy, NetworkPolicy, SourceAuditor,
                                SignatureVerifier, generate_keypair, sign_plugin_file)

# Web UI：页面挂载与热替换（5.4）+ SSE 背压（超高并发，第 14.3 节）
from norpagent.builtin.ui.web import WebUI
ui = WebUI(port=8787, html="/path/to/my.html",
           flow_html="/path/to/flow.html")     # / 与 /flow 页面整体替换
ui = WebUI(port=8787, sse_queue_size=2048,
           sse_queue_policy="drop_oldest")
ui.mount_page("flow", "/path/to/new-flow.html")  # 运行中热换页（不重启服务）
ui.mount_page("flow", None)                      # 卸载挂载，回落库内置
ui.page_bytes("flow")                            # 当前 /flow 页面字节
ui.set_sse_queue(4096, "drop_newest")   # 运行中热改变（POST /api/streams 等价）
ui.streams_info()                        # 订阅者数 / 丢弃事件数 / 缓冲深度
# WebFrontend 同构入口：frontend.mount_page(page, html)
# remount 页面热替换键（v0.9）：
#   np.remount(flow_html="/path/to/new-flow.html")  # /flow 立即换页
#   np.remount(html="/path/to/new-front.html")      # / 主页面立即换页
#   np.remount(flow_html=None)                      # 卸载挂载，回落库内置
# frontend 槽位 HTML 路径直挂（v0.9）：
#   np(frontend="/path/to/my.html")  ==  np(frontend="...WebFrontend;html=/path/to/my.html")
```

---

## 第 20 章　模块流程编排（FLOW）

> 本章对应代码：`norpagent/flows/__init__.py`（1535 行，框架最大的编排内核之一）。
> 5.7 节讲了 `/flow` 页面的前端形态；本章深入其**内核**：注册表快照、
> 文件即模块、拓扑执行与智能体联动。

### 20.1 定位与总览

「模块流程」（`/flow`）不是动画演示，而是用**真实注册组件**执行画布图：

```
build_snapshot(registry, agent)   注册表快照：模型 / 工具 / 会话 / 沙箱 /
                                 调度器 / 插件 / 预设 / 钩子
                                 → 前端「核心模块坞」按真实组件渲染卡片
ModuleWorkspace.register(...)    「文件即模块」：拖入 .py 走完整安全管线
                                 （签名校验 → AST 审计 → 导入限制 → 注册）
FlowRunner                       按画布图（节点 + beam）拓扑执行，
                                 进度经 flow.* 事件经 SSE 推送
```

三个核心类 / 函数：

| 名称 | 职责 |
|---|---|
| `build_snapshot(registry, agent)` | 把注册表当前状态序列化为快照 dict，驱动前端模块坞 |
| `ModuleWorkspace` | 流程模块的落盘工作区：注册 / 加载 .py / .json / .yaml 模块 |
| `FlowRunner` | 画布图执行器：节点 + beam 拓扑执行、零中断、事件发布 |

### 20.2 注册表快照：build_snapshot

```python
from norpagent.flows import build_snapshot

snap = build_snapshot(registry, agent)
# 包含：models / tools / sessions / sandboxes / schedulers /
#       plugins / presets / hooks（每个已注册组件一条记录）
```

- 快照是**实时**的：注册表里注册了什么，前端模块坞就显示什么；
- 每个组件附带元信息（描述 / 来源 / 端口推断），供画布渲染；
- 快照驱动「模块坞 → 拖入画布 → 实例选择」的完整交互。

### 20.3 文件即模块：ModuleWorkspace

```python
ws = ModuleWorkspace(registry, base_dir=default_modules_dir())
info = ws.register("my_node.py")      # 走插件安全管线
info = ws.register("graph.json")      # 纯描述模块（直通节点）
info = ws.register("graph.yaml")      # 同上
```

| 文件类型 | 处理方式 |
|---|---|
| `.py` | 完整安全管线：签名校验 → AST 审计 → 导入限制 → 注册进注册表；注册成功的插件钩子会逐个成为画布上的钩子节点 |
| `.json` / `.yaml` | 注册为纯描述模块（直通节点，无执行逻辑） |
| 其它 | 明确报错，由前端回退到官方模块 |

- 模块目录：环境变量 `NORPAGENT_FLOW_MODULES` 可覆盖，默认 `~/.norpagent/flow_modules`；
- 单文件大小上限 200KB（`_MAX_MODULE_SIZE`）。

### 20.4 画布图格式：normalize_graph

画布图是「节点 + beam」的 dict 结构，`normalize_graph` 负责规范化与校验：

```python
graph = {
    "nodes": [
        {"id": "n1", "type": "trigger"},
        {"id": "n2", "type": "model", "model": "openai_compat",
         "system_prompt": "...", "tools": ["echo"]},
        {"id": "n3", "type": "tool", "tool": "file_read",
         "inputs": {"path": {"from": "n4", "port": "path"}}},
        {"id": "n4", "type": "path", "value": "./readme.md"},
        {"id": "n5", "type": "output"},
    ],
    "beams": [["n1", "n2"], ["n2", "n3"], ["n3", "n5"], ["n4", "n3"]],
}
```

节点类型与执行语义（type → 真实动作）：

| 节点 | 真实动作 |
|---|---|
| `trigger` | 读取 prompt 输入，产出 start 信号 |
| `model` | 调用注册表真实模型；`tools` 端口 = 容器挂载的工具集（自动解析 schema 传给 provider）；`system_prompt` 端口 = 系统提示词（优先级：beam 值 > 输入面板 > 节点配置 > 引擎预设参数，空值不注入 system 消息） |
| `tool` | 调用注册表真实工具：每个输入端口 = 一个 schema 参数（不再是全局 query/result 黑箱） |
| `toolbox` | 工具容器：输入端口 = 成员工具参数的并集（按端口名扇出投递）；输出端口 = 每个成员的「工具名.端口名」限定名 + tools 打包端口 |
| `sandbox` | 在注册表真实沙箱里执行 code（子进程隔离） |
| `security` | 对 payload 做越狱 / 注入扫描（`norpagent.security.guard`） |
| `session` | 读写会话管理器（默认引擎会话存储） |
| `plugin` | 插件容器（members = 工具 + 钩子成员，端口并集语义）或独立插件工具执行 |
| `hook` | 触发插件的单个钩子（一个钩子 = 一个节点；可变钩子走 intercept，返回值成为节点输出） |
| `other` | 直通（payload 原样转发） |
| `output` | 汇总最终结果 |
| `path` | 路径模块：产出经过公共路径安全校验（拒绝绝对路径 / `..` 穿越）的相对路径值；空值 = 工作区根目录 `.` |
| `file` | 文件模块：已注册为插件则按插件执行，否则直通 |

### 20.5 FlowRunner：拓扑执行与零中断语义

```python
runner = FlowRunner(graph, registry, agent, publish=on_event, workspace=ws)
result = runner.run(prompt="...", session_id="...", params={...})
```

关键设计：

1. **拓扑执行**：按 beam 依赖关系确定执行顺序，每个节点独立 `try/except`；
2. **零中断语义**：单节点失败记录 `error` 但不中断整条链路（其它节点照常执行）；
3. **事件发布**：进度经 `publish` 回调以 `flow.*` 事件推送，Web UI 复用 SSE 通道（`/events`）实时送达浏览器；
4. **停止支持**：`runner.request_stop()` 在节点边界生效；
5. **取消传播**：节点执行同样检查 `params["_cancel_event"]`，引擎停止 / Ctrl+C 可尽早退出。

### 20.6 流程与智能体联动（应用到智能体）

画布图可以「应用」为主界面的执行引擎：

```python
# Web UI 侧（/flow 页面「应用到智能体」按钮）：
ui.flow_save(graph, activate=True)    # 保存并激活
_active = ui._active_chat_flow()      # 当前激活的流程（未激活返回 None）

# 激活后，主界面聊天任务按流程执行：
ui._run_flow_task(prompt, session_id, task_params)
# 流程执行结果写入会话历史（_append_flow_history），与普通任务一致
```

即：**普通聊天 → 画布编排 → 会话历史**全链路打通。

### 20.7 Web UI 集成与 API

| API | 作用 |
|---|---|
| `flow_snapshot` | 注册表快照，驱动 `/flow` 页面模块坞与实例选择 |
| `flow_run` | 启动一次画布图执行（后台线程，进度经 SSE 推送） |
| `flow_stop` | 停止运行中的流程（节点边界生效） |
| `flow_register` | 「文件即模块」真实注册（.py 插件安全管线 / .json / .yaml 描述） |
| `flow_save` | 保存画布图（自动保存入口），可选激活「应用到智能体」 |
| `flow_load` | 返回上次自动保存的画布图与激活状态（页面刷新后恢复） |
| `_load_flow_graph_from_disk` | 启动时恢复上次保存的流程（文件缺失 / 损坏时静默忽略） |
| `fe_read_file` / `fe_load_config` / `fe_save_config` | FE 前端模块文件读取与独立配置（互不干扰） |

### 20.8 模块目录与安全边界

- 模块目录：`NORPAGENT_FLOW_MODULES` 环境变量或 `default_modules_dir()`；
- `.py` 模块与外部插件同一安全管线（第 11 章）：签名 → 审计 → 导入限制 → 注册；
- `path` 节点强制公共路径安全校验（拒绝绝对路径 / `..` 穿越）；
- 单文件 200KB 上限 + 输出 4000 字符截断（`_MAX_OUTPUT_CHARS`），防止资源失控。

---

## 第 21 章　内置组件深度剖析

> 本章逐个剖析 `builtin/` 下的内置实现：内部机制、协议关系、选用建议。
> 全部内置组件与第三方组件同等地位——同样走注册表，可被任意替换。

### 21.1 模型适配器

| 适配器 | 文件 | 特点 |
|---|---|---|
| `mock` | `builtin/models/mock.py` | 确定性输出：内置问答对 + 引导语，零依赖，用于测试 / 基准 / 无网络环境 |
| `openai_compat` | `builtin/models/openai_compat.py` | OpenAI 兼容协议（DeepSeek / OpenAI / Qwen / vLLM / Ollama 等），`norpagent[openai]` 提供 SDK；支持 reasoning effort（`model_supports_reasoning_effort` / `normalize_effort`）、DeepSeek v4 特判（`model_is_deepseek_v4`）、思维链提取（`_extract_reasoning`） |
| `anthropic` | `builtin/models/anthropic.py` | Anthropic 协议适配器，`norpagent[anthropic]` 提供 SDK |

共同点（协议 `ModelProvider`）：

- `generate(messages, tool_schemas, params) -> ModelOutput`（含 usage）；
- 可选 `stream(...)`：流式产出 `ModelStreamChunk`（正文增量 / 思维链 / 工具调用）；
- 取消支持：适配器读取 `params["_cancel_event"]`，引擎停止 / Ctrl+C 时尽早退出流式循环；
- 凭据回落：未提供任何 Key 时装配层自动回落 `mock`（`runtime/mount.py` 的 `_has_model_credentials` 检查 `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` / `DASHSCOPE_API_KEY` / `NORPAGENT_API_KEY`）。

### 21.2 工具集（21 个内置工具）

| 分组 | 工具 | 说明 |
|---|---|---|
| P1 基础 | `echo` / `get_time` / `run_python` | 回显 / 时钟 / Python 执行（PTC 雏形） |
| P2 工程 | `file_read` / `file_write` / `file_list` / `file_delete` | 文件操作，**严格限定工作区根目录**（`pathsafe` 校验：拒绝绝对路径与 `..` 穿越，可配置根目录） |
| P2 命令 | `exec_cmd` | 命令执行，经沙箱协议（`sandbox.run_shell`），超时钳制（`_MAX_TIMEOUT`） |
| P2 联网 | `web_search` / `web_fetch` / `web_extract_links` | 联网检索；**SSRF 防护**（`is_private_url` 拒绝内网 / 元数据地址）；requests 可用则用，否则 urllib 兜底；bs4 可用则结构化提取，否则正则兜底——零依赖可用 |
| P3 上下文 | `context_add` / `context_search` / `context_list` / `context_delete` | 跨会话可检索知识库（FTS5，见 21.6） |
| P3 项目 | `project_status` | 项目管理（git 感知，见 21.7） |
| P3 任务 | `task_submit` / `task_list` / `task_status` / `task_cancel` | 长周期任务协作（persistent 调度器，见 21.5） |

工具协议（`protocols/tool.py`）：`name` / `schema()` / `run(args, ctx)`，返回 `ToolResult`；`ctx` 携带 `RunContext`（组件访问：`ctx.component("context_store")` 等）。

### 21.3 会话存储

| 实现 | 特点 | 适用 |
|---|---|---|
| `memory` | 纯内存 dict，进程结束即失 | 嵌入式 / 测试 / 单次任务 |
| `sqlite` | SQLite 持久化：schema + 消息迁移（`_MESSAGE_MIGRATIONS` 增量升级）、`close` / `clear` 生命周期 | 默认（standard 预设），重启续聊 |

协议（`protocols/session.py`）：`create_session / get_session / append_message / history / list_sessions / delete_session`。

### 21.4 沙箱

| 实现 | 机制 | 适用 |
|---|---|---|
| `subprocess` | 每次调用起子进程，简单直接 | 轻量 / 嵌入式 |
| `pooled` | **沙箱池**：复用子进程 + 并发上限 + 超时强杀进程树（`_kill_process_tree`，Windows 用 `taskkill /T`）；`PooledSandboxProvider` 管理池生命周期（create / release / discard / kill_task / close_all / stats） | 默认（standard 预设），性能与隔离平衡 |
| `isolated_python` | PTC 子进程隔离执行：`check_ptc_source` 静态校验 + 包装模板（`_WRAPPER_TEMPLATE`）+ 结果回传（`_CALLER_SRC`） | `run_python` 工具的隔离执行路径 |

协议（`protocols/sandbox.py`）：`Sandbox.run_shell / run_python / close`、`SandboxProvider.create`。

### 21.5 调度器

| 实现 | 机制 | 适用 |
|---|---|---|
| `simple` | 内存队列：submit / pending / drain | 嵌入式 / 测试 |
| `persistent` | **SQLite 持久化调度**：任务落盘（`_SCHEMA`）、终端状态（`_TERMINAL_STATUSES`）、崩溃后 `resume` 续跑、`counts` / `list_tasks` / `cancel` / `clear` | 默认（standard 预设），长周期任务协作 |

### 21.6 上下文库（FTS5）

`builtin/context/fts5.py`：SQLite FTS5 全文索引实现的跨会话知识库。

- **中文分词**：`_tokenize` 内置中文切分（bigram + 单字），不依赖 jieba——零第三方依赖；
- **查询**：`_tokenize_for_query` + `_fts5_phrase` 构造短语查询；
- API：`add / update / search / get / list / delete / clear / stats / close`。

### 21.7 项目管理（BasicProjectManager）

`builtin/projects/basic.py`：

- 项目元数据：`_META_DIR` / `_META_FILE`（meta 读写，`init / load_meta / save_meta / touch`）；
- 目录扫描：`scan`（跳过 `_SKIP_DIRS`）；
- **git 感知**：`git_status`（`git` 命令探测，无 git 时优雅降级）；
- API：`status`（供 `project_status` 工具调用）。

---

## 第 22 章　Web UI 与前端深度解析

> 对应代码：`builtin/ui/web.py`（2596 行）+ `frontends/web.py`（407 行）。
> 5.4 节讲用法；本章讲**内部机制与 API 全集**。

### 22.1 架构与线程模型

```
浏览器 ──HTTP──▶ _RobustHTTPServer（ThreadingHTTPServer 调优版）
              ├── /           主聊天页面（front.html，可热替换）
              ├── /flow       流程画布页面（norpflow.html，可热替换）
              ├── /api/*      数十个 REST 端点
              └── /events     SSE 长连接（全部事件实时推送）
                └── _SSESubscriber（有界缓冲 + 条件变量唤醒，每连接一个）

WebUI.start()        后台守护线程 serve_forever（非阻塞）
WebUI.submit()       提交任务（后台线程执行，不阻塞 HTTP）
WebUI.on_event()     接收 AgentEvent → 推给全部 SSE 订阅者 + 记录历史
```

`_RobustHTTPServer` 的调优点：断连噪声静默（WinError 10053 等）、`daemon_threads=True`、`allow_reuse_address=True`（重启立即复用端口）、`request_queue_size=256`（监听积压）、`block_on_close=False`（快速停机）。

### 22.2 REST API 总表

| 分组 | 端点（方法） | 作用 |
|---|---|---|
| 任务 | `submit` / `stop_task` | 提交任务 / 停止任务（步骤边界生效） |
| 会话 | `create_session` / `session_info` / `list_sessions` / `session_messages` / `close_session` / `set_session_title` / `set_session_workspace` | 会话全生命周期 + 消息历史 + 工作区 |
| 配置 | `get_config` / `save_config` / `reset_config` / `set_api_key` / `validate_api_key` / `first_run` | 配置面板（持久化到 `~/.norpagent/webui_config.json`，原子写入） |
| 模型 | `list_models` / `set_agent_tools` / `agent_effective_tools` / `tools_info` | 模型列表（含远端）、工具集管理 |
| 插件 | `get_plugin_dirs` / `list_plugins` / `add_plugin_dir` / `remove_plugin_dir` / `reload_plugins` | 插件目录与热重载 |
| 安全 | `get_security` / `set_security` | 安全级别读取 / 设置 |
| 监控 | `health` / `usage` / `debug_info` / `streams_info` | 健康检查 / 用量 / 调试 / SSE 背压统计 |
| 文件 | `list_fs` / `read_fs_file` / `upload_files` | 目录导航 / 文件读取 / dataURL 上传（二进制不支持） |
| 流程 | `flow_snapshot` / `flow_run` / `flow_stop` / `flow_register` / `flow_save` / `flow_load` | 画布编排（第 20 章） |
| 回退 | `recovery_handle` | 快照 / Undo / Redo / Rollback API（`/api/snapshots`） |
| 前端模块 | `fe_read_file` / `fe_load_config` / `fe_save_config` | FE 模块读取与独立配置 |

### 22.3 SSE 事件协议

- 通道：`/events` 长连接；
- 帧格式：`data: {json}\n\n`（`_encode_sse_frame`，模块级复用，避免每帧建 lambda）；
- 事件内容：与 EventBus 同名事件（`on_task_start` / `on_content` / `on_reasoning` / `after_tool_call` / `flow.*` …）序列化为 JSON 帧；
- **背压**（`_SSESubscriber`）：

| 策略 | 语义 | 适用 |
|---|---|---|
| `drop_oldest`（默认） | 缓冲满丢最旧，客户端降级不掉线 | 展示型前端 |
| `drop_newest` | 缓冲满丢最新，保持旧状态 | 状态同步型消费者 |
| `unlimited` | 无上限（旧行为） | 明确需要全量时 |

- 运行中热改变：`ui.set_sse_queue(maxsize, policy)`（对既有连接立即生效）；
- 唤醒优化：空→非空转换唤醒一次，读者每次唤醒排空缓冲（高并发减锁）；
- 断连回收：连接断开 ≤1s 内回收订阅者（写时复制替换，不阻塞发布）。

### 22.4 配置面板与持久化

- 配置项：模型（名称 / base_url / api_key / temperature 等采样参数）、工具集、插件目录、安全级别、端口 / 语言等；
- 持久化：`_save_config_to_disk` 原子写入（失败只记日志，不拖垮保存流程）；启动 `_load_config_from_disk` 恢复（文件缺失 / 损坏静默忽略）；
- 配置保存后经 `set_config_apply` 回调重新注册模型 / 插件 / 安全（`WebFrontend._apply_config`）；
- 配置快照：工作回退系统会把 WebUI 设置文件纳入快照（第 15 章），回退时 `restore_config` 恢复。

### 22.5 页面与前端模块（FE）

- 页面：`/`（front.html）与 `/flow`（norpflow.html），均可运行中热替换（`mount_page` / `np.remount(html=...)` / `np.remount(flow_html=...)`），HTTP 不重启、端口不变；
- FE 前端模块：`.html` / `.js` / `.ts` 文件（`fe_read_file` 按 mime 返回），启动后 `_scan_fe_modules` 扫描模块目录恢复列表（重启不丢失）；
- 独立配置：`fe_save_config` / `fe_load_config`（无记录时返回全局配置副本作为默认值来源，互不干扰）。

### 22.6 上传与安全限制

- 上传大小：`_MAX_JSON` / `_MAX_UPLOAD_JSON` / `_MAX_UPLOAD_FILE` 多重限制；
- 上传内容：仅文本（dataURL 解码），二进制明确不支持；
- 远端模型过滤：`filter_remote_models`（`RETIRED_REMOTE_MODELS` 退役模型名单）；
- 敏感字段：`json_safe` 序列化时对密钥类字段脱敏。

---

## 第 23 章　性能设计与基准测试

> 对应代码：`kernel/events.py`（EventBus）、`builtin/ui/web.py`（SSE / HTTP）、
> `nasyncio.py`（调度核心）、`loops/nasyncio.py`（LoopRuntime）。

### 23.1 EventBus：写时复制 + 无锁迭代

```python
# subscribe / unsubscribe：锁内创建新列表并替换引用（绝不原地修改）
self._all = self._all + [listener]
# emit / intercept：锁内取一次引用，随后无锁直接迭代
listeners = self._snapshot(event_type)   # 不复制，只取引用
for fn in listeners: fn(event)
```

- 读者持有的旧快照不会被并发写者修改——线程安全性由「不可变 + 引用替换」保证；
- 高频事件（流式 `on_content` 逐 token 推送）省去每条事件的列表复制开销；
- 实测 >160 万事件/秒（**单机单线程发布场景**）。

**基准口径（重要）**：160 万/秒为**单一发布线程 + 订阅表静态（无并发
subscribe / remount）**场景的实测数据。**无锁迭代 ≠ 无锁 emit**——
`emit` 每次仍在锁内取一次订阅表快照引用（`_snapshot`），随后才无锁
迭代。单线程发布下锁无竞争，故测得 160 万；动态热挂载（高频
subscribe / remount）下写者持锁复制列表期间，emit 会被挡在锁外
（µs 级——订阅者 n<50 时复制是微秒级，远低于 ms；仅当热挂载频率达
每秒成百上千次时才会被感知）。流式 `on_content` 的 emit 发生在 worker
线程内（自发布自迭代），不构成多线程竞争。

**「短暂看到旧表」是 COW 的线性化语义，不是缺陷**：先于 subscribe
完成的 emit 用旧表，保证「先订阅后发布」因果一致。若要彻底消除 emit
的锁获取，可改为「emit 完全不拿锁、直接读引用」（CPython 属性读天然
原子，写者只在锁内替换引用、绝不原地改，读者看到旧表或新表都是合法
快照）——当前实现保守保留读锁，属可优化项（会同步补动态热挂载混跑
基准）。

### 23.2 SSE 有界背压

- 每个连接独立缓冲：有界双端队列 + 条件变量；
- 满则按策略丢（默认丢最旧），**慢客户端不再无限吃内存**；
- 批量 flush + 断连 ≤1s 回收；`dropped` 计数可监控（`streams_info`）。

### 23.3 HTTP 并发调优

| 参数 | 值 | 效果 |
|---|---|---|
| `request_queue_size` | 256 | 监听积压放大，高并发接入不丢 SYN |
| `allow_reuse_address` | True | 重启立即复用端口（避开 TIME_WAIT） |
| `daemon_threads` | True | 请求线程守护，进程退出不挂起 |
| `block_on_close` | False | shutdown 不等连接关闭，停机更快 |

### 23.4 nasyncio 调度核心

- 一个 EventLoop 绑定一个线程；`run_forever` 在哪个线程调用，循环就属于哪个线程；
- 跨线程唤醒：线程安全队列 + socketpair 自管道（不依赖 asyncio 内部机制）；
- 取消语义：`Task.cancel()` 跨线程安全、done 回调写自管道、`loop.interrupt()` 取消全部在途任务；
- 工作池：`_DaemonPool`（守护线程，进程退出不 join），`NORPAGENT_MAX_WORKERS` 环境变量调节（嵌入式可压到 1）；
- 工作池队列**无界**：`put_nowait` 永不失败，池满时任务无限堆积、无拒绝策略、无任务时间预算（边界与卡死兜底矩阵见 4.6.4）。

### 23.5 验证方式

仓库自带全套专项验证脚本（`test/_verify_*.py` / `test/_smoke_*.py`）：

| 脚本 | 覆盖 |
|---|---|
| `_verify_install.py` / `_verify_wheel.py` | 安装与打包 |
| `_verify_js*.py` / `_verify_front.py` / `_verify_css_*` | 前端页面与资源 |
| `_e2e_webui.py` / `_e2e_shot.py` | WebUI 端到端与截图 |
| `_verify_ppt_*.py` / `_pixel_check*.py` | 演示文稿与像素校验 |
| `_final_check.py` / `_verify_coverage.py` | 整体回归与覆盖核对 |

性能基准方法建议：固定输入集 + 固定工具集（minimal 预设），对比不同模型 / 组件实现的输出质量、步数、token 消耗（第 7.3 节模型基准测试）。

---

## 第 24 章　救援模式：底层循环控制与人类接管

> 对应代码：`rescue.py`（纯标准库 CLI）、`rescue_api.py`（人类接管环境）、
> `nasyncio.py` / `loops/nasyncio.py`（循环内核与 LoopRuntime）。
> 15.6 讲**用法**（命令、端点、参数）；本章讲**原理**——救援模式与底层最小
> 主异步循环的关系：主程序不可用时如何直接控制循环、如何手动操作全部工具。

### 24.1 三层故障模型：循环、引擎、模型

救援模式按「故障发生在哪一层」决定用哪套入口：

| 故障层 | 现象 | 可用入口 | 依赖 |
|---|---|---|---|
| 模型死 | 循环 / 引擎正常，模型调用失败 | `norpagent-rescue tools / tool-call / manual / serve` | 需框架可导入（`rescue_api` 惰性加载） |
| 引擎死 | 循环可能还活着，AgentRuntime 起不来 | `norpagent-rescue rollback` / `np(safemode="on")` | 纯标准库 |
| 循环死 | 调度 / 任务全部瘫痪 | `norpagent-rescue list / show / rollback / mark-good / prune` | 纯标准库 |

**核心原则（rescue.py 的隔离边界）**：

1. 快照命令层（list / show / rollback / mark-good / prune）**只依赖标准库**
   ——主程序坏到完全无法导入时依然可用；
2. 人类接管层（tools / tool-call / manual / serve）在命令函数**内部惰性导入**
   框架（`norpagent.rescue_api`）——框架能导入时才有意义；
3. `rescue_api` 装配的 `RescueToolEnvironment` **不使用主引擎的循环**，也不
   加载任何插件 / 钩子 / 模型——它是独立的最小工具环境。

### 24.2 救援模式下控制底层最小主异步循环

救援场景下「控制循环」分三个层次：直接操作**循环内核**（EventLoop）、通过
**LoopRuntime 协议**操作、以及**绕过循环**直接驱动工具。

#### 24.2.1 循环内核的直接控制面（norpagent.nasyncio.EventLoop）

最小主异步循环是自研 `norpagent.nasyncio.EventLoop`（零 asyncio 依赖，线程
模型：一个循环绑定一个线程，`run_forever` 在哪个线程调用就属于哪个线程）。
救援时可在任意脚本中直接操作它：

| API | 调用线程 | 用途 |
|---|---|---|
| `run_forever()` | 绑定线程 | 启动循环（阻塞）；重复调用抛 `RuntimeError` |
| `run_until_complete(coro)` | 绑定线程 | 跑完一个协程后自动停止，返回结果 |
| `stop()` | 任意 | 优雅停止：当前轮 ready 队列清空后退出 |
| `abort_main()` | 任意 | **强停**：向主任务注入 `CancelledError`，打断当前 await（工具 / API 流 / 用户输入等待），循环随任务完成退出——标准 asyncio 没有等价公开入口 |
| `call_soon_threadsafe(cb)` | 任意 | 跨线程投递回调；写自管道唤醒 select 中的循环线程 |
| `run_coroutine_threadsafe(coro, loop)` | 任意 | 跨线程提交协程，返回 `concurrent.futures.Future`（结果 / 异常 / 取消正确传递） |
| `create_task(coro)` / `create_future()` | 循环线程 | 创建任务 / 结果容器 |
| `call_later(delay, cb)` | 循环线程 | 定时回调（可取消）；**跨线程调用不安全**（与 asyncio 契约一致） |
| `interrupt()`（LoopRuntime 层） | 任意 | 置位全部在途任务的取消事件（沙箱强杀子进程 / 流式循环退出） |
| `close()` | 非运行中 | 释放 selector 与自管道 socketpair |

**自研核心的三个救援关键语义**：

1. **跨线程取消**：`Task.cancel()` 自动检测调用线程——循环线程内 `call_soon`，
   循环线程外 `call_soon_threadsafe` + 自管道唤醒。外部线程可以直接取消任何
   在途任务，不需要包一层 wrapper；
2. **自管道唤醒**：`call_soon_threadsafe` / 跨线程 `set_result` / `Event.set`
   都会写 socketpair 自管道，循环线程即使阻塞在 `select()` 也会立刻醒来——
   不存在「回调已入队但循环还在睡」的悬挂；
3. **select 超时上限**：`_run_once` 把 select 等待时间钳制在 24 小时以内
   （`_MAX_SELECT_TIMEOUT`，与 CPython asyncio 同值）。超远定时器
   （`sleep(1e18)`、远期 `call_later`）在 Windows 上会让 `select()` 抛
   `OverflowError` 崩溃循环线程——这是暴力压测（24.2.6）发现的真实缺陷，
   已修复：循环每睡 24h 醒来重查定时器堆，`abort_main()` 仍可随时打断。

#### 24.2.2 LoopRuntime 协议级控制（NasyncioLoopRuntime）

`loops/nasyncio.py` 的 `NasyncioLoopRuntime` 是 `async_loop` 槽位的默认实现，
把 EventLoop 封装进线程 + 守护工作池。救援脚本可以通过协议控制它：

```python
from norpagent.loops.nasyncio import NasyncioLoopRuntime

rt = NasyncioLoopRuntime(config={"max_workers": 2})   # 不自动启动
rt.start()                                            # 起循环线程 + 惰性起工作池
rt.submit(lambda: run_a_tool_by_hand(...))            # 同步函数 → 工作池执行并阻塞等结果
rt.run_async(some_coroutine())                        # 协程 → 循环线程内执行（跨线程自管道唤醒）
rt.interrupt()                                        # 取消全部在途任务（Ctrl+C / 救援强停路径）
rt.stop()                                             # 优雅停循环
rt.join(timeout)                                      # 等循环线程退出并释放资源
```

`submit()` 的取消语义（4.6 节）：每个任务通过 contextvars 携带自己的取消
事件；`interrupt()` 置位后，任务体内的 `cancel_requested()` 返回 True——
沙箱强杀子进程树、流式循环尽早退出。`run_async` 在循环线程内被调用会显式
抛 `RuntimeError`（阻塞等待会卡死循环，宁可拒绝也不挂起）。

#### 24.2.3 场景 A：模型死了，循环活着——绕过模型直接驱动

引擎健康、仅模型不可用时，不必重建任何东西：直接通过引擎持有的循环
（`engine.async_loop`，LoopRuntime 协议）提交同步函数或协程，绕开
AgentRuntime 的模型调用路径：

```python
import norpagent as np

engine = np.current()                      # 已启动的引擎（循环线程 + 工作池活着）
loop = engine.async_loop                   # LoopRuntime 协议实例

# 方式一：同步函数（工作池执行，阻塞等结果）
out = loop.submit(
    lambda: engine.registry.resolve_tool("file_read")
                .run({"path": "readme.md"}, make_rescue_context(engine))
)

# 方式二：协程（循环线程内执行，跨线程唤醒）
out = loop.run_async(read_file_and_log(engine))
```

`submit` 的轮询等待（`poll_interval`）保证主线程每 ≤50ms 回到字节码边界，
Windows 上 Ctrl+C 立即生效为 `KeyboardInterrupt`，同时任务取消事件被置位。

#### 24.2.4 场景 B：引擎也死了——裸 EventLoop 手工驱动

引擎 / AgentRuntime 完全起不来时，可以绕开整个装配层，手工驱动一个裸循环：

```python
import threading
import norpagent.nasyncio as nio            # 自研核心：零依赖、无插件、无钩子

loop = nio.EventLoop()
thread = threading.Thread(target=loop.run_forever, daemon=True)
thread.start()

# 跨线程提交一个协程并等待结果
cf = nio.run_coroutine_threadsafe(do_manual_work(), loop)
result = cf.result(timeout=30.0)            # 异常 / 取消都会原样传递

loop.call_soon_threadsafe(loop.stop)        # 优雅停止
thread.join(5.0)
loop.close()
```

配合 `RescueToolEnvironment`（24.3.3）可以做到「裸循环调度 + 手工工具执行」：
循环负责编排（定时、重试、并发），工具执行仍走 `call_tool` 的独立线程与取消
事件——两者互补，互不阻塞。

#### 24.2.5 场景 C：循环卡死——强停与重建

循环线程卡在 `select()` 或某协程长时间 await 时：

1. **先软后硬**：`loop.call_soon_threadsafe(loop.stop)`（优雅）→ 不行再
   `loop.abort_main()`（注入 `CancelledError`，打断当前 await）；
2. **任务级取消**：`rt.interrupt()` 置位在途任务取消事件——沙箱会强杀子进程
   树，流式读取会退出（4.6.2）；
3. **看门狗模式**：健康检查协程周期性 `loop.time()` 心跳，检测到循环无响应
   （心跳超时）即 `abort_main()` + 关闭 + 重建新循环（24.2.4 的裸循环模板）；
4. **不可回收任务**：工作池中卡死的任务（C 扩展阻塞 / 非沙箱 subprocess）
   无任务时间预算（4.6.4 如实说明）——救援兜底是 daemon 线程随进程退出，
   或通过 `RescueToolEnvironment.call_tool(timeout=...)` 的硬超时弃置线程。

#### 24.2.6 循环内核暴力压测（test/stress_nasyncio_core.py）

新增 35 项暴力压测，覆盖「testway.txt 选取 + 事件循环专项补充」两大部分：

| 来源 | 压测项 |
|---|---|
| testway.txt 选取（B/C/D/E 类映射） | 冷启动就绪（B01）、快速启停 200 次（D10）、生命周期与资源释放（B02/B24）、100/500/1000 高并发（D02）、5000 批量（D08）、20 万跨线程提交风暴（D05）、超时与内层取消（B17/C02）、异常隔离（C04）、空输入与极端数值（D15/D16）、死锁拒绝（C14）、50 万 handle 资源耗尽（C17）、2000 层递归任务（D19）、重复回调风暴（D27）、内存基线（D11）、60s 混合 soak（D09）、强停延迟（B15）、看门狗 interrupt（E06） |
| 专项补充（矩阵未覆盖） | 8 线程唤醒竞争、1000 定时器精度与乱序注册顺序、10 万取消风暴、ready 队列不饿死定时器（公平性）、单线程绑定、跨线程 Future 完成、跨线程 Event 唤醒、Lock/Condition 竞争、Task.cancel 穿透（BaseException）、executor 结果/异常回传、closed-loop 拒绝、空循环不忙转（select 阻塞）、1000 并发 sleep 定时器、子进程取消杀进程（zombie 保护） |

运行：`python test/stress_nasyncio_core.py`（约 2 分钟，含 60s soak）。

**压测发现并修复的真实缺陷**：`select()` 超时溢出（Windows `OverflowError:
timestamp out of range`）——已通过 `_MAX_SELECT_TIMEOUT` 钳制修复（24.2.1
第 3 条）。其余测试均为对既有实现的验证性通过（35 项 / 110 断言 / 0 失败）。

### 24.3 手动操作工具（人类接管）

#### 24.3.1 四种入口与「传入 / 传出」语义

| 入口 | 形态 | 传入 | 传出 |
|---|---|---|---|
| `norpagent-rescue tools` | CLI | — | 全量 20 个工具的 schema（名称 / 描述 / 参数 / 必填 / 分类） |
| `norpagent-rescue tool-call <name> --args '<json>'` | CLI | 手工 JSON 参数 | 结构化结果 `{ok, tool, output, error, timed_out, duration_ms}` |
| `norpagent-rescue manual` | 交互台 | `<tool> <json>` 或 `{"tool":..., "args":...}` | 原始输出逐行打印 |
| `norpagent-rescue serve` | HTTP API | POST body `{"args":{...},"timeout":N}` | 统一 JSON 响应 |

手动调用与模型发起调用走**完全相同的执行路径**：`tool.run(args, ctx)` +
同一个 `RunContext`（registry / sandbox / session / scheduler /
context_store / project_manager 一个不少），写入同一份状态——文件落在同一
workspace、`context_add` 写入同一上下文库、`task_submit` 进入同一任务队列。
「传入」= 人代替模型生成 `args`；「传出」= 结果按模型视角的结构化格式返回，
模型恢复后可直接续用同一份状态。

#### 24.3.2 手动调用的循环交互模型

`RescueToolEnvironment.call_tool()` **不依赖主循环**，也不占用循环线程：

```
调用方（CLI / HTTP 线程 / 主线程）
  └─ call_tool(name, args, timeout)
       ├─ 解析工具 + 组装 RunContext（含 per-call 取消事件 ContextVar）
       ├─ 起独立 worker 线程（contextvars.copy_context 隔离取消信号）
       ├─ worker: tool.run(args, ctx) → 结果装箱
       ├─ 主线程 join(timeout)：超时 → 置位取消事件 + 弃置为 daemon 孤儿线程
       └─ 返回结构化结果（ok / output / error / timed_out / duration_ms）
```

要点：

- **并行安全**：每次调用独立线程，多个手动调用可并发；组件内部有锁；
- **取消信号**：`cancel_requested()` 在 worker 线程可见——沙箱强杀子进程树、
  流式循环尽早退出；超时后调用方立即返回，不等任务真正结束；
- **与 24.2 的关系**：若希望手动调用也能被循环编排（定时 / 并发 / 重试），
  把 `call_tool` 放进 `loop.submit(...)`（工作池）或裸循环的
  `run_coroutine_threadsafe` 即可——`call_tool` 是纯同步函数，任何循环都能
  调度它。

#### 24.3.3 程序化嵌入：救援环境 + 自定义循环控制

```python
from norpagent.rescue_api import RescueToolEnvironment, RescueToolAPI
import norpagent.nasyncio as nio

env = RescueToolEnvironment(workspace_root=".", context_db="./rescue.db")

# 1) 直接手动调用（同步，独立线程 + 硬超时）
r = env.call_tool("exec_cmd", {"command": "git status"}, timeout=30)
print(r["output"])

# 2) 挂 HTTP 接管服务（127.0.0.1，可选 Bearer token）
api = RescueToolAPI(env, port=8799, token="my-secret")
api.start()

# 3) 用裸循环编排手动调用（定时轮询任务队列等）
loop = nio.EventLoop()
threading.Thread(target=loop.run_forever, daemon=True).start()
nio.run_coroutine_threadsafe(poll_and_act(env, loop), loop).result(timeout=60)
loop.call_soon_threadsafe(loop.stop)
```

应用侧也可在模型健康检查失败后自动拉起 `RescueToolAPI`（挂在现有进程内），
值班人员通过操作员页面接管，模型恢复后 Agent 从同一份状态继续。

#### 24.3.4 超时与安全边界（速查）

- **双层超时**：工具自带超时（exec_cmd 最大 300s、run_python 的
  `ptc_timeout`）+ 环境级硬超时（默认 300s，`--timeout` / HTTP body 可调）；
- **弃置线程**：超时后 worker 成为 daemon 孤儿线程（与模型调用超时同一
  模式），`_orphan_threads` 按调用过滤回收；
- **零插件零钩子**：救援环境不订阅任何钩子，操作者即最终审批人；
- **路径安全照常**：`file_*` 绝对路径 / `..` 穿越拒绝、`run_python` AST 预检
  照常；HTTP 默认仅 127.0.0.1，token 可选。

### 24.4 故障决策树

```
模型调用失败？
├─ 是 → 引擎 / 循环还活着吗？
│        ├─ 活着 → norpagent-rescue tools / tool-call / manual / serve
│        │           （或程序化：engine.async_loop.submit(λ 手动工具)）
│        └─ 死了 → 快照回退 rollback --last-good（纯标准库）
│                  → 仍起不来 → np(safemode="on") 最小内核
└─ 否 → 但任务卡死 / 无响应？
         ├─ rt.interrupt() / loop.abort_main() 强停（24.2.5）
         ├─ 循环线程也死了 → 裸 EventLoop 重建 + RescueToolEnvironment（24.2.4）
         └─ 全部瘫痪 → norpagent-rescue list（纯标准库兜底）
```

### 24.5 与 15.6 的分工

| 章节 | 视角 | 内容 |
|---|---|---|
| 15.6 | 使用者 | 命令 / 端点 / 参数 / 响应格式 / 环境默认值（用法速查） |
| 24 | 原理与底层 | 三层故障模型、循环直接控制（EventLoop / LoopRuntime）、手动调用的循环交互模型、裸循环重建、压测与缺陷修复记录 |

两者互补：先用 15.6 上手，遇到「循环也出问题」再回本章 24.2 做底层控制。

---

## 附录 D　术语表

| 术语 | 定义 |
|---|---|
| 地址函数（Address Function） | 框架的核心抽象：槽位值填「地址」（模块路径 / 工厂 / 实例）即接入，不填走默认 |
| 槽位（Slot） | 一个可替换组件的位置；`np(...)` 的关键字参数名就是槽位名 |
| 槽位表热插拔 | `register_slot()` 运行时注册自定义槽位，注册即接入装配 / 校验 / 热替换全管线 |
| 最小内核 | 全框架仅四样不可替换：ArchLayer、地址解析器、Registry、EventBus |
| 地址字符串语义 | `address` / `name` / `name_or_address` / `literal` 四种字符串解读方式 |
| 附加配置子句 | 地址后的 `;key=value` 对，注入工厂的 `config` 参数 |
| defer_factory | 槽位工厂推迟到引擎装配期调用（agent_runtime 用） |
| 热挂载（remount） | 运行中替换槽位实现，无需重启进程 |
| nasyncio | 自研异步 IO 核心（不依赖标准 asyncio），默认事件循环实现 |
| LoopRuntime | 事件循环系统的协议接口（start / stop / submit / interrupt …） |
| 钩子（Hook） | 一个执行结构的命名事件，可订阅 / 改写（可变钩子）/ 否决（HookVeto） |
| 钩子层（HookLayer） | 钩子的分组（9 个标准层 + 自定义层 + dynamic 层） |
| HookVeto | 可变钩子抛出的一票否决异常，运行时按执行点语义安全收尾 |
| 注册表（Registry） | 名字 → 组件的映射中心；一切皆注册项 |
| 事件总线（EventBus） | 组件间事件传递通道；写时复制 + 无锁迭代 |
| 预设（Preset） | 声明式装配：槽位不填时的默认组合（六种内置模式） |
| 协议（Protocol） | 组件的接口契约（ModelProvider / Tool / SessionManager / Sandbox …） |
| 沙箱（Sandbox） | 工具执行的隔离边界（subprocess / pooled / isolated_python） |
| PTC | Programmatic Tool Composition：模型生成 Python 代码组合多步工具调用 |
| FTS5 | SQLite 全文索引引擎，上下文库的底层存储 |
| 快照（Snapshot） | 系统状态的序列化存档（架构槽位 + 运行时参数 + WebUI 设置） |
| 最后正常快照 | 引擎启动成功 30 秒健康期后自动标记的正常版本，救援 CLI 的一键恢复目标 |
| 崩溃救援（Rescue） | `norpagent-rescue`：主程序起不来也能回退快照的纯标准库 CLI |
| 安全模式（Safe Mode） | `np(safemode="on")`：只加载最小化内核，跳过全部插件 |
| 人类救援（Human Rescue） | 模型失效时人工接管：`norpagent-rescue tools / tool-call / manual / serve` 手动传参调用全部工具并读取原始结果 |
| 安全套件（SafetyKit） | `norpagent.safe()` 安装的安全策略集合（审批 / 网络 / 插件 / 防护 API） |
| 插件安全管线 | 签名 → 审计 → 导入限制 → 注册 的插件加载全流程 |
| FLOW | 模块流程编排：可视化画布对接真实注册表（节点 + beam 拓扑执行） |
| 文件即模块 | 拖入 .py / .json / .yaml 即注册为画布模块（.py 走插件安全管线） |
| 前端模块（FE） | `/flow` 页面可加载的 .html / .js / .ts 前端扩展 |
| SSE 背压 | 每连接有界缓冲，慢客户端丢事件不丢内存（三策略可热改） |
| 写时复制（COW） | 订阅者表不可变快照 + 引用替换，emit 无锁迭代 |

---

## 附录 E　29 钩子事件负载速查表

> 每个钩子的 `payload_keys` 即事件负载字段；可变钩子的返回语义见 9.3 节与 `test/docs/hooks.md`。

### L1 运行时生命周期

| 钩子 | 可变 | payload_keys |
|---|---|---|
| `on_agent_init` | - | `preset` |
| `on_agent_shutdown` | - | `preset` |

### L2 任务生命周期

| 钩子 | 可变 | payload_keys |
|---|---|---|
| `on_task_start` | - | `task_id`, `session_id`, `preset`, `user_input` |
| `on_task_done` | - | `task_id`, `session_id`, `content`, `steps`, `context` |
| `on_task_error` | - | `task_id`, `error` |
| `on_task_stopped` | - | `task_id`, `reason` |
| `on_task_timeout` | - | `task_id`, `timeout`, `kind` |

### L3 输入管线

| 钩子 | 可变 | payload_keys |
|---|---|---|
| `before_input` | ✅ | `task_id`, `user_input`, `session_id`, `params` |
| `after_input` | - | `task_id`, `user_input`, `session_id` |
| `on_user_input_required` | - | `question`, `default` |

### L4 会话与历史

| 钩子 | 可变 | payload_keys |
|---|---|---|
| `before_session_create` | ✅ | `session_id`, `title`, `params`, `task_id` |
| `after_session_create` | - | `session_id`, `title`, `task_id` |
| `before_message_append` | ✅ | `session_id`, `message`, `task_id` |
| `after_message_append` | - | `session_id`, `message`, `task_id` |

### L5 消息组装

| 钩子 | 可变 | payload_keys |
|---|---|---|
| `before_build_messages` | ✅ | `system_prompt`, `session_id`, `step`, `task_id`, `tool_names` |
| `after_build_messages` | ✅ | `messages`, `system_prompt`, `step`, `task_id` |

### L6 步骤

| 钩子 | 可变 | payload_keys |
|---|---|---|
| `before_step` | ✅ | `task_id`, `step`, `messages`, `context`, `params` |
| `after_step` | - | `task_id`, `step`, `content`, `tool_calls` |

### L7 模型调用

| 钩子 | 可变 | payload_keys |
|---|---|---|
| `before_model_call` | ✅ | `task_id`, `step`, `messages`, `tool_schemas`, `params` |
| `after_model_call` | ✅ | `task_id`, `step`, `output` |
| `on_reasoning` | - | `task_id`, `content`, `stream` |
| `on_content` | - | `task_id`, `content`, `stream`, `final` |
| `on_event` | - | `event_type`, `data`, `task_id` |
| `on_usage_update` | - | `task_id`, `input`, `output`, `total` |

### L8 工具调用

| 钩子 | 可变 | payload_keys |
|---|---|---|
| `before_tool_call` | ✅ | `task_id`, `tool_name`, `args`, `context` |
| `after_tool_call` | ✅ | `task_id`, `tool_name`, `args`, `result`, `success`, `context` |
| `on_tool_error` | - | `task_id`, `tool_name`, `error`, `args` |

### L9 结果定型

| 钩子 | 可变 | payload_keys |
|---|---|---|
| `before_result` | ✅ | `task_id`, `result` |
| `after_result` | ✅ | `task_id`, `result` |

> 插件加载管线另有 8 个钩子（`PLUGIN_PIPELINE_LAYER`：`before_plugin_load` /
> `after_plugin_register` 等），见 11.4 节；FLOW 编排另有 `flow.*` 事件（第 20.5 节）。

---

*NorpAgent 开发手册 · v0.9.4 · Copyright (c) 2026 xingluosama121, MIT Licensed*

# NORP Agent 开发手册

> **版本**：0.9.0 ｜ **许可**：Copyright (c) 2026 xingluosama121, MIT Licensed

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
- [第 15 章　库集成示例](#第-15-章库集成示例)
- [第 16 章　测试与调试](#第-16-章测试与调试)
- [第 17 章　迁移指南](#第-17-章迁移指南)
- [第 18 章　常见问题（FAQ）](#第-18-章常见问题faq)
- [附录 A　架构槽位速查表](#附录-a架构槽位速查表)
- [附录 B　9 层钩子速查表](#附录-b9-层钩子速查表)
- [附录 C　公开 API 索引](#附录-c公开-api-索引)

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
[norpagent] listening on http://127.0.0.1:8787/
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
| `name` | 字符串 = 注册表组件名 | preset, ui, tools |
| `name_or_address` | 先按名、再按地址 | model, session, sandbox, scheduler |
| `literal` | 字符串 = 字面值 | security(级别), storage(路径), hooks, plugins, logger, error_handler |

其中：`np(model="mock")` 中的 `"mock"` 是注册表里的模型名；
`np(model="myapp.model:create")` 中的字符串是地址。
`np(session="sqlite")` 引用内置 SQLite 会话组件；
`np(session="myapp.sessions:create")` 按地址加载自定义会话实现。

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
| 装配槽位 | session / sandbox / scheduler / ui / agent_runtime / preset / context_store / project_manager | AgentRuntime 热重建：停旧运行时（释放沙箱/组件/退订渲染器）→ 按当前装配建新运行时 → 前端重绑渲染器（HTTP 端口不变） |
| 基础设施槽位 | frontend / async_loop | 停旧实现、启新实现；新实现启动失败自动回滚旧实现。async_loop 替换时旧循环上在途任务会被放弃，建议无任务时替换 |
| 基础服务槽位 | logger / storage / error_handler | 直接更新引擎引用，立即生效 |

#### 3.7.1 热挂载前端页面（html 参数）

`frontend` 是基础设施槽位，替换语义为「停旧启新」。配合 WebFrontend
的 `html` 挂载参数，可在运行中换掉 `/` 路由页面——不用重启进程，
刷新浏览器即见新页面：

```python
import norpagent as np

np(html="front.html")                       # 启动并挂载自定义页面
# ... 修改 front.html 或换别的页面文件 ...
np.remount(frontend="norpagent.frontends.web:WebFrontend;html=front.html")
# 端口不变，浏览器刷新（或重开 http://127.0.0.1:8787/）即为新页面
```

参数优先级：remount **显式给出**的键覆盖启动参数，**未显式给出**的键
（如 port）沿用启动参数——所以只换页面时浏览器 URL 不变。显式键的
判定：字符串地址取分句 `;key=value` 中的键；实例取构造参数中与默认值
不同的键（html 以 `_html` 属性判定）。例如：

```python
np.remount(frontend="norpagent.frontends.web:WebFrontend;port=9000")  # 换端口
np.remount(frontend="norpagent.frontends.web:WebFrontend;html=")      # 重置为库内置页
from norpagent.frontends.web import WebFrontend
np.remount(frontend=WebFrontend(html="front.html"))                   # 实例形式
```

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
守护线程保证不被阻塞。

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
| Web（默认） | `norpagent.frontends.web:WebFrontend` | HTTP + SSE，无第三方依赖；页面 = front.html（多标签会话/流式渲染/设置/插件面板）；控制台打印 `listening on http://127.0.0.1:8787/`；可配 `;port=9000`、`;html=自定义页面`（槽位挂载参数，见 5.4）或 `np(port=9000, language="zh_CN")` |
| 控制台 REPL | `norpagent.frontends.console:ConsoleFrontend` | 显式指定；`/exit`（或 `exit`/`quit`/`exit()`）退出，`/reset` 新会话；在 Python 交互式解释器中自动切换同步模式 |
| 无头 | `norpagent.frontends.headless:HeadlessFrontend` | 纯 API；`prompt` 模式默认；输出（正文/工具/结果）打印到 stdout |

### 5.4 Web UI 行为与配置持久化

Web UI 的行为与配置项：

| 能力 | 说明 |
|---|---|
| 配置持久化 | 设置面板保存后落盘 `~/.norpagent/webui_config.json`（`NORPAGENT_WEBUI_CONFIG` 可覆盖；`WebUI(config_path=...)` 可指定，传 `None` 关闭）。磁盘加载只接受 `DEFAULT_CONFIG` 白名单键，未知键丢弃。0.9 起**磁盘加载延迟到 `start()`**：构造 WebUI 不再触发任何磁盘 I/O（嵌入式 / 只读根文件系统友好），显式构造参数 > 磁盘值 > 默认值的优先级不变 |
| 页面防缓存 | 页面响应带 `Cache-Control: no-store`，浏览器每次刷新获取最新 front.html；服务端页面字节读入内存缓存（0.9：每次 GET / 不再读盘） |
| 页面挂载（html 参数） | `/` 路由默认页面可整体替换：`html` 接收**文件路径**或 **HTML 内容**（strip 后以 `<` 开头视为内容，否则视为文件路径）；文件不存在时构造抛 `ValueError`（快速失败，不静默回落）。无需物理覆盖 `norpagent/builtin/ui/assets/front.html`。`/flow` 页面不受影响 |
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

**页面挂载（html 参数）四种写法等价：**

```python
import norpagent as np

# 1. 槽位地址子句（;key=value，推荐）
np(frontend="norpagent.frontends.web:WebFrontend;html=/path/to/my.html")

# 2. 构造函数直接传（WebFrontend / WebUI 均支持）
np(frontend=WebFrontend(html="<html><body>我的界面</body></html>"))

# 3. 配置字典
np(config={"web": {"html": "/path/to/my.html"}})

# 4. 运行时参数透传
np(html="/path/to/my.html")

# 文件路径不存在时构造报错（快速失败，不静默回落默认页面）
# ValueError: WebUI html 挂载参数既不是 HTML 内容（以 '<' 开头）也不是存在的文件: ...
```

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
np()                            # 默认前端 = Web（listening on http://127.0.0.1:8787/）
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

## 第 15 章　库集成示例

### 15.1 FastAPI 集成

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

### 15.2 桌面应用集成（pywebview 风格）

```python
import norpagent as np

np(frontend="myapp.tray_frontend:TrayFrontend")
fe = np.current().frontend

# JS 桥接层把用户输入转发给 fe.send()；
# 事件总线订阅 on_content 把流式输出推回前端。
```

### 15.3 集成要点

1. **单例引擎**：运行中的引擎是单例，`np()` 幂等返回当前引擎；
2. **生命周期**：主循环轮询 `np.stop()`，进程退出有 atexit 兜底清理；
3. **装配观测**：`np.current().layer.describe()` 打印装配清单。

---

## 第 16 章　测试与调试

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

## 第 17 章　迁移指南

### 17.1 从旧版 norpagent（≤0.4）迁移

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

### 17.2 从旧桌面应用迁移

旧应用中的模块可按以下映射接入槽位：

| 旧应用模块 | 新槽位 | 接入方式 |
|---|---|---|
| `nasync_io`（自研事件循环） | `async_loop` | **已打包进库**：`norpagent.nasyncio` 即默认调度核心（原 nasync_io v2.0.0），无需自带文件；如需换实现再填地址 |
| `async_loop.AsyncAgentLoop` | `agent_runtime` | 实现 run/shutdown → 填地址 |
| FastAPI 后端 + 桌面 UI | `frontend` | 实现 Frontend 协议 → 填地址 |
| `plugin_system` | `plugins` | 目录列表直接传 |
| `sandbox_pool` | `sandbox` | `"pooled"` 或自研地址 |
| `config.json` 各开关 | 预设 params | 任务参数透传 |

### 17.3 版本兼容

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
- 删除性变更只会出现在大版本。

---

## 第 18 章　常见问题（FAQ）

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

# 前端
from norpagent.frontends import (Frontend, ConsoleFrontend,
                                 HeadlessFrontend, WebFrontend)

# 运行时
from norpagent.runtime import (launch, current, stop, submit,
                               shutdown, NorpEngine, EngineState, EngineError)

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

# Web UI：SSE 背压（超高并发，第 14.3 节）
from norpagent.builtin.ui.web import WebUI
ui = WebUI(port=8787, sse_queue_size=2048,
           sse_queue_policy="drop_oldest")
ui.set_sse_queue(4096, "drop_newest")   # 运行中热改变（POST /api/streams 等价）
ui.streams_info()                        # 订阅者数 / 丢弃事件数 / 缓冲深度
```

---

*NorpAgent 开发手册 · v0.9.0 · Copyright (c) 2026 xingluosama121, MIT Licensed*

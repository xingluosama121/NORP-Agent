# NORP Agent 开发手册

> **版本**：0.7.0 ｜ **许可**：Copyright (c) 2026 xingluosama121, MIT Licensed

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
- [第 14 章　库集成示例](#第-14-章库集成示例)
- [第 15 章　测试与调试](#第-15-章测试与调试)
- [第 16 章　迁移指南](#第-16-章迁移指南)
- [第 17 章　常见问题（FAQ）](#第-17-章常见问题faq)
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
| `norpagent/arch/slots.py` | 18 个架构槽位的规格定义表 |
| `norpagent/arch/address.py` | 地址函数解析器（字符串 → 模块/对象） |
| `norpagent/arch/layer.py` | ArchLayer：槽位连接与工厂调用 |
| `norpagent/loops/base.py` | LoopRuntime 协议（事件循环契约） |
| `norpagent/loops/std_asyncio.py` | 默认循环实现（标准 asyncio 适配器） |
| `norpagent/loops/__init__.py` | `norpagent.nasyncio()` 架构函数 |
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

共 **18 个槽位**。每个槽位都有规格说明（SlotSpec）：名称、职责、协议、
默认实现、字符串语义、工厂参数约定：

```python
from norpagent.arch.slots import get_slot

print(get_slot("async_loop").format_help())
# [async_loop] 事件循环系统：Agent 运行所在的异步调度核心。等价于架构函数 norpagent.nasyncio()。
#   协议: LoopRuntime 协议（norpagent.loops.base.LoopRuntime）：...
#   默认: norpagent.loops.std_asyncio:StdLoopRuntime
#   字符串语义: address
#   工厂参数 layer: 所在架构层
#   工厂参数 config: 该槽位的附加配置 dict
#   示例: np(async_loop='norpagent.loops.std_asyncio:StdLoopRuntime')
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
  async_loop       <- 默认逻辑                         => StdLoopRuntime
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

注意事项：

1. 只允许在引擎 RUNNING 状态调用，否则抛 `EngineError`；槽位名非法同样抛错；
2. `remount(slot=None)` 清空该槽位配置（回落默认逻辑）；
3. 预设对象同一性：热挂载后 `registry.resolve_preset(name)` 与
   `engine.agent.preset` 仍是同一实例（前端对 preset.tools 的热改写依赖此约定）；
4. `agent_runtime` 是 `defer_factory` 槽位：工厂推迟到引擎装配期调用
   （registry / preset 上下文就绪后），地址子句 `;key=value` 经
   `ArchLayer.subconfig()` 注入工厂的 config。

---

## 第 4 章　事件循环系统：norpagent.nasyncio()

### 4.1 循环系统独立架构函数

事件循环决定任务调度方式：任务运行的线程、中断方式、唤醒方式。
NorpAgent 将循环系统提供为独立架构函数：

```python
import norpagent as np

loop = np.nasyncio()                       # 默认循环
loop = np.nasyncio("myapp.loop:create")    # 自定义循环
```

它与槽位等价：

```python
np(async_loop="myapp.loop:create")   # 等价于 np.nasyncio("myapp.loop:create")
```

`np.nasyncio()` 的返回值是一个 **LoopRuntime**（协议见下）。
如需使用其他事件循环实现（例如不依赖标准 asyncio 的循环），实现
LoopRuntime 协议并为 `async_loop` 槽位填入地址即可，无需修改框架核心代码。

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

### 4.3 默认实现：StdLoopRuntime

默认实现基于标准 asyncio：独立线程跑事件循环，`submit()` 把同步函数
交给**自有守护工作池**执行并等待结果（不用 asyncio 默认线程池，
原因见 4.6）。配置项：`max_workers`（工作池线程数，默认
`max(4, cpu_count)`），经 `np(config={"async_loop": {"max_workers": 8}})`
或 `np.nasyncio(max_workers=8)` 调整。

```python
loop = np.nasyncio()
loop.start()
result = loop.submit(lambda: 1 + 1)   # → 2
loop.stop()
loop.join()
```

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

### 4.5 跨线程桥接注意事项

**事项一**：`asyncio.Future.result()` 不是线程安全的阻塞等待。
从非循环线程调用它不会阻塞，future 未完成时直接抛
`InvalidStateError: Result is not set.`。正确做法是让**执行线程**
（而非循环线程）负责「写完结果再置位事件」，不依赖
asyncio future 的 done-callback 调度时序（`StdLoopRuntime.submit`
即按此实现）。

**事项二**：跨线程调用 `Future.add_done_callback()`，如果 future
**已经完成**，回调会走 `loop.call_soon`（非线程安全版本）——
它只把句柄塞进 `_ready` 队列而不写自管道，此时循环若阻塞在
selector/proactor 上就收不到唤醒，`done.wait()` 挂起。
（`StdLoopRuntime.run_async` 的跨线程等待改用
concurrent.futures.Future 的 done 回调 + 轮询 Event 置位：回调由
Future 保证在结果写入线程内同步触发，无唤醒竞态。）

规则：跨线程协调使用「执行线程写结果 + threading.Event 置位」，
循环线程不作为唤醒路径上的必要环节。

### 4.6 Ctrl+C 与任务取消语义

#### 4.6.1 问题：主线程不在 asyncio.run() 里，Ctrl+C 为什么可能失灵

`np()` 启动后主线程只做生命周期轮询（`np.stop()`），真正的工作线程
在后台执行任务，调用方（如控制台 REPL 的主线程）阻塞在
`loop.submit()` 的等待上。两条信号链路上的坑：

1. **Windows 上一次性 `Event.wait()` 收不到 Ctrl+C**：SIGINT 以
   pending interrupt 的形式投递到主线程，只在**字节码边界**被检查；
   主线程阻塞在 `Event.wait()`（底层 `WaitForSingleObject` 无限等待）
   时永远不会回到字节码边界，Ctrl+C 形同虚设。
   **解法**：`StdLoopRuntime.submit()` 改为**轮询等待**（每 ≤0.2s
   经过一次字节码边界），Ctrl+C 即刻以 `KeyboardInterrupt` 冒出。
2. **卡在阻塞 I/O 里的工作线程杀不死，进程僵住**：SIGINT 只到主线程，
   工作线程若卡在沙箱 `subprocess` / HTTP 请求里，只能等它自己超时；
   更糟的是 asyncio 默认 ThreadPoolExecutor 的工作线程被 CPython 登记
   在 `threading._threads_queues`，解释器退出时会被**强制 join**——
   任务不结束进程就退不出去。
   **解法**：工作池用裸守护线程（退出不 join）；同时把「取消」信号
   显式传给任务执行体（见 4.6.2），让它尽早自行退出。

#### 4.6.2 取消信号：contextvars + 取消事件

`StdLoopRuntime.submit()` 给每个任务包进一个带取消事件的
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

#### 4.6.3 为什么不「全部用 nasyncio」——线程边界说明

`norpagent.nasyncio()` 是 **async_loop 槽位的架构函数**（默认实现
StdLoopRuntime = 标准 asyncio 适配器），不是一套自研事件循环。
全库所有任务调度（engine.submit → loop.submit）都经它走协议，
库内只有 `loops/std_asyncio.py` 一个文件 import asyncio。剩下的
裸线程是**刻意的阻塞 I/O 泵**，不应进事件循环：

| 线程 | 职责 | 为什么不用循环线程 |
|---|---|---|
| `norpagent-loop-pool-*` | 执行 submit 的同步任务 | 任务本体可能阻塞（沙箱/HTTP），放循环线程会卡死整个循环 |
| `norpagent-std-loop` | 跑 asyncio 事件循环 | 循环本体 |
| 沙箱管道 reader（PTC/pooled/插件宿主） | 读取子进程 stdout/stderr | 阻塞管道读取，不可中断 |
| `norpagent-webui` / 请求线程 | HTTP 服务与请求 | socketserver 自身的线程模型 |
| `norpagent-model-*` | call_timeout 硬中断看守 | 限时 join，超时即弃 |

规则：**计算与调度进循环（可替换），阻塞 I/O 用守护线程泵
（不可取消但也不阻塞退出）**。替换 `async_loop` 槽位即可整体换掉
循环系统（协议见 4.2），无需改动框架其他部分。

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
| 配置持久化 | 设置面板保存后落盘 `~/.norpagent/webui_config.json`（`NORPAGENT_WEBUI_CONFIG` 可覆盖；`WebUI(config_path=...)` 可指定，传 `None` 关闭）。启动时自动加载——API Key / 模型 / 语言等跨进程保留。磁盘加载只接受 `DEFAULT_CONFIG` 白名单键，未知键丢弃 |
| 页面防缓存 | 页面响应带 `Cache-Control: no-store`，浏览器每次刷新获取最新 front.html |
| 页面挂载（html 参数） | `/` 路由默认页面可整体替换：`html` 接收**文件路径**或 **HTML 内容**（strip 后以 `<` 开头视为内容，否则视为文件路径）；文件不存在时构造抛 `ValueError`（快速失败，不静默回落）。无需物理覆盖 `norpagent/builtin/ui/assets/front.html`。`/flow` 页面不受影响 |
| 断连处理 | 客户端断连（WinError 10053 / EPIPE 等）静默处理，内部错误记录 DEBUG 日志 |
| 端口顺延 | 绑定失败（含 Windows 10013 监听占用）时向后顺延最多 10 个端口，以实际端口为准 |
| 请求体防护 | 负数 Content-Length 按无请求体处理；超过 1MB 拒收 |
| 关闭幂等 | `shutdown()` 幂等 + 同线程死锁防护，可跨线程调用 |
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

1. **参数分拣**：关键字参数中与 18 个槽位同名的键 → 槽位值；
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
    # ── 架构槽位（18 个，不填 = 默认逻辑）──
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
    # ── 其余键 = 任务参数，透传 Agent 循环 ──
    max_steps=32, task_timeout=0, call_timeout=0,
    workspace_root=..., system_prompt=...,
)
```

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

### 9.1 钩子分层

Agent 循环的执行结构均以钩子 API 暴露，可用钩子干预。Agent 循环
切为 9 层，每层引出钩子：

```
L1 运行时生命周期 ─ L2 任务 ─ L3 输入 ─ L4 会话与历史 ─ L5 消息组装
   ─ L6 步骤 ─ L7 模型调用 ─ L8 工具调用 ─ L9 结果定型
```

每个钩子是独立 API，有三种用法：

```python
from norpagent.hooks import before_model_call

# 1. 模块级：订阅
before_model_call.subscribe(my_fn)
# 2. 运行时视图：agent.hooks.before_model_call
# 3. 槽位批量订阅：np(hooks={"before_model_call": my_fn})
```

钩子分两类：**可变钩子**（intercept 可改写数据流）与**观测钩子**（emit）。
`HookVeto` 异常使当前执行结构立即终止（任务按 stopped 收尾），
事件总线对其特判透传。

### 9.2 自定义钩子与自定义层

```python
from norpagent.hooks import HookLayer, define_hook

my_layer = HookLayer("my_layer", 0)          # 自定义层
my_hook = define_hook("my_hook", layer=my_layer, mutating=True)
```

未注册的具名事件触发时自动成为动态钩子。扩展方式有三种：
标准 29 钩子 / 自定义钩子 / 自定义层。

### 9.3 每个执行结构都可覆写

AgentRuntime 的七个执行结构同时是公共方法：`prepare_input /
create_session / append_message / build_messages / call_model /
execute_tool_call / finalize_result`。子类覆写即可替换，
无需改动循环本体；或使用 `agent_runtime` 槽位整体替换循环类。

### 9.4 行为细节

- 被阻止/否决/审批拒绝的工具调用统一流经 `after_tool_call`；
- `before_step` 钩子否决本轮 → 跳过模型调用进入下一轮；
- 钩子改写作用于实际数据流（消息/参数/结果）。

---

## 第 10 章　安全系统：norpagent.safe()

### 10.1 启用方式

```python
import norpagent as np

np(security="high")                      # 槽位方式
# 或：
from norpagent import safe
safe(registry, level="standard")         # 直接方式（basic / standard / high）
```

安全系统由独立函数 `safe()` 提供：内核不 import 任何
`norpagent.security` 模块，防护/加固/审批/审计/签名通过
`registry.security` 注入（由 before_input 等钩子执行）。

### 10.2 三档级别

| 能力 | basic | standard | high |
|---|---|---|---|
| 输入越狱/注入防护（L3 钩子） | ✓ | ✓ | ✓ |
| 系统提示词加固（L5 钩子） | ✓ | ✓ | ✓ |
| 插件源码 AST 审计 | warn | warn | **block** |
| 插件导入限制 | off | safe | safe |
| 权限声明（manifest permissions） | | | ✓ |
| 插件网络策略 | allow_all | deny | deny |
| 插件工具人工审批 | | ✓ | ✓ |
| 强制受信任签名 | | | ✓ |

SafetyKit 提供独立检查 API（scan_input / harden / audit /
verify_plugin / check_network / approval_policy），可单独使用。
插件加载继承当前安全级别（config 缺省时采用
`registry.security.plugin_config()`）；未装 cryptography 时签名校验
返回「不受信任」，不放行。详见 `docs/security.md`。

---

## 第 11 章　插件系统

```python
np(plugins=["./my_plugins"])     # 目录列表：签名→审计→导入限制→注册
```

- 与现有应用插件格式兼容（模块级 PLUGIN_NAME / TOOLS / HOOKS 等）；
- Ed25519 签名验证（`norpagent plugin-sign --gen` 生成密钥）；
- 导入限制两层：AST 静态预检 + meta_path 运行时拦截；
- 进程级隔离：`ISOLATION = "process"` 的插件在宿主子进程执行，
  崩溃后自动重启；
- `PluginSystem` 门面：load / reload / unload / status / shutdown。

---

## 第 12 章　预设模式

### 12.1 内置四模式

| 模式 | 用途 | 组件组合 |
|---|---|---|
| `minimal` | 模型基准测试 | mock + echo/get_time + memory |
| `standard` | 通用编码任务 | sqlite + pooled + persistent + fts5 + 全部内置工具 |
| `ptc` | 代码编排工具调用 | run_python（沙箱执行） |
| `creative` | 自定义模式调试 | 模式文件加载（--mode-file） |

```python
np(preset="standard")
np(preset="ptc")
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
norpagent --mode ptc --prompt "..."               # 单次任务
norpagent --mode-file my_mode.py                  # 模式文件
norpagent --mode standard --ui web --port 8787    # Web UI
norpagent --mode standard --plugin-dir ./my_plugins
norpagent --mode standard --safe high
norpagent plugin-sign --gen                       # 插件签名密钥
```

CLI 与 `np()` 等价：CLI 内部流程为
「安装默认组件 → 注册预设 → 应用安全 → 加载插件 → 构建运行时」。

---

## 第 14 章　库集成示例

### 14.1 FastAPI 集成

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

### 14.2 桌面应用集成（pywebview 风格）

```python
import norpagent as np

np(frontend="myapp.tray_frontend:TrayFrontend")
fe = np.current().frontend

# JS 桥接层把用户输入转发给 fe.send()；
# 事件总线订阅 on_content 把流式输出推回前端。
```

### 14.3 集成要点

1. **单例引擎**：运行中的引擎是单例，`np()` 幂等返回当前引擎；
2. **生命周期**：主循环轮询 `np.stop()`，进程退出有 atexit 兜底清理；
3. **装配观测**：`np.current().layer.describe()` 打印装配清单。

---

## 第 15 章　测试与调试

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

## 第 16 章　迁移指南

### 16.1 从旧版 norpagent（≤0.4）迁移

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

### 16.2 从旧桌面应用迁移

旧应用中的模块可按以下映射接入槽位：

| 旧应用模块 | 新槽位 | 接入方式 |
|---|---|---|
| `nasync_io`（自研事件循环） | `async_loop` | 实现 LoopRuntime 协议 → 填地址 |
| `async_loop.AsyncAgentLoop` | `agent_runtime` | 实现 run/shutdown → 填地址 |
| FastAPI 后端 + 桌面 UI | `frontend` | 实现 Frontend 协议 → 填地址 |
| `plugin_system` | `plugins` | 目录列表直接传 |
| `sandbox_pool` | `sandbox` | `"pooled"` 或自研地址 |
| `config.json` 各开关 | 预设 params | 任务参数透传 |

### 16.3 版本兼容

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
- 删除性变更只会出现在大版本。

---

## 第 17 章　常见问题（FAQ）

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
`Registry() + AgentRuntime(...)`，不受单例约束（第 16.1 节）。

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
（每 ≤0.2s 一次边界），Ctrl+C 即刻冒出 `KeyboardInterrupt`。
② 卡在沙箱 `subprocess` / HTTP 里的工作线程杀不死，且 asyncio
默认线程池在解释器退出时被强制 join——任务不结束进程就僵住；
现在工作池用裸守护线程（退出不 join），且 Ctrl+C / 引擎停止会
置位任务的取消事件：PTC 沙箱立即强杀子进程、池化沙箱杀进程树、
模型流式中断、Agent 轮次边界以 stopped 收尾。任务执行体可用
`norpagent.loops.cancel.cancel_requested()` 主动响应取消。

---

## 附录 A　架构槽位速查表

| 槽位 | 字符串语义 | 默认 | 工厂上下文键 |
|---|---|---|---|
| async_loop | address | StdLoopRuntime | layer, config |
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
| frontend | address | prompt→headless，否则 web | layer, config |
| ui | name | 预设声明 | - |
| preset | name | standard | - |
| logger | literal | logging.getLogger("norpagent") | - |
| storage | literal | ~/.norpagent | - |
| error_handler | literal | 记录日志 | - |

> 运行中热挂载（3.7）：所有槽位均可 `np.remount(slot=value)` 替换。
> `agent_runtime` 为 `defer_factory` 槽位（工厂推迟到引擎装配期调用）。

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

## 附录 C　公开 API 索引

```python
# 模块入口
np()                      # launch()
np.stop()                 # 生命周期轮询
np.nasyncio(address=...)  # 事件循环架构函数
np.current() / np.submit() / np.shutdown()
np.remount(model=..., ...)   # 运行中热挂载：任何槽位均可替换

# 架构层
from norpagent.arch import ArchLayer, SlotSpec, SLOT_SPECS
from norpagent.arch import resolve_address, call_factory, AddressError
layer.remount(slot, value)  # 架构层热挂载（模块缓存 + pyc 失效）
layer.subconfig(slot)       # 槽位附加子配置（";key=value"）

# 循环系统
from norpagent.loops import nasyncio, LoopRuntime, StdLoopRuntime
from norpagent.loops.cancel import cancel_requested, current_cancel_event
loop.interrupt()   # 请求取消全部在途 submit 任务（引擎停止路径）

# 前端
from norpagent.frontends import (Frontend, ConsoleFrontend,
                                 HeadlessFrontend, WebFrontend)

# 运行时
from norpagent.runtime import (launch, current, stop, submit,
                               shutdown, NorpEngine, EngineState, EngineError)

# 内核（手工装配，等价保留）
from norpagent import (Registry, EventBus, Preset, AgentRuntime,
                       RunResult, install_defaults, register_all_presets)

# 安全 / 钩子 / 插件
from norpagent import safe, SafetyKit, SecurityContext
from norpagent import hooks
from norpagent.plugins import PluginSystem, install_plugin_dirs
```

---

*NorpAgent 开发手册 · v0.7.0 · Copyright (c) 2026 xingluosama121, MIT Licensed*

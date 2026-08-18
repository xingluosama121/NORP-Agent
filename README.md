# norpagent



可插拔积木式 Agent 框架（模块即入口）。像搭乐高一样构建 Agent：换零件只填「地址」，核心代码零修改；运行中也能换零件（热挂载），无需重启进程。



> **版本**：0.9.0 ｜ **许可**：Copyright (c) 2026 xingluosama121, MIT Licensed

> **依赖**：核心包零第三方依赖，纯 Python 标准库即可运行；可选能力按需安装



---



## 特性一览



- **架构层 + 地址函数**：除底层最小内核（`ArchLayer` / 地址解析 / 注册表 / 事件总线）外，全部组件都是槽位——模型、前端、事件循环、会话、沙箱……填地址即可替换；

- **槽位表热插拔（v0.9）**：`register_slot()` / `unregister_slot()` 运行时注册 / 注销自定义槽位，注册即接入 `np()` 参数校验、装配、`np.remount()` 热替换、`layer.describe()` 清单全管线（内置 18 槽位受保护，其值可随时热替换）；

- **自研异步调度核心**：`norpagent.nasyncio`（原 nasync_io，已打包进库）是默认事件循环核心——**不依赖、不 import 标准 asyncio**；

- **安全系统整体剥离**：`norpagent.safe()` 一句话挂载全套安全策略，默认**钩子零干预**（不挂钩子），`hooks=True` / `kit.install_hooks()` 才显式干预钩子；

- **9 层 29 钩子体系**：每个执行结构都是独立 API，可订阅 / 改写 / 否决，支持自定义钩子与自定义层；

- **嵌入式与超高并发（v0.9）**：`install_core()` 极简装配 + `embedded` 预设（纯内存、默认 headless、零磁盘依赖）；EventBus 写时复制、SSE 有界队列 + 批量 flush、HTTP 并发调优；

- **外部插件**：签名 → 审计 → 导入限制 → 注册的完整安全管线，支持进程级隔离与 `PluginSystem` 门面；

- **前端体系**：console / headless / web 开箱即用，支持任意自定义前端；Web UI 基于 HTTP + SSE，零依赖；

- **上下文 / 项目 / 调度 / 沙箱**：FTS5 上下文库、`project_status`（git 感知）、persistent 持久调度器、pooled 沙箱池与 PTC 子进程隔离执行。



---



## 安装



```bash

pip install norpagent

```



核心包无第三方依赖，安装后可在纯 Python 环境运行（内置 mock 模型与工具）。可选能力按需安装：



```bash

pip install norpagent[openai]       # OpenAI 兼容模型适配器（DeepSeek/OpenAI/Qwen/vLLM/Ollama）

pip install norpagent[anthropic]    # Anthropic 协议模型适配器

pip install norpagent[web]          # 联网检索（web_search / web_fetch 工具）

pip install norpagent[security]     # 插件 Ed25519 验签（cryptography）

pip install norpagent[all]          # 全部

```



---



## 快速开始



### 5 行代码跑起来



```python

import norpagent as np



np()                        # 完全按默认逻辑运行（standard 预设 + Web 前端）

running = True

while running:

    if np.stop() == True:   # 生命周期函数：应用结束即退出

        running = False

```



保存为 `hello.py` 运行，控制台打印 `[norpagent] listening on http://127.0.0.1:8787/`，浏览器访问该地址打开聊天界面。



两个要点：



1. **`np()` 是模块级调用**——`norpagent` 模块本身可调用，等价于 `norpagent.launch()`；

2. **`np.stop()` 是生命周期函数**——返回 `True` 表示 Agent 应用已结束，主循环应退出。



### 单次任务



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



传入 `prompt` 后：Agent 执行完这一条任务即自动停止，任务结果保存在 `np.current().last_result`。



### 换零件：填地址即可



```python

np(preset="standard")                                  # 换预设模式

np(model="openai_compat")                              # 换大脑（模型）

np(async_loop="norpagent.loops.nasyncio:NasyncioLoopRuntime")  # 换事件循环系统

np(frontend="norpagent.frontends.console:ConsoleFrontend")     # 换前端（默认即 Web）

np(session="sqlite", sandbox="pooled")                 # 换会话与沙箱

```



### 运行中热挂载（无需重启）



```python

np.remount(model="openai_compat")                      # 换模型：下一次 run 生效

np.remount(frontend="norpagent.frontends.console:ConsoleFrontend")

np.remount(model="myapp.model:create")                 # 改模块文件后重挂载即热重载

```



### 控制台 REPL



显式使用控制台前端时，在 Python 交互式解释器（REPL）中自动切换同步模式：`np()` 阻塞到用户退出（`/exit`、`exit()`、Ctrl+C 或 EOF），无需轮询循环：



```python

import norpagent as np



np(frontend="norpagent.frontends.console:ConsoleFrontend")

# >>> 你: 你好

# >>> 使用 /exit 退出

```



---



## 命令行入口



```bash

# 单次任务

python -m norpagent --mode standard --prompt "你好"



# 交互 REPL

python -m norpagent --mode standard



# 列出全部预设模式（minimal / standard / ptc / creative / longrun / embedded）

python -m norpagent --list-modes



# 接 OpenAI 兼容服务（DeepSeek 等）

python -m norpagent --model openai_compat --model-name deepseek-v4-flash \

    --base-url https://api.deepseek.com/v1 --api-key sk-xxxx



# 安全策略（运行态：审批 / 审计 / 签名；默认钩子零干预）

python -m norpagent --safe high

python -m norpagent --safe high --safe-hooks   # 显式开启钩子干预（越狱拦截 + 提示词加固）



# Web UI 端口

python -m norpagent --ui web --port 8787



# 外部插件（可重复指定，安全管线：签名 -> 审计 -> 导入限制）

python -m norpagent --plugin-dir ./plugins --plugin-isolation auto



# 插件签名工具（NORP 插件签名协议 v1）

python -m norpagent plugin-sign myplugin.py --gen

python -m norpagent plugin-sign myplugin.py --key <ed25519-hex>

```



---



## 核心概念



### 槽位与地址函数



除底层最小内核外，全部组件都是槽位；`np(...)` 的关键字参数名就是槽位名，槽位值填「地址字符串」（`package.module:attr` 或注册名）即完成替换，装配由 `ArchLayer` 统一解析。



槽位表本身也可以热插拔——第三方库运行时注册全新槽位，注册即接入 `np()` 参数校验、装配、热替换、清单全管线：



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

    remount_rebuild_agent=True,     # 热替换后热重建 AgentRuntime，组件立即生效

))



import norpagent as np



np(vector_store=MyVectorStore())    # 装配：engine.agent.components["vector_store"]

np.remount(vector_store=Other())    # 热替换：AgentRuntime 热重建

```



内置 18 个槽位是框架结构契约，不可注册 / 覆盖 / 注销（其值仍可随时 `np.remount` 热替换）。完整契约与保护规则见开发手册第 3 章。



### 自研异步核心：norpagent.nasyncio



**明确声明：norpagent 不依赖标准 asyncio。** 0.8 起库内零 `import asyncio`：默认调度核心是打包进库的自研异步 IO 库 `norpagent.nasyncio`（原 nasync_io，v2.0.0），底层只依赖 Python 标准库的非 asyncio 模块（`threading` / `selectors` / `socket` / `heapq` 等）。原因：



1. **调度 / 取消 / 跨线程唤醒语义完全自控**——修复标准 asyncio 的公认坑：`Task.cancel()` 非线程安全、done 回调不写自管道导致等待方挂起、没有对外的「取消主任务」入口；

2. **依赖面压缩到可审计**——事件循环全部行为是库内自己写的代码，不引入标准 asyncio 的内部实现细节与版本差异；

3. **退出语义可控**——不用会被解释器强制 join 的 `ThreadPoolExecutor`，Ctrl+C 后进程即刻收尾；

4. **API 语义对齐、迁移零成本**——`EventLoop` / `Future` / `Task` / `Lock` / `Condition` / `sleep` / `wait_for` 等与 asyncio 同名对应，把 `import asyncio` 换成 `import norpagent.nasyncio` 即可移植；

5. **核心独立可用**——`norpagent.nasyncio` 本身是独立微型异步库，可脱离框架单独使用。



```python

import norpagent as np

import norpagent.nasyncio as core   # 自研核心模块（可调用）



core.EventLoop                      # 自研事件循环类

loop_rt = np.nasyncio()             # LoopRuntime 默认实现（同 core()）

```



0.7 旧地址 `norpagent.loops.std_asyncio:StdLoopRuntime` 保留为兼容垫片（不 import asyncio），历史代码不失效。详见开发手册第 4 章。



### 安全系统：完全剥离，钩子零干预



安全系统与钩子管线解耦：`norpagent.safe()` 默认只挂运行态策略（人工审批 / 网络策略 / 插件加载策略），**不订阅任何钩子**，钩子管线保持纯净。干预钩子的权力完全交给用户：



```python

from norpagent.safe import safe



safe(reg, level="high", hooks=True)     # 安装时即挂钩子

kit = safe(reg, level="high")           # 默认：零干预

kit.install_hooks(reg)                  # 之后手动挂载

kit.uninstall_hooks(reg)                # 卸载（只移除安全套件自己的订阅者）

kit.hooks_installed(reg)                # 查询挂载状态

```



防护能力保留为独立 API（`kit.scan_input()` / `kit.harden()` 等），用户可在自己的钩子订阅者或方法覆写中自由调用。详见开发手册第 10 章。



### 9 层 29 钩子



每个执行结构都是独立 API，可订阅 / 改写 / 否决，支持自定义钩子与自定义层：



- 输入层（before_input / input_pipeline 等）——含 veto 否决语义；

- 模型层、工具层、输出层、会话层、任务层、生命周期层、总线层等 9 层共 29 个钩子。



完整清单见开发手册第 9 章与附录 B。



### 预设模式



模式说明


minimal：最小闭环：mock 模型 + 最小工具集

standard：标准装配（默认）

ptc：PTC 沙箱执行（run_python 子进程隔离）

creative：创造模式：从 .py 文件加载自定义模式

longrun：长周期任务协作（persistent 调度器）

embedded：嵌入式：纯内存组件、默认 headless、零磁盘依赖



### 嵌入式与超高并发



**嵌入式**（依赖面最小化）：



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



也可以直接 `np(preset="embedded")` 开箱即用（默认 headless，不启动 HTTP 服务）。



**超高并发**：EventBus 写时复制（每事件省一次监听者列表复制，实测 >160 万事件/秒）；SSE 有界背压（默认丢最旧，慢客户端不再无限吃内存，且支持运行中热改变）；SSE 帧批量写出 + 断连 ≤1s 快速回收；HTTP 并发调优（监听积压、反向代理缓冲禁用）。详见开发手册第 14 章。



### 顶层 API 速查



```python

import norpagent as np



np()                          # 启动默认 Agent（= np.launch()）

np.stop()                     # 生命周期轮询：返回 True 表示应用已结束

np.current()                  # 当前引擎（NorpEngine）

np.submit("你好")              # 纯 API 提交

np.remount(model="...")       # 热挂载槽位

np.shutdown()                 # 停机

np.nasyncio()                 # 自研事件循环（LoopRuntime）

np.safe(...)                  # 安全策略挂载

np.register_slot(...)         # 槽位表热插拔

np.unregister_slot(...)

np.snapshot_slots()

np.is_builtin_slot("model")

```



---



## 项目结构



```

norpagent/

├── arch/          # 架构层：ArchLayer / SlotSpec / 槽位表热插拔 / 地址解析

├── kernel/        # 最小内核：Registry / EventBus / AgentRuntime / 预设

├── loops/         # 事件循环架构函数（nasyncio 默认实现 + std 兼容垫片）

├── nasyncio.py    # 自研异步 IO 核心（原 nasync_io，零 asyncio 依赖）

├── runtime/       # np() 启动与生命周期：launch / stop / remount / NorpEngine

├── hooks/         # 9 层 29 钩子体系

├── security/      # 安全管线（审批 / 审计 / 签名）——与钩子解耦，默认零干预

├── safe.py        # norpagent.safe()：安全套件一句话挂载

├── models/        # 模型适配器（mock / openai_compat / anthropic）

├── tools/         # 工具集（echo / get_time / run_python / file_* / web_* 等）

├── sessions/      # 会话存储（memory / sqlite）

├── sandboxes/     # 沙箱（pooled / PTC 子进程隔离）

├── frontends/     # 前端外壳（console / headless / web）

├── builtin/       # 内置组件与 Web UI（HTTP + SSE，零依赖，懒导入）

├── modes/         # 六种预设模式

├── plugins/       # 插件系统（签名 -> 审计 -> 导入限制 -> 注册）

├── protocols/     # 组件协议

├── flows/         # 执行流程

├── cli.py         # 命令行入口（python -m norpagent）

└── __init__.py    # 模块即入口：np() 可调用模块

```



---



## 文档与测试



- **完整开发手册**：`test/docs/DEVELOPER_MANUAL.md`——17 章 + 3 附录：架构与数据流、槽位表热插拔、nasyncio 事件循环（含「不依赖标准 asyncio」完整声明）、前端体系、钩子、安全、插件、嵌入式与超高并发部署、迁移指南、FAQ、API 索引；

- **钩子速查**：`test/docs/hooks.md`；

- **回归测试**：`test/_verify_*.py` / `test/_smoke_*.py`——覆盖 nasyncio 迁移、取消语义、热挂载循环、槽位热插拔、嵌入式与并发、WebUI 冒烟、front.html 迁移等全部专项。



---



## 许可



Copyright (c) 2026 xingluosama121, MIT Licensed



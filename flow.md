# NORP FLOW · 模块流程编排使用文档

> 「模块流程 FLOW」是 norpagent 的独立前端分类（`/flow`）。
> 它把 Agent 的组装过程变成一张可视化画布：模块是方块，注册的钩子是端口，
> beam 连线就是执行链路。**画布上的 RUN 不是动画演示——图会被提交给
> 后端，用注册表里的真实组件（模型 / 工具 / 沙箱 / 插件 / 安全）拓扑执行。**
>
> 版本 0.6.2 起：**已废除离线模式**——没有 norpagent 后端时页面拒绝加载；
> 输入框在 TR（入口）卡片上，输出控制台在 OUT（出口）卡片上。

Copyright (c) 2026 xingluosama,MIT Licensed

---

## 1. 快速开始

```python
import norpagent as np

np()                    # 默认 Web 前端，控制台打印 listening on 地址
```

浏览器打开：

| 入口 | 说明 |
|---|---|
| `http://127.0.0.1:8787/` | WebUI 聊天主界面 |
| `http://127.0.0.1:8787/flow` | **模块流程编排**（本页） |

WebUI 顶栏的「🧩 流程编排」按钮以全屏浮层内嵌这个独立分类——
融合入口，不覆盖聊天界面。

**无后端 = 拒绝加载**：直接以 `file://` 打开、或后端不在线时，
页面显示「未检测到 norpagent 后端」遮罩与原因，可重试 / 刷新。
必须先启动 np() 后端，再从 `http://127.0.0.1:8787/flow` 访问。

---

## 2. 界面总览

```
┌─────────────────────────────────────────────────────────────┐
│ 顶栏   模块流程 FLOW | WebUI KEEP | Headless KEEP  [返回]    │
│        [RUN] [STOP] [清空画布] [自动排列] [导出] [导入]       │
│        [应用到智能体] [设置] [帮助]                          │
├─────────────────────────────────────────────────────────────┤
│ 状态条  后端已连接 · 真实执行 | 模块 N | beam N | 热插拔 N    │
│         节点错误 N | 流程中断 N | 注册回退 N | 自动保存 ·     │
│         已保存 | 项目根目录 | 时长                           │
├──────────────────────────────┬──────────────────────────────┤
│  画布（模块 + beam 链路）      │  SYSTEM LOG（事件日志）       │
│                              │                              │
│  ┌─────────┐                 │  热插拔 / 注册 / 执行 /        │
│  │ TR 输入卡 │                 │  回退 全量记录                 │
│  └─────────┘                 │                              │
│  ┌─────────┐                 │  节点配置面板（选中节点时）     │
│  │ OUT 输出卡│                │  实例下拉 + 输入值编辑          │
│  └─────────┘                 │                              │
├──────────────────────────────┴──────────────────────────────┤
│ 底部模块坞  流程节点 | 核心模块(真实注册组件) | 模板 | 自定义   │
│            （横向滚动条 + 滚轮横滚 + 卡片文字自动换行）          │
└─────────────────────────────────────────────────────────────┘
```

- **输入在 TR 卡片上**：TR（入口）卡内嵌 prompt 输入框；
- **输出在 OUT 卡片上**：OUT（出口）卡内嵌滚动控制台，RUN 后每个
  节点的输出实时流式滚入；每个 OUT 卡自己的最终结果也会写进它自己的
  控制台（多 OUT 卡各自收各自的最终值）；
- 点击节点，右侧面板出现**实例选择与输入值编辑框**；
- 每个节点方块底部有一行 `◈ 实例 · ▸ 输入值` 预览；
- **输入框体系**：一切需要输入的地方都是输入框——FE / 全局设置节点
  卡片上每个配置项一行输入框（IN 端口 · 标签 · 输入框 · OUT 端口），
  model / tool / sandbox 等节点卡片底部带值输入框带（context /
  query / code…），模型字段可直接手输任意远端模型名；
- **画布管理三件套**（防画布乱局，误触注入已移除）：
  - `Alt+左键拖拽` 空白处 = **框选**，与矩形相交的模块全部高亮，
    `Del` 一次批量删除；
  - `Ctrl+A` = **全选**所有模块，`Del` 批量删除；
  - 顶栏 **「清空画布」** 按钮 = 一键删除全部模块与 beam（弹确认框
    防误触），确认后立即自动保存；清空后刷新页面保持空白，不再
    回落示例模板；
  - **双击空白注入**与**单击坞卡片快速注入**已移除：误触曾导致
    打开画布冒出一大堆 other 节点并被自动保存固化。现在注入只有
    一条途径——从模块坞**拖拽**卡片上画布（或拖入文件）；
- 底部模块坞支持**横向滚动条 + 鼠标滚轮横滚**，卡片长名称自动换行截断。

---

## 3. 模块坞 = 注册表快照（自动检测）

后端连接成功后，核心模块坞**按注册表快照真实渲染**——每个已注册组件
一张卡片；页面获得焦点 / 流程执行完 / 文件注册后自动重新拉取快照，
组件增删热更新到坞：

| 分组 | 来源 | 拖入画布生成的节点 |
|---|---|---|
| 模型（自动检测） | `registry.list_models()`，默认模型带 DEFAULT 标记 | `model` 节点（prompt / context / tools / **system_prompt** 输入） |
| 工具（每工具独立钩子） | `registry.list_tools()` | `tool` 节点，**端口 = 该工具 schema 参数**（卡上 `AGENT` 徽章 = 挂到 front 智能体，见第 9 节） |
| 会话 | `registry.list_sessions()` | `session` 节点 |
| 沙箱 | `registry.list_sandboxes()` | `sandbox` 节点 |
| 调度器 | `registry.list_schedulers()` | `other` 节点（直通） |
| 插件 | `registry.list_plugins()` | **插件容器**（内部铺开其工具与钩子） |
| **插件钩子** | 每个插件的每个注册钩子 | **`hook` 节点（一个钩子 = 一个节点）** |
| 预设 | `registry.list_presets()` | `model` 节点（按预设解析模型） |
| **前端 FE** | 流程模块目录里的 `.html/.js/.ts`（文件即前端） | **`frontend` 节点**（卡片带「↗」新标签页打开，见第 8 节） |
| 工具容器 | 固定卡片 | `toolbox` 容器节点（空容器，拖工具/插件卡入内） |

**每工具独立钩子（不再是全局 query/result 黑箱）**：后端从工具的
OpenAI function schema 提取真实参数作为输入端口。例如 `web_fetch`
的端口是 `url / max_chars / timeout → result / success`，
`context_add` 是 `text / source / title / metadata → result / success`。
beam 连到哪个端口，RUN 时那个值就成为工具对应实参。

**流程节点区（固定卡片，不依赖快照）**：

| 卡片 | 节点 | 端口 |
|---|---|---|
| 入口 TR | `trigger`（卡上内嵌 prompt 输入框） | `start` |
| **路径 PATH** | `path` **路径模块**（卡上内嵌路径输入框） | `base` → `path` / `resolved` |
| 出口 OUT | `output`（卡上内嵌输出控制台） | `final` |
| 工具容器 TB | `toolbox` 容器（可装任意模块） | 成员并集 |
| **前端 FE** | `frontend` 前端模块（配置集合，形态 1/2/3 切换） | 每个配置项 in+out 双向端口 |
| **全局设置 SET** | `settings` 全局设置节点（写入连接设置） | 每个配置项 in+out 双向端口 |

**路径模块（PATH）**专门对接文件类工具的 `path` 端口：`file_read /
file_write / file_list / file_delete` 等工具的 schema 都带 `path`
参数，因此它们的工具节点上都有 `path` 输入端口。把 PATH 节点的
`path` 输出 beam 到这些端口即可统一注入路径值。**路径值直接输入
在 PATH 卡片上的输入框里**（占位符提示「路径 · 空 = 根目录 "."」），
输入即生效、无需打开侧栏；也可通过 `base` 输入端口由上游 beam
投喂。路径值会经过与文件工具完全相同的公共安全校验（拒绝绝对
路径 / `..` 穿越），空值 = 工作区根目录 `.`（快照里的
`workspace_root`）。「文件读取链路」模板（路径 → file_read →
出口）可直接拖出体验。

---

## 4. 工具 / 插件容器（接到 AI 的 tool 端点）

**容器 = 可装模块的「TB 工具箱」**：任意模块（工具 / 钩子 / 会话 /
沙箱 / 模型 / 安全 / 调度器 / 预设 / 文件模块）都可以**拖入容器装入**，
成员显示在 TB 标题栏下方，**从上到下依次排列**（成员行 = 图标 +
名称 + 端口数 + 状态点 + × 移除）。

**容器外侧端口 = 每个子模块的端口 + 容器自身的端口**：

- **输入端口** = 自身输入 + 成员钩子参数的并集：值按端口名**扇出**
  给每个拥有该参数的成员；
- **输出端口** = 自身输出（如 `tools` 打包端口）+ 每个成员的限定名
  端口（`成员名.端口名`，如 `greet.result`，杜绝命名冲突）；
- 把容器的 `tools` 端口用 beam 连到 **AI（模型）节点的 `tools` 端口**，
  即完成工具集挂载——RUN 时后端把容器内全部工具解析成 schema，
  作为 `tools` 传给模型 provider（openai_compat 等支持 tool call 的
  适配器直接生效）；
- **装 / 卸成员**：画布上把模块拖到容器上 = 装入；底部坞的模型 / 工具
  / 会话 / 沙箱 / 调度器 / 预设 / 插件 / 钩子卡都可以直接拖进容器；
  **把成员行拖出容器 = 脱离为独立模块**；成员行 × = 热移除；
  删除容器只解散成员（成员物化为独立节点保留在画布，不级联删除，
  链路安全）；
- 插件卡拖入画布 = 生成**插件容器**，内部自动铺开该插件的工具成员
  （各自 schema 端口）与钩子成员（各自参数端口），黑箱消失；
- 入口（TR）/ 出口（OUT）与容器节点本身不支持装入（入口/出口是流程
  终端，容器不支持嵌套）；
- 拖动容器头部 = 整体移动（成员内嵌在容器 DOM 中自动随动）；
  容器成员在 RUN 时由容器统一执行（状态显示「容器成员」），
  避免重复调用；成员被外部 beam 直接消费时才会单独执行一次。

---

## 5. 节点执行语义（后端真实执行）

RUN 时后端把画布图按拓扑序执行，每个节点独立容错：
**单节点失败只记录 error，不中断整条链路**（零中断语义）。

| 节点类型 | 真实动作 | 主要输入 | 输出端口 |
|---|---|---|---|
| `trigger` | 读取 TR 卡上的 prompt | 卡片输入框 | `start` |
| `path` | **路径模块**：产出经公共路径安全校验（拒绝绝对路径 / `..` 穿越）的相对路径值，空值 = 工作区根目录 `.`；可 beam 到任何工具的 `path` 输入端口 | **卡片上内嵌的路径输入框** / 上游 `base` | `path`、`resolved` |
| `model` | 调用注册表真实模型；`tools` 端口接收容器挂载的工具集；**`system_prompt` 端口 = 系统提示词**（优先级：beam 值 > 输入面板 > 节点配置 > 引擎预设参数，空值不注入 system 消息） | `prompt` + `context` + `tools` + `system_prompt` | `inference`、`reasoning` |
| `tool` | 真实调用；**每个输入端口 = 一个 schema 参数** | schema 参数端口 | `result`、`success` |
| `toolbox` / 插件容器 | 逐成员独立执行（tool / hook / session / sandbox / model / security 等任意成员），结果输出到 `成员名.端口名` 限定端口；单个成员失败不中断容器 | 并集端口 | `tools` + `*.result` + `*.success` |
| `sandbox` | 注册表真实沙箱 `run_shell`（子进程隔离） | `code` | `output`、`exit_code` |
| `security` | `scan_message` 越狱/注入扫描 | `payload` | `audited`、`blocked`、`reason` |
| `session` | 会话管理器读写（新建 flow 会话） | `context` | `session_id`、`state` |
| `plugin`（独立节点） | 执行插件注册的工具（config.tool / 首个工具） | `query` | `result` |
| `hook` | 触发插件的单个钩子：可变钩子走 `intercept`，返回值成为节点输出 | 钩子参数端口 | `result` |
| `frontend`（FE） | 把合并后的设置项（beam 值 > 面板/卡片值）写入该 FE 的**独立配置**（scope=global 时写全局配置），输出 = 各设置项当前值（供就近连线） | 配置端口（api_key / api_base / model…） | 各配置项 |
| `settings` | 把合并后的设置项写入**全局配置**（`save_config`），等效「连接设置」 | 配置端口 | 各配置项 |
| `output` | 汇总最终结果（写进该卡自己的控制台） | `final` | — |
| `other` / 文件模块 | 直通（payload 原样转发） | `any` | `any` / `output` |

模型节点支持流式：适配器实现 `stream()` 时输出增量实时推送。

---

## 6. 连接设置（Key / BaseURL / 项目根目录）

顶栏「连接设置」按钮打开设置弹窗（**立即显示，不被远端模型拉取阻塞**；
点击弹窗外部不关闭，Esc / × / 取消 / 保存并应用 关闭）：

| 字段 | 说明 |
|---|---|
| API Key | 模型服务密钥（保存到后端配置，与 WebUI 设置共用） |
| API Base URL | OpenAI 兼容服务地址（如 `https://api.deepseek.com`） |
| 项目根目录 | 工作区根目录（状态条「项目根目录」卡片实时跟进） |
| 文件模块落盘目录 | 「文件即模块」的落盘目录（空 = `~/.norpagent/flow_modules`） |
| 默认模型（自动检测） | **输入框 + 提示列表（datalist）**：可直接手输任意远端模型名（即使模型列表拉取失败也能手动输入），留空 = 引擎默认；填了 API Key 后**自动拉取远端模型列表**（也可点「拉取模型列表」手动刷新） |

各输入框**失焦自动保存应用**（弹窗保持打开）；保存即 `POST /api/config`：
配置落盘、应用到运行时（模型按新 Key/BaseURL 重注册）、快照自动刷新；
保存时若已填 API Key 还会自动拉取 provider 远端模型列表填入提示列表。
「拉取模型列表」用**表单里当前的 Key/Base 即时请求**（无需先保存）。
WebUI 的设置面板看到的是同一份配置（互相跟进）；WebUI 设置弹窗里的
模型字段同样是可手输输入框（下拉仅提示）。

---

## 7. 文件即模块（真实注册）

把模块文件**直接拖进画布空白（或拖到已有方块上替换）**：

| 文件类型 | 后端行为 |
|---|---|
| `.py` 插件 | 落盘到文件模块目录，走**完整安全管线**：签名校验 → AST 审计 → 导入限制 → 注册进注册表。注册成功即真实可用：工具进工具表（带 schema 端口）、钩子订阅事件总线、模块坞新增插件卡与钩子卡（一个钩子一个节点） |
| `.json` 模块描述 | 注册为纯描述模块（直通节点），含 `@module / @in / @out / @color` 字段 |
| `.html / .js / .ts` | 注册为**前端模块 FE**（模块坞「前端 FE」分组，托管到 `/fe/<name>`，见第 8 节） |
| `.md / .yaml` 等其他类型 | 后端明确报错「无法执行」，方块**自动回退官方模块**（回退计数 +1），链路保持安全 |

模块标记约定（放在文件头注释里）：

```
@module 模块名        # 模块名称
@in query data        # 输入端口（钩子/端口名）
@out result           # 输出端口
@color #22d3ee        # 方块颜色
```

同一文件内容重复拖入是幂等的（不会重复订阅钩子）。
`.py` 插件的完整格式与 norpagent 插件完全一致（`PLUGIN_NAME`、
`TOOLS`、`execute()`、生命周期钩子函数），示例：

```python
"""
@module 问候插件
@in who
@out result
@color #22d3ee
"""
PLUGIN_NAME = "greeter"
TOOLS = [{"type": "function", "function": {
    "name": "greet", "description": "say hello",
    "parameters": {"type": "object",
        "properties": {"who": {"type": "string"}},
        "required": ["who"]},
}}]

def execute(tool_name, args, ctx):
    return "hello " + str((args or {}).get("who", ""))

def on_task_done(content, summary, ctx):   # 注册的钩子 → 画布上一个钩子节点
    return None
```

---

## 8. 前端模块（FE）与输入框体系

### 8.1 FE 前端模块（文件即前端）

把 `.html / .js / .ts` 文件拖入画布即注册为**前端模块**：

- 模块坞「前端 FE」分组出现该模块卡：图标 + 名称 + `FE · .html` 标记 +
  **「↗」按钮**（在新标签页打开托管页面 `/fe/<name>`）；
- 拖卡入画布 = 生成 **FE 节点**；后端把文件托管到 `/fe/<name>`
  （`Cache-Control: no-store`，重启进程后自动扫描恢复）；
- 每个 FE 拥有**独立配置作用域**（互不干扰），默认值取自「连接设置」
  全局配置；已有记录则用 FE 自己的配置，落盘到
  `~/.norpagent/fe_configs/<fe_id>.json`。

### 8.2 FE 节点形态（1/2/3 切换）

卡片标题栏的 `1 2 3` 按钮切换形态：

| 形态 | 名称 | 行为 |
|---|---|---|
| 1 | 全局设置节点 | 配置写入「连接设置」（scope=global，等同 SET 节点） |
| 2 | FE 即设置集合 | 独立配置作用域（scope=fe，**默认形态**） |
| 3 | 拆散成子卡片 | 每个配置项物化为一个成员行内嵌在 FE 卡里，可单独拖出 / 就近连线 |

### 8.3 输入框体系（一切需要输入的地方都是输入框）

- **FE / 全局设置节点卡片**：每个配置项（`api_key / api_base / model /
  project_root / plugin_dirs / temperature / max_tokens / max_steps /
  task_timeout / system_prompt / language`）渲染成一行
  「IN 端口 · 标签 · 输入框 · OUT 端口」，值直接写在卡片输入框上，
  改完 **500ms 防抖自动保存**（settings → `POST /api/config`；
  FE → `POST /api/fe/config`）；api_key 行是密码输入框；
- **形态 3 成员行**：每个设置项成员行同样带一个输入框（值 = 该项当前
  值）；编辑成员 = 同步更新 FE 配置并防抖保存；成员拖出容器后物化为
  独立节点，卡片底部带值输入框带；
- **普通节点卡片的值输入框带**：model（context）/ tool（query）/
  sandbox（code）/ other 与文件模块（value）等节点的卡片底部带一个
  值输入框，直接编辑节点输入值；TR 卡 prompt 输入框、PATH 卡路径
  输入框保持不变；
- **卡片与右侧面板双向同步**：卡片输入框、右侧面板、TR 卡三者
  编辑同一份 `node.inputs`；
- **模型字段全部可手输**：flow 连接设置弹窗的「默认模型」、WebUI
  设置弹窗的「模型」、model 节点的实例字段，都是**输入框 +
  datalist 提示列表**——远端模型列表拉取失败（如 401）或模型不在
  列表里时，直接键入任意模型名即可；留空 = 引擎默认（模型节点则
  跟随引擎默认模型）；
- **连线即时生效**：beam 连到设置端口时，连线动作本身就把上游当前值
  写入独立 / 全局配置（就近连线只影响连到的那个 FE / 设置节点）；
  RUN 时 `frontend` 节点执行 = 把合并后的设置项（beam 值 > 面板值）
  写入配置。

---

## 9. 模块工具挂载到 front 智能体（自动调用）

「文件即模块」注册的工具默认只参与画布流程。想让 **front 聊天里的
智能体自动调用它们（tool calling）**，有两种等价方式：

### 9.1 flow 页：工具卡「AGENT」开关

底部模块坞「工具」分组的每张工具卡（原生 + 文件模块）右下角都有一个
**`AGENT` / `+AGENT` 徽章**：

- 点一下 = 挂载：工具进入智能体工具集，徽章变绿（`AGENT`），
  状态提示「已挂载到智能体」；
- 再点一下 = 卸载，徽章恢复灰色（`+AGENT`）；
- 挂载 / 卸载经 `POST /api/agent/tools` 热应用：**下一次 front 聊天
  立即生效**，无需重启或重开开关——模型可以直接调用该工具，
  工具调用过程照常在聊天界面以「工具调用」事件展示。

### 9.2 WebUI 设置：智能体工具清单

front 主界面「设置」弹窗新增 **「🧰 智能体工具（Tool Calling）」** 区：

- 列出注册表**全部工具**（原生 + 插件 / 文件即模块），每行 =
  勾选框 + 工具名 + 描述 + 来源徽章（原生 / 模块）；
- 勾选 = 挂载，取消 = 卸载，保存后立即生效；
- 未勾选过 = **跟随预设默认集**（状态栏显示「跟随预设默认集」）；
- 「恢复预设工具」一键回到预设默认集；
- 文件模块卸载（文件不再加载）后，配置里残留的工具名会被自动
  过滤，不会导致报错。

### 9.3 语义与实现

- 配置键：`agent_tools`（显式工具全集）+ `agent_tools_explicit`
  （True = 显式；False / 空 = 跟随预设默认集）。挂载结果等于预设
  默认集时自动回落为非显式（预设演进自动跟随）；
- 生效方式：保存时把 `preset.tools` 热改写为「预设默认集 + 模块
  工具」（或显式全集），下一次 `run()` 生成 tool schemas 即包含
  模块工具，模型按 OpenAI function schema 自动调用；
- 挂载不区分来源：原生工具、插件工具、「文件即模块」工具一视同仁；
- 快照字段：`/api/flow/snapshot` 返回 `agent_tools`（当前生效集）与
  `agent_base_tools`（预设默认集），flow 页据此渲染徽章状态；
- 与「应用到智能体」开关的区别：开关 = 聊天任务整体改走画布流程；
  工具挂载 = 常规 Agent 循环里把模块工具加入可调用工具集。两者
  可以同时用（画布流程里也能挂工具给模型节点）。

---

## 10. 运行与事件流

1. 在 **TR 卡片的输入框**里写 prompt（或选中 TR 节点在右侧编辑，两者同步）；
2. 点击 **RUN** → 前端把 `{nodes, links, prompt}` 提交到
   `POST /api/flow/run`，返回 `flow_id`；
3. 后端在线程中拓扑执行，进度经 **SSE（`/events`）** 实时推送；
4. 每个节点实时亮状态：`运行中 → 完成 / 出错 / 等待输入 / 容器成员`
   （容器成员行内圆点同步变色），输出逐条滚入 **OUT 卡片控制台**与
   右侧日志；
5. **STOP** → `POST /api/flow/stop`，在节点边界安全收尾；
6. 结束统计：节点错误数、流程中断数、最终输出（写进 OUT 卡）。

### SSE 事件（type: `flow.*`）

| 事件 | payload 关键字段 |
|---|---|
| `flow.node_start` | `flow_id / node_id / label / type` |
| `flow.node_output` | `node_id / port / output / stream`（流式增量 stream=true） |
| `flow.node_done` | `node_id / status(done\|error\|wait\|boxed) / outputs` |
| `flow.node_error` | `node_id / label / error` |
| `flow.log` | `level / message` |
| `flow.done` | `status(done\|stopped\|error) / final_output / errors / interrupts / nodes` |

---

## 11. 自动保存 / 应用到智能体（行为热切换）

**画布自动保存**：任何画布变化（增删节点 / 连线 / 改输入值 / 拖动
位置 / 容器成员装卸 / 面板配置）都会在 **1.5 秒内自动保存到后端**
（状态条「自动保存 · 已保存」实时显示状态），无需手动 Ctrl+S。
保存内容 = 与「导出流程」同构的完整图（含坐标 / 配置 / 输入值 /
TR 卡片 prompt），落盘到 ``~/.norpagent/flow_graph.json``
（环境变量 ``NORPAGENT_FLOW_GRAPH`` 可覆盖）。刷新页面或重启
``np()`` 进程后画布**原样自动恢复**（有保存内容时不再注入演示模板）。

**应用到智能体（顶栏开关）**：开启后，保存请求携带
``active=true``——**front 主界面（WebUI 聊天）的每个任务都改为按
当前流程执行**：

1. 用户在 front 里发送消息 → 后端不跑常规 Agent 循环，而是把消息
   作为流程入口 prompt，用注册表里的真实组件拓扑执行画布图；
2. 节点级进度以 ``flow.*`` 事件经同一 SSE 通道推送——此时打开
   ``/flow`` 页面可以看到节点实时亮起、日志滚动（与画布 RUN 一致）；
3. 流程最终输出（OUT 节点汇总值）作为**助手回复**渲染在 front
   聊天界面（on_content 事件），本轮对话写入会话历史，上下文连续；
4. front 的「停止」按钮同样生效（节点边界安全收尾）；
5. 关闭开关（active=false）后 front 立即恢复常规智能体行为。

「应用到智能体」只影响 front 聊天任务；``/flow`` 页面自己的 RUN
始终直接执行当前画布，与开关状态无关。

### 前端保存格式与执行格式

自动保存 / 导出共用**前端序列化格式**（含 ``x/y`` 坐标、
``container``、``members`` 等展示字段）；画布 RUN 时前端会额外补齐
``config.delegate_to`` / ``config.members`` / ``config.tools``。
后端 ``norpagent.flows.normalize_graph()`` 负责把保存格式规范化成
执行格式（幂等），保证「应用到智能体」的聊天任务与画布 RUN 的
执行语义完全一致。

---

## 12. REST API 参考

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/flow/snapshot` | 注册表快照：models（含 default 标记）/ tools（**含 schema 端口 ins/outs**）/ sessions / sandboxes / schedulers / plugins（含钩子）/ presets / hooks / 安全姿态 / modules_dir |
| POST | `/api/flow/run` | `{graph:{nodes,links,prompt}}` → `{ok, flow_id}`（异步，事件走 SSE） |
| **POST** | **`/api/flow/save`** | **自动保存：`{graph, active}` → `{ok, active, nodes, beams}`；active=true 时激活「应用到智能体」** |
| **GET** | **`/api/flow/load`** | **返回上次保存的图与激活状态：`{ok, active, graph}`（页面刷新后恢复）** |
| POST | `/api/flow/stop` | `{flow_id}` → `{ok}` |
| POST | `/api/flow/register` | `{name, content}` → `{ok, module}` 或 `{ok:false, reason}`（.html/.js/.ts → `module.kind=frontend`） |
| GET/POST | `/api/config` | 连接设置读写（api_key / api_base / project_root / flow_modules_dir / model，与 WebUI 共用） |
| **GET** | **`/api/fe/config?fe_id=`** | **读取某 FE 的独立配置（无记录时返回全局配置副本）** |
| **POST** | **`/api/fe/config`** | **保存某 FE 的独立配置 `{fe_id, config}` → `{ok, fe_id, config}`（原子落盘）** |
| **POST** | **`/api/agent/tools`** | **智能体工具挂载 `{tools:[...], explicit:true}` → `{ok, agent_tools, explicit, dropped}`（与预设默认集一致自动回落非显式，未注册工具名自动丢弃）** |

### 后端内核（`norpagent.flows`）

```python
from norpagent.flows import (build_snapshot, ModuleWorkspace,
                             FlowRunner, normalize_graph)

snap = build_snapshot(registry, agent)          # 注册表快照（含工具 schema 端口）
ws = ModuleWorkspace("/path/to/modules")        # 文件即模块工作区
res = ws.register(registry, "my_plugin.py", source)  # 安全管线注册
runner = FlowRunner(registry, agent, publish=cb)     # 流程执行器
result = runner.run(normalize_graph(saved))     # 保存格式 → 执行格式 → 拓扑执行
```

容器节点在图中以 `config.members`（`{kind, name, plugin?}` 成员列表，
向后兼容 `config.tools`）+ 成员节点的 `config.delegate_to`（指向容器）
表达：容器按端口并集统一执行成员，成员不重复执行。

---

## 13. 安全说明

- `/api/flow/register` 上传的 `.py` **会被执行**（注册为插件）。
  它复用与「插件目录」完全相同的安全管线（`plugin_security_audit`
  默认 `block`、`plugin_security_import_restrict` 默认 `strict`、
  签名校验默认开启），危险导入在加载前即被拒绝；
- Web UI 默认仅监听 `127.0.0.1`，流量不出本机；
- 模块文件大小上限 200KB；文件名会被净化，防路径穿越；
- 文件模块注册失败时前端自动回退官方模块，且不破坏已有链路；
- 流程执行中，`sandbox` 节点的代码始终在注册表沙箱（子进程隔离）
  中运行，不进主进程。

---

## 14. 常见问题

**Q：页面打不开，显示「未检测到 norpagent 后端」？**
离线模式已废除。请先启动 np() 后端，再从
`http://127.0.0.1:8787/flow` 访问（file:// 直接打开会被拒绝）。

**Q：刷新页面后连线歪了？**
页面已在字体就绪 / 动画结束 / 窗口变化后多阶段重测 beam 锚点；
若仍偏移，拖一下任意模块即触发全量重绘。

**Q：RUN 之后没反应？**
- 看状态条：`后端已连接 · 真实执行` = 后端真实执行；
- 输出在 **OUT 卡片**的控制台里（右下角日志也有全量记录）；
- 模型节点未选实例时使用引擎预设模型；无 API Key 时预设回落 mock 模型。

**Q：工具节点为什么有这么多端口？**
每个端口对应工具 schema 里的一个真实参数（如 `url`、`max_chars`），
beam 连到哪个端口就传哪个参数——不再是全局 query/result 黑箱。

**Q：如何把工具交给 AI 使用？**
把工具卡拖进「工具容器」（或拖到已有容器上装入），再把容器的
`tools` 端口连到模型节点的 `tools` 端口即可；容器外侧端口 =
成员端口并集 + 容器自身端口，会随成员自动变化。

**Q：容器里能装什么？**
工具、插件钩子、会话、沙箱、模型、调度器、预设等任意模块都能拖入
容器装入，成员显示在 TB 标题栏下方从上到下排列；把成员行拖出容器
即脱离为独立模块；入口/出口与容器本身不支持嵌套。

**Q：拖入 .js 文件报错？**
后端把 `.html / .js / .ts` 注册为**前端模块 FE**（托管到 `/fe/<name>`，
拖入画布生成 FE 配置节点），不再报错。其余无法执行的类型（.md /
.yaml 等）会报错并自动回退官方模块；要执行逻辑请改写成 `.py` 插件。

**Q：FE 节点的配置在哪里改？**
FE 节点卡片上**每个配置项一行输入框**（IN 端口 · 标签 · 输入框 ·
OUT 端口），值直接写在卡片上改完自动保存；选中节点后右侧面板也有
同源输入框；形态 3 的每个子卡行同样是输入框。连线到配置端口即时
生效。独立配置存于 `~/.norpagent/fe_configs/<fe_id>.json`。

**Q：模型字段是下拉框，拉取模型列表失败就选不了模型怎么办？**
所有模型字段已改为**输入框 + 提示列表（datalist）**：flow 连接设置
弹窗、WebUI 设置弹窗、model 节点实例字段都可以直接手输任意远端
模型名（如 deepseek-v4-flash），列表只是提示，拉取失败不影响手输。
留空 = 引擎默认。

**Q：流程能保存 / 复用吗？**
画布**自动保存**：任何变化 1.5s 内自动落盘到后端
（`~/.norpagent/flow_graph.json`），刷新 / 重启进程自动恢复；
`Ctrl+S` 或「导出流程」得到 `norp-flow.json`（含节点配置与输入值），
拖回画布或「导入流程」即可还原（导入内容同样进入自动保存）。

**Q：如何让流程接管整个智能体的行为？**
打开顶栏「应用到智能体」开关：front 主界面的聊天任务改为按当前
流程执行（消息作为入口 prompt，最终输出作为助手回复，进度在
`/flow` 页面实时可见）。关闭开关即恢复常规智能体行为。

**Q：一打开画布就冒出一大堆 other 节点怎么办？**
两个误触注入源（双击画布空白注入、单击坞卡片快速注入）已移除，
现在注入只有拖拽一条途径，不会再误触铺节点。历史遗留的乱局用
顶栏「清空画布」一键清掉（弹确认框后立即保存）；也可以用
`Alt+拖拽` 框选或 `Ctrl+A` 全选后 `Del` 批量删除。

**Q：deepseek-chat / deepseek-reasoner 还能用吗？**
不能。DeepSeek 官方已于 2026-07-24 15:59 UTC 停用这两个模型名，
现役模型为 `deepseek-v4-flash`（默认，便宜快）与 `deepseek-v4-pro`
（重推理）。旧名会被前端提示列表与远端模型坞自动过滤；历史缓存
中的旧名同样不再展示。思考模式由 `thinking` 参数控制（V4 默认
开启思考），后端适配器已按官方契约处理 `reasoning_content` 回传
与 `thinking: enabled/disabled`。

**Q：模型节点的 System Prompt 在哪里设置？**
模型节点新增 `system_prompt` 输入端口：选中节点后在右侧面板
填写，或由上游 beam 投喂。写入 system 消息定义智能体角色；
beam 值 > 输入面板 > 节点配置 > 引擎预设参数，全空则不注入。

**Q：如何让流程输出进入聊天会话？**
在流程里接 `session` 节点（会写入会话历史），之后在 WebUI 聊天中
选择对应会话即可看到。流程本身的最终输出展示在 OUT 卡片控制台；
「应用到智能体」开启时，聊天回复本身就是流程输出，且自动写入
当前会话历史。

**Q：拖入的 .py 工具怎么让 front 聊天里的智能体直接调用？**
两条路任选：① `/flow` 模块坞工具卡右下角的 `+AGENT` 徽章点一下
（变绿 `AGENT` = 已挂载）；② front 设置弹窗「🧰 智能体工具」清单
里勾选该工具。挂载后下一次聊天即生效——模型按工具的 OpenAI
schema 自动调用，无需画布流程。取消勾选 / 再点徽章即卸载。

**Q：挂载的模块工具为什么不见了 / 报错？**
模块文件若不再加载（文件被替换 / 未注册），`agent_tools` 配置里
残留的工具名会被自动过滤，不会报错；重新注册后重新勾选即可。
「恢复预设工具」可一键回到预设默认集（agent_tools 回落为非显式）。


# 预设模式开发者文档：standard / ptc / minimal / creative

一种模式 = 一份声明式组件组合（`Preset`）：只声明「用哪些组件 +
什么行为参数」，不含任何实现。开发者新增模式 = 新建一个 `Preset`。

`np()` 不指定 preset 时默认使用 **standard**（全量工具，模型知道全部
可用工具）；未提供 API 凭据时模型自动回落 `mock` 并回复引导性提示，
用户在前端「设置」配置模型与 Key 后即切换真实模型。

## 1. standard — 功能完整的通用编码助手

- **定位**：开箱即用的完整智能体（生产级默认）。
- **组件**：`sqlite` 会话 + `pooled` 沙箱 + `persistent` 调度 +
  `fts5` 上下文库 + `basic` 项目管理 + 17 项工具全集。
- **工具全集**：文件读写删列、命令执行、联网检索、上下文管理、
  项目状态、任务提交/查询/取消、echo/get_time。
- **行为参数**：`max_steps=128`、`call_timeout=0`（不限时）、`task_timeout=0`。
- **适用**：日常编码任务；叠加 `norpagent.safe(reg)` 即达生产安全姿态。

```python
from norpagent.modes import register_all_presets
reg = Registry(); install_defaults(reg); register_all_presets(reg)
agent = AgentRuntime(reg, preset="standard")
```

## 2. ptc — 用代码组合多步工具调用

- **定位**：需要条件 / 循环 / 批量聚合的编排任务。
- **区别**：模型不逐个发起工具调用，而是生成 Python 代码，在代码中
  通过 `call_tool(工具名, **参数)` 组合调用，交给 `run_python` 执行。
- **执行模型（P4 起）**：代码在**沙箱子进程**中隔离执行——AST 静态
  预检（禁 import / 双下划线访问 / 魔法下标）+ 受限 builtins + 干净
  函数命名空间 + 超时强杀进程树 + 输出上限；`call_tool` 经协议通道
  回传宿主执行。沙箱未实现 `run_python` 时回退进程内受限执行。
- **可调参数**：`ptc_timeout`（代码执行超时秒，默认 60）。

```python
# 模型生成的典型 PTC 代码
files = call_tool("file_list", path=".")
summary = []
for f in files.splitlines()[:3]:
    summary.append(call_tool("file_read", path=f, max_lines=5))
print("\n---\n".join(summary))
```

## 3. minimal — 仅基础工具，模型基准测试

- **定位**：评估模型本身的指令遵循 / 工具调用能力，变量最少。
- **组件**：`memory` 会话 + `subprocess` 沙箱 + `simple` 调度，
  仅 `echo` / `get_time` 两个工具，无任何可选依赖。
- **配套基准**：内置 `mock` 模型确定性输出，可在无网、无 SDK 的
  环境完整跑通（CI 基准 / 论文复现）。

```python
agent = AgentRuntime(reg, preset="minimal")
result = agent.run("你好")
assert result.ok
```

## 4. creative — 调试与创建自定义模式

- **定位**：模式的「试验台」：固定使用 `mock` 模型 + 最小工具集
  （`echo` / `get_time` / `run_python`），保证调试环境确定可复现，
  便于观察事件流、调参、验证自定义模式文件。
- **从文件加载**：`load_preset_file(path)` 加载模块级 `PRESET`
  变量（Preset 实例或 dict），CLI：`norpagent --mode-file my_mode.py`。
- **注册**：`reg.register_preset(preset)`，之后与内置模式完全同权。

```python
# examples/custom_mode_file.py 的形态
from norpagent.kernel.presets import Preset
PRESET = Preset(
    name="my_mode",
    description="自定义模式",
    model="mock",
    tools=["echo", "get_time", "file_read"],
    session="memory",
    params={"max_steps": 16, "system_prompt": "你是我的助手"},
    components={},
)
```

## 模式字段速查

| 字段 | 说明 |
|---|---|
| `model` | 模型名（注册表 `register_model`） |
| `tools` | 工具名列表 |
| `session` | memory / sqlite（注册表 `register_session`） |
| `sandbox` | subprocess / pooled（`register_sandbox`） |
| `scheduler` | simple / persistent（`register_scheduler`） |
| `ui` | console / web（`register_ui`） |
| `mode` | single / ptc / custom |
| `components` | {种类: 名字}（`register_component`） |
| `params` | max_steps / temperature / call_timeout / task_timeout / system_prompt / workspace_root / ptc_timeout / 安全开关 |

## 常见定制场景

| 需求 | 做法 |
|---|---|
| 换大脑 | `reg.register_model("my_llm", provider)` + `model="my_llm"` |
| 加工具 | `reg.register_tool("my_tool", tool)` + 加入 `tools` |
| 换会话存储 | `reg.register_session("redis", factory)` |
| 换沙箱（容器） | `reg.register_sandbox("docker", provider.create)` |
| 单任务覆盖 | `agent.run(text, task_params={"max_steps": 8})` |
| 子 Agent 编排 | `task_submit` 工具 + `preset_name` 指定不同模式 |

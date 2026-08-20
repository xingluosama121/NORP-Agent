# norpagent 钩子体系：9 层 29 钩子

> 设计原则：**每一个执行结构都必须暴露为 API，并且可以被钩子干预。**
> 每个钩子都是独立的模块级 API；支持自定义钩子、自定义层；零依赖，标准库自带。

## 一分钟上手

```python
from norpagent import Registry, AgentRuntime
from norpagent.hooks import before_model_call, after_tool_call, HookVeto

reg = Registry()

# 1) 模块级独立 API：直接订阅
before_model_call.subscribe(log_request, system=reg)

# 2) 运行时绑定视图（与 reg.hooks 同一总线）
agent = AgentRuntime(reg, preset="minimal")
agent.hooks.after_tool_call.subscribe(on_result)

# 3) 可变钩子：改写数据流
def rewrite_input(event):
    return event.get("user_input", "").strip()          # 返回 str = 替换输入
agent.hooks.before_input.subscribe(rewrite_input)

# 4) 一票否决
def veto_rm(event):
    if event.get("tool_name") == "file_delete":
        raise HookVeto("file_delete 已被策略禁止")
agent.hooks.before_tool_call.subscribe(veto_rm)
```

## 九层结构

| 层 | 关注点 | 钩子 |
|---|---|---|
| L1 | 运行时生命周期 | `on_agent_init` `on_agent_shutdown` |
| L2 | 任务生命周期 | `on_task_start` `on_task_done` `on_task_error` `on_task_stopped` `on_task_timeout` |
| L3 | 输入管线 | `before_input`* `after_input` `on_user_input_required` |
| L4 | 会话与历史 | `before_session_create`* `after_session_create` `before_message_append`* `after_message_append` |
| L5 | 消息组装 | `before_build_messages`* `after_build_messages`* |
| L6 | 步骤 | `before_step`* `after_step` |
| L7 | 模型调用 | `before_model_call`* `after_model_call`* `on_reasoning` `on_content` `on_event` `on_usage_update` |
| L8 | 工具调用 | `before_tool_call`* `after_tool_call`* `on_tool_error` |
| L9 | 结果定型 | `before_result`* `after_result`* |

带 `*` 为可变钩子：订阅者可通过返回值改写数据流，或抛 `HookVeto` 一票否决。

## 可变钩子的返回语义

| 钩子 | 返回值 | 效果 |
|---|---|---|
| `before_input` | `str` | 替换用户输入 |
| | `HookVeto(reason)` | 任务以 stopped 收尾，reason 进错误信息 |
| `before_session_create` | `str` / `{"title": str}` | 改写会话标题 |
| `before_message_append` | `ChatMessage` | 替换消息 |
| | `False` / `HookVeto` | 丢弃该条消息 |
| `before_build_messages` | `str` / `{"system_prompt": str}` | 替换系统提示词 |
| `after_build_messages` | `List[ChatMessage]` | 替换整组消息 |
| `before_step` | `List[ChatMessage]` | 替换本轮消息 |
| | `HookVeto` | 跳过本轮模型调用 |
| `before_model_call` | `{"messages": [...], "params": {...}}` | 按需替换请求 |
| | `HookVeto` | 拒绝本轮调用（任务 stopped） |
| `after_model_call` | `ModelOutput` | 替换本次输出 |
| `before_tool_call` | `dict` | 替换工具参数 |
| | `False` / `HookVeto` | 阻止调用（回填 blocked_by_hook） |
| `after_tool_call` | `str` / `ToolResult` | 替换工具结果 |
| `before_result` / `after_result` | `RunResult` | 替换最终结果 |

## 自定义钩子 / 自定义层

```python
from norpagent.hooks import HookLayer

# 方式一：自定义层 + 层内声明钩子
network_layer = HookLayer("L10_network", order=100, description="网络访问层")
before_net = network_layer.hook("before_network_call", mutating=True)

agent.hooks.install_layer(network_layer)
agent.hooks.before_network_call.subscribe(monitor)      # 立即可用

# 方式二：直接定义钩子（不建层）
agent.hooks.define_hook("after_cache_hit", mutating=False, description="缓存命中")

# 方式三：零定义触发——未注册的具名事件自动成为 dynamic 层钩子
agent.hooks.hook("my_custom_event").emit(data=42)
```

自定义钩子照常支持 `subscribe / unsubscribe / emit / intercept`，
与标准钩子完全同权。

## 事件负载约定

所有钩子的负载字段见 `Hook.payload_keys`（与 `norpagent.hooks.standard`
中各钩子注释一致）。历史兼容性：事件名与早期 plugin_system 的 15 个
hook 完全一致，旧插件无需修改。

## 与 EventBus 的关系

钩子体系是 EventBus 的**结构化视图**：订阅/发布最终落在
`registry.bus` 上。因此旧代码 `reg.bus.subscribe(fn, "before_step")`
与新代码 `reg.hooks.before_step.subscribe(fn)` 完全等价，
两者可混用。

## 执行结构的方法级 API

除钩子外，`AgentRuntime` 的每个执行结构同时暴露为可覆写方法
（子类无需改内核循环）：

```python
class MyRuntime(AgentRuntime):
    def build_messages(self, system_prompt, session_id, *, step, task_id, tool_names=None):
        messages = super().build_messages(...)      # 先走 L5 钩子
        messages.append(ChatMessage(role="system", content="自定义注入"))
        return messages

    def call_model(self, provider, history, schemas, params, task_id, result, step):
        ...                                          # 完全接管 L7
```

方法清单：`prepare_input` / `create_session` / `append_message` /
`build_messages` / `call_model` / `execute_tool_call` / `finalize_result`。

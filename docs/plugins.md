# 外部插件系统（库化）：加载器 / 门面 / 进程级隔离

## 安全管线

```
发现（单文件 / manifest 包）
  → 签名校验（Ed25519，invalid 拒绝；signature_required 时仅 trusted 放行）
  → AST 审计（危险调用/导入/反射绕过，block 级拒绝）
  → 权限声明校验（manifest.permissions）
  → 导入限制下加载（safe/strict：静态预检 + meta_path 运行时拦截）
  → 适配为 Plugin 协议 → 注册进 Registry（工具入表、钩子订阅总线）
```

管线每个阶段都是钩子（`PLUGIN_PIPELINE_LAYER` 自定义层）：
`before/after_plugin_discover`、`before/after_plugin_load`、
`before/after_plugin_audit`、`before/after_plugin_register`。
订阅者抛 `HookVeto` 可一票否决单个插件的加载 / 注册。

## 两种 API

```python
# 便捷入口
from norpagent.plugins import install_plugin_dirs
loader = install_plugin_dirs(reg, ["my_plugins"], config={...})

# 库化门面：生命周期 + 状态 + 热重载
from norpagent.plugins import PluginSystem
ps = PluginSystem(reg, ["my_plugins"], config={...})
infos = ps.load()            # -> List[PluginInfo]（签名/审计/工具/钩子元数据）
ps.status()                  # 完整状态（含隔离宿主）
ps.reload("my_tool")         # 开发期热重载
ps.shutdown()                # 释放隔离子进程
```

`install_plugin_dirs(reg, dirs)` 未传 config 时自动继承
`registry.security`（norpagent.safe() 安装的安全策略）。

> 运行中热挂载：`np.remount(plugins=["./my_plugins"])` 可整体替换
> plugins 槽位——旧插件钩子先退订、sys.modules 缓存清理、隔离宿主释放，
> 再按新目录走完整安全管线重装（见 DEVELOPER_MANUAL 3.7 节）。

## 进程级插件隔离（P4）

插件模块级声明 `ISOLATION = "process"`（或 manifest `isolation` 字段、
或配置 `plugin_isolation: "process"` 全局强制）：

```python
# my_plugin.py
PLUGIN_NAME = "Isolated Hello"
ISOLATION = "process"
TOOLS = [{"type": "function", "function": {"name": "iso_hello",
    "description": "...", "parameters": {"type": "object", "properties": {}}}}]

def execute(tool_name, args, ctx):
    return "hello from subprocess"
```

隔离语义：

- 插件模块对象**只存在于宿主子进程**（`python -m norpagent.plugins.host`，
  JSON 行协议 RPC：load / call_tool / fire_hook / set_context）；
- 工具执行经 RPC 回传，钩子经 fire_hook 转发（可变钩子返回值透传
  内核 intercept），单次钩子限时 5s，超时放弃——插件永不拖死主循环；
- 崩溃自愈：子进程死亡 → 自动重启 + 重载全部插件 → 重试一次；
- 导入限制在子进程内继续生效（纵深防御）。

低层 API：`ProcessIsolationManager`（多插件宿主管理）/
`ProcessPluginHost`（单宿主 RPC 客户端），见
`norpagent.plugins.isolation`。

## 插件格式（与旧 plugin_system 完全兼容）

- 模块级常量：`PLUGIN_NAME` / `PLUGIN_PUBLISHER` / `PLUGIN_VERSION` /
  `PLUGIN_DESCRIPTION` / `ISOLATION`；
- `TOOLS`：OpenAI function schema 列表 + `execute(tool_name, args, ctx)`；
- 钩子函数：`on_task_start` / `before_step` / `before_tool_call` /
  `after_tool_call` 等（与 9 层钩子体系同名事件）；
- `APPROVAL_HINTS`：工具 → 审批提示（approval=none 免审批）。

# norpagent 外部插件开发指南

> Copyright (c) 2026 xingluosama121, MIT Licensed

本指南说明如何为 norpagent（pip 库）开发**外部插件**：插件以独立 `.py` 文件
（或 manifest 包）分发，宿主应用通过 `norpagent.plugins` 加载器接入，
自动获得签名校验 / AST 审计 / 导入限制 / 网络策略 / 人工审批全套安全防护。

插件格式与现有应用的 plugin_system 完全兼容，旧插件无需改代码即可迁移。

## 1. 最小插件

```python
# my_plugin.py
PLUGIN_NAME = "我的第一个插件"
PLUGIN_VERSION = "1.0.0"
PLUGIN_PUBLISHER = "开发者名"
PLUGIN_DESCRIPTION = "一个打招呼的插件"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "greet",
            "description": "向用户打招呼",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "称呼，默认 world"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]


def execute(tool_name, args, ctx):
    if tool_name == "greet":
        name = args.get("name") or "world"
        return f"你好，{name}！"
    return None  # 不处理的工具名返回 None
```

加载：

```python
from norpagent.plugins import install_plugin_dirs

loader = install_plugin_dirs(registry, ["./my_plugins"], config={})
for info in loader.plugins:
    print(info.name, info.enabled, info.error)
```

## 2. 模块级接口一览

| 名称 | 类型 | 说明 |
|------|------|------|
| `PLUGIN_NAME` | str | 插件显示名（必填） |
| `PLUGIN_VERSION` | str | 版本，默认 "0.0.0" |
| `PLUGIN_PUBLISHER` | str | 发布者 |
| `PLUGIN_DESCRIPTION` | str | 描述 |
| `TOOLS` | list | OpenAI function schema 列表 |
| `execute(tool_name, args, ctx)` | callable | 工具统一入口，返回 str（或 None） |
| `APPROVAL_HINTS` | dict | 工具 → 审批提示（见下） |
| `ISOLATION` | str | `"process"` = 进程级隔离（插件代码只在宿主子进程加载执行，见第 10 节） |
| `__norpagent_type__` | str | 文件即模块类型声明：`"tool"` / `"plugin"` 等（见下） |
| 生命周期钩子函数 | callable | 与旧应用 hook 名完全对齐（on_task_start / before_step / before_tool_call / after_tool_call 等，见下） |

### 文件即模块（FLOW 拖入自动识别）

模块文件头部可声明 `__norpagent_type__` 模块级变量（`.json/.yaml` 用
`type` 字段），前端据此路由到对应节点；未声明回退 `other`：

```python
__norpagent_type__ = "tool"   # 声明为工具模块（拖入 /flow 即真实注册）
```

## 3. 生命周期钩子（与现有应用 15 个 hook 对齐）

钩子函数签名约定：**业务参数在前，`ctx`（PluginContext）在最后**。
`ctx` 提供 `plugin_name / project_root / app_dir / config / current_step`。

| 钩子 | 签名 | 可变 |
|------|------|------|
| `on_agent_init` / `on_agent_shutdown` | `(ctx)` | 否 |
| `on_task_start` | `(task_text, ctx)` | 否 |
| `on_task_done` | `(summary, final_reply, ctx)` | 否 |
| `on_task_error` | `(error_msg, ctx)` | 否 |
| `on_task_stopped` | `(ctx)` | 否 |
| `on_task_timeout` | `(elapsed, ctx)` | 否 |
| `before_step` | `(step, messages, ctx)` | 是：返回 list 替换本轮消息 |
| `after_step` | `(step, content, tool_calls, ctx)` | 否 |
| `before_tool_call` | `(tool_name, args, ctx)` | 是：返回 dict 替换参数；返回 False 阻止调用 |
| `after_tool_call` | `(tool_name, args, result, ctx)` | 是：返回 str 改写工具结果 |
| `on_user_input_required` | `(question, ctx)` | 否 |
| `on_reasoning` / `on_content` | `(token, ctx)` | 否 |
| `on_event` | `(event_type, data, ctx)` | 否 |
| `on_usage_update` | `(usage_dict, ctx)` | 否 |

示例（审计工具调用参数）：

```python
def before_tool_call(tool_name, args, ctx):
    if tool_name == "file_delete":
        # 阻止删除操作
        return False
    return None  # 不干预其他工具
```

## 4. APPROVAL_HINTS（人工审批精细控制）

```python
APPROVAL_HINTS = {
    "read_config": {"approval": "none", "risk": "L0"},   # 只读：免审批
    "deploy_all": {"approval": "plugin", "risk": "L3"},  # 高风险：走审批开关
}
```

- `approval="none"`：该工具调用不弹审批（插件自认低风险）；
- 未声明的工具默认走 `approval_enabled` 总开关（向后兼容）。

## 5. manifest 包格式

```
my_plugins/
  fancy/
    manifest.json      # 元数据 + 可选签名
    plugin.py          # 入口（默认名 plugin.py，可自定义）
    helper.py          # 其他文件
```

```json
{
  "name": "fancy",
  "version": "1.2.0",
  "publisher": "作者",
  "description": "说明",
  "entry": "plugin.py",
  "permissions": ["file_read"],
  "signature": {
    "algorithm": "ed25519",
    "public_key": "<公钥hex>",
    "signature": "<签名hex>",
    "signed_files": ["plugin.py"]
  }
}
```

## 6. 安全管线（宿主配置）

```python
config = {
    "plugin_security_audit": "warn",            # off / warn / block
    "plugin_security_import_restrict": "off",   # off / safe / strict
    "plugin_security_require_permissions": False,
    "plugin_signature_verify": True,
    "plugin_trusted_keys": ["<用户信任公钥hex>"],
    "plugin_network_policy": "deny",            # deny/audited_public/public_only/allow_all
    "plugin_network_url_allowlist": ["https://api.example.com/"],
    "plugin_network_domain_allowlist": ["api.example.com"],
    "approval_enabled": True,
}
```

加载管线：

1. **签名校验**（Ed25519，`norpagent[security]`）：`invalid` 直接拒绝；
   未安装 cryptography 时按「不受信任」处理（安全姿态不降级）；
2. **AST 审计**：危险调用 / 危险导入 / `getattr`、`__dict__` 反射绕过检测，
   `block` 级别发现 critical 即拒绝；受信任签名自动放宽为 warn；
3. **权限声明**：开启 `require_permissions` 时校验 manifest.permissions
   覆盖审计发现（process / network / file_write / file_read）；
4. **导入限制**：`safe` 阻断危险模块（subprocess / ctypes / socket 等，
   静态预检 + 运行时 meta_path 双重拦截），`strict` 仅允许安全模块白名单；
5. **注册**：工具进入工具注册表，钩子订阅事件总线。

## 7. 插件签名

```bash
pip install norpagent[security]

python -m norpagent plugin-sign --gen              # 生成密钥对（公私钥各一）
python -m norpagent plugin-sign my_plugin.py --key <私钥hex>   # 生成 my_plugin.py.sig
```

把公钥加入宿主配置 `plugin_trusted_keys` 即成为受信任插件
（审计放宽为 warn、导入限制 off）。

## 8. 网络访问

插件代码里发起 HTTP 请求前，先了解宿主网络策略。策略由宿主配置
`plugin_network_policy` 决定，默认 `deny`（插件禁止访问网络）。
插件自身无法绕过：策略执行于宿主进程，独立于插件代码。

## 9. 进程级插件隔离

插件模块级声明 `ISOLATION = "process"`（或 manifest `isolation` 字段、
或宿主配置 `plugin_isolation: "process"` 全局强制）：

```python
PLUGIN_NAME = "Isolated Hello"
ISOLATION = "process"
```

隔离语义：

- 插件模块对象**只存在于宿主子进程**（`python -m norpagent.plugins.host`，
  JSON 行协议 RPC：load / call_tool / fire_hook / set_context）；
- 工具执行经 RPC 回传，钩子经 fire_hook 转发（单次钩子限时 5s，
  超时放弃——插件永不拖死主循环）；
- 崩溃自愈：子进程死亡 → 自动重启 + 重载全部插件 → 重试一次；
- 导入限制在子进程内继续生效（纵深防御）。

## 10. 调试技巧

- 加载结果：`loader.plugins[i].error` 给出拒绝原因（含审计行号）；
- 审计明细：`loader.plugins[i].audit_issues`（severity / line / category）；
- 热重载：修改插件后重建 Registry 并重新 `install_plugin_dirs` 即可；
  运行中的引擎也可以 `np.remount(plugins=["./my_plugins"])` 整体替换
  plugins 槽位（旧订阅自动退订，见开发手册 3.7 节运行中热挂载）；
- 单测插件逻辑：`PluginLoader([dir], config={"plugin_security_audit": "off",
  "plugin_signature_verify": False})` 临时关闭防护（仅限本地调试）。

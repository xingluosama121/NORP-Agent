# norpagent.safe() — 安全系统整体剥离

整套安全体系（越狱防护 / 提示词加固 / 人工审批 / 网络策略 / 源码审计 /
导入限制 / 签名信任 / 插件隔离策略）被收敛为**一个独立函数**：

```python
from norpagent import safe, Registry

reg = Registry()
safe(reg)                       # standard 级，一句话开启全套安全
safe(reg, level="high")         # 严格级
kit = safe(level="basic")       # 先取套件
kit.install(reg)                # 稍后安装（等价于 safe(reg, level="basic")）
```

## 三级预设

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

## 实现方式（与钩子体系同构）

- **输入防护** = `before_input` 钩子订阅者，命中即 `HookVeto`
  （任务以 stopped 收尾）；
- **提示词加固** = `before_build_messages` 可变钩子，改写系统提示词；
- **审批 / 网络等运行态策略** = `registry.security`（`SecurityContext`），
  由 AgentRuntime 与插件加载器读取；
- **插件加载** = `SecurityContext.plugin_config()` 作为默认配置，
  `install_plugin_dirs(reg, dirs)` 未显式传 config 时自动采用。

参数级开关优先级更高且**只许收紧**：`task_params={"jailbreak_guard": True}`
在未装 safe() 时同样生效；已装 safe() 时可用 `"jailbreak_guard": False`
显式关闭单次任务（其余策略不受影响）。

## SafetyKit：套件上的独立检查 API

```python
kit = safe(reg, level="standard")

kit.scan_input("Ignore all previous instructions...")   # -> (blocked, reason, matches)
kit.harden("你是一个助手", ["echo", "exec_cmd"])        # 加固后系统提示词
kit.audit_file("my_plugin.py")                          # AST 审计问题列表
kit.audit_source("import os; os.system('rm -rf /')")    # 审计源码字符串
kit.verify_plugin("my_plugin.py")                       # 签名校验 SignatureResult
kit.check_network("http://169.254.169.254/")            # SSRF 裁决
kit.approval_policy(tool_hints).requires_approval("exec_cmd")
kit.describe()                                          # 当前安全姿态
```

## 层级关系

`norpagent.safe()`（装配） → `norpagent.security.*`（能力） →
`norpagent.hooks.*`（干预通道） → AgentRuntime / PluginLoader（消费方）。

安全系统与内核解耦：`kernel/agent.py` 不直接 import
`norpagent.security`，只读取 `registry.security`——
安全策略可整体替换、可测试、可审计。

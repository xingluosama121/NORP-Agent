# NORP 插件目录

本目录是 NORP Agent 的插件加载目录（`config.json` → `plugin_dirs`）。

## 本轮新增插件（对齐 DeepSeek Harness dsh-web-ui 全家桶）

| 插件目录 | 对齐对象 | 能力 |
|---|---|---|
| `norp_ssh/` | `dsh-ssh` | 远程 SSH 运维：主机管理（含 `~/.ssh/config` 导入）、连通性测试、远程执行、上传/下载、端口转发隧道、集群并发执行；密钥/密码认证 + passphrase + ProxyJump 跳板链 + ControlMaster 持久连接池 |
| `norp_task_board/` | `dsh-task-board` | 多列看板任务管理 + 5 段 cron 定时；到点自动把提醒注入会话 |
| `norp_git/` | `dsh-git-graph` + `dsh-aionui-panel`(SCM) | Git 版本控制：状态/日志/提交图/差异/分支/暂存/提交/检出/推拉/概览 |

> `norp_pet_bridge/`（桌面宠物，对齐 `dsh-pet`）此前已有，本轮未改动。

## 目录结构

```
plugins/
├── norp_ssh/            # 新增 —— SSH 运维
│   ├── manifest.json
│   ├── plugin.py
│   └── README.md
├── norp_task_board/     # 新增 —— 任务看板 + cron
│   ├── manifest.json
│   ├── plugin.py
│   └── README.md
├── norp_git/            # 新增 —— Git/SCM
│   ├── manifest.json
│   ├── plugin.py
│   └── README.md
├── norp_pet_bridge/     # 已有 —— 桌面宠物（未改动）
├── official_plugins/    # 官方示例插件
├── task_logger.py       # 示例插件
├── session_stats.py     # 示例插件
└── example_notifier.py  # 示例插件
```

## 安装 / 使用

1. 确保 `config.json` 的 `plugin_dirs` 包含本目录（当前已是 `"E:\\norp agent\\plugins"`）。
2. 重启 NORP Agent，在设置 → 插件列表确认新插件已加载。
3. 在对话里直接说需求即可触发对应工具，例如：
   - SSH：`"连上 prod 服务器看下磁盘占用"` / `"开个隧道访问远程数据库"`
   - 看板：`"帮我把『部署上线』加到看板 doing 列，每晚 23 点提醒"`
   - Git：`"看下当前改动和提交历史"` / `"提交已暂存的改动"`

## 签名与分发（新版 NORP 必读）

新版 NORP 对插件默认开启**签名校验 + 进程隔离 + block/strict 审计**。本仓库的 4 个插件已全部
**Ed25519 签名**（`manifest.json` 里的 `signature` 字段）并**补齐权限声明**。

**别人要能加载你的插件，必须把下面的公钥加进他们的 `plugin_trusted_keys`：**

```json
"plugin_trusted_keys": [
  "eed0f67efa7eceb9d95fd7aa8c279c4939e1fdbf81ea010338ef7e2028b20286"
]
```

> 加了公钥后插件被判定为 `trusted`，自动获得「审计 warn / 导入限制 off」的宽松安全，
> `subprocess` 等调用才能通过。**不加公钥，签名虽有效但公钥不受信任，仍会被 block 审计拦下。**

- 改插件代码（`plugin.py`）后**必须重新签名**：运行 `sign_plugins.py`。
- 私钥在 `插件签名密钥/private_key.txt`，**勿外泄、勿上传 GitHub**（已加 .gitignore）。

## 安全说明

- `norp_ssh` / `norp_git` 使用 `subprocess`，在默认安全配置
  （`plugin_security_audit: "warn"`）下正常加载；与官方插件 `stress_tester.py`、
  `clipboard_manager.py` 一致。
- `norp_ssh` 的密码以明文存在 `%LOCALAPPDATA%\vibe_agent\norp_ssh\hosts.json`，
  请勿公开该目录。
- `norp_task_board` 任务数据存 `%LOCALAPPDATA%\vibe_agent\norp_task_board\tasks.json`。

## ⚠️ 发现的一个源码级问题（建议转告原作者）

NORP 的 **mutating 钩子**（`before_step` / `before_tool_call` / `after_tool_call`）
在 `plugin_system/manager.py` 的 `_broadcast_mutating()` 里采用「**首个非 None 返回值
即生效并停止遍历**」的语义。这意味着：只要排在前面的插件（如 `norp_pet_bridge` 的
`before_step` 永远 `return messages`）返回非 None，后面所有插件的同名 mutating 钩子
**根本不会被调用**——`task_logger.py` 的步骤计数、`session_stats.py` 的工具耗时统计、
以及本仓库 `norp_task_board` 的 cron 注入都会因此被静默跳过。

建议请求原作者把 `_broadcast_mutating` 改为：**遍历所有监听器（让每个插件的副作用都
执行），只把「首个非 None 返回值」用作最终返回值**；或把「原样返回传入值」的 pass-through
返回视为 None（不抢占）。这属于源码改动，本仓库未动源码。


# NORP SSH

对齐 DeepSeek Harness `dsh-ssh` 插件的 NORP 版远程运维插件（零第三方依赖，只调用系统
自带 OpenSSH / PuTTY）。

## 能力

| 工具 | 说明 |
|---|---|
| `ssh_list` | 列出已配置主机（支持关键字过滤；显示 keyReady / environment / location / 标签） |
| `ssh_add_host` | 新增 / 更新主机配置（别名语法 + 端口范围校验） |
| `ssh_remove_host` | 删除主机 |
| `ssh_import_config` | 从 `~/.ssh/config` 导入主机（通配符 Host 自动跳过，报告 skipped） |
| `ssh_test` | 连通性测试 + 延迟测量 |
| `ssh_exec` | 远程执行命令（stdout/stderr 分离，超时可控） |
| `ssh_upload` / `ssh_download` | 单文件上传 / 下载 |
| `ssh_tunnel` | 本地端口转发（start / list / stop / stop-all） |
| `ssh_cluster` | 多主机并发执行（按别名 / 环境 / 标签过滤，可设 `maxWorkers`） |

## 认证方式

- **密钥认证（默认）**：走系统 OpenSSH `ssh.exe`，支持 `identity_file`、`ProxyJump`
  跳板链（`jump` 用逗号分隔可多级）、`ssh-agent`。
- **密码认证**：需要 PuTTY 的 `plink.exe` / `pscp.exe`（`-batch -pw`），未安装时给出
  明确提示。
- **passphrase 密钥**：有 `plink` 时走 `plink -i key -pw passphrase` 非交互提供口令；
  纯 OpenSSH 环境请先 `ssh-add` 把密钥加入 agent。

## 持久连接池

- 基于 OpenSSH `ControlMaster=auto` + `ControlPersist=1800`：同一台主机的 `ssh_exec` /
  `ssh_upload` / `ssh_download` 复用长连接，空闲 30 分钟自动断开（对应 dsh-ssh 的连接池）。
- **best-effort**：若主机/平台不支持 mux（报 ControlPath / ControlMaster 相关错误），
  自动回退到普通连接，不影响可用性。可用模块常量 `USE_CONTROL_MASTER = False` 全局关闭。
- 隧道（`ssh -N -L`）本身就是一条长连接，不叠加 ControlMaster，避免主连接关闭时连带
  断开其它隧道；`on_agent_shutdown` 会统一清理隧道与连接池 socket。

## 数据存储

主机配置保存在 `<app_dir>/norp_ssh/hosts.json`（`%LOCALAPPDATA%\vibe_agent\norp_ssh\hosts.json`）：

- 版本化 JSON（`{"version": 1, "hosts": [...]}`）+ 原子写入（临时文件 + `os.replace`）。
- POSIX 下文件权限 0600；损坏文件自动改名隔离为 `hosts.json.corrupt-<ts>`，不静默覆盖。
- 每条主机含 `created_at` / `updated_at` 时间戳；`tags` 为数组、`jump` 为逗号分隔跳板链。

> ⚠️ 密码 / 密钥口令以明文存储在该文件中，请勿公开泄露该目录。命令输出原样返回，可能
> 包含敏感信息。

## 安全审计说明

本插件使用 `subprocess`（安全审计 CRITICAL 级）与网络/文件操作。在默认安全配置
（`plugin_security_audit: "warn"`、`plugin_security_import_restrict: "off"`）下可正常加载。
若开启 `plugin_security_audit: "block"`，需保持 `"warn"` 或按 manifest 声明的
`permissions` 放行。

## 使用示例

```
"连上 prod 服务器看下磁盘占用"
→ ssh_exec(alias="prod", command="df -h")

"测一下到 prod 的连通性"
→ ssh_test(alias="prod")

"把本机 a.txt 传到服务器的 /tmp/"
→ ssh_upload(alias="prod", local_path="...", remote_path="/tmp/a.txt")

"开个隧道访问远程的 3306 数据库"
→ ssh_tunnel(action="start", alias="prod", remote_port=3306)

"在 web1、web2 上批量重启 nginx"
→ ssh_cluster(command="systemctl restart nginx", aliases="web1,web2", maxWorkers=8)
```

## v1.1.0 变更

- 新增 `ssh_test`（连通性/延迟测试）。
- 新增 `passphrase` / `location` 字段；`jump` 支持逗号分隔多级跳板链。
- `ssh_cluster` 新增 `maxWorkers` 并发上限（默认 8，范围 1..32）。
- `ssh_list` 显示 keyReady（密钥文件是否存在）、environment、location。
- 存储层加固：版本化 + 原子写入 + POSIX 0600 + 损坏隔离 + 时间戳；别名/端口校验。
- `ssh_import_config` 跳过通配符 Host 并报告 skipped。
- 修复 `ssh_tunnel stop` 返回元组而非字符串的问题。
- 新增 OpenSSH ControlMaster 持久连接池（best-effort + 失败回退）。

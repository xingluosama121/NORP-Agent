# NORP Git

对齐 DeepSeek Harness `dsh-git-graph` + `dsh-aionui-panel`（SCM 面板）能力的 NORP 版
Git 版本控制插件。

## 能力

| 工具 | 说明 |
|---|---|
| `git_status` | 分支 + 与远程差异 + 变更文件列表 |
| `git_log` | 提交历史（支持 `--graph` 提交图、`--all` 全部分支） |
| `git_diff` | 工作区 / 已暂存差异 |
| `git_branch` | 分支 list / create / switch / delete |
| `git_stage` / `git_unstage` | 暂存 / 取消暂存 |
| `git_commit_staged` | 提交已暂存内容（与内置 `git_commit` 全量提交区分） |
| `git_checkout` | 切换分支 / 提交，或新建并切换 |
| `git_pull` / `git_push` | 拉取 / 推送 |
| `git_summary` | 一屏概览（SCM 面板首页式） |

## 设计要点

- 所有命令都在当前工作区 `context.project_root` 执行。
- 命令参数以**列表形式**传给 git（不经过 shell 字符串拼接），规避 shell 注入。
- 依赖系统 Git（`git.exe`，需在 PATH 中）。

## 与 dsh 的差异

dsh-git-graph / aionui-panel 是 Web GUI 上的可视化面板；NORP 插件系统没有独立
UI 槽位，故本插件以**结构化文本**呈现（提交图用 ASCII `--graph`）。可视化面板
需要改 NORP 前端源码（`front.html`），不在本插件范围内。

## 安全审计说明

本插件使用 `subprocess`（CRITICAL 级）。默认安全配置
（`plugin_security_audit: "warn"`）下可正常加载，与官方插件
（`stress_tester.py`、`clipboard_manager.py`）一致。

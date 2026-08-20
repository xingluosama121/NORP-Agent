# NORP Task Board

对齐 DeepSeek Harness `dsh-task-board` 插件的 NORP 版任务看板。

## 能力

| 工具 | 说明 |
|---|---|
| `board_list` | 列出任务（按列 / 关键字过滤） |
| `board_add` | 新增任务（标题/详情/优先级/标签/cron） |
| `board_move` | 任务列间流转（backlog → todo → doing → done） |
| `board_update` | 更新任务字段 |
| `board_delete` | 删除任务 |
| `board_next_run` | 校验 cron 并计算下次触发时间 |
| `board_columns` | 看板各列概览 |

## cron 定时

支持 5 段 cron（`分 时 日 月 周`，周 `0`=周日），例如：

- `0 23 * * *` —— 每天 23:00
- `*/30 * * * *` —— 每 30 分钟
- `0 9 * * 1` —— 每周一 09:00

到点后，插件通过两条通道提醒：

1. **`before_step` 钩子注入**（尽力而为）：把一条 system 提醒注入当前会话，让
   Agent（和用户）知道该定时任务已到点。
2. **`board_list` 实时展示**（可靠）：查询看板时，已到点的任务会带 `🔔已到点` 标记。

## 限制（与 dsh-task-board 的差异）

1. **无独立 UI**：任务数据与看板以工具 + 文本形式呈现，不像 dsh-task-board 有
   Web 看板界面（那需要改 NORP 前端源码）。
2. **到点不自动新开会话**：dsh-task-board 的定时能「驱动新 agent 会话」，NORP 插件
   系统没有「从插件启动新任务」的接口，故本插件只在**当前正在运行的会话**里提醒。
3. 定时检查发生在每个 ReAct 步骤；没有任务在跑时不会主动唤醒。
4. **`before_step` 注入是尽力而为**：NORP 的 mutating 钩子采用「首个非 None 返回值
   生效」语义，若其他插件（如 `norp_pet_bridge`）的 `before_step` 先返回非 None，
   本插件的注入会被跳过。此时仍可通过 `board_list` 看到 `🔔已到点` 的任务。

## 数据存储

任务保存在 `<app_dir>/norp_task_board/tasks.json`
（`%LOCALAPPDATA%\vibe_agent\norp_task_board\tasks.json`），跨会话保留。

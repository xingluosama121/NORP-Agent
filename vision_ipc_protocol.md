# 视觉/操作外挂 —— IPC 传输层与会话层协议定义

> 版本：v1.0（定稿）
> 关联：docs/vision_agent_design.md 第 3/4/12 节
> 状态：消息格式沿用「XML 信封 + JSON 负载」（vision_ipc.py）；本文档定稿**传输层**与**会话层**，并扩展消息类型。
> 自研约束：仅使用 Python 标准库（socket / threading / hmac / hashlib / secrets / xml.etree / json / ctypes），不引入任何第三方 IPC / 序列化库。

---

## 0. 一句话定位

主架构（插件桥）通过 **loopback TCP socket** 向**独立外挂进程（vision_daemon.py）**发送带一次性 HMAC 令牌的 `<vision-op>` 指令；外挂进程独立裁决、执行、验证，返回 `<vision-result>`，并主动推送 `<vision-event>` 状态事件。**裁决权唯一归属外挂进程**，主架构只能「申请」，不能「裁决自己」。

---

## 1. 拓扑与发现

```
┌──────────────────────────────────────────────────────┐
│ 主架构（plugin_host 子进程内的插件桥）                  │
│  - VisionDaemonClient（vision_ipc_transport.py）      │
│  - 读 lock 文件拿 port+secret → 连接 → 握手 → 发 op   │
│  - daemon 未运行则自动拉起（subprocess）               │
└───────────────────────┬──────────────────────────────┘
                        │ TCP 127.0.0.1:<port>
                        │ 帧: [VIPC][len][XML信封+JSON负载]
┌───────────────────────▼──────────────────────────────┐
│ 视觉/操作外挂进程（vision_daemon.py，可被杀可重启）      │
│  - SafetyArbiter 裁决器（唯一裁决权）                   │
│  - VisionCoordinator（动作-验证-收敛）                 │
│  - Ctrl+End 热键线程（RegisterHotKey 仅消息窗口）       │
│  - 在场检测线程（GetLastInputInfo，每秒）              │
│  - 状态/审计落盘（JSON 原子写 + JSONL 追加）            │
│  - 横幅控制器接口（预留，下一里程碑实现）               │
└──────────────────────────────────────────────────────┘
```

### 1.1 发现机制（lock 文件）

- 路径：`<app_dir>/vision_daemon.lock`（app_dir 由主架构启动 daemon 时传入，默认主架构 app_dir）。
- 内容（JSON，UTF-8）：

```json
{"version": 1, "pid": 12345, "port": 38476,
 "secret": "<32位hex，HMAC共享密钥>", "started_at": 1750000000.0}
```

- `secret` 由 daemon 启动时用 `secrets.token_hex(16)` 生成，**只写本文件、不外传**；主架构凭文件权限（用户目录）读取。文件在 daemon 优雅退出时删除；崩溃残留由客户端按「pid 不存活 / 端口连不通」判定陈旧并覆盖。
- 端口：默认 **38476**，被占用则顺序递增（+1…+16）尝试；实际端口写入 lock 文件。

### 1.2 启动参数

```
python vision_daemon.py --start [--port N] [--app-dir DIR] [--allow-admin] [--fg]
python vision_daemon.py --stop  [--app-dir DIR]   # 客户端方式发 shutdown
python vision_daemon.py --status [--app-dir DIR]  # 客户端方式发 state 查询
```

- **权限自检**：daemon 启动时检查 `IsUserAnAdmin`，为管理员则**默认拒绝启动**（宪法约束「不提权」）。用户显式知情后可 `--allow-admin` 强制放行（对应配置 `vision_daemon_allow_admin=true`），该放行会写入审计与启动日志。

---

## 2. 传输层：帧格式

### 2.1 帧结构（定长头 + 变长体）

```
偏移  0   1   2   3   4   5   6   7
    +---+---+---+---+---+---+---+---+
    |   magic = 'V' 'I' 'P' 'C'      |
    +---+---+---+---+---+---+---+---+
    |   payload_len（uint32，小端）   |
    +---+---+---+---+---+---+---+---+
    |   payload = UTF-8 编码的 XML 消息
    +-------------------------------+
```

- 头部 8 字节：`b"VIPC"` + `struct.pack("<I", payload_len)`。
- `payload_len` 范围：1 ~ **16 MiB**（`MAX_FRAME`）。越界、magic 错误、UTF-8 非法 → 立即断连（防内存攻击）。
- TCP 粘包/半包由接收侧缓冲处理：连续 `feed(data)` 逐帧切分。
- 一个 TCP 连接 = 一个会话；帧内 XML 消息类型见第 4 节。

### 2.2 超时约定

| 场景 | 超时 | 处理 |
|---|---|---|
| 握手整体 | 10 s | 超时断连 |
| 指令执行（look 等视觉请求） | 120 s（客户端可设） | 超时客户端断连视为失败 |
| 会话空闲（无任何帧） | 600 s | daemon 主动断开 |
| 客户端请求等待 | 120 s 默认 | 调用方异常返回 |

---

## 3. 会话层：握手与令牌

### 3.1 握手（HMAC 挑战应答，四次消息）

```
C → S   <vision-hello version="1.0" role="plugin-host" nonce_c="<16B hex>" ts="..."/>
S → C   <vision-challenge version="1.0" nonce_s="<16B hex>" ts="..."/>
C → S   <vision-auth version="1.0" proof="<64 hex>"/>
S → C   <vision-hello-ack version="1.0" auth_ok="true" reason="ok" session_id="<8B hex>" ts="..."/>
```

- `proof = HMAC-SHA256(secret, "vipc-auth|" + nonce_c + "|" + nonce_s + "|" + challenge_ts)`，hex 小写。
- 每个连接**只有一次** auth 机会：失败 → `auth_ok="false"` + 立即断连（防爆破）。
- 版本不匹配（非 "1.0"）→ `auth_ok="false"`，reason 说明。
- 首帧必须为 `vision-hello`，否则断连。
- 握手成功后 S 建立会话：分配 `session_id`，连接进入「指令模式」，可收发 op/result/event/ping/pong。

### 3.2 一次性授权令牌（每条 op 必带）

信封 `token` 属性 = **主架构用共享 secret 自签的 HMAC**：

```
material = "vipc-op|" + op + "|" + risk + "|" + str(ts) + "|" + req_id
         + "|" + sha256(payload_json).hexdigest()[:16]
token    = HMAC-SHA256(secret, material)  的 hex 小写
payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

daemon 侧校验（全部通过才受理）：

1. token 为 64 hex 且 HMAC 匹配（**payload 被篡改 → 摘要变 → 签名失效**）；
2. `|ts - now| <= 10 s`（本机同源时钟；容忍调度抖动）；
3. token 未使用过（**防重放**：内存记录最近 300 s 内已用 token，滚动清理）；
4. `ttl_ms` 未过期（指令有效期，默认 3000 ms，look 类长请求可放宽）。

签名在本地计算（微秒级），每次工具调用现签现用，**用后即焚**——满足设计文档「一次性授权令牌」。

### 3.3 心跳（可选）

```
C → S   <vision-ping version="1.0" ts="..."/>
S → C   <vision-pong version="1.0" ts="..." t="..."/>
```

心跳不改变任何状态；仅用于保持长连接与探测 daemon 存活。客户端长连接空闲 > 300 s 时建议发一次 ping。

---

## 4. 消息层：根元素总表（vision_ipc.py 实现）

| 根元素 | 方向 | 阶段 | 关键属性 | 说明 |
|---|---|---|---|---|
| `vision-hello` | C→S | 握手 | version, role, nonce_c, ts | 连接第一帧 |
| `vision-challenge` | S→C | 握手 | version, nonce_s, ts | 挑战 |
| `vision-auth` | C→S | 握手 | version, proof | 应答 |
| `vision-hello-ack` | S→C | 握手 | version, auth_ok, reason, session_id | 握手结论 |
| `vision-op` | C→S | 指令 | version, op, risk, token, ttl_ms, ts, id, confirm? | 操作请求（见 4.1） |
| `vision-result` | S→C | 指令 | version, op, status, risk, ts, id | 操作回执（id 与 op 一一对应） |
| `vision-event` | S→C | 推送 | version, event, ts | 状态事件（见 4.3） |
| `vision-ping` / `vision-pong` | C→S / S→C | 心跳 | version, ts, t? | 保活 |

### 4.1 `<vision-op>` 指令表

- `id`：请求 ID（8 hex 随机），result 原样回带，支持并发请求关联。
- `confirm="user"`：表示主架构的**用户审批链路已批准**（审批弹窗 / 设置面板用户操作）。daemon **不信任**该属性本身，只把它作为「用户已确认」的输入参与裁决（熔断 / override / 在场 / delegate 裁决独立执行）。
- 所有 op 的信封 `risk` 由主架构按固定映射填写（与插件工具名绑定，LLM 不可自报）；daemon 受理时**再次核对** op→risk 白名单，不符直接拒绝。

| op | 固定 risk | payload（JSON） | result.payload 关键字段 | 说明 |
|---|---|---|---|---|
| `list_windows` | L0 | `{max_results}` | `{windows:[{hwnd,title}]}` | 枚举可见顶层窗口 |
| `look` | L0 | `{hwnd, prompt?, max_chars?}` | `{hwnd, description, truncated}` | 捕获 + 视觉理解（provider 配置由 daemon 本地 config 提供） |
| `state` | L0 | `{}` | `{arbiter_state:{...}, audit_tail:[...]}` | 查询安全状态与最近审计 |
| `move` | L1 | `{hwnd?, x, y}` | `{verdict, reason, attempts, verification_detail}` | 移动鼠标（坐标闭环 + 光标验证） |
| `scroll` | L1 | `{hwnd?, x, y, clicks}` | 同上 | 滚轮 |
| `click` | L2 | `{hwnd, x, y, button?, double?, expect?}` | 同上 | 点击（动作-验证-收敛） |
| `type` | L2 | `{text, hwnd?}` | 同上 | 文本输入 |
| `key` | L2 | `{key, mods?, hwnd?}` | 同上 | 按键 / 组合键 |
| `delegate` | L0 | `{action: grant\|revoke\|query, scope?, max_risk?, window_sec?, hwnd?}` | `{delegate:{...}}` | 让渡确认权；grant 必须 confirm="user" |
| `veto` | L0 | `{op, hwnd?}` | `{consecutive_vetoes, circuit}` | 上报「用户否决了某操作」（审批弹窗点拒绝时调用） |
| `manual_reset` | L0 | `{}` | `{allowed, reason, circuit}` | 用户手动复位熔断；必须 confirm="user" |
| `reload_config` | L0 | `{}` | `{reloaded, arbiter:{...}}` | 重读 daemon 本地配置（安全参数热更新，状态迁移保留） |
| `ping` | L0 | `{}` | `{t}` | 心跳（同 3.3，也可用专用帧） |
| `shutdown` | L0 | `{}` | `{shutting_down}` | 优雅退出；必须 confirm="user" |

动作类 op（move/scroll/click/type/key）**串行执行**：同一时刻最多一个动作在执行，新动作在队列满时被直接拒绝（`reason="上一条操作尚未完成"`）。

### 4.2 `<vision-result>` status 枚举

| status | 含义 |
|---|---|
| `approved` | 已放行并执行，验证通过 |
| `requires_confirmation` | 需要用户确认（如 L2 无 confirm） |
| `rejected` | 裁决器拒绝（熔断 / override / 空闲锁死 / 失败耗尽 / 参数非法） |
| `vetoed` | 用户否决（确认环节被拒） |
| `circuit_open` | 熔断中拒绝 |
| `timeout` | 指令超时（执行侧看护） |
| `error` | 执行 / 捕获 / 视觉调用异常 |
| `ok` | 无动作类语义的管理类 op 成功（state / delegate 等） |

### 4.3 `<vision-event>` 事件表（S 主动推送，无需应答）

| event | payload 关键字段 | 触发 |
|---|---|---|
| `circuit_opened` | `{reason: "hotkey"\|"vetoes", circuit}` | 热键 / 连续 3 次否决 → OPEN |
| `circuit_half_open` | `{circuit}` | 用户复位（冷却期后 OPEN→HALF_OPEN） |
| `circuit_closed` | `{circuit}` | HALF-OPEN 试探成功 → CLOSED |
| `override_engaged` | `{at}` | 用户在 Agent 操作中介入（键鼠输入） |
| `override_resumed` | `{}` | 用户指示恢复 |
| `idle_locked` | `{idle_sec}` | 空闲超时锁死（仅保留 L0） |
| `idle_unlocked` | `{}` | 用户回归，锁死解除 |
| `banner_changed` | `{color: "green"\|"red"\|"hidden", visible, paused}` | 横幅状态变化（与横幅 UI 联动） |
| `daemon_shutdown` | `{reason}` | daemon 即将退出（优雅关闭 / 收到 shutdown） |

主架构应订阅事件用于 UI 呈现与审计联动；事件丢失不重发（可随时用 `state` op 拉全量状态）。

---

## 5. 安全模型（自研，零外部依赖）

1. **绑定回环**：只 `bind("127.0.0.1")`，不监听外网接口。
2. **共享密钥**：`secrets.token_hex(16)`，仅落 lock 文件（app_dir 用户目录权限）。HMAC-SHA256 挑战应答 + 逐条签名（stdlib `hmac`/`hashlib`）。
3. **防重放**：token 用后即焚 + ts 新鲜度窗口 + payload 摘要绑定（改一个字签名即失效）。
4. **帧上限**：16 MiB 硬顶，超限断连，防内存耗尽。
5. **单次认证**：每个连接一次 auth 机会，失败断连。
6. **裁决权隔离**：op→risk 白名单在 daemon 内二次核对；熔断 / override / 在场 / delegate 全部在 daemon 进程内独立裁决；Agent（及主架构）只能申请。
7. **审计铁律**：每次裁决（含被拒）追加 `<app_dir>/vision_audit.jsonl`；熔断 / 失败计数 / delegate 状态原子写 `<app_dir>/vision_state.json`，daemon 重启不丢。
8. **物理熔断**：`Ctrl+End` 热键在 daemon 进程内用仅消息窗口（HWND_MESSAGE）的 `RegisterHotKey` 实现，不经 IPC、不经 Agent；注册失败（热键被占用）只降级告警，不阻塞其余安全机制。

---

## 6. 主架构对接约定（插件桥）

- 每个工具调用 = 一次 `VipcClient.request(op, risk, payload, confirm=...)`，同步等 result，req_id 自增。
- daemon 未运行：写 `<app_dir>/vision_daemon_config.json`（安全参数 + 视觉 provider 配置）→ `subprocess` 拉起 `vision_daemon.py --start` → 轮询 lock 文件（≤10 s）→ 连接握手。
- daemon 拒绝管理员运行且主架构配置 `vision_daemon_allow_admin=true` 时，拉起参数追加 `--allow-admin`（风险由用户知情自担）。
- 连接 / 拉起失败：工具返回明确错误文本（含 daemon stderr 摘要），**绝不静默降级到进程内执行**——裁决权必须唯一。
- `veto` op：主进程审批弹窗用户点「拒绝」时上报，驱动「连续 3 次否决 → 熔断」。

---

## 7. 版本演进

- 协议 `version="1.0"` 字段贯穿所有消息；daemon 拒绝非 1.0 握手。
- 帧头 magic/长度不可变；XML 信封可加新属性（旧端忽略未知属性），根元素可新增（旧端按未知根元素断连规则处理：**协议不识别即断连，不猜**）。
- op / event 新增时同步更新本表与 daemon 白名单，不回改历史消息语义。

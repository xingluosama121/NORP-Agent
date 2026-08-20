# NORP Agent 视觉 API 开发手册

> 版本：v1.0（对应 vision.py / vision_adapters.py / vision_capture.py / vision_actions.py / vision_safety.py / vision_coordinator.py / vision_ipc.py 现状）
> 适用对象：想让 Agent「看得懂图片/视频」的开发者、想接入任意多模态模型的开发者、想构建「视觉 + 键鼠操作」能力的重度开发者

---

## 目录

- [第 0 章 什么是视觉 API](#第-0-章-什么是视觉-api)
- [第 1 章 快速开始：30 秒让 Agent 看见图片](#第-1-章-快速开始30-秒让-agent-看见图片)
- [第 2 章 三种接入方式与优先级](#第-2-章-三种接入方式与优先级)
- [第 3 章 核心 API 参考（vision.py）](#第-3-章-核心-api-参考visionpy)
- [第 4 章 外部视觉服务协议](#第-4-章-外部视觉服务协议)
- [第 5 章 内置 Provider 适配层（vision_adapters.py）](#第-5-章-内置-provider-适配层vision_adapterspy)
- [第 6 章 配置项全参考](#第-6-章-配置项全参考)
- [第 7 章 Agent 内的视觉链路](#第-7-章-agent-内的视觉链路)
- [第 8 章 窗口捕获与键鼠操作（外挂模块）](#第-8-章-窗口捕获与键鼠操作外挂模块)
- [第 9 章 完整示例代码](#第-9-章-完整示例代码)
- [第 10 章 测试与调试](#第-10-章-测试与调试)
- [第 11 章 常见问题 FAQ](#第-11-章-常见问题-faq)
- [附录 A API 速查表](#附录-a-api-速查表)

---

## 第 0 章 什么是视觉 API

视觉 API 是 NORP Agent 的**开放视觉接口层**：把「图片 / 视频 / 屏幕窗口」交给多模态视觉模型（LLM）理解，并把结果以**文字**形式返回给 Agent。

一句话：**让 Agent 拥有眼睛。**

视觉 API 由两部分组成：

1. **视觉理解**（vision.py + vision_adapters.py）：处理图片/视频文件 —— 上传图片自动描述、`read_file` 读图自动转视觉描述。
2. **窗口捕获 + 键鼠操作**（外挂模块）：让 Agent 主动「看」屏幕上的指定窗口（被遮挡也可见），并在安全约束下执行键鼠操作 —— 这是「有物理副作用」的能力，有独立的安全体系（见第 8 章）。

> 设计原则（详见 `docs/vision_agent_design.md`）：
> - 视觉调用全部**异步**，禁止阻塞主事件循环。
> - 图片二进制会 base64 后发往视觉服务方；云端 provider 时注意隐私（本地 llama.cpp 提供完全离线选项）。
> - 键鼠操作的安全模型与 LLM 内容安全系统**完全独立**，从零设计（L0~L3 分级 + 熔断 + 审计）。

---

## 第 1 章 快速开始：30 秒让 Agent 看见图片

### 1.1 在设置面板开启

打开 NORP Agent → 设置 → 视觉 API：

| 配置项 | 填写示例 | 说明 |
|---|---|---|
| 启用视觉 API | 开 | `vision_enabled` |
| Provider | `openai_compatible` | openai_compatible / anthropic / llama_cpp |
| 模型 | `gpt-4o` / `qwen-vl-max` / `claude-3-5-sonnet` | `vision_model` |
| API Key | `sk-...` | `vision_api_key`（云端 provider） |
| Base URL | 留空或 `http://localhost:11434/v1` | `vision_base_url`（本地 Ollama / vLLM / llama.cpp） |

### 1.2 测试

**方式 A：上传图片** —— 主界面输入框点附件，上传一张图片，Agent 会自动收到 `[视觉描述] ...` 的内容。

**方式 B：让 Agent 读图** —— 输入：

```
请读取 D:\images\cat.png 并描述图片内容
```

`read_file` 工具遇到图片扩展名会自动转调视觉层，返回视觉描述。

### 1.3 代码级测试（开发者）

```python
import vision

# 读一张图片并得到描述
desc = vision.describe_visual_file("D:/images/cat.png", {
    "vision_provider": "openai_compatible",
    "vision_model": "gpt-4o",
    "vision_api_key": "sk-xxx",
})
print(desc)
```

---

## 第 2 章 三种接入方式与优先级

视觉 API 支持三种 provider，**可同时配置，按优先级降级调用**：

```
优先级 1（最高）：内置 provider（vision_provider 配置）
    openai_compatible / anthropic / llama_cpp —— 开箱即用
优先级 2：本地注册回调（register_vision_handler）
    开发者在自己的代码里注册处理函数，可接任意模型/服务
优先级 3：外部服务 URL（vision_service_url）
    NORP 把图片 POST 给你自建的 HTTP 服务，服务返回文字描述
```

调用链（`vision.process_visual`）：

1. 若配置了 `vision_provider` → 调用内置 adapter（`describe_with_provider`）。
   - adapter 抛 `VisionAdapterError`（如 key 无效、模型不存在）→ 记日志后**降级**到下一步。
2. 遍历本地注册的回调（`_handlers`）→ 第一个返回非空字符串的生效；返回 `None` 或抛异常则尝试下一个。
3. 若配置了 `vision_service_url` → POST 外部服务。
4. 全部不可用 → 抛 `VisionNotConfigured`。

> 降级设计的意义：云端模型挂了，本地模型自动顶上；本地没配，外部服务兜底。

---

## 第 3 章 核心 API 参考（vision.py）

### 3.1 文件分类

```python
vision.IMAGE_EXTS  # {"png","jpg","jpeg","gif","webp","bmp","svg","ico","tiff","tif"}
vision.VIDEO_EXTS  # {"mp4","avi","mov","mkv","webm","flv","wmv","m4v","mpg","mpeg"}
```

| 函数 | 签名 | 说明 |
|---|---|---|
| `is_visual_ext` | `(ext: str) -> bool` | 扩展名（不含点、小写）是否为视觉文件 |
| `media_type_of` | `(ext: str) -> str` | 返回 `"image"` / `"video"` / `"other"` |
| `mime_of` | `(ext: str) -> str` | 扩展名 → MIME（如 `image/png`）；未知返回 `application/octet-stream` |

### 3.2 视觉处理统一入口

```python
def process_visual(data: bytes, ext: str, config: dict) -> str
```

- `data`：图片/视频二进制内容。
- `ext`：扩展名（不含点，如 `"png"`）。
- `config`：配置 dict（视觉相关键见第 6 章）。
- 返回：文字描述。
- 未配置任何 provider → 抛 `VisionNotConfigured`。

### 3.3 读取文件并描述

```python
def describe_visual_file(file_path: str, config: dict) -> str
```

- 读取视觉文件二进制并调用 `process_visual`。
- 非视觉扩展名 → 抛 `ValueError`。
- `config` 可为空 dict（此时 `process_visual` 会抛 `VisionNotConfigured`）。

### 3.4 本地回调注册（开发者接入点）

```python
def register_vision_handler(handler: Callable) -> None
def unregister_vision_handler(handler: Callable) -> None
```

handler 签名：

```python
def my_handler(data: bytes, ext: str, media_type: str) -> Optional[str]:
    # data        图片/视频二进制
    # ext         扩展名（不含点，小写），如 "png" / "mp4"
    # media_type  "image" 或 "video"
    # 返回        文字描述；返回 None 或抛异常 = 无法处理，尝试下一个 provider
```

示例（接任意多模态 API）：

```python
from vision import register_vision_handler

def my_handler(data: bytes, ext: str, media_type: str) -> str:
    # 在这里调用你自己的多模态模型（OpenAI / Qwen-VL / 本地模型等）
    return "这是一张包含猫咪的照片……"

register_vision_handler(my_handler)
```

### 3.5 异常

```python
class VisionNotConfigured(Exception):
    """未配置任何视觉处理器（既无本地回调，也无外部服务 URL）。"""
```

---

## 第 4 章 外部视觉服务协议

当使用 `vision_service_url` 时，NORP 按以下协议调用你的服务：

### 4.1 请求（POST JSON）

```
POST {vision_service_url}
Content-Type: application/json
```

```json
{
  "media_type": "image",
  "mime_type": "image/png",
  "extension": "png",
  "data": "<base64 编码的二进制>"
}
```

| 字段 | 说明 |
|---|---|
| `media_type` | `"image"` 或 `"video"` |
| `mime_type` | 标准 MIME，如 `image/png` / `video/mp4` |
| `extension` | 文件扩展名（不含点） |
| `data` | 文件二进制内容的 base64（ASCII） |

### 4.2 响应（JSON）

优先按以下字段取值：

```json
{ "description": "文字描述" }
```

兼容字段（按顺序尝试）：`description` → `text` → `result` → `content`。

也兼容 OpenAI 风格响应：

```json
{
  "choices": [
    { "message": { "content": "文字描述" } }
  ]
}
```

响应若是纯字符串（`"文字描述"`）也直接接受；都不是则返回整个 JSON 的字符串形式。

### 4.3 最小服务端示例（FastAPI）

```python
# server.py —— 运行: uvicorn server:app --port 8765
import base64
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/describe")
async def describe(req: Request):
    body = await req.json()
    image_bytes = base64.b64decode(body["data"])
    # 在这里调用你的视觉模型，返回文字描述
    description = f"收到一张 {body['mime_type']}，共 {len(image_bytes)} 字节"
    return {"description": description}
```

配置：`vision_service_url = "http://127.0.0.1:8765/describe"`。

> 注意：外部服务方式会把图片 base64 后发往该地址。本地服务请用 `127.0.0.1`。

---

## 第 5 章 内置 Provider 适配层（vision_adapters.py）

### 5.1 统一接口

所有内置 provider 实现**同一个签名**，主逻辑只认它：

```python
def describe(data: bytes, ext: str, mime: str, prompt: str, cfg: dict) -> str
# data   图片二进制
# ext    扩展名
# mime   MIME
# prompt 给模型的指令
# cfg    该 provider 的配置（vision_* 键）
# 返回   模型输出的文字描述
```

provider 注册表与统一调用入口：

```python
from vision_adapters import ADAPTERS, describe_with_provider, VisionAdapterError

desc = describe_with_provider("openai_compatible", data, "png", "image/png", prompt, cfg)
# 未知 provider / 调用失败 → VisionAdapterError（带 provider 名与原因）
```

### 5.2 OpenAICompatibleAdapter（`openai_compatible`）

**覆盖**：OpenAI / Qwen-VL / GLM-4V / Ollama（/v1）/ vLLM / llama.cpp 的 /v1 端点。

- 依赖：`pip install openai`
- 协议：`/chat/completions`，`content` 里 `text` + `image_url`（data URL）
- 配置键：`vision_model`（必填）、`vision_api_key`（可空，本地服务用 `"not-needed"`）、`vision_base_url`、`vision_max_tokens`（默认 1024）、`vision_temperature`（默认 0.2）

```python
cfg = {
    "vision_provider": "openai_compatible",
    "vision_model": "qwen-vl-max",
    "vision_api_key": "sk-xxx",
    "vision_base_url": "",          # 本地 Ollama: http://localhost:11434/v1
    "vision_max_tokens": 1024,
    "vision_temperature": 0.2,
}
```

### 5.3 AnthropicAdapter（`anthropic`）

**覆盖**：Claude（claude-3-5-sonnet 等）。

- 依赖：`pip install anthropic`
- 协议：`/v1/messages`，`content` 里 `{"type":"image","source":{"type":"base64","media_type":...,"data":...}}` + text
- 配置键：`vision_model`（必填）、`vision_api_key`（必填）、`vision_max_tokens`、`vision_temperature`

### 5.4 LlamaCppAdapter（`llama_cpp`）

**覆盖**：llama.cpp server 的 **raw `/completion`** 接口（多模态模型，`image_data` 传图）。完全离线。

- 配置键：`vision_base_url`（必填，如 `http://127.0.0.1:8080`）、`vision_max_tokens`、`vision_temperature`、`vision_timeout`（默认 120 秒）
- 兼容多种响应字段：`content` / `text` / `result` / `response`
- 提示：若你的 llama-server 启用了 OpenAI 兼容端点（`/v1/chat/completions`），改用 `openai_compatible` 并指向 `{base_url}/v1` 功能等价。

### 5.5 新增自定义 provider（重度开发者）

「格式转换」与「调用」已解耦：新增 provider = 新增一个 adapter，主逻辑零改动。

```python
# 1. 写一个 adapter 类，实现 describe()
class MyAdapter:
    name = "my_provider"

    def describe(self, data, ext, mime, prompt, cfg) -> str:
        # 构造请求 → 调用 → 解析响应 → 返回文字
        ...

# 2. 注册进 ADAPTERS
from vision_adapters import ADAPTERS
ADAPTERS["my_provider"] = MyAdapter()

# 3. 配置 vision_provider = "my_provider" 即可使用
```

异常约定：失败请抛 `VisionAdapterError(provider, message)`，上层会降级到下一个 provider；未知异常会被包装成 `VisionAdapterError`。

---

## 第 6 章 配置项全参考

全部视觉配置在 `config.json`（默认值见 `config.py`）：

| 配置键 | 默认值 | 说明 |
|---|---|---|
| `vision_enabled` | `false` | 视觉 API 总开关（影响 upload_files 链路） |
| `vision_provider` | `""` | 内置 provider：`openai_compatible` / `anthropic` / `llama_cpp`；空 = 不启用内置 |
| `vision_model` | `""` | 视觉模型名（如 gpt-4o / qwen-vl-max / claude-3-5-sonnet） |
| `vision_api_key` | `""` | 云端 provider 的 API key |
| `vision_base_url` | `""` | base URL（本地 llama.cpp / Ollama / vLLM 等） |
| `vision_max_tokens` | `1024` | 描述最大输出 token |
| `vision_temperature` | `0.2` | 采样温度（偏低 = 更确定） |
| `vision_timeout` | `120` | 请求超时（秒） |
| `vision_prompt` | `""` | 默认视觉指令 prompt（空 = 内置「请详细描述这张图片的内容。」） |
| `vision_service_url` | `""` | 外部视觉服务 URL（POST JSON，见第 4 章协议） |

---

## 第 7 章 Agent 内的视觉链路

### 7.1 上传文件链路（前端 → 视觉）

```
用户上传图片/视频
  → api.py: upload_files(files_data)
  → is_visual_ext(ext)?
      ├─ 否 → 文本提取（extract_text_from_file）
      └─ 是 → vision_enabled?
          ├─ 否 → 返回错误「未配置视觉处理能力…」
          └─ 是 → process_visual(raw, ext, cfg)
                → 结果 content = "[视觉描述] <文字>"
```

### 7.2 read_file 读图链路（工具 → 视觉）

`executor.py` / `async_executor.py` 的 `_read_file`：

```
read_file(path)
  → 扩展名是视觉文件（is_visual_ext）？
      └─ 是 → describe_visual_file(path, vision_config)
            → 返回视觉描述文本
            → VisionNotConfigured → 返回 "[视觉未配置] ..."
            → 其他异常 → 返回 "[视觉处理失败] ..."
```

所以 Agent 的 ReAct 循环里，`read_file` 直接就能「看」图片 —— **不需要任何额外配置**，只要视觉 API 开启。

> 缓存说明：异步执行器会按「路径 + mtime」缓存大文件读取结果，视觉描述也会复用，避免 ReAct 循环反复读同一张图重复调用视觉模型。

---

## 第 8 章 窗口捕获与键鼠操作（外挂模块）

这是「视觉 + 可操作 Agent」外挂模块：Agent 不仅能读磁盘上的图片，还能**主动看屏幕上的窗口**，并在安全约束下**操作电脑**。

> 安全定位：这是「有物理副作用」的能力。**裁决权**在独立的安全裁决器（SafetyArbiter）手里，Agent 只能申请、不能裁决自己。操作全程可见（左上角横幅）、可审计、可熔断（Ctrl+End 物理熔断）。

### 8.1 架构

```
Agent（主架构）
  │  发指令 + 一次性授权令牌 + 分级 + 审计
  ▼
安全裁决器 SafetyArbiter（vision_safety.py）
  │  分级 L0~L3 / 三态熔断 / 在场检测 / override / delegate
  ▼
协调器 VisionCoordinator（vision_coordinator.py）
  │  动作 → 重捕获验证 → 收敛（3 次失败停手）
  ▼
操作执行层 vision_actions.py（SendInput 键鼠注入 + 坐标闭环）
  │  捕获层 vision_capture.py（capture_worker.exe 单窗口取帧）
  ▼
capture_worker.exe（C++ Graphics Capture，被遮挡窗口也可见）
```

### 8.2 窗口捕获（vision_capture.py）

```python
from vision_capture import capture_window, capture_window_bmp, describe_window, FrameSource

# 1. 捕获窗口一帧 → PNG 字节 + 物理尺寸（可直接喂视觉模型）
result = capture_window(hwnd, timeout=10.0)
# CaptureResult(png=bytes, width=int, height=int, hwnd=int)

# 2. 捕获 + 视觉理解一步到位 → 文字描述
desc = describe_window(hwnd, config, prompt=None, timeout=10.0)
# config 含 vision_provider/vision_model/...；prompt 缺省用「描述界面布局」

# 3. 高频重捕获（动作-验证-收敛用）：capture_worker 驻留模式
with FrameSource(hwnd) as src:
    bmp = src.shot_ready()          # 最新一帧 BMP bytes（可转 PNG）
    # src.shot() / src.shot_ready(retries=50, interval=0.1) / src.close()
```

前提：`capture_worker.exe` 需先用 `capture_worker\build.bat` 编译（VS2022 桌面 C++ 负载）。未编译时调用会抛 `VisionCaptureError` 并提示。

### 8.3 操作执行层（vision_actions.py）

**坐标闭环**（视觉模型坐标 → 屏幕坐标）：

```python
from vision_actions import (
    image_to_screen, client_to_screen, get_window_dpi,
    get_client_rect, get_window_rect, get_cursor_pos,
    is_window, set_foreground,
    move_mouse, click, double_click, scroll, type_text,
    key_press, key_combo, VK_RETURN, VK_CONTROL,
)

# 视觉模型在「发送给模型的图片」上的坐标 → 屏幕坐标
sx, sy = image_to_screen(
    hwnd,
    img_x=320, img_y=180,          # 模型给的图片坐标
    img_width=800, img_height=600, # 发送给模型的图片尺寸
    phys_width=800, phys_height=600,  # 窗口物理像素尺寸（= capture 帧尺寸）
)
```

**键鼠原语**（全部是原子操作，**调用前必须先过 SafetyArbiter**）：

| 函数 | 说明 |
|---|---|
| `move_mouse(x, y)` | 移动鼠标到屏幕坐标 |
| `click(x, y, button="left"/"right"/"middle")` | 单击（带移动/按下/抬起延迟） |
| `double_click(x, y, button="left")` | 双击 |
| `scroll(x, y, clicks)` | 滚轮（正上负下，1 ≈ 120 单位） |
| `type_text(text, delay=0.01)` | 逐字符输入（Unicode，支持中文，不依赖键盘布局） |
| `key_press(vk, hold=0.03)` | 按下并抬起单个虚拟键 |
| `key_combo((VK_CONTROL,), ord("C"))` | 组合键（Ctrl+C） |
| `set_foreground(hwnd)` / `is_window(hwnd)` / `get_cursor_pos()` | 窗口/状态辅助 |

坐标换算要点：

- 进程启动即设为 **Per-Monitor-Aware-V2**（DPI 感知），`ClientToScreen` / `SetCursorPos` / capture 帧全程「物理像素」一致，**无需额外 96/dpi 缩放**。
- `ClientToScreen` 自动处理窗口位置、标题栏、边框。
- 窗口被拖动/缩放/跨屏后坐标缓存失效，需重新换算。

### 8.4 安全裁决器（vision_safety.py）

**操作分级 L0~L3**：

| 级别 | 定义 | 确认方式 |
|---|---|---|
| L0 | 只读（读控件/截图/视觉描述） | 不确认，直接执行 |
| L1 | 无副作用点击（切换 tab、移动鼠标） | 静默确认，可打断 |
| L2 | 有副作用写入（填表单、点删除/发送） | **必须显式确认** |
| L3 | 破坏性/不可逆（清空、提交、关闭应用） | 双因子确认，**永不免确认** |

```python
from vision_safety import SafetyArbiter, RiskLevel, CircuitState

arbiter = SafetyArbiter(
    consecutive_vetoes_to_open=3,   # 连续 N 次被否决 → 熔断 OPEN
    cooldown_sec=60.0,              # 熔断冷却时间
    idle_timeout_sec=150.0,         # 用户空闲多久判定「离开」
    idle_allow_operate=False,       # 空闲后是否允许继续操作（默认锁死）
    delegate_window_sec=300.0,      # delegate 让渡（预授权）时长
    delegate_scope="window",        # window | app | session
    delegate_max_risk="L2",         # 让渡最多覆盖到 L2（L3 永不免确认）
    auto_tighten_threshold=3,
    cooldown_after_veto=60.0,
    max_failures=3,                 # 同一操作连续失败 N 次 → 停手
)

# 核心裁决（纯决策，不弹 UI）
decision = arbiter.evaluate("click", RiskLevel.L2, "计算器", user_confirmed=False)
# Decision(allowed, reason, risk, op, requires_confirmation)
if decision.requires_confirmation:
    approved = ui_confirm(...)          # 上层弹窗问用户
    decision = arbiter.evaluate("click", RiskLevel.L2, "计算器", user_confirmed=approved)
if decision.allowed:
    ...  # 执行
```

**状态控制 API**：

| 方法 | 说明 |
|---|---|
| `user_veto()` | 用户否决 → 连续计数，达阈值触发熔断 OPEN |
| `hotkey_emergency_stop()` | **Ctrl+End 物理熔断**：最高优先级旁路，立即 OPEN |
| `manual_reset()` | 用户手动复位：冷却期过后 OPEN → HALF-OPEN（试探） |
| `report_trial_outcome(success)` | HALF-OPEN 试探结果：成功 → CLOSED，失败 → OPEN |
| `begin_operation()` / `end_operation()` | 标记 Agent 操作序列开始/结束 |
| `notify_user_input()` | 用户键鼠输入：刷新在场时间戳；操作中 → 触发 override 接管 |
| `resume_from_override()` | 用户明确指示恢复 |
| `grant_delegate(scope, max_risk, window_sec, target_window)` | 用户主动让渡确认权（预授权） |
| `revoke_delegate()` | 撤销让渡，立即回到事事确认 |
| `record_failure(op, target_window)` / `record_success(...)` | 失败/成功收敛计数 |
| `audit_log()` | 审计记录（一等公民，每次裁决一条） |

**三态熔断机**：`CLOSED`（放行）→ 连续 3 次否决 → `OPEN`（拒绝一切 L1+，只保留 L0 读）→ 冷却 60s + 用户复位 → `HALF-OPEN`（允许单个 L1 试探）→ 成功回 CLOSED / 失败回 OPEN。

**铁律**：熔断后只能「用户手动复位」或「冷却 + 试探」，**Agent 永远不能自己把自己从熔断里拉出来**。

### 8.5 协调器（vision_coordinator.py）：动作-验证-收敛

```python
from vision_coordinator import (
    VisionCoordinator, ActionSpec, ActionResult, Verdict,
    SendInputExecutor, PixelDiffVerifier, VisionModelVerifier,
)

coord = VisionCoordinator(
    hwnd=123456,
    config=vision_cfg,              # 视觉配置（VisionModelVerifier 用）
    arbiter=arbiter,                # 可注入自定义裁决器
    confirm_fn=ui_confirm,          # 可注入确认回调（L2/L3 弹窗）
    settle_delay=0.3,               # 动作后等待应用渲染的延迟
)

result = coord.run(ActionSpec(
    op="click",
    risk="L2",
    payload={"x": 320, "y": 180, "img_w": 800, "img_h": 600},
    target_window="计算器",
))

print(result.to_report())       # 「我点了什么 / 看到了什么 / 为什么失败」
print(result.to_result_xml())   # 转 IPC <vision-result> 审计消息
```

**ActionSpec.payload 按 op 变化**：

| op | payload |
|---|---|
| `click` | `{"x","y","button","img_w","img_h"}` |
| `move` | `{"x","y"}` |
| `type` | `{"text"}` |
| `key` | `{"key":"ENTER"}` 或 `{"mods":["CTRL"],"key":"C"}` |
| `scroll` | `{"x","y","clicks"}` |

**Verdict 终态**：`EXECUTED`（执行且验证通过）/ `NEEDS_CONFIRMATION`（需要确认但没注入确认回调）/ `REJECTED`（被裁决器拒绝）/ `EXHAUSTED`（连续 3 次失败停手）/ `ERROR` / `CANCELLED`（用户拒绝确认）。

**依赖注入设计**（重度开发者）：`executor` / `verifier` / `capture_fn` / `confirm_fn` 全部可注入：

- `SendInputExecutor(hwnd)`：默认执行器。
- `PixelDiffVerifier(changed_threshold=0.02, ...)`：默认验证器，像素比对，**离线可用**（无需视觉 API）。
- `VisionModelVerifier(config, expected_desc)`：语义验证（需配置视觉 provider）。
- `capture_fn`：默认单帧冷启动；高频循环可注入 FrameSource 版。

### 8.6 IPC 协议层（vision_ipc.py）

主架构与外挂之间用「XML 信封 + JSON 负载」协议（自定规范）：

```python
from vision_ipc import (
    build_op, build_result, parse_message, validate_op,
    ResultStatus, Message, IPCError,
)

# 指令消息（主架构 → 外挂）
op_xml = build_op(
    op="click", risk="L2",
    payload={"hwnd": 123456, "x": 320, "y": 180},
    token="一次性令牌", ttl_ms=3000,
)
# <vision-op version="1.0" op="click" risk="L2" token="..." ttl_ms="3000" ts="...">
#   <payload><![CDATA[{...}]]></payload>
# </vision-op>

# 结果消息（外挂 → 主架构）
result_xml = build_result("click", ResultStatus.APPROVED,
                          {"user_approved": True}, risk="L2")

# 解析 + 校验
msg = parse_message(op_xml)                    # Message(kind="op", op="click", ...)
ok, reason = validate_op(msg, expected_token="一次性令牌")
```

`ResultStatus` 枚举：`APPROVED` / `REJECTED` / `VETOED`（用户否决）/ `CIRCUIT_OPEN`（熔断）/ `TIMEOUT`。

`validate_op` 校验：类型 / 协议版本 / 令牌 / TTL 有效期（`now - ts > ttl_ms` 判过期）。

### 8.7 外挂模块安全铁律（务必遵守）

1. **裁决权唯一归属 SafetyArbiter**：任何键鼠操作前必须先过 `evaluate()`，绝不绕过裁决器直接调用 `click` / `type_text`。
2. **不提权**：不使用 UAC / 驱动 / ring0 / 全局钩子，只用标准 SendInput / SetCursorPos，普通用户权限运行。
3. **不做全屏捕获**：只捕获用户显式选择的单个窗口。
4. **操作可见**：运行期间屏幕左上角常驻横幅「⚠ Agent 正在操控电脑，按 Ctrl+End 停止」。
5. **不阻塞主线程**：同步接口调用方（插件/工具层）应放进线程池（async_executor）执行。
6. **审计铁律**：每一次 L1+ 操作（含被否决、被熔断的）都落一条不可篡改的审计记录。

---

## 第 9 章 完整示例代码

### 9.1 示例 A：本地回调接入任意视觉 API（无内置 provider 配置）

```python
# 在你的代码 / 插件中
import base64, json, requests
from vision import register_vision_handler

def my_vision_handler(data: bytes, ext: str, media_type: str) -> str:
    """把图片发给任意 HTTP 视觉服务，返回文字描述。"""
    resp = requests.post(
        "https://my-vision.example.com/describe",
        json={
            "image_base64": base64.b64encode(data).decode("ascii"),
            "ext": ext,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["description"]

register_vision_handler(my_vision_handler)

# 之后无需任何配置，上传图片 / read_file 读图都会走这个回调
```

### 9.2 示例 B：读图 + 追问（read_file 链路）

```python
from vision import describe_visual_file, VisionNotConfigured

try:
    desc = describe_visual_file("screenshot.png", {
        "vision_provider": "openai_compatible",
        "vision_model": "qwen-vl-max",
        "vision_api_key": "sk-xxx",
        "vision_prompt": "请描述这张截图的界面布局，并读出所有可见文字。",
    })
    print(desc)
except VisionNotConfigured as e:
    print(f"未配置视觉: {e}")
```

### 9.3 示例 C：捕获窗口并描述（主动看图）

```python
import win32gui
from vision_capture import capture_window, describe_window

# 找到「记事本」窗口
hwnd = win32gui.FindWindow(None, "无标题 - 记事本")
if hwnd:
    # 捕获一帧（PNG + 尺寸）
    cap = capture_window(hwnd)
    print(f"帧尺寸: {cap.width}x{cap.height}, PNG {len(cap.png)} 字节")

    # 捕获 + 视觉理解
    desc = describe_window(hwnd, {
        "vision_provider": "openai_compatible",
        "vision_model": "gpt-4o",
        "vision_api_key": "sk-xxx",
    })
    print(desc)
```

### 9.4 示例 D：完整「点击按钮 → 验证 → 收敛」流程

```python
import time
from vision_safety import SafetyArbiter, RiskLevel
from vision_coordinator import VisionCoordinator, ActionSpec

# 1. 裁决器（安全边界）
arbiter = SafetyArbiter(
    consecutive_vetoes_to_open=3,
    cooldown_sec=60.0,
    max_failures=3,
)

# 2. 确认回调（L2/L3 弹窗，这里用控制台模拟）
def ui_confirm(spec, decision):
    print(f"需要确认: {spec.op} risk={spec.risk} ({decision.reason})")
    return input("批准? (y/n): ").strip().lower() == "y"

# 3. 协调器
coord = VisionCoordinator(
    hwnd=123456,
    arbiter=arbiter,
    confirm_fn=ui_confirm,
    settle_delay=0.3,
)

# 4. 执行一次「点击」：裁决 → 执行 → 重捕获验证 → 收敛
result = coord.run(ActionSpec(
    op="click",
    risk="L2",
    payload={"x": 320, "y": 180, "img_w": 800, "img_h": 600},
    target_window="目标应用",
))

# 5. 完整上报
print(result.to_report())
if result.verdict != "executed":
    # 失败/被拒：按 result.reason 处理，绝不自动重试超过 max_failures
    pass
```

> 生产环境请把 `coord.run()` 放进线程池（async_executor）执行，禁止阻塞主事件循环。

---

## 第 10 章 测试与调试

### 10.1 内置回归测试（dry-run，零副作用）

```bash
python _test_vision_safety.py        # 裁决器测试（分级/熔断/override/delegate/收敛）
python _test_vision_ipc.py           # IPC 协议测试（编解码/令牌/TTL）
python _test_vision_coordinator.py   # 协调器测试（动作-验证-收敛闭环）
```

需要真实窗口句柄的测试（不自动运行）：

```bash
python _test_vision_actions.py       # 坐标换算（需传真实 hwnd）
python _test_vision_capture.py       # 捕获链路（需真实 hwnd + 编译 worker）
```

### 10.2 诊断点

| 现象 | 排查 |
|---|---|
| `VisionNotConfigured` | 未配置任何 provider：检查 `vision_provider` / `vision_service_url` / 是否注册了本地回调 |
| `VisionAdapterError: [openai_compatible] ...` | 内置 provider 调用失败：检查 key / model / base_url；会降级到本地回调/外部服务 |
| `[视觉未配置] ...` | read_file 返回：视觉未配置（原因见上） |
| `[视觉处理失败] ...` | 视觉调用抛异常（网络/超时/模型报错） |
| 图片上传返回错误 | `vision_enabled` 未开启（upload_files 链路要求开启） |
| `capture_worker.exe 不存在` | 未编译：运行 `capture_worker\build.bat`（需 VS2022 桌面 C++ 负载） |
| 点击位置偏了 | 检查 `img_w/img_h` 与 `phys_width/phys_height` 是否传对（缩放比例必须精确） |

### 10.3 日志

视觉模块使用 Python logging，logger 名 `vision`。内置 provider 失败降级时会记录 warning。可在主程序日志中查看。

---

## 第 11 章 常见问题 FAQ

**Q1：没有 API Key 能用视觉吗？**
能。三种方式任选：本地 llama.cpp（`llama_cpp` provider，完全离线）、本地 Ollama/vLLM（`openai_compatible` + `vision_base_url`，key 填任意值）、自建外部服务（`vision_service_url`）。

**Q2：视频能处理吗？**
扩展名分类支持视频（mp4/avi/mov 等），`process_visual` 会把视频二进制交给 provider；但第一版**不做抽帧**，云端 provider 对视频的支持取决于模型本身。设计文档明确「先做图片链路，视频暂缓」。

**Q3：read_file 读图会占用大量 token 吗？**
视觉模型返回的是**文字描述**（受 `vision_max_tokens` 限制，默认 1024），图片本身不进入 LLM 上下文。若你的 ReAct 循环需要反复读同一张图，建议在业务侧自行缓存描述（设计文档已规划「路径 + mtime」缓存，当前版本尚未落地）。

**Q4：图片会发给谁？**
取决于 provider：云端 provider（openai_compatible / anthropic）会把图片 base64 后发往对应服务；`llama_cpp` 完全本地；外部服务 URL 发往你配置的地址。隐私敏感数据请用本地方案。

**Q5：键鼠操作安全吗？**
安全模型从零设计：分级确认（L2/L3 必须显式批准）、三态熔断（连续否决自动熔断）、Ctrl+End 物理熔断（不经过任何软件链路）、空闲 150 秒锁死、用户一动 Agent 立即挂起（override）、每次操作留审计。**裁决权在独立裁决器，Agent 不能自己批准自己。**

**Q6：操作失败会怎样？**
动作-验证-收敛：最多重试 3 次（自动微调/降级），超过即停手，把「我点了什么、看到了什么、为什么失败」完整上报，等待用户命令，不再自动执行。

**Q7：如何让 Agent 通过插件调用视觉？**
在插件里 import vision / vision_capture / vision_coordinator，把操作封装成工具（TOOLS + execute），并放进线程池执行。注意插件默认网络策略是 `deny`，云端视觉 provider 需要按插件开发指南配置网络权限（或使用本地 provider）。

---

## 附录 A API 速查表

### vision.py

```python
process_visual(data: bytes, ext: str, config: dict) -> str
describe_visual_file(file_path: str, config: dict) -> str
register_vision_handler(handler: Callable[[bytes, str, str], Optional[str]]) -> None
unregister_vision_handler(handler) -> None
is_visual_ext(ext: str) -> bool
media_type_of(ext: str) -> str          # "image" | "video" | "other"
mime_of(ext: str) -> str
VisionNotConfigured(Exception)
IMAGE_EXTS / VIDEO_EXTS
```

### vision_adapters.py

```python
describe_with_provider(provider, data, ext, mime, prompt, cfg) -> str
ADAPTERS  # {"openai_compatible": ..., "anthropic": ..., "llama_cpp": ...}
VisionAdapterError(provider, message)
# 统一 adapter 签名: describe(data, ext, mime, prompt, cfg) -> str
```

### vision_capture.py

```python
capture_window(hwnd, timeout=10.0) -> CaptureResult(png, width, height, hwnd)
capture_window_bmp(hwnd, timeout=10.0) -> bytes      # 原始 BMP（调试用）
describe_window(hwnd, config, prompt=None, timeout=10.0) -> str
FrameSource(hwnd, ready_timeout=10.0)  # with 语法; .shot()/.shot_ready()/.close()
VisionCaptureError(Exception)
```

### vision_actions.py

```python
# 坐标
get_window_dpi(hwnd) -> int
client_to_screen(hwnd, x, y) -> (sx, sy)
image_to_screen(hwnd, img_x, img_y, img_width, img_height, phys_width, phys_height) -> (sx, sy)
get_client_rect(hwnd) -> (l, t, r, b)
get_window_rect(hwnd) -> (l, t, r, b)
get_cursor_pos() -> (x, y)
is_window(hwnd) -> bool
set_foreground(hwnd) -> None
# 键鼠（必须先过裁决器）
move_mouse(x, y); click(x, y, button="left"); double_click(x, y)
scroll(x, y, clicks); type_text(text, delay=0.01)
key_press(vk, hold=0.03); key_combo((mods...), key)
# VK_* 常量: VK_RETURN / VK_TAB / VK_CONTROL / VK_MENU / VK_ESCAPE / VK_SPACE / VK_LEFT ... VK_DELETE
VisionActionError(Exception)
```

### vision_safety.py

```python
RiskLevel: L0 / L1 / L2 / L3          # IntEnum，可比较
CircuitState: CLOSED / OPEN / HALF_OPEN
Decision(allowed, reason, risk, op, requires_confirmation)
AuditRecord(ts, op, risk, target_window, decision, reason, user_confirmed)
SafetyArbiter(consecutive_vetoes_to_open=3, cooldown_sec=60, idle_timeout_sec=150,
              idle_allow_operate=False, delegate_window_sec=300, delegate_scope="window",
              delegate_max_risk="L2", auto_tighten_threshold=3, cooldown_after_veto=60,
              max_failures=3, clock=time.time)
  .evaluate(op, risk, target_window=None, user_confirmed=False) -> Decision
  .user_veto() / .hotkey_emergency_stop() / .manual_reset() / .report_trial_outcome(success)
  .begin_operation() / .end_operation() / .notify_user_input() / .resume_from_override()
  .grant_delegate(scope, max_risk, window_sec, target_window=None) / .revoke_delegate()
  .record_failure(op, target_window=None) -> int / .record_success(op, target_window=None)
  .audit_log() -> list[AuditRecord]
  .circuit / .override_active   # 属性
```

### vision_coordinator.py

```python
ActionSpec(op, risk, payload, target_window=None)
VerificationResult(success, detail="", confidence=0.0)
ActionResult(op, risk, verdict, reason, attempts=0, allowed=False, verification=None, target_window=None)
  .to_report() -> str   .to_result_xml() -> str
Verdict: EXECUTED / NEEDS_CONFIRMATION / REJECTED / EXHAUSTED / ERROR / CANCELLED
SendInputExecutor(hwnd)                     # .execute(spec, before)
PixelDiffVerifier(changed_threshold=0.02)   # .verify(spec, before, after)
VisionModelVerifier(config, expected_desc=None)
VisionCoordinator(hwnd, config=None, arbiter=None, executor=None, verifier=None,
                  capture_fn=None, confirm_fn=None, settle_delay=0.3)
  .run(spec: ActionSpec) -> ActionResult
  .arbiter / .audit_log()
CoordinatorError(Exception)
```

### vision_ipc.py

```python
build_op(op, risk, payload, token, ttl_ms=3000, ts=None, version="1.0") -> str
build_result(op, status, payload, risk=None, ts=None, version="1.0") -> str
parse_message(xml_str) -> Message
validate_op(msg, expected_token=None, now_ms=None) -> (bool, str)
ResultStatus: APPROVED / REJECTED / VETOED / CIRCUIT_OPEN / TIMEOUT
Message(kind, op, payload, version, risk, token, ttl_ms, ts, status)
IPCError(Exception)
```

---

> 文档维护：本文档对应 vision 系列模块当前实现（2026-08）。设计与里程碑细节见 `docs/vision_agent_design.md`，成果总结见 `视觉操作外挂_成果总结.txt`。请以代码为准。

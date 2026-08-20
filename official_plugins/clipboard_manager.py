# ──────────────────────────────────────────────────────────────
# Plugin: Clipboard Manager
# Publisher: xingluosama
# Version: 1.0.0
# Description: 剪贴板管理器：读写系统剪贴板、剪贴板历史记录、
#   清空剪贴板。让 agent 可以灵活复制粘贴文本内容。
# ──────────────────────────────────────────────────────────────

PLUGIN_NAME = "Clipboard Manager"
PLUGIN_PUBLISHER = "xingluosama"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "剪贴板管理器：读写系统剪贴板、历史记录、清空剪贴板。"

import os
import platform
import subprocess
import json
import threading
from datetime import datetime

# ── 1. 工具注册 ────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "clipboard_read",
            "description": (
                "读取系统剪贴板中的文本内容，并自动记录到剪贴板历史。"
                "用户说「读取剪贴板」「粘贴」「看看剪贴板里有什么」时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard_write",
            "description": (
                "将文本写入系统剪贴板，并自动记录到剪贴板历史。"
                "用户说「复制到剪贴板」「拷贝这段文字」时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要写入剪贴板的文本内容"
                    }
                },
                "required": ["text"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard_history",
            "description": (
                "查看剪贴板历史记录。列出最近复制/读取的剪贴板内容摘要。"
                "用户说「剪贴板历史」「之前复制了什么」「查看复制记录」时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "返回条数上限，默认 10，最大 30",
                        "default": 10
                    }
                },
                "required": [],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard_clear",
            "description": (
                "清空系统剪贴板。用户说「清空剪贴板」「清除剪贴板」时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False
            }
        }
    }
]

# ── 2. 历史记录存储 ────────────────────────────────────────────

_HISTORY_FILE = "clipboard_history.json"
_MAX_HISTORY = 100
_lock = threading.Lock()


def _get_history_path(context) -> str:
    """获取历史记录文件路径。"""
    return os.path.join(context.app_dir, _HISTORY_FILE)


def _load_history(context) -> list:
    """加载剪贴板历史。"""
    path = _get_history_path(context)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_history(context, history: list):
    """保存剪贴板历史。"""
    if len(history) > _MAX_HISTORY:
        history = history[-_MAX_HISTORY:]
    path = _get_history_path(context)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def _add_to_history(context, text: str, source: str = "write"):
    """将剪贴板操作记录到历史。"""
    if not text.strip():
        return
    with _lock:
        history = _load_history(context)
        entry = {
            "text": text,
            "source": source,
            "length": len(text),
            "preview": text[:100] + "..." if len(text) > 100 else text,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        history.append(entry)
        _save_history(context, history)


# ── 3. 跨平台剪贴板操作 ─────────────────────────────────────────

def _read_clipboard_impl() -> str:
    """跨平台读取剪贴板。"""
    system = platform.system()
    try:
        if system == "Windows":
            # 优先使用 PowerShell（支持 UTF-8）
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                    capture_output=True, text=True, timeout=15,
                    creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                )
                if result.returncode == 0:
                    return result.stdout
            except FileNotFoundError:
                pass

            # 备用：通过临时文件读取（兼容非英文环境）
            import tempfile
            tmp = os.path.join(tempfile.gettempdir(), "_vibe_clipread.txt")
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Get-Clipboard | Out-File -FilePath '{tmp}' -Encoding UTF8"],
                capture_output=True, timeout=15,
                creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            try:
                with open(tmp, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
            finally:
                try:
                    os.remove(tmp)
                except Exception:
                    pass

        elif system == "Darwin":
            result = subprocess.run(
                ["pbpaste"], capture_output=True, text=True, timeout=15
            )
            return result.stdout

        else:  # Linux
            for cmd in [["wl-paste"], ["xclip", "-selection", "clipboard", "-o"]]:
                try:
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=15
                    )
                    if result.returncode == 0:
                        return result.stdout
                except FileNotFoundError:
                    continue
            return "Error: No clipboard tool found. Install xclip (X11) or wl-clipboard (Wayland)."

    except subprocess.TimeoutExpired:
        return "Error: clipboard read timed out"
    except Exception as e:
        return f"Failed to read clipboard: {str(e)}"

    return ""


def _write_clipboard_impl(text: str) -> bool:
    """跨平台写入剪贴板。返回是否成功。"""
    system = platform.system()
    try:
        if system == "Windows":
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"],
                input=text, capture_output=True, text=True, timeout=15,
                creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            return proc.returncode == 0

        elif system == "Darwin":
            proc = subprocess.run(
                ["pbcopy"], input=text, capture_output=True, text=True, timeout=15
            )
            return proc.returncode == 0

        else:  # Linux
            for cmd in [["wl-copy"], ["xclip", "-selection", "clipboard"]]:
                try:
                    proc = subprocess.run(
                        cmd, input=text, capture_output=True, text=True, timeout=15
                    )
                    if proc.returncode == 0:
                        return True
                except FileNotFoundError:
                    continue
            return False

    except Exception:
        return False


def _clear_clipboard_impl() -> bool:
    """跨平台清空剪贴板。"""
    return _write_clipboard_impl("")


# ── 4. 工具实现 ────────────────────────────────────────────────

def _handle_read_clipboard(args: dict, context) -> str:
    """读取剪贴板。"""
    text = _read_clipboard_impl()

    # 记录到历史
    if text and not text.startswith("Error:") and not text.startswith("Failed"):
        _add_to_history(context, text, source="read")

    if not text:
        return "📋 (剪贴板为空)"
    if text.startswith("Error:") or text.startswith("Failed"):
        return f"❌ {text}"

    context.logger.info(f"Clipboard read: {len(text)} chars")
    return text


def _handle_write_clipboard(args: dict, context) -> str:
    """写入剪贴板。"""
    text = args.get("text", "")
    if not text:
        return "❌ 剪贴板内容不能为空。"

    ok = _write_clipboard_impl(text)
    if not ok:
        return "❌ 写入剪贴板失败。请检查系统剪贴板工具是否可用。"

    # 记录到历史
    _add_to_history(context, text, source="write")

    preview = text[:80] + "..." if len(text) > 80 else text
    context.logger.info(f"Clipboard write: {len(text)} chars")
    return f"✅ 已复制到剪贴板 ({len(text)} 字符):\n```\n{preview}\n```"


def _handle_clipboard_history(args: dict, context) -> str:
    """查看剪贴板历史。"""
    limit = min(args.get("limit", 10), 30)
    history = _load_history(context)

    if not history:
        return "📋 剪贴板历史为空。还没有复制或读取过任何内容。"

    # 倒序显示（最新的在前）
    history = list(reversed(history))[:limit]

    lines = [f"📋 **剪贴板历史** (最近 {len(history)} 条)", ""]
    for i, entry in enumerate(history, 1):
        ts = entry.get("timestamp", "?")
        source = entry.get("source", "?")
        length = entry.get("length", 0)
        preview = entry.get("preview", "")[:60]
        icon = "📤" if source == "write" else "📥"
        lines.append(
            f"**{i}.** {icon} {preview}\n"
            f"    ⏰ {ts} | {length} 字符 | 来源: {source}"
        )

    return "\n".join(lines)


def _handle_clear_clipboard(args: dict, context) -> str:
    """清空剪贴板。"""
    ok = _clear_clipboard_impl()
    if not ok:
        return "❌ 清空剪贴板失败。"

    context.logger.info("Clipboard cleared")
    return "✅ 剪贴板已清空。"


# ── 5. 工具分发 ────────────────────────────────────────────────

def execute(tool_name: str, args: dict, context) -> str:
    if tool_name == "clipboard_read":
        return _handle_read_clipboard(args, context)
    if tool_name == "clipboard_write":
        return _handle_write_clipboard(args, context)
    if tool_name == "clipboard_history":
        return _handle_clipboard_history(args, context)
    if tool_name == "clipboard_clear":
        return _handle_clear_clipboard(args, context)
    return f"Unknown tool: {tool_name}"


# ── 6. 生命周期钩子 ────────────────────────────────────────────

def on_agent_init(context):
    """初始化剪贴板管理器。"""
    history = _load_history(context)
    context.storage["clipboard_ops"] = 0
    context.storage["history_count"] = len(history)
    context.logger.info(
        f"Clipboard Manager loaded — {len(history)} history entries 📋"
    )


def on_agent_shutdown(context):
    """会话结束时输出统计。"""
    ops = context.storage.get("clipboard_ops", 0)
    history = _load_history(context)
    context.logger.info(
        f"Clipboard Manager shutting down. "
        f"{ops} operation(s) this session, {len(history)} total history entries."
    )


def on_task_start(task_text: str, context):
    """检测剪贴板相关任务。"""
    keywords = ["剪贴板", "复制", "粘贴", "拷贝", "clipboard", "copy", "paste"]
    if any(kw in task_text.lower() for kw in keywords):
        context.logger.info(f"Clipboard task detected: {task_text[:80]}")


def on_task_done(summary: str, final_reply: str, context):
    """任务完成后统计操作。"""
    pass  # 保持轻量

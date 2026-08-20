# ──────────────────────────────────────────────────────────────
# Plugin: Dev Utilities
# Publisher: xingluosama
# Version: 1.0.0
# Description: 开发实用工具集：UUID/密码生成、哈希计算、时间戳转换、
#   代码行数统计。
# ──────────────────────────────────────────────────────────────

PLUGIN_NAME = "Dev Utilities"
PLUGIN_PUBLISHER = "xingluosama"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "开发实用工具集：UUID/密码生成、哈希计算、时间戳转换、代码行数统计。"

import hashlib
import os
import random
import string
import time
import uuid
from datetime import datetime, timezone

# ── 1. 工具注册 ────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "generate_uuid",
            "description": (
                "生成 UUID（通用唯一标识符）。"
                "支持 UUID4（随机）和 UUID7（时间排序）两种格式。"
                "默认生成 UUID4。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "version": {
                        "type": "integer",
                        "description": "UUID 版本：4（随机，默认）或 7（时间排序）",
                        "default": 4
                    },
                    "count": {
                        "type": "integer",
                        "description": "生成数量，默认 1，最大 20",
                        "default": 1
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
            "name": "generate_password",
            "description": (
                "生成安全的随机密码。"
                "可自定义长度、是否包含大写/小写/数字/特殊字符。"
                "默认 16 位，包含所有字符类型。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "length": {
                        "type": "integer",
                        "description": "密码长度，默认 16，范围 8-128",
                        "default": 16
                    },
                    "include_upper": {
                        "type": "boolean",
                        "description": "包含大写字母，默认 true",
                        "default": True
                    },
                    "include_lower": {
                        "type": "boolean",
                        "description": "包含小写字母，默认 true",
                        "default": True
                    },
                    "include_digits": {
                        "type": "boolean",
                        "description": "包含数字，默认 true",
                        "default": True
                    },
                    "include_symbols": {
                        "type": "boolean",
                        "description": "包含特殊字符，默认 true",
                        "default": True
                    },
                    "count": {
                        "type": "integer",
                        "description": "生成数量，默认 1，最大 10",
                        "default": 1
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
            "name": "hash_text",
            "description": (
                "计算文本的哈希值。支持 MD5、SHA1、SHA256、SHA512。"
                "可用于验证文件完整性或生成简短标识符。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要计算哈希的文本"
                    },
                    "algorithm": {
                        "type": "string",
                        "description": "哈希算法：md5、sha1、sha256（默认）、sha512",
                        "default": "sha256"
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
            "name": "timestamp_convert",
            "description": (
                "时间戳与日期互转。"
                "支持：Unix 时间戳 → 日期、日期 → Unix 时间戳。"
                "自动检测输入格式（10位秒级 / 13位毫秒级）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {
                        "type": "string",
                        "description": (
                            "要转换的值。可以是 Unix 时间戳（如 '1700000000' 或 '1700000000000'），"
                            "也可以是日期字符串（如 '2024-01-01' 或 '2024-01-01 12:30:00'）"
                        )
                    }
                },
                "required": ["value"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "count_lines",
            "description": (
                "统计项目中代码文件的行数。"
                "按文件类型分类统计：总行数、代码行、注释行、空行。"
                "支持 Python、JavaScript、TypeScript、Go、Rust、Java、C/C++ 等。"
                "默认统计工作区根目录下所有代码文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "要统计的目录（相对于工作区根目录），默认 '.' 表示根目录",
                        "default": "."
                    },
                    "include_hidden": {
                        "type": "boolean",
                        "description": "是否包含隐藏目录（如 .git），默认 false",
                        "default": False
                    }
                },
                "required": [],
                "additionalProperties": False
            }
        }
    }
]

# ── 2. 工具实现 ────────────────────────────────────────────────

_CODE_EXTENSIONS = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "React TSX",
    ".jsx": "React JSX",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".c": "C",
    ".cpp": "C++",
    ".h": "C/C++ Header",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".lua": "Lua",
    ".sh": "Shell",
    ".bat": "Batch",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".html": "HTML",
    ".css": "CSS",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".xml": "XML",
    ".md": "Markdown",
}

_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "env", ".env", "dist", "build", "target", ".next",
    ".nuxt", "vendor", ".idea", ".vscode",
}

_COMMENT_PREFIXES = {
    ".py": "#",
    ".js": "//",
    ".ts": "//",
    ".tsx": "//",
    ".jsx": "//",
    ".go": "//",
    ".rs": "//",
    ".java": "//",
    ".kt": "//",
    ".swift": "//",
    ".c": "//",
    ".cpp": "//",
    ".h": "//",
    ".cs": "//",
    ".rb": "#",
    ".php": "//",
    ".lua": "--",
    ".sh": "#",
    ".bat": "REM",
    ".ps1": "#",
    ".sql": "--",
}


def _handle_generate_uuid(args: dict) -> str:
    """生成 UUID。"""
    version = args.get("version", 4)
    count = min(args.get("count", 1), 20)

    results = []
    for _ in range(count):
        if version == 7:
            # UUID7 per RFC 9562 – time-ordered UUID
            # Structure: 48-bit Unix ts (ms) | 4-bit ver=7 | 12-bit rand
            #             | 2-bit variant=10 | 62-bit random
            ts = int(time.time() * 1000)  # 48-bit millisecond timestamp
            ts_bytes = ts.to_bytes(6, 'big')  # 6 bytes = 48 bits
            rand_bytes = os.urandom(10)

            uuid_bytes = bytearray(16)
            uuid_bytes[0:6] = ts_bytes
            uuid_bytes[6:16] = rand_bytes

            # Set version (7) in the top 4 bits of byte 6
            uuid_bytes[6] = (uuid_bytes[6] & 0x0F) | 0x70
            # Set variant (10xx) in the top 2 bits of byte 8
            uuid_bytes[8] = (uuid_bytes[8] & 0x3F) | 0x80

            results.append(str(uuid.UUID(bytes=bytes(uuid_bytes))))
        else:
            results.append(str(uuid.uuid4()))

    if count == 1:
        return f"🆔 UUID: `{results[0]}`"
    return "🆔 **UUIDs:**\n" + "\n".join(f"  {i+1}. `{u}`" for i, u in enumerate(results))


def _handle_generate_password(args: dict) -> str:
    """生成安全密码。"""
    length = max(8, min(args.get("length", 16), 128))
    count = min(args.get("count", 1), 10)

    # 构建字符池
    pool = ""
    if args.get("include_upper", True):
        pool += string.ascii_uppercase
    if args.get("include_lower", True):
        pool += string.ascii_lowercase
    if args.get("include_digits", True):
        pool += string.digits
    if args.get("include_symbols", True):
        pool += "!@#$%^&*()-_=+[]{}|;:,.<>?/~"

    if not pool:
        pool = string.ascii_letters + string.digits

    # 使用 secrets 模块生成密码（密码学安全）
    import secrets

    def _gen_one() -> str:
        # 确保至少包含每种类型的一个字符
        chars = []
        if args.get("include_upper", True):
            chars.append(secrets.choice(string.ascii_uppercase))
        if args.get("include_lower", True):
            chars.append(secrets.choice(string.ascii_lowercase))
        if args.get("include_digits", True):
            chars.append(secrets.choice(string.digits))
        if args.get("include_symbols", True):
            chars.append(secrets.choice("!@#$%^&*()-_=+[]{}|;:,.<>?/~"))

        remaining = length - len(chars)
        chars.extend(secrets.choice(pool) for _ in range(remaining))
        random.shuffle(chars)
        return "".join(chars)

    passwords = [_gen_one() for _ in range(count)]

    # 评估密码强度
    def _strength(pw: str) -> str:
        score = 0
        if len(pw) >= 16:
            score += 2
        elif len(pw) >= 12:
            score += 1
        if any(c.isupper() for c in pw):
            score += 1
        if any(c.islower() for c in pw):
            score += 1
        if any(c.isdigit() for c in pw):
            score += 1
        if any(c in "!@#$%^&*()-_=+[]{}|;:,.<>?/~" for c in pw):
            score += 2
        if score >= 6:
            return "🔒 非常强"
        if score >= 4:
            return "🔐 强"
        if score >= 2:
            return "🔓 中等"
        return "⚠️ 弱"

    if count == 1:
        return (
            f"🔑 **生成的密码**: `{passwords[0]}`\n"
            f"  长度: {len(passwords[0])} 位 | 强度: {_strength(passwords[0])}"
        )

    lines = ["🔑 **生成的密码**:", ""]
    for i, pw in enumerate(passwords, 1):
        lines.append(f"  {i}. `{pw}` ({_strength(pw)})")
    return "\n".join(lines)


def _handle_hash_text(args: dict) -> str:
    """计算哈希。"""
    text = args.get("text", "")
    algorithm = args.get("algorithm", "sha256").lower()

    algo_map = {
        "md5": hashlib.md5,
        "sha1": hashlib.sha1,
        "sha256": hashlib.sha256,
        "sha512": hashlib.sha512,
    }

    if algorithm not in algo_map:
        supported = ", ".join(algo_map.keys())
        return f"❌ 不支持的算法 `{algorithm}`。支持：{supported}"

    h = algo_map[algorithm](text.encode("utf-8"))
    return (
        f"🔐 **{algorithm.upper()} 哈希**:\n"
        f"```\n{h.hexdigest()}\n```\n"
        f"  输入长度: {len(text)} 字符"
    )


def _handle_timestamp_convert(args: dict) -> str:
    """时间戳转换。"""
    value = args.get("value", "").strip()

    # 尝试解析为纯数字（时间戳）
    if value.replace("-", "").replace(".", "").isdigit() and not value.startswith("-"):
        ts = float(value)
        # 判断是秒级还是毫秒级
        if ts > 1e12:
            # 毫秒级
            ts /= 1000.0
        try:
            dt_local = datetime.fromtimestamp(ts)
            dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError) as e:
            return f"❌ 时间戳超出范围: {e}"

        return (
            f"🕐 **时间戳 → 日期**:\n"
            f"  输入: `{value}`\n"
            f"  本地时间: {dt_local.strftime('%Y-%m-%d %H:%M:%S')} (周{['一','二','三','四','五','六','日'][dt_local.weekday()]})\n"
            f"  UTC 时间: {dt_utc.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"  ISO 8601: {dt_utc.isoformat()}"
        )

    # 尝试解析为日期字符串
    date_formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y",
        "%Y年%m月%d日 %H:%M:%S",
        "%Y年%m月%d日",
    ]

    for fmt in date_formats:
        try:
            dt = datetime.strptime(value, fmt)
            ts = dt.timestamp()
            return (
                f"🕐 **日期 → 时间戳**:\n"
                f"  输入: `{value}`\n"
                f"  Unix 时间戳: `{int(ts)}` (秒)\n"
                f"  毫秒时间戳: `{int(ts * 1000)}`\n"
                f"  星期: 周{['一','二','三','四','五','六','日'][dt.weekday()]}"
            )
        except ValueError:
            continue

    return f"❌ 无法解析 `{value}`。请使用 Unix 时间戳（如 1700000000）或日期（如 2024-01-01 12:00:00）。"


def _handle_count_lines(args: dict, context) -> str:
    """统计代码行数。"""
    directory = args.get("directory", ".")
    include_hidden = args.get("include_hidden", False)

    target_dir = os.path.join(context.project_root, directory)
    target_dir = os.path.normpath(target_dir)

    if not os.path.isdir(target_dir):
        return f"❌ 目录不存在: {directory}"

    # 按扩展名统计
    stats = {}  # ext -> {files, total, code, blank, comment}

    for dirpath, dirnames, filenames in os.walk(target_dir):
        # 跳过隐藏目录和常见排除目录
        rel_dir = os.path.relpath(dirpath, target_dir)
        parts = set(rel_dir.split(os.sep))
        if not include_hidden:
            if parts & _SKIP_DIRS:
                continue
            # 跳过以 . 开头的目录
            if any(p.startswith(".") for p in parts if p != "."):
                continue

        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in _CODE_EXTENSIONS:
                continue

            filepath = os.path.join(dirpath, filename)
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    file_lines = f.readlines()
            except Exception:
                continue

            comment_prefix = _COMMENT_PREFIXES.get(ext, "#")

            total = len(file_lines)
            blank = sum(1 for l in file_lines if not l.strip())
            comment = sum(
                1 for l in file_lines
                if l.strip().startswith(comment_prefix) and l.strip()
            )
            code = total - blank - comment

            if ext not in stats:
                stats[ext] = {"files": 0, "total": 0, "code": 0, "blank": 0, "comment": 0}
            stats[ext]["files"] += 1
            stats[ext]["total"] += total
            stats[ext]["code"] += code
            stats[ext]["blank"] += blank
            stats[ext]["comment"] += comment

    if not stats:
        return f"📊 在 `{directory}` 中未找到代码文件。"

    # 排序
    sorted_stats = sorted(stats.items(), key=lambda x: x[1]["total"], reverse=True)

    grand_total = sum(s["total"] for _, s in sorted_stats)
    grand_code = sum(s["code"] for _, s in sorted_stats)
    grand_blank = sum(s["blank"] for _, s in sorted_stats)
    grand_comment = sum(s["comment"] for _, s in sorted_stats)
    grand_files = sum(s["files"] for _, s in sorted_stats)

    lines = [
        f"📊 **代码行数统计** — `{directory}`",
        "",
        f"| 语言 | 文件数 | 总行 | 代码行 | 注释行 | 空行 |",
        f"|------|--------|------|--------|--------|------|",
    ]

    for ext, s in sorted_stats:
        lang = _CODE_EXTENSIONS.get(ext, ext)
        lines.append(
            f"| {lang} | {s['files']} | {s['total']:,} | "
            f"{s['code']:,} | {s['comment']:,} | {s['blank']:,} |"
        )

    lines.append(
        f"| **合计** | **{grand_files}** | **{grand_total:,}** | "
        f"**{grand_code:,}** | **{grand_comment:,}** | **{grand_blank:,}** |"
    )

    lines.append("")
    if grand_total > 0:
        lines.append(
            f"💡 代码密度: {grand_code / max(grand_total, 1) * 100:.1f}% "
            f"（注释率 {grand_comment / max(grand_total, 1) * 100:.1f}%）"
        )

    return "\n".join(lines)


# ── 3. 工具分发 ────────────────────────────────────────────────

def execute(tool_name: str, args: dict, context) -> str:
    if tool_name == "generate_uuid":
        return _handle_generate_uuid(args)
    if tool_name == "generate_password":
        return _handle_generate_password(args)
    if tool_name == "hash_text":
        return _handle_hash_text(args)
    if tool_name == "timestamp_convert":
        return _handle_timestamp_convert(args)
    if tool_name == "count_lines":
        return _handle_count_lines(args, context)
    return f"Unknown tool: {tool_name}"


# ── 4. 钩子 ────────────────────────────────────────────────────

def on_agent_init(context):
    context.storage["utils_called"] = 0
    context.logger.info("Dev Utilities plugin loaded 🔧")


def on_agent_shutdown(context):
    calls = context.storage.get("utils_called", 0)
    if calls > 0:
        context.logger.info(
            f"Dev Utilities: {calls} tool call(s) this session"
        )


def on_task_start(task_text: str, context):
    """检测用户是否要使用开发工具。"""
    keywords = ["uuid", "密码", "password", "哈希", "hash",
                "时间戳", "timestamp", "代码行数", "count lines"]
    if any(kw in task_text.lower() for kw in keywords):
        context.logger.info(f"Dev utility task detected: {task_text[:80]}")

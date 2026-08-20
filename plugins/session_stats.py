# ──────────────────────────────────────────────────────────────
# Plugin: Session Stats Tracker
# Publisher: xingluosama
# Version: 1.0.0
# Description: 注册 2 个工具供 Agent 调用：session_stats（会话统计）
#   和 generate_gitignore（生成 .gitignore）。使用 7 个钩子实现
#   任务追踪和工具调用计时。
# ──────────────────────────────────────────────────────────────

PLUGIN_NAME = "Session Stats Tracker"
PLUGIN_PUBLISHER = "xingluosama"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "会话统计与 .gitignore 生成工具。使用 7 个钩子实现任务追踪和工具调用计时。"

import json
import os
import time
from datetime import datetime

# ── 1. 工具注册 ────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "session_stats",
            "description": (
                "获取当前编码会话的统计信息。"
                "返回：任务数量、工具调用次数和分布、累计 token 用量、"
                "会话运行时长、成功/失败任务数。"
                "当用户询问进度或统计数据时调用此工具。"
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
            "name": "generate_gitignore",
            "description": (
                "根据项目类型在当前工作区生成或追加 .gitignore 文件。"
                "支持类型：python, node, rust, go, java, unity, generic。"
                "如果 .gitignore 已存在，将追加缺失的条目。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_type": {
                        "type": "string",
                        "description": (
                            "项目类型。可选值：python, node, rust, go, java, unity, generic。"
                            "多个类型用逗号分隔，如 'python,node'。"
                        )
                    }
                },
                "required": ["project_type"],
                "additionalProperties": False
            }
        }
    }
]

# ── 2. 内置 .gitignore 模板 ────────────────────────────────────

_GITIGNORE_TEMPLATES = {
    "python": """
# Python
__pycache__/
*.py[cod]
*.egg-info/
.eggs/
dist/
build/
*.egg
.venv/
venv/
env/
*.so
.pytest_cache/
.mypy_cache/
.ruff_cache/
coverage/
htmlcov/
.tox/
""",
    "node": """
# Node.js
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
dist/
build/
.next/
.nuxt/
.env.local
.env.*.local
*.tsbuildinfo
coverage/
.nyc_output/
""",
    "rust": """
# Rust
target/
**/*.rs.bk
*.pdb
Cargo.lock
""",
    "go": """
# Go
*.exe
*.exe~
*.dll
*.so
*.dylib
*.test
*.out
vendor/
go.work.sum
""",
    "java": """
# Java / Gradle / Maven
*.class
*.jar
*.war
target/
build/
.gradle/
*.iml
.idea/
.classpath
.project
.settings/
bin/
""",
    "unity": """
# Unity
[Ll]ibrary/
[Tt]emp/
[Oo]bj/
[Bb]uild/
[Ll]ogs/
MemoryCaptures/
*.apk
*.aab
*.unitypackage
*.csproj
*.sln
.vs/
.idea/
""",
    "generic": """
# OS
.DS_Store
Thumbs.db
desktop.ini

# Editor
.vscode/
.idea/
*.swp
*.swo
*~

# Env
.env
.env.*
!.env.example
""",
}


# ── 3. 工具执行函数 ────────────────────────────────────────────

def execute(tool_name: str, args: dict, context) -> str:
    """工具执行 dispatcher。"""

    if tool_name == "session_stats":
        return _handle_session_stats(context)

    if tool_name == "generate_gitignore":
        project_type = args.get("project_type", "generic")
        return _handle_generate_gitignore(project_type, context)

    return f"Unknown tool: {tool_name}"


def _handle_session_stats(context) -> str:
    """生成当前会话的统计报告。"""
    s = context.storage

    # 从 storage 中读取累计数据
    task_count = s.get("task_count", 0)
    done_count = s.get("done_count", 0)
    error_count = s.get("error_count", 0)
    tool_calls_total = s.get("tool_calls_total", 0)
    tool_breakdown = s.get("tool_breakdown", {})
    total_tool_time = s.get("total_tool_time", 0.0)
    session_start = s.get("session_start", time.time())

    elapsed = time.time() - session_start
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    seconds = int(elapsed % 60)

    # 格式化工具分布
    top_tools = sorted(tool_breakdown.items(), key=lambda x: x[1], reverse=True)
    tool_lines = "\n".join(
        f"    • {name}: {count} 次" for name, count in top_tools[:10]
    )
    if len(top_tools) > 10:
        tool_lines += f"\n    … 及其他 {len(top_tools) - 10} 种工具"

    return f"""📊 **会话统计报告**

⏱️  会话运行时长：{hours}时{minutes}分{seconds}秒
📋  任务总数：{task_count} 个（✅ {done_count} 完成  ❌ {error_count} 失败）
🔧  工具调用总次数：{tool_calls_total} 次
⏳  工具累计耗时：{total_tool_time:.2f} 秒

📈 工具使用分布：
{tool_lines or '    暂无数据'}"""


def _handle_generate_gitignore(project_type: str, context) -> str:
    """为项目生成 .gitignore 文件。"""
    types = [t.strip().lower() for t in project_type.split(",")]
    unknown = [t for t in types if t not in _GITIGNORE_TEMPLATES]
    valid = [t for t in types if t in _GITIGNORE_TEMPLATES]

    if not valid:
        known = ", ".join(sorted(_GITIGNORE_TEMPLATES.keys()))
        return (
            f"未知的项目类型：{', '.join(unknown)}。"
            f"支持的类型：{known}"
        )

    # 合并所选类型的模板
    header = "# Generated by Vibe Coding Agent (session_stats plugin)\n"
    header += f"# Types: {', '.join(valid)}\n"
    header += f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    new_entries = header
    for t in valid:
        new_entries += _GITIGNORE_TEMPLATES[t].strip() + "\n\n"

    gitignore_path = os.path.join(context.project_root, ".gitignore")

    # 如果已存在，则追加缺失条目
    if os.path.exists(gitignore_path):
        with open(gitignore_path, "r", encoding="utf-8") as f:
            existing = f.read()

        # 解析已有的 glob 条目
        existing_lines = set(
            line.strip() for line in existing.split("\n")
            if line.strip() and not line.strip().startswith("#")
        )
        new_lines = [
            line for line in new_entries.split("\n")
            if line.strip() and not line.strip().startswith("#")
        ]
        missing = [l for l in new_lines if l not in existing_lines]

        if not missing:
            return f".gitignore 已包含所有 {', '.join(valid)} 类型的条目，无需更新。"

        with open(gitignore_path, "a", encoding="utf-8") as f:
            f.write("\n# Appended by session_stats plugin\n")
            f.write(f"# Types: {', '.join(valid)}\n")
            for line in missing:
                f.write(line + "\n")

        context.logger.info(
            f"Appended {len(missing)} entries to .gitignore "
            f"for types: {', '.join(valid)}"
        )
        return (
            f"✅ 已追加 {len(missing)} 条规则到 .gitignore"
            f"（{', '.join(valid)}）。"
            + (f" 未知类型：{', '.join(unknown)}。" if unknown else "")
        )

    # 新建 .gitignore
    with open(gitignore_path, "w", encoding="utf-8") as f:
        f.write(new_entries.strip() + "\n")

    context.logger.info(
        f"Created .gitignore with {len(new_entries.splitlines())} lines "
        f"for types: {', '.join(valid)}"
    )
    msg = f"✅ 已创建 .gitignore（类型：{', '.join(valid)}）"
    if unknown:
        msg += f" | ⚠️ 未知类型已跳过：{', '.join(unknown)}"
    return msg


# ── 4. 钩子：L1 生命周期 ────────────────────────────────────────

def on_agent_init(context):
    """会话开始时初始化统计计数器。"""
    context.storage["session_start"] = time.time()
    context.storage["task_count"] = 0
    context.storage["done_count"] = 0
    context.storage["error_count"] = 0
    context.storage["tool_calls_total"] = 0
    context.storage["tool_breakdown"] = {}
    context.storage["total_tool_time"] = 0.0
    context.storage["_tool_start_times"] = {}

    context.logger.info(
        f"Session Stats plugin loaded. "
        f"Project: {os.path.basename(context.project_root)}"
    )


def on_agent_shutdown(context):
    """会话结束时输出总结。"""
    s = context.storage
    elapsed = time.time() - s.get("session_start", time.time())
    context.logger.info(
        f"Session ended. "
        f"{s.get('task_count', 0)} tasks, "
        f"{s.get('tool_calls_total', 0)} tool calls, "
        f"{elapsed:.0f}s elapsed."
    )


# ── 5. 钩子：L2 任务生命周期 ────────────────────────────────────

def on_task_start(task_text: str, context):
    """新任务开始，递增计数器。"""
    s = context.storage
    s["task_count"] = s.get("task_count", 0) + 1
    context.logger.info(f"Task #{s['task_count']}: {task_text[:80]}...")


def on_task_done(summary: str, final_reply: str, context):
    """任务成功完成。"""
    s = context.storage
    s["done_count"] = s.get("done_count", 0) + 1
    context.logger.info(f"Task #{s['task_count']} completed: {summary[:80]}")


def on_task_error(error_msg: str, context):
    """任务因异常失败。"""
    s = context.storage
    s["error_count"] = s.get("error_count", 0) + 1
    context.logger.error(f"Task #{s['task_count']} error: {error_msg[:120]}")


# ── 6. 钩子：L3 工具调用拦截 ────────────────────────────────────

def before_tool_call(tool_name: str, args: dict, context):
    """每个工具调用前：记录时间和调用次数。"""
    s = context.storage

    # 计数
    s["tool_calls_total"] = s.get("tool_calls_total", 0) + 1

    # 按工具名分桶
    breakdown = s.get("tool_breakdown", {})
    breakdown[tool_name] = breakdown.get(tool_name, 0) + 1
    s["tool_breakdown"] = breakdown

    # 记录开始时间（用于计时）
    start_times = s.get("_tool_start_times", {})
    start_times[tool_name] = time.time()
    s["_tool_start_times"] = start_times

    return args  # 放行 — 不拦截


def after_tool_call(tool_name: str, args: dict, result: str, context):
    """每个工具调用后：计算耗时。"""
    s = context.storage
    start_times = s.get("_tool_start_times", {})
    start = start_times.pop(tool_name, None)

    if start is not None:
        duration = time.time() - start
        s["total_tool_time"] = s.get("total_tool_time", 0.0) + duration

    return result  # 不修改返回值

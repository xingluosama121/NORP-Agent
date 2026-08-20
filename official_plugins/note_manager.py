# ──────────────────────────────────────────────────────────────
# Plugin: Note Manager
# Publisher: xingluosama
# Version: 1.0.0
# Description: 笔记管理器：保存/列出/搜索笔记，支持标签系统、
#   自动任务笔记。
# ──────────────────────────────────────────────────────────────

PLUGIN_NAME = "Note Manager"
PLUGIN_PUBLISHER = "xingluosama"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "笔记管理器：保存/列出/搜索笔记，支持标签和自动任务笔记。"

import json
import os
import re
from datetime import datetime

# ── 1. 工具注册 ────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": (
                "保存一条笔记到本地数据库。支持标签和分类。"
                "笔记会自动附带时间戳。重复标题的笔记会追加内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "笔记标题（用于检索和列表展示）"
                    },
                    "content": {
                        "type": "string",
                        "description": "笔记正文内容"
                    },
                    "tags": {
                        "type": "string",
                        "description": "标签，用逗号分隔，如 'bug,frontend,urgent'"
                    }
                },
                "required": ["title", "content"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_notes",
            "description": (
                "列出所有已保存的笔记。可按标签过滤。"
                "返回笔记列表，包含标题、创建时间、标签和内容预览。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {
                        "type": "string",
                        "description": "按标签过滤（可选），如 'bug' 只显示含 bug 标签的笔记"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数上限，默认 20",
                        "default": 20
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
            "name": "search_notes",
            "description": (
                "全文搜索笔记。在标题和正文中搜索关键词。"
                "支持多个关键词（空格分隔）的 AND 搜索。"
                "返回匹配的笔记列表，高亮显示匹配内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，多个词用空格分隔（AND 逻辑）"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数上限，默认 10",
                        "default": 10
                    }
                },
                "required": ["query"],
                "additionalProperties": False
            }
        }
    }
]

# ── 2. 笔记存储层 ──────────────────────────────────────────────

_NOTES_DIR_NAME = "plugin_notes"
_NOTES_FILE_NAME = "notes.json"


def _get_notes_path(context) -> str:
    """获取笔记文件路径（在 app_dir 下）。"""
    notes_dir = os.path.join(context.app_dir, _NOTES_DIR_NAME)
    os.makedirs(notes_dir, exist_ok=True)
    return os.path.join(notes_dir, _NOTES_FILE_NAME)


def _load_notes(context) -> list:
    """加载所有笔记。"""
    path = _get_notes_path(context)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return data
    except Exception:
        return []


def _save_notes(context, notes: list):
    """保存所有笔记到磁盘。"""
    path = _get_notes_path(context)
    # 只保留最近 500 条
    if len(notes) > 500:
        trimmed = len(notes) - 500
        context.logger.warn(
            f"Notes database reached {len(notes)} entries – "
            f"trimming oldest {trimmed} to stay at 500 limit"
        )
        notes = notes[-500:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(notes, f, indent=2, ensure_ascii=False)


# ── 3. 工具实现 ────────────────────────────────────────────────

def _handle_save_note(args: dict, context) -> str:
    """保存笔记。"""
    title = args.get("title", "").strip()
    content = args.get("content", "").strip()
    tags_str = args.get("tags", "").strip()

    if not title or not content:
        return "❌ 标题和内容不能为空。"

    # 解析标签
    tags = [t.strip().lower() for t in tags_str.split(",") if t.strip()] if tags_str else []

    notes = _load_notes(context)

    # 检查是否有同标题笔记（追加模式）
    for note in notes:
        if note["title"].lower() == title.lower():
            # 追加内容
            note["content"] += f"\n\n--- 更新于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n{content}"
            note["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 合并标签
            existing_tags = set(note.get("tags", []))
            existing_tags.update(tags)
            note["tags"] = sorted(existing_tags)
            _save_notes(context, notes)
            context.logger.info(f"Note updated: {title}")
            return (
                f"✅ 已更新笔记 **「{title}」**\n"
                f"  更新时间: {note['updated_at']}\n"
                f"  标签: {', '.join(note['tags']) if note['tags'] else '(无)'}"
            )

    # 新建笔记
    note = {
        "title": title,
        "content": content,
        "tags": sorted(tags),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    notes.append(note)
    _save_notes(context, notes)

    context.logger.info(f"Note saved: {title}")
    return (
        f"✅ 已保存笔记 **「{title}」**\n"
        f"  创建时间: {note['created_at']}\n"
        f"  标签: {', '.join(tags) if tags else '(无)'}\n"
        f"  内容长度: {len(content)} 字符\n"
        f"  总笔记数: {len(notes)}"
    )


def _handle_list_notes(args: dict, context) -> str:
    """列出笔记。"""
    tag_filter = args.get("tag", "").strip().lower()
    limit = min(args.get("limit", 20), 50)

    notes = _load_notes(context)

    if tag_filter:
        notes = [n for n in notes if tag_filter in [t.lower() for t in n.get("tags", [])]]

    if not notes:
        msg = "📝 暂无笔记。"
        if tag_filter:
            msg = f"📝 没有标签为「{tag_filter}」的笔记。"
        return msg

    # 按更新时间倒序
    notes = sorted(notes, key=lambda n: n.get("updated_at", ""), reverse=True)
    notes = notes[:limit]

    lines = [f"📝 **笔记列表** ({len(notes)} 条)", ""]

    for i, note in enumerate(notes, 1):
        title = note.get("title", "无标题")
        tags = note.get("tags", [])
        created = note.get("created_at", "?")
        content_preview = note.get("content", "")[:80].replace("\n", " ")

        tag_str = f" `{'` `'.join(tags)}`" if tags else ""
        lines.append(
            f"**{i}.** {title}{tag_str}\n"
            f"    📅 {created} | {content_preview}..."
        )

    if len(notes) >= limit:
        lines.append(f"\n  *(仅显示最近 {limit} 条)*")

    return "\n".join(lines)


def _handle_search_notes(args: dict, context) -> str:
    """搜索笔记。"""
    query = args.get("query", "").strip()
    limit = min(args.get("limit", 10), 30)

    if not query:
        return "❌ 搜索关键词不能为空。"

    keywords = [kw.lower() for kw in query.split() if kw]

    notes = _load_notes(context)
    results = []

    for note in notes:
        title = note.get("title", "")
        content = note.get("content", "")
        text = f"{title} {content}".lower()

        # AND 逻辑：所有关键词都必须匹配
        if all(kw in text for kw in keywords):
            # 高亮匹配片段
            snippet = _highlight_match(content, keywords, title, 120)
            results.append({
                **note,
                "snippet": snippet,
            })

    if not results:
        return f"🔍 未找到与「{query}」匹配的笔记。"

    results = sorted(results, key=lambda n: n.get("updated_at", ""), reverse=True)
    results = results[:limit]

    lines = [f"🔍 **搜索结果**: `{query}` ({len(results)} 条)", ""]

    for i, r in enumerate(results, 1):
        tags = r.get("tags", [])
        tag_str = f" `{'` `'.join(tags)}`" if tags else ""
        lines.append(
            f"**{i}.** {r['title']}{tag_str}\n"
            f"    📅 {r.get('created_at', '?')}\n"
            f"    {r['snippet']}"
        )

    return "\n".join(lines)


def _highlight_match(content: str, keywords: list, title: str, max_len: int) -> str:
    """提取包含关键词的摘要片段。"""
    content_lower = content.lower()

    # 找到第一个匹配关键词的位置
    best_pos = 0
    for kw in keywords:
        pos = content_lower.find(kw)
        if pos >= 0:
            if best_pos == 0 or pos < best_pos:
                best_pos = pos

    start = max(0, best_pos - max_len // 2)
    end = min(len(content), start + max_len)

    snippet = content[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."

    return snippet


# ── 4. 工具分发 ────────────────────────────────────────────────

def execute(tool_name: str, args: dict, context) -> str:
    if tool_name == "save_note":
        return _handle_save_note(args, context)
    if tool_name == "list_notes":
        return _handle_list_notes(args, context)
    if tool_name == "search_notes":
        return _handle_search_notes(args, context)
    return f"Unknown tool: {tool_name}"


# ── 5. 钩子 ────────────────────────────────────────────────────

def on_agent_init(context):
    """初始化笔记管理器。"""
    notes = _load_notes(context)
    context.storage["notes_count"] = len(notes)
    context.storage["notes_added_this_session"] = 0
    context.logger.info(
        f"Note Manager loaded — {len(notes)} existing notes found 📝"
    )


def on_agent_shutdown(context):
    """会话结束时统计。"""
    added = context.storage.get("notes_added_this_session", 0)
    total = len(_load_notes(context))
    if added > 0:
        context.logger.info(
            f"Note Manager: {added} note(s) added this session, "
            f"{total} total notes."
        )


def on_task_start(task_text: str, context):
    """检测笔记相关任务。"""
    keywords = ["记笔记", "note", "笔记", "记录一下", "记下来",
                "save note", "备忘录"]
    if any(kw in task_text.lower() for kw in keywords):
        context.logger.info(f"Note-taking task detected: {task_text[:80]}")


def on_task_done(summary: str, final_reply: str, context):
    """任务完成时自动记录摘要笔记（可选）。

    Auto-saves are enqueued to a background thread so the hook chain
    is never blocked by disk I/O.
    """
    auto_note_keywords = ["修复", "完成", "实现", "添加", "创建",
                          "fix", "done", "implement", "add", "create"]
    should_auto_note = any(
        kw in (summary + final_reply).lower()
        for kw in auto_note_keywords
    )

    if should_auto_note and len(summary) > 10:
        note_title = f"任务完成: {summary[:50].strip()}"
        note_content = (
            f"任务摘要: {summary}\n\n"
            f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        # Offload the save to a daemon thread so we don't block the hook chain
        import threading
        def _save_auto_note():
            try:
                _handle_save_note({
                    "title": note_title,
                    "content": note_content,
                    "tags": "auto,task-complete"
                }, context)
                s = context.storage
                s["notes_added_this_session"] = s.get("notes_added_this_session", 0) + 1
            except Exception:
                pass

        t = threading.Thread(target=_save_auto_note, daemon=True)
        t.start()


def on_task_error(error_msg: str, context):
    """任务错误时自动记录。"""
    import threading
    def _save_error_note():
        try:
            _handle_save_note({
                "title": f"任务错误: {error_msg[:50].strip()}",
                "content": (
                    f"错误信息: {error_msg}\n\n"
                    f"发生时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                ),
                "tags": "auto,task-error"
            }, context)
        except Exception:
            pass

    t = threading.Thread(target=_save_error_note, daemon=True)
    t.start()

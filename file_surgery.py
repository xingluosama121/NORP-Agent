# ──────────────────────────────────────────────────────────────
# Native Tool: File Surgery (分子手术刀)
# 从 official_plugins/file_surgeon.py 迁移而来
# ──────────────────────────────────────────────────────────────

import os
import re
import shutil
import tempfile
import time
from datetime import datetime

_BUFFER_SIZE = 16 * 1024 * 1024       # 16MB 读写缓冲
_MAX_FILE_SIZE = 1 * 1024 * 1024 * 1024  # 1GB 上限


def _format_size(size: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def _line_matches(
    line_num: int,
    line_text: str,
    target_line: int | None,
    target_content: str | None,
    regex: re.Pattern | None,
) -> bool:
    if target_line is not None and target_content is not None:
        if line_num != target_line:
            return False
        if regex:
            return bool(regex.search(line_text))
        return target_content in line_text

    if target_line is not None:
        return line_num == target_line

    if target_content is not None:
        if regex:
            return bool(regex.search(line_text))
        return target_content in line_text

    return False


def _build_success_report(
    file_path: str, file_size: int, total_lines: int,
    matched_count: int, mode: str, elapsed: float, backup: bool,
) -> str:
    mode_labels = {
        "replace": "替换", "replace_all": "全部替换",
        "insert_before": "前插", "insert_after": "后插",
        "delete": "删除",
    }
    icons = {
        "replace": "✏️", "replace_all": "✏️",
        "insert_before": "⬆️", "insert_after": "⬇️",
        "delete": "🗑️",
    }

    report = [
        f"{icons.get(mode, '🔧')} **手术完成** — `{os.path.basename(file_path)}`",
        f"",
        f"📊 **操作摘要**：",
        f"  • 操作模式：{mode_labels.get(mode, mode)}",
        f"  • 匹配行数：{matched_count}",
        f"  • 文件行数：{total_lines:,}",
        f"  • 文件大小：{_format_size(file_size)}",
        f"  • 耗时：{elapsed:.2f} 秒",
        f"  • 吞吐量：{_format_size(file_size / max(elapsed, 0.001))}/s",
    ]

    if backup:
        report.append(f"  • 备份：已保存为 `{file_path}.bak`")

    report.append("")
    report.append("✅ 文件已成功修改。")
    return "\n".join(report)


def _dry_run_preview(
    file_path: str, file_size: int, line_number: int | None,
    old_content: str | None, search_regex: re.Pattern | None,
    new_content: str | None, mode: str, count,
    context_lines: int, encoding: str,
) -> str:
    matched_lines = []
    total_lines = 0

    with open(file_path, "r", encoding=encoding, errors="replace",
              buffering=_BUFFER_SIZE) as f:
        for i, line in enumerate(f, 1):
            total_lines = i
            if _line_matches(i, line.rstrip("\n\r"), line_number,
                             old_content, search_regex):
                matched_lines.append((i, line.rstrip("\n\r")))
            if count != float("inf") and len(matched_lines) >= count:
                break

    if not matched_lines:
        return (
            f"🔍 **dry_run 预览** — `{os.path.basename(file_path)}`\n\n"
            f"⚠️ 未找到匹配的行。请检查：\n"
            f"  • 行号是否正确（文件共 {total_lines:,} 行）\n"
            f"  • 搜索内容是否拼写正确\n"
            f"  • 是否忘记启用 `use_regex`（当前 {'已启用' if search_regex else '未启用'}）\n"
            f"  • 使用 `surgical_scan` 工具扫描文件内容辅助定位"
        )

    need_lines = set()
    for ln, _ in matched_lines:
        for cl in range(max(1, ln - context_lines), ln + context_lines + 1):
            need_lines.add(cl)

    context_data = {}
    with open(file_path, "r", encoding=encoding, errors="replace",
              buffering=_BUFFER_SIZE) as f:
        for i, line in enumerate(f, 1):
            if i in need_lines:
                context_data[i] = line.rstrip("\n\r")

    mode_icons = {
        "replace": "✏️", "replace_all": "✏️",
        "insert_before": "⬆️", "insert_after": "⬇️",
        "delete": "🗑️",
    }
    action_texts = {
        "replace": "替换为", "replace_all": "替换为",
        "insert_before": "在此行**之前**插入", "insert_after": "在此行**之后**插入",
        "delete": "**删除此行**",
    }

    report = [
        f"🔍 **dry_run 预览** — `{os.path.basename(file_path)}`",
        f"",
        f"📁 **文件信息**：",
        f"  • 路径：`{file_path}`",
        f"  • 大小：{_format_size(file_size)}",
        f"  • 编码：{encoding}",
        f"",
        f"🎯 **匹配结果**：找到 {len(matched_lines)} 处匹配，模式=`{mode}`",
        f"",
    ]

    for ln, text in matched_lines:
        icon = mode_icons.get(mode, "🔧")
        action = action_texts.get(mode, "操作")

        report.append(f"--- **目标行 L{ln}** ---")
        report.append(f"  📍 原始内容：`{text[:200]}`")

        if mode == "delete":
            report.append(f"  {icon} 操作：{action}")
        else:
            preview = new_content.replace("\n", "\\n")[:200] if new_content else ""
            report.append(f"  {icon} 操作：{action} `{preview}`")

        ctx_start = max(1, ln - context_lines)
        ctx_end = ln + context_lines
        report.append(f"  📄 上下文 (L{ctx_start}-L{ctx_end})：")
        report.append("  ```")
        for cl in range(ctx_start, ctx_end + 1):
            if cl in context_data:
                marker = ">>>" if cl == ln else "   "
                truncated = context_data[cl][:300]
                report.append(f"  {marker} L{cl:>6}: {truncated}")
        report.append("  ```")
        report.append("")

    report.append(
        "💡 **提示**：确认无误后，使用相同参数并将 `dry_run=false` 执行实际操作。"
    )
    return "\n".join(report)


def _execute_surgery(
    file_path: str, file_size: int, line_number: int | None,
    old_content: str | None, search_regex: re.Pattern | None,
    new_content: str | None, mode: str, count,
    backup: bool, encoding: str,
) -> str:
    start_time = time.time()

    if backup:
        backup_path = file_path + ".bak"
        try:
            shutil.copy2(file_path, backup_path)
        except Exception as e:
            return f"❌ 备份失败：{e}"

    dir_name = os.path.dirname(file_path) or "."
    fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix=".surgical_")
    os.close(fd)

    try:
        matched_count = 0
        total_lines = 0

        with open(file_path, "r", encoding=encoding, errors="replace",
                  buffering=_BUFFER_SIZE) as fin, \
             open(temp_path, "w", encoding=encoding, errors="replace",
                  buffering=_BUFFER_SIZE) as fout:

            for i, line in enumerate(fin, 1):
                total_lines = i
                raw_line = line.rstrip("\n\r")

                is_match = _line_matches(i, raw_line, line_number,
                                         old_content, search_regex)

                if is_match and matched_count < count:
                    matched_count += 1

                    if mode == "delete":
                        continue

                    elif mode == "insert_before":
                        if new_content:
                            fout.write(new_content)
                            if not new_content.endswith("\n"):
                                fout.write("\n")
                        fout.write(line)

                    elif mode == "insert_after":
                        fout.write(line)
                        if new_content:
                            fout.write(new_content)
                            if not new_content.endswith("\n"):
                                fout.write("\n")

                    elif mode in ("replace", "replace_all"):
                        if new_content is not None:
                            fout.write(new_content)
                            if not new_content.endswith("\n"):
                                fout.write("\n")
                else:
                    fout.write(line)

        os.replace(temp_path, file_path)
        elapsed = time.time() - start_time

        return _build_success_report(
            file_path, file_size, total_lines, matched_count,
            mode, elapsed, backup
        )

    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
        return f"❌ **手术失败**：{e}"


def perform_surgery(
    file_path: str, line_number: int | None,
    old_content: str | None, new_content: str | None,
    mode: str, use_regex: bool, count: int,
    dry_run: bool, context_lines: int, backup: bool, encoding: str,
) -> str:
    if not line_number and not old_content:
        return (
            "❌ **参数错误**：必须指定 `line_number` 或 `old_content` "
            "中的至少一个来定位目标行。"
        )

    if mode == "delete":
        new_content = None
    elif new_content is None and mode != "delete":
        return f"❌ **参数错误**：`{mode}` 模式需要提供 `new_content`。"

    if count == -1:
        count = float("inf")

    if not os.path.isfile(file_path):
        return f"❌ 文件不存在：`{file_path}`"

    file_size = os.path.getsize(file_path)
    if file_size > _MAX_FILE_SIZE:
        return (
            f"❌ **文件过大**：{_format_size(file_size)} 超过了 "
            f"手术刀最大支持 {_format_size(_MAX_FILE_SIZE)} 的限制。"
        )

    search_regex = None
    if old_content and use_regex:
        try:
            search_regex = re.compile(old_content)
        except re.error as e:
            return f"❌ **正则表达式错误**：`{old_content}` — {e}"

    if dry_run:
        return _dry_run_preview(
            file_path, file_size, line_number, old_content,
            search_regex, new_content, mode, count, context_lines, encoding
        )

    return _execute_surgery(
        file_path, file_size, line_number, old_content,
        search_regex, new_content, mode, count, backup, encoding
    )


def perform_scan(
    file_path: str, pattern: str, use_regex: bool,
    line_start: int | None, line_end: int | None,
    context_lines: int, max_matches: int, encoding: str,
) -> str:
    if not os.path.isfile(file_path):
        return f"❌ 文件不存在：`{file_path}`"

    file_size = os.path.getsize(file_path)

    regex = None
    if use_regex:
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"❌ **正则表达式错误**：`{pattern}` — {e}"

    max_matches = min(max_matches, 200)

    matched_lines = []
    total_lines = 0

    with open(file_path, "r", encoding=encoding, errors="replace",
              buffering=_BUFFER_SIZE) as f:
        for i, line in enumerate(f, 1):
            total_lines = i
            if line_start and i < line_start:
                continue
            if line_end and i > line_end:
                break

            raw = line.rstrip("\n\r")
            if regex:
                if regex.search(raw):
                    matched_lines.append((i, raw))
            else:
                if pattern in raw:
                    matched_lines.append((i, raw))

            if len(matched_lines) >= max_matches:
                break

    if not matched_lines:
        return (
            f"🔍 **扫描结果** — `{os.path.basename(file_path)}`\n\n"
            f"未找到匹配 `{pattern[:100]}` 的行。\n"
            f"文件共 {total_lines:,} 行，{_format_size(file_size)}。"
        )

    need_lines = set()
    for ln, _ in matched_lines:
        for cl in range(max(1, ln - context_lines), ln + context_lines + 1):
            need_lines.add(cl)

    context_data = {}
    with open(file_path, "r", encoding=encoding, errors="replace",
              buffering=_BUFFER_SIZE) as f:
        for i, line in enumerate(f, 1):
            if i in need_lines:
                context_data[i] = line.rstrip("\n\r")

    report = [
        f"🔍 **扫描结果** — `{os.path.basename(file_path)}`",
        f"",
        f"📁 文件：{_format_size(file_size)}，{total_lines:,} 行",
        f"🔎 模式：`{pattern[:200]}`（{'正则' if use_regex else '文本匹配'}）",
        f"🎯 匹配：{len(matched_lines)} 处",
        f"",
    ]

    for idx, (ln, text) in enumerate(matched_lines, 1):
        report.append(f"### 匹配 #{idx}  — 行 {ln}")
        report.append("```")
        ctx_start = max(1, ln - context_lines)
        ctx_end = ln + context_lines
        for cl in range(ctx_start, ctx_end + 1):
            if cl in context_data:
                marker = ">>>" if cl == ln else "   "
                truncated = context_data[cl][:300]
                report.append(f"{marker} L{cl:>6}: {truncated}")
        report.append("```")
        report.append("")

    if len(matched_lines) >= max_matches:
        report.append(
            f"⚠️ 仅显示前 {max_matches} 个匹配。"
            f"缩小搜索范围（line_start/line_end）查看更多。"
        )
    report.append(
        "💡 使用 `surgical_replace` 并指定 `line_number=` "
        "或 `old_content=` 来精确修改这些行。"
    )

    return "\n".join(report)

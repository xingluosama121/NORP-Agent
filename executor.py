# Vibe Coding Agent - Tool executor with Docker sandbox
# Copyright (c) 2026 xingluosama

import os
import json
import shutil
import subprocess
import sys
import platform
import concurrent.futures
from datetime import datetime
from pathlib import Path
from typing import Optional

from norp_safe import NorpSafe, get_norp_safe, ThreatLevel
from vision import is_visual_ext, describe_visual_file, VisionNotConfigured
from workspace_index import WorkspaceIndex, get_workspace_index, search_large_file_stream, scan_and_index, _fmt_size as _wi_fmt_size
from file_surgery import perform_surgery, perform_scan
from web_fetcher_native import handle_web_fetch, handle_web_extract_links
from context_index import FTS5Retriever, get_context_index, chunk_text
from agent_shared import robust_decode


class DockerSandbox:
    """Docker container sandbox for isolated command execution."""

    def __init__(
        self,
        project_root: str,
        image: str = "python:3.11-slim",
        network_mode: str = "none",
        mem_limit: str = "512m"
    ):
        self.project_root = os.path.abspath(project_root)
        self.image = image
        self.network_mode = network_mode
        self.mem_limit = mem_limit
        self._container = None
        self._client = None

    def _get_client(self):
        if self._client is None:
            import docker
            self._client = docker.from_env()
        return self._client

    def start(self) -> str:
        client = self._get_client()
        volumes = {self.project_root: {"bind": "/workspace", "mode": "rw"}}
        self._container = client.containers.run(
            self.image,
            command="tail -f /dev/null",
            volumes=volumes,
            network_mode=self.network_mode,
            mem_limit=self.mem_limit,
            detach=True,
            remove=True
        )
        return self._container.id

    def exec(self, cmd: str, timeout: int = 30) -> str:
        if not self._container:
            raise RuntimeError("Sandbox not started")
        exit_code, output = self._container.exec_run(
            cmd,
            workdir="/workspace",
            stdout=True,
            stderr=True
        )
        result = output.decode("utf-8", errors="replace") if output else ""
        if not result.strip():
            result = f"Exit code: {exit_code}"
        return result

    def stop(self):
        if self._container:
            try:
                self._container.stop(timeout=5)
            except Exception:
                pass
            self._container = None

    def is_running(self) -> bool:
        if not self._container:
            return False
        try:
            self._container.reload()
            return self._container.status == "running"
        except Exception:
            return False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    def __del__(self):
        self.stop()


class ToolExecutor:

    def __init__(self, project_root: str, sandbox: Optional[DockerSandbox] = None, app_dir: str = "",
                 allow_full_read_large_files: bool = False,
                 norp_safe: Optional[NorpSafe] = None,
                 task_id: str = ""):
        self.project_root = os.path.abspath(project_root)
        self.sandbox = sandbox
        self.app_dir = app_dir
        self.allow_full_read_large_files = allow_full_read_large_files
        self.task_id = task_id
        if app_dir:
            self.history_path = os.path.join(app_dir, ".agent_history.json")
            os.makedirs(app_dir, exist_ok=True)
        else:
            self.history_path = os.path.join(self.project_root, ".agent_history.json")
        self._use_sandbox = sandbox is not None
        # NORP 安全系统
        self.norp_safe = norp_safe or get_norp_safe(app_dir)

    # 内置工具执行超时（秒），防止大目录搜索永久挂起
    BUILTIN_TOOL_TIMEOUT = 300.0

    def execute(self, tool_name: str, args: dict) -> str:
        handlers = {
            "read_file": self._read_file,
            "write_file": self._write_file,
            "replace_in_file": self._replace_in_file,
            "list_dir": self._list_dir,
            "search_in_files": self._search_in_files,
            "delete_file": self._delete_file,
            "exec_cmd": self._exec_cmd,
            "init_project": self._init_project,
            "install_dependency": self._install_dependency,
            "git_commit": self._git_commit,
            "task_done": self._task_done,
            "web_search": self._web_search,
            "open_file": self._open_file,
            "read_clipboard": self._read_clipboard,
            "write_clipboard": self._write_clipboard,
            "unpack_archive": self._unpack_archive,
            "copy_file": self._copy_file,
            "move_file": self._move_file,
            "index_workspace": self._index_workspace,
            "search_files": self._search_files_native,
            "find_files": self._find_files_native,
            "search_large_file": self._search_large_file,
            "workspace_index_status": self._workspace_index_status,
            "clear_workspace_index": self._clear_workspace_index,
            "surgical_replace": self._surgical_replace,
            "surgical_scan": self._surgical_scan,
            "index_context": self._index_context,
            "search_context": self._search_context,
            "clear_index": self._clear_index,
            "index_stats": self._index_stats,
            "web_fetch": self._web_fetch,
            "web_extract_links": self._web_extract_links,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return f"Error: unknown tool '{tool_name}'"

        # ★ 大目录搜索等阻塞操作：通过线程池执行 + 超时保护
        blocking_tools = {"search_in_files", "read_file", "list_dir"}
        if tool_name in blocking_tools:
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(handler, args)
                    return future.result(timeout=self.BUILTIN_TOOL_TIMEOUT)
            except concurrent.futures.TimeoutError:
                return (
                    f"Error: built-in tool '{tool_name}' timed out "
                    f"after {self.BUILTIN_TOOL_TIMEOUT:.0f}s"
                )
            except Exception as e:
                return f"Tool execution failed: {str(e)}"

        try:
            return handler(args)
        except Exception as e:
            return f"Tool execution failed: {str(e)}"

    def _safe_path(self, path: str) -> str:
        """验证并规范化路径，确保在工作区范围内（结构化，防前缀误判）。"""
        # NORP 安全系统：路径越界语义检查
        result = self.norp_safe.check_path(
            path, workspace_root=self.project_root, task_id=self.task_id)
        if result.blocked:
            raise ValueError(
                f"NORP安全系统拦截: {result.reason}（威胁等级: {result.threat_level.value}）")

        # 结构化解析：resolve 解析符号链接与 ..，组件级比较避免前缀误判
        base = Path(self.project_root).resolve()
        try:
            expanded = os.path.expandvars(os.path.expanduser(str(path)))
        except Exception:
            expanded = str(path)

        if os.path.isabs(expanded):
            candidate = Path(expanded)
        else:
            candidate = base / expanded

        try:
            resolved = candidate.resolve(strict=False)
        except Exception:
            resolved = Path(os.path.abspath(str(candidate)))

        try:
            resolved.relative_to(base)
        except ValueError:
            raise ValueError(
                f"NORP安全系统拦截: 路径越界 - {path}（工作区={self.project_root}）")

        return str(resolved)

    def _get_vision_config(self) -> dict:
        """读取视觉相关配置（用于 read_file 视觉分支）。"""
        if not self.app_dir:
            return {}
        try:
            from config import ConfigManager
            return ConfigManager(self.app_dir).load()
        except Exception:
            return {}

    def _read_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])
        start_line = args.get("start_line")
        end_line = args.get("end_line")

        # ── 视觉文件（图片/视频）：读二进制 → 视觉描述 ──
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if is_visual_ext(ext):
            try:
                return describe_visual_file(path, self._get_vision_config())
            except VisionNotConfigured as e:
                return f"[视觉未配置] {e}"
            except Exception as e:
                return f"[视觉处理失败] {e}"

        # ── 全量读取大文件开关 ──
        if start_line is None and end_line is None:
            file_size = os.path.getsize(path)
            MAX_FULL_READ_SIZE = 100 * 1024  # 100KB
            if file_size > MAX_FULL_READ_SIZE and not self.allow_full_read_large_files:
                size_kb = file_size / 1024
                return (
                    f"文件过大，仅能部分读取（{size_kb:.0f} KB > 100 KB）。\n"
                    f"请使用 start_line / end_line 参数按行范围读取需要的代码片段。\n"
                    f"也可先用 list_dir 查看文件大小，或用 search_large_file / "
                    f"search_files / surgical_scan 定位后再读。"
                )
        with open(path, "r", encoding="utf-8") as f:
            if start_line is None and end_line is None:
                return f.read()
            lines = f.readlines()
            total = len(lines)
            if start_line is None:
                start_line = 1
            if end_line is None:
                end_line = total
            start_line = max(1, start_line)
            end_line = min(total, end_line)
            if start_line > end_line:
                return f"Error: start_line ({start_line}) > end_line ({end_line})"
            result = "".join(lines[start_line - 1:end_line])
            header = f"[Lines {start_line}-{end_line} of {total}]\n"
            return header + result

    def _write_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(args["content"])
        return f"File written: {path}"

    def _replace_in_file(self, args: dict) -> str:
        """替换文件中的指定文本片段。

        old_str 必须在文件中精确匹配唯一一处。
        若匹配 0 处则报错，匹配多处则提示需要更多上下文。
        """
        path = self._safe_path(args["path"])
        old_str = args["old_str"]
        new_str = args["new_str"]

        if not os.path.exists(path):
            return f"Error: file not found: {path}"

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        if old_str == "":
            return "Error: old_str cannot be empty"

        count = content.count(old_str)
        if count == 0:
            old_first_line = old_str.split('\n')[0].strip()
            if old_first_line:
                suggestions = []
                for lineno, line in enumerate(content.split('\n'), 1):
                    if old_first_line[:20] in line:
                        suggestions.append(f"  Line {lineno}: {line.strip()[:80]}")
                if suggestions:
                    hint = "\n".join(suggestions[:5])
                    return f"Error: old_str not found in file. Similar lines:\n{hint}\n\nTip: use read_file to verify the exact content."
            return f"Error: old_str not found in file. The text must match exactly (including whitespace). Use read_file to verify."

        if count > 1:
            return (
                f"Error: old_str matches {count} locations in the file. "
                f"Please include more surrounding context to make it unique. "
                f"Use read_file to see the file and select a larger unique snippet."
            )

        new_content = content.replace(old_str, new_str, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return f"File modified: {path} (1 replacement)"

    def _list_dir(self, args: dict) -> str:
        path = self._safe_path(args.get("path", "."))
        if not os.path.isdir(path):
            return f"Not a directory: {path}"
        items = os.listdir(path)
        if not items:
            return "(empty)"
        dirs = [d + "/" for d in items if os.path.isdir(os.path.join(path, d))]
        files = [f for f in items if not os.path.isdir(os.path.join(path, f))]
        return "\n".join(sorted(dirs) + sorted(files))

    def _search_in_files(self, args: dict) -> str:
        pattern = args["pattern"]
        root = self._safe_path(args.get("path", "."))
        matches = []
        if os.path.isfile(root):
            targets = [root]
        else:
            targets = []
            for dirpath, _, filenames in os.walk(root):
                if any(part.startswith(".") or part in ("node_modules", "__pycache__", ".git")
                       for part in Path(dirpath).relative_to(root).parts):
                    continue
                for fn in filenames:
                    if not fn.startswith("."):
                        targets.append(os.path.join(dirpath, fn))
        for filepath in targets:
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, 1):
                        if pattern in line:
                            rel = os.path.relpath(filepath, self.project_root)
                            matches.append(f"{rel}:{lineno}: {line.strip()[:120]}")
            except Exception:
                continue
            if len(matches) >= 50:
                matches.append("... (truncated, max 50 results)")
                break
        return "\n".join(matches) if matches else "No matches found."

    def _delete_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])
        if os.path.isdir(path):
            shutil.rmtree(path)
            return f"Directory deleted: {path}"
        elif os.path.isfile(path):
            os.remove(path)
            return f"File deleted: {path}"
        else:
            return f"Path not found: {path}"

    def _copy_file(self, args: dict) -> str:
        """复制文件或目录。"""
        source = self._safe_path(args["source"])
        destination = self._safe_path(args["destination"])

        if not os.path.exists(source):
            return f"Error: source not found: {source}"

        # 如果目标已存在且是目录，复制到目录内
        if os.path.isdir(destination):
            dest = os.path.join(destination, os.path.basename(source))
        else:
            dest = destination

        # 检查目标是否已存在
        if os.path.exists(dest):
            return f"Error: destination already exists: {dest}. Delete it first or choose a different name."

        # 确保目标父目录存在
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        try:
            if os.path.isdir(source):
                shutil.copytree(source, dest)
            else:
                shutil.copy2(source, dest)
            return f"Copied: {source} → {dest}"
        except Exception as e:
            return f"Failed to copy: {str(e)}"

    def _move_file(self, args: dict) -> str:
        """移动文件或目录（也可用于重命名）。"""
        source = self._safe_path(args["source"])
        destination = self._safe_path(args["destination"])

        if not os.path.exists(source):
            return f"Error: source not found: {source}"

        # 如果目标已存在且是目录，移动到目录内
        if os.path.isdir(destination):
            dest = os.path.join(destination, os.path.basename(source))
        else:
            dest = destination

        # 检查目标是否已存在
        if os.path.exists(dest):
            return f"Error: destination already exists: {dest}. Delete it first or choose a different name."

        # 确保目标父目录存在
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        try:
            shutil.move(source, dest)
            return f"Moved: {source} → {dest}"
        except Exception as e:
            return f"Failed to move: {str(e)}"

    def _exec_cmd(self, args: dict) -> str:
        cmd = args["command"]
        timeout = args.get("timeout", 30)

        # NORP 安全系统：全面安全检查（危险命令 + UAC提权）
        result = self.norp_safe.check_command_full(cmd, task_id=self.task_id)
        if result.blocked:
            return f"NORP安全系统拦截: {result.reason}（威胁等级: {result.threat_level.value}）"

        if self._use_sandbox:
            return self._exec_in_sandbox(cmd, timeout)
        return self._exec_local(cmd, timeout)

    def _exec_local(self, cmd: str, timeout: int) -> str:
        # ★ 编码修复：Windows 控制台程序默认以本地代码页（GBK）输出，
        #   text=True 依赖 locale 猜测编码且可能抛 UnicodeDecodeError。
        #   改为捕获原始字节 + robust_decode 多编码兜底，彻底消除乱码。
        result = subprocess.run(
            cmd, shell=True, capture_output=True,
            timeout=timeout, cwd=self.project_root
        )
        output = (robust_decode(result.stdout) + robust_decode(result.stderr)).strip()
        if not output:
            output = f"Command exited with code {result.returncode}"
        return output

    def _exec_in_sandbox(self, cmd: str, timeout: int) -> str:
        return self.sandbox.exec(cmd, timeout=timeout)

    def _open_file(self, args: dict) -> str:
        """用系统默认程序打开文件。"""
        path = self._safe_path(args["path"])
        if not os.path.exists(path):
            return f"Error: file not found: {path}"

        try:
            system = platform.system()
            if system == "Windows":
                os.startfile(path)
            elif system == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            return f"File opened: {path}"
        except Exception as e:
            return f"Failed to open file: {str(e)}"

    def _read_clipboard(self, args: dict) -> str:
        """读取系统剪贴板文本。"""
        system = platform.system()
        try:
            if system == "Windows":
                try:
                    result = subprocess.run(
                        ["powershell", "-Command", "Get-Clipboard"],
                        capture_output=True, text=True, timeout=10,
                        creationflags=0x08000000,
                    )
                    if result.returncode != 0:
                        raise RuntimeError(result.stderr.strip())
                    text = result.stdout
                except FileNotFoundError:
                    import tempfile
                    tmp = os.path.join(tempfile.gettempdir(), "_vibe_paste.txt")
                    subprocess.run(
                        ["powershell", "-Command", f"Get-Clipboard > '{tmp}'"],
                        capture_output=True, timeout=10,
                        creationflags=0x08000000,
                    )
                    try:
                        with open(tmp, "r", encoding="utf-8", errors="replace") as f:
                            text = f.read()
                    finally:
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass
            elif system == "Darwin":
                result = subprocess.run(
                    ["pbpaste"], capture_output=True, text=True, timeout=10
                )
                text = result.stdout
            else:
                for cmd in [["wl-paste"], ["xclip", "-selection", "clipboard", "-o"]]:
                    try:
                        result = subprocess.run(
                            cmd, capture_output=True, text=True, timeout=10
                        )
                        if result.returncode == 0:
                            text = result.stdout
                            break
                    except FileNotFoundError:
                        continue
                else:
                    return "Error: No clipboard tool found. Install xclip (X11) or wl-clipboard (Wayland)."
        except Exception as e:
            return f"Failed to read clipboard: {str(e)}"

        if not text:
            return "(clipboard is empty)"
        return text

    def _write_clipboard(self, args: dict) -> str:
        """将文本写入系统剪贴板。"""
        text = args["text"]
        system = platform.system()
        try:
            if system == "Windows":
                proc = subprocess.run(
                    ["powershell", "-Command", "Set-Clipboard", "-Value", "$input"],
                    input=text, capture_output=True, text=True, timeout=10,
                    creationflags=0x08000000,
                )
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.strip())
            elif system == "Darwin":
                proc = subprocess.run(
                    ["pbcopy"], input=text, capture_output=True, text=True, timeout=10
                )
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.strip())
            else:
                for cmd in [["wl-copy"], ["xclip", "-selection", "clipboard"]]:
                    try:
                        proc = subprocess.run(
                            cmd, input=text, capture_output=True, text=True, timeout=10
                        )
                        if proc.returncode == 0:
                            break
                    except FileNotFoundError:
                        continue
                else:
                    return "Error: No clipboard tool found. Install xclip (X11) or wl-clipboard (Wayland)."
        except Exception as e:
            return f"Failed to write clipboard: {str(e)}"

        preview = text[:80] + "..." if len(text) > 80 else text
        return f"Text copied to clipboard ({len(text)} chars): {preview}"

    def _unpack_archive(self, args: dict) -> str:
        """安全解压缩文件。"""
        path = self._safe_path(args["path"])
        dest_dir = args.get("dest_dir")
        if dest_dir:
            dest_dir = self._safe_path(dest_dir)
        from archive_utils import unpack_archive
        return unpack_archive(path, dest_dir)

    def _web_search(self, args: dict) -> str:
        """使用 DuckDuckGo Instant Answer API 进行联网搜索。"""
        query = args.get("query", "")
        if not query:
            return "Error: search query is required"
        try:
            import requests
            url = "https://api.duckduckgo.com/"
            params = {
                "q": query,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1,
                "t": "vibe_agent"
            }
            resp = requests.get(url, params=params, timeout=15,
                                headers={"User-Agent": "VibeCodingAgent/1.0"})
            resp.raise_for_status()
            data = resp.json()

            results = []
            # Abstract / instant answer
            if data.get("AbstractText"):
                results.append(f"📌 {data['AbstractText']}")
                if data.get("AbstractURL"):
                    results.append(f"   来源: {data['AbstractURL']}")

            # Related topics
            topics = data.get("RelatedTopics", [])
            if topics:
                results.append("")
                count = 0
                for topic in topics:
                    if isinstance(topic, dict) and topic.get("Text"):
                        results.append(f"🔹 {topic['Text']}")
                        if topic.get("FirstURL"):
                            results.append(f"   {topic['FirstURL']}")
                        count += 1
                        if count >= 5:
                            break

            # Answer
            if data.get("Answer"):
                results.insert(0, f"✅ Answer: {data['Answer']}")

            # Definition
            if data.get("Definition"):
                results.insert(0, f"📖 Definition: {data['Definition']}")
                if data.get("DefinitionSource"):
                    results.insert(1, f"   来源: {data['DefinitionSource']}")

            if not results:
                return f"No results found for: {query}"

            return "\n".join(results)

        except ImportError:
            # Fallback to urllib if requests is not available
            import urllib.request
            import urllib.parse
            try:
                qs = urllib.parse.urlencode({
                    "q": query, "format": "json", "no_html": 1,
                    "skip_disambig": 1, "t": "vibe_agent"
                })
                url = f"https://api.duckduckgo.com/?{qs}"
                req = urllib.request.Request(url, headers={"User-Agent": "VibeCodingAgent/1.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                results = []
                if data.get("AbstractText"):
                    results.append(f"📌 {data['AbstractText']}")
                topics = data.get("RelatedTopics", [])
                for topic in topics[:5]:
                    if isinstance(topic, dict) and topic.get("Text"):
                        results.append(f"🔹 {topic['Text']}")
                if data.get("Answer"):
                    results.insert(0, f"✅ Answer: {data['Answer']}")
                if not results:
                    return f"No results found for: {query}"
                return "\n".join(results)
            except Exception as e:
                return f"Web search failed: {str(e)}"
        except Exception as e:
            return f"Web search failed: {str(e)}"

    def _init_project(self, args: dict) -> str:
        ptype = args["type"]
        name = args["name"]
        proj_path = self._safe_path(name)
        os.makedirs(proj_path, exist_ok=True)
        if ptype == "python":
            os.makedirs(os.path.join(proj_path, name), exist_ok=True)
            with open(os.path.join(proj_path, "requirements.txt"), "w") as f:
                f.write("")
            with open(os.path.join(proj_path, name, "__init__.py"), "w") as f:
                f.write("")
            with open(os.path.join(proj_path, name, "main.py"), "w") as f:
                f.write(f"# {name}\n\ndef main():\n    pass\n\nif __name__ == '__main__':\n    main()\n")
        elif ptype == "web":
            os.makedirs(os.path.join(proj_path, "css"), exist_ok=True)
            os.makedirs(os.path.join(proj_path, "js"), exist_ok=True)
            with open(os.path.join(proj_path, "index.html"), "w") as f:
                f.write(f"<!DOCTYPE html>\n<html>\n<head><title>{name}</title></head>\n<body>\n</body>\n</html>\n")
            with open(os.path.join(proj_path, "css", "style.css"), "w") as f:
                f.write("/* styles */\n")
            with open(os.path.join(proj_path, "js", "main.js"), "w") as f:
                f.write("// scripts\n")
        else:
            with open(os.path.join(proj_path, "README.md"), "w") as f:
                f.write(f"# {name}\n")
        return f"Project '{name}' (type: {ptype}) created at {proj_path}"

    def _install_dependency(self, args: dict) -> str:
        package = args["package"]
        manager = args.get("manager", "pip")

        # ★ 安全修复：使用列表参数 + shell=False，防止命令注入
        # 不再用字符串拼接（如 pip install requests && rm -rf /）
        if manager == "pip":
            cmd_list = [sys.executable, "-m", "pip", "install", package]
        elif manager == "npm":
            cmd_list = ["npm", "install", package]
        else:
            return f"Unsupported package manager: {manager}"

        if self._use_sandbox:
            return self._exec_in_sandbox(" ".join(cmd_list), timeout=120)

        result = subprocess.run(
            cmd_list,
            capture_output=True, text=True, timeout=120, cwd=self.project_root,
            shell=False
        )
        return result.stdout.strip() or result.stderr.strip()

    def _git_commit(self, args: dict) -> str:
        message = args["message"]
        subprocess.run(["git", "add", "-A"], cwd=self.project_root, capture_output=True)
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=self.project_root, capture_output=True, text=True
        )
        return result.stdout.strip() or result.stderr.strip()

    def _task_done(self, args: dict) -> str:
        summary = args["summary"]
        code_path = args.get("code_path", ".")
        record = {
            "task": summary,
            "path": code_path,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        history = []
        if os.path.exists(self.history_path):
            try:
                with open(self.history_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                history = []
        history.append(record)
        if len(history) > 20:
            history = history[-20:]
        with open(self.history_path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        return f"Task recorded: {summary}"

    # ═══════════════════════════════════════════════════════════════
    #  原生文件检索工具（从 file_searcher 插件迁移）
    # ═══════════════════════════════════════════════════════════════

    def _get_ws_index(self) -> WorkspaceIndex:
        """获取工作区文件索引实例。"""
        app_dir = os.path.dirname(self.history_path) if self.history_path else ""
        return get_workspace_index(app_dir=app_dir, project_root=self.project_root)

    def _index_workspace(self, args: dict) -> str:
        index = self._get_ws_index()
        directory = (args.get("directory") or "").strip()
        if directory:
            root = os.path.normpath(os.path.join(self.project_root, directory)) if not os.path.isabs(directory) else os.path.normpath(directory)
        else:
            root = os.path.normpath(self.project_root)

        include_patterns = args.get("include_patterns") or []
        exclude_dirs = set(args.get("exclude_dirs") or []) | {
            ".git", "node_modules", "__pycache__", ".venv", "venv",
            "dist", "build", ".idea", ".vscode", "output", "indexes",
        }
        max_file_mb = max(1.0, min(float(args.get("max_file_mb", 256)), 4096))
        force = bool(args.get("force", False))

        result = scan_and_index(
            index, root,
            include_patterns=include_patterns,
            exclude_dirs=exclude_dirs,
            max_file_mb=max_file_mb,
            force=force,
        )

        if "error" in result:
            return f"❌ {result['error']}"

        lines = [
            f"✅ **工作区索引完成**",
            f"  索引根目录: `{result['root']}`",
            f"  扫描文件: {result['scanned']}",
            f"  新索引/更新: **{result['indexed']}** 个文件",
            f"  未变化跳过: {result['unchanged']} 个文件",
            f"  跳过(二进制/超大/过滤): {result['skipped']} 个文件",
            f"  本次索引字符数: {result['total_chars_indexed']:,}",
            f"  耗时: {result['elapsed_seconds']} 秒",
            f"",
            f"💡 现在可用 `search_files(query='...')` 精确检索，或 `find_files(name_pattern='*.py')` 按文件名查找。",
        ]
        return "\n".join(lines)

    def _search_files_native(self, args: dict) -> str:
        query = (args.get("query") or "").strip()
        if not query:
            return "❌ 检索文本不能为空。"

        index = self._get_ws_index()
        stats = index.stats()
        if stats["total_files"] == 0:
            return "📭 工作区索引为空。请先调用 `index_workspace()` 建立索引。"

        path_filter = (args.get("path") or "").strip()
        if path_filter:
            path_filter = os.path.normpath(os.path.join(self.project_root, path_filter)) if not os.path.isabs(path_filter) else os.path.normpath(path_filter)
        file_pattern = (args.get("file_pattern") or "").strip()
        case_sensitive = bool(args.get("case_sensitive", False))
        exact_phrase = bool(args.get("exact_phrase", True))
        top_k = max(1, min(int(args.get("top_k", 10)), 50))
        max_lines_per_file = max(1, min(int(args.get("max_lines_per_file", 5)), 20))
        line_context = max(0, min(int(args.get("line_context", 1)), 5))

        result = index.search(
            query=query, top_k=top_k,
            path_filter=path_filter, file_pattern=file_pattern,
            case_sensitive=case_sensitive, exact_phrase=exact_phrase,
            max_lines_per_file=max_lines_per_file, line_context=line_context,
        )

        matches = result["matches"]
        if not matches:
            return f"🔍 未在索引中找到「**{query}**」\n  索引统计: {stats['total_files']} 文件, {stats['total_characters']:,} 字符"

        mode = "完整短语匹配" if exact_phrase else "关键词 AND 匹配"
        lines = [
            f"🔍 **文件内容精确检索**: `{query}`",
            f"  模式: {mode} | 命中: {result['total_lines_found']} 行 / {len(result['file_hits'])} 个文件\n",
        ]

        current_file = None
        for m in matches:
            if m["file"] != current_file:
                current_file = m["file"]
                try:
                    rel = os.path.relpath(current_file, self.project_root)
                except ValueError:
                    rel = current_file
                lines.append(f"📄 **{rel}**")

            snippet = m["text"]
            if len(snippet) > 200:
                snippet = snippet[:200] + "…"
            for cb in m["context_before"][-line_context:]:
                cb_s = cb.strip()
                if cb_s:
                    lines.append(f"    ┊ {cb_s[:150]}")
            lines.append(f"  **L{m['line']}** │ {snippet}")
            for ca in m["context_after"][:line_context]:
                ca_s = ca.strip()
                if ca_s:
                    lines.append(f"    ┊ {ca_s[:150]}")
            lines.append("")

        lines.append(f"📊 索引: {stats['total_files']} 文件, {stats['total_characters']:,} 字符")
        return "\n".join(lines)

    def _find_files_native(self, args: dict) -> str:
        pattern = (args.get("name_pattern") or "").strip()
        if not pattern:
            return "❌ 文件名模式不能为空。"

        index = self._get_ws_index()
        path_filter = (args.get("path") or "").strip()
        if path_filter:
            path_filter = os.path.normpath(os.path.join(self.project_root, path_filter)) if not os.path.isabs(path_filter) else os.path.normpath(path_filter)
        top_k = max(1, min(int(args.get("top_k", 30)), 100))

        results = index.find_by_name(pattern, path_filter, top_k)
        if not results:
            return f"🔍 未找到文件名匹配「**{pattern}**」的文件。"

        lines = [f"📁 **文件检索**: `{pattern}` — 共 {len(results)} 个文件\n"]
        for r in results:
            size_str = _wi_fmt_size(r["size"])
            status_icon = {"indexed": "✅", "binary": "⚙️", "too_large": "🐘"}.get(r["status"], "❓")
            rel = r["path"]
            try:
                rel = os.path.relpath(rel, self.project_root)
            except ValueError:
                pass
            lines.append(f"  {status_icon} `{rel}` ({size_str})")
        return "\n".join(lines)

    def _search_large_file(self, args: dict) -> str:
        path = (args.get("path") or "").strip()
        query = (args.get("query") or "").strip()
        if not path or not query:
            return "❌ 需要同时提供 path 和 query。"

        full = self._safe_path(path)
        if not os.path.exists(full):
            return f"❌ 文件不存在: `{full}`"

        result = search_large_file_stream(
            full, query,
            regex=bool(args.get("regex", False)),
            case_sensitive=bool(args.get("case_sensitive", False)),
            line_context=max(0, min(int(args.get("line_context", 2)), 10)),
            max_matches=max(1, min(int(args.get("max_matches", 30)), 100)),
            encoding=(args.get("encoding") or "").strip(),
        )

        if "error" in result:
            return f"❌ {result['error']}"

        matches = result["matches"]
        n_hits = len(matches)
        size_str = _wi_fmt_size(result["file_size"])

        lines = [
            f"🔍 **大文件检索完成**",
            f"📄 文件: `{full}` ({size_str})",
            f"✅ 命中 **{n_hits}** 处" + ("（已提前停止）" if result["stopped_early"] else ""),
            f"⏱️ 耗时 {result['elapsed_seconds']} 秒 | 扫描 {result['total_lines']:,} 行\n",
        ]

        for m in matches:
            lines.append(f"  **L{m['line']:,}** │ {m['text']}")
            for cb in m["context_before"][-3:]:
                cb_s = cb.strip()
                if cb_s:
                    lines.append(f"    ┊ {cb_s[:150]}")
            lines.append("")

        return "\n".join(lines)

    def _workspace_index_status(self, args: dict) -> str:
        stats = self._get_ws_index().stats()

        lines = [
            "📊 **工作区文件索引状态**",
            f"",
            f"  索引根目录: `{stats.get('index_root') or '(未扫描)'}`",
            f"  登记文件数: {stats['total_files']}",
            f"  内容索引块数: {stats['total_chunks']:,}",
            f"  索引字符总量: {stats['total_characters']:,}",
            f"  上次扫描: {stats.get('last_scan') or '从未'}",
        ]

        dist = stats["status_distribution"]
        if dist:
            lines.append(f"  **文件状态分布**:")
            for status, cnt in sorted(dist.items(), key=lambda x: x[1], reverse=True):
                icon = {"indexed": "✅", "binary": "⚙️", "too_large": "🐘"}.get(status, "❓")
                label = {"indexed": "已索引内容", "binary": "二进制(仅登记)", "too_large": "超大文件(仅登记)"}.get(status, status)
                lines.append(f"    {icon} {label} — {cnt} 个")
        else:
            lines.append("  *(尚未扫描任何文件)*")

        return "\n".join(lines)

    def _clear_workspace_index(self, args: dict) -> str:
        index = self._get_ws_index()
        path = (args.get("path") or "").strip()

        if path:
            full = os.path.normpath(os.path.join(self.project_root, path)) if not os.path.isabs(path) else os.path.normpath(path)
            if os.path.isdir(full):
                removed = index.remove_by_dir(full)
                return f"✅ 已清除目录 `{full}` 下 **{removed}** 个文件的索引。"
            removed_chunks = index.remove_file(full)
            return f"✅ 已清除文件 `{full}` 的索引（{removed_chunks} 个块）。"

        stats_before = index.stats()
        index.clear_all()
        return f"✅ 已清空全部工作区文件索引（{stats_before['total_files']} 个文件、{stats_before['total_chunks']} 个块）。"

    # ═══════════════════════════════════════════════════════════════
    #  原生文件手术工具（从 file_surgeon 插件迁移）
    # ═══════════════════════════════════════════════════════════════

    def _surgical_replace(self, args: dict) -> str:
        file_path = self._safe_path(args.get("file_path", ""))
        line_number = args.get("line_number")
        old_content = args.get("old_content")
        new_content = args.get("new_content")
        mode = args.get("mode", "replace")
        use_regex = args.get("use_regex", False)
        count = args.get("count", 1)
        dry_run = args.get("dry_run", False)
        context_lines = args.get("context_lines", 2)
        backup = args.get("backup", False)
        encoding = args.get("encoding", "utf-8")

        valid_modes = ("replace", "insert_before", "insert_after", "delete", "replace_all")
        if mode not in valid_modes:
            return f"❌ 不支持的模式 `{mode}`。可选：{', '.join(valid_modes)}"

        return perform_surgery(
            file_path=file_path, line_number=line_number,
            old_content=old_content, new_content=new_content,
            mode=mode, use_regex=use_regex, count=count,
            dry_run=dry_run, context_lines=context_lines,
            backup=backup, encoding=encoding,
        )

    def _surgical_scan(self, args: dict) -> str:
        file_path = self._safe_path(args.get("file_path", ""))
        pattern = args.get("pattern", "")
        use_regex = args.get("use_regex", False)
        line_start = args.get("line_start")
        line_end = args.get("line_end")
        context_lines = args.get("context_lines", 1)
        max_matches = args.get("max_matches", 20)
        encoding = args.get("encoding", "utf-8")

        if not pattern:
            return "❌ 必须提供 `pattern` 搜索模式。"

        return perform_scan(
            file_path=file_path, pattern=pattern, use_regex=use_regex,
            line_start=line_start, line_end=line_end,
            context_lines=context_lines, max_matches=max_matches,
            encoding=encoding,
        )

    # ═══════════════════════════════════════════════════════════════
    #  原生网页抓取工具（从 web_fetcher 插件迁移）
    # ═══════════════════════════════════════════════════════════════

    def _web_fetch(self, args: dict) -> str:
        url = args.get("url", "").strip()
        max_chars = args.get("max_chars", 8000)
        timeout = args.get("timeout", 15)
        return handle_web_fetch(url=url, max_chars=max_chars, timeout=timeout)

    def _web_extract_links(self, args: dict) -> str:
        url = args.get("url", "").strip()
        same_domain_only = args.get("same_domain_only", False)
        max_links = args.get("max_links", 50)
        timeout = args.get("timeout", 15)
        return handle_web_extract_links(url=url, same_domain_only=same_domain_only,
                                         max_links=max_links, timeout=timeout)

    # ═══════════════════════════════════════════════════════════════
    #  原生上下文检索工具（从 context_retriever 插件迁移）
    # ═══════════════════════════════════════════════════════════════

    def _get_ctx_index(self) -> FTS5Retriever:
        app_dir = os.path.dirname(self.history_path) if self.history_path else ""
        return get_context_index(app_dir=app_dir, project_root=self.project_root)

    def _index_context(self, args: dict) -> str:
        content = args.get("content", "").strip()
        source = args.get("source", "manual").strip() or "manual"
        title = args.get("title", "").strip()
        chunk_size = max(100, min(args.get("chunk_size", 500), 2000))
        chunk_overlap = max(0, min(args.get("chunk_overlap", 100), chunk_size // 2))

        if not content:
            return "⚠️ 没有提供要索引的内容。\n\n用法示例：\n  `index_context(content='...很长的文本...', source='docs')`"

        retriever = self._get_ctx_index()
        chunks = chunk_text(content, chunk_size, chunk_overlap)

        count = 0
        total_chars = 0
        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            chunk_title = f"{title} [块 {i+1}/{len(chunks)}]" if title else ""
            retriever.add(
                text=chunk, source=source, title=chunk_title,
                metadata={"chunk_index": i, "total_chunks": len(chunks)},
                chunk_index=i,
            )
            count += 1
            total_chars += len(chunk)

        retriever.flush()
        stats = retriever.stats()
        return (
            f"✅ 已索引 **{count}** 个文本块\n"
            f"  来源: `{source}`\n"
            f"  总字符数: {total_chars:,}\n"
            f"  分块大小: {chunk_size} 字符（重叠 {chunk_overlap}）\n"
            f"  索引总文档数: {stats['total_documents']}\n\n"
            f"💡 现在可以用 `search_context(query='你的查询')` 进行检索。"
        )

    def _search_context(self, args: dict) -> str:
        query = args.get("query", "").strip()
        top_k = max(1, min(args.get("top_k", 5), 20))
        min_score = max(0.0, args.get("min_score", 0.1))
        source_filter = args.get("source_filter", "").strip() or ""
        expand_context = args.get("expand_context", True)

        if not query:
            return "❌ 搜索查询不能为空。"

        retriever = self._get_ctx_index()
        stats = retriever.stats()
        if stats["total_documents"] == 0:
            return "📭 索引为空，还没有任何已索引的内容。\n\n请先用 `index_context` 添加内容。"

        results = retriever.search(
            query=query, top_k=top_k, min_score=min_score,
            source_filter=source_filter, expand_context=expand_context,
        )

        if not results:
            hint = ""
            if source_filter:
                hint = f"\n  💡 当前按 source='{source_filter}' 过滤，可用来源: {list(stats['sources'].keys())}"
            return f"🔍 未找到与「**{query}**」匹配的结果。{hint}"

        lines = [
            f"🔍 **检索结果**: `{query}`",
            f"  匹配 {len(results)} 条，显示前 {min(len(results), top_k)} 条\n",
        ]

        for i, r in enumerate(results, 1):
            score = r["score"]
            source = r["source"]
            title = r.get("title", "")
            score_bar = "🟢" if score >= 2.0 else ("🟡" if score >= 0.5 else "🟠")

            header = f"**{i}.** {score_bar} `{source}`"
            if title:
                header += f" — *{title}*"
            lines.append(header)
            lines.append(f"   分数: {score:.3f}")

            text = r["text"]
            if len(text) > 400:
                positions = r.get("match_positions", [])
                if positions:
                    center = positions[0]
                    snippet_start = max(0, center - 200)
                    snippet_end = min(len(text), center + 200)
                    if snippet_start > 0:
                        text = "..." + text[snippet_start:snippet_end]
                    else:
                        text = text[:snippet_end]
                    if snippet_end < len(r["text"]):
                        text = text + "..."
                else:
                    text = text[:400] + "..."

            lines.append(f"   ```\n   {text}\n   ```")

            if expand_context:
                if r.get("context_before"):
                    before = r["context_before"]
                    if len(before) > 150:
                        before = "..." + before[-150:]
                    lines.append(f"   📄 上文: _{before}_")
                if r.get("context_after"):
                    after = r["context_after"]
                    if len(after) > 150:
                        after = after[:150] + "..."
                    lines.append(f"   📄 下文: _{after}_")
            lines.append("")

        lines.append(f"📊 索引: {stats['total_documents']} 文档, {stats['total_characters']:,} 字符")
        return "\n".join(lines)

    def _clear_index(self, args: dict) -> str:
        source_filter = args.get("source_filter", "").strip() or ""
        retriever = self._get_ctx_index()

        if source_filter:
            removed = retriever.remove_by_source(source_filter)
            return f"✅ 已清除来源 `{source_filter}` 的 **{removed}** 个文档。"
        else:
            stats_before = retriever.stats()
            retriever.clear_all()
            return f"✅ 已清空全部索引（共 {stats_before['total_documents']} 个文档）。"

    def _index_stats(self, args: dict) -> str:
        stats = self._get_ctx_index().stats()

        lines = [
            "📊 **检索引擎统计**",
            f"",
            f"  引擎: {stats.get('engine', 'Unknown')}",
            f"  总文档数: {stats['total_documents']}",
            f"  总字符数: {stats['total_characters']:,}",
            f"  词库大小: ~{stats['vocabulary_size']} 个唯一词（估算）",
            f"  平均文档长度: {stats['average_document_length']} tokens",
        ]

        if stats["sources"]:
            lines.append(f"  **来源分布**:")
            for src, cnt in sorted(stats["sources"].items(), key=lambda x: x[1], reverse=True):
                pct = (cnt / max(stats['total_documents'], 1)) * 100
                lines.append(f"    `{src}` — {cnt} 文档 ({pct:.1f}%)")
        else:
            lines.append(f"  *(索引为空)*")

        return "\n".join(lines)


if __name__ == "__main__":
    import tempfile

    def test_no_sandbox():
        print("[test1] local executor")
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            r = ex.execute("write_file", {"path": "hello.py", "content": "print('hello')"})
            assert "File written" in r
            r = ex.execute("read_file", {"path": "hello.py"})
            assert "print('hello')" in r
            r = ex.execute("list_dir", {"path": "."})
            assert "hello.py" in r
            r = ex.execute("delete_file", {"path": "hello.py"})
            assert "File deleted" in r
            print("  pass")

    def test_sandbox():
        print("[test2] docker sandbox")
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = DockerSandbox(tmp)
            try:
                sandbox.start()
            except Exception as e:
                print(f"  skip (docker not available: {e})")
                return
            ex = ToolExecutor(tmp, sandbox=sandbox)
            r = ex.execute("write_file", {"path": "hello.py", "content": "print('hello')"})
            assert "File written" in r
            r = ex.execute("exec_cmd", {"command": "python hello.py"})
            assert "hello" in r
            r = ex.execute("exec_cmd", {"command": "pip --version"})
            assert "pip" in r
            sandbox.stop()
            print("  pass")

    def test_context_manager():
        print("[test3] context manager")
        with tempfile.TemporaryDirectory() as tmp:
            try:
                with DockerSandbox(tmp) as sb:
                    assert sb.is_running()
                assert not sb.is_running()
                print("  pass")
            except Exception as e:
                print(f"  skip (docker not available: {e})")

    def test_dangerous_block():
        print("[test4] dangerous command block")
        ex = ToolExecutor(".")
        r = ex.execute("exec_cmd", {"command": "sudo rm -rf /"})
        assert "NORP安全系统拦截" in r
        r = ex.execute("exec_cmd", {"command": "mkfs /dev/sda"})
        assert "NORP安全系统拦截" in r
        # 正常命令应该通过
        r = ex.execute("exec_cmd", {"command": "echo hello"})
        assert "NORP安全系统拦截" not in r
        print("  pass")

    def test_replace_in_file():
        print("[test5] replace_in_file")
        with tempfile.TemporaryDirectory() as tmp:
            ex = ToolExecutor(tmp)
            ex.execute("write_file", {"path": "test.py", "content": "def foo():\n    return 1\n\ndef bar():\n    return 2\n"})
            # 单次替换
            r = ex.execute("replace_in_file", {"path": "test.py", "old_str": "return 1", "new_str": "return 42"})
            assert "1 replacement" in r
            content = ex.execute("read_file", {"path": "test.py"})
            assert "return 42" in content
            assert "return 1" not in content
            # 多处匹配应报错
            ex.execute("write_file", {"path": "test.py", "content": "x = 1\ny = 1\nz = 2\n"})
            r = ex.execute("replace_in_file", {"path": "test.py", "old_str": "= 1", "new_str": "= 99"})
            assert "matches 2 locations" in r
            # 匹配不到应报错
            r = ex.execute("replace_in_file", {"path": "test.py", "old_str": "nonexistent", "new_str": "x"})
            assert "not found" in r
            print("  pass")

    test_no_sandbox()
    test_dangerous_block()
    test_replace_in_file()
    test_sandbox()
    test_context_manager()
    print("\nall tests done")

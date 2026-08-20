# Vibe Coding Agent - 异步工具执行器 (Async Tool Executor)
# 集成：沙箱池、文件IO队列、路径映射、权限级联、生命周期、资源隔离
# Copyright (c) 2026 xingluosama

import nasync_io
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

from sandbox_pool import SandboxPool, Sandbox, get_sandbox_pool
from file_io_queue import FileIOQueue, FileOp, get_file_io_queue
from path_mapper import PathMapper, PluginPathMapper
from permission_cascade import (
    Permission, PermissionCascade, PermissionSet,
    get_permission_cascade, SYSTEM_PERMISSIONS,
)
from lifecycle_manager import LifecycleManager, get_lifecycle_manager
from resource_isolator import ResourceIsolator, ResourceLimits, get_resource_isolator
from norp_safe import NorpSafe, get_norp_safe, ThreatLevel
from vision import is_visual_ext, describe_visual_file, VisionNotConfigured
from workspace_index import WorkspaceIndex, get_workspace_index, search_large_file_stream, scan_and_index, _fmt_size as _wi_fmt_size
from file_surgery import perform_surgery, perform_scan
from web_fetcher_native import handle_web_fetch, handle_web_extract_links
from context_index import FTS5Retriever, get_context_index, chunk_text
from agent_shared import robust_decode


class AsyncToolExecutor:
    """异步工具执行器。

    集成所有新架构模块：
    - SandboxPool: 多沙箱池，异步获取/释放
    - FileIOQueue: 文件并发访问检测和排队
    - PathMapper: 路径映射（宿主↔沙箱）
    - PermissionCascade: 权限级联检查
    - LifecycleManager: 进程组生命周期
    - ResourceIsolator: 资源隔离
    """

    def __init__(
        self,
        project_root: str,
        app_dir: str = "",
        task_id: str = "",
        sandbox_pool: Optional[SandboxPool] = None,
        file_io_queue: Optional[FileIOQueue] = None,
        path_mapper: Optional[PathMapper] = None,
        permission_cascade: Optional[PermissionCascade] = None,
        lifecycle_manager: Optional[LifecycleManager] = None,
        resource_isolator: Optional[ResourceIsolator] = None,
        allow_full_read_large_files: bool = False,
        norp_safe: Optional[NorpSafe] = None,
    ):
        self.project_root = os.path.abspath(project_root)
        self.app_dir = app_dir
        self.task_id = task_id or f"task_{id(self)}"
        self.allow_full_read_large_files = allow_full_read_large_files

        # 注入依赖（默认使用全局单例）
        self.sandbox_pool = sandbox_pool or get_sandbox_pool()
        self.file_io_queue = file_io_queue or get_file_io_queue()
        self.path_mapper = path_mapper or PathMapper()
        self.permission_cascade = permission_cascade or get_permission_cascade()
        self.lifecycle_manager = lifecycle_manager or get_lifecycle_manager()
        self.resource_isolator = resource_isolator or get_resource_isolator()
        # NORP 安全系统
        self.norp_safe = norp_safe or get_norp_safe(app_dir)

        # 当前占用的沙箱
        self._sandbox: Optional[Sandbox] = None

        # 历史记录路径
        if app_dir:
            self.history_path = os.path.join(app_dir, ".agent_history.json")
            os.makedirs(app_dir, exist_ok=True)
        else:
            self.history_path = os.path.join(self.project_root, ".agent_history.json")

        # 工具日志路径
        if app_dir:
            self.tool_log_path = os.path.join(app_dir, "tool_calls.jsonl")
        else:
            self.tool_log_path = ""

    # ── 沙箱管理 ──

    async def acquire_sandbox(self) -> Sandbox:
        """获取沙箱（异步）。"""
        if self._sandbox and self._sandbox.in_use:
            return self._sandbox

        # 配置路径映射
        extra_paths = {}
        if self.app_dir:
            extra_paths[self.app_dir] = "/sandbox_app"

        self._sandbox = await self.sandbox_pool.acquire(
            task_id=self.task_id,
            workspace_root=self.project_root,
            extra_paths=extra_paths,
        )

        # ★ 将沙箱的路径映射同步到本地 PathMapper（供无沙箱回退路径使用）
        for host_path, sandbox_path in self._sandbox.path_map.items():
            self.path_mapper.add_mapping(host_path, sandbox_path)

        return self._sandbox

    async def release_sandbox(self):
        """释放沙箱。"""
        if self._sandbox:
            await self.sandbox_pool.release(self._sandbox)
            self._sandbox = None

    # ── 工具执行入口 ──

    # 内置工具执行超时（秒），防止任何工具永久挂起
    BUILTIN_TOOL_TIMEOUT = 300.0

    async def execute(self, tool_name: str, args: dict) -> str:
        """异步执行工具。"""
        _start = time.time()

        # 权限检查
        self._check_tool_permission(tool_name, args)

        # 资源检查
        if not self.resource_isolator.check_any(self.task_id):
            self._record_debug_tool_call(tool_name, args, "Error: resource quota exhausted for this task", _start)
            return "Error: resource quota exhausted for this task"

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
            self._record_debug_tool_call(tool_name, args, f"Error: unknown tool '{tool_name}'", _start)
            return f"Error: unknown tool '{tool_name}'"

        try:
            result = await nasync_io.wait_for(
                handler(args),
                timeout=self.BUILTIN_TOOL_TIMEOUT,
            )
        except nasync_io.TimeoutError:
            result = (
                f"Error: built-in tool '{tool_name}' timed out "
                f"after {self.BUILTIN_TOOL_TIMEOUT:.0f}s"
            )
        except PermissionError as e:
            result = f"Permission denied: {str(e)}"
        except Exception as e:
            result = f"Tool execution failed: {str(e)}"

        self._record_debug_tool_call(tool_name, args, result, _start)
        return result

    def _record_debug_tool_call(self, tool_name: str, args: dict,
                                result: str, start_time: float):
        """记录工具调用详情到调试收集器（模块 2：诊断报告卡）。"""
        try:
            from debug_logger import get_debug_logger
            dl = get_debug_logger(self.app_dir)
            elapsed_ms = (time.time() - start_time) * 1000
            sandbox_paths = {}
            if self._sandbox is not None:
                sandbox_paths = {
                    "host_workspace": self.project_root,
                    "sandbox_workspace": self._sandbox.map_path(self.project_root),
                    "path_map": dict(self._sandbox.path_map),
                }
            dl.record_tool_call(
                tool=tool_name,
                args=args,
                result=result,
                elapsed_ms=elapsed_ms,
                sandbox_paths=sandbox_paths,
            )
        except Exception:
            pass  # 调试记录失败绝不影响主流程

    def _check_tool_permission(self, tool_name: str, args: dict):
        """检查工具执行权限。"""
        perm_map = {
            "read_file": Permission.FILE_READ,
            "write_file": Permission.FILE_WRITE,
            "replace_in_file": Permission.FILE_WRITE,
            "list_dir": Permission.FILE_LIST,
            "search_in_files": Permission.FILE_READ,
            "delete_file": Permission.FILE_DELETE,
            "exec_cmd": Permission.PROCESS_SHELL,
            "init_project": Permission.FILE_WRITE,
            "install_dependency": Permission.PROCESS_SHELL,
            "git_commit": Permission.PROCESS_SHELL,
            "open_file": Permission.PROCESS_EXEC,
            "web_search": Permission.NETWORK_OUT,
            "read_clipboard": Permission.PROCESS_EXEC,
            "write_clipboard": Permission.PROCESS_EXEC,
            "copy_file": Permission.FILE_WRITE,
            "move_file": Permission.FILE_WRITE,
        }

        perm = perm_map.get(tool_name)
        if perm:
            path = args.get("path", "")
            self.permission_cascade.check_or_raise(perm, path)

    # ── 路径安全 ──

    def _safe_path(self, path: str) -> str:
        """验证并规范化路径，确保在工作区范围内。

        ★ P0-6 修复：改用 pathlib 组件级比较，取代脆弱的前缀字符串检测。
        处理项：符号链接解析、.. 穿越、~ 展开、环境变量展开、Windows 大小写。
        """
        # NORP 安全系统：路径越界语义检查（.. / 环境变量 / 系统目录 / 工作区边界）
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

        # 组件级包含判断（大小写不敏感，兼容 Windows）
        try:
            resolved.relative_to(base)
        except ValueError:
            raise ValueError(
                f"NORP安全系统拦截: 路径越界 - {path}（工作区={self.project_root}）")

        return str(resolved)

    def _map_to_sandbox(self, host_path: str) -> str:
        """将宿主路径映射为沙箱路径。"""
        if self._sandbox:
            return self._sandbox.map_path(host_path)
        return self.path_mapper.to_sandbox(host_path)

    # ═══════════════════════════════════════════════════════════════
    #  文件操作（带 FileIOQueue 并发检测）
    # ═══════════════════════════════════════════════════════════════

    def _get_vision_config(self) -> dict:
        """读取视觉相关配置（用于 read_file 视觉分支）。"""
        if not self.app_dir:
            return {}
        try:
            from config import ConfigManager
            return ConfigManager(self.app_dir).load()
        except Exception:
            return {}

    async def _read_file(self, args: dict) -> str:
        """异步读取文件 — 实际 I/O 在线程池中执行，不阻塞事件循环。"""
        path = self._safe_path(args["path"])
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        allow_full = self.allow_full_read_large_files
        vision_cfg = self._get_vision_config()

        # 文件 I/O 队列：获取读权限
        await self.file_io_queue.acquire(self.task_id, path, FileOp.READ)
        try:
            loop = nasync_io.get_running_loop()
            return await loop.run_in_executor(
                None,
                self._read_file_sync,
                path, start_line, end_line, allow_full, vision_cfg,
            )
        finally:
            self.file_io_queue.release(self.task_id, path, FileOp.READ)

    @staticmethod
    def _read_file_sync(path: str, start_line, end_line, allow_full: bool, vision_cfg: dict = None) -> str:
        """同步读取文件（在线程池中运行）。"""
        # 视觉文件（图片/视频）：读二进制 → 视觉描述
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if is_visual_ext(ext):
            try:
                return describe_visual_file(path, vision_cfg or {})
            except VisionNotConfigured as e:
                return f"[视觉未配置] {e}"
            except Exception as e:
                return f"[视觉处理失败] {e}"

        if start_line is None and end_line is None:
            file_size = os.path.getsize(path)
            MAX_FULL_READ_SIZE = 100 * 1024  # 100KB
            if file_size > MAX_FULL_READ_SIZE and not allow_full:
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

    async def _write_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])

        # 文件 I/O 队列：获取写权限（如有冲突则排队）
        await self.file_io_queue.acquire(self.task_id, path, FileOp.WRITE)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(args["content"])
            return f"File written: {path}"
        finally:
            self.file_io_queue.release(self.task_id, path, FileOp.WRITE)

    async def _replace_in_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])
        old_str = args["old_str"]
        new_str = args["new_str"]

        if not os.path.exists(path):
            return f"Error: file not found: {path}"

        # 文件 I/O 队列：写权限
        await self.file_io_queue.acquire(self.task_id, path, FileOp.WRITE)
        try:
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
                        return (f"Error: old_str not found in file. Similar lines:\n{hint}\n\n"
                                f"Tip: use read_file to verify the exact content.")
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
        finally:
            self.file_io_queue.release(self.task_id, path, FileOp.WRITE)

    async def _delete_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])

        # 文件 I/O 队列：删除权限（视为写操作）
        await self.file_io_queue.acquire(self.task_id, path, FileOp.DELETE)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
                return f"Directory deleted: {path}"
            elif os.path.isfile(path):
                os.remove(path)
                return f"File deleted: {path}"
            else:
                return f"Path not found: {path}"
        finally:
            self.file_io_queue.release(self.task_id, path, FileOp.DELETE)

    async def _copy_file(self, args: dict) -> str:
        """异步复制文件或目录。"""
        source = self._safe_path(args["source"])
        destination = self._safe_path(args["destination"])

        if not os.path.exists(source):
            return f"Error: source not found: {source}"

        # 如果目标已存在且是目录，复制到目录内
        if os.path.isdir(destination):
            dest = os.path.join(destination, os.path.basename(source))
        else:
            dest = destination

        if os.path.exists(dest):
            return f"Error: destination already exists: {dest}. Delete it first or choose a different name."

        os.makedirs(os.path.dirname(dest), exist_ok=True)

        # 文件 I/O 队列：写入权限
        await self.file_io_queue.acquire(self.task_id, source, FileOp.READ)
        await self.file_io_queue.acquire(self.task_id, dest, FileOp.WRITE)
        try:
            loop = nasync_io.get_running_loop()
            await loop.run_in_executor(
                None,
                self._copy_file_sync,
                source, dest,
            )
            return f"Copied: {source} → {dest}"
        except Exception as e:
            return f"Failed to copy: {str(e)}"
        finally:
            self.file_io_queue.release(self.task_id, dest, FileOp.WRITE)
            self.file_io_queue.release(self.task_id, source, FileOp.READ)

    @staticmethod
    def _copy_file_sync(source: str, dest: str):
        """同步复制文件（在线程池中运行）。"""
        if os.path.isdir(source):
            shutil.copytree(source, dest)
        else:
            shutil.copy2(source, dest)

    async def _move_file(self, args: dict) -> str:
        """异步移动文件或目录（也可用于重命名）。"""
        source = self._safe_path(args["source"])
        destination = self._safe_path(args["destination"])

        if not os.path.exists(source):
            return f"Error: source not found: {source}"

        # 如果目标已存在且是目录，移动到目录内
        if os.path.isdir(destination):
            dest = os.path.join(destination, os.path.basename(source))
        else:
            dest = destination

        if os.path.exists(dest):
            return f"Error: destination already exists: {dest}. Delete it first or choose a different name."

        os.makedirs(os.path.dirname(dest), exist_ok=True)

        # 文件 I/O 队列：写权限
        await self.file_io_queue.acquire(self.task_id, source, FileOp.READ)
        await self.file_io_queue.acquire(self.task_id, dest, FileOp.WRITE)
        try:
            loop = nasync_io.get_running_loop()
            await loop.run_in_executor(
                None,
                self._move_file_sync,
                source, dest,
            )
            return f"Moved: {source} → {dest}"
        except Exception as e:
            return f"Failed to move: {str(e)}"
        finally:
            self.file_io_queue.release(self.task_id, dest, FileOp.WRITE)
            self.file_io_queue.release(self.task_id, source, FileOp.READ)

    @staticmethod
    def _move_file_sync(source: str, dest: str):
        """同步移动文件（在线程池中运行）。"""
        shutil.move(source, dest)

    async def _list_dir(self, args: dict) -> str:
        """异步列出目录 — 实际 I/O 在线程池中执行，不阻塞事件循环。"""
        path = self._safe_path(args.get("path", "."))
        loop = nasync_io.get_running_loop()
        return await loop.run_in_executor(None, self._list_dir_sync, path)

    @staticmethod
    def _list_dir_sync(path: str) -> str:
        """同步列出目录（在线程池中运行）。"""
        if not os.path.isdir(path):
            return f"Not a directory: {path}"
        items = os.listdir(path)
        if not items:
            return "(empty)"
        dirs = [d + "/" for d in items if os.path.isdir(os.path.join(path, d))]
        files = [f for f in items if not os.path.isdir(os.path.join(path, f))]
        return "\n".join(sorted(dirs) + sorted(files))

    async def _search_in_files(self, args: dict) -> str:
        """异步搜索文件内容 — os.walk + 文件读取在线程池中执行。

        ★ 关键修复：之前此方法虽标记为 async def，但内部零 await，
        os.walk() 遍历大目录（如 ComfyUI 数万文件）时直接阻塞事件循环，
        导致 nasync_io.wait_for 无法取消、整个 UI 挂起。
        现在将阻塞 I/O 剥离到 run_in_executor 线程池中。
        """
        pattern = args["pattern"]
        root = self._safe_path(args.get("path", "."))
        project_root = self.project_root

        loop = nasync_io.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._search_in_files_sync,
            pattern, root, project_root,
        )

    @staticmethod
    def _search_in_files_sync(pattern: str, root: str, project_root: str) -> str:
        """同步搜索文件内容（在线程池中运行）。

        遍历目录 → 逐个文件读取匹配 → 最多返回 50 条结果。
        """
        matches = []
        if os.path.isfile(root):
            targets = [root]
        else:
            targets = []
            for dirpath, _, filenames in os.walk(root):
                # 跳过隐藏目录和常见忽略目录
                try:
                    rel_parts = Path(dirpath).relative_to(root).parts
                except ValueError:
                    rel_parts = ()
                if any(part.startswith(".") or part in ("node_modules", "__pycache__", ".git")
                       for part in rel_parts):
                    continue
                for fn in filenames:
                    if not fn.startswith("."):
                        targets.append(os.path.join(dirpath, fn))

        for filepath in targets:
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, 1):
                        if pattern in line:
                            rel = os.path.relpath(filepath, project_root)
                            matches.append(f"{rel}:{lineno}: {line.strip()[:120]}")
            except Exception:
                continue
            if len(matches) >= 50:
                matches.append("... (truncated, max 50 results)")
                break

        return "\n".join(matches) if matches else "No matches found."

    # ═══════════════════════════════════════════════════════════════
    #  命令执行（沙箱池 + 生命周期绑定）
    # ═══════════════════════════════════════════════════════════════

    async def _exec_cmd(self, args: dict) -> str:
        cmd = args["command"]
        timeout = args.get("timeout", 30)

        # NORP 安全系统：全面安全检查（危险命令 + UAC提权）
        result = self.norp_safe.check_command_full(cmd, task_id=self.task_id)
        if result.blocked:
            return f"NORP安全系统拦截: {result.reason}（威胁等级: {result.threat_level.value}）"

        # 资源隔离检查
        if self.resource_isolator.throttle_plugins():
            # 终端资源紧张，非终端命令延后
            pass

        # 获取沙箱
        try:
            sandbox = await self.acquire_sandbox()
        except Exception as e:
            # 沙箱不可用，回退到本地执行
            return await self._exec_local_async(cmd, timeout)

        # 在沙箱中执行
        try:
            # 路径映射：将命令中的宿主路径替换为沙箱路径
            mapped_cmd = self._map_command_paths(cmd)
            cwd = self._map_to_sandbox(self.project_root)

            result = await self.sandbox_pool.exec_in_sandbox(
                sandbox, mapped_cmd, timeout=timeout, cwd=cwd,
            )

            # 路径反向映射：将输出中的沙箱路径还原为宿主路径
            result = self._unmap_result_paths(result)
            return result
        except Exception as e:
            # 沙箱执行失败，回退本地
            return await self._exec_local_async(cmd, timeout)

    async def _exec_local_async(self, cmd: str, timeout: int) -> str:
        """本地异步执行命令（带进程组管理）。"""
        creationflags = 0
        if platform.system() == "Windows":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            creationflags |= 0x08000000

        try:
            proc = await nasync_io.create_subprocess_shell(
                cmd,
                stdin=nasync_io.subprocess.DEVNULL,
                stdout=nasync_io.subprocess.PIPE,
                stderr=nasync_io.subprocess.PIPE,
                cwd=self.project_root,
                creationflags=creationflags if platform.system() == "Windows" else 0,
                start_new_session=True,
            )

            # 注册到生命周期管理器
            self.lifecycle_manager.register_process(self.task_id, proc.pid)

            stdout, stderr = await nasync_io.wait_for(
                proc.communicate(), timeout=timeout
            )
            output = (robust_decode(stdout) if stdout else "") + \
                     (robust_decode(stderr) if stderr else "")
            if not output.strip():
                output = f"Command exited with code {proc.returncode}"
            return output.strip()

        except nasync_io.TimeoutError:
            # 超时：生命周期管理器会杀进程组
            self.lifecycle_manager.stop_task(self.task_id, reason="cmd_timeout")
            return f"Command timed out after {timeout}s and process group was terminated"

    def _map_command_paths(self, cmd: str) -> str:
        """将命令中的宿主路径替换为沙箱路径。"""
        if not self._sandbox:
            return cmd

        result = cmd
        # 替换工作区路径
        if self.project_root in result:
            sandbox_root = self._sandbox.map_path(self.project_root)
            result = result.replace(self.project_root, sandbox_root)

        # 替换 app_dir
        if self.app_dir and self.app_dir in result:
            sandbox_app = self._sandbox.map_path(self.app_dir)
            result = result.replace(self.app_dir, sandbox_app)

        return result

    def _unmap_result_paths(self, text: str) -> str:
        """将输出中的沙箱路径还原为宿主路径。"""
        if not self._sandbox:
            return text

        result = text
        for sandbox_path, host_path in self._sandbox.reverse_path_map.items():
            if sandbox_path in result:
                result = result.replace(sandbox_path, host_path)
        return result

    # ═══════════════════════════════════════════════════════════════
    #  其他工具（异步化）
    # ═══════════════════════════════════════════════════════════════

    async def _open_file(self, args: dict) -> str:
        path = self._safe_path(args["path"])
        if not os.path.exists(path):
            return f"Error: file not found: {path}"

        def _open():
            system = platform.system()
            if system == "Windows":
                os.startfile(path)
            elif system == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])

        await nasync_io.get_running_loop().run_in_executor(None, _open)
        return f"File opened: {path}"

    async def _read_clipboard(self, args: dict) -> str:
        """读取系统剪贴板文本。"""
        def _read():
            system = platform.system()
            if system == "Windows":
                try:
                    result = subprocess.run(
                        ["powershell", "-Command", "Get-Clipboard"],
                        capture_output=True, text=True, timeout=10,
                        creationflags=0x08000000 if platform.system() == "Windows" else 0,
                    )
                    if result.returncode != 0:
                        raise RuntimeError(result.stderr.strip())
                    return result.stdout
                except FileNotFoundError:
                    # 回退：使用 clip 命令 + 临时文件
                    import tempfile
                    tmp = os.path.join(tempfile.gettempdir(), "_vibe_paste.txt")
                    subprocess.run(
                        ["powershell", "-Command", f"Get-Clipboard > '{tmp}'"],
                        capture_output=True, timeout=10,
                        creationflags=0x08000000,
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
                    ["pbpaste"], capture_output=True, text=True, timeout=10
                )
                return result.stdout
            else:
                # Linux: 尝试 wl-paste (Wayland) 或 xclip (X11)
                for cmd in [["wl-paste"], ["xclip", "-selection", "clipboard", "-o"]]:
                    try:
                        result = subprocess.run(
                            cmd, capture_output=True, text=True, timeout=10
                        )
                        if result.returncode == 0:
                            return result.stdout
                    except FileNotFoundError:
                        continue
                return "Error: No clipboard tool found. Install xclip (X11) or wl-clipboard (Wayland)."

        try:
            text = await nasync_io.get_running_loop().run_in_executor(None, _read)
            if not text:
                return "(clipboard is empty)"
            return text
        except Exception as e:
            return f"Failed to read clipboard: {str(e)}"

    async def _write_clipboard(self, args: dict) -> str:
        """将文本写入系统剪贴板。"""
        text = args["text"]

        def _write():
            system = platform.system()
            if system == "Windows":
                # 使用 PowerShell Set-Clipboard，避免特殊字符问题
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
                # Linux: 尝试 wl-copy (Wayland) 或 xclip (X11)
                for cmd in [["wl-copy"], ["xclip", "-selection", "clipboard"]]:
                    try:
                        proc = subprocess.run(
                            cmd, input=text, capture_output=True, text=True, timeout=10
                        )
                        if proc.returncode == 0:
                            return
                    except FileNotFoundError:
                        continue
                raise RuntimeError(
                    "No clipboard tool found. Install xclip (X11) or wl-clipboard (Wayland)."
                )

        try:
            await nasync_io.get_running_loop().run_in_executor(None, _write)
            preview = text[:80] + "..." if len(text) > 80 else text
            return f"Text copied to clipboard ({len(text)} chars): {preview}"
        except Exception as e:
            return f"Failed to write clipboard: {str(e)}"

    async def _unpack_archive(self, args: dict) -> str:
        """安全解压缩文件。"""
        path = self._safe_path(args["path"])
        dest_dir = args.get("dest_dir")
        if dest_dir:
            dest_dir = self._safe_path(dest_dir)

        def _do_unpack():
            from archive_utils import unpack_archive
            return unpack_archive(path, dest_dir)

        return await nasync_io.get_running_loop().run_in_executor(None, _do_unpack)

    async def _web_search(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return "Error: search query is required"

        def _search():
            try:
                import requests
                url = "https://api.duckduckgo.com/"
                params = {
                    "q": query, "format": "json", "no_html": 1,
                    "skip_disambig": 1, "t": "vibe_agent"
                }
                resp = requests.get(url, params=params, timeout=15,
                                    headers={"User-Agent": "VibeCodingAgent/1.0"})
                resp.raise_for_status()
                data = resp.json()

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

        return await nasync_io.get_running_loop().run_in_executor(None, _search)

    async def _init_project(self, args: dict) -> str:
        ptype = args["type"]
        name = args["name"]
        proj_path = self._safe_path(name)
        os.makedirs(proj_path, exist_ok=True)

        def _init():
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

        return await nasync_io.get_running_loop().run_in_executor(None, _init)

    async def _install_dependency(self, args: dict) -> str:
        package = args["package"]
        manager = args.get("manager", "pip")

        # ★ 安全修复：使用列表参数 + shell=False，防止命令注入
        if manager == "pip":
            cmd_list = [sys.executable, "-m", "pip", "install", package]
        elif manager == "npm":
            cmd_list = ["npm", "install", package]
        else:
            return f"Unsupported package manager: {manager}"

        # 直接在本地异步执行，绕过 _exec_cmd 的 shell 封装
        try:
            proc = await nasync_io.create_subprocess_exec(
                *cmd_list,
                stdin=nasync_io.subprocess.DEVNULL,
                stdout=nasync_io.subprocess.PIPE,
                stderr=nasync_io.subprocess.PIPE,
                cwd=self.project_root,
            )
            self.lifecycle_manager.register_process(self.task_id, proc.pid)

            stdout, stderr = await nasync_io.wait_for(
                proc.communicate(), timeout=120
            )
            output = (robust_decode(stdout) if stdout else "") + \
                     (robust_decode(stderr) if stderr else "")
            return output.strip() or f"Exit code: {proc.returncode}"
        except nasync_io.TimeoutError:
            self.lifecycle_manager.stop_task(self.task_id, reason="install_timeout")
            return f"Package install timed out after 120s"
        except Exception as e:
            return f"Package install failed: {str(e)}"

    async def _git_commit(self, args: dict) -> str:
        message = args["message"]

        async def _commit():
            proc1 = await nasync_io.create_subprocess_exec(
                "git", "add", "-A",
                cwd=self.project_root,
                stdout=nasync_io.subprocess.PIPE,
                stderr=nasync_io.subprocess.PIPE,
            )
            await proc1.communicate()

            proc2 = await nasync_io.create_subprocess_exec(
                "git", "commit", "-m", message,
                cwd=self.project_root,
                stdout=nasync_io.subprocess.PIPE,
                stderr=nasync_io.subprocess.PIPE,
            )
            stdout, stderr = await proc2.communicate()
            output = (robust_decode(stdout) if stdout else "") + \
                     (robust_decode(stderr) if stderr else "")
            return output.strip()

        try:
            return await _commit()
        except Exception as e:
            return f"Git commit failed: {str(e)}"

    async def _task_done(self, args: dict) -> str:
        summary = args["summary"]
        code_path = args.get("code_path", ".")

        def _record():
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

        return await nasync_io.get_running_loop().run_in_executor(None, _record)

    # ── 日志 ──

    def log_tool_call(self, step: int, tool_name: str, args: dict, result: str):
        """记录工具调用（JSONL 格式）。"""
        if not self.tool_log_path:
            return
        try:
            result_summary = result[:500] + "..." if len(result) > 500 else result
            record = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "step": step,
                "tool": tool_name,
                "args": args,
                "result_length": len(result),
                "result_summary": result_summary,
                "file_io_stats": self.file_io_queue.get_stats(),
            }
            with open(self.tool_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── 清理 ──

    async def cleanup(self):
        """清理资源。

        ★ P0-7 修复：取消语义 —— 终止任务关联的沙箱进程树（杀整个进程组），
        再释放沙箱，确保用户停止任务后命令执行的子进程树被完整终止。
        """
        try:
            await self.sandbox_pool.kill_task_sandbox(self.task_id)
        except Exception:
            pass
        await self.release_sandbox()

    # ═══════════════════════════════════════════════════════════════
    #  原生工具 handlers（从插件迁移，同步方法在线程池执行）
    # ═══════════════════════════════════════════════════════════════

    def _get_ws_index(self):
        app_dir = self.app_dir if self.app_dir else ""
        return get_workspace_index(app_dir=app_dir, project_root=self.project_root)

    def _get_ctx_index(self):
        app_dir = self.app_dir if self.app_dir else ""
        return get_context_index(app_dir=app_dir, project_root=self.project_root)

    async def _index_workspace(self, args: dict) -> str:
        return await nasync_io.get_running_loop().run_in_executor(
            None, self._index_workspace_sync, args)

    def _index_workspace_sync(self, args: dict) -> str:
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
        result = scan_and_index(index, root, include_patterns=include_patterns,
                                exclude_dirs=exclude_dirs, max_file_mb=max_file_mb, force=force)
        if "error" in result:
            return f"❌ {result['error']}"
        return (f"✅ **工作区索引完成**\n"
                f"  索引根目录: `{result['root']}`\n"
                f"  扫描文件: {result['scanned']}\n"
                f"  新索引/更新: **{result['indexed']}** 个文件\n"
                f"  耗时: {result['elapsed_seconds']} 秒\n\n"
                f"💡 现在可用 `search_files(query='...')` 精确检索。")

    async def _search_files_native(self, args: dict) -> str:
        return await nasync_io.get_running_loop().run_in_executor(
            None, self._search_files_sync, args)

    def _search_files_sync(self, args: dict) -> str:
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
        result = index.search(query=query, top_k=top_k, path_filter=path_filter,
                              file_pattern=file_pattern, case_sensitive=case_sensitive,
                              exact_phrase=exact_phrase, max_lines_per_file=max_lines_per_file,
                              line_context=line_context)
        matches = result["matches"]
        if not matches:
            return f"🔍 未在索引中找到「**{query}**」"
        mode = "完整短语匹配" if exact_phrase else "关键词 AND 匹配"
        lines = [f"🔍 **文件内容精确检索**: `{query}`",
                 f"  模式: {mode} | 命中: {result['total_lines_found']} 行 / {len(result['file_hits'])} 个文件\n"]
        current_file = None
        for m in matches:
            if m["file"] != current_file:
                current_file = m["file"]
                try:
                    rel = os.path.relpath(current_file, self.project_root)
                except ValueError:
                    rel = current_file
                lines.append(f"📄 **{rel}**")
            snippet = m["text"][:200] + ("…" if len(m["text"]) > 200 else "")
            for cb in m["context_before"][-line_context:]:
                if cb.strip():
                    lines.append(f"    ┊ {cb.strip()[:150]}")
            lines.append(f"  **L{m['line']}** │ {snippet}")
            for ca in m["context_after"][:line_context]:
                if ca.strip():
                    lines.append(f"    ┊ {ca.strip()[:150]}")
            lines.append("")
        lines.append(f"📊 索引: {stats['total_files']} 文件, {stats['total_characters']:,} 字符")
        return "\n".join(lines)

    async def _find_files_native(self, args: dict) -> str:
        return await nasync_io.get_running_loop().run_in_executor(
            None, self._find_files_sync, args)

    def _find_files_sync(self, args: dict) -> str:
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
            icon = {"indexed": "✅", "binary": "⚙️", "too_large": "🐘"}.get(r["status"], "❓")
            rel = r["path"]
            try:
                rel = os.path.relpath(rel, self.project_root)
            except ValueError:
                pass
            lines.append(f"  {icon} `{rel}` ({size_str})")
        return "\n".join(lines)

    async def _search_large_file(self, args: dict) -> str:
        return await nasync_io.get_running_loop().run_in_executor(
            None, self._search_large_file_sync, args)

    def _search_large_file_sync(self, args: dict) -> str:
        path = (args.get("path") or "").strip()
        query = (args.get("query") or "").strip()
        if not path or not query:
            return "❌ 需要同时提供 path 和 query。"
        full = self._safe_path(path)
        if not os.path.exists(full):
            return f"❌ 文件不存在: `{full}`"
        result = search_large_file_stream(
            full, query, regex=bool(args.get("regex", False)),
            case_sensitive=bool(args.get("case_sensitive", False)),
            line_context=max(0, min(int(args.get("line_context", 2)), 10)),
            max_matches=max(1, min(int(args.get("max_matches", 30)), 100)),
            encoding=(args.get("encoding") or "").strip())
        if "error" in result:
            return f"❌ {result['error']}"
        lines = [f"🔍 **大文件检索完成**",
                 f"📄 文件: `{full}` ({_wi_fmt_size(result['file_size'])})",
                 f"✅ 命中 **{len(result['matches'])}** 处",
                 f"⏱️ 耗时 {result['elapsed_seconds']} 秒\n"]
        for m in result["matches"]:
            lines.append(f"  **L{m['line']:,}** │ {m['text']}")
            for cb in m["context_before"][-3:]:
                if cb.strip():
                    lines.append(f"    ┊ {cb.strip()[:150]}")
            lines.append("")
        return "\n".join(lines)

    async def _workspace_index_status(self, args: dict) -> str:
        return await nasync_io.get_running_loop().run_in_executor(
            None, self._workspace_index_status_sync)

    def _workspace_index_status_sync(self) -> str:
        stats = self._get_ws_index().stats()
        lines = ["📊 **工作区文件索引状态**", "",
                 f"  索引根目录: `{stats.get('index_root') or '(未扫描)'}`",
                 f"  登记文件数: {stats['total_files']}",
                 f"  内容索引块数: {stats['total_chunks']:,}",
                 f"  索引字符总量: {stats['total_characters']:,}",
                 f"  上次扫描: {stats.get('last_scan') or '从未'}"]
        dist = stats["status_distribution"]
        if dist:
            lines.append("  **文件状态分布**:")
            for status, cnt in sorted(dist.items(), key=lambda x: x[1], reverse=True):
                icon = {"indexed": "✅", "binary": "⚙️", "too_large": "🐘"}.get(status, "❓")
                lines.append(f"    {icon} {status} — {cnt} 个")
        return "\n".join(lines)

    async def _clear_workspace_index(self, args: dict) -> str:
        return await nasync_io.get_running_loop().run_in_executor(
            None, self._clear_workspace_index_sync, args)

    def _clear_workspace_index_sync(self, args: dict) -> str:
        index = self._get_ws_index()
        path = (args.get("path") or "").strip()
        if path:
            full = os.path.normpath(os.path.join(self.project_root, path)) if not os.path.isabs(path) else os.path.normpath(path)
            if os.path.isdir(full):
                removed = index.remove_by_dir(full)
                return f"✅ 已清除目录下 **{removed}** 个文件的索引。"
            removed_chunks = index.remove_file(full)
            return f"✅ 已清除文件索引（{removed_chunks} 个块）。"
        stats_before = index.stats()
        index.clear_all()
        return f"✅ 已清空全部工作区文件索引。"

    async def _surgical_replace(self, args: dict) -> str:
        return await nasync_io.get_running_loop().run_in_executor(
            None, self._surgical_replace_sync, args)

    def _surgical_replace_sync(self, args: dict) -> str:
        file_path = self._safe_path(args.get("file_path", ""))
        mode = args.get("mode", "replace")
        valid_modes = ("replace", "insert_before", "insert_after", "delete", "replace_all")
        if mode not in valid_modes:
            return f"❌ 不支持的模式 `{mode}`。"
        return perform_surgery(
            file_path=file_path, line_number=args.get("line_number"),
            old_content=args.get("old_content"), new_content=args.get("new_content"),
            mode=mode, use_regex=args.get("use_regex", False),
            count=args.get("count", 1), dry_run=args.get("dry_run", False),
            context_lines=args.get("context_lines", 2),
            backup=args.get("backup", False), encoding=args.get("encoding", "utf-8"))

    async def _surgical_scan(self, args: dict) -> str:
        return await nasync_io.get_running_loop().run_in_executor(
            None, self._surgical_scan_sync, args)

    def _surgical_scan_sync(self, args: dict) -> str:
        file_path = self._safe_path(args.get("file_path", ""))
        pattern = args.get("pattern", "")
        if not pattern:
            return "❌ 必须提供 `pattern` 搜索模式。"
        return perform_scan(
            file_path=file_path, pattern=pattern,
            use_regex=args.get("use_regex", False),
            line_start=args.get("line_start"), line_end=args.get("line_end"),
            context_lines=args.get("context_lines", 1),
            max_matches=args.get("max_matches", 20),
            encoding=args.get("encoding", "utf-8"))

    async def _web_fetch(self, args: dict) -> str:
        return await nasync_io.get_running_loop().run_in_executor(
            None, handle_web_fetch,
            args.get("url", "").strip(),
            args.get("max_chars", 8000),
            args.get("timeout", 15))

    async def _web_extract_links(self, args: dict) -> str:
        return await nasync_io.get_running_loop().run_in_executor(
            None, handle_web_extract_links,
            args.get("url", "").strip(),
            args.get("same_domain_only", False),
            args.get("max_links", 50),
            args.get("timeout", 15))

    async def _index_context(self, args: dict) -> str:
        return await nasync_io.get_running_loop().run_in_executor(
            None, self._index_context_sync, args)

    def _index_context_sync(self, args: dict) -> str:
        content = args.get("content", "").strip()
        source = args.get("source", "manual").strip() or "manual"
        title = args.get("title", "").strip()
        chunk_size = max(100, min(args.get("chunk_size", 500), 2000))
        chunk_overlap = max(0, min(args.get("chunk_overlap", 100), chunk_size // 2))
        if not content:
            return "⚠️ 没有提供要索引的内容。"
        retriever = self._get_ctx_index()
        chunks = chunk_text(content, chunk_size, chunk_overlap)
        count = 0
        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            retriever.add(text=chunk, source=source,
                          title=f"{title} [块 {i+1}/{len(chunks)}]" if title else "",
                          chunk_index=i)
            count += 1
        retriever.flush()
        return f"✅ 已索引 **{count}** 个文本块（来源: `{source}`）"

    async def _search_context(self, args: dict) -> str:
        return await nasync_io.get_running_loop().run_in_executor(
            None, self._search_context_sync, args)

    def _search_context_sync(self, args: dict) -> str:
        query = args.get("query", "").strip()
        if not query:
            return "❌ 搜索查询不能为空。"
        retriever = self._get_ctx_index()
        stats = retriever.stats()
        if stats["total_documents"] == 0:
            return "📭 索引为空。请先用 `index_context` 添加内容。"
        results = retriever.search(
            query=query, top_k=max(1, min(args.get("top_k", 5), 20)),
            min_score=max(0.0, args.get("min_score", 0.1)),
            source_filter=args.get("source_filter", "").strip() or "",
            expand_context=args.get("expand_context", True))
        if not results:
            return f"🔍 未找到与「**{query}**」匹配的结果。"
        lines = [f"🔍 **检索结果**: `{query}`", f"  匹配 {len(results)} 条\n"]
        for i, r in enumerate(results, 1):
            score_bar = "🟢" if r["score"] >= 2.0 else ("🟡" if r["score"] >= 0.5 else "🟠")
            lines.append(f"**{i}.** {score_bar} `{r['source']}` — 分数: {r['score']:.3f}")
            text = r["text"][:400] + ("..." if len(r["text"]) > 400 else "")
            lines.append(f"   ```\n   {text}\n   ```")
            lines.append("")
        return "\n".join(lines)

    async def _clear_index(self, args: dict) -> str:
        return await nasync_io.get_running_loop().run_in_executor(
            None, self._clear_index_sync, args)

    def _clear_index_sync(self, args: dict) -> str:
        source_filter = args.get("source_filter", "").strip() or ""
        retriever = self._get_ctx_index()
        if source_filter:
            removed = retriever.remove_by_source(source_filter)
            return f"✅ 已清除来源 `{source_filter}` 的 **{removed}** 个文档。"
        else:
            stats_before = retriever.stats()
            retriever.clear_all()
            return f"✅ 已清空全部索引（共 {stats_before['total_documents']} 个文档）。"

    async def _index_stats(self, args: dict) -> str:
        return await nasync_io.get_running_loop().run_in_executor(
            None, self._index_stats_sync)

    def _index_stats_sync(self) -> str:
        stats = self._get_ctx_index().stats()
        lines = ["📊 **检索引擎统计**", "",
                 f"  引擎: {stats.get('engine', 'Unknown')}",
                 f"  总文档数: {stats['total_documents']}",
                 f"  总字符数: {stats['total_characters']:,}"]
        if stats["sources"]:
            lines.append("  **来源分布**:")
            for src, cnt in sorted(stats["sources"].items(), key=lambda x: x[1], reverse=True):
                lines.append(f"    `{src}` — {cnt} 文档")
        return "\n".join(lines)

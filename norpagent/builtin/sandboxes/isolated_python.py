# Copyright (c) 2026 xingluosama121, MIT Licensed
"""PTC 隔离执行器：模型生成的 Python 代码在子进程中执行（零第三方依赖）。

威胁模型：模型生成的代码不可信（可能被提示词注入诱导出逃逸尝试）。
隔离设计（纵深防御，四层）：

1. **AST 静态预检**（宿主侧，主边界）：拒绝 import / 双下划线属性访问 /
   危险内建名 / 魔法下标（``d["__globals__"]``）——阻断经典逃逸入口；
2. **受限 builtins**（子进程内）：无 import/open/eval/exec/type/repr 等；
3. **干净函数命名空间**：暴露给用户代码的 ``call_tool`` 及其引用的
   辅助函数全部通过「编译进独立 globals」构造——``fn.__globals__``
   无法攀爬回含 sys/os 的模块命名空间；
4. **进程级隔离**：代码运行在独立子进程，宿主通过 stdin/stdout 长度
   前缀协议服务其工具调用请求；超时强杀进程树、输出长度受限。

协议（宿主 <-> 子进程，文本行）：
    子进程 -> 宿主:  JSON 行 {"op":"call_tool","name":..,"args":{..}}
                     或 {"op":"done","output":..,"error":..}
    宿主   -> 子进程:  "OK <n>" 换行 + 输出 n 字符 + 换行
                      "ERR <n>" 换行 + 错误 n 字符 + 换行

注意：本执行器是「工具级」隔离。若需要 OS 级隔离（容器 / 虚拟机），
替换预设中的 sandbox 组件即可，PTC 工具签名不变。
"""

from __future__ import annotations

import ast
import json
import os
import platform
import queue
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from norpagent.loops.cancel import cancel_requested
from norpagent.protocols.sandbox import SandboxResult

_IS_WINDOWS = platform.system() == "Windows"

# ── 用户代码受限 builtins 白名单（无任何模块/IO/代码执行入口）──

_ALLOWED_BUILTIN_NAMES = (
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "int", "isinstance", "len", "list",
    "map", "max", "min", "pow", "print", "range", "reversed", "round",
    "set", "slice", "sorted", "str", "sum", "tuple", "zip",
    # 异常类型：允许 try/except 捕获工具调用失败
    "Exception", "RuntimeError", "ValueError", "TypeError",
    "KeyError", "IndexError", "AttributeError", "StopIteration",
    "ZeroDivisionError", "OverflowError", "LookupError",
)

# 静态预检：禁止出现的名字（任意上下文）
_FORBIDDEN_NAMES = {
    "__import__", "__builtins__", "eval", "exec", "compile", "open",
    "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "input", "breakpoint", "memoryview", "type", "super", "object",
    "staticmethod", "classmethod", "property",
}


def check_ptc_source(code: str, max_len: int = 200_000) -> Tuple[bool, str]:
    """PTC 代码 AST 静态预检。

    返回 (是否放行, 拒绝原因)。空代码 / 超长代码 / 语法错误均拒绝。
    """
    if not code or not isinstance(code, str):
        return False, "代码为空"
    if len(code) > max_len:
        return False, f"代码过长（{len(code)} 字符，上限 {max_len}）"
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"语法错误: {exc}"

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return False, f"PTC 沙箱禁止 import（第 {node.lineno} 行）"
        if isinstance(node, ast.Name):
            if node.id in _FORBIDDEN_NAMES:
                return False, f"禁止使用内建名 '{node.id}'（第 {node.lineno} 行）"
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                return False, (
                    f"禁止双下划线属性访问（第 {node.lineno} 行，"
                    "属于沙箱逃逸入口）"
                )
        elif isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Constant) \
                    and isinstance(node.slice.value, str) \
                    and node.slice.value.startswith("__"):
                return False, (
                    f"禁止魔法下标访问（第 {node.lineno} 行，"
                    "属于沙箱逃逸入口）"
                )
    return True, ""


# ── 支持函数源码（宿主侧定义，经 %r 嵌入包装器，自动完成转义）──
# 说明：这些源码字符串的值会被 repr() 嵌入子进程文件，文件解析一次后
# 得到的字符串值与这里完全一致，再交给 compile 执行。

_ENC_SRC = """\
def _enc(obj):
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, str):
        s = obj
        s = s.replace("\\\\", "\\\\\\\\")
        s = s.replace('"', '\\\\"')
        s = s.replace("\\n", "\\\\n")
        s = s.replace("\\r", "\\\\r")
        s = s.replace("\\t", "\\\\t")
        return '"' + s + '"'
    if isinstance(obj, (int, float, bool)):
        return str(obj)
    if isinstance(obj, dict):
        parts = [_enc(k) + ":" + _enc(v) for k, v in obj.items()]
        return "{" + ",".join(parts) + "}"
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(_enc(x) for x in obj) + "]"
    return _enc(str(obj))
"""

_WRITE_SRC = """\
def _write(out, obj):
    out.write(_enc(obj) + "\\n")
    out.flush()
"""

_READ_SRC = """\
def _read(inp):
    header = inp.readline()
    if not header:
        raise RuntimeError("宿主连接已关闭")
    status, num = header.split(" ", 1)
    n = int(num)
    data = inp.read(n)
    inp.read(1)
    if status == "ERR":
        raise RuntimeError(data)
    return data
"""

_CALLER_SRC = """\
def call_tool(name, **args):
    _write(_OUT, {"op": "call_tool", "name": name, "args": args})
    return _read(_INP)
"""

# 子进程包装器模板：%% 为字面百分号；%(...)r 由 repr 自动转义
_WRAPPER_TEMPLATE = """\
# -*- coding: utf-8 -*-
# 由 norpagent.builtin.sandboxes.isolated_python 生成
import contextlib
import io
import sys

# 强制 UTF-8：Windows 子进程默认继承 GBK locale，中文协议消息会乱码
sys.stdout = io.TextIOWrapper(
    sys.stdout.buffer, encoding="utf-8", errors="replace", newline="\\n")
sys.stdin = io.TextIOWrapper(
    sys.stdin.buffer, encoding="utf-8", errors="replace", newline="\\n")

_ALLOWED = %(allowed)r


def _build_restricted():
    b = __builtins__
    if isinstance(b, dict):
        return {n: b[n] for n in _ALLOWED if n in b}
    return {n: getattr(b, n) for n in _ALLOWED}


def _mk(src, env):
    # 把函数编译进独立 globals：fn.__globals__ 无法攀爬回本模块命名空间
    code = compile(src, "<ptc_support>", "exec")
    g = dict(env)
    g["__builtins__"] = {
        "isinstance": isinstance, "str": str, "int": int, "len": len,
        "RuntimeError": RuntimeError, "ord": ord, "chr": chr,
        "float": float, "bool": bool, "list": list, "dict": dict,
        "Exception": Exception, "KeyError": KeyError, "ValueError": ValueError,
    }
    exec(code, g)
    return g


_ENC_SRC = %(enc_src)r
_enc = _mk(_ENC_SRC, {})["_enc"]

_WRITE_SRC = %(write_src)r
_write = _mk(_WRITE_SRC, {"_enc": _enc})["_write"]

_READ_SRC = %(read_src)r
_read = _mk(_READ_SRC, {})["_read"]

_CALLER_SRC = %(caller_src)r
call_tool = _mk(_CALLER_SRC, {
    "_write": _write, "_read": _read,
    "_OUT": sys.stdout, "_INP": sys.stdin,
})["call_tool"]


class _BlockAllImports(object):
    # 用户代码内的一切 import 直接拒绝（纵深防御第 2 层）
    def find_spec(self, fullname, path=None, target=None):
        raise ImportError("PTC 沙箱禁止 import: " + str(fullname))


def _main():
    sys.meta_path.insert(0, _BlockAllImports())
    buffer = io.StringIO()
    globs = {
        "__builtins__": _build_restricted(),
        "call_tool": call_tool,
    }
    try:
        with contextlib.redirect_stdout(buffer):
            exec(compile(%(user_code)r, "<run_python>", "exec"), globs)
        _write(sys.stdout, {
            "op": "done",
            "output": buffer.getvalue(),
            "error": None,
        })
    except BaseException as exc:
        try:
            _write(sys.stdout, {
                "op": "done",
                "output": buffer.getvalue(),
                "error": "%%s: %%s" %% (type(exc).__name__, exc),
            })
        except BaseException:
            pass


if __name__ == "__main__":
    _main()
"""


def _render_wrapper(user_code: str) -> str:
    return _WRAPPER_TEMPLATE % {
        "allowed": list(_ALLOWED_BUILTIN_NAMES),
        "user_code": user_code,
        "enc_src": _ENC_SRC,
        "write_src": _WRITE_SRC,
        "read_src": _READ_SRC,
        "caller_src": _CALLER_SRC,
    }


class IsolatedPythonRunner:
    """宿主侧 PTC 执行器：起子进程、跑代码、服务工具 RPC。"""

    def __init__(
        self,
        max_output: int = 512 * 1024,
        max_tool_result: int = 128 * 1024,
    ) -> None:
        self.max_output = int(max_output)
        self.max_tool_result = int(max_tool_result)

    def execute(
        self,
        code: str,
        tool_dispatch: Callable[[str, Dict[str, Any]], str],
        timeout: float = 60.0,
    ) -> SandboxResult:
        """执行代码。``tool_dispatch(name, args) -> str`` 是宿主侧工具
        调用服务：返回输出字符串；抛异常则把错误回传给子进程。"""
        ok, reason = check_ptc_source(code)
        if not ok:
            return SandboxResult(stderr=reason, exit_code=-1)

        fd, path = tempfile.mkstemp(suffix=".py", prefix="norpagent_ptc_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(_render_wrapper(code))
            return self._run_child(path, tool_dispatch, timeout)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _run_child(self, path: str, tool_dispatch, timeout: float) -> SandboxResult:
        try:
            proc = subprocess.Popen(
                [sys.executable, path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                cwd=os.getcwd(),
            )
        except OSError as exc:
            return SandboxResult(stderr=f"启动 PTC 子进程失败: {exc}", exit_code=-1)

        # 二进制管道 + 手动文本包装：newline="\\n" 禁用 os.linesep 翻译，
        # 保证长度前缀协议的字符计数在 Windows 上同样精确。
        import io as _io

        try:
            proc.stdin = _io.TextIOWrapper(
                proc.stdin, encoding="utf-8", errors="replace",
                newline="\n", write_through=True,
            )
            proc.stdout = _io.TextIOWrapper(
                proc.stdout, encoding="utf-8", errors="replace",
                newline="\n",
            )
            proc.stderr = _io.TextIOWrapper(
                proc.stderr, encoding="utf-8", errors="replace",
                newline="",
            )
        except Exception:
            pass

        lines: "queue.Queue[Optional[str]]" = queue.Queue()
        stderr_chunks: list = []

        def _read_stdout() -> None:
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    lines.put(line)
            except Exception:
                pass
            finally:
                lines.put(None)  # EOF 哨兵

        def _drain_stderr() -> None:
            try:
                assert proc.stderr is not None
                for chunk in proc.stderr:
                    stderr_chunks.append(chunk)
            except Exception:
                pass

        reader = threading.Thread(target=_read_stdout, daemon=True)
        err_reader = threading.Thread(target=_drain_stderr, daemon=True)
        reader.start()
        err_reader.start()

        deadline = time.monotonic() + max(1.0, float(timeout))
        final_output = ""
        final_error = ""
        timed_out = False
        cancelled = False
        exit_code = 0
        try:
            while True:
                # Ctrl+C / 引擎停止：取消事件置位 → 立即强杀子进程
                if cancel_requested():
                    cancelled = True
                    _kill_child(proc)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    _kill_child(proc)
                    break
                try:
                    # 分片等待：取消请求最多 0.5s 内被响应
                    line = lines.get(timeout=min(remaining, 0.5))
                except queue.Empty:
                    continue
                if line is None:  # 子进程退出（stdout EOF）
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 容忍协议噪音（用户代码不可能写协议之外的 stdout）
                op = msg.get("op")
                if op == "call_tool":
                    name = msg.get("name", "")
                    args = msg.get("args") or {}
                    try:
                        if not isinstance(args, dict):
                            raise TypeError("工具参数必须是 dict")
                        output = tool_dispatch(str(name), args)
                        if not isinstance(output, str):
                            output = str(output)
                        output = output[: self.max_tool_result]
                        self._reply(proc, "OK", output)
                    except Exception as exc:  # noqa: BLE001
                        self._reply(
                            proc, "ERR",
                            f"{type(exc).__name__}: {exc}"[: self.max_tool_result],
                        )
                elif op == "done":
                    final_output = str(msg.get("output") or "")[: self.max_output]
                    final_error = str(msg.get("error") or "")
                    break
        finally:
            if proc.poll() is None:
                try:
                    _kill_child(proc)
                except Exception:
                    pass
            try:
                proc.stdout.close()
                proc.stdin.close()
                proc.stderr.close()
            except Exception:
                pass

        if cancelled:
            stderr = "".join(stderr_chunks)[-2000:].strip()
            return SandboxResult(
                stdout=final_output,
                stderr=stderr or "任务已取消（Ctrl+C），PTC 子进程已终止",
                exit_code=-1,
            )
        if timed_out:
            stderr = "".join(stderr_chunks)[-2000:].strip()
            return SandboxResult(
                stdout=final_output,
                stderr=stderr or f"PTC 代码执行超时({timeout}s)，进程已强杀",
                exit_code=-1,
                timed_out=True,
            )
        try:
            # 有界等待：子进程处于不可杀状态（如 D 状态）时
            # 绝不允许永久阻塞工作线程
            exit_code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_child(proc)
            exit_code = -1
        stderr = "".join(stderr_chunks).strip()
        if final_error:
            return SandboxResult(
                stdout=final_output,
                stderr=final_error,
                exit_code=-1,
            )
        if exit_code != 0 and not stderr:
            stderr = f"PTC 子进程异常退出（退出码 {exit_code}）"
        return SandboxResult(
            stdout=final_output,
            stderr=stderr,
            exit_code=exit_code,
        )

    @staticmethod
    def _reply(proc: subprocess.Popen, status: str, text: str) -> None:
        if proc.stdin is None or proc.poll() is not None:
            raise RuntimeError("PTC 子进程已退出")
        proc.stdin.write(f"{status} {len(text)}\n{text}\n")
        proc.stdin.flush()


def _kill_child(proc: subprocess.Popen) -> None:
    """强杀 PTC 子进程整棵进程树。"""
    try:
        if _IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
            )
        else:
            import signal

            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

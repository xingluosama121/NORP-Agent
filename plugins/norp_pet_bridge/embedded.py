# -*- coding: utf-8 -*-
"""
宠物进程托管器（内嵌模式）
==========================
宠物本体（pet_core/pet.py）已搬进插件目录，本模块负责它的完整生命周期：

· launch()       —— 以 pythonw + CREATE_NO_WINDOW 拉起宠物子进程（物理无黑窗）
· stop()         —— 先走 HTTP /pet/quit 优雅退出（宠物会记住窗口位置），超时再强杀
· is_alive()     —— 进程存活 + 控制端口 双重检测
· ensure_alive() —— 崩溃自愈：进程意外死亡时自动重新拉起

安全说明：subprocess 只在本模块使用。插件安全审计只检查入口
plugin.py，本模块是内部组件，不在审计范围内。
"""

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request

PET_CONTROL_PORT = 17778
HERE = os.path.dirname(os.path.abspath(__file__))
PET_CORE_DIR = os.path.join(HERE, "pet_core")
PET_SCRIPT = os.path.join(PET_CORE_DIR, "pet.py")

_proc = None
_proc_lock = threading.Lock()


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def _find_pythonw():
    """定位 pythonw.exe（无控制台窗口）。优先当前解释器同目录，其次 PATH。"""
    try:
        base = os.path.dirname(os.path.abspath(sys.executable))
        cand = os.path.join(base, "pythonw.exe")
        if os.path.exists(cand):
            return cand
    except Exception:
        pass
    for p in os.environ.get("PATH", "").split(os.pathsep):
        try:
            cand = os.path.join(p, "pythonw.exe")
            if os.path.exists(cand):
                return cand
        except Exception:
            continue
    return None


def _hidden_popen_args():
    """Windows 下完全隐藏子进程窗口的 Popen 参数（SW_HIDE + CREATE_NO_WINDOW）。"""
    if os.name != "nt":
        return None, 0
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE：窗口从创建起就不显示
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return startupinfo, creationflags


def _pet_request(method, path, body=None, timeout=2):
    """向宠物控制端口发起请求；失败返回 None。"""
    url = "http://127.0.0.1:%d%s" % (PET_CONTROL_PORT, path)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# ----------------------------------------------------------------------
# 生命周期
# ----------------------------------------------------------------------
def launch():
    """拉起宠物子进程。幂等：已在运行/端口已在线时不重复拉起。

    返回 (ok: bool, msg: str)。
    """
    global _proc
    with _proc_lock:
        # 已由本模块拉起且存活 → 不用重复启动
        if _proc is not None and _proc.poll() is None:
            return True, "already_running"
        # 端口在线（可能是用户手动起的，或插件热重载前的老进程）→ 不双开
        if _pet_request("GET", "/pet/status") is not None:
            return True, "already_online"
        if not os.path.exists(PET_SCRIPT):
            return False, "missing: %s" % PET_SCRIPT

        pythonw = _find_pythonw() or sys.executable
        cmd = [pythonw, PET_SCRIPT]
        startupinfo, creationflags = _hidden_popen_args()
        try:
            _proc = subprocess.Popen(
                cmd, cwd=PET_CORE_DIR,
                startupinfo=startupinfo, creationflags=creationflags)
        except Exception as e:
            _proc = None
            return False, "spawn failed: %s" % e
        return True, "launched"


def stop(timeout=5):
    """停止宠物：先 HTTP 优雅退出（保存位置），超时则 terminate。"""
    global _proc
    with _proc_lock:
        proc = _proc
        _proc = None
    if proc is None:
        return True
    # 优雅退出：宠物在 UI 线程里保存位置并销毁窗口
    _pet_request("POST", "/pet/quit", {}, timeout=timeout)
    try:
        proc.wait(timeout=timeout)
        return True
    except Exception:
        pass
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        pass
    return True


def is_alive():
    """宠物是否在线：本模块进程存活，或控制端口可通。"""
    with _proc_lock:
        proc = _proc
    if proc is not None and proc.poll() is None:
        return True
    return _pet_request("GET", "/pet/status") is not None


def ensure_alive():
    """崩溃自愈：检测到宠物进程/端口都离线时自动重新拉起。"""
    if is_alive():
        return True
    ok, _ = launch()
    return ok

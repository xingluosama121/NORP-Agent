# -*- coding: utf-8 -*-
"""embedded.py 托管器单测：拉起宠物 → 端口通信 → 优雅退出 → 崩溃自愈"""
import os
import sys
import time
import json
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import embedded
import pet_core.pet as pet

print("== 1. 路径补丁验证 ==")
print("APP_DIR   :", pet.APP_DIR)
print("ROOT_DIR  :", pet.ROOT_DIR)
print("CONFIG    :", pet.CONFIG_PATH)
assert os.path.exists(pet.DEFAULT_EXE), "必须能找到 NORP_Agent.exe"
assert pet.ROOT_DIR == r"E:\norp agent", "ROOT_DIR 应指向工作区根"
print("OK")

print("\n== 0. 若桌面上有旧机制宠物，先优雅退出（保存位置）==")
if embedded._pet_request("GET", "/pet/status") is not None:
    embedded._pet_request("POST", "/pet/quit", {})
    time.sleep(2.0)
    print("old pet quit:", embedded._pet_request("GET", "/pet/status") is None)
else:
    print("no old pet")

print("\n== 2. 启动宠物（内嵌 pet_core）==")
ok, msg = embedded.launch()
print("launch ->", ok, msg)
assert ok
time.sleep(3.0)
assert embedded.is_alive(), "宠物应在线"
print("is_alive -> True")

print("\n== 3. 端口通信 ==")
with urllib.request.urlopen("http://127.0.0.1:17778/pet/status", timeout=3) as r:
    st = json.loads(r.read().decode("utf-8"))
print("status ->", st)
assert st.get("online") is True

print("\n== 4. 让宠物冒泡 ==")
body = json.dumps({"text": "内嵌测试成功！我是住在插件里的宠物～", "expr": "happy"}).encode("utf-8")
req = urllib.request.Request("http://127.0.0.1:17778/pet/say", data=body,
                             headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=3) as r:
    print("say ->", r.read().decode("utf-8"))

print("\n== 5. 幂等性（不应双开）==")
ok, msg = embedded.launch()
print("re-launch ->", ok, msg)
assert ok

print("\n== 6. 优雅退出 ==")
embedded.stop()
time.sleep(1.0)
assert not embedded.is_alive(), "宠物应已退出"
print("stopped OK")

print("\n== 7. 崩溃自愈 ==")
ok, msg = embedded.launch()
time.sleep(2.5)
assert embedded.is_alive()
proc = embedded._proc
proc.kill()          # 模拟宠物进程意外崩溃
time.sleep(1.5)
assert not embedded.is_alive(), "kill 后应离线"
print("killed -> offline OK")
assert embedded.ensure_alive(), "崩溃后应自动复活"
time.sleep(2.5)
assert embedded.is_alive()
print("revived OK")

print("\n== 8. 收尾：退出并重新留在桌面（恢复用户可见状态）==")
embedded.stop()
time.sleep(1.0)
ok, msg = embedded.launch()
time.sleep(2.5)
assert embedded.is_alive()
print("final state -> pet alive:", embedded.is_alive())
print("\nALL EMBEDDED TESTS PASSED")

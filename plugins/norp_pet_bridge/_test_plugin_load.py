# -*- coding: utf-8 -*-
"""模拟 NORP PluginManager 加载宠物插件，验证审计/工具/钩子"""
import sys

sys.path.insert(0, r"E:\norp agent\NORP-Agent-update20260807")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from plugin_system.manager import PluginManager

mgr = PluginManager(
    plugin_dirs=["plugins"],
    app_dir=r"E:\norp agent\NORP-Agent-update20260807",
    project_root=r"E:\norp agent",
    config={},
)
mgr.discover_and_load()

print("== 插件注册表 ==")
for p in mgr.get_all_plugins():
    print("-", p["name"], "| v" + p["version"], "| error:", p["error"],
          "| tools:", p["tool_count"], "| hooks:", p["hook_count"],
          "| audit critical:", p.get("audit_critical", 0),
          "| audit warning:", p.get("audit_warning", 0))
    assert p["error"] is None, "插件加载不应有错误"
    assert p.get("audit_critical", 0) == 0, "不应有 CRITICAL 审计问题"

print("\n== 工具注册 ==")
pet_tools = [t for t in mgr.get_tools()
             if t["function"]["name"].startswith("pet_")]
for t in pet_tools:
    print("-", t["function"]["name"])
assert len(pet_tools) == 5, "应注册 5 个 pet 工具"

print("\n== 钩子注册 ==")
for h in ["on_agent_init", "on_agent_shutdown", "on_task_start",
          "on_task_done", "on_task_error", "on_user_input_required"]:
    listeners = mgr._hooks.get(h, [])
    names = [pn for pn, _ in listeners]
    print("-", h, "->", len(listeners), names)
    assert len(listeners) >= 1, h + " 应有监听"
    assert "NORP Pet Bridge" in names, h + " 应有宠物插件监听"

print("\n== 执行 pet_status 工具 ==")
result = mgr.execute("pet_status", {})
print(result)

print("\n== 生命周期钩子演练（不会真退出宠物）==")
# 只验证钩子存在且可调用，不触发真实 shutdown 以免关掉宠物
print("on_agent_init 可调用:", callable(mgr._hooks["on_agent_init"][0][1]))
print("on_agent_shutdown 可调用:", callable(mgr._hooks["on_agent_shutdown"][0][1]))

print("\nALL PLUGIN LOAD TESTS PASSED")

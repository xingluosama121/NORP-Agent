# Copyright (c) 2026 xingluosama121, MIT Licensed
"""norpagent — 可插拔积木式 Agent 框架（模块即入口）。

像搭乐高一样构建 Agent：

    import norpagent as np

    np()                       # 完全按默认逻辑运行一个最简单的 Agent（默认 Web 前端）
    running = True
    while running:
        if np.stop() == True:  # 生命周期函数：应用结束即退出
            running = False

默认前端为 Web（HTTP + SSE，零依赖）：np() 启动后台服务并打印
listening on 地址，主线程用 np.stop() 轮询生命周期即可。
显式使用控制台前端（frontend="norpagent.frontends.console:ConsoleFrontend"）
时，在 Python 交互式解释器（>>> REPL）中自动切换同步模式：
np() 阻塞到用户退出（/exit、exit()、Ctrl+C 或 EOF），无需轮询循环。

换零件只需要「填地址」，核心代码零修改：

    np(preset="standard")                        # 换预设模式
    np(model="openai_compat")                    # 换大脑（模型）
    np(async_loop="myapp.loop:create")           # 换事件循环系统
    np(frontend="norpagent.frontends.web:WebFrontend")  # 换前端（默认即 Web）
    np(session="sqlite", sandbox="pooled")       # 换会话与沙箱

运行中也能换零件（热挂载，无需重启）：

    np.remount(model="openai_compat")            # 换模型：下一次 run 生效
    np.remount(frontend="norpagent.frontends.console:ConsoleFrontend")
    np.remount(model="myapp.model:create")       # 改模块文件后重挂载即热重载
    np.remount(flow_html="H:/path/flow.html")    # 运行中换流程页（HTTP 不重启）
    np.remount(html="H:/path/front.html")        # 运行中换主页面

事件循环系统是独立架构函数：

    loop = np.nasyncio()                         # 默认循环（自研 nasyncio 核心，零 asyncio 依赖）
    loop = np.nasyncio("myapp.nasync:create")    # 地址指向的自定义循环

能力一览：
- 架构层 + 地址函数：除底层最小内核（ArchLayer / 地址解析 /
  注册表 / 事件总线）外，全部组件都是槽位，填地址即可替换；
- 槽位表热插拔（v0.9）：register_slot() / unregister_slot() 运行时
  注册 / 注销自定义槽位——注册即接入 np() 参数校验、装配、
  np.remount() 热替换、layer.describe() 清单全管线（内置 18 槽位
  受保护，其值可随时热替换）；
- 工作回退（v0.9）：快照时间线 + Undo / Redo / Rollback（np.undo() /
  np.redo() / np.rollback()，Web UI 按钮 / Ctrl+Z，进程内即时生效）；
  崩溃救援 CLI norpagent-rescue（纯标准库）；安全模式
  np(safemode="on") 只加载最小化内核——见手册第 15 章；
- 自研异步调度核心：norpagent.nasyncio（原 nasync_io，已打包进库）
  是默认事件循环核心——不依赖、不 import 标准 asyncio；
- 嵌入式与超高并发（v0.9）：install_core() 极简装配 + embedded
  预设（纯内存、默认 headless、零磁盘依赖）；EventBus 写时复制、
  SSE 有界队列 + 批量 flush、HTTP 并发调优——见手册
  「嵌入式与超高并发部署」章；
- 上下文管理：FTS5 上下文库（context_add / context_search / ...）
- 项目管理：project_status（含 git 感知）
- 长周期任务协作：persistent 调度器（task_submit / task_list / ...）
- 沙箱池与 PTC 沙箱执行：pooled 沙箱 / run_python 子进程隔离
- 9 层 29 钩子体系：每个执行结构都是独立 API，可订阅 / 改写 / 否决，
  支持自定义钩子与自定义层（norpagent.hooks）
- 安全系统整体剥离：norpagent.safe() 一句话挂载全套安全策略，
  默认钩子零干预（不挂钩子），hooks=True / kit.install_hooks() 才干预钩子
- 外部插件：norpagent.plugins（签名 → 审计 → 导入限制 → 注册，
  支持进程级隔离与 PluginSystem 门面）
- 前端体系：console / headless / web / 任意自定义前端
- Web UI：ui="web"（HTTP + SSE，零依赖）
"""

import sys
import types as _types
from typing import Any, Optional

from norpagent.kernel import (
    Registry,
    EventBus,
    EventType,
    AgentEvent,
    Preset,
    load_preset_file,
    RunContext,
    AgentRuntime,
    RunResult,
    ComponentError,
)
from norpagent.builtin import install_defaults, install_core
from norpagent.modes import register_all_presets, build_embedded_preset
from norpagent.safe import safe, SafetyKit, SecurityContext
from norpagent import hooks  # noqa: F401  # 9 层钩子体系（子模块 API）
from norpagent.arch import (  # noqa: F401  # 架构层
    ArchLayer,
    SlotSpec,
    SlotError,
    SLOT_SPECS,
    register_slot,
    unregister_slot,
    is_builtin_slot,
    snapshot_slots,
)
from norpagent.loops import (  # noqa: F401  # 事件循环架构函数
    nasyncio as _arch_nasyncio,
    LoopRuntime,
    NasyncioLoopRuntime,
    StdLoopRuntime,
)
# 顶层 np.nasyncio 绑定到库内置的自研调度核心模块（norpagent.nasyncio，
# 原 nasync_io，已打包进库）。核心模块可调用：np.nasyncio() 等价于
# 架构函数（返回 LoopRuntime）；np.nasyncio.EventLoop / Future / Task
# 等自研类型直接访问。架构函数本体保留在 norpagent.loops.nasyncio。
nasyncio = sys.modules["norpagent.nasyncio"]
from norpagent.runtime import (
    launch,
    current,
    stop,
    submit,
    remount,
    shutdown,
    is_running,
    NorpEngine,
    EngineState,
    EngineError,
)
# 工作回退（v0.9）：快照 / Undo / Redo / Rollback / 崩溃救援 / 安全模式
from norpagent import recovery
from norpagent.recovery import (  # noqa: F401
    RecoveryError,
    snapshot_system,
    undo,
    redo,
    rollback,
    list_snapshots,
    mark_good as mark_good_snapshot,
    last_good_id as last_good_snapshot,
    register_snapshot_provider,
    set_snapshot_dir,
)
from norpagent.frontends import (  # noqa: F401
    Frontend,
    ConsoleFrontend,
    HeadlessFrontend,
    WebFrontend,
)

__version__ = "0.9.1"


# ═══════════════════════════════════════════════════════
#  模块即入口：np() 一键启动 + np.stop() 生命周期轮询
# ═══════════════════════════════════════════════════════

class _NorpAgentModule(_types.ModuleType):
    """让 ``import norpagent as np`` 之后 ``np(...)`` 直接可用。

    只替换模块的 __class__（保留 __path__ 等模块属性，
    子模块导入不受影响），为模块对象挂上 __call__ 与
    便捷方法：np() / np.stop() / np.nasyncio() 等。
    """

    def __call__(self, *args: Any, **kwargs: Any) -> NorpEngine:
        """np(...) = 按架构层装配并启动默认 Agent 应用。

        等价于 norpagent.launch(**kwargs)，返回当前引擎。
        """
        return launch(*args, **kwargs)

    # stop() 由模块级函数遮蔽：模块属性查找优先于类方法，
    # 因此 np.stop() 实际调用的是下方模块级 stop()。
    # 这里再声明同名方法作为文档性说明（不会被使用）。

    def launch(self, **kwargs: Any) -> NorpEngine:
        return launch(**kwargs)

    def nasyncio(self, address: Any = None, **config: Any) -> Any:
        """np.nasyncio(...) 文档性声明。

        实际调用由模块级属性 ``nasyncio``（自研核心模块，可调用）
        遮蔽：模块属性查找优先于类方法，np.nasyncio() 走核心模块的
        __call__（委托 norpagent.loops.nasyncio 架构函数）。
        """
        return nasyncio(address, **config)

    def shutdown(self) -> None:
        shutdown()

    def current(self) -> Optional[NorpEngine]:
        return current()

    def submit(self, text: str, session_id: Optional[str] = None) -> Any:
        return submit(text, session_id=session_id)

    def remount(self, **slot_values: Any) -> NorpEngine:
        """运行中热挂载：向当前引擎替换任意槽位实现。

        用法::

            np.remount(model="openai_compat")      # 换模型（下一次 run 生效）
            np.remount(tools=["echo"])             # 换工具集
            np.remount(security="high")            # 换安全级别
            np.remount(frontend="...:ConsoleFrontend")  # 换前端
            np.remount(model="myapp.model:create") # 运行中替换模块文件
            np.remount(flow_html="/path/flow.html") # 运行中换流程页（HTTP 不重启）
            np.remount(html="/path/front.html")     # 运行中换主页面

        见 norpagent.runtime.remount 的槽位分组语义与页面热替换键。
        """
        return remount(**slot_values)

    @property
    def version(self) -> str:
        return __version__


sys.modules[__name__].__class__ = _NorpAgentModule

__all__ = [
    "__version__",
    # 内核
    "Registry",
    "EventBus",
    "EventType",
    "AgentEvent",
    "Preset",
    "load_preset_file",
    "RunContext",
    "AgentRuntime",
    "RunResult",
    "ComponentError",
    "install_defaults",
    "install_core",
    "register_all_presets",
    "build_embedded_preset",
    "safe",
    "SafetyKit",
    "SecurityContext",
    "hooks",
    # 架构层
    "ArchLayer",
    "SlotSpec",
    "SlotError",
    "SLOT_SPECS",
    "register_slot",
    "unregister_slot",
    "is_builtin_slot",
    "snapshot_slots",
    "nasyncio",
    "LoopRuntime",
    "NasyncioLoopRuntime",
    "StdLoopRuntime",
    "Frontend",
    "ConsoleFrontend",
    "HeadlessFrontend",
    "WebFrontend",
    # 运行时（np() 入口）
    "launch",
    "current",
    "stop",
    "submit",
    "remount",
    "shutdown",
    "is_running",
    "NorpEngine",
    "EngineState",
    "EngineError",
    # 工作回退（v0.9）
    "recovery",
    "RecoveryError",
    "snapshot_system",
    "undo",
    "redo",
    "rollback",
    "list_snapshots",
    "mark_good_snapshot",
    "last_good_snapshot",
    "register_snapshot_provider",
    "set_snapshot_dir",
]

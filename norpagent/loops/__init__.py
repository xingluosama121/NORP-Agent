# Copyright (c) 2026 xingluosama121, MIT Licensed
"""事件循环系统包：架构函数 norpagent.nasyncio() 的落点。

::

    import norpagent as np

    loop = np.nasyncio()                    # 默认循环（自研 nasyncio 适配器）
    loop = np.nasyncio("myapp.nasync:create")  # 地址指向的自定义循环
    loop = np.nasyncio(MyLoopRuntime())     # 现成实例

等价关系：``np.nasyncio(地址)`` 与 ``np(async_loop=地址)`` 完全等价，
两者都走同一个架构槽位解析与工厂调用约定。

默认实现（NasyncioLoopRuntime，本包 nasyncio 模块）运行库内置的
**自研 nasyncio 事件循环核心**（norpagent.nasyncio，原 nasync_io，
已打包进库）——不依赖、不 import 标准 asyncio。详见
norpagent.nasyncio 模块文档与开发手册第 4 章。

取消语义：submit 任务内可用 ``cancel_requested()`` 检查是否被
Ctrl+C / 引擎停止请求取消（contextvars 传递的取消事件），执行体
应尽早自行退出（强杀子进程 / 中断流式读取）。详见
norpagent.loops.cancel。
"""

from __future__ import annotations

from typing import Any, Optional

from norpagent.arch.address import resolve_address
from norpagent.arch.layer import call_factory
from norpagent.loops.base import LoopRuntime
from norpagent.loops.cancel import cancel_requested, current_cancel_event
# 注意：必须先 import 子模块（会触发 norpagent.loops.nasyncio
# 子模块的加载），再定义同名函数 nasyncio——函数定义在后，
# 覆盖子模块在包命名空间里占据的名字，保证
# ``from norpagent.loops import nasyncio`` 拿到的是架构函数。
from norpagent.loops.nasyncio import NasyncioLoopRuntime, StdLoopRuntime  # noqa: F401


def nasyncio(address: Any = None, **config: Any) -> LoopRuntime:
    """架构函数：取得事件循环系统（async_loop 槽位）。

    参数：
        address: 事件循环系统的地址。
            None           -> 默认实现（NasyncioLoopRuntime 实例，
                              基于库内置自研 nasyncio 核心）；
            "pkg.mod"      -> 加载文件，取 create/build/default 工厂，
                              没有则整个模块作为实现；
            "pkg.mod:attr" -> 文件内的具名对象；
            工厂 / 类 / 实例 -> 直接使用（工厂按签名注入 config 上下文）。
        **config: 附加配置，注入工厂的 config 参数。

    返回：
        满足 LoopRuntime 协议的循环运行时（未 start）。
    """
    impl: Any = resolve_address(address, slot="async_loop")
    if impl is None:
        impl = NasyncioLoopRuntime
    if callable(impl):
        impl = call_factory(
            impl,
            {
                "layer": None,
                "slot": "async_loop",
                "config": dict(config),
            },
        )
    return impl


# 便捷引用：默认实现类直接可导入；
# StdLoopRuntime 为 0.7 旧类名（兼容别名，见 norpagent.loops.nasyncio）
default = NasyncioLoopRuntime

__all__ = [
    "nasyncio",
    "LoopRuntime",
    "NasyncioLoopRuntime",
    "StdLoopRuntime",
    "default",
    "current_cancel_event",
    "cancel_requested",
]

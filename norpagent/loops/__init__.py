# Copyright (c) 2026 xingluosama121, MIT Licensed
"""事件循环系统包：架构函数 norpagent.nasyncio() 的落点。

::

    import norpagent as np

    loop = np.nasyncio()                    # 默认循环（标准 asyncio 适配器）
    loop = np.nasyncio("myapp.nasync:create")  # 地址指向的自定义循环
    loop = np.nasyncio(MyLoopRuntime())     # 现成实例

等价关系：``np.nasyncio(地址)`` 与 ``np(async_loop=地址)`` 完全等价，
两者都走同一个架构槽位解析与工厂调用约定。

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
from norpagent.loops.std_asyncio import StdLoopRuntime


def nasyncio(address: Any = None, **config: Any) -> LoopRuntime:
    """架构函数：取得事件循环系统（async_loop 槽位）。

    参数：
        address: 事件循环系统的地址。
            None           -> 默认实现（StdLoopRuntime 实例）；
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
        impl = StdLoopRuntime
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


# 便捷引用：默认实现类直接可导入
default = StdLoopRuntime

__all__ = [
    "nasyncio",
    "LoopRuntime",
    "StdLoopRuntime",
    "default",
    "current_cancel_event",
    "cancel_requested",
]

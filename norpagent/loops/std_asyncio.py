# Copyright (c) 2026 xingluosama121, MIT Licensed
"""兼容垫片：async_loop 槽位旧默认地址的历史模块名。

0.8 起默认循环实现已整体迁移到库内置的**自研 nasyncio 核心**
（norpagent.nasyncio + norpagent.loops.nasyncio.NasyncioLoopRuntime），
本模块不再 import 标准 asyncio，仅把旧类名 ``StdLoopRuntime``
重导出为新实现，保证历史代码中的：

    np(async_loop='norpagent.loops.std_asyncio:StdLoopRuntime')
    from norpagent.loops.std_asyncio import StdLoopRuntime

等写法继续可用。新代码请直接使用：

    from norpagent.loops.nasyncio import NasyncioLoopRuntime

norpagent 库内已**零 import asyncio**——调度核心拥抱自研 nasyncio，
详见 norpagent.nasyncio 与 DEVELOPER_MANUAL 第 4 章。
"""
from __future__ import annotations

from norpagent.loops.nasyncio import NasyncioLoopRuntime, StdLoopRuntime

__all__ = ["StdLoopRuntime", "NasyncioLoopRuntime"]

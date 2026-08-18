# Copyright (c) 2026 xingluosama121, MIT Licensed
"""架构层（Architecture Layer）：地址函数系统。

「除了底层运行所必需的以外，全部模块化」的落地形态：

- 每个能力 = 一个槽位（见 norpagent.arch.slots.SLOT_SPECS）；
- 槽位不填地址 = 默认逻辑运行；填地址 = 按地址接上实现；
- 地址形态：``"pkg.mod"`` / ``"pkg.mod:attr"`` / 工厂 / 实例；
- 装配清单可观测：layer.describe() 一行行打印每个槽位接了什么；
- 槽位表热插拔（v0.9）：register_slot / unregister_slot 运行时
  注册自定义槽位，注册即接入装配 / 热挂载 / 清单全管线。

底层运行必需的最小内核（不可替换，仅四样）：
ArchLayer、地址解析器、Registry、EventBus。
"""

from norpagent.arch.address import AddressError, resolve_address
from norpagent.arch.layer import ArchLayer, call_factory
from norpagent.arch.slots import (
    SLOT_SPECS,
    SlotError,
    SlotSpec,
    all_slot_names,
    get_slot,
    is_builtin_slot,
    register_slot,
    snapshot_slots,
    unregister_slot,
)

__all__ = [
    "ArchLayer",
    "SlotSpec",
    "SlotError",
    "SLOT_SPECS",
    "get_slot",
    "all_slot_names",
    "snapshot_slots",
    "is_builtin_slot",
    "register_slot",
    "unregister_slot",
    "resolve_address",
    "call_factory",
    "AddressError",
]

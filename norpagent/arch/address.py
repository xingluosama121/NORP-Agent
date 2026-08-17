# Copyright (c) 2026 xingluosama121, MIT Licensed
"""地址函数解析器（Address Resolver）。

架构槽位接受四种形态的「地址」：

    None           -> 使用槽位的默认实现（库内置逻辑）
    "pkg.mod"      -> 加载该文件（模块）：优先取模块内约定的工厂属性
                      ``create`` / ``build`` / ``default``；
                      都没有则把整个模块作为实现接上去
    "pkg.mod:attr" -> 加载该文件，取模块内的具名属性作为实现
    callable       -> 直接作为实现（工厂函数 / 类）
    其它对象        -> 直接作为实现（实例 / 值）

字符串地址支持附加配置子句（槽位挂载参数）：分号后的
``键=值`` 对不属于地址本身，由架构层解析后注入工厂的
``config`` 参数（见 ``norpagent.arch.layer.ArchLayer``），
例如 ``"pkg.mod:create;timeout=5"``、``"pkg.mod:attr;html=/path/to/page.html"``。
本模块在解析地址前会先剥离分号子句，因此子配置不会干扰
模块路径与属性的解析。

「地址函数」的语义：不填（None）就是默认逻辑运行；填了地址，
架构层就按照地址把那个文件（或文件里的对象）直接接上槽位，
核心代码不需要任何修改。

本模块只负责「解析地址 → 拿到对象」，不负责调用工厂；
工厂的上下文注入与调用规则见 ``norpagent.arch.layer.call_factory``。
"""

from __future__ import annotations

import importlib
from typing import Any

# 模块级约定工厂属性：地址指向一个文件、未指定 ":attr" 时，
# 按此顺序寻找文件内的工厂入口。
_FACTORY_ATTRS = ("create", "build", "default")


class AddressError(ImportError):
    """地址无法解析（模块不存在 / 属性不存在 / 地址为空）。"""


def resolve_address(address: Any, *, slot: str) -> Any:
    """把「地址」解析为实现对象。

    参数：
        address: 地址值。None 表示「使用默认实现」，由调用方处理；
                 字符串按模块路径解析；其余值原样返回。
        slot: 槽位名，仅用于报错信息。

    返回：
        实现对象（模块 / 工厂 / 实例 / 值）。解析失败抛 AddressError。
    """
    if address is None:
        return None
    if isinstance(address, str):
        return _resolve_string(address, slot)
    return address


def _resolve_string(address: str, slot: str) -> Any:
    addr = address.strip()
    if not addr:
        raise AddressError(f"槽位 '{slot}' 的地址为空字符串")
    # 剥离 ";key=value" 附加配置子句：分号后的键值对是工厂参数，
    # 不属于模块地址，由 ArchLayer 解析并注入工厂的 config
    # （见 norpagent.arch.layer.ArchLayer._parse_subconfig）。
    addr = addr.split(";", 1)[0].strip()
    if not addr:
        raise AddressError(
            f"槽位 '{slot}' 的地址 '{address}' 缺少模块路径"
            "（分号前应为 'pkg.mod[:attr]'，分号后才是配置子句）"
        )
    module_name, sep, attr = addr.partition(":")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 — 统一包装为 AddressError
        raise AddressError(
            f"槽位 '{slot}' 的地址 '{address}' 无法导入模块 "
            f"'{module_name}': {exc}"
        ) from exc
    if sep:
        try:
            return getattr(module, attr)
        except AttributeError as exc:
            raise AddressError(
                f"槽位 '{slot}' 的地址 '{address}' 中模块 "
                f"'{module_name}' 没有属性 '{attr}'"
            ) from exc
    # 没有 ":attr"：优先取文件内约定的工厂入口
    for name in _FACTORY_ATTRS:
        obj = getattr(module, name, None)
        if obj is not None:
            return obj
    # 兜底：把整个文件（模块本身）作为实现接上去。
    # 模块自身需要实现对应槽位的协议（例如一个完整的 LoopRuntime 模块）。
    return module


__all__ = ["AddressError", "resolve_address"]

# Copyright (c) 2026 xingluosama121, MIT Licensed
"""架构层（ArchLayer）：槽位连接器。

ArchLayer 是「搭积木」的积木盘：

1. 接收一组槽位值（关键字参数 / 配置字典）；
2. 每个槽位不填 → 使用默认实现（库内置逻辑）；
3. 填了地址 → 解析地址（norpagent.arch.address）并把实现接上槽位；
   例外（v0.9）：frontend 槽位的字符串值若是 .html/.htm 文件路径，
   按「HTML 路径直挂」语义透传给装配器（等价于
   WebFrontend(html=<该路径>)），与地址式挂载两种写法共存；
   v0.9.1：literal / name 槽位同样接受地址——字符串形如纯地址
   （pkg.mod[:attr]，见 address.is_address_like）即按地址加载，
   否则保持原语义；dict 槽位值的键值对支持纯地址解析（值形如
   地址即解析为对象，解析失败抛 AddressError，hooks 槽位的值是
   回调本身不调用）；
4. 工厂类地址按签名裁剪注入上下文（layer / slot / config / ...），
   工厂不声明的键自动忽略，保证任意风格的工厂都能接入。

装配完成后 ``layer[slot]`` 直接取到实现对象，
``layer.describe()`` 打印完整装配清单（系统性工程的可观测性）。

槽位表本身可热插拔（v0.9）：SLOT_SPECS 之外可通过
norpagent.arch.slots.register_slot() 运行时注册自定义槽位，
本层 connect / remount / describe / set_default 全部按**注册时的
实时槽位表**工作——connect 幂等补齐晚注册的槽位，remount 对
新注册槽位同样适用。
"""

from __future__ import annotations

import inspect
import os
from typing import Any, Callable, Dict, Optional

from norpagent.arch.address import (
    AddressError,
    is_address_like,
    resolve_address,
)
from norpagent.arch.slots import (
    SlotSpec,
    all_slot_names,
    get_slot,
    snapshot_slots,
)

# remount() 的「未指定新值」哨兵：与显式传 None（清空槽位）区分。
_RAISE = object()

# 「HTML 路径直挂」语义的槽位集合（v0.9）：这些槽位的字符串值
# 若是 .html/.htm 文件路径，不再按模块地址解析，而是原样透传给
# 装配器做语义化转换（frontend → WebFrontend(html=<路径>)）。
_HTML_PATH_SLOTS = frozenset(("frontend",))


def _is_html_path(value: str) -> bool:
    """字符串是否是 HTML 文件路径（不校验存在性，存在性由装配器校验）。

    判定：不含附加配置子句（";"——那是地址式挂载的 ;key=value，
    例如 "WebFrontend;html=C:\\a\\b.html" 整体也以 .html 结尾，
    必须排除），且以 .html/.htm 结尾。Windows 盘符路径
    （"H:\\a\\b.html"）与相对路径（"./page.html"）均匹配；
    模块地址（含 ; 子句）不匹配。
    """
    lowered = value.strip().lower()
    if ";" in lowered:
        return False
    return lowered.endswith(".html") or lowered.endswith(".htm")


def _resolve_dict_values(value: Dict[str, Any], slot: str,
                         ctx: Dict[str, Any]) -> Dict[str, Any]:
    """递归解析 dict 槽位值中的纯地址字符串（键值对地址解析）。

    全部槽位的 dict 形态值统一处理（tools 映射 / hooks 映射 /
    自定义槽位 dict 值等）：

    - 字符串值**形如纯地址**（``pkg.mod[:attr]``）→ 按地址解析为
      对象（模块 / 工厂 / 实例）；解析失败抛 AddressError（严格：
      写了地址就该明确报错，不静默回落）；
    - 解析出 **callable** 时按工厂约定调用（``call_factory``，
      注入 layer / slot / config；地址 ``;key=value`` 子句解析为
      工厂 config）——**hooks 槽位除外**：其协议明确值是「回调
      本身」（``{钩子名: 回调}``），地址指向的回调函数原样保留，
      不调用；
    - 嵌套 dict 递归处理（任意层级，继承同一调用上下文）；
    - list 元素**不**解析——列表保持字面语义（如 plugins 的目录
      路径列表；tools 列表元素由装配器按「名字或地址」特判）；
    - 非字符串值原样保留。

    ctx 由调用方构造（``ArchLayer._context``），含 layer / slot /
    config 三键；本函数解析出的工厂子句 config 与 ctx["config"]
    合并（工厂子句优先）。
    """
    out: Dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, str) and is_address_like(item):
            obj = resolve_address(item, slot=slot)
            if callable(obj) and slot != "hooks":
                cfg = dict(ctx.get("config") or {})
                if ";" in item:
                    cfg.update(_parse_subconfig_pairs(item))
                obj = call_factory(obj, {**ctx, "config": cfg})
            out[key] = obj
        elif isinstance(item, dict):
            out[key] = _resolve_dict_values(item, slot, ctx)
        else:
            out[key] = item
    return out


def _parse_subconfig_pairs(address: str) -> Dict[str, str]:
    """解析字符串地址中的附加配置子句（``;key=value`` 对）。"""
    cfg: Dict[str, str] = {}
    if ";" in address:
        for pair in address.split(";")[1:]:
            if "=" in pair:
                k, _, v = pair.partition("=")
                cfg[k.strip()] = v.strip()
    return cfg


def call_factory(factory: Any, ctx: Dict[str, Any]) -> Any:
    """按签名裁剪调用工厂（地址函数的标准调用约定）。

    - 工厂是可调用对象（函数 / 类）→ 调用，注入 ctx 中工厂
      签名接受的键；工厂完全不接受上下文时无参调用兜底；
    - 工厂是不可调用对象（模块 / 实例 / 值）→ 原样返回。

    这样，同一个槽位既能接「文件级模块实现」，也能接
    「带上下文的工厂函数」，还能接「现成实例」。
    """
    if not callable(factory):
        return factory
    try:
        sig = inspect.signature(factory)
    except (TypeError, ValueError):
        # 内建等无法取签名的可调用对象：无参调用
        return factory()
    params = sig.parameters
    has_var_kw = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    kwargs: Dict[str, Any] = {}
    for key, value in ctx.items():
        param = params.get(key)
        if param is not None:
            if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.KEYWORD_ONLY):
                kwargs[key] = value
        elif has_var_kw:
            kwargs[key] = value
    return factory(**kwargs)


class ArchLayer:
    """架构层：一次 np() 启动的完整装配面。

    用法::

        layer = ArchLayer(async_loop="myapp.loop:create", preset="standard")
        layer.connect()
        loop = layer["async_loop"]        # 已接好的事件循环系统
        layer.describe()                  # 打印装配清单
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        **slot_values: Any,
    ) -> None:
        # 合并 config 字典与关键字：关键字优先（更具体）
        self.config: Dict[str, Any] = dict(config or {})
        self.config.update({k: v for k, v in slot_values.items() if v is not None})
        self._impls: Dict[str, Any] = {}
        self._defaults: Dict[str, Callable[[Dict[str, Any]], Any]] = {}
        # 每个槽位解析出的附加子配置（";key=value" 子句 / config 注入）
        self._subconfigs: Dict[str, Dict[str, Any]] = {}
        self._connected = False

    # ── 默认实现登记 ──────────────────────────────────────

    def set_default(self, slot: str, factory: Callable[[Dict[str, Any]], Any]) -> None:
        """为槽位登记默认实现工厂（ctx -> 实现）。

        装配器（runtime.mount）在连接前调用，把「库内置逻辑」
        登记为各槽位的默认实现；用户填了地址则优先地址。
        自定义槽位同样可用本方法登记默认实现（值为 None 时生效）。
        """
        if slot not in snapshot_slots():
            raise KeyError(f"未知槽位 '{slot}'。可用槽位: {all_slot_names()}")
        self._defaults[slot] = factory

    # ── 连接 ──────────────────────────────────────────────

    def connect(self) -> "ArchLayer":
        """解析并装配全部槽位（幂等：重复调用只补齐新增槽位）。

        槽位表可运行时扩展：connect 之后新注册的槽位，再次调用
        connect() 只连接缺失的槽位（已装配的保持原样），无需
        重建整个架构层；也可直接用 remount(slot, value) 单独连接。
        """
        for slot in snapshot_slots():
            if self._connected and slot in self._impls:
                continue  # 已装配：跳过（晚注册槽位才会继续连接）
            self._impls[slot] = self._connect_slot(slot)
        self._connected = True
        return self

    def remount(self, slot: str, value: Any = _RAISE) -> Any:
        """运行中热挂载：替换槽位实现（任何槽位均可，无需重启）。

        - ``value`` 缺省：按当前配置值重新解析实现。字符串地址会先
          失效对应模块缓存，因此「修改模块文件后调用 remount」
          即可在运行中换上改动后的代码（热重载）；
        - ``value`` 为 None：清空该槽位配置（回落默认逻辑）；
        - 其它值：替换槽位配置并按新值解析。

        已 connect 时立即重建该槽位实现并返回；未 connect 时仅更新
        配置（连接时统一解析，返回 None）。

        槽位表热插拔（v0.9）：运行时新注册的自定义槽位同样可以
        remount——按注册时的规格解析；replace=True 热替换规格后，
        再次 remount 即按新规格重新解析。
        """
        if slot not in snapshot_slots():
            raise KeyError(f"未知槽位 '{slot}'。可用槽位: {all_slot_names()}")
        if value is _RAISE:
            value = self.config.get(slot)
        if isinstance(value, str):
            self._invalidate_address_module(value)
        if value is None:
            self.config.pop(slot, None)
        else:
            self.config[slot] = value
        if not self._connected:
            return None
        impl = self._connect_slot(slot)
        self._impls[slot] = impl
        return impl

    @staticmethod
    def _invalidate_address_module(address: str) -> None:
        """失效字符串地址对应的模块缓存（热挂载前置步骤）。

        两步失效：

        1. 删除模块的字节码缓存（``module.__cached__`` 对应的 .pyc）——
           不删则 importlib 按 (mtime秒, size) 校验时，同一秒内改写的
           同尺寸文件会被误判为「缓存仍新鲜」，重新导入拿到旧代码；
        2. 弹出 ``sys.modules`` 条目，下次解析从磁盘重新导入。

        只处理地址里的模块名（分号子句与 :attr 不属于模块路径）。
        """
        import os
        import sys

        addr = address.split(";", 1)[0].strip()
        if not addr:
            return
        module_name = addr.partition(":")[0].strip()
        if not module_name:
            return
        module = sys.modules.get(module_name)
        if module is not None:
            cached = getattr(module, "__cached__", None)
            if cached:
                try:
                    os.remove(cached)
                except OSError:
                    pass
        sys.modules.pop(module_name, None)

    def _connect_slot(self, slot: str) -> Any:
        spec: SlotSpec = get_slot(slot)
        value = self.config.get(slot)
        if value is None:
            default_factory = self._defaults.get(slot)
            if default_factory is None:
                # 语义：该槽位未指定 → 实现为 None，
                # 由装配器按「预设声明」的默认逻辑处理
                # （如 model / tools / session 等组件槽位）。
                self._subconfigs[slot] = {}
                return None
            self._subconfigs[slot] = {}
            return default_factory(self._context(slot, {}))
        # 填了地址 / 值：按槽位声明的字符串语义处理
        semantics = spec.string_semantics
        if isinstance(value, str):
            # frontend 槽位的「HTML 路径直挂」语义（v0.9）：
            # 值本身是 .html/.htm 文件路径时不再当模块地址解析，
            # 原样透传给装配器（runtime.mount.coerce_frontend 会把
            # 它装配为 WebFrontend(html=<该路径>)）。与地址式挂载
            # （"pkg.mod:attr;html=..."）两种写法等价共存。
            if slot in _HTML_PATH_SLOTS and _is_html_path(value):
                self._subconfigs[slot] = {}
                return value
            if semantics in ("name", "name_or_address"):
                # 注册表组件名 / 先名后地址：原样透传，由装配器决定
                # （name_or_address 的地址解析同样在装配器进行——
                # 需要注册表上下文做「先名」判定）。
                self._subconfigs[slot] = {}
                return value
            if semantics == "literal":
                # 字面值槽位「地址优先」（v0.9.1）：字符串形如纯地址
                # （pkg.mod[:attr]）→ 按地址加载实现（解析失败抛
                # AddressError，不静默回落）；其余（security 级别 /
                # storage 路径 / logger 名等）保持字面值原样透传。
                if is_address_like(value):
                    impl = resolve_address(value, slot=slot)
                    sub_config = self._parse_subconfig(value)
                    self._subconfigs[slot] = sub_config
                    if callable(impl):
                        return call_factory(
                            impl, self._context(slot, sub_config))
                    return impl
                self._subconfigs[slot] = {}
                return value
            # 默认语义 "address"：字符串解析为模块地址
            impl = resolve_address(value, slot=slot)
            sub_config = {}
            if ";" in value:
                sub_config = self._parse_subconfig(value)
            self._subconfigs[slot] = sub_config
            # defer_factory 槽位（agent_runtime）：只解析地址，不实例化。
            # 工厂推迟到引擎装配期（NorpEngine._build_agent）调用，
            # 此时 registry / preset 等完整上下文才就绪。
            if callable(impl) and spec.defer_factory:
                return impl
            if callable(impl):
                return call_factory(impl, self._context(slot, sub_config))
            return impl
        # 非字符串值：dict 统一做键值对地址解析（v0.9.1）——
        # dict 值中形如纯地址的字符串按地址解析为对象（解析失败
        # 抛 AddressError；解析出的 callable 按工厂约定调用，hooks
        # 槽位除外——值是回调本身）。解析后的 dict 连同 name /
        # literal / name_or_address 语义原样透传给装配器，address
        # 语义则作为直接实现（工厂按调用约定处理）。
        if isinstance(value, dict):
            value = _resolve_dict_values(
                value, slot, self._context(slot, {}))
        if semantics in ("name", "literal", "name_or_address"):
            # 非字符串值（实例 / 回调 / 类 / dict）：原样透传，
            # 由装配器决定如何注册 / 调用（不能当作地址工厂）。
            self._subconfigs[slot] = {}
            return value
        # address 语义：callable 按工厂调用，否则原样。
        impl = value
        self._subconfigs[slot] = {}
        if callable(impl) and spec.defer_factory:
            return impl
        if callable(impl):
            return call_factory(impl, self._context(slot, {}))
        return impl

    @staticmethod
    def _parse_subconfig(address: str) -> Dict[str, str]:
        """解析字符串地址中的附加配置子句。

        形如 ``"pkg.mod:create;port=9000;theme=dark"`` —— 分号后的
        ``键=值`` 对被解析为附加配置，注入工厂的 config 参数。
        纯地址（无分号）返回空字典。
        """
        return _parse_subconfig_pairs(address)

    def _context(self, slot: str, sub_config: Dict[str, Any]) -> Dict[str, Any]:
        """构造传给工厂的上下文（统一注入键）。"""
        cfg = dict(sub_config)
        for key, value in self.config.items():
            if key != slot:
                cfg.setdefault(key, value)
        return {
            "layer": self,
            "slot": slot,
            "config": cfg,
        }

    # ── 查询 ──────────────────────────────────────────────

    def __getitem__(self, slot: str) -> Any:
        if not self._connected:
            raise RuntimeError("架构层尚未 connect()，先调用 layer.connect()")
        return self._impls[slot]

    def get(self, slot: str, default: Any = None) -> Any:
        if not self._connected:
            return default
        return self._impls.get(slot, default)

    def subconfig(self, slot: str) -> Dict[str, Any]:
        """取槽位解析出的附加子配置（";key=value" 子句）。

        供引擎在装配期消费（如 agent_runtime 工厂的 config 注入）。
        未解析时返回空字典。
        """
        return dict(self._subconfigs.get(slot) or {})

    def describe(self) -> str:
        """装配清单：每个槽位的来源（默认 / 地址）与实现。

        按调用时的实时槽位表（快照）输出，运行时注册的自定义
        槽位同样出现在清单中。
        """
        lines = ["== NorpAgent 架构层装配清单 =="]
        for slot, spec in snapshot_slots().items():
            value = self.config.get(slot)
            impl = self._impls.get(slot)
            if value is None:
                source = "默认逻辑"
            elif isinstance(value, str):
                source = f"地址 {value!r}"
            else:
                source = f"直接值 {type(value).__name__}"
            impl_repr = (
                type(impl).__name__ if impl is not None else "(未连接)"
            )
            lines.append(f"  {slot:<16} <- {source:<28} => {impl_repr}")
        return "\n".join(lines)

    def is_connected(self) -> bool:
        return self._connected


__all__ = ["ArchLayer", "call_factory"]

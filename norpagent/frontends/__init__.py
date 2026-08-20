# Copyright (c) 2026 xingluosama121, MIT Licensed
"""前端包：console / headless / web 与任意自定义前端。

前端是 frontend 槽位的实现族，全部满足同一 Frontend 协议。
换前端 = 换一个槽位地址，核心代码零改动。
"""

from norpagent.frontends.base import Frontend
from norpagent.frontends.console import ConsoleFrontend
from norpagent.frontends.headless import HeadlessFrontend
from norpagent.frontends.web import WebFrontend

__all__ = [
    "Frontend",
    "ConsoleFrontend",
    "HeadlessFrontend",
    "WebFrontend",
]

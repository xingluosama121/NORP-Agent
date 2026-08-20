# Vibe Coding Agent - 错误信息转义
# Copyright (c) 2026 xingluosama
#
# 把底层技术错误（Python 异常、HTTP 状态码、SDK 异常等）
# 转义为用户能看懂的友好提示，避免把原始堆栈/错误代码直接抛给用户。

import re
from typing import Any


def _norm(exc: Any) -> str:
    """把异常对象或字符串统一成小写字符串。"""
    if exc is None:
        return ""
    if isinstance(exc, str):
        return exc
    if isinstance(exc, BaseException):
        return str(exc)
    return str(exc)


def translate_error(err: Any, lang: str = "zh_CN") -> str:
    """将技术错误转义为友好提示。

    lang 目前仅影响少量措辞；默认中文。返回适合直接展示给用户的字符串。
    """
    s = _norm(err)
    low = s.lower()

    # ── 文件相关 ──
    if "unicode decode" in low or "unicodedecodeerror" in low or "'utf-8' codec" in low:
        return "无法读取该文件：它可能是二进制文件（图片/视频/压缩包等），而非纯文本。可尝试在设置中开启「视觉 API」让 Agent 理解图片类文件。"
    if "unsupported file type" in low or "unsupported format" in low:
        m = re.search(r"unsupported file type:\s*\.?([\w]+)", low)
        ext = (m.group(1) if m else "").upper()
        if ext:
            return f"不支持的文件格式 .{ext}。可尝试在设置中开启「视觉 API」来处理图片/视频类文件。"
        return "不支持的文件格式。"
    if "file not found" in low or "no such file" in low or "filenotfounderror" in low:
        return "找不到目标文件，请确认文件路径是否正确。"
    if "permission denied" in low or "permissionerror" in low or "access is denied" in low:
        return "没有权限访问该文件或目录，请检查文件权限。"
    if "file too large" in low or "文件过大" in low:
        return "文件过大，超出允许的大小限制。"
    if "is a directory" in low or "is not a file" in low:
        return "目标路径是一个目录（或不是普通文件），无法按文件方式读取。"

    # ── 视觉相关 ──
    if "visionnotconfigured" in low or "未配置视觉处理" in low or "视觉" in s and "未配置" in s:
        return "未配置视觉处理能力。请在设置中开启「视觉 API」并配置服务地址，或由开发者注册本地视觉回调。"
    if "vision" in low and ("fail" in low or "error" in low or "timeout" in low):
        return "视觉处理失败，请检查视觉服务是否可用。"

    # ── API / 模型相关（需在 HTTP 404 之前，避免 "model not found" 误判为 404）──
    if "api key" in low and ("empty" in low or "missing" in low or "not configured" in low):
        return "尚未配置 API 密钥，请在设置中填写。"
    if "invalid api key" in low:
        return "API 密钥无效，请检查密钥配置。"
    if "insufficient" in low and ("balance" in low or "quota" in low or "credit" in low):
        return "账户余额或额度不足，请充值后重试。"
    if "context length" in low or "maximum context" in low or "context window" in low:
        return "对话上下文超出模型限制，请精简输入或开启记忆摘要模式。"
    if "max tokens" in low or "maximum token" in low or "too many tokens" in low:
        return "请求的 Token 数超出模型上限，请缩短内容。"
    if "model not found" in low or "model does not exist" in low or "no such model" in low:
        return "指定的模型不存在，请检查模型名称是否正确。"
    if "invalid model" in low:
        return "模型配置无效，请在设置中选择有效模型。"

    # ── 网络 / HTTP ──
    if "connection" in low and ("refused" in low or "reset" in low or "aborted" in low):
        return "无法连接到服务器，请检查网络或服务地址是否正确。"
    if "timed out" in low or "timeout" in low or "timeouterror" in low:
        return "请求超时，请稍后重试，或检查网络连接。"
    if "connectionerror" in low or "connection error" in low or "failed to establish" in low:
        return "网络连接失败，请检查网络或服务地址。"
    if "dns" in low and ("fail" in low or "error" in low or "resolve" in low):
        return "无法解析服务器域名，请检查服务地址。"
    if re.search(r"\b401\b", s) or "unauthorized" in low:
        return "API 密钥无效或已过期，请检查密钥配置。"
    if re.search(r"\b403\b", s) or "forbidden" in low:
        return "没有访问权限（403），请检查密钥或服务权限。"
    if re.search(r"\b404\b", s) or "not found" in low:
        return "请求的资源不存在（404），请检查服务地址或模型名称。"
    if re.search(r"\b429\b", s) or "rate limit" in low or "too many requests" in low:
        return "请求过于频繁（429），请稍后重试。"
    if re.search(r"\b5\d\d\b", s) or "internal server error" in low:
        return "服务端内部错误，请稍后重试。"
    if "http" in low and re.search(r"\b\d{3}\b", s):
        return f"请求失败（HTTP 错误）：{s.strip()}"

    # ── 任务 / Agent 相关 ──
    if "task already running" in low:
        return "任务已在运行中，请先停止当前任务。"
    if "max_steps" in low or "max steps" in low:
        return "任务已达到最大步数限制。"
    if "jailbreak" in low and "block" in low:
        return "消息因涉嫌绕过安全约束被拦截。如为误报，可在设置中关闭越狱防护。"

    # ── 其他常见 ──
    if "out of memory" in low or "memoryerror" in low:
        return "内存不足，请关闭部分程序后重试。"
    if "disk full" in low or "no space left" in low:
        return "磁盘空间不足，请清理后重试。"

    # 兜底：若本身就是一句可读的中文/短句，直接返回；否则包裹成通用提示
    if s and len(s) < 200 and not any(c in s for c in ("Traceback", "  File ", "  File \"")):
        # 已经是简短可读信息，原样返回
        return s

    return f"发生未知错误：{s[:200]}"


def translate_http_status(status: int, fallback: str = "") -> str:
    """把 HTTP 状态码转成友好提示。"""
    mapping = {
        400: "请求参数有误（400）。",
        401: "API 密钥无效或未授权（401）。",
        403: "没有访问权限（403）。",
        404: "请求的资源不存在（404），请检查地址或模型名。",
        408: "请求超时（408）。",
        429: "请求过于频繁（429），请稍后重试。",
        500: "服务端内部错误（500）。",
        502: "网关错误（502）。",
        503: "服务不可用（503）。",
        504: "网关超时（504）。",
    }
    if status in mapping:
        return mapping[status]
    if fallback:
        return fallback
    return f"请求失败（HTTP {status}）。"

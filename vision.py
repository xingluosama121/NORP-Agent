# Vibe Coding Agent - 视觉 API（开放接口层）
# Copyright (c) 2026 xingluosama
#
# 视觉 API 是一个「开放接口」，让开发者自行接入多模态视觉模型，
# 对图片 / 视频流做视觉理解，并把结果以文字形式返回给 Agent。
#
# 支持两种 provider（可同时存在，按优先级调用）：
#   1. 本地注册回调（register_vision_handler）
#   2. 外部服务 URL（在设置中配置 vision_service_url）
#
# 开发者接入示例（本地回调）：
#   from vision import register_vision_handler
#
#   def my_handler(data: bytes, ext: str, media_type: str) -> str:
#       # data 为图片二进制；media_type 为 "image" 或 "video"
#       # 在这里调用你自己的多模态模型（OpenAI / Qwen-VL / 本地模型等）
#       return "这是一张包含猫咪的照片……"
#
#   register_vision_handler(my_handler)

import base64
import json
import logging
import os
import threading
from typing import Callable, List, Optional

import requests

from vision_adapters import VisionAdapterError, describe_with_provider


# ── 视觉文件分类 ──────────────────────────────────────────────
# 图片扩展名
IMAGE_EXTS = {
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico", "tiff", "tif",
}
# 视频扩展名
VIDEO_EXTS = {
    "mp4", "avi", "mov", "mkv", "webm", "flv", "wmv", "m4v", "mpg", "mpeg",
}

# 扩展名 → MIME 类型（用于外部服务、以及开发者回调判断）
_EXT_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
    "svg": "image/svg+xml", "ico": "image/x-icon",
    "tiff": "image/tiff", "tif": "image/tiff",
    "mp4": "video/mp4", "avi": "video/x-msvideo", "mov": "video/quicktime",
    "mkv": "video/x-matroska", "webm": "video/webm", "flv": "video/x-flv",
    "wmv": "video/x-ms-wmv", "m4v": "video/x-m4v",
    "mpg": "video/mpeg", "mpeg": "video/mpeg",
}


class VisionNotConfigured(Exception):
    """未配置任何视觉处理器（既无本地回调，也无外部服务 URL）。"""


# 全局注册表：开发者通过 register_vision_handler 注册的回调
_handlers: List[Callable] = []
_lock = threading.Lock()


def register_vision_handler(handler: Callable) -> None:
    """注册一个视觉处理回调。

    handler 签名：
        handler(data: bytes, ext: str, media_type: str) -> Optional[str]
        - data       图片/视频的二进制内容（视频可能较大，建议按需处理）
        - ext        文件扩展名（不含点，小写），如 "png" / "mp4"
        - media_type "image" 或 "video"
        - 返回       文字描述字符串；返回 None 或抛异常表示无法处理，将尝试下一个 provider
    """
    if not callable(handler):
        raise TypeError("register_vision_handler expects a callable")
    with _lock:
        if handler not in _handlers:
            _handlers.append(handler)


def unregister_vision_handler(handler: Callable) -> None:
    """取消注册一个视觉处理回调。"""
    with _lock:
        if handler in _handlers:
            _handlers.remove(handler)


def is_visual_ext(ext: str) -> bool:
    """判断扩展名是否为视觉文件（图片/视频）。"""
    return ext in IMAGE_EXTS or ext in VIDEO_EXTS


def media_type_of(ext: str) -> str:
    """返回 "image" / "video" / "other"。"""
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return "other"


def mime_of(ext: str) -> str:
    return _EXT_MIME.get(ext, "application/octet-stream")


def process_visual(data: bytes, ext: str, config: dict) -> str:
    """视觉处理统一入口。

    优先级：内置 provider（openai_compatible / anthropic / llama_cpp）> 本地注册回调 > 外部服务 URL。

    返回文字描述；若未配置任何 provider，抛 VisionNotConfigured。
    """
    media = media_type_of(ext)
    mime = mime_of(ext)
    cfg = config or {}

    # 0. 内置 provider（优先级最高，开箱即用）
    provider = cfg.get("vision_provider", "").strip()
    if provider:
        prompt = cfg.get("vision_prompt", "").strip() or "请详细描述这张图片的内容。"
        try:
            return describe_with_provider(provider, data, ext, mime, prompt, cfg)
        except VisionAdapterError as e:
            # 内置 provider 失败 → 降级到本地回调 / 外部服务（不静默吞掉，留日志）
            logging.getLogger("vision").warning("内置 provider 调用失败，降级：%s", e)

    # 1. 本地回调
    with _lock:
        handlers = list(_handlers)
    for handler in handlers:
        try:
            result = handler(data, ext, media)
            if result:
                return str(result)
        except Exception:
            continue

    # 2. 外部服务 URL
    url = cfg.get("vision_service_url", "").strip()
    if url:
        return _call_external_service(url, data, ext, media)

    raise VisionNotConfigured(
        f"未配置视觉处理（文件类型 .{ext}）。请在设置中开启视觉 API 并配置服务地址，"
        f"或通过 register_vision_handler 注册本地视觉回调。"
    )


def describe_visual_file(file_path: str, config: dict) -> str:
    """读取一个视觉文件（图片/视频）的二进制，返回视觉描述。

    供 read_file 工具在遇到图片时调用。config 传入视觉相关配置（可为空 dict，
    空 dict 表示「未配置任何 provider」，process_visual 会抛 VisionNotConfigured）。
    """
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    if not is_visual_ext(ext):
        raise ValueError(f"不是视觉文件: {file_path}")
    with open(file_path, "rb") as f:
        data = f.read()
    return process_visual(data, ext, config)


def _call_external_service(url: str, data: bytes, ext: str, media: str) -> str:
    """调用外部视觉服务。

    约定协议（POST JSON）：
        {
            "media_type": "image" | "video",
            "mime_type":  "image/png" | ...,
            "extension":  "png",
            "data":       "<base64 编码的二进制>"
        }
    期望响应（JSON）：
        { "description": "文字描述" }
    兼容响应字段：description / text / result / content。
    """
    payload = {
        "media_type": media,
        "mime_type": mime_of(ext),
        "extension": ext,
        "data": base64.b64encode(data).decode("ascii"),
    }
    resp = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        for key in ("description", "text", "result", "content"):
            if body.get(key):
                return str(body[key])
        # 某些服务直接返回 {"choices": [{"message": {"content": "..."}}]}（OpenAI 风格）
        choices = body.get("choices")
        if choices and isinstance(choices, list):
            msg = choices[0].get("message", {})
            content = msg.get("content") if isinstance(msg, dict) else None
            if content:
                return str(content)
    return json.dumps(body, ensure_ascii=False)

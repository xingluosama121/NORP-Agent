# Vibe Coding Agent - 视觉 Provider 适配层（内置 provider）
# Copyright (c) 2026 xingluosama
#
# 三个内置 provider，统一实现 describe() 签名：
#     describe(data: bytes, ext: str, mime: str, prompt: str, cfg: dict) -> str
#
# provider 选择（config 的 vision_provider 配置项）：
#   - "openai_compatible"  OpenAI / Qwen-VL / GLM-4V / Ollama / vLLM / llama.cpp 的 /v1 端点
#   - "anthropic"          Claude
#   - "llama_cpp"          llama.cpp server 的 raw /completion 接口
#
# 每个 adapter 只做两件事：构造请求 + 解析响应（即 to_messages/parse 的职责）。
# 新增 provider = 新增一个 adapter，主逻辑零改动。
#
# 隐私提示：图片二进制会被 base64 后发往所配置的视觉服务方；
# 云端 provider（openai_compatible / anthropic）时务必在设置面板标注「图片将发送至视觉服务方」。
# 本地 llama.cpp 提供完全离线选项。

import base64
import json
from typing import Any, Dict


class VisionAdapterError(Exception):
    """某个 provider 调用失败（携带 provider 名与原因），上层据此回退到下一个 provider。"""

    def __init__(self, provider: str, message: str):
        self.provider = provider
        self.message = message
        super().__init__(f"[{provider}] {message}")


def _data_url(data: bytes, mime: str) -> str:
    """图片 → data URL（OpenAI 兼容协议的 image_url 用）。"""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _b64(data: bytes) -> str:
    """图片 → 纯 base64（Anthropic / llama.cpp 用）。"""
    return base64.b64encode(data).decode('ascii')


def _cfg_int(cfg: Dict[str, Any], key: str, default: int) -> int:
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _cfg_float(cfg: Dict[str, Any], key: str, default: float) -> float:
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


class OpenAICompatibleAdapter:
    """OpenAI 兼容协议：/chat/completions，content 里 text + image_url(data URL)。

    覆盖：OpenAI、Qwen-VL、GLM-4V、Ollama（/v1）、vLLM、llama.cpp 的 /v1 端点等。
    """

    name = "openai_compatible"

    def describe(self, data: bytes, ext: str, mime: str, prompt: str, cfg: Dict[str, Any]) -> str:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise VisionAdapterError(self.name, f"缺少依赖 openai：{e}")

        base_url = (cfg.get("vision_base_url") or "").strip()
        api_key = (cfg.get("vision_api_key") or "").strip()
        model = (cfg.get("vision_model") or "").strip()
        if not model:
            raise VisionAdapterError(self.name, "未配置 vision_model")

        kwargs: Dict[str, Any] = {"api_key": api_key or "not-needed"}
        if base_url:
            kwargs["base_url"] = base_url

        client = OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": _data_url(data, mime)}},
                    ],
                }
            ],
            max_tokens=_cfg_int(cfg, "vision_max_tokens", 1024),
            temperature=_cfg_float(cfg, "vision_temperature", 0.2),
        )
        if not resp.choices:
            raise VisionAdapterError(self.name, "响应无 choices")
        content = resp.choices[0].message.content
        if not content:
            raise VisionAdapterError(self.name, "响应 content 为空")
        return content


class AnthropicAdapter:
    """Anthropic：/v1/messages，content 里 image source base64 + text。"""

    name = "anthropic"

    def describe(self, data: bytes, ext: str, mime: str, prompt: str, cfg: Dict[str, Any]) -> str:
        try:
            from anthropic import Anthropic
        except ImportError as e:
            raise VisionAdapterError(self.name, f"缺少依赖 anthropic：{e}")

        api_key = (cfg.get("vision_api_key") or "").strip()
        model = (cfg.get("vision_model") or "").strip()
        if not model:
            raise VisionAdapterError(self.name, "未配置 vision_model")
        if not api_key:
            raise VisionAdapterError(self.name, "未配置 vision_api_key")

        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=_cfg_int(cfg, "vision_max_tokens", 1024),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": mime, "data": _b64(data)},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        if not resp.content:
            raise VisionAdapterError(self.name, "响应 content 为空")
        # content 是块列表，取其中的 text 块拼接
        text = "".join(getattr(block, "text", "") for block in resp.content)
        if not text:
            raise VisionAdapterError(self.name, "响应无可读文本")
        return text


class LlamaCppAdapter:
    """llama.cpp server 的 raw /completion 接口（多模态模型用 image_data 传图）。

    注意：若你的 llama-server 启用了 OpenAI 兼容端点（/v1/chat/completions），
    请改用 provider = "openai_compatible" 并指向 {base_url}/v1，二者功能等价。
    这里走 raw /completion 是为了不依赖 /v1 端点即可用。
    """

    name = "llama_cpp"

    def describe(self, data: bytes, ext: str, mime: str, prompt: str, cfg: Dict[str, Any]) -> str:
        import requests

        base_url = (cfg.get("vision_base_url") or "").strip().rstrip("/")
        if not base_url:
            raise VisionAdapterError(self.name, "未配置 vision_base_url")

        payload = {
            "prompt": prompt,
            "image_data": [{"data": _b64(data), "id": 0}],
            "n_predict": _cfg_int(cfg, "vision_max_tokens", 1024),
            "temperature": _cfg_float(cfg, "vision_temperature", 0.2),
            "stream": False,
        }
        resp = requests.post(
            f"{base_url}/completion",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=_cfg_int(cfg, "vision_timeout", 120),
        )
        resp.raise_for_status()
        body = resp.json()
        # 兼容多种返回字段
        for key in ("content", "text", "result", "response"):
            if body.get(key):
                return str(body[key])
        return json.dumps(body, ensure_ascii=False)


# provider 注册表
ADAPTERS: Dict[str, Any] = {
    "openai_compatible": OpenAICompatibleAdapter(),
    "anthropic": AnthropicAdapter(),
    "llama_cpp": LlamaCppAdapter(),
}


def describe_with_provider(
    provider: str, data: bytes, ext: str, mime: str, prompt: str, cfg: Dict[str, Any]
) -> str:
    """按 provider 名调用对应 adapter 的统一入口，主逻辑只认这个函数。

    底层异常统一包装成 VisionAdapterError（带 provider 名），供上层降级处理。
    """
    adapter = ADAPTERS.get(provider)
    if adapter is None:
        raise VisionAdapterError(provider, f"未知的 provider：{provider}，可用：{', '.join(ADAPTERS)}")
    try:
        return adapter.describe(data, ext, mime, prompt, cfg)
    except VisionAdapterError:
        raise
    except Exception as e:
        raise VisionAdapterError(provider, f"调用失败：{e}") from e

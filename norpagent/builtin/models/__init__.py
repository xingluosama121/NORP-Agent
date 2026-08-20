# Copyright (c) 2026 xingluosama121, MIT Licensed
"""内置模型适配器。

- mock：确定性脚本模型（基准测试），零依赖；
- openai_compat：OpenAI 兼容服务（OpenAI / DeepSeek / Qwen / vLLM /
  Ollama 等），需 norpagent[openai]，SDK 懒加载；
- anthropic：Claude 系列，需 norpagent[anthropic]，SDK 懒加载。
"""

from norpagent.builtin.models.mock import MockModelProvider
from norpagent.builtin.models.openai_compat import OpenAICompatProvider
from norpagent.builtin.models.anthropic import AnthropicProvider

__all__ = ["MockModelProvider", "OpenAICompatProvider", "AnthropicProvider"]

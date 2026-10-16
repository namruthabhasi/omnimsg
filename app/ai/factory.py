from __future__ import annotations

from app.ai.base import BaseLLMProvider
from app.ai.gemini_provider import GeminiProvider
from app.ai.mock_provider import MockLLMProvider
from app.ai.openai_provider import OpenAIProvider
from app.core.config import settings

_default_mock_provider = MockLLMProvider()


def get_llm_provider(provider_type: str | None = None) -> BaseLLMProvider:
    provider = (provider_type or settings.LLM_PROVIDER).lower()
    if provider == "openai":
        return OpenAIProvider()
    elif provider in ("gemini", "google"):
        return GeminiProvider()
    elif provider == "mock":
        return _default_mock_provider
    else:
        return _default_mock_provider

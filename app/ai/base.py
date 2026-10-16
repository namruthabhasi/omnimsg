from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List

from app.ai.schemas import LLMResponse


class BaseLLMProvider(ABC):
    """Abstract interface for LLM providers."""

    @abstractmethod
    async def generate_reply(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        context: List[str],
    ) -> LLMResponse:
        """Generate a structured reply given conversation messages, system prompt, and context snippets."""
        pass

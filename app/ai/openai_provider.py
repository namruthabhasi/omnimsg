from __future__ import annotations

import json
from typing import Dict, List

import httpx

from app.ai.base import BaseLLMProvider
from app.ai.schemas import LLMResponse
from app.core.config import settings


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API provider for generating structured replies."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.OPENAI_API_KEY
        self.model = model or settings.OPENAI_MODEL

    async def generate_reply(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        context: List[str],
    ) -> LLMResponse:
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is not set")

        kb_context = ""
        if context:
            kb_context = "\n\nKnowledge Base:\n" + "\n---\n".join(context)

        full_system_prompt = (
            f"{system_prompt}{kb_context}\n\n"
            "You MUST respond ONLY with valid JSON matching this schema:\n"
            "{\n"
            '  "reply": "string (the response message to the customer)",\n'
            '  "confidence": float (between 0.0 and 1.0),\n'
            '  "needs_human": boolean (true if you are unsure or the customer asks for a human)\n'
            "}"
        )

        formatted_messages = [{"role": "system", "content": full_system_prompt}]
        for msg in messages:
            formatted_messages.append({"role": msg["role"], "content": msg["content"]})

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        try:
            content_str = data["choices"][0]["message"]["content"]
            parsed = json.loads(content_str)
            return LLMResponse.model_validate(parsed)
        except Exception as exc:
            raise ValueError(f"Malformed output from OpenAI: {exc}") from exc

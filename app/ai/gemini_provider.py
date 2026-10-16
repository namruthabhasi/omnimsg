from __future__ import annotations

import json
from typing import Dict, List

import httpx

from app.ai.base import BaseLLMProvider
from app.ai.schemas import LLMResponse
from app.core.config import settings


class GeminiProvider(BaseLLMProvider):
    """Google Gemini API provider for generating structured replies."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.GOOGLE_API_KEY
        self.model = model or settings.GOOGLE_MODEL

    async def generate_reply(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        context: List[str],
    ) -> LLMResponse:
        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY is not set")

        kb_context = ""
        if context:
            kb_context = "\n\nKnowledge Base:\n" + "\n---\n".join(context)

        full_system_instruction = (
            f"{system_prompt}{kb_context}\n\n"
            "You MUST respond ONLY with a raw JSON object matching:\n"
            '{"reply": string, "confidence": float between 0.0 and 1.0, "needs_human": boolean}'
        )

        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        headers = {"Content-Type": "application/json"}
        payload = {
            "system_instruction": {"parts": [{"text": full_system_instruction}]},
            "contents": contents,
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.3,
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        try:
            candidate = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(candidate)
            return LLMResponse.model_validate(parsed)
        except Exception as exc:
            raise ValueError(f"Malformed output from Gemini: {exc}") from exc

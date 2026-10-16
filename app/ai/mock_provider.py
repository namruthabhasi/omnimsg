from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.ai.base import BaseLLMProvider
from app.ai.schemas import LLMResponse


class MockLLMProvider(BaseLLMProvider):
    """Mock LLM provider with controllable behavior for testing."""

    def __init__(
        self,
        mode: str = "normal",
        reply: Optional[str] = None,
        confidence: float = 0.95,
        needs_human: bool = False,
    ) -> None:
        self.mode = mode
        self.reply = reply
        self.confidence = confidence
        self.needs_human = needs_human
        self.call_history: List[Dict[str, Any]] = []

    def set_mode(
        self,
        mode: str,
        reply: Optional[str] = None,
        confidence: Optional[float] = None,
        needs_human: Optional[bool] = None,
    ) -> None:
        self.mode = mode
        if reply is not None:
            self.reply = reply
        if confidence is not None:
            self.confidence = confidence
        if needs_human is not None:
            self.needs_human = needs_human

    def reset(self) -> None:
        self.mode = "normal"
        self.reply = None
        self.confidence = 0.95
        self.needs_human = False
        self.call_history.clear()

    async def generate_reply(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        context: List[str],
    ) -> LLMResponse:
        self.call_history.append(
            {
                "messages": list(messages),
                "system_prompt": system_prompt,
                "context": list(context),
            }
        )

        if self.mode == "exception":
            raise RuntimeError("LLM provider internal error")

        if self.mode == "malformed":
            # Malformed output cannot validate into LLMResponse
            raise ValueError("Malformed response from LLM provider")

        if self.mode == "low_confidence":
            return LLMResponse(
                reply=self.reply or "I am not completely sure.",
                confidence=self.confidence if self.confidence < 0.7 else 0.4,
                needs_human=False,
            )

        if self.mode == "needs_human":
            return LLMResponse(
                reply=self.reply or "Transferring to human agent.",
                confidence=self.confidence,
                needs_human=True,
            )

        # Normal mode
        last_user_message = (
            messages[-1]["content"] if messages else "your message"
        )
        default_reply = (
            self.reply
            or f"Thank you for contacting us regarding: '{last_user_message}'. How else can I help?"
        )
        return LLMResponse(
            reply=default_reply,
            confidence=self.confidence,
            needs_human=self.needs_human,
        )

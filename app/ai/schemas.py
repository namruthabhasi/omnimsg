from __future__ import annotations

from pydantic import BaseModel, Field


class LLMResponse(BaseModel):
    """Structured output expected from all LLM providers."""

    reply: str = Field(..., description="The AI agent's generated reply text")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0",
    )
    needs_human: bool = Field(
        default=False,
        description="Flag indicating if the conversation should be handed over to a human",
    )

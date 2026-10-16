from __future__ import annotations

import json
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    # ── Database ──────────────────────────────────────────────
    DATABASE_URL: str = "sqlite+aiosqlite:///./omnimessage.db"

    # ── JWT ────────────────────────────────────────────────────
    SECRET_KEY: str = "change-this-to-a-random-secret"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Encryption (Fernet) ───────────────────────────────────
    ENCRYPTION_KEY: str = ""

    # ── CORS ──────────────────────────────────────────────────
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8080"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors(cls, v):  # noqa: N805
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [origin.strip() for origin in v.split(",")]
        return v

    # ── AI / LLM ──────────────────────────────────────────────
    LLM_PROVIDER: str = "mock"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4"
    GOOGLE_API_KEY: str = ""
    GOOGLE_MODEL: str = "gemini-pro"
    AI_CONFIDENCE_THRESHOLD: float = 0.7
    AI_SYSTEM_PROMPT: str = "You are a helpful customer service agent."
    AI_FALLBACK_MESSAGE: str = (
        "I'm connecting you with a human agent who can help you better. "
        "Please hold on."
    )

    # ── Telegram ──────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_WEBHOOK_SECRET: str = ""

    # ── Meta / WhatsApp ───────────────────────────────────────
    GRAPH_API_VERSION: str = "v19.0"
    META_APP_SECRET: str = ""
    META_VERIFY_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""

    # ── Instagram ─────────────────────────────────────────────
    INSTAGRAM_ACCESS_TOKEN: str = ""

    # ── Messenger ─────────────────────────────────────────────
    MESSENGER_PAGE_ACCESS_TOKEN: str = ""

    # ── Rate limiting ─────────────────────────────────────────
    RATE_LIMIT_AUTH: str = "5/minute"
    RATE_LIMIT_WEBHOOK: str = "100/minute"

    # ── Simulator ─────────────────────────────────────────────
    ENABLE_SIMULATOR: bool = False

    # ── Demo ──────────────────────────────────────────────────
    DEMO_MODE: bool = False
    DEMO_PASSWORD: str = "demo1234"

    # ── Logging ───────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()

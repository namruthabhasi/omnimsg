from __future__ import annotations

from typing import Dict, Optional

from app.channels.base import BaseChannelAdapter
from app.channels.instagram import InstagramAdapter
from app.channels.messenger import MessengerAdapter
from app.channels.mock import MockChannelAdapter
from app.channels.telegram import TelegramAdapter
from app.channels.whatsapp import WhatsAppAdapter


class ChannelRegistry:
    """Registry maintaining active channel adapters."""

    def __init__(self) -> None:
        self._adapters: Dict[str, BaseChannelAdapter] = {}

    def register(self, channel: str, adapter: BaseChannelAdapter) -> None:
        self._adapters[channel.lower()] = adapter

    def get(self, channel: str) -> Optional[BaseChannelAdapter]:
        return self._adapters.get(channel.lower())

    def get_or_raise(self, channel: str) -> BaseChannelAdapter:
        adapter = self.get(channel)
        if not adapter:
            raise ValueError(f"No adapter registered for channel '{channel}'")
        return adapter

    def registered_channels(self) -> list[str]:
        return list(self._adapters.keys())


channel_registry = ChannelRegistry()
# Register default mock adapter for testing and simulation
mock_adapter = MockChannelAdapter()
channel_registry.register("mock", mock_adapter)

# Register default telegram adapter
telegram_adapter = TelegramAdapter()
channel_registry.register("telegram", telegram_adapter)

# Register Meta adapters
whatsapp_adapter = WhatsAppAdapter()
channel_registry.register("whatsapp", whatsapp_adapter)

instagram_adapter = InstagramAdapter()
channel_registry.register("instagram", instagram_adapter)

messenger_adapter = MessengerAdapter()
channel_registry.register("messenger", messenger_adapter)

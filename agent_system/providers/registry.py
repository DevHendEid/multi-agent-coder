from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .base import ModelProvider, ProviderAvailability, ProviderTier
from .mock import MockProvider
from .command_provider import CommandProvider
from .codex_provider import CodexProvider, CodexReviewerProvider
from .ollama_provider import OllamaProvider


class ProviderRegistry:
    def __init__(self, config_path: str | Path | None = None):
        self.config_path = Path(config_path) if config_path else None
        self._providers: dict[str, ModelProvider] = {}
        self._provider_configs: dict[str, dict[str, Any]] = {}
        self._load_config()

    def _load_config(self) -> None:
        if self.config_path and self.config_path.exists():
            data = yaml.safe_load(self.config_path.read_text()) or {}
            self._provider_configs = data.get("providers", {})

    def register(self, provider: ModelProvider) -> None:
        self._providers[provider.provider_id] = provider

    def unregister(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    def get(self, provider_id: str) -> ModelProvider | None:
        if provider_id in self._providers:
            return self._providers[provider_id]
        if provider_id in self._provider_configs:
            provider = self._create_from_config(provider_id, self._provider_configs[provider_id])
            if provider:
                self._providers[provider_id] = provider
                return provider
        return None

    def _create_from_config(self, provider_id: str, config: dict[str, Any]) -> ModelProvider | None:
        if not config.get("enabled", True):
            return None
        ptype = config.get("type", "mock")
        name = config.get("name", provider_id)
        tier_str = config.get("tier", "free")
        try:
            tier = ProviderTier(tier_str)
        except ValueError:
            tier = ProviderTier.FREE

        if ptype == "mock":
            return MockProvider(name, provider_id, tier, config)
        if ptype == "command":
            return CommandProvider(name, provider_id, tier, config)
        if ptype == "codex":
            return CodexProvider(name, provider_id, tier, config)
        if ptype == "codex-reviewer":
            return CodexReviewerProvider(name, provider_id, tier, config)
        if ptype == "ollama":
            return OllamaProvider(name, provider_id, tier, config)
        return None

    def list_available(self) -> list[ModelProvider]:
        result = []
        for pid in self._provider_configs:
            provider = self.get(pid)
            if provider and provider.availability() == ProviderAvailability.AVAILABLE:
                result.append(provider)
        for provider in self._providers.values():
            if provider.provider_id not in self._provider_configs:
                if provider.availability() == ProviderAvailability.AVAILABLE:
                    result.append(provider)
        return result

    def list_all(self) -> list[ModelProvider]:
        result = []
        for pid in self._provider_configs:
            provider = self.get(pid)
            if provider:
                result.append(provider)
        for provider in self._providers.values():
            if provider.provider_id not in self._provider_configs:
                result.append(provider)
        return result

    def get_by_tier(self, tier: ProviderTier) -> list[ModelProvider]:
        return [p for p in self.list_available() if p.tier == tier]

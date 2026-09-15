from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class ProviderAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    AUTH_ERROR = "AUTH_ERROR"
    UNAVAILABLE = "UNAVAILABLE"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class ProviderTier(str, Enum):
    FREE = "free"
    PREMIUM = "premium"
    LOCAL = "local"


class ProviderTrustStatus(str, Enum):
    EXPERIMENTAL = "EXPERIMENTAL"
    VALIDATED = "VALIDATED"
    DISABLED = "DISABLED"


@dataclass(slots=True)
class ProviderCapabilities:
    supports_tools: bool = False
    supports_file_editing: bool = False
    supports_long_context: bool = False
    max_context_tokens: int = 4096


@dataclass(slots=True)
class ExecutionResult:
    success: bool
    output: str
    error: str | None = None
    modified_files: list[str] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class ProviderMetadata:
    provider_id: str
    model_name: str
    tier: ProviderTier
    supports_code: bool
    supports_tools: bool
    supports_file_editing: bool
    supports_long_context: bool
    context_limit: int | None
    availability: ProviderAvailability
    timeout_seconds: int
    trust_status: ProviderTrustStatus


class ProviderError(Exception):
    def __init__(self, message: str, availability: ProviderAvailability = ProviderAvailability.PROVIDER_ERROR):
        super().__init__(message)
        self.availability = availability


class ModelProvider(ABC):
    def __init__(self, name: str, provider_id: str, tier: ProviderTier, config: dict[str, Any] | None = None):
        self.name = name
        self.provider_id = provider_id
        self.tier = tier
        self.config = config or {}

    @abstractmethod
    def availability(self) -> ProviderAvailability:
        pass

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        pass

    @abstractmethod
    def execute(self, task: dict[str, Any], context: dict[str, Any], worktree: Path) -> ExecutionResult:
        pass

    @abstractmethod
    def classify_error(self, error: Exception) -> ProviderAvailability:
        pass

    def get_metadata(self) -> ProviderMetadata:
        caps = self.capabilities()
        avail = self.availability()
        trust = ProviderTrustStatus.EXPERIMENTAL
        if hasattr(self, 'trust_status'):
            trust = self.trust_status
        return ProviderMetadata(
            provider_id=self.provider_id,
            model_name=getattr(self, 'model', 'unknown'),
            tier=self.tier,
            supports_code=caps.supports_tools,
            supports_tools=caps.supports_tools,
            supports_file_editing=caps.supports_file_editing,
            supports_long_context=caps.supports_long_context,
            context_limit=caps.max_context_tokens,
            availability=avail,
            timeout_seconds=self.config.get("timeout_seconds", 300),
            trust_status=trust,
        )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, provider_id={self.provider_id!r}, tier={self.tier.value})"

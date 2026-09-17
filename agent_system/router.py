from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .benchmark import BenchmarkRegistry, BenchmarkStatus
from .providers import ModelProvider, ProviderAvailability, ProviderRegistry


@dataclass(slots=True)
class RoutingDecision:
    provider: ModelProvider | None
    fallback_providers: list[ModelProvider]
    reason: str
    wait_state: str | None = None


class ModelRouter:
    def __init__(self, registry: ProviderRegistry, routing_config_path: str | Path | None = None, benchmark_registry: BenchmarkRegistry | None = None):
        self.registry = registry
        self.routing_config_path = Path(routing_config_path) if routing_config_path else None
        self.benchmark_registry = benchmark_registry
        self._routing_config: dict[str, list[str]] = {}
        self._load_routing_config()

    def _load_routing_config(self) -> None:
        if self.routing_config_path and self.routing_config_path.exists():
            self._routing_config = yaml.safe_load(self.routing_config_path.read_text()) or {}

    def _benchmark_status(self, provider: ModelProvider) -> str:
        if self.benchmark_registry is None:
            return BenchmarkStatus.UNBENCHMARKED
        try:
            model_name = provider.get_metadata().model_name
            return self.benchmark_registry.status(provider.provider_id, model_name)
        except Exception:
            return "BENCHMARK_ERROR"

    def route(self, risk: str) -> RoutingDecision:
        candidates = self._routing_config.get(risk.lower(), [])
        if not candidates:
            candidates = self._routing_config.get("medium", ["mock-free"])
        selected = None
        fallbacks = []
        tried = []
        for provider_id in candidates:
            provider = self.registry.get(provider_id)
            if not provider:
                tried.append(f"{provider_id} (not configured)")
                continue
            benchmark_status = self._benchmark_status(provider)
            if benchmark_status in (BenchmarkStatus.REJECTED, "BENCHMARK_ERROR"):
                tried.append(f"{provider_id} ({benchmark_status})")
                continue
            availability = provider.availability()
            tried.append(f"{provider_id} ({availability.value}, benchmark={benchmark_status})")
            if availability == ProviderAvailability.AVAILABLE:
                if selected is None:
                    selected = provider
                else:
                    fallbacks.append(provider)
        if selected is None:
            wait_state = self._determine_wait_state(tried)
            return RoutingDecision(None, [], f"No available provider for {risk} risk. Tried: {', '.join(tried)}", wait_state)
        status = self._benchmark_status(selected)
        return RoutingDecision(selected, fallbacks, f"Selected {selected.provider_id} ({selected.tier.value}) for {risk} risk; benchmark={status}", None)

    def _determine_wait_state(self, tried: list[str]) -> str:
        if any("QUOTA_EXHAUSTED" in t for t in tried):
            return "WAITING_CODEX" if "codex" in str(tried).lower() else "WAITING_PROVIDER"
        return "WAITING_PROVIDER"

    def select_fallback(self, failed_provider_id: str, risk: str, error: Exception) -> RoutingDecision:
        provider = self.registry.get(failed_provider_id)
        availability = provider.classify_error(error) if provider else ProviderAvailability.PROVIDER_ERROR
        candidates = self._routing_config.get(risk.lower(), [])
        fallbacks = []
        for pid in candidates:
            if pid == failed_provider_id:
                continue
            p = self.registry.get(pid)
            if not p or self._benchmark_status(p) in (BenchmarkStatus.REJECTED, "BENCHMARK_ERROR"):
                continue
            if p.availability() == ProviderAvailability.AVAILABLE:
                fallbacks.append(p)
        if fallbacks:
            return RoutingDecision(fallbacks[0], fallbacks[1:], f"Fallback after {failed_provider_id} failed with {availability.value}: {error}")
        wait_state = "WAITING_CODEX" if availability == ProviderAvailability.QUOTA_EXHAUSTED else "WAITING_PROVIDER"
        return RoutingDecision(None, [], f"No fallback available after {failed_provider_id} failed with {availability.value}", wait_state)

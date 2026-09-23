"""Local admin UI for inspecting providers and editing routing (agent-ui).

Localhost only, no authentication (V1). State-changing requests must carry the
X-Agent-UI header (blocks cross-site form posts) and the Host header must be a
loopback name (blocks DNS rebinding).
"""
from __future__ import annotations

import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..providers import ProviderAvailability
from .config_store import (
    RISK_LEVELS,
    ConfigConflictError,
    ConfigStore,
    ConfigValidationError,
    redact,
    redact_text,
)

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
CSRF_HEADER = "x-agent-ui"
BENCHMARK_TIMEOUT_SECONDS = 1800
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _default_benchmark_command(provider_id: str) -> list[str]:
    # The existing CLI command: `agent benchmark <provider_id>`.
    return [sys.executable, "-m", "agent_system.cli", "benchmark", provider_id]


def _host_only(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class BenchmarkJobs:
    def __init__(self, root: Path, command: Callable[[str], list[str]], timeout: int):
        self.root = root
        self.command = command
        self.timeout = timeout
        self.jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(self, provider_id: str) -> dict[str, Any]:
        with self._lock:
            if any(j["provider_id"] == provider_id and j["status"] == "running" for j in self.jobs.values()):
                raise HTTPException(409, f"a benchmark for '{provider_id}' is already running")
            job = {
                "job_id": uuid.uuid4().hex[:12],
                "provider_id": provider_id,
                "status": "running",
                "returncode": None,
                "output": "",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
            }
            self.jobs[job["job_id"]] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return dict(job)

    def _run(self, job: dict[str, Any]) -> None:
        try:
            proc = subprocess.run(
                self.command(job["provider_id"]), cwd=str(self.root), capture_output=True, text=True, timeout=self.timeout
            )
            job["returncode"] = proc.returncode
            job["output"] = redact_text((proc.stdout + proc.stderr)[-20000:])
            job["status"] = "passed" if proc.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            job["status"] = "timeout"
            job["output"] = f"benchmark exceeded {self.timeout}s and was stopped"
        except Exception as exc:
            job["status"] = "failed"
            job["output"] = redact_text(f"{type(exc).__name__}: {exc}")
        job["finished_at"] = datetime.now(timezone.utc).isoformat()

    def get(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown benchmark job")
        return dict(job)


def create_app(
    root: str | Path,
    *,
    benchmark_command: Callable[[str], list[str]] = _default_benchmark_command,
    benchmark_timeout: int = BENCHMARK_TIMEOUT_SECONDS,
) -> FastAPI:
    store = ConfigStore(root)
    jobs = BenchmarkJobs(store.root, benchmark_command, benchmark_timeout)
    app = FastAPI(title="Multi-Agent Coder — Model Control", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.jobs = jobs

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if _host_only(request.headers.get("host", "")) not in LOOPBACK_HOSTS:
            return JSONResponse({"detail": "host not allowed"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": f"missing {CSRF_HEADER} header"}, status_code=403)
        return await call_next(request)

    def provider_views() -> list[dict[str, Any]]:
        snap = store.load()
        bench = _benchmark_registry(store.root)
        views = []
        for pid, cfg in snap.providers.items():
            if not isinstance(cfg, dict):
                continue
            provider = store.instantiate(str(pid), cfg)
            # Same model name ModelProvider.get_metadata() reports, so benchmark lookups match `agent models`.
            model = str(getattr(provider, "model", "unknown")) if provider else "unknown"
            trust = getattr(getattr(provider, "trust_status", None), "value", None) or cfg.get("trust_status", "EXPERIMENTAL")
            role = store.role_of(str(pid), cfg) if provider else None
            views.append({
                "provider_id": str(pid),
                "name": cfg.get("name", pid),
                "type": cfg.get("type", "mock"),
                "model": model or (f"unconfigured (set ${cfg['model_env']})" if cfg.get("model_env") else "unconfigured"),
                "role": role or "unknown",
                "tier": cfg.get("tier", "free"),
                "trust_status": trust,
                "benchmark_status": bench.status(str(pid), model) if bench else "UNBENCHMARKED",
                "timeout_seconds": cfg.get("timeout_seconds", 300),
                "enabled": cfg.get("enabled", True) is True,
                "config": redact(cfg),
            })
        return views

    def config_payload() -> dict[str, Any]:
        snap = store.load()
        views = provider_views()
        return {
            "version": snap.version,
            "valid": not snap.errors,
            "errors": snap.errors,
            "risk_levels": list(RISK_LEVELS),
            "routing": {level: snap.routing.get(level, []) for level in RISK_LEVELS},
            "implementers": [v["provider_id"] for v in views if v["role"] == "implementer" and v["enabled"]],
            "reviewers": [{"provider_id": v["provider_id"], "enabled": v["enabled"], "tier": v["tier"]} for v in views if v["role"] == "reviewer"],
        }

    def require_enabled(provider_id: str) -> None:
        cfg = store.load().providers.get(provider_id)
        if not isinstance(cfg, dict):
            raise HTTPException(404, f"unknown provider '{provider_id}'")
        if cfg.get("enabled", True) is not True:
            raise HTTPException(409, f"provider '{provider_id}' is disabled")

    @app.get("/")
    def index():
        return RedirectResponse("/providers")

    @app.get("/providers")
    def providers_page(request: Request):
        snap = store.load()
        return TEMPLATES.TemplateResponse(request, "providers.html", {"providers": provider_views(), "errors": snap.errors, "page": "providers"})

    @app.get("/routing")
    def routing_page(request: Request):
        return TEMPLATES.TemplateResponse(request, "routing.html", {"page": "routing"})

    @app.get("/api/providers")
    def api_providers():
        return {"providers": provider_views()}

    @app.post("/api/providers/{provider_id}/availability")
    def api_availability(provider_id: str):
        require_enabled(provider_id)
        provider = store.registry().get(provider_id)
        if provider is None:
            raise HTTPException(404, f"provider '{provider_id}' could not be built from config")
        try:
            availability = provider.availability().value
        except Exception as exc:
            availability = ProviderAvailability.PROVIDER_ERROR.value
            return {"provider_id": provider_id, "availability": availability, "error": redact_text(str(exc)),
                    "checked_at": datetime.now(timezone.utc).isoformat()}
        return {"provider_id": provider_id, "availability": availability, "checked_at": datetime.now(timezone.utc).isoformat()}

    @app.post("/api/providers/{provider_id}/benchmark")
    def api_benchmark(provider_id: str):
        require_enabled(provider_id)
        return jobs.start(provider_id)

    @app.get("/api/benchmarks/{job_id}")
    def api_benchmark_job(job_id: str):
        return jobs.get(job_id)

    @app.get("/api/config")
    def api_config():
        return config_payload()

    @app.post("/api/config/reload")
    def api_reload():
        return config_payload()

    @app.post("/api/config")
    def api_save(payload: dict[str, Any] = Body(...)):
        version = payload.get("version")
        if not isinstance(version, str):
            raise HTTPException(400, "missing config version")
        try:
            snap, backups = store.save(
                expected_version=version,
                routing=payload.get("routing"),
                reviewers_enabled=payload.get("reviewers_enabled"),
            )
        except ConfigValidationError as exc:
            return JSONResponse({"detail": "validation failed", "errors": exc.errors}, status_code=400)
        except ConfigConflictError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        return {**config_payload(), "saved": True, "backups": [Path(b).name for b in backups], "version": snap.version}

    return app


def _benchmark_registry(root: Path):
    # Read-only view: never create benchmarks.db just by opening the UI.
    if not (root / ".agent-system" / "benchmarks.db").exists():
        return None
    from ..cli import build_benchmark_registry

    return build_benchmark_registry(root)

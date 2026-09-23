"""Load, validate, redact and safely save provider/routing config for the admin UI.

This module has no web-framework dependency. It reuses the existing config files
(config/models.yaml, config/routing.yaml), ProviderRegistry and BenchmarkRegistry;
it does not implement any routing decisions itself.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..models import RiskLevel
from ..providers import ModelProvider, ProviderRegistry, ProviderTier

RISK_LEVELS: tuple[str, ...] = tuple(r.value for r in RiskLevel)
REDACTED = "***REDACTED***"
_SECRET_KEY_RE = re.compile(r"(api[_-]?key|key$|token|secret|passw|credential|auth)", re.IGNORECASE)
_URL_USERINFO_RE = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)
_SECRET_ASSIGN_RE = re.compile(
    r"((?:api[_-]?key|token|secret|password|passwd|credential|authorization)[\w-]*\s*[=:]\s*)(\S+)",
    re.IGNORECASE,
)
_SECRET_FLAG_RE = re.compile(r"^--?[\w-]*(key|token|secret|passw|credential|auth)[\w-]*$", re.IGNORECASE)


class ConfigValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class ConfigConflictError(RuntimeError):
    """The config on disk changed since the client loaded it."""


# --- redaction ---------------------------------------------------------------


def redact_text(text: str) -> str:
    text = _URL_USERINFO_RE.sub(rf"\1{REDACTED}@", text)
    return _SECRET_ASSIGN_RE.sub(rf"\1{REDACTED}", text)


def redact(value: Any, key: str = "", *, force: bool = False) -> Any:
    """Return a deep copy of a config value with secrets replaced by REDACTED."""
    if isinstance(value, dict):
        # Environment blocks are passed to subprocesses and commonly carry credentials.
        return {k: redact(v, str(k), force=force or str(k).lower() == "env") for k, v in value.items()}
    if isinstance(value, list):
        out: list[Any] = []
        redact_next = False
        for item in value:
            if redact_next and not isinstance(item, (dict, list)):
                out.append(REDACTED)
                redact_next = False
                continue
            out.append(redact(item, key, force=force))
            redact_next = isinstance(item, str) and bool(_SECRET_FLAG_RE.match(item))
        return out
    if force or (key and _SECRET_KEY_RE.search(key)):
        return REDACTED if value not in (None, "") else value
    if isinstance(value, str):
        return redact_text(value)
    return value


# --- snapshot ----------------------------------------------------------------


@dataclass(slots=True)
class ConfigSnapshot:
    models_text: str
    routing_text: str
    models: dict[str, Any]
    routing: dict[str, Any]
    version: str
    errors: list[str] = field(default_factory=list)

    @property
    def providers(self) -> dict[str, dict[str, Any]]:
        providers = self.models.get("providers") if isinstance(self.models, dict) else None
        return providers if isinstance(providers, dict) else {}


def _version(models_text: str, routing_text: str) -> str:
    digest = hashlib.sha256()
    digest.update(models_text.encode())
    digest.update(b"\0")
    digest.update(routing_text.encode())
    return digest.hexdigest()[:16]


class ConfigStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.models_path = self.root / "config" / "models.yaml"
        self.routing_path = self.root / "config" / "routing.yaml"
        self.backup_dir = self.root / ".agent-system" / "config-backups"
        self._lock = threading.Lock()

    # -- load -----------------------------------------------------------------

    def load(self) -> ConfigSnapshot:
        models_text = self.models_path.read_text() if self.models_path.exists() else ""
        routing_text = self.routing_path.read_text() if self.routing_path.exists() else ""
        errors: list[str] = []
        models = self._parse(models_text, "models.yaml", errors)
        routing = self._parse(routing_text, "routing.yaml", errors)
        snap = ConfigSnapshot(models_text, routing_text, models, routing, _version(models_text, routing_text))
        if not errors:
            errors = self.validate(models, routing)
        snap.errors = errors
        return snap

    @staticmethod
    def _parse(text: str, name: str, errors: list[str]) -> dict[str, Any]:
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            errors.append(f"{name}: invalid YAML: {exc}")
            return {}
        if not isinstance(data, dict):
            errors.append(f"{name}: top level must be a mapping")
            return {}
        return data

    # -- providers ------------------------------------------------------------

    @staticmethod
    def instantiate(provider_id: str, cfg: dict[str, Any]) -> ModelProvider | None:
        """Build a provider through the registry's own factory, even if disabled (inspection only)."""
        return ProviderRegistry()._create_from_config(provider_id, {**cfg, "enabled": True})

    @classmethod
    def role_of(cls, provider_id: str, cfg: dict[str, Any]) -> str | None:
        provider = cls.instantiate(provider_id, cfg)
        if provider is None:
            return None
        # Same rule BenchmarkRunner uses to classify providers.
        return "implementer" if provider.capabilities().supports_file_editing else "reviewer"

    def registry(self) -> ProviderRegistry:
        return ProviderRegistry(self.models_path)

    # -- validation -----------------------------------------------------------

    def validate(self, models: dict[str, Any], routing: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        providers = models.get("providers")
        if not isinstance(providers, dict) or not providers:
            return ["models.yaml: 'providers' must be a non-empty mapping"]

        roles: dict[str, str | None] = {}
        for pid, cfg in providers.items():
            if not isinstance(cfg, dict):
                errors.append(f"provider '{pid}': entry must be a mapping")
                continue
            if "provider_id" in cfg and str(cfg["provider_id"]) != str(pid):
                errors.append(f"provider '{pid}': provider_id '{cfg['provider_id']}' does not match its key")
            if not isinstance(cfg.get("enabled", True), bool):
                errors.append(f"provider '{pid}': enabled must be true or false")
            if str(cfg.get("tier", "free")) not in {t.value for t in ProviderTier}:
                errors.append(f"provider '{pid}': unknown tier '{cfg.get('tier')}'")
            try:
                roles[str(pid)] = self.role_of(str(pid), cfg)
            except Exception as exc:
                errors.append(f"provider '{pid}': cannot be constructed: {exc}")
                continue
            if roles[str(pid)] is None:
                errors.append(f"provider '{pid}': unknown provider type '{cfg.get('type', 'mock')}'")

        enabled = {str(pid) for pid, cfg in providers.items() if isinstance(cfg, dict) and cfg.get("enabled", True) is True}

        unknown_levels = sorted(str(k) for k in routing if str(k) not in RISK_LEVELS)
        if unknown_levels:
            errors.append(f"routing.yaml: unknown risk levels: {', '.join(unknown_levels)}")
        for level in RISK_LEVELS:
            ids = routing.get(level)
            if not isinstance(ids, list) or not ids:
                # ModelRouter falls back to 'medium' for a missing level, which could route
                # critical work to a weaker provider; require every level explicitly.
                errors.append(f"routing.yaml: '{level}' must be a non-empty list of provider IDs")
                continue
            seen: set[str] = set()
            for raw in ids:
                pid = str(raw)
                if not isinstance(raw, str) or not raw.strip():
                    errors.append(f"routing '{level}': invalid provider ID {raw!r}")
                elif pid in seen:
                    errors.append(f"routing '{level}': duplicate provider '{pid}'")
                elif pid not in providers:
                    errors.append(f"routing '{level}': unknown provider '{pid}'")
                elif pid not in enabled:
                    errors.append(f"routing '{level}': provider '{pid}' is disabled")
                elif roles.get(pid) == "reviewer":
                    errors.append(f"routing '{level}': provider '{pid}' is reviewer-only and cannot implement")
                seen.add(pid)
        return errors

    # -- save -----------------------------------------------------------------

    def save(
        self,
        *,
        expected_version: str,
        routing: dict[str, Any] | None = None,
        reviewers_enabled: dict[str, bool] | None = None,
    ) -> tuple[ConfigSnapshot, list[str]]:
        """Validate and atomically write routing.yaml / reviewer enabled flags.

        Returns the new snapshot and the backup files created. Raises
        ConfigValidationError or ConfigConflictError without writing anything.
        """
        with self._lock:
            current = self.load()
            if current.version != expected_version:
                raise ConfigConflictError("config changed on disk since it was loaded; reload and retry")

            new_models = copy.deepcopy(current.models)
            new_routing = copy.deepcopy(current.routing)
            errors: list[str] = []

            if routing is not None:
                if not isinstance(routing, dict):
                    raise ConfigValidationError(["routing must be a mapping of risk level to provider list"])
                new_routing = {level: routing.get(level) for level in RISK_LEVELS}
                new_routing.update({k: v for k, v in routing.items() if k not in RISK_LEVELS})

            for pid, value in (reviewers_enabled or {}).items():
                cfg = current.providers.get(pid)
                if cfg is None:
                    errors.append(f"unknown provider '{pid}'")
                elif not isinstance(value, bool):
                    errors.append(f"provider '{pid}': enabled must be true or false")
                elif self.role_of(pid, cfg) != "reviewer":
                    errors.append(f"provider '{pid}' is not a reviewer; only reviewer providers can be toggled here")
                else:
                    new_models["providers"][pid]["enabled"] = value
            if errors:
                raise ConfigValidationError(errors)

            errors = self.validate(new_models, new_routing)
            if errors:
                raise ConfigValidationError(errors)

            writes: list[tuple[Path, str]] = []
            if new_models != current.models:
                models_text = current.models_text
                for pid, value in (reviewers_enabled or {}).items():
                    models_text = _set_enabled_in_text(models_text, pid, value)
                _assert_roundtrip(models_text, new_models, "models.yaml")
                writes.append((self.models_path, models_text))
            if new_routing != current.routing:
                routing_text = _dump_routing(new_routing, current.routing_text)
                _assert_roundtrip(routing_text, new_routing, "routing.yaml")
                writes.append((self.routing_path, routing_text))

            backups = self._write_all(writes)
            return self.load(), backups

    def _write_all(self, writes: list[tuple[Path, str]]) -> list[str]:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backups: list[tuple[Path, Path]] = []
        for path, _ in writes:
            if path.exists():
                self.backup_dir.mkdir(parents=True, exist_ok=True)
                backup = self.backup_dir / f"{path.name}.{stamp}.bak"
                shutil.copy2(path, backup)
                backups.append((path, backup))
        done: list[Path] = []
        try:
            for path, text in writes:
                _atomic_write(path, text)
                done.append(path)
        except Exception:
            # Roll back any file already replaced so both files stay consistent.
            for path, backup in backups:
                if path in done:
                    _atomic_write(path, backup.read_text())
            raise
        return [str(b) for _, b in backups]


# --- helpers -------------------------------------------------------------------


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _assert_roundtrip(text: str, expected: dict[str, Any], name: str) -> None:
    try:
        parsed = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigValidationError([f"{name}: generated YAML is invalid: {exc}"]) from exc
    if parsed != expected:
        raise ConfigValidationError([f"{name}: generated YAML does not match the validated config; nothing written"])


class _IndentedListDumper(yaml.SafeDumper):
    """Indent block sequences under their key (`  - id`), matching the repo's routing.yaml style."""

    def increase_indent(self, flow: bool = False, indentless: bool = False):  # type: ignore[override]
        return super().increase_indent(flow, False)


def _dump_routing(routing: dict[str, Any], original_text: str) -> str:
    """Serialize routing.yaml, keeping any leading comment header from the original file.

    Comments elsewhere in routing.yaml are not preserved (the file is regenerated).
    """
    header: list[str] = []
    for line in original_text.splitlines():
        if line.startswith("#") or (header and not line.strip()):
            header.append(line)
        else:
            break
    ordered = {level: routing[level] for level in RISK_LEVELS if level in routing}
    ordered.update({k: v for k, v in routing.items() if k not in ordered})
    parts = ["\n".join(header).rstrip() + "\n"] if header else []
    blocks = [
        yaml.dump({k: v}, Dumper=_IndentedListDumper, sort_keys=False, default_flow_style=False).rstrip()
        for k, v in ordered.items()
    ]
    return "".join(parts) + "\n\n".join(blocks) + "\n"


def _set_enabled_in_text(text: str, provider_id: str, enabled: bool) -> str:
    """Change one provider's `enabled:` value in place, preserving comments and layout."""
    lines = text.splitlines(keepends=True)
    value = "true" if enabled else "false"
    header_re = re.compile(rf"^(\s+){re.escape(provider_id)}:\s*(#.*)?$")
    for i, line in enumerate(lines):
        m = header_re.match(line.rstrip("\n"))
        if not m:
            continue
        indent = len(m.group(1))
        child_indent: str | None = None
        for j in range(i + 1, len(lines)):
            body = lines[j].rstrip("\n")
            stripped = body.strip()
            if not stripped or stripped.startswith("#"):
                continue
            cur = len(body) - len(body.lstrip())
            if cur <= indent:
                break
            if child_indent is None:
                child_indent = body[:cur]
            em = re.match(r"^(\s*)enabled:\s*[^#\s]*(\s*#.*)?$", body)
            if em and cur == len(child_indent):
                newline = "\n" if lines[j].endswith("\n") else ""
                lines[j] = f"{em.group(1)}enabled: {value}{em.group(2) or ''}{newline}"
                return "".join(lines)
        insert = f"{child_indent or ' ' * (indent + 2)}enabled: {value}\n"
        lines.insert(i + 1, insert)
        return "".join(lines)
    raise ConfigValidationError([f"provider '{provider_id}' not found in models.yaml text"])

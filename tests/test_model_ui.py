from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from agent_system.providers import ProviderRegistry  # noqa: E402
from agent_system.router import ModelRouter  # noqa: E402
from agent_system.ui import app as ui_app  # noqa: E402
from agent_system.ui import cli as ui_cli  # noqa: E402
from agent_system.ui import config_store  # noqa: E402
from agent_system.ui.config_store import REDACTED, ConfigConflictError, ConfigStore, ConfigValidationError, redact  # noqa: E402

MODELS = """\
providers:
  impl-a:
    type: mock
    name: "Mock Implementer"
    provider_id: "impl-a"
    tier: free
    enabled: true

  impl-b:
    type: command
    name: "Command Implementer"
    provider_id: "impl-b"
    tier: premium
    enabled: true
    base_url: "https://deploy:hunter2@example.invalid/v1"
    command:
      - /nonexistent/impl-tool
      - --api-key
      - sk-cli-secret
    timeout_seconds: 60
    env:
      SERVICE_TOKEN: "sk-env-secret"

  rev-a:
    type: ollama
    name: "Local Reviewer"
    provider_id: "rev-a"
    tier: free
    # Keep this reviewer local-only.
    enabled: true  # toggled from the UI
    base_url: "http://127.0.0.1:9"
    model: "qwen-test"
    role: "reviewer"
    timeout_seconds: 30

  rev-b:
    type: codex-reviewer
    name: "Premium Reviewer"
    provider_id: "rev-b"
    tier: premium
    enabled: false
    executable: /nonexistent/codex
    api_key: "sk-direct-secret"

  off-impl:
    type: mock
    provider_id: "off-impl"
    tier: free
    enabled: false
"""

ROUTING = """\
# Routing managed by agent-ui
low:
  - impl-a
  - impl-b

medium:
  - impl-a
  - impl-b

high:
  - impl-b
  - impl-a

critical:
  - impl-b
"""

SECRETS = ["hunter2", "sk-cli-secret", "sk-env-secret", "sk-direct-secret"]
HEADERS = {"X-Agent-UI": "1"}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "models.yaml").write_text(MODELS)
    (tmp_path / "config" / "routing.yaml").write_text(ROUTING)
    return tmp_path


@pytest.fixture
def client(root: Path) -> TestClient:
    return TestClient(ui_app.create_app(root), base_url="http://127.0.0.1:8765")


def files(root: Path) -> tuple[str, str]:
    return (root / "config" / "models.yaml").read_text(), (root / "config" / "routing.yaml").read_text()


# --- config load ------------------------------------------------------------------


def test_config_load_reads_existing_schema(root: Path, client: TestClient):
    snap = ConfigStore(root).load()
    assert snap.errors == []
    assert list(snap.providers) == ["impl-a", "impl-b", "rev-a", "rev-b", "off-impl"]
    assert snap.routing["critical"] == ["impl-b"]

    data = client.get("/api/config").json()
    assert data["valid"] is True
    assert data["implementers"] == ["impl-a", "impl-b"]
    assert data["reviewers"] == [
        {"provider_id": "rev-a", "enabled": True, "tier": "free"},
        {"provider_id": "rev-b", "enabled": False, "tier": "premium"},
    ]

    providers = {p["provider_id"]: p for p in client.get("/api/providers").json()["providers"]}
    assert providers["rev-a"] | {"config": None} == {
        "provider_id": "rev-a", "name": "Local Reviewer", "type": "ollama", "model": "qwen-test",
        "role": "reviewer", "tier": "free", "trust_status": "EXPERIMENTAL",
        "benchmark_status": "UNBENCHMARKED", "timeout_seconds": 30, "enabled": True, "config": None,
    }
    assert providers["off-impl"]["enabled"] is False
    assert providers["impl-b"]["role"] == "implementer"


def test_pages_render_without_probing_or_creating_state(root: Path, client: TestClient):
    page = client.get("/providers")
    assert page.status_code == 200
    assert "impl-a" in page.text and "not tested" in page.text
    assert client.get("/routing").status_code == 200
    assert not (root / ".agent-system").exists()


def test_reload_returns_current_disk_state(root: Path, client: TestClient):
    before = client.get("/api/config").json()
    (root / "config" / "routing.yaml").write_text(ROUTING.replace("  - impl-b\n", "", 1))
    after = client.post("/api/config/reload", headers=HEADERS).json()
    assert after["version"] != before["version"]
    assert after["routing"]["low"] == ["impl-a"]


# --- config validation ------------------------------------------------------------


@pytest.mark.parametrize(
    "routing, expected",
    [
        ({"low": ["impl-a"], "medium": ["impl-a"], "high": ["impl-a"]}, "'critical' must be a non-empty list"),
        ({"low": [], "medium": ["impl-a"], "high": ["impl-a"], "critical": ["impl-b"]}, "'low' must be a non-empty list"),
        ({"low": ["impl-a", "impl-a"], "medium": ["impl-a"], "high": ["impl-a"], "critical": ["impl-b"]}, "duplicate provider 'impl-a'"),
        ({"low": ["rev-a"], "medium": ["impl-a"], "high": ["impl-a"], "critical": ["impl-b"]}, "'rev-a' is reviewer-only"),
        ({"low": ["off-impl"], "medium": ["impl-a"], "high": ["impl-a"], "critical": ["impl-b"]}, "'off-impl' is disabled"),
        ({"low": ["impl-a"], "medium": ["impl-a"], "high": ["impl-a"], "critical": ["impl-b"], "urgent": ["impl-a"]}, "unknown risk levels: urgent"),
        ({"low": [7], "medium": ["impl-a"], "high": ["impl-a"], "critical": ["impl-b"]}, "invalid provider ID 7"),
    ],
)
def test_routing_validation_rejects_bad_routing(root: Path, routing, expected):
    store = ConfigStore(root)
    errors = store.validate(store.load().models, routing)
    assert any(expected in e for e in errors), errors


def test_validation_reports_invalid_yaml_and_bad_provider_entries(root: Path):
    (root / "config" / "routing.yaml").write_text("low: [impl-a\n")
    assert any("routing.yaml: invalid YAML" in e for e in ConfigStore(root).load().errors)

    (root / "config" / "routing.yaml").write_text(ROUTING)
    (root / "config" / "models.yaml").write_text(
        MODELS + "  bogus:\n    type: teleport\n    provider_id: other\n    tier: gold\n    enabled: 'yes'\n"
    )
    errors = ConfigStore(root).load().errors
    assert "provider 'bogus': unknown provider type 'teleport'" in errors
    assert "provider 'bogus': provider_id 'other' does not match its key" in errors
    assert "provider 'bogus': unknown tier 'gold'" in errors
    assert "provider 'bogus': enabled must be true or false" in errors


def test_current_repository_config_is_valid():
    repo_root = Path(__file__).resolve().parents[1]
    assert ConfigStore(repo_root).load().errors == []


# --- safe save ----------------------------------------------------------------------


def test_save_is_atomic_with_backup_and_preserves_comments(root: Path):
    store = ConfigStore(root)
    snap = store.load()
    new_routing = dict(snap.routing, low=["impl-b", "impl-a"])

    saved, backups = store.save(expected_version=snap.version, routing=new_routing, reviewers_enabled={"rev-a": False})

    models_text, routing_text = files(root)
    assert yaml.safe_load(routing_text)["low"] == ["impl-b", "impl-a"]
    assert routing_text.startswith("# Routing managed by agent-ui\n")
    assert "low:\n  - impl-b\n  - impl-a\n\nmedium:\n" in routing_text
    # Only the enabled line changed in models.yaml; comments and layout are intact.
    diff = [(a, b) for a, b in zip(MODELS.splitlines(), models_text.splitlines()) if a != b]
    assert diff == [("    enabled: true  # toggled from the UI", "    enabled: false  # toggled from the UI")]
    assert "# Keep this reviewer local-only." in models_text
    assert sorted(Path(b).name.split(".")[0] for b in backups) == ["models", "routing"]
    assert {Path(b).read_text() for b in backups} == {MODELS, ROUTING}
    assert all(Path(b).parent == root / ".agent-system" / "config-backups" for b in backups)
    assert not list((root / "config").glob(".*.tmp"))
    assert saved.version != snap.version and saved.errors == []


def test_save_noop_writes_nothing(root: Path):
    store = ConfigStore(root)
    snap = store.load()
    _, backups = store.save(expected_version=snap.version, routing=snap.routing, reviewers_enabled={"rev-a": True})
    assert backups == []
    assert files(root) == (MODELS, ROUTING)


def test_save_rejects_stale_version_without_writing(root: Path):
    store = ConfigStore(root)
    stale = store.load().version
    (root / "config" / "routing.yaml").write_text(ROUTING + "\n")
    with pytest.raises(ConfigConflictError):
        store.save(expected_version=stale, routing={"low": ["impl-b"]})
    assert files(root) == (MODELS, ROUTING + "\n")


def test_save_rolls_back_first_file_if_second_write_fails(root: Path, monkeypatch):
    store = ConfigStore(root)
    snap = store.load()
    real_write = config_store._atomic_write

    def flaky(path: Path, text: str) -> None:
        if path.name == "routing.yaml":
            raise OSError("disk full")
        real_write(path, text)

    monkeypatch.setattr(config_store, "_atomic_write", flaky)
    with pytest.raises(OSError):
        store.save(expected_version=snap.version, routing=dict(snap.routing, low=["impl-b"]), reviewers_enabled={"rev-a": False})
    assert files(root) == (MODELS, ROUTING)


def test_api_save_conflict_returns_409(root: Path, client: TestClient):
    version = client.get("/api/config").json()["version"]
    (root / "config" / "routing.yaml").write_text(ROUTING + "\n")
    r = client.post("/api/config", json={"version": version, "routing": {"low": ["impl-a"]}}, headers=HEADERS)
    assert r.status_code == 409


# --- invalid provider rejection -----------------------------------------------------


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"routing": {"low": ["ghost"], "medium": ["impl-a"], "high": ["impl-a"], "critical": ["impl-b"]}}, "unknown provider 'ghost'"),
        ({"reviewers_enabled": {"ghost": True}}, "unknown provider 'ghost'"),
        ({"reviewers_enabled": {"impl-a": False}}, "'impl-a' is not a reviewer"),
        ({"reviewers_enabled": {"rev-a": "yes"}}, "enabled must be true or false"),
    ],
)
def test_api_rejects_invalid_or_unknown_providers(root: Path, client: TestClient, payload, expected):
    version = client.get("/api/config").json()["version"]
    r = client.post("/api/config", json={"version": version, **payload}, headers=HEADERS)
    assert r.status_code == 400
    assert any(expected in e for e in r.json()["errors"]), r.json()
    assert files(root) == (MODELS, ROUTING)
    assert not (root / ".agent-system" / "config-backups").exists()


def test_disabling_routed_provider_is_impossible_from_reviewer_toggles(root: Path):
    # Implementers can't be toggled here, so a save can never leave routing pointing at a disabled provider.
    store = ConfigStore(root)
    with pytest.raises(ConfigValidationError, match="not a reviewer"):
        store.save(expected_version=store.load().version, reviewers_enabled={"impl-b": False})


# --- routing update -----------------------------------------------------------------


def test_api_routing_update_is_consumed_by_existing_router(root: Path, client: TestClient):
    state = client.get("/api/config").json()
    routing = state["routing"] | {"medium": ["impl-b", "impl-a"], "critical": ["impl-a", "impl-b"]}

    r = client.post("/api/config", json={"version": state["version"], "routing": routing, "reviewers_enabled": {"rev-b": True}}, headers=HEADERS)

    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["saved"] is True and body["routing"]["medium"] == ["impl-b", "impl-a"]
    assert {"provider_id": "rev-b", "enabled": True, "tier": "premium"} in body["reviewers"]
    assert yaml.safe_load((root / "config" / "models.yaml").read_text())["providers"]["rev-b"]["enabled"] is True
    # The unchanged ModelRouter reads the saved file: impl-a is the only available one (impl-b's command is missing).
    router = ModelRouter(ProviderRegistry(root / "config" / "models.yaml"), root / "config" / "routing.yaml")
    decision = router.route("critical")
    assert decision.provider.provider_id == "impl-a"
    assert "Tried" not in decision.reason


# --- secret redaction ---------------------------------------------------------------


def test_redact_masks_secret_keys_env_urls_and_cli_flags():
    cfg = yaml.safe_load(MODELS)["providers"]
    assert redact(cfg["impl-b"]) | {} == {
        **cfg["impl-b"],
        "base_url": f"https://{REDACTED}@example.invalid/v1",
        "command": ["/nonexistent/impl-tool", "--api-key", REDACTED],
        "env": {"SERVICE_TOKEN": REDACTED},
    }
    assert redact(cfg["rev-b"])["api_key"] == REDACTED
    assert redact({"model_env": "OLLAMA_MODEL", "timeout_seconds": 5}) == {"model_env": "OLLAMA_MODEL", "timeout_seconds": 5}


def test_secrets_never_reach_ui_responses(client: TestClient):
    bodies = [
        client.get("/api/providers").text,
        client.get("/providers").text,
        client.get("/api/config").text,
        client.get("/routing").text,
    ]
    for body in bodies:
        for secret in SECRETS:
            assert secret not in body
    assert REDACTED in bodies[0]


# --- availability / actions ---------------------------------------------------------


def test_availability_endpoint(client: TestClient):
    ok = client.post("/api/providers/impl-a/availability", headers=HEADERS)
    assert ok.status_code == 200 and ok.json()["availability"] == "AVAILABLE"
    missing_cmd = client.post("/api/providers/impl-b/availability", headers=HEADERS)
    assert missing_cmd.json()["availability"] == "UNAVAILABLE"
    assert client.post("/api/providers/ghost/availability", headers=HEADERS).status_code == 404
    assert client.post("/api/providers/off-impl/availability", headers=HEADERS).status_code == 409


def test_state_changing_requests_require_ui_header_and_loopback_host(root: Path, client: TestClient):
    assert client.post("/api/providers/impl-a/availability").status_code == 403
    rebinding = TestClient(ui_app.create_app(root), base_url="http://evil.example:8765")
    assert rebinding.get("/api/providers").status_code == 403
    assert TestClient(ui_app.create_app(root), base_url="http://localhost:8765").get("/api/config").status_code == 200


def test_benchmark_action_runs_existing_command_for_selected_provider(root: Path):
    seen: list[str] = []

    def fake_command(pid: str) -> list[str]:
        seen.append(pid)
        return ["python3", "-c", "print('Run 1/1: ok token=sk-leak')"]

    client = TestClient(ui_app.create_app(root, benchmark_command=fake_command), base_url="http://127.0.0.1:8765")
    job = client.post("/api/providers/impl-a/benchmark", headers=HEADERS).json()
    for _ in range(100):
        state = client.get(f"/api/benchmarks/{job['job_id']}").json()
        if state["status"] != "running":
            break
        time.sleep(0.05)
    assert seen == ["impl-a"]
    assert state["status"] == "passed" and "Run 1/1: ok" in state["output"]
    assert "sk-leak" not in state["output"]
    assert client.post("/api/providers/off-impl/benchmark", headers=HEADERS).status_code == 409
    assert ui_app._default_benchmark_command("impl-a")[-3:] == ["agent_system.cli", "benchmark", "impl-a"]


def test_agent_ui_refuses_non_loopback_bind(root: Path):
    with pytest.raises(SystemExit, match="loopback-only"):
        ui_cli.main(["--host", "0.0.0.0", "--root", str(root)])

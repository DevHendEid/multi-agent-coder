from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import yaml


class ProjectConfigError(RuntimeError):
    pass


@dataclass(slots=True)
class ProjectConfig:
    name: str
    repo_path: Path
    commands: dict[str, str]
    protected_paths: list[str]
    risk_rules: dict[str, str]
    allowed_generated_paths: list[str] = field(default_factory=list)
    timeouts: dict[str, int] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)

    COMMAND_KEYS = (
        "install",
        "analyze",
        "lint",
        "test",
        "build",
        "integration_test",
    )

    @classmethod
    def load(cls, path: str | Path) -> "ProjectConfig":
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise ProjectConfigError(f"Project config not found: {p}")
        data = yaml.safe_load(p.read_text()) or {}
        try:
            repo_path = Path(data["repository"]["path"]).expanduser().resolve()
            cfg = cls(
                name=data["name"],
                repo_path=repo_path,
                commands=data.get("commands", {}),
                protected_paths=data.get("protected_paths", []),
                risk_rules=data.get("risk_rules", {}),
                allowed_generated_paths=data.get("allowed_generated_paths", []),
                timeouts=data.get("timeouts", {}),
                environment=data.get("environment", {}),
            )
        except KeyError as exc:
            raise ProjectConfigError(f"Missing required project config key: {exc}") from exc
        cfg.validate_repository()
        return cfg

    def validate_repository(self) -> None:
        if not self.repo_path.exists():
            raise ProjectConfigError(f"Repository path does not exist: {self.repo_path}")
        result = subprocess.run(
            ["git", "-C", str(self.repo_path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or result.stdout.strip() != "true":
            raise ProjectConfigError(f"Not a Git repository: {self.repo_path}")

    def get_command(self, key: str) -> str | None:
        return self.commands.get(key, "").strip() or None

    def get_timeout(self, key: str) -> int:
        return self.timeouts.get(key, 300)

    def has_command(self, key: str) -> bool:
        return bool(self.get_command(key))

    def get_verification_commands(self, risk: str) -> list[tuple[str, str]]:
        commands = []
        if self.has_command("analyze"):
            cmd = self.get_command("analyze")
            if cmd:
                commands.append(("analyze", cmd))
        if self.has_command("lint"):
            cmd = self.get_command("lint")
            if cmd:
                commands.append(("lint", cmd))
        if self.has_command("test"):
            cmd = self.get_command("test")
            if cmd:
                commands.append(("test", cmd))
        if risk in ("medium", "high", "critical") and self.has_command("build"):
            cmd = self.get_command("build")
            if cmd:
                commands.append(("build", cmd))
        if risk in ("high", "critical") and self.has_command("integration_test"):
            cmd = self.get_command("integration_test")
            if cmd:
                commands.append(("integration_test", cmd))
        return commands

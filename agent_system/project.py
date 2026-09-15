from __future__ import annotations

from dataclasses import dataclass
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

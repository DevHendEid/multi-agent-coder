from pathlib import Path
import subprocess
import yaml
from agent_system.project import ProjectConfig


def test_project_config_loads_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    cfg_path = tmp_path / "project.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "name": "demo",
        "repository": {"path": str(repo)},
        "commands": {"test": "pytest"},
        "protected_paths": [".env"],
        "risk_rules": {"docs": "low"},
    }))
    cfg = ProjectConfig.load(cfg_path)
    assert cfg.name == "demo"
    assert cfg.repo_path == repo.resolve()

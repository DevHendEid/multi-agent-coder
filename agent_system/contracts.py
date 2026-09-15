from __future__ import annotations

from pathlib import Path
import yaml
from .models import TaskContract


def write_contract(contract: TaskContract, tasks_dir: str | Path) -> Path:
    out_dir = Path(tasks_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{contract.id}.yaml"
    out.write_text(yaml.safe_dump(contract.to_dict(), sort_keys=False, allow_unicode=True))
    return out

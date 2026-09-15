from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    NEW = "NEW"
    PLANNED = "PLANNED"
    CONTEXT_READY = "CONTEXT_READY"
    IMPLEMENTING = "IMPLEMENTING"
    IMPLEMENTED = "IMPLEMENTED"
    VERIFYING = "VERIFYING"
    REVIEWING = "REVIEWING"
    REPAIRING = "REPAIRING"
    WAITING_PROVIDER = "WAITING_PROVIDER"
    WAITING_CODEX = "WAITING_CODEX"
    WAITING_DEPENDENCY = "WAITING_DEPENDENCY"
    WAITING_HUMAN = "WAITING_HUMAN"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    PR_READY = "PR_READY"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(slots=True)
class TaskContract:
    id: str
    goal: str
    project: str
    risk: str = RiskLevel.MEDIUM.value
    allowed_scope: list[str] | None = None
    forbidden_scope: list[str] | None = None
    acceptance: list[str] | None = None
    max_files_changed: int = 8
    max_free_attempts: int = 2
    auto_merge: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

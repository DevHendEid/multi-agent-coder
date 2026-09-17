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
    READY_FOR_PR = "READY_FOR_PR"
    PR_READY = "PR_READY"
    PR_OPEN = "PR_OPEN"
    CI_PENDING = "CI_PENDING"
    CI_FAILED = "CI_FAILED"
    MERGE_PENDING = "MERGE_PENDING"
    MERGE_BLOCKED = "MERGE_BLOCKED"
    MERGED = "MERGED"
    POST_MERGE_VERIFYING = "POST_MERGE_VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


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
    production_target: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

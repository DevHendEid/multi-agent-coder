from __future__ import annotations
from .models import RiskLevel

CRITICAL_WORDS = {"secret", "credential", "production", "deploy", "payment", "migration", "delete database"}
HIGH_WORDS = {"architecture", "auth", "security", "scoring", "gateway", "encryption"}
LOW_WORDS = {"readme", "docs", "documentation", "typo", "format", "comment"}


def classify_risk(goal: str) -> str:
    g = goal.lower()
    if any(w in g for w in CRITICAL_WORDS):
        return RiskLevel.CRITICAL.value
    if any(w in g for w in HIGH_WORDS):
        return RiskLevel.HIGH.value
    if any(w in g for w in LOW_WORDS):
        return RiskLevel.LOW.value
    return RiskLevel.MEDIUM.value

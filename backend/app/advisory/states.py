"""Advisor-facing coverage states. These labels do not score a policy."""
from __future__ import annotations

_STATE = {
    "COVERED": "COVERED",
    "PARTIALLY_COVERED": "PARTIAL",
    "CONDITIONAL": "CONDITIONAL",
    "ADD_ON": "ADD_ON",
    "EXCLUDED": "EXCLUDED",
    "NOT_FOUND": "NOT_ESTABLISHED",
    "UNKNOWN": "NOT_ESTABLISHED",
    "REVIEW_REQUIRED": "REVIEW_REQUIRED",
}

_LABEL = {
    "COVERED": "Covered",
    "PARTIAL": "Partial",
    "CONDITIONAL": "Conditional",
    "ADD_ON": "Optional add-on",
    "EXCLUDED": "Excluded",
    "NOT_ESTABLISHED": "Not established",
    "REVIEW_REQUIRED": "Review required",
}


def coverage_state(status: str | None) -> str:
    return _STATE.get(str(status or "NOT_FOUND"), "NOT_ESTABLISHED")


def coverage_label(state: str) -> str:
    return _LABEL.get(state, "Not established")

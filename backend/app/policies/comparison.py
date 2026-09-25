"""Deterministic four-policy comparison matrix built from structured FeatureFacts.

Every cell keeps the full fact (and therefore its SourceRefs) so the UI can open provenance.
"""
from __future__ import annotations

from app.models.policy import ComparisonCell, ComparisonMatrix, CoverageStatus, FeatureFact, PolicyExtractionResult
from app.policies.features import DEFAULT_MATRIX_FEATURES

STATUS_LABEL = {
    CoverageStatus.COVERED: "Covered",
    CoverageStatus.PARTIALLY_COVERED: "Partial",
    CoverageStatus.CONDITIONAL: "Conditional",
    CoverageStatus.EXCLUDED: "Excluded",
    CoverageStatus.ADD_ON: "Add-on",
    CoverageStatus.NOT_FOUND: "Not found",
    CoverageStatus.UNKNOWN: "Unknown",
}


def cell_display(fact: FeatureFact) -> str:
    status = STATUS_LABEL[fact.coverage_status]
    if fact.coverage_status in {CoverageStatus.NOT_FOUND, CoverageStatus.UNKNOWN}:
        return status
    detail = fact.limit or fact.waiting_period or fact.copay or fact.deductible
    if not detail and fact.value:
        v = fact.value
        detail = v.split(":", 1)[1].strip() if ":" in v and len(v.split(":", 1)[1].strip()) > 3 else v
    if detail and len(detail) > 70:
        detail = detail[:67].rstrip() + "..."
    return f"{status}: {detail}" if detail else status


def build_matrix(results: dict[str, PolicyExtractionResult], features: list[str] | None = None, policy_order: list[str] | None = None) -> ComparisonMatrix:
    feats = features or DEFAULT_MATRIX_FEATURES
    pids = policy_order or list(results.keys())
    cells: dict[str, dict[str, ComparisonCell]] = {}
    for f in feats:
        cells[f] = {}
        for pid in pids:
            fact = results[pid].facts.get(f) if pid in results else None
            if fact is None:
                fact = FeatureFact(policy_id=pid, feature=f, coverage_status=CoverageStatus.NOT_FOUND, notes="Feature not extracted")
            cells[f][pid] = ComparisonCell(feature=f, policy_id=pid, status=fact.coverage_status, display=cell_display(fact), fact=fact)
    return ComparisonMatrix(features=feats, policy_ids=pids, cells=cells)

"""Reciprocal Rank Fusion."""
from __future__ import annotations

from typing import Sequence


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = 60, weights: Sequence[float] | None = None) -> dict[str, float]:
    """Fuse ranked lists of ids. Returns id -> fused score (higher is better)."""
    weights = list(weights) if weights else [1.0] * len(rankings)
    fused: dict[str, float] = {}
    for ranking, w in zip(rankings, weights):
        for rank, cid in enumerate(ranking, start=1):
            fused[cid] = fused.get(cid, 0.0) + w * (1.0 / (k + rank))
    return fused

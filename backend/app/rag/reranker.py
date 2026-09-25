"""Cross-encoder reranking with fastembed (ONNX, no PyTorch). Optional via RERANKER_ENABLED."""
from __future__ import annotations

from typing import Sequence

from app.config import get_settings
from app.utils.logging import get_logger

log = get_logger(__name__)


class CrossEncoderReranker:
    def __init__(self, model: str | None = None):
        settings = get_settings()
        self.model_name = model or settings.reranker_model
        self._model = None
        self.available = False
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._model = TextCrossEncoder(model_name=self.model_name, cache_dir=str(settings.cache_path / "models"))
            self.available = True
        except Exception as exc:  # pragma: no cover - depends on network / model cache
            log.warning("Reranker unavailable (%s); continuing with fused scores only", exc)

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not self.available or not passages:
            return [0.0] * len(passages)
        try:
            scores = list(self._model.rerank(query, [p[:1500] for p in passages], batch_size=16))
            return [float(s) for s in scores]
        except Exception as exc:  # pragma: no cover
            log.warning("Rerank failed (%s); using fused scores", exc)
            return [0.0] * len(passages)


class NoopReranker:
    available = False
    model_name = "none"

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        return [0.0] * len(passages)


def get_reranker():
    settings = get_settings()
    if not settings.reranker_enabled:
        return NoopReranker()
    return CrossEncoderReranker()

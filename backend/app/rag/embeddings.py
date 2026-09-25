"""Embedding providers: local fastembed ONNX (default, offline, no quota) or Gemini embeddings.

`gemini` uses GEMINI_EMBEDDING_MODEL through the same GEMINI_API_KEY. Every query embedding then
consumes free-tier quota, so it is opt-in; the shipped indexes were built with the local model."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Sequence

import numpy as np

from app.config import get_settings
from app.utils.logging import get_logger

log = get_logger(__name__)


class EmbeddingError(RuntimeError):
    pass


def _normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype="float32")
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return v / norms


class GeminiEmbedder:
    DIM = 768  # requested via output_dimensionality; keeps FAISS compact and comparable across models

    def __init__(self, model: str | None = None, cache_dir: Path | None = None):
        settings = get_settings()
        if not settings.gemini_api_key:
            raise EmbeddingError("GEMINI_API_KEY is not set; cannot use Gemini embeddings (set EMBEDDING_PROVIDER=local for offline)")
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=settings.gemini_api_key, http_options=types.HttpOptions(timeout=60_000, retry_options=types.HttpRetryOptions(attempts=1)))
        self.model_name = model or settings.gemini_embedding_model
        self.dim = self.DIM
        self._cache_dir = (cache_dir or settings.cache_path) / "embeddings" / self.model_name.replace("/", "_")
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, text: str, task: str) -> Path:
        return self._cache_dir / (hashlib.sha1(f"{task}|{text}".encode("utf-8")).hexdigest() + ".json")

    def _embed(self, texts: Sequence[str], task: str) -> np.ndarray:
        out: list[np.ndarray | None] = [None] * len(texts)
        todo: list[int] = []
        for i, t in enumerate(texts):
            p = self._cache_key(t, task)
            if p.exists():
                out[i] = np.asarray(json.loads(p.read_text()), dtype="float32")
            else:
                todo.append(i)
        for start in range(0, len(todo), 50):
            batch_idx = todo[start : start + 50]
            batch = [texts[i].replace("\n", " ")[:8000] for i in batch_idx]
            try:
                resp = self._client.models.embed_content(model=self.model_name, contents=batch, config=self._types.EmbedContentConfig(task_type=task, output_dimensionality=self.dim))
            except Exception as exc:
                if getattr(exc, "code", None) == 429:
                    raise EmbeddingError("Gemini free-tier quota reached while embedding; retry later or set EMBEDDING_PROVIDER=local") from exc
                raise EmbeddingError(f"Gemini embedding call failed: {getattr(exc, 'message', exc)}") from exc
            for i, item in zip(batch_idx, resp.embeddings or []):
                vec = np.asarray(item.values, dtype="float32")
                out[i] = vec
                self._cache_key(texts[i], task).write_text(json.dumps(vec.tolist()))
        return _normalize(np.vstack([o for o in out]))  # type: ignore[arg-type]

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text], "RETRIEVAL_QUERY")[0]


class LocalEmbedder:
    """fastembed ONNX model (no PyTorch). Used for offline tests and as a fallback."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        try:
            from fastembed import TextEmbedding
        except Exception as exc:  # pragma: no cover
            raise EmbeddingError(f"fastembed not available: {exc}") from exc
        settings = get_settings()
        self.model_name = model
        self._model = TextEmbedding(model_name=model, cache_dir=str(settings.cache_path / "models"))
        self.dim = 384

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        vecs = list(self._model.embed(list(texts), batch_size=32))
        return _normalize(np.vstack(vecs))

    def embed_query(self, text: str) -> np.ndarray:
        vecs = list(self._model.query_embed(text))
        return _normalize(np.vstack(vecs))[0]


def get_embedder(provider: str | None = None):
    settings = get_settings()
    provider = (provider or settings.embedding_provider).lower()
    if provider == "gemini":
        if not settings.gemini_api_key:
            log.warning("GEMINI_API_KEY missing; falling back to local embeddings")
            return LocalEmbedder()
        return GeminiEmbedder()
    if provider != "local":
        log.warning("Unknown EMBEDDING_PROVIDER=%s; using local embeddings", provider)
    return LocalEmbedder()

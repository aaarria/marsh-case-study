"""FAISS flat inner-product index (vectors are L2-normalised => inner product == cosine)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import numpy as np

from app.config import get_settings


class FaissVectorStore:
    def __init__(self, path: Path | None = None):
        self.path = path or get_settings().vector_index_path
        self.path.mkdir(parents=True, exist_ok=True)
        self._index = None
        self._ids: list[str] = []
        self._meta: dict = {}

    @property
    def index_file(self) -> Path:
        return self.path / "faiss.index"

    @property
    def meta_file(self) -> Path:
        return self.path / "metadata.json"

    def build(self, chunk_ids: Sequence[str], vectors: np.ndarray, meta: dict | None = None) -> None:
        import faiss

        vectors = np.ascontiguousarray(vectors.astype("float32"))
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        self._index = index
        self._ids = list(chunk_ids)
        self._meta = {"dim": int(vectors.shape[1]), "count": len(self._ids), **(meta or {})}

    def search(self, query_vec: np.ndarray, top_k: int, allowed_ids: set[str] | None = None) -> list[tuple[str, float]]:
        if self._index is None or not self._ids:
            return []
        q = np.ascontiguousarray(query_vec.astype("float32").reshape(1, -1))
        # Over-fetch when filtering so the per-policy quota is still met
        k = min(len(self._ids), top_k if allowed_ids is None else max(top_k * 6, 60))
        scores, idxs = self._index.search(q, k)
        out: list[tuple[str, float]] = []
        for s, i in zip(scores[0], idxs[0]):
            if i < 0:
                continue
            cid = self._ids[i]
            if allowed_ids is not None and cid not in allowed_ids:
                continue
            out.append((cid, float(s)))
            if len(out) >= top_k:
                break
        return out

    def save(self) -> None:
        import faiss

        if self._index is None:
            return
        faiss.write_index(self._index, str(self.index_file))
        self.meta_file.write_text(json.dumps({"ids": self._ids, **self._meta}))

    def load(self) -> bool:
        import faiss

        if not (self.index_file.exists() and self.meta_file.exists()):
            return False
        self._index = faiss.read_index(str(self.index_file))
        raw = json.loads(self.meta_file.read_text())
        self._ids = raw.pop("ids")
        self._meta = raw
        return True

    def size(self) -> int:
        return len(self._ids)

    @property
    def meta(self) -> dict:
        return dict(self._meta)

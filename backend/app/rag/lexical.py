"""BM25 lexical index via bm25s (exact terms, numbers, currency, medical terminology)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Sequence

from app.config import get_settings

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*|%")

_SYNONYMS = {
    "hospitalisation": "hospitalization",
    "hospitalised": "hospitalized",
    "lakhs": "lakh",
    "lacs": "lakh",
    "lac": "lakh",
    "crores": "crore",
    "cr": "crore",
    "rs": "inr",
    "₹": "inr",
    "copay": "co-payment",
    "copayment": "co-payment",
    "opd": "outpatient",
    "ped": "pre-existing",
    "si": "sum insured",
    "bsi": "base sum insured",
}


def tokenize(text: str) -> list[str]:
    text = text.lower().replace("-", " ")
    toks = _TOKEN_RE.findall(text)
    out: list[str] = []
    for t in toks:
        t = _SYNONYMS.get(t, t)
        out.extend(t.split())
    return out


class BM25Index:
    def __init__(self, path: Path | None = None):
        self.path = path or get_settings().bm25_index_path
        self.path.mkdir(parents=True, exist_ok=True)
        self._retriever = None
        self._ids: list[str] = []

    def build(self, chunk_ids: Sequence[str], texts: Sequence[str]) -> None:
        import bm25s

        corpus_tokens = [tokenize(t) for t in texts]
        retriever = bm25s.BM25(k1=1.2, b=0.75)
        retriever.index(corpus_tokens, show_progress=False)
        self._retriever = retriever
        self._ids = list(chunk_ids)

    def search(self, query: str, top_k: int, allowed_ids: set[str] | None = None) -> list[tuple[str, float]]:
        if self._retriever is None or not self._ids:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        k = min(len(self._ids), top_k if allowed_ids is None else max(top_k * 6, 60))
        docs, scores = self._retriever.retrieve([q_tokens], k=k, show_progress=False)
        out: list[tuple[str, float]] = []
        for i, s in zip(docs[0], scores[0]):
            if float(s) <= 0:
                continue
            cid = self._ids[int(i)]
            if allowed_ids is not None and cid not in allowed_ids:
                continue
            out.append((cid, float(s)))
            if len(out) >= top_k:
                break
        return out

    def save(self) -> None:
        if self._retriever is None:
            return
        self._retriever.save(str(self.path / "bm25s"))
        (self.path / "ids.json").write_text(json.dumps(self._ids))

    def load(self) -> bool:
        import bm25s

        if not (self.path / "ids.json").exists():
            return False
        self._retriever = bm25s.BM25.load(str(self.path / "bm25s"))
        self._ids = json.loads((self.path / "ids.json").read_text())
        return True

    def size(self) -> int:
        return len(self._ids)

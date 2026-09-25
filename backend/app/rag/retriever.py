"""Hybrid, policy-balanced retrieval.

For each query the retriever runs BM25 + dense search *independently per policy_id* (quota), fuses
with RRF, optionally reranks with a cross-encoder, then attaches linked footnote/condition chunks and
parent context. This guarantees every policy is represented for each criterion.

Scores are relevance signals; they are never presented as factual accuracy.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from app.config import get_settings
from app.models.policy import Chunk, ContentType, RetrievedChunk
from app.rag.embeddings import get_embedder
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.lexical import BM25Index
from app.rag.metadata_store import SQLiteMetadataStore
from app.rag.reranker import get_reranker
from app.rag.vector_store import FaissVectorStore
from app.utils.logging import get_logger

log = get_logger(__name__)

NON_EVIDENCE_TYPES = {ContentType.SECTION.value}


class RetrievalError(RuntimeError):
    pass


@dataclass
class PolicyEvidence:
    policy_id: str
    query: str
    results: list[RetrievedChunk] = field(default_factory=list)
    conditions: list[Chunk] = field(default_factory=list)  # linked footnotes / conditions


class HybridRetriever:
    def __init__(
        self,
        store: SQLiteMetadataStore | None = None,
        vector: FaissVectorStore | None = None,
        lexical: BM25Index | None = None,
        embedder=None,
        reranker=None,
        load: bool = True,
    ):
        self.settings = get_settings()
        self.store = store or SQLiteMetadataStore()
        self.vector = vector or FaissVectorStore()
        self.lexical = lexical or BM25Index()
        self._embedder = embedder
        self._reranker = reranker
        self._policy_ids_cache: dict[str, set[str]] = {}
        self.ready = False
        if load:
            self.ready = self.load()

    # ---- lazy heavy deps ----
    @property
    def embedder(self):
        if self._embedder is None:
            provider = self.vector.meta.get("embedding_provider") if self.vector.meta else None
            self._embedder = get_embedder(provider)
        return self._embedder

    @property
    def reranker(self):
        if self._reranker is None:
            self._reranker = get_reranker()
        return self._reranker

    def load(self) -> bool:
        ok_v = self.vector.load()
        ok_l = self.lexical.load()
        if not (ok_v and ok_l):
            log.warning("Indexes not found; run `python backend/scripts/ingest_policies.py`")
            return False
        return True

    def status(self) -> dict:
        return {
            "ready": self.ready,
            "chunks": self.store.count_chunks(),
            "vector_size": self.vector.size(),
            "bm25_size": self.lexical.size(),
            "embedding_model": self.vector.meta.get("embedding_model") if self.vector.meta else None,
            "reranker": getattr(self.reranker, "model_name", "none") if self.settings.reranker_enabled else "disabled",
            "policies": [p.model_dump() for p in self.store.list_policies()],
        }

    def _allowed(self, policy_id: str) -> set[str]:
        if policy_id not in self._policy_ids_cache:
            self._policy_ids_cache[policy_id] = self.store.chunk_ids_for_policy(policy_id)
        return self._policy_ids_cache[policy_id]

    # ---- core ----
    def retrieve_for_policy(
        self,
        query: str,
        policy_id: str,
        top_k: int = 6,
        candidate_k: int = 20,
        content_types: set[str] | None = None,
        rerank: bool | None = None,
        use_cache: bool = True,
    ) -> PolicyEvidence:
        if not self.ready:
            raise RetrievalError("Retrieval indexes are not built. Run the ingestion script first.")
        cache_key = hashlib.sha1(json.dumps([query, policy_id, top_k, candidate_k, sorted(content_types or []), bool(rerank)]).encode()).hexdigest()
        if use_cache:
            cached = self.store.cache_get("retrieval", cache_key)
            if cached:
                try:
                    results = [RetrievedChunk.model_validate(r) for r in cached["results"]]
                    conds = [Chunk.model_validate(c) for c in cached["conditions"]]
                    return PolicyEvidence(policy_id=policy_id, query=query, results=results, conditions=conds)
                except Exception:
                    pass

        allowed = self._allowed(policy_id)
        if not allowed:
            return PolicyEvidence(policy_id=policy_id, query=query)

        bm25_hits = self.lexical.search(query, candidate_k, allowed_ids=allowed)
        qvec = self.embedder.embed_query(query)
        dense_hits = self.vector.search(qvec, candidate_k, allowed_ids=allowed)

        bm25_rank = {cid: i + 1 for i, (cid, _) in enumerate(bm25_hits)}
        dense_rank = {cid: i + 1 for i, (cid, _) in enumerate(dense_hits)}
        dense_score = {cid: s for cid, s in dense_hits}
        fused = reciprocal_rank_fusion([[c for c, _ in bm25_hits], [c for c, _ in dense_hits]], k=60, weights=[1.0, 1.0])
        if not fused:
            return PolicyEvidence(policy_id=policy_id, query=query)

        ordered = sorted(fused.items(), key=lambda kv: -kv[1])
        chunks = {c.chunk_id: c for c in self.store.get_chunks([cid for cid, _ in ordered])}
        cands: list[RetrievedChunk] = []
        for cid, fs in ordered:
            ch = chunks.get(cid)
            if not ch or ch.content_type.value in NON_EVIDENCE_TYPES:
                continue
            if content_types and ch.content_type.value not in content_types:
                continue
            cands.append(
                RetrievedChunk(
                    chunk=ch,
                    bm25_rank=bm25_rank.get(cid),
                    dense_rank=dense_rank.get(cid),
                    dense_score=dense_score.get(cid),
                    fused_score=fs,
                    final_score=fs,
                )
            )
        cands = cands[: max(candidate_k, top_k)]

        do_rerank = self.settings.reranker_enabled if rerank is None else rerank
        if do_rerank and cands and getattr(self.reranker, "available", False):
            scores = self.reranker.score(query, [c.chunk.index_text for c in cands])
            if any(s != 0.0 for s in scores):
                lo, hi = min(scores), max(scores)
                span = (hi - lo) or 1.0
                # blend: normalised rerank score with RRF rank prior (RRF as a strong prior)
                for c, s in zip(cands, scores):
                    rrf_norm = c.fused_score / (ordered[0][1] or 1.0)
                    c.final_score = 0.7 * ((s - lo) / span) + 0.3 * rrf_norm
        else:
            top = ordered[0][1] or 1.0
            for c in cands:
                c.final_score = c.fused_score / top
        cands.sort(key=lambda c: -c.final_score)
        results = cands[:top_k]

        # attach linked conditions / footnotes and parent table context
        cond_ids: list[str] = []
        for r in results:
            cond_ids.extend(r.chunk.footnote_refs)
        conditions = self.store.get_chunks(cond_ids) if cond_ids else []
        ev = PolicyEvidence(policy_id=policy_id, query=query, results=results, conditions=conditions)
        if use_cache:
            self.store.cache_set(
                "retrieval",
                cache_key,
                {"results": [r.model_dump(mode="json") for r in results], "conditions": [c.model_dump(mode="json") for c in conditions]},
            )
        return ev

    def retrieve_feature(self, queries: list[str], policy_id: str, top_k: int = 8, **kw) -> PolicyEvidence:
        """Multiple sub-queries for one feature/policy, merged by max final score."""
        merged: dict[str, RetrievedChunk] = {}
        conds: dict[str, Chunk] = {}
        for q in queries:
            ev = self.retrieve_for_policy(q, policy_id, top_k=top_k, **kw)
            for r in ev.results:
                if r.chunk.chunk_id not in merged or r.final_score > merged[r.chunk.chunk_id].final_score:
                    merged[r.chunk.chunk_id] = r
            for c in ev.conditions:
                conds[c.chunk_id] = c
        results = sorted(merged.values(), key=lambda r: -r.final_score)[:top_k]
        return PolicyEvidence(policy_id=policy_id, query=" || ".join(queries), results=results, conditions=list(conds.values()))

    def policy_chunks(self, policy_id: str, content_types: list[str] | None = None) -> list[Chunk]:
        return self.store.list_chunks(policy_id=policy_id, content_types=content_types)


_retriever: HybridRetriever | None = None


def get_retriever() -> HybridRetriever:
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()
    return _retriever


def reset_retriever() -> None:
    global _retriever
    _retriever = None

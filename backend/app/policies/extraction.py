"""Structured policy-fact extraction (per policy x per feature).

Retrieval is deterministic and policy-scoped; the LLM only turns the retrieved passages into a
validated `FeatureFact`. Evidence ids are echoed back and mapped to SourceRefs deterministically.
A status other than NOT_FOUND without cited evidence is downgraded (never fabricate).

A heuristic extractor exists for offline mode/tests: it only reads table rows / typed chunks and
marks everything else UNKNOWN.
"""
from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from app.config import get_settings
from app.models.policy import Chunk, ContentType, CoverageStatus, FeatureFact, PolicyExtractionResult, SourceRef
from app.policies.features import FEATURES, FEATURE_BY_KEY, FeatureSpec
from app.rag.metadata_store import SQLiteMetadataStore
from app.rag.retriever import HybridRetriever, PolicyEvidence, get_retriever
from app.services.llm import LLMQuotaExceeded, LLMService, LLMUnavailable, get_llm
from app.utils.logging import get_logger

log = get_logger(__name__)

EXTRACTION_SYSTEM = """You are a meticulous insurance policy analyst working for Marsh.
You read ONLY the numbered evidence passages from ONE insurer's product brochure and fill a structured record for ONE feature.
Rules:
- Use only the evidence. Never use outside knowledge about the insurer or product.
- If the evidence does not establish a field, set it to null. If the evidence does not address the feature at all, set coverage_status = "NOT_FOUND". NOT_FOUND is not EXCLUDED.
- Set coverage_status = "EXCLUDED" only when the evidence explicitly lists the feature as an exclusion / not covered.
- Set coverage_status = "ADD_ON" when the benefit exists only as an optional / add-on / rider cover at extra premium.
- Set "CONDITIONAL" when coverage depends on conditions (waiting period, minimum hours, network-only, sum-insured band, variant-only).
- Set "PARTIALLY_COVERED" when coverage is capped well below the sum insured or limited to a subset.
- Quote numbers exactly as written in the evidence (e.g. "INR 2,50,000", "36 months", "20%").
- List every condition / footnote that changes the meaning of the benefit.
- evidence_ids must list ONLY the passage ids you actually relied on.
- Do not write explanations or reasoning; fill the fields only."""


class FeatureExtraction(BaseModel):
    coverage_status: Literal["COVERED", "PARTIALLY_COVERED", "CONDITIONAL", "EXCLUDED", "ADD_ON", "NOT_FOUND"]
    value: str | None = Field(description="One-sentence factual summary of what the brochure says about this feature, or null")
    limit: str | None = None
    waiting_period: str | None = None
    deductible: str | None = None
    copay: str | None = None
    exclusions: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    is_add_on: bool = False
    variant_scope: str | None = Field(default=None, description="If the benefit applies to a specific plan variant only, name it; else null")
    evidence_ids: list[str] = Field(default_factory=list, description="Passage ids like E1, E3 that support the record")
    confidence: float = Field(ge=0, le=1, description="Confidence that the record faithfully reflects the evidence")


def _ev_id(i: int) -> str:
    return f"E{i}"


def _format_evidence(items: list[tuple[str, Chunk, float | None]]) -> str:
    lines = []
    for eid, ch, _score in items:
        loc = f"page {ch.page_number}, section: {ch.section or '-'}, clause {ch.clause}, type: {ch.content_type.value}"
        scope = f", variant: {ch.meta.get('variant_scope')}" if ch.meta.get("variant_scope") else ""
        lines.append(f"[{eid}] ({loc}{scope})\n{ch.source_text}")
    return "\n\n".join(lines)


def _collect_evidence(retriever: HybridRetriever, spec: FeatureSpec, policy_id: str, top_k: int = 8) -> tuple[list[tuple[str, Chunk, float | None]], PolicyEvidence]:
    ev = retriever.retrieve_feature(list(spec.queries), policy_id, top_k=top_k)
    seen: dict[str, tuple[Chunk, float | None]] = {}
    for r in ev.results:
        seen[r.chunk.chunk_id] = (r.chunk, r.final_score)
    for c in ev.conditions:
        seen.setdefault(c.chunk_id, (c, None))
    # Deterministically add typed exclusion / waiting / eligibility chunks that mention the feature keywords
    kw = [k.lower() for k in spec.keywords]
    typed = retriever.policy_chunks(policy_id, [ContentType.EXCLUSION.value, ContentType.WAITING_PERIOD.value, ContentType.CONDITION.value, ContentType.ADD_ON.value])
    for c in typed:
        t = c.source_text.lower()
        if c.chunk_id not in seen and any(k in t for k in kw) and not c.meta.get("list") and not c.meta.get("footnote_block"):
            seen[c.chunk_id] = (c, None)
    # Parent context for table rows
    for _cid, (c, _s) in list(seen.items()):
        if c.parent_chunk_id and c.content_type == ContentType.TABLE_ROW and c.parent_chunk_id not in seen:
            parent = retriever.store.get_chunk(c.parent_chunk_id)
            if parent and len(parent.source_text) < 1200:
                seen[parent.chunk_id] = (parent, None)
    items = [(_ev_id(i + 1), ch, sc) for i, (ch, sc) in enumerate(seen.values())][:16]
    return items, ev


def _to_fact(policy_id: str, feature: str, ext: FeatureExtraction, items: list[tuple[str, Chunk, float | None]], ev: PolicyEvidence) -> FeatureFact:
    by_id = {eid: (ch, sc) for eid, ch, sc in items}
    sources: list[SourceRef] = []
    for eid in ext.evidence_ids:
        if eid in by_id:
            ch, sc = by_id[eid]
            sources.append(SourceRef.from_chunk(ch, sc))
    status = CoverageStatus(ext.coverage_status)
    notes = None
    if status != CoverageStatus.NOT_FOUND and not sources:
        notes = "Downgraded to NOT_FOUND: extraction cited no evidence passages."
        status = CoverageStatus.NOT_FOUND
    if status == CoverageStatus.NOT_FOUND:
        ext.value, ext.limit, ext.waiting_period, ext.deductible, ext.copay = None, None, None, None, None
    if ext.is_add_on and status in {CoverageStatus.COVERED, CoverageStatus.CONDITIONAL, CoverageStatus.PARTIALLY_COVERED}:
        status = CoverageStatus.ADD_ON
    return FeatureFact(
        policy_id=policy_id,
        feature=feature,
        coverage_status=status,
        value=ext.value,
        limit=ext.limit,
        waiting_period=ext.waiting_period,
        deductible=ext.deductible,
        copay=ext.copay,
        exclusions=ext.exclusions,
        conditions=ext.conditions,
        is_add_on=ext.is_add_on or status == CoverageStatus.ADD_ON,
        variant_scope=ext.variant_scope,
        sources=sources,
        notes=notes,
    )


class HeuristicExtractor:
    """Offline extractor: reads only typed chunks (table rows, exclusions, waiting periods, add-ons)."""

    def extract(self, spec: FeatureSpec, policy_id: str, items: list[tuple[str, Chunk, float | None]], ev: PolicyEvidence) -> FeatureExtraction:
        kw = [k.lower() for k in spec.keywords]
        primary = kw[0] if kw else ""
        # keywords with digits ("30 days", "36 months") are too generic to establish a feature on their own
        strong_kw = [k for k in kw if not re.search(r"\d", k)]
        best_row = None
        row_text_hit = None
        clause_hit = None
        excl = None
        addon = None
        for eid, ch, _sc in items:
            t = ch.source_text.lower()
            label = (ch.meta.get("row_label") or ch.source_text.split(":")[0]).lower()
            hit_label = any(k in label for k in kw)
            hits = [k for k in strong_kw if k in t]
            # text-only hits must be unambiguous: the primary keyword, or two distinct keywords
            hit_text = (primary and primary in t) or len(hits) >= 2
            if spec.key.startswith("waiting_period") and ch.content_type != ContentType.WAITING_PERIOD and "waiting" not in t:
                hit_text = False
            if ch.content_type == ContentType.EXCLUSION and hit_text and ch.meta.get("list_item") and excl is None:
                excl = (eid, ch)
            elif ch.content_type == ContentType.WAITING_PERIOD and spec.key.startswith("waiting_period") and hit_text and best_row is None:
                best_row = (eid, ch)
            elif ch.content_type == ContentType.TABLE_ROW and hit_label and best_row is None:
                best_row = (eid, ch)
            elif ch.content_type == ContentType.TABLE_ROW and hit_text and len(ch.source_text) < 260 and row_text_hit is None:
                row_text_hit = (eid, ch)
            elif ch.content_type == ContentType.ADD_ON and (hit_label or (hit_text and len(ch.source_text) < 200)) and addon is None:
                addon = (eid, ch)
            elif ch.content_type in {ContentType.CLAUSE, ContentType.LIST_ITEM} and hit_text and 20 < len(ch.source_text) < 320 and clause_hit is None:
                clause_hit = (eid, ch)
        if excl:
            return FeatureExtraction(coverage_status="EXCLUDED", value=f"Listed under exclusions: {excl[1].source_text}", exclusions=[excl[1].source_text], evidence_ids=[excl[0]], confidence=0.6)
        pick = best_row or row_text_hit
        if pick:
            text = pick[1].source_text
            value = text.split(":", 1)[1].strip() if ":" in text else text
            status = "CONDITIONAL" if pick[1].footnote_refs else "COVERED"
            return FeatureExtraction(coverage_status=status, value=text, limit=value if re.search(r"\d", value) else None, evidence_ids=[pick[0]], confidence=0.55 if pick is best_row else 0.45)
        if addon:
            return FeatureExtraction(coverage_status="ADD_ON", value=addon[1].source_text, is_add_on=True, evidence_ids=[addon[0]], confidence=0.5)
        if clause_hit:
            return FeatureExtraction(coverage_status="CONDITIONAL" if clause_hit[1].footnote_refs else "COVERED", value=clause_hit[1].source_text, evidence_ids=[clause_hit[0]], confidence=0.4)
        return FeatureExtraction(coverage_status="NOT_FOUND", value=None, evidence_ids=[], confidence=0.3)


class PolicyFactExtractor:
    def __init__(self, retriever: HybridRetriever | None = None, llm: LLMService | None = None, store: SQLiteMetadataStore | None = None):
        self.retriever = retriever or get_retriever()
        self.llm = llm or get_llm()
        self.store = store or self.retriever.store
        self.heuristic = HeuristicExtractor()

    def _cache_key(self, policy_id: str, feature: str, items: list[tuple[str, Chunk, float | None]]) -> str:
        ids = ",".join(ch.chunk_id for _, ch, _ in items)
        return hashlib.sha1(f"{policy_id}|{feature}|{ids}|{self.llm.model if self.llm.available else 'heuristic'}".encode()).hexdigest()

    def extract_feature(self, policy_id: str, feature: str, use_cache: bool = True) -> FeatureFact:
        spec = FEATURE_BY_KEY[feature]
        items, ev = _collect_evidence(self.retriever, spec, policy_id)
        if not items:
            return FeatureFact(policy_id=policy_id, feature=feature, coverage_status=CoverageStatus.NOT_FOUND, notes="No evidence retrieved for this policy/feature")
        key = self._cache_key(policy_id, feature, items)
        if use_cache:
            cached = self.store.cache_get("extraction", key)
            if cached:
                return FeatureFact.model_validate(cached)
        mode = "llm"
        if self.llm.available:
            user = (
                f"POLICY: {items[0][1].policy_name} (policy_id={policy_id})\n"
                f"FEATURE: {spec.label} (key={spec.key})\n"
                f"FEATURE QUESTIONS: {' / '.join(spec.queries)}\n\n"
                f"EVIDENCE PASSAGES:\n{_format_evidence(items)}"
            )
            try:
                ext = self.llm.structured(EXTRACTION_SYSTEM, user, FeatureExtraction, purpose="policy_extraction")
            except LLMUnavailable:
                ext = self.heuristic.extract(spec, policy_id, items, ev)
                mode = "heuristic"
        else:
            ext = self.heuristic.extract(spec, policy_id, items, ev)
            mode = "heuristic"
        fact = _to_fact(policy_id, feature, ext, items, ev)
        if mode == "heuristic":
            fact.notes = (fact.notes + " " if fact.notes else "") + "Extracted by offline heuristic (no LLM); treat as provisional."
        if use_cache:
            self.store.cache_set("extraction", key, fact.model_dump(mode="json"))
        return fact

    def extract_policy(self, policy_id: str, features: list[str] | None = None, use_cache: bool = True, workers: int = 6) -> PolicyExtractionResult:
        keys = features or [f.key for f in FEATURES]
        facts: dict[str, FeatureFact] = {}
        # Fewer parallel workers keep Gemini free-tier requests-per-minute in check.
        with ThreadPoolExecutor(max_workers=min(workers, 3) if self.llm.available else 1) as pool:
            futs = {pool.submit(self.extract_feature, policy_id, k, use_cache): k for k in keys}
            for fut in as_completed(futs):
                k = futs[fut]
                try:
                    facts[k] = fut.result()
                except LLMQuotaExceeded:
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise  # surface quota clearly instead of writing UNKNOWN facts
                except Exception as exc:
                    log.error("Extraction failed for %s/%s: %s", policy_id, k, exc)
                    facts[k] = FeatureFact(policy_id=policy_id, feature=k, coverage_status=CoverageStatus.UNKNOWN, notes=f"Extraction error: {exc}")
        return PolicyExtractionResult(policy_id=policy_id, facts=facts, generated_at=datetime.now(timezone.utc).isoformat(), model=self.llm.model if self.llm.available else "heuristic")


def facts_file():
    return get_settings().metadata_path / "policy_facts.json"


def load_policy_facts() -> dict[str, PolicyExtractionResult] | None:
    f = facts_file()
    if not f.exists():
        return None
    raw = json.loads(f.read_text(encoding="utf-8"))
    return {pid: PolicyExtractionResult.model_validate(v) for pid, v in raw.items()}


def save_policy_facts(results: dict[str, PolicyExtractionResult]) -> None:
    facts_file().write_text(json.dumps({pid: r.model_dump(mode="json") for pid, r in results.items()}, indent=1, ensure_ascii=False), encoding="utf-8")


def extract_all_policies(force: bool = False, policy_ids: list[str] | None = None) -> dict[str, PolicyExtractionResult]:
    """Extract facts for every policy/feature; persisted to storage/metadata/policy_facts.json."""
    existing = None if force else load_policy_facts()
    retriever = get_retriever()
    ids = policy_ids or [p.policy_id for p in retriever.store.list_policies()]
    if existing and all(pid in existing for pid in ids):
        # Do not reuse heuristic facts when an LLM is now available
        if not (get_llm().available and any(r.model == "heuristic" for r in existing.values())):
            return existing
    extractor = PolicyFactExtractor(retriever=retriever)
    results: dict[str, PolicyExtractionResult] = dict(existing or {})
    for pid in ids:
        log.info("Extracting structured facts for %s", pid)
        results[pid] = extractor.extract_policy(pid, use_cache=not force)
    save_policy_facts(results)
    return results


def get_policy_facts(policy_ids: list[str] | None = None) -> dict[str, PolicyExtractionResult]:
    """Runtime accessor: cached facts, computed on first use if missing."""
    facts = load_policy_facts()
    retriever = get_retriever()
    ids = policy_ids or [p.policy_id for p in retriever.store.list_policies()]
    if facts and all(pid in facts for pid in ids):
        return {pid: facts[pid] for pid in ids}
    return {pid: r for pid, r in extract_all_policies(policy_ids=ids).items() if pid in ids}

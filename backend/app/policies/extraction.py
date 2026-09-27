"""Structured policy-fact extraction (per policy x per feature).

Retrieval is deterministic and policy-scoped; the LLM only turns the retrieved passages into a
validated `FeatureFact`. Evidence ids are echoed back and mapped to SourceRefs deterministically.
A status other than NOT_FOUND without cited evidence is downgraded (never fabricate).
A quoted span must exist inside a retrieved chunk; page numbers come from chunk metadata.

A heuristic extractor exists for offline mode/tests: it only reads table rows / typed chunks and
marks everything else NOT_FOUND.
"""
from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.models.policy import Chunk, ContentType, CoverageStatus, FeatureFact, PolicyDocument, PolicyExtractionResult, SourceRef
from app.policies.evidence_contract import (
    EVIDENCE_SCHEMA_VERSION,
    prompt_hash,
    retrieval_method_for,
    stamp_fact,
    validate_extraction,
)
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
- If the evidence does not establish a field, set it to null. If the evidence does not address the feature at all, set coverage_status = "NOT_FOUND". NOT_FOUND is not EXCLUDED. NOT_FOUND is not COVERED. Never infer "not mentioned, therefore excluded".
- Set coverage_status = "EXCLUDED" only when the evidence explicitly lists the feature as an exclusion / not covered.
- Set coverage_status = "ADD_ON" when the benefit exists only as an optional / add-on / rider cover at extra premium. ADD_ON is not a weak form of COVERED.
- Set "CONDITIONAL" when coverage depends on conditions (waiting period, minimum hours, network-only, sum-insured band, variant-only).
- Set "PARTIALLY_COVERED" when coverage is capped well below the sum insured or limited to a subset.
- Quote numbers exactly as written in the evidence (e.g. "INR 2,50,000", "36 months", "20%").
- "quote" must be a verbatim substring of one cited evidence passage. Never invent a quote or a page number.
- List every condition / footnote that changes the meaning of the benefit.
- evidence_ids must list ONLY the passage ids you actually relied on (E1, E2, ...). Do not invent ids.
- Do not write explanations or reasoning; fill the fields only."""

EXTRACTION_PROMPT_VERSION = prompt_hash(EXTRACTION_SYSTEM)


class FeatureExtraction(BaseModel):
    coverage_status: Literal["COVERED", "PARTIALLY_COVERED", "CONDITIONAL", "EXCLUDED", "ADD_ON", "NOT_FOUND"]
    value: str | None = Field(default=None, description="One-sentence factual summary of what the brochure says about this feature, or null")
    limit: str | None = None
    waiting_period: str | None = None
    deductible: str | None = None
    copay: str | None = None
    exclusions: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    is_add_on: bool = False
    variant_scope: str | None = Field(default=None, description="If the benefit applies to a specific plan variant only, name it; else null")
    quote: str | None = Field(default=None, description="Verbatim substring copied from a cited evidence passage")
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


def _to_fact(
    policy_id: str,
    feature: str,
    ext: FeatureExtraction,
    items: list[tuple[str, Chunk, float | None]],
    ev: PolicyEvidence,
    *,
    spec: FeatureSpec | None = None,
    doc: PolicyDocument | None = None,
    prompt_version: str = EXTRACTION_PROMPT_VERSION,
    model: str | None = None,
) -> FeatureFact:
    status = CoverageStatus(ext.coverage_status)
    status, cited, original_quote, notes = validate_extraction(
        policy_id,
        feature,
        status,
        ext.evidence_ids,
        ext.quote,
        items,
        feature_keywords=spec.keywords if spec else (),
    )
    sources: list[SourceRef] = []
    methods: list[str] = []
    for eid, ch, sc in cited:
        method = retrieval_method_for(None, None, False)
        for r in ev.results:
            if r.chunk.chunk_id == ch.chunk_id:
                method = retrieval_method_for(r.bm25_rank, r.dense_rank, False)
                break
        methods.append(method)
        sources.append(SourceRef.from_chunk(ch, sc, retrieval_method=method))
        if doc:
            sources[-1].insurer_name = sources[-1].insurer_name or doc.insurer
            sources[-1].source_document = sources[-1].source_document or doc.file_name
            sources[-1].product_name = sources[-1].product_name or doc.policy_name
    if status == CoverageStatus.NOT_FOUND:
        ext.value, ext.limit, ext.waiting_period, ext.deductible, ext.copay = None, None, None, None, None
        original_quote = None
        sources = []
    if ext.is_add_on and status in {CoverageStatus.COVERED, CoverageStatus.CONDITIONAL, CoverageStatus.PARTIALLY_COVERED}:
        status = CoverageStatus.ADD_ON
    if status == CoverageStatus.ADD_ON:
        ext.is_add_on = True
    fact = FeatureFact(
        policy_id=policy_id,
        feature=feature,
        coverage_status=status,
        value=ext.value if status != CoverageStatus.NOT_FOUND else None,
        limit=ext.limit if status != CoverageStatus.NOT_FOUND else None,
        waiting_period=ext.waiting_period if status != CoverageStatus.NOT_FOUND else None,
        deductible=ext.deductible if status != CoverageStatus.NOT_FOUND else None,
        copay=ext.copay if status != CoverageStatus.NOT_FOUND else None,
        eligibility=ext.variant_scope,
        sublimit=ext.limit if status == CoverageStatus.PARTIALLY_COVERED else None,
        exclusions=ext.exclusions if status != CoverageStatus.NOT_FOUND else [],
        conditions=ext.conditions if status != CoverageStatus.NOT_FOUND else [],
        is_add_on=ext.is_add_on or status == CoverageStatus.ADD_ON,
        variant_scope=ext.variant_scope,
        original_quote=original_quote,
        sources=sources,
        notes=notes,
    )
    return stamp_fact(
        fact,
        doc=doc,
        prompt_version=prompt_version,
        model=model,
        retrieval_method=methods[0] if methods else None,
        evidence_confidence=ext.confidence,
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
            return FeatureExtraction(coverage_status="EXCLUDED", value=f"Listed under exclusions: {excl[1].source_text}", quote=excl[1].source_text, exclusions=[excl[1].source_text], evidence_ids=[excl[0]], confidence=0.6)
        pick = best_row or row_text_hit
        if pick:
            text = pick[1].source_text
            value = text.split(":", 1)[1].strip() if ":" in text else text
            status = "CONDITIONAL" if pick[1].footnote_refs else "COVERED"
            return FeatureExtraction(coverage_status=status, value=text, limit=value if re.search(r"\d", value) else None, quote=text, evidence_ids=[pick[0]], confidence=0.55 if pick is best_row else 0.45)
        if addon:
            return FeatureExtraction(coverage_status="ADD_ON", value=addon[1].source_text, is_add_on=True, quote=addon[1].source_text, evidence_ids=[addon[0]], confidence=0.5)
        if clause_hit:
            return FeatureExtraction(coverage_status="CONDITIONAL" if clause_hit[1].footnote_refs else "COVERED", value=clause_hit[1].source_text, quote=clause_hit[1].source_text, evidence_ids=[clause_hit[0]], confidence=0.4)
        return FeatureExtraction(coverage_status="NOT_FOUND", value=None, evidence_ids=[], confidence=0.3)


class PolicyFactExtractor:
    def __init__(self, retriever: HybridRetriever | None = None, llm: LLMService | None = None, store: SQLiteMetadataStore | None = None):
        self.retriever = retriever or get_retriever()
        self.llm = llm or get_llm()
        self.store = store or self.retriever.store
        self.heuristic = HeuristicExtractor()
        self._docs: dict[str, PolicyDocument] = {p.policy_id: p for p in self.store.list_policies()}

    def _cache_key(self, policy_id: str, feature: str, items: list[tuple[str, Chunk, float | None]], prompt_version: str | None = None) -> str:
        ids = ",".join(ch.chunk_id for _, ch, _ in items)
        doc = self._docs.get(policy_id)
        doc_id = f"{(doc.document_hash if doc else None) or (doc.file_name if doc else policy_id)}:{(doc.pages if doc else 0)}"
        model = self.llm.model if self.llm.available else "heuristic"
        pv = prompt_version or EXTRACTION_PROMPT_VERSION
        return hashlib.sha1(
            f"{policy_id}|{doc_id}|{feature}|{ids}|{pv}|{EVIDENCE_SCHEMA_VERSION}|{model}".encode()
        ).hexdigest()

    def extract_feature(self, policy_id: str, feature: str, use_cache: bool = True) -> FeatureFact:
        spec = FEATURE_BY_KEY[feature]
        doc = self._docs.get(policy_id)
        items, ev = _collect_evidence(self.retriever, spec, policy_id)
        if not items:
            fact = FeatureFact(
                policy_id=policy_id,
                feature=feature,
                coverage_status=CoverageStatus.NOT_FOUND,
                notes="No evidence retrieved for this policy/feature",
            )
            return stamp_fact(fact, doc=doc, prompt_version=EXTRACTION_PROMPT_VERSION, model=self.llm.model if self.llm.available else "heuristic")
        key = self._cache_key(policy_id, feature, items)
        if use_cache:
            cached = self.store.cache_get("extraction", key)
            if cached:
                return FeatureFact.model_validate(cached)
        mode = "llm"
        ext: FeatureExtraction | None = None
        if self.llm.available:
            user = (
                f"POLICY: {items[0][1].policy_name} (policy_id={policy_id})\n"
                f"FEATURE: {spec.label} (key={spec.key})\n"
                f"FEATURE QUESTIONS: {' / '.join(spec.queries)}\n\n"
                f"EVIDENCE PASSAGES:\n{_format_evidence(items)}"
            )
            try:
                raw = self.llm.structured(EXTRACTION_SYSTEM, user, FeatureExtraction, purpose="policy_extraction")
                ext = raw if isinstance(raw, FeatureExtraction) else FeatureExtraction.model_validate(raw)
            except LLMUnavailable:
                ext = self.heuristic.extract(spec, policy_id, items, ev)
                mode = "heuristic"
            except (ValidationError, TypeError, ValueError, json.JSONDecodeError) as exc:
                fact = FeatureFact(
                    policy_id=policy_id,
                    feature=feature,
                    coverage_status=CoverageStatus.UNKNOWN,
                    notes=f"Malformed extraction JSON: {exc}",
                )
                return stamp_fact(fact, doc=doc, prompt_version=EXTRACTION_PROMPT_VERSION, model=self.llm.model)
        else:
            ext = self.heuristic.extract(spec, policy_id, items, ev)
            mode = "heuristic"
        fact = _to_fact(
            policy_id,
            feature,
            ext,
            items,
            ev,
            spec=spec,
            doc=doc,
            prompt_version=EXTRACTION_PROMPT_VERSION,
            model=self.llm.model if self.llm.available else "heuristic",
        )
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
        selected = {pid: facts[pid] for pid in ids}
    else:
        selected = {pid: result for pid, result in extract_all_policies(policy_ids=ids).items() if pid in ids}
    from app.policies.normalize import apply_normalization

    return apply_normalization(selected, lambda pid: retriever.store.list_chunks(pid))

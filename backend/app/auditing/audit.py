"""Audit orchestration: run auditors per claim, build Evidence Passports, apply the Evidence Gate."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app.auditing.auditors import citation_check, contradiction_check, coverage_check, exclusion_check, numerical_check
from app.auditing.claims import extract_claims
from app.models.pitch import AuditReport, AuditStatus, AuditSummary, CheckResult, Claim, ClaimAudit, ClaimType, EvidencePassport, Pitch
from app.models.policy import ComparisonMatrix, ContentType, FeatureFact, SourceRef
from app.rag.metadata_store import SQLiteMetadataStore
from app.services.llm import LLMService, get_llm
from app.utils.ids import new_id
from app.utils.logging import get_logger

log = get_logger(__name__)

SEVERITY = {AuditStatus.CONTRADICTED: 4, AuditStatus.NOT_FOUND: 3, AuditStatus.UNCERTAIN: 2, AuditStatus.PARTIALLY_SUPPORTED: 1, AuditStatus.SUPPORTED: 0, AuditStatus.NOT_APPLICABLE: -1}


def aggregate(checks: list[CheckResult], claim: Claim) -> tuple[AuditStatus, str]:
    if claim.claim_type != ClaimType.POLICY:
        return AuditStatus.NOT_APPLICABLE, "NONE"
    statuses = [c.status for c in checks if c.status != AuditStatus.NOT_APPLICABLE]
    if not statuses:
        return AuditStatus.UNCERTAIN, "REVIEW"
    worst = max(statuses, key=lambda s: SEVERITY[s])
    if worst == AuditStatus.CONTRADICTED:
        return worst, "CORRECT"
    if worst == AuditStatus.NOT_FOUND:
        return worst, "CORRECT" if claim.material else "REVIEW"
    if worst == AuditStatus.UNCERTAIN:
        return worst, "REVIEW"
    if worst == AuditStatus.PARTIALLY_SUPPORTED:
        return worst, "CORRECT" if claim.material else "REVIEW"
    return AuditStatus.SUPPORTED, "NONE"


def _passport(claim: Claim, status: AuditStatus, action: str, sources: list[SourceRef], cond_texts: list[str]) -> EvidencePassport:
    s = sources[0] if sources else None
    audit = "N/A" if status == AuditStatus.NOT_APPLICABLE else ("PASS" if status == AuditStatus.SUPPORTED else ("FAIL" if status in {AuditStatus.CONTRADICTED, AuditStatus.NOT_FOUND} else "REVIEW"))
    return EvidencePassport(
        claim_id=claim.claim_id, claim_text=claim.claim_text, policy_id=claim.policy_id, policy_name=s.policy_name if s else None,
        page=s.page if s else None, section=s.section if s else None, clause=s.clause if s else None, source_text=s.source_text if s else None,
        retrieval_relevance=s.retrieval_relevance if s else None, verification=status, audit=audit, linked_conditions=cond_texts[:4],
    )


class PitchAuditor:
    def __init__(self, store: SQLiteMetadataStore | None = None, llm: LLMService | None = None, matrix: ComparisonMatrix | None = None):
        self.store = store or SQLiteMetadataStore()
        self.llm = llm or get_llm()
        self.matrix = matrix
        self._excl_cache: dict[str, list[SourceRef]] = {}

    def _fact(self, claim: Claim) -> FeatureFact | None:
        if not self.matrix or not claim.feature_key or not claim.policy_id:
            return None
        cell = self.matrix.cells.get(claim.feature_key, {}).get(claim.policy_id)
        return cell.fact if cell else None

    def _exclusions(self, policy_id: str) -> list[SourceRef]:
        if policy_id not in self._excl_cache:
            chunks = self.store.list_chunks(policy_id=policy_id, content_types=[ContentType.EXCLUSION.value])
            self._excl_cache[policy_id] = [SourceRef.from_chunk(c) for c in chunks]
        return self._excl_cache[policy_id]

    def _sources(self, pitch: Pitch, claim: Claim) -> tuple[list[SourceRef], list[str], list[str]]:
        slide = next((s for s in pitch.slides if s.slide_number == claim.slide), None)
        ids = slide.bullets[claim.bullet_index].source_chunk_ids if slide and claim.bullet_index is not None and claim.bullet_index < len(slide.bullets) else []
        chunks = self.store.get_chunks(ids) if ids else []
        found = {c.chunk_id for c in chunks}
        missing = [i for i in ids if i not in found]
        refs = [SourceRef.from_chunk(c) for c in chunks]
        cond_ids: list[str] = []
        for c in chunks:
            cond_ids.extend(c.footnote_refs)
        conds = self.store.get_chunks(list(dict.fromkeys(cond_ids))) if cond_ids else []
        return refs, [c.source_text for c in conds], missing

    def audit_claim(self, pitch: Pitch, claim: Claim, all_claims: list[Claim]) -> ClaimAudit:
        sources, cond_texts, missing = self._sources(pitch, claim)
        fact = self._fact(claim)
        checks = [
            citation_check(claim, sources, missing),
            numerical_check(claim, sources, cond_texts),
            exclusion_check(claim, fact, self._exclusions(claim.policy_id) if claim.policy_id else []),
            contradiction_check(claim, fact, all_claims),
        ]
        # only spend the audit model on material policy claims that passed citation
        if claim.claim_type == ClaimType.POLICY and sources:
            checks.append(coverage_check(claim, sources, cond_texts, self.llm if claim.material else None))
        else:
            checks.append(coverage_check(claim, sources, cond_texts, None))
        status, action = aggregate(checks, claim)
        hint = None
        if action == "CORRECT":
            failing = [c for c in checks if c.status == status]
            hint = failing[0].detail if failing else None
            if fact is not None and fact.value:
                hint = (hint or "") + f" Brochure states: {fact.value}"
        return ClaimAudit(claim=claim, status=status, checks=checks, evidence=sources, passport=_passport(claim, status, action, sources, cond_texts), action=action, correction_hint=hint)

    def audit(self, pitch: Pitch, allowed_features: list[str] | None = None, major_gaps: list[str] | None = None) -> AuditReport:
        claims = extract_claims(pitch, allowed_features)
        with ThreadPoolExecutor(max_workers=4) as ex:
            audits = list(ex.map(lambda c: self.audit_claim(pitch, c, claims), claims))
        summary = summarise(audits)
        return AuditReport(audit_id=new_id("audit"), pitch_id=pitch.pitch_id, pitch_version=pitch.version, summary=summary, claims=audits, major_policy_gaps=list(major_gaps or []), generated_at=datetime.now(timezone.utc).isoformat())


def summarise(audits: list[ClaimAudit]) -> AuditSummary:
    count = lambda st: sum(1 for a in audits if a.status == st)
    material = [a for a in audits if a.claim.material]
    supported_material = sum(1 for a in material if a.status == AuditStatus.SUPPORTED)
    gate = evidence_gate(audits)
    return AuditSummary(
        total_claims=len(audits), material_claims=len(material), supported=count(AuditStatus.SUPPORTED), partially_supported=count(AuditStatus.PARTIALLY_SUPPORTED),
        contradicted=count(AuditStatus.CONTRADICTED), not_found=count(AuditStatus.NOT_FOUND), uncertain=count(AuditStatus.UNCERTAIN), not_applicable=count(AuditStatus.NOT_APPLICABLE),
        gate=gate, confidence_score=round(supported_material / len(material), 3) if material else 0.0,
    )


def evidence_gate(audits: list[ClaimAudit]) -> str:
    """PASS: no material claim is contradicted/not-found/partially supported. FAIL: any contradicted or material not-found. Else UNCERTAIN."""
    material = [a for a in audits if a.claim.material]
    if any(a.status == AuditStatus.CONTRADICTED for a in audits if a.claim.claim_type == ClaimType.POLICY):
        return "FAIL"
    if any(a.status == AuditStatus.NOT_FOUND for a in material):
        return "FAIL"
    if any(a.status in {AuditStatus.UNCERTAIN, AuditStatus.PARTIALLY_SUPPORTED} for a in material):
        return "UNCERTAIN"
    return "PASS"


def review_feedback(report: AuditReport) -> str:
    """Auditor findings as writer feedback, appended when the advisor asks to regenerate."""
    lines = []
    for ca in report.claims:
        if ca.status in {AuditStatus.CONTRADICTED, AuditStatus.NOT_FOUND, AuditStatus.PARTIALLY_SUPPORTED, AuditStatus.UNCERTAIN} and ca.claim.claim_type == ClaimType.POLICY:
            lines.append(f"- Slide {ca.claim.slide}: '{ca.claim.claim_text[:100]}' -> {ca.status.value}: {ca.correction_hint or ca.checks[0].detail}")
    return "\n".join(lines)

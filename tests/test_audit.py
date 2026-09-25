"""Claim extraction, auditors and the evidence gate (offline)."""
from __future__ import annotations

from app.auditing.audit import aggregate, evidence_gate, summarise
from app.auditing.auditors import citation_check, contradiction_check, coverage_check, exclusion_check, numerical_check
from app.auditing.claims import extract_claims
from app.models.pitch import AuditStatus, Claim, ClaimAudit, ClaimType, EvidencePassport, Pitch, Slide, SlideBullet
from app.models.policy import CoverageStatus, FeatureFact, SourceRef

POL = "hdfc_optima_secure_plus"


def _ref(text: str, chunk_id: str = "c1", policy: str = POL, page: int = 3, ctype: str = "table_row") -> SourceRef:
    return SourceRef(policy_id=policy, policy_name="HDFC ERGO Optima Secure Plus", chunk_id=chunk_id, page=page, section="Benefits", content_type=ctype, source_text=text)


def _claim(text: str, feature: str | None = None, ctype: ClaimType = ClaimType.POLICY, material: bool = True) -> Claim:
    return Claim(claim_id="k1", claim_text=text, claim_type=ctype, slide=2, bullet_index=0, policy_id=POL, feature_key=feature, numbers=[], material=material)


def _pitch() -> Pitch:
    return Pitch(pitch_id="p1", company_name="Acme", recommended_policy_id=POL, slides=[
        Slide(slide_number=1, title="Context", bullets=[SlideBullet(text="Acme employs 12,000 people.", kind="company")]),
        Slide(slide_number=2, title="Benefits", bullets=[
            SlideBullet(text="Air ambulance covered up to INR 2,50,000 per policy year.", source_chunk_ids=["c1"], policy_id=POL, kind="policy"),
            SlideBullet(text="Recommended: this plan (decision-support score 70/100).", kind="recommendation"),
        ]),
    ])


def test_claim_extraction_types_and_materiality():
    claims = extract_claims(_pitch())
    assert len(claims) == 3
    assert claims[0].claim_type == ClaimType.COMPANY and not claims[0].material
    assert claims[1].claim_type == ClaimType.POLICY and claims[1].material and claims[1].numbers
    assert claims[1].feature_key == "air_ambulance"
    assert claims[2].claim_type == ClaimType.RECOMMENDATION


def test_citation_check_requires_matching_policy_evidence():
    c = _claim("Air ambulance covered up to INR 2,50,000 per year.")
    assert citation_check(c, [], []).status == AuditStatus.NOT_FOUND
    assert citation_check(c, [_ref("Air ambulance: up to INR 2,50,000", policy="care_supreme")], []).status == AuditStatus.CONTRADICTED
    assert citation_check(c, [_ref("Air Ambulance: up to INR 2,50,000")], []).passed is True


def test_numerical_check_detects_wrong_and_missing_figures():
    ok = numerical_check(_claim("Air ambulance up to INR 2.5 lakh."), [_ref("Air Ambulance: up to INR 2,50,000")], [])
    assert ok.status == AuditStatus.SUPPORTED
    bad = numerical_check(_claim("Air ambulance up to INR 5 lakh."), [_ref("Air Ambulance: up to INR 2,50,000")], [])
    assert bad.status == AuditStatus.CONTRADICTED
    missing = numerical_check(_claim("Waiting period of 36 months applies."), [_ref("Pre-existing diseases are subject to a waiting period.")], [])
    assert missing.status == AuditStatus.NOT_FOUND
    dur = numerical_check(_claim("Pre-existing diseases covered after 3 years."), [_ref("PED waiting period: 36 months")], [])
    assert dur.status == AuditStatus.SUPPORTED


def test_exclusion_check_flags_excluded_feature_and_unstated_conditions():
    fact_excl = FeatureFact(policy_id=POL, feature="maternity", coverage_status=CoverageStatus.EXCLUDED, value="Listed under exclusions: maternity")
    r = exclusion_check(_claim("Maternity expenses are covered.", "maternity"), fact_excl, [])
    assert r.status == AuditStatus.CONTRADICTED
    fact_cond = FeatureFact(policy_id=POL, feature="maternity", coverage_status=CoverageStatus.CONDITIONAL, value="Maternity cover", conditions=["24-month waiting period"])
    r2 = exclusion_check(_claim("Maternity expenses are covered.", "maternity"), fact_cond, [])
    assert r2.status == AuditStatus.PARTIALLY_SUPPORTED
    r3 = exclusion_check(_claim("Maternity expenses are covered subject to a 24-month waiting period.", "maternity"), fact_cond, [])
    assert r3.passed is True
    r4 = exclusion_check(_claim("Cosmetic surgery is covered.", None), None, [_ref("Cosmetic or plastic surgery is not covered", ctype="exclusion")])
    assert r4.status == AuditStatus.UNCERTAIN


def test_contradiction_check_between_claims_and_against_matrix():
    a = _claim("Air ambulance up to INR 2,50,000.", "air_ambulance")
    b = Claim(claim_id="k2", claim_text="Air ambulance up to INR 5,00,000.", claim_type=ClaimType.POLICY, slide=3, bullet_index=0, policy_id=POL, feature_key="air_ambulance")
    assert contradiction_check(a, None, [a, b]).status == AuditStatus.CONTRADICTED
    nf = FeatureFact(policy_id=POL, feature="air_ambulance", coverage_status=CoverageStatus.NOT_FOUND)
    assert contradiction_check(a, nf, [a]).status == AuditStatus.UNCERTAIN


def test_not_found_is_not_excluded_in_aggregation():
    c = _claim("Dental treatment is covered.", "dental")
    checks = [citation_check(c, [], []), numerical_check(c, [], []), exclusion_check(c, None, []), contradiction_check(c, None, [c]), coverage_check(c, [], [], None)]
    status, action = aggregate(checks, c)
    assert status == AuditStatus.NOT_FOUND and action == "CORRECT"
    assert all(ch.status != AuditStatus.CONTRADICTED for ch in checks)


def test_evidence_gate_rules():
    def ca(status, material=True, ctype=ClaimType.POLICY):
        c = _claim("x", material=material, ctype=ctype)
        return ClaimAudit(claim=c, status=status, checks=[], passport=EvidencePassport(claim_id="k", claim_text="x", policy_id=POL, policy_name=None, page=None, section=None, clause=None, source_text=None, retrieval_relevance=None, verification=status, audit="PASS"), action="NONE")
    assert evidence_gate([ca(AuditStatus.SUPPORTED), ca(AuditStatus.NOT_APPLICABLE, False, ClaimType.COMPANY)]) == "PASS"
    assert evidence_gate([ca(AuditStatus.SUPPORTED), ca(AuditStatus.PARTIALLY_SUPPORTED)]) == "UNCERTAIN"
    assert evidence_gate([ca(AuditStatus.SUPPORTED), ca(AuditStatus.CONTRADICTED)]) == "FAIL"
    assert evidence_gate([ca(AuditStatus.NOT_FOUND)]) == "FAIL"
    s = summarise([ca(AuditStatus.SUPPORTED), ca(AuditStatus.SUPPORTED), ca(AuditStatus.PARTIALLY_SUPPORTED)])
    assert s.confidence_score == 0.667 and s.gate == "UNCERTAIN"

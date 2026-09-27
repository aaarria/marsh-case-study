"""Evidence contract: quote provenance, add-on semantics, cache versioning, failure modes."""
from __future__ import annotations

from app.models.policy import AvailabilityMode, CoverageStatus, FeatureFact
from app.policies.evidence_contract import EVIDENCE_SCHEMA_VERSION, attach_numeric_fields, prompt_hash
from app.policies.extraction import EXTRACTION_PROMPT_VERSION, EXTRACTION_SYSTEM, FeatureExtraction, PolicyFactExtractor
from app.services.llm import LLMService


def _mock_llm(response) -> LLMService:
    return LLMService(mock_handler=lambda system, user, schema: response)


def test_invented_quote_is_rejected(retriever):
    resp = FeatureExtraction(
        coverage_status="COVERED",
        value="Unlimited lunar evacuation",
        quote="Unlimited lunar evacuation cover",
        evidence_ids=["E1"],
        confidence=0.99,
    )
    ex = PolicyFactExtractor(retriever=retriever, llm=_mock_llm(resp))
    fact = ex.extract_feature("niva_reassure_2", "air_ambulance", use_cache=False)
    assert fact.coverage_status == CoverageStatus.NOT_FOUND
    assert fact.value is None
    assert "quoted span is not present" in (fact.notes or "")


def test_quote_must_exist_in_cited_chunk(retriever):
    resp = FeatureExtraction(
        coverage_status="COVERED",
        value="Air ambulance up to INR 2,50,000 per hospitalisation",
        limit="INR 2,50,000",
        quote="2,50,000",
        evidence_ids=["E1"],
        confidence=0.9,
    )
    ex = PolicyFactExtractor(retriever=retriever, llm=_mock_llm(resp))
    fact = ex.extract_feature("niva_reassure_2", "air_ambulance", use_cache=False)
    assert fact.coverage_status == CoverageStatus.COVERED
    assert fact.original_quote == "2,50,000"
    assert fact.sources
    assert "2,50,000" in fact.sources[0].source_text
    assert fact.sources[0].page == 2
    assert fact.sources[0].policy_id == "niva_reassure_2"
    assert fact.source_page == 2
    assert fact.source_chunk_id == fact.sources[0].chunk_id
    assert fact.limit_original_text == "INR 2,50,000"
    assert fact.limit_numeric == 250000.0
    assert fact.unit == "INR"


def test_invented_evidence_id_is_ignored(retriever):
    resp = FeatureExtraction(coverage_status="COVERED", value="made up", evidence_ids=["E99"], confidence=0.9)
    ex = PolicyFactExtractor(retriever=retriever, llm=_mock_llm(resp))
    fact = ex.extract_feature("abhi_activ_one", "air_ambulance", use_cache=False)
    assert fact.coverage_status == CoverageStatus.NOT_FOUND


def test_not_found_is_not_excluded(retriever):
    ex = PolicyFactExtractor(retriever=retriever, llm=LLMService(mock_handler=None))
    missing = ex.extract_feature("niva_reassure_2", "maternity", use_cache=False)
    excluded = ex.extract_feature("hdfc_optima_secure_plus", "maternity", use_cache=False)
    assert missing.coverage_status == CoverageStatus.NOT_FOUND
    assert missing.availability_mode == AvailabilityMode.NOT_FOUND
    assert excluded.coverage_status == CoverageStatus.EXCLUDED
    assert excluded.availability_mode == AvailabilityMode.EXCLUDED
    assert missing.coverage_status != CoverageStatus.EXCLUDED
    assert excluded.coverage_status != CoverageStatus.NOT_FOUND


def test_add_on_is_not_base_covered(retriever):
    resp = FeatureExtraction(
        coverage_status="COVERED",
        value="Parenthood covers maternity",
        is_add_on=True,
        evidence_ids=["E1"],
        confidence=0.8,
    )
    ex = PolicyFactExtractor(retriever=retriever, llm=_mock_llm(resp))
    fact = ex.extract_feature("hdfc_optima_secure_plus", "maternity", use_cache=False)
    assert fact.coverage_status == CoverageStatus.ADD_ON
    assert fact.availability_mode == AvailabilityMode.OPTIONAL_ADD_ON
    assert fact.add_on_required is True
    assert fact.coverage_status != CoverageStatus.COVERED


def test_numeric_original_text_preserved():
    fact = FeatureFact(policy_id="niva_reassure_2", feature="pre_post_hospitalisation", waiting_period="180 days")
    attach_numeric_fields(fact)
    assert fact.waiting_period == "180 days"
    assert fact.waiting_period_days == 180
    assert fact.unit == "days"


def test_copay_percent_normalization():
    fact = FeatureFact(policy_id="niva_reassure_2", feature="copay", copay="co-payment of 20%")
    attach_numeric_fields(fact)
    assert fact.copay == "co-payment of 20%"
    assert fact.copay_percent == 20.0


def test_footnote_conditions_attached(retriever):
    ev = retriever.retrieve_for_policy("hospital cash per day", "niva_reassure_2", top_k=3, use_cache=False, rerank=False)
    assert any("48 hrs" in c.source_text for c in ev.conditions)
    assert any(r.chunk.footnote_refs for r in ev.results)


def test_all_four_policies_independently_retrieved(retriever):
    ids = ["abhi_activ_one", "care_supreme", "hdfc_optima_secure_plus", "niva_reassure_2"]
    for pid in ids:
        ev = retriever.retrieve_for_policy("room rent ICU hospitalisation", pid, top_k=4, use_cache=False, rerank=False)
        assert ev.results
        assert all(r.chunk.policy_id == pid for r in ev.results)


def test_policy_order_does_not_change_evidence(retriever):
    q = "air ambulance limit per hospitalisation"
    pid = "niva_reassure_2"
    first = retriever.retrieve_for_policy(q, pid, top_k=5, use_cache=False, rerank=False)
    for other in ("hdfc_optima_secure_plus", "care_supreme", "abhi_activ_one"):
        retriever.retrieve_for_policy(q, other, top_k=5, use_cache=False, rerank=False)
    second = retriever.retrieve_for_policy(q, pid, top_k=5, use_cache=False, rerank=False)
    assert [r.chunk.chunk_id for r in first.results] == [r.chunk.chunk_id for r in second.results]
    assert [r.chunk.page_number for r in first.results] == [r.chunk.page_number for r in second.results]


def test_extraction_cache_key_includes_prompt_and_schema(retriever):
    ex = PolicyFactExtractor(retriever=retriever, llm=LLMService(mock_handler=None))
    items, _ev = __import__("app.policies.extraction", fromlist=["_collect_evidence"])._collect_evidence(
        retriever, __import__("app.policies.features", fromlist=["FEATURE_BY_KEY"]).FEATURE_BY_KEY["room_rent"], "care_supreme"
    )
    a = ex._cache_key("care_supreme", "room_rent", items)
    b = ex._cache_key("care_supreme", "room_rent", items, prompt_version="changed-prompt")
    assert a != b
    assert EVIDENCE_SCHEMA_VERSION in "evidence-v1"
    assert EXTRACTION_PROMPT_VERSION == prompt_hash(EXTRACTION_SYSTEM)


def test_extraction_cache_invalidates_on_prompt_hash_change(retriever):
    resp = FeatureExtraction(coverage_status="COVERED", value="Room Rent", quote="Room Rent", evidence_ids=["E1"], confidence=0.7)
    ex = PolicyFactExtractor(retriever=retriever, llm=_mock_llm(resp))
    spec_items, _ = __import__("app.policies.extraction", fromlist=["_collect_evidence"])._collect_evidence(
        retriever, __import__("app.policies.features", fromlist=["FEATURE_BY_KEY"]).FEATURE_BY_KEY["room_rent"], "care_supreme"
    )
    key_old = ex._cache_key("care_supreme", "room_rent", spec_items)
    fact = ex.extract_feature("care_supreme", "room_rent", use_cache=True)
    cached = ex.store.cache_get("extraction", key_old)
    assert cached is not None
    key_new = ex._cache_key("care_supreme", "room_rent", spec_items, prompt_version="deadbeef")
    assert ex.store.cache_get("extraction", key_new) is None
    assert fact.schema_version == EVIDENCE_SCHEMA_VERSION
    assert fact.prompt_version == EXTRACTION_PROMPT_VERSION


def test_missing_policy_is_not_found(retriever):
    ex = PolicyFactExtractor(retriever=retriever, llm=LLMService(mock_handler=None))
    fact = ex.extract_feature("missing_brochure_id", "room_rent", use_cache=False)
    assert fact.coverage_status == CoverageStatus.NOT_FOUND
    assert "No evidence retrieved" in (fact.notes or "")


def test_empty_retrieval_results(retriever):
    ev = retriever.retrieve_for_policy("room rent", "does_not_exist", top_k=8, use_cache=False, rerank=False)
    assert ev.results == []
    assert ev.conditions == []


def test_malformed_extraction_json(retriever):
    ex = PolicyFactExtractor(retriever=retriever, llm=_mock_llm({"not": "a fact"}))
    fact = ex.extract_feature("niva_reassure_2", "air_ambulance", use_cache=False)
    assert fact.coverage_status == CoverageStatus.UNKNOWN
    assert "Malformed extraction" in (fact.notes or "")

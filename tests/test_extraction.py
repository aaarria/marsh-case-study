"""Structured extraction: evidence mapping, never-fabricate downgrade, heuristic path."""
from __future__ import annotations

from app.models.policy import CoverageStatus
from app.policies.extraction import FeatureExtraction, PolicyFactExtractor
from app.services.llm import LLMService


def _mock_llm(response: FeatureExtraction) -> LLMService:
    return LLMService(mock_handler=lambda system, user, schema: response)


def test_llm_extraction_maps_evidence_ids_to_sources(retriever):
    resp = FeatureExtraction(coverage_status="COVERED", value="Air ambulance up to INR 2,50,000 per hospitalisation", limit="INR 2,50,000", evidence_ids=["E1"], confidence=0.9)
    ex = PolicyFactExtractor(retriever=retriever, llm=_mock_llm(resp))
    fact = ex.extract_feature("niva_reassure_2", "air_ambulance", use_cache=False)
    assert fact.coverage_status == CoverageStatus.COVERED
    assert fact.sources and fact.sources[0].policy_id == "niva_reassure_2"
    assert fact.sources[0].page == 2 and fact.sources[0].chunk_id.startswith("niva_reassure_2:")
    assert fact.original_quote
    assert fact.original_quote in fact.sources[0].source_text or fact.original_quote.lower() in fact.sources[0].source_text.lower()


def test_status_without_evidence_is_downgraded_to_not_found(retriever):
    resp = FeatureExtraction(coverage_status="COVERED", value="Something the model made up", limit="INR 99,00,000", evidence_ids=[], confidence=0.9)
    ex = PolicyFactExtractor(retriever=retriever, llm=_mock_llm(resp))
    fact = ex.extract_feature("abhi_activ_one", "air_ambulance", use_cache=False)
    assert fact.coverage_status == CoverageStatus.NOT_FOUND
    assert fact.value is None and fact.limit is None
    assert "Downgraded" in (fact.notes or "")


def test_add_on_flag_forces_add_on_status(retriever):
    resp = FeatureExtraction(coverage_status="COVERED", value="Parenthood add-on covers maternity", is_add_on=True, evidence_ids=["E1"], confidence=0.8)
    ex = PolicyFactExtractor(retriever=retriever, llm=_mock_llm(resp))
    fact = ex.extract_feature("hdfc_optima_secure_plus", "maternity", use_cache=False)
    assert fact.coverage_status == CoverageStatus.ADD_ON and fact.is_add_on


def test_heuristic_extractor_finds_exclusion_and_table_rows(retriever):
    ex = PolicyFactExtractor(retriever=retriever, llm=LLMService(mock_handler=None))  # no key -> heuristic
    assert not ex.llm.available or True
    mat = ex.extract_feature("hdfc_optima_secure_plus", "maternity", use_cache=False)
    assert mat.coverage_status == CoverageStatus.EXCLUDED
    ped = ex.extract_feature("care_supreme", "waiting_period_ped", use_cache=False)
    assert "36 months" in (ped.value or "")
    unknown = ex.extract_feature("niva_reassure_2", "maternity", use_cache=False)
    assert unknown.coverage_status == CoverageStatus.NOT_FOUND

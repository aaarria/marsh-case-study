"""Hybrid retrieval: strict per-policy filtering, footnote linking, known-value hits."""
from __future__ import annotations

from app.models.policy import SourceRef
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.lexical import tokenize


def test_rrf_prefers_items_in_both_lists():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["c", "d", "a"]])
    assert fused["a"] > fused["b"] and fused["c"] > fused["d"]


def test_tokenizer_normalises_indian_units():
    toks = tokenize("Air Ambulance up to ₹2,50,000 or 5 lacs; co-pay 20%")
    assert "lakh" in toks and "2,50,000" in toks and "%" in toks


def test_policy_filter_is_strict(retriever):
    ev = retriever.retrieve_for_policy("maternity", "care_supreme", top_k=8, use_cache=False, rerank=False)
    assert all(r.chunk.policy_id == "care_supreme" for r in ev.results)


def test_known_values_found(retriever):
    ev = retriever.retrieve_for_policy("air ambulance limit per hospitalisation", "niva_reassure_2", top_k=3, use_cache=False, rerank=False)
    assert any("2,50,000" in r.chunk.source_text for r in ev.results)

    ev = retriever.retrieve_for_policy("pre-existing disease waiting period", "care_supreme", top_k=3, use_cache=False, rerank=False)
    assert any("36 months" in r.chunk.source_text for r in ev.results)

    ev = retriever.retrieve_for_policy("maternity exclusion", "hdfc_optima_secure_plus", top_k=5, use_cache=False, rerank=False)
    assert any(r.chunk.content_type.value == "exclusion" and "maternity" in r.chunk.source_text.lower() for r in ev.results)

    ev = retriever.retrieve_for_policy("co-payment percentage", "niva_reassure_2", top_k=3, use_cache=False, rerank=False)
    assert any("20%" in r.chunk.source_text for r in ev.results)


def test_conditions_attached_via_footnotes(retriever):
    ev = retriever.retrieve_for_policy("hospital cash per day", "niva_reassure_2", top_k=3, use_cache=False, rerank=False)
    assert any("48 hrs" in c.source_text for c in ev.conditions)


def test_scores_are_relevance_not_accuracy(retriever):
    ev = retriever.retrieve_for_policy("room rent", "care_supreme", top_k=2, use_cache=False, rerank=False)
    ref = SourceRef.from_chunk(ev.results[0].chunk, ev.results[0].final_score)
    assert ref.retrieval_relevance is not None
    assert "retrieval_relevance" in type(ref).model_fields
    assert "accuracy" not in type(ref).model_fields

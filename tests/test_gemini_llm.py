"""Gemini LLM service: quota classification, no-model-substitution guarantee, offline behaviour (no network)."""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from app.config import FREE_TIER_TEXT_MODELS, get_settings
from app.services.llm import LLMQuotaExceeded, LLMService, LLMUnavailable, classify_quota_error, quota_error_from


class _Out(BaseModel):
    text: str


def _429(quota_id: str, retry: str | None = "23s") -> dict:
    details = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests", "quotaId": quota_id}]}]
    if retry:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    return {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota.", "details": details}}


def test_daily_quota_is_not_retried_and_points_at_pacific_midnight():
    q = classify_quota_error(_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier", retry=None), "gemini-3.5-flash-lite")
    assert isinstance(q, LLMQuotaExceeded)
    assert q.scope == "day"
    assert q.retry_after is not None and 60 <= q.retry_after <= 24 * 3600
    assert "does not switch to a paid model" in str(q)


def test_minute_quota_uses_retry_delay():
    q = classify_quota_error(_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", retry="17s"), "gemini-3.5-flash-lite")
    assert q.scope == "minute"
    assert q.retry_after == 17.0
    assert "17s" in str(q)


def test_unknown_payload_defaults_to_minute_scope():
    q = classify_quota_error(None, "m")
    assert q.scope == "minute" and q.retry_after == 60.0


def test_groq_schema_drops_null_unions_and_still_parses_a_profile():
    from app.research.company_research import ProfileOut
    from app.services.llm import _groq_schema, _parse_structured

    schema = _groq_schema(ProfileOut)
    assert "anyOf" not in json.dumps(schema)
    assert schema["properties"]["overview"]["type"] == "string"
    parsed = _parse_structured(ProfileOut, """```json
    {"overview":"Car maker","industry":null,"facts":[{"field":"industry","text":"Automotive","kind":"fact","source_ids":"S1","confidence":2}]}
    ```""")
    assert parsed.overview == "Car maker"
    assert parsed.facts[0].kind == "FACT"
    assert parsed.facts[0].source_ids == ["S1"]
    assert parsed.facts[0].confidence == 1.0
    assert parsed.size is None and parsed.geography is None and parsed.workforce is None
    from app.services.llm import _relax_payload
    assert _relax_payload({"kind": "COMPANY", "text": "BMW"})["kind"] == "company"


def test_quota_error_found_through_exception_chain():
    inner = LLMQuotaExceeded("boom", retry_after=5, scope="minute", model="m")
    try:
        try:
            raise inner
        except LLMQuotaExceeded as e:
            raise RuntimeError("node wrapper") from e
    except RuntimeError as outer:
        assert quota_error_from(outer) is inner
    assert quota_error_from(RuntimeError("plain")) is None


def test_default_model_is_free_tier_and_never_substituted():
    s = get_settings()
    assert s.gemini_model in FREE_TIER_TEXT_MODELS
    assert s.model_free_tier_known
    svc = LLMService(mock_handler=None)
    assert svc.model == s.gemini_model
    assert svc.audit_model == svc.model  # one configurable model for every purpose


def test_without_key_llm_is_unavailable_and_raises_clearly():
    svc = LLMService(mock_handler=None)
    assert not svc.available
    with pytest.raises(LLMUnavailable):
        svc.structured("s", "u", _Out)
    with pytest.raises(LLMUnavailable):
        svc.text("s", "u")


def test_mock_handler_bypasses_network():
    svc = LLMService(mock_handler=lambda system, user, schema: schema(text="ok"))
    assert svc.available
    assert svc.structured("s", "u", _Out).text == "ok"
    assert svc.text("s", "u") == "ok"

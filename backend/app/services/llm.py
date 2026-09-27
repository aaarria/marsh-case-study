"""LLM service: Google Gemini structured outputs with quota-aware retries, usage accounting and a
mock mode for tests.

Gemini is the only external AI provider. The model is `GEMINI_MODEL` (one configurable string,
never hard-coded at call sites) and the service never substitutes another model: if the free-tier
quota or rate limit is exhausted it raises `LLMQuotaExceeded` with a clear message and a
retry-after hint so the UI can offer "retry later".

Only semantic reasoning goes through here. Never log prompts containing secrets; never return
chain-of-thought to callers (schemas only include final fields; thinking is not requested back).
"""
from __future__ import annotations

import json
import re
import time
import types
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar, get_args, get_origin

from pydantic import BaseModel

from app.config import get_settings
from app.utils.logging import get_logger

log = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

THINKING_LEVELS = {"minimal", "low", "medium", "high"}
_QUOTA_RETRIES = 3  # bounded waits for per-minute limits before giving up


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    """No API key configured. Callers fall back to deterministic heuristics (never fabricate)."""


class LLMQuotaExceeded(LLMError):
    """Gemini returned 429 RESOURCE_EXHAUSTED and bounded retries did not help.

    `retry_after` is seconds until a retry is worth attempting (None when unknown). `scope` is
    "day" for requests-per-day quotas (reset at midnight Pacific) or "minute" for RPM/TPM limits.
    """

    def __init__(self, message: str, *, retry_after: float | None = None, scope: str = "minute", model: str = ""):
        super().__init__(message)
        self.retry_after = retry_after
        self.scope = scope
        self.model = model



# ---------------------------------------------------------------------------
# Error classification (pure functions; unit-tested without network)
# ---------------------------------------------------------------------------

_DURATION = re.compile(r"^(\d+(?:\.\d+)?)s$")


def _parse_duration(s: Any) -> float | None:
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, str):
        m = _DURATION.match(s.strip())
        if m:
            return float(m.group(1))
    return None


def seconds_until_pacific_midnight(now: datetime | None = None) -> float:
    """Free-tier requests-per-day quotas reset at midnight Pacific time (per Google's rate-limit docs)."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Los_Angeles")
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60.0, (nxt - now).total_seconds())


def classify_quota_error(details: Any, model: str) -> LLMQuotaExceeded:
    """Turn a Gemini 429 payload into an LLMQuotaExceeded with scope and retry hint.

    Gemini error bodies look like {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
    "message": "...", "details": [{"@type": ".../RetryInfo", "retryDelay": "23s"},
    {"@type": ".../QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", ...}]}]}}
    """
    err = details.get("error", details) if isinstance(details, dict) else {}
    parts = err.get("details", []) if isinstance(err, dict) else []
    retry_after: float | None = None
    quota_ids: list[str] = []
    for d in parts or []:
        if not isinstance(d, dict):
            continue
        if "retryDelay" in d:
            retry_after = _parse_duration(d["retryDelay"])
        for v in d.get("violations", []) or []:
            if isinstance(v, dict):
                quota_ids.append(str(v.get("quotaId") or v.get("quotaMetric") or ""))
    text = " ".join(quota_ids) + " " + str(err.get("message", "") if isinstance(err, dict) else "")
    per_day = "PerDay" in text or "per day" in text.lower()
    if per_day:
        return LLMQuotaExceeded(
            f"Gemini free-tier daily quota reached for {model}. The limit resets at midnight Pacific time; "
            "try again then. The application does not switch to a paid model.",
            retry_after=retry_after or seconds_until_pacific_midnight(),
            scope="day",
            model=model,
        )
    wait = retry_after or 60.0
    return LLMQuotaExceeded(
        f"Gemini free-tier rate limit reached for {model} (requests or tokens per minute). "
        f"Wait about {int(round(wait))}s and retry. The application does not switch to a paid model.",
        retry_after=wait,
        scope="minute",
        model=model,
    )


def _is_quota(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    status = str(getattr(exc, "status", "") or "")
    return code == 429 or status == "RESOURCE_EXHAUSTED"


def _is_transient(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    if isinstance(code, int) and (code >= 500 or code == 408):
        return True
    return type(exc).__name__ in {"ServerError", "ConnectError", "ReadTimeout", "ConnectTimeout", "RemoteProtocolError", "TimeoutException"}


def _json_text(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _groq_schema(schema: type[BaseModel]) -> dict:
    """Pydantic JSON schema Groq will accept.

    Groq rejects ``anyOf`` null unions and ``$defs``. Optional strings become plain strings,
    and every object lists all of its properties as required.
    """
    raw = schema.model_json_schema()
    defs = raw.get("$defs") or {}

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            return resolve(defs.get(name, {}))
        out = {key: resolve(value) for key, value in node.items() if key not in {"title", "$defs", "minimum", "maximum"}}
        options = out.get("anyOf") or out.get("oneOf")
        if isinstance(options, list):
            kept = [item for item in options if isinstance(item, dict) and item.get("type") != "null"]
            if len(kept) == 1:
                merged = dict(kept[0])
                for key, value in out.items():
                    if key not in {"anyOf", "oneOf"}:
                        merged.setdefault(key, value)
                out = merged
        if out.get("type") == "object":
            props = out.get("properties") or {}
            out["additionalProperties"] = False
            if props:
                out["required"] = list(props)
        return out

    resolved = resolve(raw)
    return resolved if isinstance(resolved, dict) else {"type": "object", "additionalProperties": False}


def _relax_payload(data: Any) -> Any:
    """Make a model JSON object validate when a field is missing, null, or cased differently."""
    if isinstance(data, list):
        return [_relax_payload(item) for item in data]
    if not isinstance(data, dict):
        return data
    out = {key: _relax_payload(value) for key, value in data.items()}
    if isinstance(out.get("kind"), str):
        out["kind"] = out["kind"].strip().upper()
    if "confidence" in out:
        try:
            out["confidence"] = max(0.0, min(1.0, float(out["confidence"])))
        except (TypeError, ValueError):
            out["confidence"] = 0.5
    if isinstance(out.get("source_ids"), str):
        out["source_ids"] = [out["source_ids"]]
    for key in ("business_characteristics", "key_risks", "facts", "source_ids"):
        if key in out and out[key] is None:
            out[key] = []
    if isinstance(out.get("facts"), list):
        kept = []
        for item in out["facts"]:
            if not isinstance(item, dict) or not item.get("field") or not str(item.get("text") or "").strip() or not item.get("kind"):
                continue
            item.setdefault("confidence", 0.5)
            item.setdefault("source_ids", [])
            kept.append(item)
        out["facts"] = kept
    return out


def _allows_none(annotation: Any) -> bool:
    if annotation is type(None):
        return True
    origin = get_origin(annotation)
    if origin in {types.UnionType}:
        return any(_allows_none(arg) for arg in get_args(annotation))
    args = get_args(annotation)
    return any(arg is type(None) for arg in args)


def _parse_structured(schema: type[BaseModel], raw: str) -> BaseModel:
    text = _json_text(raw)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    data = _relax_payload(json.loads(text))
    if isinstance(data, dict):
        for name, info in schema.model_fields.items():
            if name not in data and _allows_none(info.annotation):
                data[name] = None
    return schema.model_validate(data)


def _thinking_config(model: str, level: str):
    """Thinking level applies to Gemini 3.x models only; other families use their defaults."""
    lvl = (level or "").strip().lower()
    if lvl not in THINKING_LEVELS or not model.startswith("gemini-3"):
        return None
    from google.genai import types

    return types.ThinkingConfig(thinking_level=lvl.upper())


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class LLMService:
    """Thin wrapper over google-genai. `mock_handler` lets tests supply deterministic outputs without network."""

    def __init__(self, model: str | None = None, mock_handler: Callable[[str, str, type[BaseModel]], BaseModel] | None = None):
        settings = get_settings()
        self.settings = settings
        self._mock = mock_handler
        self._client = None
        self._groq_key = None
        if mock_handler:
            self.model = (model or settings.gemini_model).strip()
        elif settings.uses_groq:
            self._groq_key = (settings.groq_api_key or "").strip()
            self.model = (model or settings.groq_model).strip()
        else:
            self.model = (model or settings.gemini_model).strip()
        if not mock_handler and not self._groq_key and settings.gemini_api_key:
            from google import genai
            from google.genai import types

            # SDK auto-retry is disabled: quota handling below is explicit and bounded.
            self._client = genai.Client(
                api_key=settings.gemini_api_key,
                http_options=types.HttpOptions(timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)),
            )
            if not settings.model_free_tier_known:
                log.warning("GEMINI_MODEL=%s is not on the known free-tier list; it will be used as configured (never substituted)", self.model)

    @property
    def available(self) -> bool:
        return self._mock is not None or self._groq_key is not None or self._client is not None

    @property
    def audit_model(self) -> str:
        """Single configurable model for every purpose (no separate, possibly paid, audit model)."""
        return self.model

    # ---- structured ----
    def structured(self, system: str, user: str, schema: type[T], *, purpose: str = "general", model: str | None = None, temperature: float = 0.0) -> T:
        if self._mock is not None:
            return self._mock(system, user, schema)  # type: ignore[return-value]
        if self._groq_key:
            return self._call_groq(system, user, schema, purpose, (model or self.model).strip(), temperature)
        if self._client is None:
            raise LLMUnavailable("GEMINI_API_KEY is not configured")
        return self._call(system, user, schema, purpose, (model or self.model).strip(), temperature)

    def text(self, system: str, user: str, *, purpose: str = "general", model: str | None = None, temperature: float = 0.2, max_tokens: int = 1200) -> str:
        if self._mock is not None:
            class _S(BaseModel):
                text: str

            return self._mock(system, user, _S).text  # type: ignore[attr-defined]
        if self._groq_key:
            return self._call_groq(system, user, None, purpose, (model or self.model).strip(), temperature, max_tokens=max_tokens)
        if self._client is None:
            raise LLMUnavailable("GEMINI_API_KEY is not configured")
        return self._call(system, user, None, purpose, (model or self.model).strip(), temperature, max_tokens=max_tokens)

    def _call_groq(self, system: str, user: str, schema: type[T] | None, purpose: str, model: str, temperature: float, max_tokens: int | None = None):
        """OpenAI-compatible Groq chat completion. Scoring never goes through this path."""
        import httpx

        formats: list[dict | None] = [None]
        if schema is not None:
            formats = [
                {"type": "json_schema", "json_schema": {"name": schema.__name__, "strict": False, "schema": _groq_schema(schema)}},
                {"type": "json_object"},
            ]
        last = "Groq returned no usable output"
        for fmt in formats:
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            if schema is not None and fmt and fmt.get("type") == "json_object":
                messages[0] = {"role": "system", "content": system + "\n\nReply with one JSON object only. Schema:\n" + str(schema.model_json_schema())}
            body: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": max(0.0, min(1.0, temperature)),
            }
            body["max_tokens"] = max_tokens or (4096 if schema is not None else 1200)
            if fmt is not None:
                body["response_format"] = fmt
            attempt = 0
            while True:
                attempt += 1
                t0 = time.time()
                try:
                    resp = httpx.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {self._groq_key}", "Content-Type": "application/json"},
                        json=body,
                        timeout=120.0,
                    )
                except httpx.HTTPError as exc:
                    if attempt < 3:
                        time.sleep(1.5 * attempt)
                        continue
                    raise LLMError(f"Groq call failed ({type(exc).__name__}) for {purpose}") from exc
                if resp.status_code == 429:
                    wait = _parse_duration(resp.headers.get("retry-after")) or 20.0
                    if attempt < 4 and wait <= 70:
                        log.warning("Groq rate limit for %s; waiting %.0fs", purpose, wait)
                        time.sleep(wait)
                        continue
                    raise LLMQuotaExceeded(
                        f"Groq rate limit reached for {model}. Wait about {int(round(wait))}s and retry.",
                        retry_after=wait,
                        scope="minute",
                        model=model,
                    )
                if resp.status_code in (401, 403):
                    raise LLMError("Groq rejected the API key. Check GROQ_API_KEY on the server.")
                if resp.status_code == 404:
                    raise LLMError(f"Groq model '{model}' was not found. Set GROQ_MODEL to a current model from the Groq console.")
                if resp.status_code == 400 and fmt and fmt.get("type") == "json_schema":
                    last = resp.text[:300]
                    break
                if resp.status_code >= 500 and attempt < 3:
                    time.sleep(1.5 * attempt)
                    continue
                if resp.status_code >= 400:
                    raise LLMError(f"Groq call failed ({resp.status_code}) for {purpose}: {resp.text[:300]}")
                data = resp.json()
                message = ((data.get("choices") or [{}])[0].get("message") or {})
                raw = message.get("content") or message.get("reasoning") or ""
                log.info("LLM %s [%s] %.1fs", purpose, model, time.time() - t0)
                if schema is None:
                    return raw
                try:
                    return _parse_structured(schema, raw)
                except Exception as exc:
                    last = str(exc)
                    if fmt and fmt.get("type") == "json_schema":
                        break
                    raise LLMError(f"Groq returned JSON that does not match {schema.__name__} for {purpose}: {exc}") from exc
        raise LLMError(f"Groq returned no structured output for {purpose}: {last}")

    # ---- transport ----
    def _config(self, system: str, schema: type[BaseModel] | None, model: str, temperature: float, max_tokens: int | None):
        from google.genai import types

        # No tools are ever passed, so turn automatic function calling off explicitly; otherwise the
        # SDK logs a warning on every call.
        kw: dict[str, Any] = {
            "system_instruction": system,
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
        if schema is not None:
            kw["response_mime_type"] = "application/json"
            kw["response_schema"] = schema
        if max_tokens:
            kw["max_output_tokens"] = max_tokens
        # Google advises keeping sampling defaults on Gemini 3.x (low temperatures can loop).
        if not model.startswith("gemini-3"):
            kw["temperature"] = max(0.0, min(1.0, temperature))
        tc = _thinking_config(model, self.settings.gemini_thinking_level)
        if tc is not None:
            kw["thinking_config"] = tc
        return types.GenerateContentConfig(**kw)

    def _call(self, system: str, user: str, schema: type[T] | None, purpose: str, model: str, temperature: float, max_tokens: int | None = None):
        cfg = self._config(system, schema, model, temperature, max_tokens)
        t0 = time.time()
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._client.models.generate_content(model=model, contents=user, config=cfg)  # type: ignore[union-attr]
                break
            except Exception as exc:
                if _is_quota(exc):
                    q = classify_quota_error(getattr(exc, "details", None), model)
                    # Per-minute limits: wait the advertised delay a bounded number of times. Daily
                    # quota: no point waiting; surface immediately.
                    if q.scope == "minute" and attempt < _QUOTA_RETRIES and (q.retry_after or 0) <= 65:
                        log.warning("Gemini rate limit for %s (%s); waiting %.0fs (attempt %d/%d)", purpose, model, q.retry_after, attempt, _QUOTA_RETRIES)
                        time.sleep(q.retry_after or 15)
                        continue
                    log.error("Gemini quota exhausted for %s: %s", purpose, q)
                    raise q from exc
                if _is_transient(exc) and attempt < 3:
                    log.warning("Gemini transient error (%s) for %s; retrying", type(exc).__name__, purpose)
                    time.sleep(1.5 * attempt)
                    continue
                code = getattr(exc, "code", None)
                if code in (401, 403):
                    raise LLMError("Gemini rejected the API key (check GEMINI_API_KEY in .env)") from exc
                if code == 404:
                    raise LLMError(f"Gemini model '{model}' was not found. Set GEMINI_MODEL to a free-tier model listed at https://ai.google.dev/gemini-api/docs/pricing") from exc
                raise LLMError(f"Gemini call failed ({type(exc).__name__}): {getattr(exc, 'message', exc)}") from exc

        meta = getattr(resp, "usage_metadata", None)
        log.info("LLM %s [%s] %.1fs tokens=%s", purpose, model, time.time() - t0, getattr(meta, "total_token_count", None))

        if schema is None:
            return resp.text or ""
        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, schema):
            return parsed
        raw = resp.text or ""
        if raw.strip():
            try:
                return schema.model_validate_json(raw)
            except Exception as exc:
                raise LLMError(f"Gemini returned JSON that does not match {schema.__name__} for {purpose}: {exc}") from exc
        reason = None
        cands = getattr(resp, "candidates", None) or []
        if cands:
            reason = getattr(cands[0], "finish_reason", None)
        raise LLMError(f"Gemini returned no structured output for {purpose} (finish_reason={reason})")


_service: LLMService | None = None


def get_llm() -> LLMService:
    global _service
    if _service is None:
        _service = LLMService()
    return _service


def quota_error_from(exc: BaseException) -> LLMQuotaExceeded | None:
    """Find an LLMQuotaExceeded in an exception chain (nodes may wrap errors)."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, LLMQuotaExceeded):
            return cur
        cur = cur.__cause__ or cur.__context__
    return None

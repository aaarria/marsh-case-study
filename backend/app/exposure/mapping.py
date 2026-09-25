"""Exposure Mapping agent: company facts + advisor priorities -> client-specific exposures.

Every exposure carries its basis (fact ids), reasoning, FACT/INFERENCE/ASSUMPTION status, confidence,
priority weight and the comparison features it maps to. Deterministic fallback when no LLM.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.client import ClientIntake, CompanyProfile, Exposure, FactKind
from app.policies.features import FEATURE_BY_KEY, FEATURES, map_text_to_features
from app.services.llm import LLMQuotaExceeded, LLMService, LLMUnavailable, get_llm
from app.utils.ids import stable_id
from app.utils.logging import get_logger

log = get_logger(__name__)

# Baseline features always evaluated regardless of exposures (core health cover + terms)
BASELINE_FEATURES = [
    "in_patient_hospitalisation", "room_rent", "pre_post_hospitalisation", "restore_recharge",
    "waiting_period_initial", "waiting_period_specific", "waiting_period_ped", "exclusions", "copay", "deductible_options",
]

SYSTEM = """You are an employee-benefits risk analyst at Marsh.
From the client facts and advisor priorities, identify 5-8 client-specific health-insurance EXPOSURES (needs/risks the health cover should address).
Rules:
- basis_fact_ids must reference the given fact ids the exposure rests on. If it rests on a FACT, status=FACT; if derived from facts or industry context, status=INFERENCE; if it rests on a general assumption, status=ASSUMPTION. Never present an inference as a verified fact.
- feature_keys: choose 1-3 keys from the allowed list that the four policies should be tested on for this exposure.
- priority 1.0 = normal; 1.5-2.0 if it matches an advisor priority; 0.7 if speculative.
- Be concrete and client-specific; avoid generic filler. One or two sentences of reasoning."""


class ExposureOut(BaseModel):
    title: str
    description: str
    basis_fact_ids: list[str] = Field(default_factory=list)
    reasoning: str
    status: Literal["FACT", "INFERENCE", "ASSUMPTION"]
    confidence: float = Field(ge=0, le=1)
    priority: float = Field(ge=0.5, le=2.0, default=1.0)
    feature_keys: list[str] = Field(default_factory=list)


class ExposuresOut(BaseModel):
    exposures: list[ExposureOut]


def _baseline(profile: CompanyProfile, intake: ClientIntake) -> list[Exposure]:
    """Deterministic exposures every employer health programme must address."""
    base = [
        Exposure(exposure_id=stable_id(profile.company_name, "hosp"), title="Employee hospitalisation", description="Employees and dependents need in-patient cover with restore of the sum insured after a large claim.",
                 basis=[], reasoning="Core purpose of any employee medical cover.", status=FactKind.FACT, confidence=0.95, priority=1.2, feature_keys=["in_patient_hospitalisation", "room_rent", "restore_recharge"]),
        Exposure(exposure_id=stable_id(profile.company_name, "wait"), title="Waiting periods for new joiners", description="New employees with pre-existing conditions face waiting periods before cover applies.",
                 basis=[], reasoning="Standard retail policies apply initial, specific-illness and PED waiting periods.", status=FactKind.INFERENCE, confidence=0.8, priority=1.0, feature_keys=["waiting_period_initial", "waiting_period_ped", "chronic_conditions_day1"]),
        Exposure(exposure_id=stable_id(profile.company_name, "oop"), title="Out-of-pocket leakage", description="Consumables, room-rent caps and co-payments create out-of-pocket costs for employees.",
                 basis=[], reasoning="Common driver of employee dissatisfaction with health cover.", status=FactKind.INFERENCE, confidence=0.75, priority=1.0, feature_keys=["non_medical_expenses_cover", "copay", "room_rent"]),
    ]
    for p in intake.client_priorities:
        keys = map_text_to_features(p, limit=3)
        if keys:
            base.append(Exposure(exposure_id=stable_id(profile.company_name, "prio", p), title=f"Advisor priority: {p}", description=f"The advisor flagged '{p}' as a client priority.",
                                 basis=["advisor_priority"], reasoning="Stated client priority from the advisor intake.", status=FactKind.FACT, confidence=0.9, priority=1.8, feature_keys=keys))
    return base


def _heuristic_from_profile(profile: CompanyProfile) -> list[Exposure]:
    text = " ".join([profile.industry or "", profile.workforce or "", profile.overview or "", " ".join(profile.key_risks), " ".join(profile.business_characteristics)]).lower()
    out: list[Exposure] = []
    rules = [
        (["it ", "software", "technology", "services", "bpo", "startup"], "Young, sedentary knowledge workforce", "Long screen hours and sedentary work raise lifestyle-disease and mental-health needs; talent retention favours family and wellness benefits.", ["wellness_renewal_discount", "teleconsultation_opd", "maternity"]),
        (["manufactur", "plant", "factory", "logistic", "mining", "construction", "steel", "cement", "chemical"], "Industrial and field workforce", "Operational sites raise accident and emergency-transport exposure.", ["personal_accident", "road_ambulance", "air_ambulance"]),
        (["bank", "financ", "insurance", "consult"], "Urban professional workforce", "Multi-city urban staff expect cashless network access and family cover.", ["network_hospitals", "family_composition", "pricing_zones"]),
        (["retail", "hospitality", "hotel", "restaurant", "airline", "aviation"], "Shift-based, distributed workforce", "Shift work and dispersed locations increase need for wide networks and outpatient access.", ["network_hospitals", "teleconsultation_opd", "pricing_zones"]),
        (["pharma", "hospital", "healthcare", "health care"], "Healthcare sector workforce", "Exposure to infection and shift work; high expectation of comprehensive cover.", ["in_patient_hospitalisation", "day_care", "teleconsultation_opd"]),
        (["global", "international", "multinational", "export", "travel"], "Internationally mobile employees", "Frequent travel or overseas postings create need for global cover.", ["global_cover", "air_ambulance"]),
    ]
    for kws, title, reasoning, keys in rules:
        if any(k in text for k in kws):
            out.append(Exposure(exposure_id=stable_id(profile.company_name, title), title=title, description=reasoning, basis=[f.fact_id for f in profile.facts if f.field in {"industry", "workforce"}][:3], reasoning=reasoning, status=FactKind.INFERENCE, confidence=0.6, priority=1.0, feature_keys=keys))
    if any(k in text for k in ["large", "thousand", "10,000", "50,000", "employees across"]):
        out.append(Exposure(exposure_id=stable_id(profile.company_name, "dist"), title="Large geographically distributed workforce", description="Employees across many cities need consistent cashless access and zone-aware pricing.", basis=[f.fact_id for f in profile.facts if f.field in {"size", "geography"}][:3], reasoning="Derived from size/geography statements.", status=FactKind.INFERENCE, confidence=0.6, priority=1.0, feature_keys=["network_hospitals", "pricing_zones", "family_composition"]))
    return out


def map_exposures(profile: CompanyProfile, intake: ClientIntake, llm: LLMService | None = None) -> list[Exposure]:
    llm = llm or get_llm()
    exposures = _baseline(profile, intake)
    if not llm.available:
        exposures.extend(_heuristic_from_profile(profile))
        return map_exposures_to_features(_dedupe(exposures), llm=llm)

    fact_lines = "\n".join(f"- id={f.fact_id} [{f.kind.value}] ({f.field}) {f.text}" for f in profile.facts[:30])
    allowed = ", ".join(f.key for f in FEATURES)
    user = (
        f"COMPANY: {profile.company_name}\nINDUSTRY: {profile.industry or 'Unknown'}\nSIZE: {profile.size or 'Unknown'}\nGEOGRAPHY: {profile.geography or 'Unknown'}\n"
        f"WORKFORCE: {profile.workforce or 'Unknown'}\nKEY RISKS: {'; '.join(profile.key_risks) or 'Unknown'}\n\nFACTS:\n{fact_lines or '- none'}\n\n"
        f"ADVISOR PRIORITIES: {', '.join(intake.client_priorities) or 'none'}\nADVISOR NOTES: {intake.advisor_notes or 'none'}\n\nALLOWED FEATURE KEYS: {allowed}"
    )
    try:
        out = llm.structured(SYSTEM, user, ExposuresOut, purpose="exposure_mapping")
        valid_ids = {f.fact_id for f in profile.facts}
        for e in out.exposures:
            basis = [b for b in e.basis_fact_ids if b in valid_ids]
            status = FactKind(e.status)
            # an exposure claiming FACT status must rest on at least one sourced FACT
            if status == FactKind.FACT and not any(f.fact_id in basis and f.kind == FactKind.FACT for f in profile.facts):
                status = FactKind.INFERENCE
            exposures.append(Exposure(exposure_id=stable_id(profile.company_name, e.title), title=e.title, description=e.description, basis=basis, reasoning=e.reasoning, status=status, confidence=e.confidence, priority=e.priority, feature_keys=e.feature_keys))
    except LLMUnavailable:
        exposures.extend(_heuristic_from_profile(profile))
    except LLMQuotaExceeded:
        raise  # quota is a stop-the-run error, not something to paper over
    except Exception as exc:
        log.error("Exposure mapping failed: %s; using heuristic exposures", exc)
        exposures.extend(_heuristic_from_profile(profile))
    return map_exposures_to_features(_dedupe(exposures), llm=llm)


def _dedupe(exposures: list[Exposure]) -> list[Exposure]:
    seen: set[str] = set()
    out: list[Exposure] = []
    for e in exposures:
        k = e.title.lower().strip()
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out[:10]


class FeatureMapping(BaseModel):
    feature_keys: list[str] = Field(description="Feature keys from the allowed list that this exposure should be tested against")


def map_exposures_to_features(exposures: list[Exposure], llm: LLMService | None = None) -> list[Exposure]:
    """Ensure every exposure maps to at least one comparison feature: keyword rules first, LLM only as a last resort."""
    llm = llm or get_llm()
    allowed = ", ".join(f"{f.key} ({f.label})" for f in FEATURES)
    for e in exposures:
        if e.feature_keys:
            e.feature_keys = [k for k in e.feature_keys if k in FEATURE_BY_KEY]
        if not e.feature_keys:
            e.feature_keys = map_text_to_features(f"{e.title} {e.description}", limit=4)
        if not e.feature_keys and llm.available:
            try:
                m = llm.structured(
                    "Map a client health-insurance exposure to comparison feature keys. Return only keys from the allowed list (max 4).",
                    f"ALLOWED: {allowed}\n\nEXPOSURE: {e.title}: {e.description}",
                    FeatureMapping,
                    purpose="feature_mapping",
                )
                e.feature_keys = [k for k in m.feature_keys if k in FEATURE_BY_KEY][:4]
            except LLMUnavailable:
                pass
            except LLMQuotaExceeded:
                raise
            except Exception as exc:  # pragma: no cover
                log.warning("Exposure feature mapping LLM failed: %s", exc)
        if not e.feature_keys:
            e.feature_keys = ["in_patient_hospitalisation"]
    return exposures


def features_for_run(exposures: list[Exposure]) -> list[str]:
    keys: list[str] = list(BASELINE_FEATURES)
    for e in exposures:
        for k in e.feature_keys:
            if k not in keys:
                keys.append(k)
    return keys

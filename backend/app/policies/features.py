"""Shared comparison schema: the feature keys every policy is assessed on.

Each feature carries the sub-queries used for per-policy retrieval (query decomposition) and the
lexical anchors that BM25 benefits from. Exposure -> feature mapping happens via `feature_keys`.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FeatureSpec:
    key: str
    label: str
    group: str  # core | financial | wellness | optional | terms
    queries: tuple[str, ...]
    keywords: tuple[str, ...] = field(default_factory=tuple)
    material: bool = True  # whether claims about it are material for the evidence gate


FEATURES: list[FeatureSpec] = [
    FeatureSpec("sum_insured_options", "Sum insured options", "core",
                ("base sum insured options available", "sum insured range in lakhs or crores"),
                ("sum insured", "lakh", "crore", "base sum insured")),
    FeatureSpec("in_patient_hospitalisation", "In-patient hospitalisation", "core",
                ("in-patient hospitalisation expenses covered up to sum insured", "hospitalisation expenses room rent ICU nursing surgeon fees"),
                ("in-patient", "hospitalisation expenses", "up to sum insured")),
    FeatureSpec("room_rent", "Room rent limit", "core",
                ("room rent limit or capping", "room rent at actuals no sub-limit"),
                ("room rent", "at actuals", "sub-limit")),
    FeatureSpec("icu", "ICU cover", "core", ("ICU charges cover limit",), ("ICU",)),
    FeatureSpec("day_care", "Day care treatment", "core", ("day care treatments procedures less than 24 hours",), ("day care",)),
    FeatureSpec("pre_post_hospitalisation", "Pre & post hospitalisation", "core",
                ("pre-hospitalisation and post-hospitalisation expenses number of days",),
                ("pre-hospitalisation", "post-hospitalisation", "60 days", "180 days")),
    FeatureSpec("modern_treatment", "Modern treatments", "core", ("modern treatment advanced procedures coverage",), ("modern treatment",)),
    FeatureSpec("ayush", "AYUSH treatment", "core", ("AYUSH ayurveda homeopathy treatment coverage",), ("AYUSH", "ayurveda")),
    FeatureSpec("domiciliary_home_care", "Domiciliary / home healthcare", "core",
                ("domiciliary hospitalisation home healthcare treatment at home",), ("domiciliary", "home healthcare", "home care")),
    FeatureSpec("organ_donor", "Organ donor expenses", "core", ("organ donor expenses coverage",), ("organ donor",)),
    FeatureSpec("road_ambulance", "Road ambulance", "core", ("road ambulance charges limit",), ("road ambulance", "ambulance")),
    FeatureSpec("air_ambulance", "Air ambulance", "core", ("air ambulance limit per hospitalisation",), ("air ambulance",)),
    FeatureSpec("restore_recharge", "Restore / recharge of sum insured", "core",
                ("automatic restore recharge reload of sum insured unlimited times",), ("restore", "recharge", "reload", "ReAssure")),
    FeatureSpec("sum_insured_growth_bonus", "Sum insured growth / cumulative bonus", "core",
                ("cumulative bonus or sum insured increase every year irrespective of claims",),
                ("cumulative bonus", "infinite benefit", "super credit", "booster")),
    FeatureSpec("non_medical_expenses_cover", "Non-medical / consumables cover", "core",
                ("non-medical expenses consumables non-payable items covered",), ("non-medical", "non-payable", "consumables", "protect benefit", "claim shield", "claim protect", "safeguard")),
    FeatureSpec("shared_room_cash", "Daily cash for shared room", "core",
                ("daily cash for shared accommodation per day maximum",), ("shared accommodation", "daily cash", "per day")),
    FeatureSpec("hospital_daily_cash", "Hospital daily cash", "optional", ("hospital cash per day benefit optional",), ("hospital cash", "hospi cash")),
    FeatureSpec("health_checkup", "Preventive health check-up", "wellness",
                ("preventive annual health check-up limit",), ("health check-up", "health checkup", "preventive")),
    FeatureSpec("teleconsultation_opd", "Teleconsultation / OPD", "wellness",
                ("e-consultation teleconsultation OPD outpatient consultations",), ("e-consultation", "OPD", "outpatient", "e-opinion", "consultation")),
    FeatureSpec("wellness_renewal_discount", "Wellness renewal discount", "wellness",
                ("wellness program renewal discount healthy days steps",), ("renewal discount", "wellness", "steps", "live healthy", "HealthReturns")),
    FeatureSpec("maternity", "Maternity cover", "core",
                ("maternity expenses coverage limit", "maternity exclusion or add-on"),
                ("maternity", "parenthood", "IVF", "newborn")),
    FeatureSpec("chronic_conditions_day1", "Chronic conditions Day-1 cover", "core",
                ("day one cover for diabetes hypertension asthma chronic conditions without waiting period",),
                ("diabetes", "hypertension", "asthma", "chronic", "day 1", "instant cover", "ABCD")),
    FeatureSpec("critical_illness", "Critical illness", "optional", ("critical illness lump sum add-on",), ("critical illness",)),
    FeatureSpec("personal_accident", "Personal accident", "optional", ("personal accident rider accidental death disability",), ("personal accident", "accidental death")),
    FeatureSpec("global_cover", "Global / international cover", "optional", ("international treatment abroad global cover",), ("global", "international", "abroad", "worldwide")),
    FeatureSpec("deductible_options", "Deductible options", "financial", ("aggregate deductible options and premium discount",), ("deductible",)),
    FeatureSpec("copay", "Co-payment", "financial", ("co-payment percentage applicable",), ("co-payment", "co-pay", "copay")),
    FeatureSpec("waiting_period_initial", "Initial waiting period", "terms", ("initial waiting period 30 days",), ("initial waiting period", "30 days")),
    FeatureSpec("waiting_period_specific", "Specific illness waiting period", "terms", ("waiting period specific illnesses named ailments 24 months",), ("24 months", "specific illness", "named ailment")),
    FeatureSpec("waiting_period_ped", "Pre-existing disease waiting period", "terms", ("pre-existing disease waiting period 36 months",), ("pre-existing", "36 months", "PED")),
    FeatureSpec("exclusions", "Exclusions", "terms", ("standard exclusions list not covered",), ("exclusion", "excluded", "not covered")),
    FeatureSpec("eligibility_entry_age", "Entry age / eligibility", "terms", ("entry age minimum maximum eligibility",), ("entry age", "eligib", "exit age")),
    FeatureSpec("family_composition", "Family composition", "terms",
                ("family floater adults children maximum members individual policy",), ("floater", "adults", "children", "spouse", "parents")),
    FeatureSpec("tenure", "Policy tenure", "terms", ("policy tenure years options",), ("tenure", "years")),
    FeatureSpec("pricing_zones", "Pricing zones", "financial", ("premium zones city of residence",), ("zone", "city")),
    FeatureSpec("premium_illustration", "Premium illustration", "financial", ("premium amount example illustration",), ("premium", "INR 22,616")),
    FeatureSpec("discounts", "Discounts", "financial", ("premium discounts family long-term loyalty welcome",), ("discount",)),
    FeatureSpec("network_hospitals", "Cashless network", "terms", ("cashless network hospitals number",), ("cashless", "network hospitals"), material=False),
    FeatureSpec("renewal_portability", "Renewal / portability", "terms", ("lifelong renewal portability migration",), ("renewal", "portability", "migration")),
]

FEATURE_BY_KEY: dict[str, FeatureSpec] = {f.key: f for f in FEATURES}
FEATURE_LABELS: dict[str, str] = {f.key: f.label for f in FEATURES}

# Default subset shown in the comparison matrix (all features are extracted; the UI can expand)
DEFAULT_MATRIX_FEATURES: list[str] = [
    "sum_insured_options", "in_patient_hospitalisation", "room_rent", "pre_post_hospitalisation",
    "restore_recharge", "sum_insured_growth_bonus", "non_medical_expenses_cover", "domiciliary_home_care",
    "air_ambulance", "health_checkup", "teleconsultation_opd", "maternity", "chronic_conditions_day1",
    "global_cover", "deductible_options", "copay", "waiting_period_initial", "waiting_period_specific",
    "waiting_period_ped", "exclusions", "family_composition", "pricing_zones", "premium_illustration", "discounts",
]

# Keyword -> feature keys, used to map free-text exposures / scenarios to features deterministically
EXPOSURE_KEYWORD_MAP: dict[str, list[str]] = {
    "hospital": ["in_patient_hospitalisation", "room_rent", "icu", "restore_recharge"],
    "inpatient": ["in_patient_hospitalisation"],
    "surgery": ["in_patient_hospitalisation", "day_care", "modern_treatment"],
    "maternity": ["maternity", "waiting_period_specific"],
    "pregnan": ["maternity"],
    "family": ["family_composition", "sum_insured_options"],
    "families": ["family_composition", "maternity"],
    "planning famil": ["maternity", "family_composition"],
    "dependent": ["family_composition"],
    "parent": ["family_composition", "eligibility_entry_age"],
    "child": ["family_composition", "eligibility_entry_age"],
    "young": ["wellness_renewal_discount", "teleconsultation_opd", "discounts"],
    "wellness": ["wellness_renewal_discount", "health_checkup"],
    "preventive": ["health_checkup"],
    "check-up": ["health_checkup"],
    "checkup": ["health_checkup"],
    "mental": ["teleconsultation_opd"],
    "opd": ["teleconsultation_opd"],
    "outpatient": ["teleconsultation_opd"],
    "telemedicine": ["teleconsultation_opd"],
    "remote": ["teleconsultation_opd", "network_hospitals", "domiciliary_home_care"],
    "distributed": ["network_hospitals", "pricing_zones"],
    "multi-city": ["network_hospitals", "pricing_zones"],
    "geograph": ["network_hospitals", "pricing_zones"],
    "travel": ["global_cover", "air_ambulance"],
    "international": ["global_cover"],
    "expat": ["global_cover"],
    "chronic": ["chronic_conditions_day1", "waiting_period_ped"],
    "diabetes": ["chronic_conditions_day1"],
    "lifestyle": ["chronic_conditions_day1", "wellness_renewal_discount"],
    "sedentary": ["chronic_conditions_day1", "wellness_renewal_discount"],
    "ageing": ["waiting_period_ped", "chronic_conditions_day1", "eligibility_entry_age"],
    "aging": ["waiting_period_ped", "chronic_conditions_day1", "eligibility_entry_age"],
    "older": ["waiting_period_ped", "eligibility_entry_age"],
    "accident": ["personal_accident", "waiting_period_initial"],
    "injury": ["personal_accident", "in_patient_hospitalisation"],
    "hazard": ["personal_accident", "exclusions"],
    "manufactur": ["personal_accident", "in_patient_hospitalisation"],
    "field": ["personal_accident", "road_ambulance"],
    "logistic": ["personal_accident", "road_ambulance"],
    "driver": ["personal_accident", "road_ambulance"],
    "cost": ["deductible_options", "discounts", "copay", "premium_illustration"],
    "budget": ["deductible_options", "discounts", "premium_illustration"],
    "afford": ["deductible_options", "discounts"],
    "premium": ["premium_illustration", "discounts", "pricing_zones"],
    "inflation": ["sum_insured_growth_bonus", "restore_recharge"],
    "high-value": ["sum_insured_options", "sum_insured_growth_bonus"],
    "critical": ["critical_illness", "sum_insured_options"],
    "cancer": ["critical_illness", "modern_treatment"],
    "cardiac": ["critical_illness", "chronic_conditions_day1"],
    "consumable": ["non_medical_expenses_cover"],
    "non-medical": ["non_medical_expenses_cover"],
    "exhausted": ["restore_recharge"],
    "out-of-pocket": ["non_medical_expenses_cover", "copay", "room_rent"],
    "retention": ["wellness_renewal_discount", "family_composition", "maternity"],
    "attrition": ["wellness_renewal_discount", "family_composition"],
    "talent": ["family_composition", "maternity", "wellness_renewal_discount"],
    "waiting": ["waiting_period_initial", "waiting_period_specific", "waiting_period_ped"],
    "pre-existing": ["waiting_period_ped"],
    "exclusion": ["exclusions"],
    "ayush": ["ayush"],
    "alternative": ["ayush"],
    "home": ["domiciliary_home_care"],
    "shift": ["teleconsultation_opd", "in_patient_hospitalisation"],
    "24/7": ["teleconsultation_opd"],
}


def map_text_to_features(text: str, limit: int = 6) -> list[str]:
    t = text.lower()
    scores: dict[str, int] = {}
    for kw, keys in EXPOSURE_KEYWORD_MAP.items():
        if kw in t:
            for i, k in enumerate(keys):
                scores[k] = scores.get(k, 0) + (len(keys) - i)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    return [k for k, _ in ranked[:limit]]


def map_claim_to_features(text: str, limit: int = 3) -> list[str]:
    """Feature keys for a pitch claim: explicit feature labels/keywords first, exposure keywords as fallback."""
    t = text.lower()
    scores: dict[str, int] = {}
    for spec in FEATURES:
        label = spec.label.lower()
        if label in t or label.split(" / ")[0] in t:
            scores[spec.key] = scores.get(spec.key, 0) + 10
        for i, kw in enumerate(spec.keywords):
            k = kw.lower()
            if len(k) >= 4 and not any(ch.isdigit() for ch in k) and k in t:
                scores[spec.key] = scores.get(spec.key, 0) + (6 if i == 0 else 3)
    ranked = [k for k, _ in sorted(scores.items(), key=lambda kv: -kv[1])]
    if len(ranked) < limit:
        ranked += [k for k in map_text_to_features(text, limit) if k not in ranked]
    return ranked[:limit]

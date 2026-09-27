"""Scenario generation: client exposures -> concrete test scenarios, each bound to feature keys.

Deterministic templates keyed by feature so all four policies are tested on identical scenarios.
"""
from __future__ import annotations

from app.models.client import Exposure
from app.models.fit import Scenario
from app.policies.features import FEATURE_LABELS
from app.utils.ids import stable_id

SCENARIO_TEMPLATES: dict[str, tuple[str, str]] = {
    "in_patient_hospitalisation": ("Employee hospitalisation", "An employee is admitted for 3 days of in-patient treatment including room, ICU, nursing and surgeon fees."),
    "room_rent": ("Room upgrade", "The employee chooses a single private room; is room rent capped or paid at actuals?"),
    "icu": ("ICU admission", "An employee needs ICU care after a cardiac event."),
    "day_care": ("Day-care procedure", "A cataract or dialysis procedure requiring under 24 hours in hospital."),
    "pre_post_hospitalisation": ("Diagnostics before and after", "Tests before admission and follow-up medicines after discharge."),
    "modern_treatment": ("Advanced treatment", "Robotic surgery or other modern treatment method."),
    "ayush": ("AYUSH treatment", "In-patient Ayurveda or homeopathy treatment."),
    "domiciliary_home_care": ("Treatment at home", "Doctor-advised treatment at home instead of hospital."),
    "organ_donor": ("Organ transplant", "Donor harvesting expenses in a transplant."),
    "road_ambulance": ("Emergency road ambulance", "Ambulance transfer to the nearest hospital."),
    "air_ambulance": ("Air ambulance evacuation", "Emergency air evacuation from a remote site."),
    "restore_recharge": ("Sum insured exhausted", "A second large claim in the same year after the base cover is exhausted."),
    "sum_insured_growth_bonus": ("Cover growth over time", "Does cover grow year on year to offset medical inflation?"),
    "non_medical_expenses_cover": ("Consumables in the bill", "Gloves, masks and other non-payable items on the hospital bill."),
    "shared_room_cash": ("Shared-room daily cash", "The employee opts for shared accommodation for 4 days."),
    "hospital_daily_cash": ("Daily cash allowance", "Out-of-pocket daily costs during a 5-day admission."),
    "health_checkup": ("Preventive health check", "Annual health check-up for the workforce."),
    "teleconsultation_opd": ("E-consultation", "Remote doctor consultation. This is not an outpatient reimbursement benefit."),
    "opd": ("Outpatient consultation", "Physical outpatient consultation, separate from e-consultation."),
    "accident_waiting_exception": ("Accident waiting-period exception", "The initial waiting period does not apply to an accident. This is not personal accident cover."),
    "wellness_renewal_discount": ("Wellness engagement", "Employees earn renewal discounts through step tracking."),
    "maternity": ("Maternity", "An employee has a normal or caesarean delivery."),
    "chronic_conditions_day1": ("Chronic condition from day one", "An employee with diabetes or hypertension is hospitalised in month two."),
    "critical_illness": ("Critical illness", "Cancer diagnosis requiring extended treatment."),
    "personal_accident": ("Accidental disability", "Permanent disability after a workplace accident."),
    "global_cover": ("Treatment abroad", "Emergency treatment while travelling internationally."),
    "deductible_options": ("Cost sharing via deductible", "Employer wants to reduce premium with an aggregate deductible."),
    "copay": ("Co-payment on claims", "Is any co-payment applied to hospital bills?"),
    "waiting_period_initial": ("Claim in first month", "A non-accident hospitalisation in the first 30 days of cover."),
    "waiting_period_specific": ("Named ailment in year one", "Hernia or joint replacement surgery within the first two years."),
    "waiting_period_ped": ("Pre-existing condition", "Hospitalisation for a declared pre-existing disease in year two."),
    "exclusions": ("Excluded treatments", "Cosmetic surgery, infertility or hazardous-sport injuries."),
    "eligibility_entry_age": ("Older employees and parents", "Enrolment of a 62-year-old employee or dependent parent."),
    "family_composition": ("Family cover", "Spouse, two children and parents under one floater."),
    "tenure": ("Multi-year cover", "Locking a 2- or 3-year policy term."),
    "pricing_zones": ("Multi-city workforce pricing", "Employees in Delhi NCR, Mumbai, Pune and tier-2 cities."),
    "premium_illustration": ("Indicative premium", "What indicative premium does the brochure show?"),
    "discounts": ("Premium discounts", "Family, long-term or wellness discounts available."),
    "network_hospitals": ("Cashless network", "Cashless admission in a network hospital."),
    "renewal_portability": ("Renewal and portability", "Lifelong renewal and porting existing cover."),
}


def build_scenarios(exposures: list[Exposure], max_per_exposure: int = 3) -> list[Scenario]:
    scenarios: list[Scenario] = []
    seen: set[tuple[str, str]] = set()
    for e in exposures:
        keys = [k for k in e.feature_keys if k in SCENARIO_TEMPLATES][:max_per_exposure]
        for k in keys:
            if (e.exposure_id, k) in seen:
                continue
            seen.add((e.exposure_id, k))
            title, desc = SCENARIO_TEMPLATES[k]
            scenarios.append(
                Scenario(
                    scenario_id=stable_id(e.exposure_id, k, length=10),
                    exposure_id=e.exposure_id,
                    title=title,
                    description=f"{desc} (tests: {FEATURE_LABELS.get(k, k)})",
                    feature_keys=[k],
                    weight=max(0.1, float(e.priority)) * (1.0 if e.status.value == "FACT" else 0.85 if e.status.value == "INFERENCE" else 0.7),
                    client_asked=float(e.priority) >= 1.5,
                )
            )
    return scenarios

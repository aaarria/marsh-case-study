"""Atomic claim extraction and typing from a pitch (deterministic)."""
from __future__ import annotations

import re

from app.models.pitch import Claim, ClaimType, Pitch
from app.policies.features import map_claim_to_features
from app.utils.ids import stable_id

KIND_TO_TYPE = {
    "policy": ClaimType.POLICY,
    "company": ClaimType.COMPANY,
    "marsh": ClaimType.MARSH_POSITIONING,
    "recommendation": ClaimType.RECOMMENDATION,
    "assumption": ClaimType.ASSUMPTION,
}
_NUM_RE = re.compile(r"(?:INR|Rs\.?|₹)?\s?\d[\d,]*(?:\.\d+)?\s?(?:%|lakhs?|lacs?|crores?|cr|days?|months?|years?|x|X)?", re.IGNORECASE)
_COVERAGE_WORDS = re.compile(r"\b(cover|covered|covers|pays|payable|reimburs|includ|benefit|limit|waiting|excluded|exclusion|co-?pay|deductible|restore|bonus|up to|unlimited)", re.IGNORECASE)


def _numbers(text: str) -> list[str]:
    out = []
    for m in _NUM_RE.finditer(text):
        tok = m.group(0).strip()
        if re.search(r"\d", tok) and len(tok.replace(" ", "")) > 0:
            out.append(tok)
    return out


def extract_claims(pitch: Pitch, allowed_features: list[str] | None = None) -> list[Claim]:
    claims: list[Claim] = []
    for s in pitch.slides:
        for bi, b in enumerate(s.bullets):
            ctype = KIND_TO_TYPE.get(b.kind, ClaimType.RECOMMENDATION)
            text = b.text.strip()
            # a recommendation bullet that asserts policy coverage terms is audited as a policy claim
            if ctype == ClaimType.RECOMMENDATION and b.source_chunk_ids:
                ctype = ClaimType.POLICY
            feats = map_claim_to_features(text, limit=3)
            if allowed_features:
                feats = [f for f in feats if f in allowed_features] or feats
            material = ctype == ClaimType.POLICY and (bool(_COVERAGE_WORDS.search(text)) or bool(_numbers(text)))
            claims.append(
                Claim(
                    claim_id=stable_id(pitch.pitch_id, str(pitch.version), str(s.slide_number), str(bi), text),
                    claim_text=text,
                    claim_type=ctype,
                    slide=s.slide_number,
                    bullet_index=bi,
                    policy_id=b.policy_id or (pitch.recommended_policy_id if ctype == ClaimType.POLICY else None),
                    feature_key=feats[0] if feats else None,
                    numbers=_numbers(text),
                    material=material,
                )
            )
    return claims

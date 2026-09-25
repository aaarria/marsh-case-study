"""Locate the four policy PDFs in data/policies and bind them to profiles.

Filenames are NOT assumed: each profile carries glob patterns; the registry matches the actual
files present and fails loudly if a policy is missing or ambiguous.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.config import get_settings
from app.models.policy import PolicyDocument

PROFILES_DIR = Path(__file__).parent / "profiles"


class PolicyRegistryError(RuntimeError):
    pass


@dataclass
class PolicyProfile:
    policy_id: str
    policy_name: str
    short_label: str
    insurer: str
    product_uin: str | None
    file_patterns: list[str]
    variants: list[str] = field(default_factory=list)
    text_mode_pages: list[int] = field(default_factory=list)
    skip_pages: list[int] = field(default_factory=list)
    section_hints: dict[str, str] = field(default_factory=dict)
    marketing_stat_patterns: list[str] = field(default_factory=list)
    add_on_names: list[str] = field(default_factory=list)
    variant_scope_patterns: dict[str, str] = field(default_factory=dict)
    page_default_sections: dict[int, str] = field(default_factory=dict)
    table_mode_pages: dict[int, dict] = field(default_factory=dict)

    def section_kind(self, heading: str) -> str | None:
        for pattern, kind in self.section_hints.items():
            if re.search(pattern, heading, re.IGNORECASE):
                return kind
        return None

    def is_marketing_stat(self, text: str) -> bool:
        return any(re.search(p, text, re.IGNORECASE) for p in self.marketing_stat_patterns)

    def variant_scope(self, text: str) -> str | None:
        for pattern, scope in self.variant_scope_patterns.items():
            if re.search(pattern, text):
                return scope
        return None


def load_profiles() -> list[PolicyProfile]:
    profiles: list[PolicyProfile] = []
    for path in sorted(PROFILES_DIR.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        profiles.append(
            PolicyProfile(
                policy_id=raw["policy_id"],
                policy_name=raw["policy_name"],
                short_label=raw.get("short_label", ""),
                insurer=raw.get("insurer", ""),
                product_uin=raw.get("product_uin"),
                file_patterns=raw.get("file_patterns", []),
                variants=raw.get("variants", []),
                text_mode_pages=raw.get("text_mode_pages", []) or [],
                skip_pages=raw.get("skip_pages", []) or [],
                section_hints=raw.get("section_hints", {}) or {},
                marketing_stat_patterns=raw.get("marketing_stat_patterns", []) or [],
                add_on_names=raw.get("add_on_names", []) or [],
                variant_scope_patterns=raw.get("variant_scope_patterns", {}) or {},
                page_default_sections={int(k): v for k, v in (raw.get("page_default_sections", {}) or {}).items()},
                table_mode_pages={int(k): (v or {}) for k, v in (raw.get("table_mode_pages", {}) or {}).items()},
            )
        )
    return profiles


def discover_policies(policies_dir: Path | None = None) -> list[tuple[PolicyProfile, PolicyDocument]]:
    """Match PDFs on disk to profiles. Raises PolicyRegistryError when a policy is missing."""
    settings = get_settings()
    root = policies_dir or settings.policies_path
    if not root.exists():
        raise PolicyRegistryError(f"Policies directory not found: {root}")
    pdfs = sorted(p for p in root.glob("*.pdf"))
    if not pdfs:
        raise PolicyRegistryError(f"No PDF files found in {root}")

    results: list[tuple[PolicyProfile, PolicyDocument]] = []
    missing: list[str] = []
    for profile in load_profiles():
        matches = [p for p in pdfs if any(fnmatch.fnmatch(p.name, pat) for pat in profile.file_patterns)]
        if not matches:
            missing.append(f"{profile.policy_name} (patterns: {profile.file_patterns})")
            continue
        if len(matches) > 1:
            raise PolicyRegistryError(f"Ambiguous PDF match for {profile.policy_id}: {[m.name for m in matches]}")
        pdf = matches[0]
        results.append(
            (
                profile,
                PolicyDocument(
                    policy_id=profile.policy_id,
                    policy_name=profile.policy_name,
                    insurer=profile.insurer,
                    product_uin=profile.product_uin,
                    document_path=str(pdf.relative_to(settings.policies_path.parent.parent) if pdf.is_relative_to(settings.policies_path.parent.parent) else pdf),
                    file_name=pdf.name,
                    variants=profile.variants,
                    short_label=profile.short_label,
                ),
            )
        )
    if missing:
        raise PolicyRegistryError("Missing policy documents: " + "; ".join(missing))
    return results

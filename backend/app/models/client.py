"""Client-side domain models: intake, company profile facts, exposures."""
from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints


class FactKind(str, Enum):
    FACT = "FACT"  # advisor input or a web-sourced statement
    INFERENCE = "INFERENCE"  # derived from facts
    ASSUMPTION = "ASSUMPTION"  # model/world knowledge without a source
    UNKNOWN = "UNKNOWN"


class WebSource(BaseModel):
    url: str
    title: str | None = None
    published_date: str | None = None
    snippet: str | None = None
    accessible: bool | None = None
    retrieved_at: str | None = None


class ClientFact(BaseModel):
    fact_id: str
    field: str  # overview | industry | size | geography | workforce | business | risk
    text: str
    kind: FactKind = FactKind.UNKNOWN
    sources: list[WebSource] = Field(default_factory=list)
    confidence: float = 0.0


class ClientIntake(BaseModel):
    company_name: str = Field(min_length=2, max_length=120)
    industry: str | None = Field(default=None, max_length=120)
    geography: str | None = Field(default=None, max_length=120)
    employee_count: int | None = Field(default=None, ge=1, le=10_000_000)
    advisor_notes: str | None = Field(default=None, max_length=2000)
    client_priorities: list[Annotated[str, StringConstraints(max_length=200)]] = Field(default_factory=list, max_length=12)
    selected_policy_ids: list[str] | None = None  # None -> all four


class CompanyProfile(BaseModel):
    company_name: str
    overview: str | None = None
    industry: str | None = None
    size: str | None = None
    geography: str | None = None
    workforce: str | None = None
    business_characteristics: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    facts: list[ClientFact] = Field(default_factory=list)
    research_status: str = "OK"  # OK | PARTIAL | UNAVAILABLE
    research_note: str | None = None


class Exposure(BaseModel):
    exposure_id: str
    title: str
    description: str
    basis: list[str] = Field(default_factory=list)  # fact_ids or free-text basis
    reasoning: str
    status: FactKind = FactKind.INFERENCE
    confidence: float = 0.5
    priority: float = 1.0  # weight; advisor priorities raise this
    feature_keys: list[str] = Field(default_factory=list)  # comparison-schema features this exposure maps to

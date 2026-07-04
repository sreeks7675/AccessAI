"""
JSON Contracts - the glue between all workstreams.
DO NOT change field names or types without team approval (Section 6.3).
Any change here breaks 2-3 other people's work.
"""

from pydantic import BaseModel
from typing import Optional, Literal


# ── IP-1: DOM Payload (Extension → Backend) ──────────────────────
# Defined by Mahesh, implemented by Anirudh. Merges Day 3.

class DOMMeta(BaseModel):
    spa_detected: bool
    dom_size_bytes: int
    page_title: str
    lang_attribute: str


class DOMPayload(BaseModel):
    url: str
    timestamp: str  # ISO 8601, e.g. "2026-07-02T10:30:00Z"
    dom_html: str
    computed_styles: dict[str, dict[str, str]]  # selector -> {css_prop: value}
    meta: DOMMeta


# ── IP-2: Finding Object (Agents → Orchestrator → Fix Engine) ────
# Defined by Sreekar, implemented by Mahesh + Charan. Merges Day 4-5.

class Finding(BaseModel):
    id: str  # uuid
    disability_class: Literal["visual", "auditory", "motor", "cognitive", "at_parsing"]
    criterion_number: str  # e.g. "1.4.3"
    criterion_level: Literal["A", "AA", "AAA"]
    criterion_text: str  # verbatim WCAG text from vector store
    legal_regulations: list[str]  # e.g. ["ADA Title III", "EAA Article 4"]
    finding_description: str
    disability_impact: str
    element_selector: str
    confidence: float
    status: Literal["confirmed", "needs_context"]
    critique_verdict: Literal["CONFIRMED", "REJECTED", "NEEDS_CONTEXT"]
    critique_citation: str
    impact_score: int


# ── IP-3: Full Report JSON (Backend → Extension) ─────────────────
# Defined by Mahesh, rendered by Anirudh. Merges Day 6.

class Fix(BaseModel):
    patch_html: str
    patch_validated: bool
    diff_html: str
    preview_srcdoc: str
    requires_human_review: bool
    review_reason: Optional[str] = None


class FindingWithFix(Finding):
    fix: Optional[Fix] = None


class AuditMetadata(BaseModel):
    url: str
    timestamp: str
    wcag_version: str
    spa_detected: bool


class Benchmark(BaseModel):
    axe_findings: int
    wave_findings: int
    our_findings: int
    unique: int


class NewsItem(BaseModel):
    headline: str
    summary: str
    wcag_tags: list[str]
    date: str


class ReportJSON(BaseModel):
    audit_metadata: AuditMetadata
    findings: list[FindingWithFix]
    benchmark: Benchmark
    news_preview: list[NewsItem]  # top 3
    disclaimer: str
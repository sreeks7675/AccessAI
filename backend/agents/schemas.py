"""
WS-3 — Pydantic Data Models for the Agent Pipeline

WHY THIS FILE EXISTS FIRST:
    Every agent, the critique sub-agent, and the orchestrator all pass data
    between each other. If we let each component define its own shapes, we
    get subtle mismatches — 'criterion_num' vs 'criterion_number', a list
    where a string is expected, a missing field that crashes at 2AM.

    Pydantic enforces the contract AT RUNTIME. If an LLM returns a field
    with the wrong type, Pydantic raises ValidationError immediately, the
    retry logic in base_agent.py catches it, and we never surface a corrupt
    finding to the user.

    Define schemas FIRST. Write agents SECOND. This is the industry practice.

Models defined here (in dependency order):
    DisabilityClass      — Enum of the 5 agent domains
    ConfidenceLevel      — Enum mapping float ranges to human labels
    CritiqueVerdict      — Enum of 3 possible critique outcomes
    WCAGCriterion        — A single WCAG success criterion (from vector store)
    AgentFinding         — One accessibility finding from a disability agent
    CritiqueResult       — The critique agent's verdict on one AgentFinding
    AuditAgentInput      — Input payload for any disability agent
    PipelineState        — LangGraph shared state (the 'clipboard' of the graph)

Author : Sreekar (WS-3)
Design : Section 2.3.1, 2.3.2, 2.4 of WCAG Audit Agent Design Document v1.0
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Annotated, Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── DisabilityClass ────────────────────────────────────────────────────────────

class DisabilityClass(str, Enum):
    """
    The five disability domains covered by the audit pipeline.

    Each value corresponds to exactly one specialist agent and one ChromaDB
    collection in the vector store. The string values match the keys used in
    regulation_mapping.csv and wcag_criteria.json — they must stay in sync.

    Design Doc Reference: Section 2.3.2 (Disability-Stratified Audit Agent Design)
    """
    VISUAL     = "visual"
    AUDITORY   = "auditory"
    MOTOR      = "motor"
    COGNITIVE  = "cognitive"
    AT_PARSING = "at_parsing"


# ── ConfidenceLevel ────────────────────────────────────────────────────────────

class ConfidenceLevel(str, Enum):
    """
    Human-readable label for an agent's confidence in a finding.

    Maps to float thresholds enforced in base_agent.py:
        HIGH   >= 0.85 → directly passes to critique agent
        MEDIUM  0.7–0.84 → passes to critique but flagged for attention
        LOW    < 0.7  → automatically routed to NEEDS_CONTEXT status

    Design Doc Reference: Section 5.2.2 (Confidence threshold guardrail)
    """
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"

    @classmethod
    def from_score(cls, score: float) -> "ConfidenceLevel":
        """
        Derive the label from a raw float confidence score.

        Parameters
        ----------
        score : float
            Value between 0.0 and 1.0.

        Returns
        -------
        ConfidenceLevel
        """
        if score >= 0.85:
            return cls.HIGH
        elif score >= 0.70:
            return cls.MEDIUM
        else:
            return cls.LOW


# ── CritiqueVerdict ────────────────────────────────────────────────────────────

class CritiqueVerdict(str, Enum):
    """
    The three possible verdicts from the Critique Sub-Agent.

    CONFIRMED    — Finding is valid. Critique agent has provided a verbatim
                   WCAG citation. Finding enters the confirmed_findings list.

    REJECTED     — Finding is a false positive. Critique agent has explained
                   why the criterion is NOT violated. Finding is silently
                   dropped (logged at DEBUG level, never shown to user).

    NEEDS_CONTEXT — Finding cannot be determined from static DOM alone.
                   Requires runtime interaction, server-side knowledge, or
                   AT user testing. Shown in report with MANUAL_REVIEW badge.

    Design Doc Reference: Section 2.4.1 (Critique Agent System Prompt Structure)
    """
    CONFIRMED     = "CONFIRMED"
    REJECTED      = "REJECTED"
    NEEDS_CONTEXT = "NEEDS_CONTEXT"


# ── WCAGCriterion ──────────────────────────────────────────────────────────────

class WCAGCriterion(BaseModel):
    """
    A WCAG success criterion as retrieved from the vector store.

    This is a lightweight DTO (Data Transfer Object) — it carries only the
    fields needed by the agents. The full criterion record in wcag_criteria.json
    has additional fields (pillar, wcag_version, etc.) that agents don't need.

    The criterion_text MUST be verbatim from the W3C spec (via the vector store).
    Agents are NOT allowed to paraphrase this field. The critique agent re-fetches
    it independently to verify.

    Design Doc Reference: Section 2.4.2 (Critique Agent Citation Requirement)
    """
    model_config = ConfigDict(use_enum_values=True, str_strip_whitespace=True)

    criterion_number: str = Field(
        ...,
        description="Dot-notation number e.g. '1.4.3'",
        pattern=r"^\d+\.\d+\.\d+$",
    )
    criterion_level: str = Field(
        ...,
        description="Conformance level: A, AA, or AAA",
        pattern=r"^(A|AA|AAA)$",
        alias="conformance_level",   # JSON from vector store uses conformance_level
    )
    criterion_text: str = Field(
        ...,
        description="Verbatim normative WCAG criterion text",
        min_length=10,
    )
    legal_regulations: list[str] = Field(
        default_factory=list,
        description="Legal frameworks that mandate this criterion",
    )

    model_config = ConfigDict(
        use_enum_values=True,
        str_strip_whitespace=True,
        populate_by_name=True,     # allow both 'criterion_level' and 'conformance_level'
    )

    @field_validator("legal_regulations", mode="before")
    @classmethod
    def parse_regulations(cls, v: Any) -> list[str]:
        """Accept comma-separated string or list."""
        if isinstance(v, str):
            import json as _json
            try:
                parsed = _json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass
            return [x.strip() for x in v.split(",") if x.strip()]
        return v or []


# ── AgentFinding ───────────────────────────────────────────────────────────────

class AgentFinding(BaseModel):
    """
    A single accessibility finding produced by one of the five disability agents.

    This is the central data structure of the pipeline. Every field has a
    specific purpose in the downstream flow:

    - id               : UUIDv4 auto-generated — links finding to critique result
    - disability_class : routes this finding to the right display section in the UI
    - criterion        : what rule was violated (carries verbatim WCAG text)
    - finding_description : plain-English description of what's wrong
    - disability_impact   : how a real AT user is blocked (not just 'this fails X')
    - element_selector    : CSS selector — Charan's fix engine uses this to target the patch
    - confidence          : agent's self-rated certainty (0.0–1.0)
    - needs_context_reason: set by guardrail when confidence < 0.7

    Design Doc Reference: Section 2.3.2, Section 5.2.2
    """
    model_config = ConfigDict(use_enum_values=True, str_strip_whitespace=True)

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUIDv4 auto-generated unique identifier for this finding",
    )
    disability_class: DisabilityClass = Field(
        ...,
        description="Which disability domain this finding belongs to",
    )
    criterion: WCAGCriterion = Field(
        ...,
        description="The WCAG criterion violated — must carry verbatim text",
    )
    finding_description: str = Field(
        ...,
        description="Plain-English description of the violation",
        min_length=10,
    )
    disability_impact: str = Field(
        ...,
        description=(
            "How a real AT user is specifically blocked. "
            "Must name the AT or user type (e.g. 'A VoiceOver user navigating by headings...'). "
            "Not a restatement of the WCAG rule."
        ),
        min_length=10,
    )
    element_selector: str = Field(
        ...,
        description=(
            "Valid CSS selector targeting the specific DOM element(s) implicated. "
            "Used by Charan's fix engine to generate a targeted code patch."
        ),
        min_length=1,
    )
    confidence: float = Field(
        ...,
        description="Agent confidence in this finding (0.0–1.0)",
        ge=0.0,
        le=1.0,
    )
    needs_context_reason: Optional[str] = Field(
        default=None,
        description=(
            "If set, this finding requires manual review. "
            "Contains a specific instruction for the human auditor. "
            "Set automatically when confidence < 0.7 or when the agent "
            "cannot determine the violation from static DOM alone."
        ),
    )
    status: str = Field(
        default="pending",
        description="Pipeline status: pending → confirmed | needs_context | rejected",
        pattern=r"^(pending|confirmed|needs_context|rejected)$",
    )

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        """Round to 2dp to prevent floating point noise in comparisons."""
        return round(v, 2)

    @model_validator(mode="after")
    def auto_set_needs_context(self) -> "AgentFinding":
        """
        If confidence < 0.7 and no needs_context_reason is set, auto-populate
        the reason. This implements the confidence gate guardrail from
        Design Doc Section 5.2.2 at the data model level.
        """
        if self.confidence < 0.70 and self.needs_context_reason is None:
            self.needs_context_reason = (
                f"Confidence score {self.confidence:.2f} is below threshold 0.70. "
                "Manual verification required before treating as confirmed violation."
            )
            self.status = "needs_context"
        return self

    @property
    def confidence_level(self) -> ConfidenceLevel:
        """Human-readable confidence label derived from float score."""
        return ConfidenceLevel.from_score(self.confidence)


# ── CritiqueResult ─────────────────────────────────────────────────────────────

class CritiqueResult(BaseModel):
    """
    The Critique Sub-Agent's verdict on a single AgentFinding.

    The critique agent is the quality gate — it receives a finding and must
    independently verify it against the WCAG vector store. It never trusts
    the criterion text in the finding; it re-fetches from the store.

    citation is MANDATORY for CONFIRMED verdicts. The code in critique_agent.py
    enforces this: if citation is empty on a CONFIRMED verdict, the result is
    overridden to REJECTED before it leaves the critique agent.

    Design Doc Reference: Section 2.4 (entire section)
    """
    model_config = ConfigDict(use_enum_values=True, str_strip_whitespace=True)

    finding_id: str = Field(
        ...,
        description="UUID matching the AgentFinding.id this result corresponds to",
    )
    verdict: CritiqueVerdict = Field(
        ...,
        description="CONFIRMED, REJECTED, or NEEDS_CONTEXT",
    )
    citation: str = Field(
        default="",
        description=(
            "Verbatim WCAG criterion text re-fetched from the vector store. "
            "MANDATORY for CONFIRMED verdicts — empty string means REJECTED."
        ),
    )
    rejection_reason: Optional[str] = Field(
        default=None,
        description="Why the finding is invalid. Required when verdict=REJECTED.",
    )
    manual_review_instruction: Optional[str] = Field(
        default=None,
        description=(
            "Specific instruction for the human auditor. "
            "Required when verdict=NEEDS_CONTEXT. "
            "Must state WHAT to test, HOW to test it, and WHY static analysis is insufficient."
        ),
    )

    @model_validator(mode="after")
    def enforce_citation_requirement(self) -> "CritiqueResult":
        """
        Hard rule from Design Doc Section 2.4.2:
        If verdict is CONFIRMED but citation is empty or too short,
        override to REJECTED. No citation = no confirmed finding.

        This is the primary anti-hallucination guardrail at the data layer.
        The same check also runs in guardrails.py as a defence-in-depth measure.
        """
        if self.verdict == CritiqueVerdict.CONFIRMED:
            if not self.citation or len(self.citation.strip()) < 20:
                self.verdict = CritiqueVerdict.REJECTED
                self.rejection_reason = (
                    "Critique agent failed to provide a valid verbatim WCAG citation. "
                    "Finding automatically rejected per citation enforcement guardrail. "
                    f"(Citation provided was: '{self.citation}')"
                )
        return self

    @model_validator(mode="after")
    def validate_needs_context_has_instruction(self) -> "CritiqueResult":
        """
        NEEDS_CONTEXT verdicts must carry a specific manual review instruction.
        A vague 'manual testing required' is not acceptable — the instruction
        must tell the auditor exactly what to check.
        """
        if (
            self.verdict == CritiqueVerdict.NEEDS_CONTEXT
            and not self.manual_review_instruction
        ):
            self.manual_review_instruction = (
                "Manual review required. The critique agent could not determine "
                "this finding from static DOM analysis alone. "
                "Test with screen reader or AT device in the relevant disability context."
            )
        return self


# ── AuditAgentInput ────────────────────────────────────────────────────────────

class AuditAgentInput(BaseModel):
    """
    Input payload passed to a disability agent's audit() method.

    The orchestrator (Mahesh's WS-2) creates one AuditAgentInput per
    disability class and dispatches them in parallel via asyncio.gather().

    dom_chunk is NOT the full page DOM — it is a semantic region (e.g. the
    content of a <main> or <form> element) produced by the dom_chunker node
    in pipeline.py. Chunking prevents context window overflow and focuses
    each agent on relevant content.

    Design Doc Reference: Section 2.3.1 (Audit Orchestrator step 3)
    """
    model_config = ConfigDict(use_enum_values=True, str_strip_whitespace=True)

    disability_class: DisabilityClass = Field(
        ...,
        description="Which disability agent should process this input",
    )
    dom_chunk: str = Field(
        ...,
        description=(
            "Semantic HTML chunk extracted from the page. "
            "Not the full DOM — a region (header, nav, main, form, etc.). "
            "Max ~50KB to stay within LLM context window."
        ),
        min_length=10,
    )
    url: str = Field(
        ...,
        description="Original page URL — used for context and report metadata",
        min_length=1,
    )
    chunk_index: int = Field(
        default=0,
        description="0-based index of this chunk within the full page (for debugging)",
        ge=0,
    )
    total_chunks: int = Field(
        default=1,
        description="Total number of chunks the page was split into",
        ge=1,
    )


# ── PipelineState ──────────────────────────────────────────────────────────────

class PipelineState(BaseModel):
    """
    The shared state object passed between every node in the LangGraph graph.

    Think of this as the clipboard. Every node reads what it needs from here
    and writes its output back to the relevant fields. LangGraph merges the
    returned partial state dict after each node — nodes should only return
    the keys they modified, not a copy of the full state.

    Lifecycle of a finding through PipelineState:
        dom_chunks populated by dom_chunker node
            → raw_findings populated by run_agents node (5 agents in parallel)
                → critique_results populated by run_critique node
                    → confirmed_findings populated by route_findings node
                        → report JSON assembled by assemble_report node

    Design Doc Reference: Section 2.3.1 (Audit Orchestrator — full pipeline flow)
    """
    model_config = ConfigDict(use_enum_values=True)

    # ── Input (set by orchestrator before graph starts) ────────────────────────
    url: str = Field(
        default="",
        description="URL of the page being audited",
    )
    full_dom: str = Field(
        default="",
        description="Full serialised DOM from the browser extension content script",
    )
    dom_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Page metadata from content script: "
            "{ spa_detected, dom_size_bytes, page_title, lang_attribute }"
        ),
    )

    # ── Populated by dom_chunker node ──────────────────────────────────────────
    dom_chunks: list[str] = Field(
        default_factory=list,
        description="DOM split into semantic region chunks for agent consumption",
    )

    # ── Populated by run_agents node ───────────────────────────────────────────
    raw_findings: list[AgentFinding] = Field(
        default_factory=list,
        description="All findings from all 5 agents before critique filtering",
    )

    # ── Populated by run_critique node ─────────────────────────────────────────
    critique_results: list[CritiqueResult] = Field(
        default_factory=list,
        description="One CritiqueResult per AgentFinding from raw_findings",
    )

    # ── Populated by route_findings node ──────────────────────────────────────
    confirmed_findings: list[AgentFinding] = Field(
        default_factory=list,
        description=(
            "Findings that passed the critique gate (verdict=CONFIRMED). "
            "These are the findings the user sees in the report."
        ),
    )
    manual_review_findings: list[AgentFinding] = Field(
        default_factory=list,
        description=(
            "Findings that need human review (verdict=NEEDS_CONTEXT or "
            "agent confidence < 0.7). Shown with MANUAL_REVIEW badge in UI."
        ),
    )

    # ── Populated by assemble_report node ──────────────────────────────────────
    audit_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Report metadata assembled at the end: "
            "{ wcag_version, conformance_level_target, tool_version, "
            "  timestamp, spa_detected, total_findings_raw, "
            "  total_findings_confirmed, total_manual_review }"
        ),
    )
    final_report: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Complete output JSON matching IP-3 contract (Backend → Extension). "
            "See Section 4 of the Master GitHub Strategy document."
        ),
    )

    # ── Pipeline execution tracking ────────────────────────────────────────────
    errors: list[str] = Field(
        default_factory=list,
        description="Non-fatal errors encountered during pipeline execution",
    )
    pipeline_version: str = Field(
        default="1.0.0",
        description="Semantic version of the pipeline — for reproducibility",
    )

    def summary(self) -> str:
        """
        One-line summary string for logging.
        Called after each node completes for structured pipeline logging.
        """
        return (
            f"url={self.url[:40]}... | "
            f"chunks={len(self.dom_chunks)} | "
            f"raw={len(self.raw_findings)} | "
            f"critiqued={len(self.critique_results)} | "
            f"confirmed={len(self.confirmed_findings)} | "
            f"manual_review={len(self.manual_review_findings)} | "
            f"errors={len(self.errors)}"
        )
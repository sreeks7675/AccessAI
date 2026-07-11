"""
backend/agents/guardrails.py
=============================
WS-3 — Pipeline Guardrails

Three defence-in-depth guardrails that operate at different points
in the audit pipeline. They exist as standalone functions — not embedded
inside agents — so they can be:
    - Called explicitly by pipeline.py at any stage
    - Unit-tested in complete isolation from LLM inference
    - Applied consistently regardless of which agent produced a finding

WHY THREE SEPARATE LAYERS?
    The Pydantic models in schemas.py already enforce some rules at
    instantiation time (e.g. CritiqueResult auto-overrides empty citations,
    AgentFinding auto-sets needs_context_reason on low confidence).
    These guardrails are the THIRD layer — explicit, callable, auditable.
    Defence in depth: if Pydantic validation is bypassed (e.g. a dict is
    passed instead of a model), these functions still enforce the rules.

Guardrail 1 — check_context_sufficiency()
    Called BEFORE an agent runs on a DOM chunk.
    Prevents agents from wasting LLM inference on irrelevant content
    (e.g. auditory agent seeing a DOM chunk with no media elements).

Guardrail 2 — enforce_citation()
    Called AFTER the critique agent returns a CritiqueResult.
    Hard rule from Design Doc Section 2.4.2:
    No citation = no CONFIRMED finding, regardless of agent confidence.
    This is the primary anti-hallucination gate.

Guardrail 3 — apply_confidence_gate()
    Called AFTER an agent returns AgentFinding objects.
    Two thresholds:
        < 0.50 → drop the finding entirely (return None)
        < 0.70 → keep but mark as needs_context

Design Doc References:
    Section 5.2.1 — Input Guardrails
    Section 5.2.2 — Inference Guardrails
    Section 2.4.2 — Citation Requirement (CRITICAL)

Author : Sreekar (WS-3)
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from .schemas import (
    AgentFinding,
    CritiqueResult,
    CritiqueVerdict,
    DisabilityClass,
)

logger = logging.getLogger("guardrails")

# ── Confidence thresholds (Design Doc Section 5.2.2) ─────────────────────────
CONFIDENCE_DROP_THRESHOLD    = 0.50   # below this: drop finding entirely
CONFIDENCE_CONTEXT_THRESHOLD = 0.70   # below this: mark as needs_context

# ── Citation minimum length (Design Doc Section 2.4.2) ───────────────────────
MIN_CITATION_LENGTH = 20

# ── Cognitive text minimum word count ────────────────────────────────────────
COGNITIVE_MIN_WORD_COUNT = 50


# ══════════════════════════════════════════════════════════════════════════════
# GUARDRAIL 1 — Context Sufficiency Check
# ══════════════════════════════════════════════════════════════════════════════

def check_context_sufficiency(
    disability_class: DisabilityClass,
    dom_chunk: str,
) -> bool:
    """
    Return True only if the DOM chunk contains content relevant to the
    given disability class.

    Called by pipeline.py BEFORE dispatching a chunk to an agent.
    An agent that sees irrelevant content has two bad failure modes:
        1. It hallucinates violations on content it can't meaningfully evaluate
        2. It wastes LLM inference time and GPU compute returning empty results

    This function prevents both by checking for presence of relevant HTML
    elements BEFORE the LLM is called.

    Rules by disability class (Design Doc Section 5.2.2):
        visual     → always True (visual checks apply to all HTML content)
        auditory   → True only if chunk contains <audio>, <video>, or <iframe>
        motor      → True only if chunk contains <a>, <button>, <input>,
                     <select>, <textarea>, or tabindex attribute
        cognitive  → True only if chunk contains <p>, <form>, or heading
                     tags AND has more than COGNITIVE_MIN_WORD_COUNT words
        at_parsing → always True (structural checks apply to all HTML)

    Parameters
    ----------
    disability_class : DisabilityClass
        The agent's domain. Accepted values: visual, auditory, motor,
        cognitive, at_parsing.
    dom_chunk : str
        Raw HTML string for a semantic page region.

    Returns
    -------
    bool
        True  → chunk is relevant; agent should proceed.
        False → chunk has no relevant content; skip this agent call.

    Examples
    --------
    >>> check_context_sufficiency(DisabilityClass.AUDITORY, '<p>Hello world</p>')
    False   # no audio/video/iframe — auditory agent has nothing to check

    >>> check_context_sufficiency(DisabilityClass.AUDITORY, '<video src="x.mp4"></video>')
    True

    >>> check_context_sufficiency(DisabilityClass.VISUAL, '<p>Any content</p>')
    True    # visual always True

    >>> check_context_sufficiency(DisabilityClass.COGNITIVE, '<p>Hi</p>')
    False   # has <p> but fewer than 50 words

    >>> check_context_sufficiency(
    ...     DisabilityClass.COGNITIVE,
    ...     '<p>' + 'word ' * 60 + '</p>'
    ... )
    True
    """
    # Normalise enum value — DisabilityClass uses use_enum_values=True so
    # the value may arrive as the string "visual" or the enum member itself
    dc_value = disability_class.value if hasattr(disability_class, "value") else str(disability_class)

    # ── visual: always relevant ────────────────────────────────────────────────
    if dc_value == DisabilityClass.VISUAL.value:
        return True

    # ── at_parsing: always relevant ────────────────────────────────────────────
    if dc_value == DisabilityClass.AT_PARSING.value:
        return True

    # For all other classes, we need to parse the HTML
    if not dom_chunk or not dom_chunk.strip():
        logger.debug(
            "check_context_sufficiency(%s): empty chunk → False", dc_value
        )
        return False

    try:
        soup = BeautifulSoup(dom_chunk, "lxml")
    except Exception as exc:
        logger.warning(
            "check_context_sufficiency(%s): parse error '%s' — defaulting to True",
            dc_value, exc,
        )
        # Fail open: if we can't parse, let the agent try rather than silently
        # dropping content that might contain real violations
        return True

    # ── auditory: needs time-based media ──────────────────────────────────────
    if dc_value == DisabilityClass.AUDITORY.value:
        has_media = bool(
            soup.find("audio")
            or soup.find("video")
            or soup.find("iframe")
            or soup.find("object")   # legacy media embed
            or soup.find("embed")    # legacy media embed
        )
        logger.debug(
            "check_context_sufficiency(auditory): has_media=%s", has_media
        )
        return has_media

    # ── motor: needs interactive elements ─────────────────────────────────────
    if dc_value == DisabilityClass.MOTOR.value:
        has_interactive = bool(
            soup.find("a")
            or soup.find("button")
            or soup.find("input")
            or soup.find("select")
            or soup.find("textarea")
            or soup.find(attrs={"tabindex": True})   # any element with tabindex
            or soup.find(attrs={"draggable": True})  # draggable elements
            or soup.find(attrs={"onclick": True})    # click handlers
        )
        logger.debug(
            "check_context_sufficiency(motor): has_interactive=%s", has_interactive
        )
        return has_interactive

    # ── cognitive: needs text content above word threshold ────────────────────
    if dc_value == DisabilityClass.COGNITIVE.value:
        has_text_structure = bool(
            soup.find("p")
            or soup.find("form")
            or soup.find(re.compile(r"^h[1-6]$"))   # any heading h1–h6
            or soup.find("ul")
            or soup.find("ol")
            or soup.find("table")
        )

        if not has_text_structure:
            logger.debug("check_context_sufficiency(cognitive): no text structure → False")
            return False

        # Count words in the chunk — cognitive checks need enough text to evaluate
        text = soup.get_text(separator=" ")
        word_count = len(re.findall(r"\b\w+\b", text))

        result = word_count > COGNITIVE_MIN_WORD_COUNT
        logger.debug(
            "check_context_sufficiency(cognitive): word_count=%d, threshold=%d → %s",
            word_count, COGNITIVE_MIN_WORD_COUNT, result,
        )
        return result

    # Unknown disability class — fail open (let agent decide)
    logger.warning(
        "check_context_sufficiency: unknown disability_class '%s' — defaulting to True",
        dc_value,
    )
    return True


# ══════════════════════════════════════════════════════════════════════════════
# GUARDRAIL 2 — Citation Enforcement
# ══════════════════════════════════════════════════════════════════════════════

def enforce_citation(critique_result: CritiqueResult) -> CritiqueResult:
    """
    Hard rule: a CONFIRMED verdict without a valid citation is overridden to REJECTED.

    This is the primary anti-hallucination guardrail for the audit pipeline.
    Design Doc Section 2.4.2 states:
        "If the critique agent cannot produce a verbatim WCAG criterion citation,
         the finding MUST be rejected regardless of how confident the audit agent was.
         No citation = no finding."

    This function is a THIRD enforcement layer (after the Pydantic model_validator
    in CritiqueResult and the check in critique_agent.py) — defence in depth
    ensures this rule cannot be bypassed regardless of how data enters the pipeline.

    When is this called?
        pipeline.py → run_critique node → after each CritiqueAgent.evaluate() call.

    Parameters
    ----------
    critique_result : CritiqueResult
        The CritiqueResult returned by CritiqueAgent.evaluate().
        May be a freshly instantiated model or one reconstructed from a dict.

    Returns
    -------
    CritiqueResult
        The same object if citation is valid.
        A new CritiqueResult with verdict=REJECTED if citation is missing/short.

    Examples
    --------
    >>> result = CritiqueResult(
    ...     finding_id="abc-123",
    ...     verdict=CritiqueVerdict.CONFIRMED,
    ...     citation="",   # empty — will be overridden
    ... )
    >>> enforced = enforce_citation(result)
    >>> enforced.verdict
    'REJECTED'

    >>> result2 = CritiqueResult(
    ...     finding_id="abc-123",
    ...     verdict=CritiqueVerdict.CONFIRMED,
    ...     citation="All non-text content has a text alternative that serves the equivalent purpose.",
    ... )
    >>> enforced2 = enforce_citation(result2)
    >>> enforced2.verdict
    'CONFIRMED'   # citation is valid — unchanged
    """
    # Get verdict as string — use_enum_values=True means it may be stored as str
    verdict = critique_result.verdict
    verdict_str = verdict.value if hasattr(verdict, "value") else str(verdict)

    # Only CONFIRMED verdicts require a citation — REJECTED and NEEDS_CONTEXT pass through
    if verdict_str != CritiqueVerdict.CONFIRMED.value:
        return critique_result

    # Check citation validity
    citation = critique_result.citation or ""
    citation_stripped = citation.strip()

    is_citation_valid = (
        bool(citation_stripped)
        and len(citation_stripped) >= MIN_CITATION_LENGTH
    )

    if is_citation_valid:
        logger.debug(
            "enforce_citation [%s]: citation valid (%d chars) → CONFIRMED unchanged",
            critique_result.finding_id[:8], len(citation_stripped),
        )
        return critique_result

    # Citation is missing or too short — override to REJECTED
    rejection_reason = (
        "No valid citation provided by critique agent. "
        f"Citation was: '{citation_stripped[:50]}' "
        f"({'empty' if not citation_stripped else f'{len(citation_stripped)} chars, minimum is {MIN_CITATION_LENGTH}'}). "
        "Design Doc Section 2.4.2: no citation = no confirmed finding."
    )

    logger.warning(
        "enforce_citation [%s]: CONFIRMED verdict has insufficient citation "
        "('%s', %d chars) → overriding to REJECTED",
        critique_result.finding_id[:8],
        citation_stripped[:30],
        len(citation_stripped),
    )

    # Return a new CritiqueResult with overridden verdict
    # We construct fresh to avoid mutating a shared object
    return CritiqueResult(
        finding_id=critique_result.finding_id,
        verdict=CritiqueVerdict.REJECTED,
        citation="",
        rejection_reason=rejection_reason,
        manual_review_instruction=None,
    )


# ══════════════════════════════════════════════════════════════════════════════
# GUARDRAIL 3 — Confidence Gate
# ══════════════════════════════════════════════════════════════════════════════

def apply_confidence_gate(finding: AgentFinding) -> Optional[AgentFinding]:
    """
    Apply the two-threshold confidence gate to a raw agent finding.

    Two thresholds (Design Doc Section 5.2.2):

    Threshold 1 — DROP (confidence < 0.50):
        Finding is too uncertain to be useful even for manual review.
        Return None — the caller must filter None values from the output list.
        Logged at DEBUG so the drop is traceable without being noisy.

    Threshold 2 — NEEDS_CONTEXT (0.50 ≤ confidence < 0.70):
        Finding is kept but flagged for human review instead of going
        through the critique agent. Sets:
            finding.needs_context_reason = 'Confidence below threshold...'
            finding.status = 'needs_context'
        Returns the modified finding.

    Threshold 3 — PASS (confidence ≥ 0.70):
        Finding is returned unchanged — it will proceed to the critique agent.

    NOTE on relation to AgentFinding Pydantic model_validator:
        The model_validator in schemas.py auto-sets needs_context_reason for
        confidence < 0.70 at construction time. This function is the EXPLICIT
        CALLABLE version — it handles findings that were constructed before the
        threshold was checked, or findings arriving as dicts reconstructed into
        models after the fact. Call this explicitly in pipeline.py for defence
        in depth; don't rely solely on the model_validator.

    Parameters
    ----------
    finding : AgentFinding
        A raw finding returned by a disability agent's audit() method.

    Returns
    -------
    AgentFinding or None
        None          → finding dropped (confidence < 0.50)
        AgentFinding  → finding with status/needs_context_reason set if needed

    Examples
    --------
    >>> f = AgentFinding(..., confidence=0.40, ...)
    >>> apply_confidence_gate(f)
    None   # dropped

    >>> f2 = AgentFinding(..., confidence=0.65, ...)
    >>> result = apply_confidence_gate(f2)
    >>> result.status
    'needs_context'
    >>> result.needs_context_reason
    'Confidence below threshold — requires manual verification'

    >>> f3 = AgentFinding(..., confidence=0.85, ...)
    >>> result3 = apply_confidence_gate(f3)
    >>> result3.status
    'pending'   # unchanged — high confidence proceeds to critique
    """
    confidence = finding.confidence

    # ── Threshold 1: DROP ─────────────────────────────────────────────────────
    if confidence < CONFIDENCE_DROP_THRESHOLD:
        logger.debug(
            "apply_confidence_gate: DROPPING finding [%s] "
            "(confidence=%.2f < drop_threshold=%.2f) | "
            "criterion=%s | selector=%s",
            finding.id[:8],
            confidence,
            CONFIDENCE_DROP_THRESHOLD,
            finding.criterion.criterion_number,
            finding.element_selector,
        )
        return None

    # ── Threshold 2: NEEDS_CONTEXT ────────────────────────────────────────────
    if confidence < CONFIDENCE_CONTEXT_THRESHOLD:
        # Use model_copy to avoid mutating the original object
        # (important if the same finding object is referenced elsewhere)
        updated = finding.model_copy(
            update={
                "needs_context_reason": (
                    "Confidence below threshold — requires manual verification. "
                    f"Agent confidence: {confidence:.2f} "
                    f"(threshold: {CONFIDENCE_CONTEXT_THRESHOLD}). "
                    f"Criterion: {finding.criterion.criterion_number}. "
                    "Verify this finding manually with a screen reader or AT device."
                ),
                "status": "needs_context",
            }
        )
        logger.debug(
            "apply_confidence_gate: NEEDS_CONTEXT for finding [%s] "
            "(confidence=%.2f, threshold=%.2f) | criterion=%s",
            finding.id[:8],
            confidence,
            CONFIDENCE_CONTEXT_THRESHOLD,
            finding.criterion.criterion_number,
        )
        return updated

    # ── Threshold 3: PASS ─────────────────────────────────────────────────────
    logger.debug(
        "apply_confidence_gate: PASS for finding [%s] "
        "(confidence=%.2f ≥ threshold=%.2f) | criterion=%s",
        finding.id[:8],
        confidence,
        CONFIDENCE_CONTEXT_THRESHOLD,
        finding.criterion.criterion_number,
    )
    return finding


# ══════════════════════════════════════════════════════════════════════════════
# BATCH HELPER — apply_confidence_gate to a full list
# ══════════════════════════════════════════════════════════════════════════════

def filter_findings_by_confidence(
    findings: list[AgentFinding],
) -> tuple[list[AgentFinding], int]:
    """
    Apply apply_confidence_gate to every finding in a list.

    Convenience wrapper used by pipeline.py's run_agents node so it
    doesn't have to handle None values inline.

    Parameters
    ----------
    findings : list[AgentFinding]
        Raw findings from one or more disability agents.

    Returns
    -------
    tuple[list[AgentFinding], int]
        (filtered_findings, dropped_count)
        filtered_findings : findings that passed the gate (None values removed)
        dropped_count     : number of findings dropped (confidence < 0.50)
    """
    results: list[AgentFinding] = []
    dropped = 0

    for f in findings:
        gated = apply_confidence_gate(f)
        if gated is None:
            dropped += 1
        else:
            results.append(gated)

    if dropped:
        logger.info(
            "filter_findings_by_confidence: dropped %d/%d findings below %.2f threshold",
            dropped, len(findings), CONFIDENCE_DROP_THRESHOLD,
        )

    return results, dropped
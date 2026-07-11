"""
backend/common/finding_adapter.py

Reconciles the two independently-designed "finding" shapes that the
fix_engine branch and the ai-agents-rag branch each built:

  - backend.agents.schemas.AgentFinding (+ CritiqueResult) -- nested
    `criterion` object, `needs_context_reason`, `status` in
    {pending, confirmed, needs_context, rejected}.

  - backend.fix_engine.patch_generator.FindingObject -- flat fields
    (criterion_number/criterion_level/criterion_text/legal_regulations
    at the top level), plus `critique_verdict`, `critique_citation`,
    `impact_score`, and `status` restricted to {confirmed, needs_context}.

These merged cleanly (no git conflict -- the files don't overlap), but
they disagree on the wire format for what is conceptually the same
object. Nothing calls this out until the orchestrator tries to wire
agents -> fix_engine, so it's a fill-in-the-gap step, not a bug in
either branch.

This adapter takes plain dicts (e.g. AgentFinding.model_dump() and
CritiqueResult.model_dump()) rather than importing the pydantic models
directly, so it has no pydantic dependency and can be unit tested
without pydantic installed.
"""

from __future__ import annotations

from backend.fix_engine.patch_generator import FindingObject

# FindingObject.status only accepts these two values. Per PipelineState
# (agents branch), only confirmed_findings and manual_review_findings are
# ever meant to reach the fix engine -- "pending" and "rejected" findings
# must never get this far. Treat that as an invariant, not an assumption.
_ALLOWED_STATUSES = {"confirmed", "needs_context"}


def agent_finding_to_finding_object(
    agent_finding: dict,
    critique_result: dict,
    *,
    impact_score: float,
) -> FindingObject:
    """Build a fix_engine FindingObject from an agents-branch AgentFinding
    dict + its corresponding CritiqueResult dict.

    Raises
    ------
    ValueError
        If `agent_finding["status"]` is not one of the two statuses the
        fix engine understands (this indicates a routing bug upstream --
        a pending/rejected finding should never have been passed here).
    KeyError
        If a required field is missing from either input dict.
    """
    status = agent_finding["status"]
    if status not in _ALLOWED_STATUSES:
        raise ValueError(
            f"agent_finding status={status!r} cannot be routed to the fix engine. "
            f"Only {_ALLOWED_STATUSES} are valid -- 'pending'/'rejected' findings "
            "must be filtered out before calling the fix engine (see PipelineState "
            "routing: only confirmed_findings / manual_review_findings should reach here)."
        )

    criterion = agent_finding["criterion"]
    criterion_level = criterion.get("criterion_level") or criterion.get("conformance_level")
    if not criterion_level:
        raise KeyError("criterion.criterion_level (or conformance_level) is required")

    return FindingObject(
        id=agent_finding["id"],
        disability_class=agent_finding["disability_class"],
        criterion_number=criterion["criterion_number"],
        criterion_level=criterion_level,
        criterion_text=criterion["criterion_text"],
        legal_regulations=criterion.get("legal_regulations", []),
        finding_description=agent_finding["finding_description"],
        disability_impact=agent_finding["disability_impact"],
        element_selector=agent_finding["element_selector"],
        confidence=agent_finding["confidence"],
        status=status,
        critique_verdict=critique_result["verdict"],
        critique_citation=critique_result.get("citation", ""),
        impact_score=impact_score,
    )

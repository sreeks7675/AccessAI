"""
Requires: pydantic (already a dependency of the ai-agents-rag branch).
Run with: pytest backend/agents/tests/test_schemas.py -v
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from backend.agents.schemas import (
    AgentFinding, CritiqueResult, CritiqueVerdict, ConfidenceLevel,
    DisabilityClass, WCAGCriterion,
)


def make_criterion(**overrides):
    base = dict(
        criterion_number="1.1.1",
        criterion_level="A",
        criterion_text="All non-text content has a text alternative.",
        legal_regulations=["ADA Title III"],
    )
    base.update(overrides)
    return WCAGCriterion(**base)


def make_finding(**overrides):
    base = dict(
        disability_class=DisabilityClass.VISUAL,
        criterion=make_criterion(),
        finding_description="img has no alt attribute on the banner image",
        disability_impact="JAWS user hears the raw filename instead of content",
        element_selector="img.banner",
        confidence=0.95,
    )
    base.update(overrides)
    return AgentFinding(**base)


class TestConfidenceGuardrail:
    def test_high_confidence_finding_stays_pending_untouched(self):
        f = make_finding(confidence=0.95)
        assert f.status == "pending"
        assert f.needs_context_reason is None

    def test_low_confidence_auto_flags_needs_context(self):
        f = make_finding(confidence=0.55)
        assert f.status == "needs_context"
        assert f.needs_context_reason is not None
        assert "0.55" in f.needs_context_reason

    def test_confidence_rounds_to_two_decimals(self):
        f = make_finding(confidence=0.9567)
        assert f.confidence == 0.96


class TestConfidenceLevelThresholds:
    @pytest.mark.parametrize("score,expected", [
        (0.90, ConfidenceLevel.HIGH),
        (0.85, ConfidenceLevel.HIGH),
        (0.84, ConfidenceLevel.MEDIUM),
        (0.70, ConfidenceLevel.MEDIUM),
        (0.69, ConfidenceLevel.LOW),
        (0.10, ConfidenceLevel.LOW),
    ])
    def test_boundaries(self, score, expected):
        assert ConfidenceLevel.from_score(score) == expected


class TestWCAGCriterionAliasAndParsing:
    def test_accepts_conformance_level_alias(self):
        c = WCAGCriterion(
            criterion_number="1.4.3", conformance_level="AA",
            criterion_text="Contrast ratio of at least 4.5:1 for normal text.",
        )
        assert c.criterion_level == "AA"

    def test_legal_regulations_accepts_comma_separated_string(self):
        c = make_criterion(legal_regulations="ADA Title III, Section 508")
        assert c.legal_regulations == ["ADA Title III", "Section 508"]

    def test_legal_regulations_accepts_json_list_string(self):
        c = make_criterion(legal_regulations='["ADA Title III", "Section 508"]')
        assert c.legal_regulations == ["ADA Title III", "Section 508"]


class TestCritiqueResultCitationEnforcement:
    def test_confirmed_with_short_citation_is_downgraded_to_rejected(self):
        result = CritiqueResult(finding_id="af-1", verdict=CritiqueVerdict.CONFIRMED, citation="too short")
        assert result.verdict == CritiqueVerdict.REJECTED
        assert result.rejection_reason is not None

    def test_confirmed_with_valid_citation_stays_confirmed(self):
        citation = "All non-text content has a text alternative that serves the equivalent purpose."
        result = CritiqueResult(finding_id="af-1", verdict=CritiqueVerdict.CONFIRMED, citation=citation)
        assert result.verdict == CritiqueVerdict.CONFIRMED

    def test_needs_context_without_instruction_gets_default(self):
        result = CritiqueResult(finding_id="af-1", verdict=CritiqueVerdict.NEEDS_CONTEXT)
        assert result.manual_review_instruction is not None
        assert "Manual review required" in result.manual_review_instruction


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

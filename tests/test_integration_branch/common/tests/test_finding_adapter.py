from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import unittest

from backend.common.finding_adapter import agent_finding_to_finding_object
from backend.fix_engine.patch_generator import PatchGenerator


def make_agent_finding_dict(**overrides) -> dict:
    """Shape matching AgentFinding.model_dump() from backend/agents/schemas.py
    (nested `criterion`, no critique_verdict/impact_score -- those live
    elsewhere in the pipeline)."""
    base = dict(
        id="af-1",
        disability_class="visual",
        criterion={
            "criterion_number": "1.1.1",
            "criterion_level": "A",
            "criterion_text": "All non-text content has a text alternative.",
            "legal_regulations": ["ADA Title III", "Section 508"],
        },
        finding_description="img.banner has no alt attribute",
        disability_impact="JAWS user hears the raw filename instead of content",
        element_selector="img.banner",
        confidence=0.95,
        needs_context_reason=None,
        status="confirmed",
    )
    base.update(overrides)
    return base


def make_critique_result_dict(**overrides) -> dict:
    base = dict(
        finding_id="af-1",
        verdict="CONFIRMED",
        citation="All non-text content has a text alternative that serves the equivalent purpose.",
        rejection_reason=None,
        manual_review_instruction=None,
    )
    base.update(overrides)
    return base


class TestAdapterBuildsValidFindingObject(unittest.TestCase):
    def test_adapter_produces_finding_object_the_fix_engine_can_consume(self):
        agent_finding = make_agent_finding_dict()
        critique = make_critique_result_dict()

        finding_object = agent_finding_to_finding_object(
            agent_finding, critique, impact_score=0.82,
        )

        self.assertEqual(finding_object.criterion_number, "1.1.1")
        self.assertEqual(finding_object.critique_verdict, "CONFIRMED")
        self.assertEqual(finding_object.impact_score, 0.82)

        # end-to-end: the adapted object must actually work with PatchGenerator,
        # proving the two branches are now interoperable, not just type-compatible.
        gen = PatchGenerator()
        result = gen.generate(finding_object, "<img src='hero.jpg'>")
        self.assertIn("alt=", gen.best_fix(result.candidate))

    def test_adapter_supports_conformance_level_alias(self):
        # the vector-store JSON shape uses `conformance_level`, not `criterion_level` --
        # WCAGCriterion accepts both via a pydantic alias, so the adapter must too.
        agent_finding = make_agent_finding_dict(
            criterion={
                "criterion_number": "1.4.3",
                "conformance_level": "AA",
                "criterion_text": "Contrast ratio of at least 4.5:1.",
                "legal_regulations": [],
            }
        )
        finding_object = agent_finding_to_finding_object(
            agent_finding, make_critique_result_dict(), impact_score=0.5,
        )
        self.assertEqual(finding_object.criterion_level, "AA")

    def test_adapter_rejects_pending_status(self):
        agent_finding = make_agent_finding_dict(status="pending")
        with self.assertRaises(ValueError):
            agent_finding_to_finding_object(
                agent_finding, make_critique_result_dict(), impact_score=0.5,
            )

    def test_adapter_rejects_rejected_status(self):
        agent_finding = make_agent_finding_dict(status="rejected")
        with self.assertRaises(ValueError):
            agent_finding_to_finding_object(
                agent_finding, make_critique_result_dict(verdict="REJECTED", citation=""),
                impact_score=0.5,
            )


if __name__ == "__main__":
    unittest.main()

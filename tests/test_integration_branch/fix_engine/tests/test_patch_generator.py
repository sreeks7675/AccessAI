from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import unittest

from backend.fix_engine.patch_generator import (
    FindingObject,
    PatchGenerator,
    MockLLMClient,
    UnsupportedViolationType,
)


def make_finding(**overrides) -> FindingObject:
    base = dict(
        id="f-1",
        disability_class="visual",
        criterion_number="1.1.1",
        criterion_level="A",
        criterion_text="text",
        legal_regulations=["ADA Title III"],
        finding_description="img has no alt text",
        disability_impact="screen reader user hears filename",
        element_selector="img.banner",
        confidence=0.9,
        status="confirmed",
        critique_verdict="CONFIRMED",
        critique_citation="verbatim wcag text over twenty chars long",
        impact_score=0.8,
    )
    base.update(overrides)
    return FindingObject(**base)


class TestInferViolationType(unittest.TestCase):
    def setUp(self):
        self.gen = PatchGenerator()

    def test_infers_from_criterion_number_map(self):
        f = make_finding(criterion_number="1.4.3", finding_description="irrelevant text")
        self.assertEqual(self.gen.infer_violation_type(f), "contrast_failure")

    def test_explicit_violation_type_wins_over_criterion(self):
        f = make_finding(criterion_number="1.1.1", violation_type="missing_label")
        self.assertEqual(self.gen.infer_violation_type(f), "missing_label")

    def test_falls_back_to_description_keywords(self):
        f = make_finding(criterion_number="9.9.9", finding_description="the image has no alt attribute")
        self.assertEqual(self.gen.infer_violation_type(f), "missing_alt_text")

    def test_raises_when_nothing_matches(self):
        f = make_finding(criterion_number="9.9.9", finding_description="totally unrelated issue")
        with self.assertRaises(UnsupportedViolationType):
            self.gen.infer_violation_type(f)


class TestMockLLMClient(unittest.TestCase):
    def test_missing_alt_text_produces_both_candidates(self):
        client = MockLLMClient()
        prompt = (
            "OUTPUT FORMAT: {...}\n"
            "FINDING_JSON:" + '{"violation_type":"missing_alt_text",'
            '"element_html":"<img src=\'a.jpg\'>","criterion_number":"1.1.1"}' + "\n---END---"
        )
        import json
        out = json.loads(client.complete("sys", prompt))
        self.assertIn("aria-hidden='true'", out["fixed_decorative"])
        self.assertIn("alt=", out["fixed_informational"])
        self.assertEqual(out["recommendation"], "informational")


class TestPatchGeneratorGenerate(unittest.TestCase):
    def setUp(self):
        self.gen = PatchGenerator()  # defaults to MockLLMClient

    def test_generate_missing_alt_text_end_to_end(self):
        finding = make_finding()
        result = self.gen.generate(finding, "<img src='hero.jpg'>")
        self.assertEqual(result.candidate.recommendation, "informational")
        best = self.gen.best_fix(result.candidate)
        self.assertIn("alt=", best)

    def test_generate_raises_for_unsupported_type(self):
        finding = make_finding(criterion_number="9.9.9", finding_description="totally unrelated")
        with self.assertRaises(UnsupportedViolationType):
            self.gen.generate(finding, "<div>x</div>")

    def test_best_fix_raises_if_recommended_candidate_missing(self):
        from backend.fix_engine.patch_generator import PatchCandidate
        candidate = PatchCandidate(
            original="<img>", fixed_decorative=None, fixed_informational=None,
            recommendation="decorative", wcag_criterion="1.1.1",
        )
        with self.assertRaises(ValueError):
            self.gen.best_fix(candidate)


class TestFindingObjectFromDict(unittest.TestCase):
    def test_from_dict_ignores_unknown_keys(self):
        data = dict(
            id="f-2", disability_class="visual", criterion_number="1.1.1",
            criterion_level="A", criterion_text="t", legal_regulations=[],
            finding_description="d", disability_impact="i", element_selector="img",
            confidence=0.9, status="confirmed", critique_verdict="CONFIRMED",
            critique_citation="x" * 25, impact_score=0.5,
            some_unrelated_field_from_agents_branch="should be dropped",
        )
        f = FindingObject.from_dict(data)
        self.assertEqual(f.id, "f-2")
        self.assertFalse(hasattr(f, "some_unrelated_field_from_agents_branch"))

    def test_from_dict_raises_if_required_field_missing(self):
        # this is the shape produced by the agents branch's AgentFinding.model_dump() —
        # no top-level criterion_number/criterion_level/criterion_text, no critique_verdict,
        # no impact_score. FindingObject.from_dict cannot construct a valid object from it.
        agent_style_dict = dict(
            id="f-3",
            disability_class="visual",
            criterion={"criterion_number": "1.1.1", "criterion_level": "A", "criterion_text": "t"},
            finding_description="d",
            disability_impact="i",
            element_selector="img",
            confidence=0.9,
            status="confirmed",
        )
        with self.assertRaises(TypeError):
            FindingObject.from_dict(agent_style_dict)


if __name__ == "__main__":
    unittest.main()

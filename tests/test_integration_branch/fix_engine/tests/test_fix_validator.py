from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import unittest

from backend.fix_engine.patch_generator import FindingObject, PatchGenerator, UnsupportedViolationType
from backend.fix_engine.fix_validator import FixValidator, MockAxeSidecarClient, AxeViolation


def make_finding(**overrides) -> FindingObject:
    base = dict(
        id="f-1", disability_class="visual", criterion_number="1.1.1", criterion_level="A",
        criterion_text="text", legal_regulations=["ADA Title III"],
        finding_description="img has no alt text", disability_impact="screen reader user hears filename",
        element_selector="img.banner", confidence=0.9, status="confirmed",
        critique_verdict="CONFIRMED", critique_citation="x" * 25, impact_score=0.8,
    )
    base.update(overrides)
    return FindingObject(**base)


class AlwaysFailingAxeClient:
    """Stub that never reports the patch as clean -- used to exercise the
    MAX_RETRIES exhaustion / requires_human_review path deterministically,
    without depending on the mock LLM eventually producing a clean fix."""

    def scan(self, html_snippet, *, target_selector=None):
        return [AxeViolation(rule_id="color-contrast", impact="serious",
                              description="still failing", target_selector=target_selector or "*")]


class TestFixValidatorHappyPath(unittest.TestCase):
    def test_validates_on_first_attempt(self):
        validator = FixValidator()  # defaults: PatchGenerator() + MockAxeSidecarClient()
        finding = make_finding()
        result = validator.validate_and_fix(finding, "<img src='hero.jpg'>")
        self.assertTrue(result.patch_validated)
        self.assertFalse(result.requires_human_review)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.remaining_violations, [])
        self.assertIn("alt=", result.patch_html)


class TestFixValidatorRetryExhaustion(unittest.TestCase):
    def test_requires_human_review_after_max_retries(self):
        validator = FixValidator(axe_client=AlwaysFailingAxeClient())
        finding = make_finding()
        result = validator.validate_and_fix(finding, "<img src='hero.jpg'>")
        self.assertFalse(result.patch_validated)
        self.assertTrue(result.requires_human_review)
        # MAX_RETRIES = 2 -> initial attempt + 2 retries = 3 total attempts
        self.assertEqual(result.attempts, 3)
        self.assertIn("did not pass axe-core validation", result.review_reason)
        self.assertTrue(len(result.remaining_violations) >= 1)


class TestFixValidatorUnsupportedType(unittest.TestCase):
    def test_unsupported_violation_type_routes_to_human_review_without_raising(self):
        validator = FixValidator()
        finding = make_finding(criterion_number="9.9.9", finding_description="totally unrelated issue")
        result = validator.validate_and_fix(finding, "<div>x</div>")
        self.assertFalse(result.patch_validated)
        self.assertTrue(result.requires_human_review)
        self.assertIn("not yet supported", result.review_reason)
        self.assertEqual(result.attempts, 1)


class TestToFindingFix(unittest.TestCase):
    def test_wires_validation_result_into_finding_fix(self):
        validator = FixValidator()
        finding = make_finding()
        validation = validator.validate_and_fix(finding, "<img src='hero.jpg'>")
        fix = validator.to_finding_fix(validation, diff_html="<diff/>", preview_srcdoc="<html/>")
        d = fix.to_dict()
        self.assertEqual(d["patch_html"], validation.patch_html)
        self.assertEqual(d["patch_validated"], True)
        self.assertEqual(d["diff_html"], "<diff/>")
        self.assertEqual(d["preview_srcdoc"], "<html/>")
        self.assertFalse(d["requires_human_review"])


if __name__ == "__main__":
    unittest.main()

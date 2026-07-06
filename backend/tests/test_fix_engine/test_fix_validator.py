"""
tests/test_fix_engine/test_fix_validator.py

"""

from backend.fix_engine.patch_generator import FindingObject
from backend.fix_engine.fix_validator import FixValidator, MAX_RETRIES, AxeViolation


def make_finding(**overrides) -> FindingObject:
    base = dict(
        id="f-002",
        disability_class="visual",
        criterion_number="1.1.1",
        criterion_level="A",
        criterion_text="Non-text Content",
        legal_regulations=["ADA Title III"],
        finding_description="img element is missing alt text",
        disability_impact="Screen reader users cannot determine the image's purpose",
        element_selector="img.hero-banner",
        confidence=0.95,
        status="confirmed",
        critique_verdict="CONFIRMED",
        critique_citation="Non-text Content",
        impact_score=78,
        violation_type="missing_alt_text",
    )
    base.update(overrides)
    return FindingObject(**base)


class AlwaysPassesAxeClient:
    """Every scan returns zero violations -- simulates a fix that passes
    immediately on attempt 1."""

    def scan(self, html_snippet, *, target_selector=None):
        return []


class AlwaysFailsAxeClient:
    """Every scan returns a violation -- simulates a fix that never passes,
    to exercise the retry-exhaustion -> human-review path."""

    call_count = 0

    def scan(self, html_snippet, *, target_selector=None):
        self.call_count += 1
        return [
            AxeViolation(
                rule_id="image-alt",
                impact="critical",
                description="Images must have alternate text",
                target_selector=target_selector or "img",
            )
        ]


def test_validation_passes_on_first_attempt():
    validator = FixValidator(axe_client=AlwaysPassesAxeClient())
    finding = make_finding()

    result = validator.validate_and_fix(finding, "<img src='hero.jpg' class='banner'>")

    assert result.patch_validated is True
    assert result.requires_human_review is False
    assert result.attempts == 1


def test_validation_exhausts_retries_and_flags_human_review():
    axe_client = AlwaysFailsAxeClient()
    validator = FixValidator(axe_client=axe_client)
    finding = make_finding()

    result = validator.validate_and_fix(finding, "<img src='hero.jpg' class='banner'>")

    assert result.patch_validated is False
    assert result.requires_human_review is True
    assert result.review_reason is not None
    # initial attempt + MAX_RETRIES retries, never more
    assert result.attempts == MAX_RETRIES + 1
    assert axe_client.call_count == MAX_RETRIES + 1


def test_unsupported_violation_type_goes_straight_to_human_review():
    validator = FixValidator(axe_client=AlwaysPassesAxeClient())
    finding = make_finding(violation_type="keyboard_trap", criterion_number="2.1.1")

    result = validator.validate_and_fix(finding, "<div tabindex='0'>...</div>")

    assert result.requires_human_review is True
    assert result.patch_validated is False
    assert "not yet supported" in result.review_reason


def test_to_finding_fix_wires_diff_and_preview_through():
    validator = FixValidator(axe_client=AlwaysPassesAxeClient())
    finding = make_finding()
    result = validator.validate_and_fix(finding, "<img src='hero.jpg' class='banner'>")

    fix = validator.to_finding_fix(result, diff_html="--- a\n+++ b", preview_srcdoc="<html></html>")

    assert fix.patch_validated is True
    assert fix.diff_html == "--- a\n+++ b"
    assert fix.preview_srcdoc == "<html></html>"
    assert fix.to_dict()["patch_html"] == result.patch_html

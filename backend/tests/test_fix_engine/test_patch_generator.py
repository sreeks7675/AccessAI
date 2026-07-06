"""
tests/test_fix_engine/test_patch_generator.py

"""

import pytest

from backend.fix_engine.patch_generator import (
    FindingObject,
    PatchGenerator,
    UnsupportedViolationType,
)


def make_finding(**overrides) -> FindingObject:
    base = dict(
        id="f-001",
        disability_class="visual",
        criterion_number="1.1.1",
        criterion_level="A",
        criterion_text="Non-text Content: All non-text content ... has a text alternative",
        legal_regulations=["ADA Title III"],
        finding_description="img element is missing alt text",
        disability_impact="Screen reader users cannot determine the image's purpose",
        element_selector="img.hero-banner",
        confidence=0.95,
        status="confirmed",
        critique_verdict="CONFIRMED",
        critique_citation="Non-text Content: All non-text content ... has a text alternative",
        impact_score=78,
    )
    base.update(overrides)
    return FindingObject(**base)


def test_missing_alt_text_produces_both_candidates():
    finding = make_finding(violation_type="missing_alt_text")
    gen = PatchGenerator()  # defaults to MockLLMClient

    result = gen.generate(finding, element_html="<img src='hero.jpg' class='banner'>")

    assert result.candidate.fixed_decorative is not None
    assert result.candidate.fixed_informational is not None
    assert "alt=" in result.candidate.fixed_decorative
    assert "alt=" in result.candidate.fixed_informational
    # Ambiguous-case guardrail: never silently pick just one.
    assert result.candidate.recommendation in ("decorative", "informational")


def test_contrast_failure_produces_a_fix():
    finding = make_finding(
        criterion_number="1.4.3",
        finding_description="text has insufficient color contrast",
        violation_type="contrast_failure",
    )
    gen = PatchGenerator()

    result = gen.generate(
        finding,
        element_html="<p style='color:#999999;background-color:#ffffff;'>Optional field</p>",
    )

    assert result.candidate.fixed_informational is not None
    assert "#999999" not in result.candidate.fixed_informational


def test_missing_label_adds_aria_label():
    finding = make_finding(
        criterion_number="4.1.2",
        finding_description="input is missing an accessible label",
        violation_type="missing_label",
    )
    gen = PatchGenerator()

    result = gen.generate(
        finding, element_html="<input type='email' name='email' placeholder='Email address'>"
    )

    assert "aria-label" in result.candidate.fixed_informational


def test_unsupported_violation_type_raises():
    finding = make_finding(
        criterion_number="2.1.1",
        finding_description="keyboard trap detected",
        violation_type="keyboard_trap",
    )
    gen = PatchGenerator()

    with pytest.raises(UnsupportedViolationType):
        gen.generate(finding, element_html="<div tabindex='0'>...</div>")


def test_infers_violation_type_when_not_provided():
    # Simulates a real IP-2 payload that doesn't carry violation_type at all
    # (see the note in patch_generator.py about this field being Charan's
    # own addition, not guaranteed by the real contract).
    finding = make_finding(violation_type=None, criterion_number="1.1.1")
    gen = PatchGenerator()

    inferred = gen.infer_violation_type(finding)
    assert inferred == "missing_alt_text"


def test_best_fix_respects_recommendation():
    finding = make_finding(violation_type="missing_alt_text")
    gen = PatchGenerator()
    result = gen.generate(finding, element_html="<img src='hero.jpg'>")

    chosen = gen.best_fix(result.candidate)
    if result.candidate.recommendation == "informational":
        assert chosen == result.candidate.fixed_informational
    else:
        assert chosen == result.candidate.fixed_decorative

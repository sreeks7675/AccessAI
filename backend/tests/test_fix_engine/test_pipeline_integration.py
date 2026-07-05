"""
tests/test_fix_engine/test_pipeline_integration.py

"""

from backend.fix_engine import FixEnginePipeline, FindingObject
from backend.fix_engine.fix_validator import FixValidator
from backend.fix_engine.patch_generator import PatchGenerator


class AlwaysPassesAxeClient:
    def scan(self, html_snippet, *, target_selector=None):
        return []


def test_full_pipeline_produces_ip3_compatible_fix_object():
    finding = FindingObject(
        id="f-100",
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

    patch_gen = PatchGenerator()
    validator = FixValidator(patch_generator=patch_gen, axe_client=AlwaysPassesAxeClient())
    pipeline = FixEnginePipeline(patch_generator=patch_gen, fix_validator=validator)

    fix = pipeline.run(
        finding,
        original_element_html="<img src='hero.jpg' class='banner'>",
        page_computed_css="body { font-family: sans-serif; }",
    )

    # Exactly the shape Mahesh's Report JSON (IP-3) expects for `finding.fix`.
    fix_dict = fix.to_dict()
    assert set(fix_dict.keys()) == {
        "patch_html",
        "patch_validated",
        "diff_html",
        "preview_srcdoc",
        "requires_human_review",
        "review_reason",
    }
    assert fix_dict["patch_validated"] is True
    assert "alt=" in fix_dict["patch_html"]
    assert "wcag-fix-highlight" in fix_dict["preview_srcdoc"]
    assert "+++ fixed.html" in fix_dict["diff_html"]

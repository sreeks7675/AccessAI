"""
backend/fix_engine/__init__.py

"""

from .patch_generator import (
    FindingObject,
    PatchCandidate,
    PatchGenerator,
    UnsupportedViolationType,
    SUPPORTED_VIOLATION_TYPES,
)
from .fix_validator import FixValidator, ValidationResult, FindingFix
from .diff_engine import HTMLDiffer, PreviewBuilder


class FixEnginePipeline:
    """The single call Mahesh's orchestrator makes per confirmed finding
    (Design Doc 2.3.1, Step 6: Fix Generation). Wires patch_generator ->
    fix_validator -> diff_engine together and returns a ready-to-embed
    FindingFix object for the Report JSON (IP-3)."""

    def __init__(
        self,
        patch_generator: PatchGenerator | None = None,
        fix_validator: FixValidator | None = None,
        differ: HTMLDiffer | None = None,
        preview_builder: PreviewBuilder | None = None,
    ):
        self.patch_generator = patch_generator or PatchGenerator()
        self.fix_validator = fix_validator or FixValidator(patch_generator=self.patch_generator)
        self.differ = differ or HTMLDiffer()
        self.preview_builder = preview_builder or PreviewBuilder()

    def run(self, finding: FindingObject, original_element_html: str, *, page_computed_css: str = "") -> FindingFix:
        validation = self.fix_validator.validate_and_fix(finding, original_element_html)

        diff_html = self.differ.to_unified_diff(original_element_html, validation.patch_html)
        preview_srcdoc = self.preview_builder.build_srcdoc(
            patched_element_html=validation.patch_html,
            page_computed_css=page_computed_css,
            element_selector=finding.element_selector,
        )

        return self.fix_validator.to_finding_fix(validation, diff_html, preview_srcdoc)


__all__ = [
    "FixEnginePipeline",
    "FindingObject",
    "PatchCandidate",
    "PatchGenerator",
    "UnsupportedViolationType",
    "SUPPORTED_VIOLATION_TYPES",
    "FixValidator",
    "ValidationResult",
    "FindingFix",
    "HTMLDiffer",
    "PreviewBuilder",
]

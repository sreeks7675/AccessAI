"""
backend/fix_engine/fix_validator.py

"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from .patch_generator import FindingObject, PatchGenerator, UnsupportedViolationType

logger = logging.getLogger(__name__)

MAX_RETRIES = 2  # Design Doc 4.2.3 -- hard cap, not a suggestion


# =============================================================================
# 1. axe-core client (shared sidecar decided with Mahesh on Day 1)
# =============================================================================

@dataclass
class AxeViolation:
    rule_id: str
    impact: str  # "minor" | "moderate" | "serious" | "critical"
    description: str
    target_selector: str


class AxeSidecarError(Exception):
    pass


class AxeSidecarClient:
    """HTTP client for the shared axe-core/Playwright sidecar. Mahesh's
    orchestrator (Step 2: rule-based pre-scan) and this validator both call
    the same `/scan` endpoint -- don't build two separate axe-core
    integrations. Kept dependency-light (`requests` imported lazily) so
    this module can be imported/unit-tested without Docker/Playwright
    installed locally."""

    def __init__(self, base_url: str | None = None, timeout: float = 30.0):
        self.base_url = (base_url or os.environ.get("AXE_SIDECAR_URL", "http://localhost:8090")).rstrip("/")
        self.timeout = timeout

    def scan(self, html_snippet: str, *, target_selector: str | None = None) -> list[AxeViolation]:
        """Runs axe-core against `html_snippet`, scoped to `target_selector`
        when given -- re-scanning the whole page per retry would be far too
        slow for a fix-validation loop, so always scope to the patched
        element when you have a selector."""
        import requests

        try:
            resp = requests.post(
                f"{self.base_url}/scan",
                json={"html": html_snippet, "target_selector": target_selector},
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise AxeSidecarError(f"axe-core sidecar call failed: {e}") from e

        data = resp.json()
        return [
            AxeViolation(
                rule_id=v["rule_id"],
                impact=v["impact"],
                description=v["description"],
                target_selector=v["target_selector"],
            )
            for v in data.get("violations", [])
        ]


class MockAxeSidecarClient:
    """Deterministic stand-in for local testing / before the Docker sidecar
    is running. Applies simple rule checks directly in Python instead of
    calling out to axe-core, so the retry loop below can be tested without
    Docker/Playwright installed."""

    def scan(self, html_snippet: str, *, target_selector: str | None = None) -> list[AxeViolation]:
        violations: list[AxeViolation] = []

        if "<img" in html_snippet and "alt=" not in html_snippet:
            violations.append(
                AxeViolation(
                    rule_id="image-alt", impact="critical",
                    description="Images must have alternate text",
                    target_selector=target_selector or "img",
                )
            )

        if "<input" in html_snippet and "aria-label" not in html_snippet and "<label" not in html_snippet:
            violations.append(
                AxeViolation(
                    rule_id="label", impact="critical",
                    description="Form elements must have labels",
                    target_selector=target_selector or "input",
                )
            )

        # Rough contrast heuristic for the mock only -- real contrast math
        # happens in the real axe-core engine, not here.
        if "#999999" in html_snippet or "#7fbfff" in html_snippet:
            violations.append(
                AxeViolation(
                    rule_id="color-contrast", impact="serious",
                    description="Elements must meet minimum color contrast ratio thresholds",
                    target_selector=target_selector or "*",
                )
            )

        return violations


# =============================================================================
# 2. FindingFix (part of IP-3, Contract 3 -- what this module produces)
# =============================================================================

@dataclass
class FindingFix:
    """Mirrors the `finding.fix` object appended to Mahesh's Report JSON
    (IP-3, Contract 3)."""

    patch_html: str
    patch_validated: bool
    diff_html: str
    preview_srcdoc: str
    requires_human_review: bool = False
    review_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "patch_html": self.patch_html,
            "patch_validated": self.patch_validated,
            "diff_html": self.diff_html,
            "preview_srcdoc": self.preview_srcdoc,
            "requires_human_review": self.requires_human_review,
            "review_reason": self.review_reason,
        }


# =============================================================================
# 3. FixValidator
# =============================================================================

@dataclass
class ValidationResult:
    patch_html: str
    patch_validated: bool
    requires_human_review: bool
    review_reason: Optional[str]
    attempts: int
    remaining_violations: list[AxeViolation]


class FixValidator:
    """Wraps PatchGenerator with the axe-core re-validation retry loop."""

    def __init__(
        self,
        patch_generator: PatchGenerator | None = None,
        axe_client: AxeSidecarClient | MockAxeSidecarClient | None = None,
    ):
        self.patch_generator = patch_generator or PatchGenerator()
        # Defaults to the mock so this is testable without Docker/Playwright
        # running. Swap to AxeSidecarClient() once the shared sidecar
        # (coordinated with Mahesh on Day 1) is up.
        self.axe_client = axe_client or MockAxeSidecarClient()

    def validate_and_fix(self, finding: FindingObject, element_html: str) -> ValidationResult:
        """Generates a patch, checks it against axe-core, and retries up to
        MAX_RETRIES times if the violation persists. Falls back to
        requires_human_review=True rather than looping forever or silently
        returning a still-broken patch."""
        retry_error_context: str | None = None
        last_candidate_html = element_html
        last_violations: list[AxeViolation] = []
        attempts_used = 0

        for attempt in range(1, MAX_RETRIES + 2):  # initial try + MAX_RETRIES retries
            attempts_used = attempt
            try:
                result = self.patch_generator.generate(
                    finding, element_html, retry_error_context=retry_error_context
                )
            except UnsupportedViolationType as e:
                logger.warning("[WS-4] Unsupported violation type for finding=%s: %s", finding.id, e)
                return ValidationResult(
                    patch_html=element_html,
                    patch_validated=False,
                    requires_human_review=True,
                    review_reason=f"Violation type not yet supported by fix engine: {e}",
                    attempts=attempts_used,
                    remaining_violations=[],
                )

            candidate_html = self.patch_generator.best_fix(result.candidate)
            last_candidate_html = candidate_html

            violations = self.axe_client.scan(candidate_html, target_selector=finding.element_selector)
            last_violations = violations

            if not violations:
                logger.info(
                    "[WS-4] Patch validated for finding=%s on attempt %d/%d",
                    finding.id, attempt, MAX_RETRIES + 1,
                )
                return ValidationResult(
                    patch_html=candidate_html,
                    patch_validated=True,
                    requires_human_review=False,
                    review_reason=None,
                    attempts=attempts_used,
                    remaining_violations=[],
                )

            retry_error_context = "; ".join(
                f"{v.rule_id} ({v.impact}): {v.description}" for v in violations
            )
            logger.info(
                "[WS-4] Patch attempt %d/%d for finding=%s still failed axe-core: %s",
                attempt, MAX_RETRIES + 1, finding.id, retry_error_context,
            )

            if attempt == MAX_RETRIES + 1:
                break

        return ValidationResult(
            patch_html=last_candidate_html,
            patch_validated=False,
            requires_human_review=True,
            review_reason=(
                f"Automated fix did not pass axe-core validation after "
                f"{attempts_used} attempts. Remaining violations: {retry_error_context}"
            ),
            attempts=attempts_used,
            remaining_violations=last_violations,
        )

    def to_finding_fix(self, validation: ValidationResult, diff_html: str, preview_srcdoc: str) -> FindingFix:
        """Assembles the final `finding.fix` object for IP-3. diff_html and
        preview_srcdoc come from diff_engine.py -- this just wires
        everything into the shape Mahesh's report assembly expects."""
        return FindingFix(
            patch_html=validation.patch_html,
            patch_validated=validation.patch_validated,
            diff_html=diff_html,
            preview_srcdoc=preview_srcdoc,
            requires_human_review=validation.requires_human_review,
            review_reason=validation.review_reason,
        )

"""
backend/fix_engine/patch_generator.py

"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# 1. Types (local mirror of IP-2 / part of IP-3)
# =============================================================================

DisabilityClass = Literal["visual", "auditory", "motor", "cognitive", "at_parsing"]
FindingStatus = Literal["confirmed", "needs_context"]
CritiqueVerdict = Literal["CONFIRMED", "REJECTED", "NEEDS_CONTEXT"]

# The 3 violation types supported as of Day 3. Expand as coverage grows --
# don't silently accept types outside this set without adding a few-shot
# example bank for them first (see Section 3 below).
SUPPORTED_VIOLATION_TYPES = (
    "missing_alt_text",
    "contrast_failure",
    "missing_label",
)


@dataclass
class FindingObject:
    """Mirrors IP-2 (Contract 2). This is what the fix engine RECEIVES."""

    id: str
    disability_class: DisabilityClass
    criterion_number: str
    criterion_level: Literal["A", "AA", "AAA"]
    criterion_text: str
    legal_regulations: list[str]
    finding_description: str
    disability_impact: str
    element_selector: str
    confidence: float
    status: FindingStatus
    critique_verdict: CritiqueVerdict
    critique_citation: str
    impact_score: float

    # Not guaranteed by the strict IP-2 schema. If missing, PatchGenerator
    # infers it from criterion_number / finding_description (see
    # infer_violation_type below).
    violation_type: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "FindingObject":
        known_fields = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known_fields})


@dataclass
class PatchCandidate:
    """Output of patch generation -- Design Doc Section 4.2.2 output format."""

    original: str
    fixed_decorative: Optional[str]
    fixed_informational: Optional[str]
    recommendation: Literal["decorative", "informational", "n/a"]
    wcag_criterion: str

    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "fixed_decorative": self.fixed_decorative,
            "fixed_informational": self.fixed_informational,
            "recommendation": self.recommendation,
            "wcag_criterion": self.wcag_criterion,
        }


class UnsupportedViolationType(Exception):
    """Raised when a finding's violation_type isn't one of the 3 (or later,
    more) types this module has a few-shot prompt for. Deliberately a hard
    error, not a silent no-op, so gaps in coverage show up in tests/logs
    instead of being quietly dropped."""


# =============================================================================
# 2. LLM client (pluggable: real cluster vs. deterministic mock)
# =============================================================================

class LLMClient(ABC):
    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return the raw text completion. Callers parse JSON out of it."""
        raise NotImplementedError


class VLLMClient(LLMClient):
    """Talks to an OpenAI-compatible /v1/chat/completions endpoint, which is
    what vLLM exposes. Reads VLLM_ENDPOINT from the environment (set up by
    Mahesh's .env.template on Day 1)."""

    def __init__(self, endpoint: str | None = None, model: str = "mistral-7b-instruct"):
        self.endpoint = endpoint or os.environ.get("VLLM_ENDPOINT", "http://localhost:8000")
        self.model = model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        import requests  # local import: keep this dependency out of the hot
        # path for anyone running only the mock client / tests.

        resp = requests.post(
            f"{self.endpoint.rstrip('/')}/v1/chat/completions",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                # Structured output enforcement (Design Doc 5.2.2): vLLM +
                # outlines supports guided JSON decoding.
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


class MockLLMClient(LLMClient):
    """Deterministic, rule-based stand-in for the real model. Used for local
    development and tests before the cluster endpoint exists -- applies
    simple, explainable rules per violation type so the rest of the
    pipeline (retry handling, ambiguous-case branching, JSON parsing) is
    fully testable without model-output variance."""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        marker = "FINDING_JSON:"
        idx = user_prompt.find(marker)
        if idx == -1:
            raise ValueError("MockLLMClient expects a FINDING_JSON: block in the prompt")
        payload = json.loads(user_prompt[idx + len(marker):].strip().split("\n---END---")[0])

        violation_type = payload["violation_type"]
        element_html = payload["element_html"]
        criterion = payload.get("criterion_number", "")

        if violation_type == "missing_alt_text":
            decorative = element_html.replace("<img", "<img alt='' aria-hidden='true'", 1)
            informational = element_html.replace(
                "<img", "<img alt='Descriptive text inferred from surrounding context'", 1
            )
            return json.dumps(
                {
                    "original": element_html,
                    "fixed_decorative": decorative,
                    "fixed_informational": informational,
                    "recommendation": "informational",
                    "wcag_criterion": criterion or "1.1.1",
                }
            )

        if violation_type == "contrast_failure":
            return json.dumps(
                {
                    "original": element_html,
                    "fixed_decorative": None,
                    "fixed_informational": self._bump_contrast(element_html),
                    "recommendation": "informational",
                    "wcag_criterion": criterion or "1.4.3",
                }
            )

        if violation_type == "missing_label":
            return json.dumps(
                {
                    "original": element_html,
                    "fixed_decorative": None,
                    "fixed_informational": self._add_label(element_html),
                    "recommendation": "informational",
                    "wcag_criterion": criterion or "4.1.2",
                }
            )

        raise ValueError(f"MockLLMClient has no rule for violation_type={violation_type!r}")

    @staticmethod
    def _bump_contrast(element_html: str) -> str:
        # Toy heuristic: replace any known-failing colour literal with a
        # known-AA-passing one. A real model call computes this from the
        # actual background colour; this mock only exercises the pipeline
        # around it, not real contrast math.
        known_failing = {"#999999": "#1a1a1a", "#7fbfff": "#0b62c4"}
        fixed = element_html
        for bad, good in known_failing.items():
            fixed = fixed.replace(bad, good)
        if fixed == element_html:
            if "style='" in fixed:
                fixed = fixed.replace("style='", "style='color:#1a1a1a;background-color:#ffffff;", 1)
            else:
                fixed = fixed.replace(">", " style='color:#1a1a1a;background-color:#ffffff;'>", 1)
        return fixed

    @staticmethod
    def _add_label(element_html: str) -> str:
        if "aria-label" in element_html or "<label" in element_html:
            return element_html
        return element_html.replace(
            "<input", "<input aria-label='Descriptive field label inferred from context'", 1
        )


# =============================================================================
# 3. Few-shot prompts (Design Doc 4.2.2 -- few-shot beats zero-shot here)
# =============================================================================

SYSTEM_PROMPT = """You are the Fix Engine of a WCAG accessibility audit tool.
You receive a confirmed WCAG violation and the exact HTML element involved.
Your job is to produce a corrected HTML snippet.

Rules you must always follow:
1. Never change the semantic meaning of the content.
2. For any case where intent is ambiguous (e.g. is an image decorative or
   informational?), output BOTH candidate fixes plus a recommendation.
   Never silently pick one -- a human must choose. This is a deliberate
   guardrail, not optional.
3. Always respond with a single JSON object matching the OUTPUT FORMAT
   shown in the examples. No prose outside the JSON.
"""

_EXAMPLE_BANKS: dict[str, list[dict]] = {
    "missing_alt_text": [
        {
            "element": "<img src='hero.jpg' class='banner'>",
            "context": "Image appears adjacent to heading 'Our Story' -- likely decorative OR informational",
            "output": {
                "original": "<img src='hero.jpg' class='banner'>",
                "fixed_decorative": "<img src='hero.jpg' class='banner' alt='' aria-hidden='true'>",
                "fixed_informational": "<img src='hero.jpg' class='banner' alt=\"Team members collaborating in the founder's original office\">",
                "recommendation": "informational",
                "wcag_criterion": "1.1.1",
            },
        },
        {
            "element": "<img src='divider-line.png'>",
            "context": "Thin horizontal line used purely as a visual section divider, no surrounding text references it",
            "output": {
                "original": "<img src='divider-line.png'>",
                "fixed_decorative": "<img src='divider-line.png' alt='' aria-hidden='true'>",
                "fixed_informational": "<img src='divider-line.png' alt='Section divider'>",
                "recommendation": "decorative",
                "wcag_criterion": "1.1.1",
            },
        },
        {
            "element": "<img src='chart-q3-revenue.png' class='report-figure'>",
            "context": "Image embedded inside a financial report section, no caption text nearby",
            "output": {
                "original": "<img src='chart-q3-revenue.png' class='report-figure'>",
                "fixed_decorative": "<img src='chart-q3-revenue.png' class='report-figure' alt='' aria-hidden='true'>",
                "fixed_informational": "<img src='chart-q3-revenue.png' class='report-figure' alt='Bar chart of Q3 revenue by region -- see accompanying data table for exact figures'>",
                "recommendation": "informational",
                "wcag_criterion": "1.1.1",
            },
        },
    ],
    "contrast_failure": [
        {
            "element": "<p style='color:#999999;background-color:#ffffff;'>Optional field</p>",
            "context": "Body text, computed contrast ratio 2.85:1, fails WCAG AA (needs 4.5:1 for normal text)",
            "output": {
                "original": "<p style='color:#999999;background-color:#ffffff;'>Optional field</p>",
                "fixed_decorative": None,
                "fixed_informational": "<p style='color:#595959;background-color:#ffffff;'>Optional field</p>",
                "recommendation": "informational",
                "wcag_criterion": "1.4.3",
            },
        },
        {
            "element": "<button style='color:#ffffff;background-color:#7fbfff;'>Submit</button>",
            "context": "Button text, computed contrast ratio 1.9:1, fails WCAG AA for UI text",
            "output": {
                "original": "<button style='color:#ffffff;background-color:#7fbfff;'>Submit</button>",
                "fixed_decorative": None,
                "fixed_informational": "<button style='color:#ffffff;background-color:#0b62c4;'>Submit</button>",
                "recommendation": "informational",
                "wcag_criterion": "1.4.3",
            },
        },
    ],
    "missing_label": [
        {
            "element": "<input type='email' name='email' placeholder='Email address'>",
            "context": "Placeholder used as the only label, disappears once user types -- not a substitute for a real label",
            "output": {
                "original": "<input type='email' name='email' placeholder='Email address'>",
                "fixed_decorative": None,
                "fixed_informational": "<input type='email' name='email' placeholder='Email address' aria-label='Email address'>",
                "recommendation": "informational",
                "wcag_criterion": "4.1.2",
            },
        },
        {
            "element": "<input type='search' id='site-search'>",
            "context": "Icon-only search button nearby, no visible text label for the input itself",
            "output": {
                "original": "<input type='search' id='site-search'>",
                "fixed_decorative": None,
                "fixed_informational": "<input type='search' id='site-search' aria-label='Search this site'>",
                "recommendation": "informational",
                "wcag_criterion": "4.1.2",
            },
        },
    ],
}


def _build_user_prompt(
    *,
    violation_type: str,
    element_html: str,
    context_description: str,
    criterion_number: str,
    retry_error_context: str | None = None,
) -> str:
    """Assembles the full few-shot prompt for one fix-generation call.
    `retry_error_context` is populated on retries (Design Doc 4.2.3): when
    axe-core still fails on a patched snippet, the error is fed back in."""
    if violation_type not in _EXAMPLE_BANKS:
        raise ValueError(
            f"No few-shot examples for violation_type={violation_type!r}. "
            f"Supported: {list(_EXAMPLE_BANKS)}"
        )

    example_blocks = []
    for i, ex in enumerate(_EXAMPLE_BANKS[violation_type], start=1):
        example_blocks.append(
            f"EXAMPLE {i}\n"
            f"VIOLATION_TYPE: {violation_type}\n"
            f"ELEMENT: {ex['element']}\n"
            f"CONTEXT: {ex['context']}\n"
            f"OUTPUT: {json.dumps(ex['output'])}\n"
        )
    examples_block = "\n".join(example_blocks)

    retry_block = ""
    if retry_error_context:
        retry_block = (
            "\nNOTE: A previous attempt at this fix was rejected because "
            f"axe-core still reported a violation after patching:\n{retry_error_context}\n"
            "Produce a different fix that actually resolves this.\n"
        )

    payload = {
        "violation_type": violation_type,
        "element_html": element_html,
        "criterion_number": criterion_number,
    }

    return (
        f"{examples_block}\n"
        "Now fix the following real case.\n"
        f"VIOLATION_TYPE: {violation_type}\n"
        f"ELEMENT: {element_html}\n"
        f"CONTEXT: {context_description}\n"
        f"{retry_block}"
        "OUTPUT FORMAT: {original, fixed_decorative, fixed_informational, recommendation, wcag_criterion}\n"
        "Respond with only the JSON object.\n"
        "FINDING_JSON:" + json.dumps(payload) + "\n---END---"
    )


# =============================================================================
# 4. PatchGenerator
# =============================================================================

@dataclass
class PatchGenerationResult:
    candidate: PatchCandidate
    raw_llm_output: str


class PatchGenerator:
    """Turns a confirmed Finding + its original HTML element into one or two
    candidate patches (Design Doc 4.2.2)."""

    def __init__(self, llm_client: LLMClient | None = None):
        # Defaults to the deterministic mock so this class is fully testable
        # before the GPU cluster / VLLM_ENDPOINT is available. Swap in
        # PatchGenerator(llm_client=VLLMClient()) once real inference is needed.
        self.llm_client = llm_client or MockLLMClient()

    def infer_violation_type(self, finding: FindingObject) -> str:
        """IP-2's FINDING_OBJECT doesn't guarantee a `violation_type` field.
        Fall back to inferring it from criterion_number / finding_description.
        If you find yourself adding many entries here, raise a Contract
        Change Protocol message proposing violation_type become a required
        IP-2 field instead of inferred."""
        if finding.violation_type:
            return finding.violation_type

        criterion_to_type = {
            "1.1.1": "missing_alt_text",
            "1.4.3": "contrast_failure",
            "4.1.2": "missing_label",
        }
        inferred = criterion_to_type.get(finding.criterion_number)
        if inferred:
            return inferred

        desc = finding.finding_description.lower()
        if "alt" in desc and "image" in desc:
            return "missing_alt_text"
        if "contrast" in desc:
            return "contrast_failure"
        if "label" in desc:
            return "missing_label"

        raise UnsupportedViolationType(
            f"Could not infer violation_type for finding {finding.id} "
            f"(criterion {finding.criterion_number}). Supported types: "
            f"{SUPPORTED_VIOLATION_TYPES}"
        )

    def generate(
        self,
        finding: FindingObject,
        element_html: str,
        *,
        retry_error_context: str | None = None,
    ) -> PatchGenerationResult:
        """Generate one patch candidate for a single finding. Raises
        UnsupportedViolationType for anything outside the 3 supported
        types -- caller (fix_validator) should catch this and route the
        finding to requires_human_review instead of crashing."""
        violation_type = self.infer_violation_type(finding)
        if violation_type not in SUPPORTED_VIOLATION_TYPES:
            raise UnsupportedViolationType(
                f"violation_type={violation_type!r} not yet supported. "
                f"Supported: {SUPPORTED_VIOLATION_TYPES}"
            )

        user_prompt = _build_user_prompt(
            violation_type=violation_type,
            element_html=element_html,
            context_description=finding.finding_description,
            criterion_number=finding.criterion_number,
            retry_error_context=retry_error_context,
        )

        raw_output = self.llm_client.complete(SYSTEM_PROMPT, user_prompt)

        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError as e:
            # Design Doc 5.2.2: malformed structured output. The retry loop
            # that handles this lives in fix_validator.py (it already has a
            # retry loop for axe-core failures -- reuse that mechanism
            # rather than building a second one here).
            raise ValueError(
                f"LLM did not return valid JSON for finding {finding.id}: {e}\n"
                f"Raw output was: {raw_output!r}"
            ) from e

        candidate = PatchCandidate(
            original=parsed["original"],
            fixed_decorative=parsed.get("fixed_decorative"),
            fixed_informational=parsed.get("fixed_informational"),
            recommendation=parsed["recommendation"],
            wcag_criterion=parsed["wcag_criterion"],
        )
        logger.info(
            "[WS-4] Generated patch for finding=%s violation_type=%s recommendation=%s",
            finding.id, violation_type, candidate.recommendation,
        )
        return PatchGenerationResult(candidate=candidate, raw_llm_output=raw_output)

    def best_fix(self, candidate: PatchCandidate) -> str:
        """Picks the recommended fix string to actually validate/apply. The
        other candidate is preserved in the Fix Studio UI for the human to
        choose instead -- this is only for "what do we run through
        axe-core first."""
        if candidate.recommendation == "decorative":
            if not candidate.fixed_decorative:
                raise ValueError("recommendation='decorative' but fixed_decorative is empty")
            return candidate.fixed_decorative
        if candidate.recommendation == "informational":
            if not candidate.fixed_informational:
                raise ValueError("recommendation='informational' but fixed_informational is empty")
            return candidate.fixed_informational
        raise ValueError(f"Unknown recommendation value: {candidate.recommendation!r}")

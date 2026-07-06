"""
WS-3 — Visual Disability Specialist Agent

Covers: Blindness (VoiceOver/NVDA/JAWS), low vision (zoom, high contrast),
colour blindness (deuteranopia, protanopia, tritanopia), ageing vision.

WCAG scope  : 1.1.x, 1.3.x, 1.4.x, 2.4.7, 2.4.11, 2.4.13
Primary ATs : VoiceOver, NVDA, JAWS, ZoomText, Windows Magnifier

Also computes APCA Lc contrast alongside WCAG 1.4.3 ratio — forward compat
with WCAG 3.0 (Design Doc Section 1.2.6).

Author : Sreekar (WS-3)
Design : Section 2.3.2, Visual Disability Agent row
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .base_agent import BaseAccessibilityAgent
from .schemas import DisabilityClass
from ..rag.vector_store import WCAGVectorStore

logger = logging.getLogger("visual_agent")


# ── WCAG contrast + APCA helpers ───────────────────────────────────────────────

def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _parse_colour(colour: str) -> Optional[tuple[float, float, float]]:
    """Return (r,g,b) in [0,1] range, or None if unparseable."""
    colour = colour.strip()
    m = re.match(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$", colour)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = h[0]*2 + h[1]*2 + h[2]*2
        return int(h[0:2],16)/255, int(h[2:4],16)/255, int(h[4:6],16)/255
    m = re.match(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", colour)
    if m:
        return int(m.group(1))/255, int(m.group(2))/255, int(m.group(3))/255
    return None


def _luminance(colour: str) -> Optional[float]:
    rgb = _parse_colour(colour)
    if rgb is None:
        return None
    R, G, B = (_srgb_to_linear(c) for c in rgb)
    return 0.2126 * R + 0.7152 * G + 0.0722 * B


def compute_wcag_contrast(fg: str, bg: str) -> Optional[float]:
    """WCAG 1.4.3 contrast ratio. Returns None if colours unparseable."""
    l1, l2 = _luminance(fg), _luminance(bg)
    if l1 is None or l2 is None:
        return None
    lighter, darker = max(l1, l2), min(l1, l2)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def compute_apca_lc(fg: str, bg: str) -> Optional[float]:
    """
    APCA-W3C Bronze Lc value (WCAG 3.0 draft contrast algorithm).
    Positive = dark-on-light, negative = light-on-dark.
    Design Doc Reference: Section 1.2.6
    """
    yt, yb = _luminance(fg), _luminance(bg)
    if yt is None or yb is None:
        return None

    SA_TXT, SA_BG, SA_RTXT, SA_RBG = 0.57, 0.56, 0.62, 0.65
    SCALE, LO_CLIP, DELTA = 1.14, 0.1, 0.027

    if yb >= yt:
        sapc = (yb ** SA_BG - yt ** SA_TXT) * SCALE
        if abs(sapc) < LO_CLIP:
            return 0.0
        if sapc < DELTA:
            sapc -= sapc * (DELTA / sapc) ** 9
    else:
        sapc = (yb ** SA_RBG - yt ** SA_RTXT) * SCALE
        if abs(sapc) < LO_CLIP:
            return 0.0
        if sapc > -DELTA:
            sapc -= sapc * (DELTA / abs(sapc)) ** 9

    return round(sapc * 100, 1)


# ── VisualAgent ────────────────────────────────────────────────────────────────

class VisualAgent(BaseAccessibilityAgent):
    """
    Specialist agent for visual disability accessibility evaluation.

    Users covered: blind (screen reader), low vision (zoom/magnification),
    colour blind, and ageing vision.
    """

    def __init__(
        self,
        vllm_endpoint: str = "",
        vector_store: Optional[WCAGVectorStore] = None,
    ) -> None:
        super().__init__(
            disability_class=DisabilityClass.VISUAL,
            vllm_endpoint=vllm_endpoint,
            vector_store=vector_store,
        )

    def _build_system_prompt(self, criteria: list[dict]) -> str:
        criteria_block = self._format_criteria_for_prompt(criteria)

        return f"""You are a specialist web accessibility auditor evaluating HTML for barriers
experienced by users with visual disabilities.

YOUR USER POPULATION:
- Blind users: rely on VoiceOver (macOS/iOS), NVDA or JAWS (Windows). They navigate
  by headings, landmarks, links, form controls — they never see the page visually.
- Low vision users: use browser zoom (up to 400%), system magnification, high contrast mode.
  Content that breaks at zoom or relies on specific colours blocks them completely.
- Colour blind users: deuteranopia (red/green, 8% of males), protanopia (red appears dark),
  tritanopia (blue/yellow confusion). Colour-only distinctions are invisible to them.
- Ageing vision users: reduced contrast sensitivity, slower processing. 1 in 3 people
  over 65 have significant vision loss — the largest single demographic affected.

WCAG CRITERIA FOR THIS AUDIT (retrieved from WCAG 2.2 knowledge base):
{criteria_block}

OUTPUT FORMAT — STRICT RULES:
- Output ONLY a valid JSON array. No prose before or after.
- If there are no violations, output exactly: []
- Each finding must follow this schema exactly:

{{
  "criterion_number": "1.1.1",
  "criterion_level": "A",
  "criterion_text": "<verbatim text from the criteria block above>",
  "legal_regulations": ["ADA Title III", "Section 508"],
  "finding_description": "<what is wrong and where, including element details>",
  "disability_impact": "<name the AT and the specific failure the user experiences>",
  "element_selector": "<valid CSS selector>",
  "confidence": 0.92,
  "needs_context_reason": null
}}

FEW-SHOT EXAMPLE 1 — Missing alt text (criterion 1.1.1, Level A):
DOM: <img src="/images/hero-banner.jpg" class="banner-image" width="1200" height="400">

OUTPUT:
[{{
  "criterion_number": "1.1.1",
  "criterion_level": "A",
  "criterion_text": "All non-text content that is presented to the user has a text alternative that serves the equivalent purpose.",
  "legal_regulations": ["ADA Title III", "Section 508", "EU EAA Article 4", "India RPWD Act Section 46"],
  "finding_description": "img.banner-image has no alt attribute. Screen readers will announce the raw filename 'hero-banner.jpg' which is meaningless to a blind user.",
  "disability_impact": "A JAWS user pressing G to navigate images hears 'hero-banner dot jpg graphic' — no information about image content is conveyed.",
  "element_selector": "img.banner-image",
  "confidence": 0.97,
  "needs_context_reason": null
}}]

FEW-SHOT EXAMPLE 2 — Contrast failure (criterion 1.4.3, Level AA):
DOM: <p style="color: #767676; background-color: #ffffff;">Read our privacy policy</p>

OUTPUT:
[{{
  "criterion_number": "1.4.3",
  "criterion_level": "AA",
  "criterion_text": "The visual presentation of text and images of text has a contrast ratio of at least 4.5:1, except for large text, incidental text, or logotypes.",
  "legal_regulations": ["ADA Title III", "Section 508", "EU EAA Article 4"],
  "finding_description": "Text #767676 on background #ffffff yields a WCAG contrast ratio of 4.48:1 (fails 4.5:1 minimum). APCA Lc score: approximately 63 Lc (target >75 Lc for normal body text per WCAG 3.0 draft).",
  "disability_impact": "A low vision user or ageing user with reduced contrast sensitivity will find this text difficult to read, particularly on mobile screens in ambient light.",
  "element_selector": "p[style*='767676']",
  "confidence": 0.96,
  "needs_context_reason": null
}}]

FEW-SHOT EXAMPLE 3 — Missing focus indicator (criterion 2.4.7, Level AA):
DOM: <button class="cta-button" style="outline: none; border: none;">Get Started</button>

OUTPUT:
[{{
  "criterion_number": "2.4.7",
  "criterion_level": "AA",
  "criterion_text": "Any keyboard operable user interface has a mode of operation where the keyboard focus indicator is visible.",
  "legal_regulations": ["ADA Title III", "Section 508"],
  "finding_description": "button.cta-button has outline:none and no alternative focus style (no box-shadow, border-color or background change on :focus). Focus is invisible to keyboard users.",
  "disability_impact": "A low vision user navigating via Tab key loses all visual feedback on where keyboard focus is located, making it impossible to determine which element will activate on Enter.",
  "element_selector": "button.cta-button",
  "confidence": 0.93,
  "needs_context_reason": null
}}]

APCA SECONDARY CHECK:
For any contrast finding (1.4.3, 1.4.11), include in finding_description:
- WCAG contrast ratio (e.g. "4.48:1")
- Estimated APCA Lc score if colour values are known (e.g. "~63 Lc")
- APCA body text target: >75 Lc; large text: >60 Lc; UI components: >45 Lc

CRITICAL RULES:
1. If an image has alt="" AND (role="presentation" OR aria-hidden="true") it is CORRECTLY
   decorative — do NOT flag criterion 1.1.1. This is a valid accessibility technique.
2. Never flag a criterion not in the WCAG CRITERIA block above.
3. Never guess element_selector — derive it from the actual DOM element given.
4. If colour values come from a CSS class you cannot see, set confidence below 0.70
   and set needs_context_reason explaining what stylesheet inspection is required.
5. Output ONLY the JSON array. No explanation. No preamble. Nothing else."""
"""
WS-3 — Auditory Disability Specialist Agent

Covers: Deaf users, hard-of-hearing users, auditory processing disorder users.

WCAG scope  : 1.2.1 – 1.2.9 (time-based media entirely)
Primary ATs : Captions/subtitles, sign language video, text transcripts

Key differentiator over free tools:
    - Free tools check if a caption TRACK exists (binary yes/no).
    - This agent also evaluates CAPTION QUALITY signals:
        * Auto-generated captions detected by filename patterns
        * Audio description track presence for prerecorded video
        * Transcript proximity for audio-only content

Design Doc Reference: Section 1.1 (caption quality dependency problem)

Author : Sreekar (WS-3)
Design : Section 2.3.2, Auditory Disability Agent row
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .base_agent import BaseAccessibilityAgent
from .schemas import DisabilityClass
from ..rag.vector_store import WCAGVectorStore

logger = logging.getLogger("auditory_agent")

# Filename patterns that suggest auto-generated captions
# Auto-generated captions have significantly higher word error rates (~20-40%)
# vs professionally captioned content (~1-3%)
AUTO_CAPTION_PATTERNS = re.compile(
    r"(auto[_-]?gen|machine[_-]?generated|auto[_-]?caption|"
    r"auto[_-]?sub|generated|asr[_-]|yt[_-]auto)",
    re.IGNORECASE,
)


def detect_auto_caption(track_src: str) -> bool:
    """
    Heuristic: does the caption track filename suggest auto-generation?

    Parameters
    ----------
    track_src : str
        The src attribute value of a <track> element.

    Returns
    -------
    bool
        True if filename pattern suggests auto-generated captions.
    """
    return bool(AUTO_CAPTION_PATTERNS.search(track_src))


class AuditoryAgent(BaseAccessibilityAgent):
    """
    Specialist agent for auditory disability accessibility evaluation.

    Users covered: deaf, hard-of-hearing, auditory processing disorder.
    Evaluates time-based media (audio/video) for caption and transcript quality.
    """

    def __init__(
        self,
        vllm_endpoint: str = "",
        vector_store: Optional[WCAGVectorStore] = None,
    ) -> None:
        super().__init__(
            disability_class=DisabilityClass.AUDITORY,
            vllm_endpoint=vllm_endpoint,
            vector_store=vector_store,
        )

    def _build_system_prompt(self, criteria: list[dict]) -> str:
        criteria_block = self._format_criteria_for_prompt(criteria)

        return f"""You are a specialist web accessibility auditor evaluating HTML for barriers
experienced by users with auditory disabilities.

YOUR USER POPULATION:
- Deaf users: rely entirely on captions, transcripts, and visual cues. Audio content
  without text alternatives is completely inaccessible to them.
- Hard-of-hearing users: benefit from captions even when audio is partially audible,
  especially in noisy environments or when speakers have accents or speak quickly.
- Auditory processing disorder users: can hear sound but struggle to decode speech.
  Captions help them follow dialogue that sounds unclear even at normal volume.
- Note: Hearing loss is the 3rd most common chronic physical condition globally.
  Age-related hearing loss (presbycusis) affects 1 in 3 people over 65.

WCAG CRITERIA FOR THIS AUDIT (retrieved from WCAG 2.2 knowledge base):
{criteria_block}

OUTPUT FORMAT — STRICT RULES:
- Output ONLY a valid JSON array. No prose before or after.
- If there are no violations, output exactly: []
- Each finding must follow this schema exactly:

{{
  "criterion_number": "1.2.2",
  "criterion_level": "A",
  "criterion_text": "<verbatim text from the criteria block above>",
  "legal_regulations": ["ADA Title III", "Section 508", "US CVAA"],
  "finding_description": "<what is wrong and where, with specific element details>",
  "disability_impact": "<specific experience of a deaf or hard-of-hearing user>",
  "element_selector": "<valid CSS selector for the violating element>",
  "confidence": 0.92,
  "needs_context_reason": null
}}

FEW-SHOT EXAMPLE 1 — Video with no captions (criterion 1.2.2, Level A):
DOM: <video src="product-demo.mp4" controls class="demo-video"></video>

OUTPUT:
[{{
  "criterion_number": "1.2.2",
  "criterion_level": "A",
  "criterion_text": "Captions are provided for all prerecorded audio content in synchronized media.",
  "legal_regulations": ["ADA Title III", "Section 508", "US CVAA"],
  "finding_description": "video.demo-video has no <track kind='captions'> child element. The video has no captions for its audio content.",
  "disability_impact": "A deaf user watching this product demonstration receives no audio information — they cannot understand spoken product descriptions, presenter commentary, or any audio cues in the video.",
  "element_selector": "video.demo-video",
  "confidence": 0.95,
  "needs_context_reason": null
}}]

FEW-SHOT EXAMPLE 2 — Auto-generated captions detected (criterion 1.2.2, Level A):
DOM:
<video src="webinar.mp4" controls>
  <track kind="captions" src="captions/auto_generated_en.vtt" srclang="en" label="English">
</video>

OUTPUT:
[{{
  "criterion_number": "1.2.2",
  "criterion_level": "A",
  "criterion_text": "Captions are provided for all prerecorded audio content in synchronized media.",
  "legal_regulations": ["ADA Title III", "Section 508", "US CVAA"],
  "finding_description": "The caption track filename 'auto_generated_en.vtt' indicates machine-generated captions. Auto-generated captions typically have 20-40% word error rates, which does not meet the accuracy standard required for WCAG conformance.",
  "disability_impact": "A hard-of-hearing user relying on captions for a technical webinar will encounter frequent transcription errors, potentially misunderstanding critical content. High error rates in captions fail the spirit of WCAG 1.2.2.",
  "element_selector": "video track[src*='auto_generated']",
  "confidence": 0.78,
  "needs_context_reason": "Manually verify caption accuracy by playing the video and reading the captions simultaneously. Check that speaker names, technical terms, and punctuation are correct."
}}]

CAPTION QUALITY HEURISTICS TO APPLY:
1. No <track> child → CONFIRMED violation of 1.2.2 (confidence: 0.95+)
2. <track kind="captions"> present with clean filename → pass (no finding)
3. <track> src filename matches auto-generation patterns
   (auto_gen, machine_generated, auto_caption, asr_, yt_auto) → flag as
   NEEDS_CONTEXT with manual verification instruction (confidence: 0.70-0.80)
4. <video> with audio but no <track kind="descriptions"> → flag 1.2.5 (AA)
5. <audio> element with no adjacent transcript text or link → flag 1.2.1 (A)

SCOPE RULES:
- Only audit <video>, <audio>, <iframe> (may embed media), and <object> elements.
- If the DOM chunk contains none of these elements, output: []
- Do not flag missing captions on <video> elements that have audio="false" or
  are demonstrably decorative (autoplay, muted, loop with no controls — ambient video).
- muted + autoplay + loop = decorative ambient video → NOT a caption violation.

CRITICAL RULES:
1. Output ONLY the JSON array. No text before or after.
2. Never invent a criterion_number not in the WCAG CRITERIA block above.
3. Confidence must reflect genuine uncertainty — auto-caption detection is a heuristic,
   not a confirmed violation. Set confidence 0.70-0.80 for heuristic findings.
4. US CVAA applies to caption requirements for online video — include it in
   legal_regulations for 1.2.2 and 1.2.4 findings."""

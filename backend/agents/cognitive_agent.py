"""
WS-3 — Cognitive Disability Specialist Agent

Covers: Dyslexia, ADHD, anxiety, dementia, intellectual disabilities,
learning disabilities, and acquired cognitive impairments (TBI, stroke).

WCAG scope  : 3.1.x (readable), 3.3.x (input assistance), 2.2.x (enough time),
              1.3.x (adaptable), 2.4.2 (page titled)
Primary ATs : Reading tools (Immersive Reader), text-to-speech, AAC devices

Beyond-WCAG checks (Design Doc Section 1.2.1 — Cognitive Accessibility):
1. Reading level — Flesch-Kincaid Grade Level computed inline (no external API)
   FK = 0.39*(words/sentences) + 11.8*(syllables/words) - 15.59
2. Form complexity — field count > 7 without fieldset grouping
3. Error prevention — destructive actions without confirmation step

Author : Sreekar (WS-3)
Design : Section 2.3.2, Cognitive Disability Agent row
         Section 1.2.1, Cognitive Accessibility — The Invisible Disability Class
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .base_agent import BaseAccessibilityAgent
from .schemas import DisabilityClass
from ..rag.vector_store import WCAGVectorStore

logger = logging.getLogger("cognitive_agent")


# ── Flesch-Kincaid Grade Level ─────────────────────────────────────────────────

def _count_syllables(word: str) -> int:
    """
    Estimate syllable count for one English word.

    Algorithm: vowel group counting with common exception handling.
    Accuracy ~85% for typical English text — sufficient for grade-level
    estimation (we need relative scores, not exact counts).

    Parameters
    ----------
    word : str
        Single word (lowercase, no punctuation).

    Returns
    -------
    int
        Estimated syllable count, minimum 1.
    """
    word = word.lower().strip(".,!?;:'\"()")
    if not word:
        return 0

    # Count vowel groups as syllable nuclei
    vowels = "aeiouy"
    count = 0
    prev_vowel = False
    for ch in word:
        is_vowel = ch in vowels
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel

    # Apply common corrections
    if word.endswith("e") and count > 1:
        count -= 1
    if word.endswith("le") and len(word) > 2 and word[-3] not in vowels:
        count += 1
    if word.endswith("es") or word.endswith("ed"):
        pass   # usually no extra syllable
    if count == 0:
        count = 1  # every word has at least one syllable

    return count


def compute_flesch_kincaid_grade(text: str) -> Optional[float]:
    """
    Compute Flesch-Kincaid Grade Level for a block of English text.

    Formula: FK = 0.39 * (words/sentences) + 11.8 * (syllables/words) - 15.59

    Design Doc Reference: Cognitive Agent specification — "implement the FK
    formula inline, do NOT call an external API."

    Parameters
    ----------
    text : str
        Plain text to analyse (HTML tags should be stripped before passing).

    Returns
    -------
    float or None
        Grade level (e.g. 9.2 = Grade 9, roughly 14-year-old reading level).
        None if text is too short to compute (< 2 sentences or < 10 words).
    """
    # Strip HTML tags if any slipped through
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Count sentences (split on .!? followed by space or end-of-string)
    sentences = re.split(r"[.!?]+(?:\s+|$)", text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 3]
    n_sentences = len(sentences)

    # Count words
    words = re.findall(r"\b[a-zA-Z]+\b", text)
    n_words = len(words)

    # Need minimum corpus for meaningful score
    if n_sentences < 2 or n_words < 10:
        return None

    # Count syllables
    n_syllables = sum(_count_syllables(w) for w in words)

    # Flesch-Kincaid formula
    fk = 0.39 * (n_words / n_sentences) + 11.8 * (n_syllables / n_words) - 15.59
    return round(fk, 1)


# ── CognitiveAgent ─────────────────────────────────────────────────────────────

class CognitiveAgent(BaseAccessibilityAgent):
    """
    Specialist agent for cognitive and neurological disability evaluation.

    Users covered: dyslexia, ADHD, anxiety disorders, dementia,
    intellectual disabilities, acquired cognitive impairments (TBI, stroke).
    """

    def __init__(
        self,
        vllm_endpoint: str = "",
        vector_store: Optional[WCAGVectorStore] = None,
    ) -> None:
        super().__init__(
            disability_class=DisabilityClass.COGNITIVE,
            vllm_endpoint=vllm_endpoint,
            vector_store=vector_store,
        )

    def _build_system_prompt(self, criteria: list[dict]) -> str:
        criteria_block = self._format_criteria_for_prompt(criteria)

        return f"""You are a specialist web accessibility auditor evaluating HTML for barriers
experienced by users with cognitive and neurological disabilities.

YOUR USER POPULATION:
- Dyslexia users: struggle with dense text, long sentences, and low letter spacing.
  ~15-20% of the population has some degree of dyslexia. They need clear structure,
  short paragraphs, and readable fonts.
- ADHD users: easily distracted by auto-playing media, blinking elements, and
  cluttered layouts. They struggle to return focus after an interruption.
- Anxiety disorder users: unexpected changes of context (form submissions that
  navigate away, auto-playing audio) trigger anxiety responses that interrupt task flow.
- Dementia and memory impairment users: cannot remember what they entered in previous
  steps, cannot tolerate session timeouts without warning, need consistent navigation.
- Intellectual disability users: need simple language (Grade 6-8 reading level target),
  clear instructions, and error messages in plain English — not codes or technical terms.
- Acquired cognitive impairment users (TBI, stroke): may have processing speed
  limitations — time limits and rapidly changing content block them entirely.

WCAG CRITERIA FOR THIS AUDIT (retrieved from WCAG 2.2 knowledge base):
{criteria_block}

OUTPUT FORMAT — STRICT RULES:
- Output ONLY a valid JSON array. No prose before or after.
- If there are no violations, output exactly: []
- Each finding must follow this schema exactly:

{{
  "criterion_number": "3.1.5",
  "criterion_level": "AAA",
  "criterion_text": "<verbatim text from the criteria block above>",
  "legal_regulations": ["ADA Title III"],
  "finding_description": "<what is wrong, including computed metrics where applicable>",
  "disability_impact": "<specific cognitive/neurological user experience>",
  "element_selector": "<valid CSS selector>",
  "confidence": 0.85,
  "needs_context_reason": null
}}

FEW-SHOT EXAMPLE 1 — High reading level (beyond standard WCAG, cognitive concern):
DOM: <main><p>The utilization of asymmetric cryptographic methodologies necessitates
     the implementation of certificate authority hierarchies to authenticate
     endpoint communications.</p></main>

OUTPUT:
[{{
  "criterion_number": "3.1.5",
  "criterion_level": "AAA",
  "criterion_text": "Where text requires reading ability more advanced than the lower secondary education level, supplemental content or a version that does not require reading ability more advanced than the lower secondary education level is available.",
  "legal_regulations": ["ADA Title III"],
  "finding_description": "The body text in main has an estimated Flesch-Kincaid Grade Level of approximately 16-18 (post-graduate level). Text uses complex multi-syllable technical vocabulary in long sentences. WCAG 3.1.5 targets Grade 9 or lower for supplemental content availability.",
  "disability_impact": "A user with dyslexia, intellectual disability, or acquired cognitive impairment (stroke, TBI) will find this text extremely difficult to decode. Long complex sentences with technical terms require significant working memory to parse.",
  "element_selector": "main p",
  "confidence": 0.82,
  "needs_context_reason": null
}}]

FEW-SHOT EXAMPLE 2 — Form too complex, no fieldset grouping (criterion 1.3.1 + cognitive concern):
DOM:
<form class="checkout-form">
  <input type="text" name="first_name" placeholder="First name">
  <input type="text" name="last_name" placeholder="Last name">
  <input type="email" name="email" placeholder="Email">
  <input type="tel" name="phone" placeholder="Phone">
  <input type="text" name="address" placeholder="Street address">
  <input type="text" name="city" placeholder="City">
  <input type="text" name="postcode" placeholder="Postcode">
  <input type="text" name="card_number" placeholder="Card number">
  <input type="text" name="expiry" placeholder="MM/YY">
  <input type="text" name="cvv" placeholder="CVV">
  <button type="submit">Pay Now</button>
</form>

OUTPUT:
[{{
  "criterion_number": "1.3.1",
  "criterion_level": "A",
  "criterion_text": "Information, structure, and relationships conveyed through presentation can be programmatically determined or are available in text.",
  "legal_regulations": ["ADA Title III", "Section 508", "EU EAA Article 4"],
  "finding_description": "form.checkout-form contains 10 input fields with no <fieldset> grouping. Personal details, address, and payment fields are all in one flat list with no programmatic structure. Related fields (address group, payment group) share no group relationship.",
  "disability_impact": "A user with ADHD or memory impairment loses track of where they are in the form after any distraction. Without fieldset/legend groupings, they cannot determine how many sections remain or which inputs belong to which logical group.",
  "element_selector": "form.checkout-form",
  "confidence": 0.91,
  "needs_context_reason": null
}}]

SPECIFIC CHECKS TO PERFORM:

1. READING LEVEL (reference WCAG 3.1.5):
   - Extract all <p> text from the DOM chunk
   - Estimate complexity by: sentence length, word length, presence of
     technical multi-syllable terms
   - If paragraphs contain predominantly long sentences (>25 words avg) AND
     complex vocabulary: flag as cognitive concern referencing 3.1.5
   - Grade level target: Grade 9 or lower for AA compliance; Grade 6-8 ideal

2. FORM COMPLEXITY (reference WCAG 1.3.1, 3.3.2):
   - Count <input>, <select>, <textarea> elements inside any <form>
   - If count > 7 AND no <fieldset> elements present: flag 1.3.1
   - If <input> elements have placeholder text but no associated <label>: flag 3.3.2
   - Note: placeholder text disappears on input — it cannot substitute for a label

3. ERROR PREVENTION (reference WCAG 3.3.4):
   - Look for <button type="submit"> or <input type="submit"> near text containing
     words: "delete", "remove", "cancel", "unsubscribe", "deactivate", "close account"
   - If destructive action button has no confirmation pattern (no confirm dialog markup,
     no "are you sure" text nearby, no undo mechanism): flag 3.3.4
   - Confidence should be 0.70-0.80 — static analysis cannot see JS confirm() dialogs

4. ANIMATION AND DISTRACTION (reference WCAG 2.2.2):
   - Flag <marquee> elements (obsolete but still used)
   - Flag elements with animation CSS classes without pause mechanism
   - Flag <video autoplay loop> without controls (ambient videos are fine;
     content videos autoplay is distracting)

5. MISSING PAGE TITLE (reference WCAG 2.4.2):
   - If DOM chunk contains <head> or <title> element, check title is present
     and descriptive (not just "Home" or the domain name alone)

6. REDUNDANT ENTRY (reference WCAG 3.3.7, Level A — WCAG 2.2):
   - Look for multi-step form patterns where the same data type appears twice
     (billing address + shipping address with no "same as billing" checkbox)
   - Flag as 3.3.7 concern with confidence 0.70

CRITICAL RULES:
1. Output ONLY the JSON array.
2. Reading level checks are COGNITIVE CONCERNS — reference WCAG 3.1.5 (AAA) and
   note it is beyond standard AA compliance but important for real users.
3. Placeholder-as-label is a finding (criterion 3.3.2) — placeholders disappear
   when users start typing, leaving no label visible for cognitive reference.
4. Never penalise technical documentation or developer-facing tools for high reading
   level — reading level violations apply to consumer-facing public content.
5. Form complexity checks only apply when you can COUNT the actual input elements —
   do not guess."""
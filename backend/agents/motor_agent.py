"""
WS-3 — Motor Disability Specialist Agent

Covers: Users with motor impairments using keyboard-only navigation,
switch access, eye tracking, mouth stick, or voice control.

WCAG scope  : 2.1.x (keyboard), 2.4.x (navigable), 2.5.x (input modalities)
Primary ATs : Keyboard, switch access (single/dual switch), eye tracking,
              Dragon NaturallySpeaking (voice control)

Specific checks beyond standard free tools (Design Doc Section 1.2.3):
1. Touch target size — WCAG 2.5.8 (AA, new in WCAG 2.2): min 24x24px
   Uses bounding box data in DOM chunk metadata if available.
2. Keyboard trap — tabindex=-1 on elements not intentionally hidden
3. Skip navigation — first focusable element must be skip-to-main-content
4. Positive tabindex values — create non-DOM tab order, confuse AT users

Author : Sreekar (WS-3)
Design : Section 2.3.2, Motor Disability Agent row
         Section 1.2.3, Touch and Motor Accessibility
"""

from __future__ import annotations

import logging
from typing import Optional

from .base_agent import BaseAccessibilityAgent
from .schemas import DisabilityClass
from ..rag.vector_store import WCAGVectorStore

logger = logging.getLogger("motor_agent")


class MotorAgent(BaseAccessibilityAgent):
    """
    Specialist agent for motor disability accessibility evaluation.

    Users covered: motor impairments requiring keyboard-only navigation,
    switch access, eye tracking, or voice control.
    """

    def __init__(
        self,
        vllm_endpoint: str = "",
        vector_store: Optional[WCAGVectorStore] = None,
    ) -> None:
        super().__init__(
            disability_class=DisabilityClass.MOTOR,
            vllm_endpoint=vllm_endpoint,
            vector_store=vector_store,
        )

    def _build_system_prompt(self, criteria: list[dict]) -> str:
        criteria_block = self._format_criteria_for_prompt(criteria)

        return f"""You are a specialist web accessibility auditor evaluating HTML for barriers
experienced by users with motor and physical disabilities.

YOUR USER POPULATION:
- Keyboard-only users: cannot use a mouse due to tremors, paralysis, or limb differences.
  They navigate entirely via Tab, Shift+Tab, Enter, Space, and arrow keys.
- Switch access users: use one or two switches to cycle through focusable elements.
  Every extra Tab stop or keyboard trap costs them significant time and effort.
- Eye tracking users: activate elements by dwelling on them — require larger targets
  and reliable focus indicators to confirm their selection.
- Voice control users (Dragon NaturallySpeaking): activate elements by name. Elements
  with no visible label or whose visible label doesn't match the accessible name break
  voice activation entirely.
- Users with tremors: need larger touch targets (WCAG 2.5.8) and pointer cancellation
  (WCAG 2.5.2) to prevent accidental activations they cannot easily reverse.

WCAG CRITERIA FOR THIS AUDIT (retrieved from WCAG 2.2 knowledge base):
{criteria_block}

OUTPUT FORMAT — STRICT RULES:
- Output ONLY a valid JSON array. No prose before or after.
- If there are no violations, output exactly: []
- Each finding must follow this schema exactly:

{{
  "criterion_number": "2.1.1",
  "criterion_level": "A",
  "criterion_text": "<verbatim text from the criteria block above>",
  "legal_regulations": ["ADA Title III", "Section 508"],
  "finding_description": "<what is wrong and where>",
  "disability_impact": "<specific motor AT failure — name the AT or user type>",
  "element_selector": "<valid CSS selector>",
  "confidence": 0.90,
  "needs_context_reason": null
}}

FEW-SHOT EXAMPLE 1 — Keyboard trap in modal (criterion 2.1.2, Level A):
DOM:
<div class="modal" role="dialog" aria-modal="true">
  <div class="modal-content">
    <h2>Subscribe to newsletter</h2>
    <input type="email" placeholder="Enter email">
    <button onclick="closeModal()">Close</button>
  </div>
</div>

OUTPUT:
[{{
  "criterion_number": "2.1.2",
  "criterion_level": "A",
  "criterion_text": "If keyboard focus can be moved to a component of the page using a keyboard interface, then focus can be moved away from that component using only a keyboard interface.",
  "legal_regulations": ["ADA Title III", "Section 508"],
  "finding_description": "The modal dialog (div.modal[role='dialog']) has no visible keyboard escape mechanism. The Close button uses onclick but there is no keydown/keyup handler for Escape key, which is the standard keyboard pattern for dismissing dialogs per ARIA Authoring Practices Guide.",
  "disability_impact": "A keyboard-only user who tabs into this modal cannot exit it without pressing Escape — which is not wired up. They are trapped inside the modal and cannot access any content outside it, effectively locking them out of the rest of the page.",
  "element_selector": "div.modal[role='dialog']",
  "confidence": 0.82,
  "needs_context_reason": "Verify with keyboard testing: Tab into the modal, then press Escape. If focus does not leave the modal, the keyboard trap is confirmed. Also check if a global keydown listener handles Escape in external JS files."
}}]

FEW-SHOT EXAMPLE 2 — Touch target too small, WCAG 2.5.8 (Level AA, new in WCAG 2.2):
DOM:
<nav class="pagination">
  <a href="/page/1" style="width: 18px; height: 18px; display: inline-block;">1</a>
  <a href="/page/2" style="width: 18px; height: 18px; display: inline-block;">2</a>
</nav>

OUTPUT:
[{{
  "criterion_number": "2.5.8",
  "criterion_level": "AA",
  "criterion_text": "The size of the target for pointer inputs is at least 24 by 24 CSS pixels.",
  "legal_regulations": ["ADA Title III", "Section 508", "EU EAA Article 4"],
  "finding_description": "Pagination links in nav.pagination are 18x18px (inline styles confirm width:18px, height:18px), which is below the WCAG 2.5.8 minimum of 24x24px. This is a new Level AA criterion added in WCAG 2.2.",
  "disability_impact": "A user with hand tremors or limited motor precision cannot reliably tap these 18x18px targets on a touch screen. Eye tracking users dwelling on small targets frequently activate adjacent elements instead.",
  "element_selector": "nav.pagination a",
  "confidence": 0.94,
  "needs_context_reason": null
}}]

SPECIFIC CHECKS TO PERFORM ON EVERY DOM CHUNK:

1. TOUCH TARGET SIZE (WCAG 2.5.8, Level AA — WCAG 2.2):
   - Inspect inline styles for width/height on <a>, <button>, <input>, <select>
   - If width < 24px OR height < 24px: flag as 2.5.8 violation
   - If size is in CSS class (not inline): set confidence 0.65, needs_context_reason
     explaining to measure computed size in browser DevTools

2. KEYBOARD TRAP (WCAG 2.1.2, Level A):
   - Look for [role="dialog"], .modal, [aria-modal="true"] elements
   - Check if they have a visible close mechanism AND keyboard event handlers
   - If onclick-only close with no onkeydown/onkeyup: flag with confidence 0.80
     and needs_context_reason to test Escape key
   - Elements with tabindex="-1" that lack aria-hidden="true" are suspicious —
     they remove elements from tab order unexpectedly

3. SKIP NAVIGATION (WCAG 2.4.1, Level A):
   - Only check this on DOM chunks that represent the page header/top of document
   - The FIRST focusable element (<a>, <button>, first [tabindex]) must be a
     skip-to-main-content link (text contains "skip", "jump to main", "skip navigation")
   - If first focusable is not a skip link: flag 2.4.1 with confidence 0.85

4. POSITIVE TABINDEX (WCAG 2.4.3, Level A):
   - Flag any element with tabindex value > 0 (e.g. tabindex="1", tabindex="5")
   - Positive tabindex values override DOM order and create unpredictable tab sequences
   - tabindex="0" is fine (adds to natural order); tabindex="-1" is fine (removes from order)
   - Only tabindex > 0 is a violation

5. DRAGGING ALTERNATIVES (WCAG 2.5.7, Level AA — WCAG 2.2):
   - Look for drag-and-drop UI patterns: [draggable="true"], sortable lists,
     .sortable, .draggable class names, ondragstart handlers
   - Flag if no single-pointer alternative is visible (no up/down buttons, no
     context menu option for reordering)

CRITICAL RULES:
1. Output ONLY the JSON array.
2. Do not flag tabindex="-1" as a violation — it is a valid technique for managing focus.
3. Do not flag tabindex="0" — it is correct usage.
4. Only flag tabindex > 0 values.
5. Keyboard trap findings must always include needs_context_reason with a specific
   keyboard test instruction — static analysis cannot confirm traps with certainty."""
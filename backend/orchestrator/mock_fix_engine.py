"""
TEMPORARY mock fix engine — stand-in for Charan's real fix engine.
Delete once real fix engine is wired in (Day 6).
Simulates: patch generation, validation, diffing, preview building.
"""

import asyncio
from backend.orchestrator.contracts import Finding, Fix


async def mock_fix_engine(finding: Finding) -> Fix:
    """
    Real version (Charan, Day 6): generates an actual HTML patch,
    validates it by re-running axe-core, builds a diff + iframe preview.
    Mock version: returns a fake but structurally correct Fix object.
    """
    await asyncio.sleep(0.2)  # simulate patch generation latency

    return Fix(
        patch_html=f"<!-- fixed: {finding.element_selector} -->",
        patch_validated=True,
        diff_html=f"<span class='diff-old'>{finding.element_selector}</span> -> <span class='diff-new'>fixed</span>",
        preview_srcdoc="<html><body>Mock preview - fix applied</body></html>",
        requires_human_review=False,
        review_reason=None,
    )
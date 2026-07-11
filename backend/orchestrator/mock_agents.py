"""
TEMPORARY mock agents — stand-ins for Sreekar's real agents (backend/agents/).
Delete this file once real agents are wired in (Day 4).
Lets Mahesh test orchestrator logic without waiting on WS-3.
"""

import asyncio
import uuid
from backend.orchestrator.contracts import Finding


async def mock_visual_agent(dom_html: str) -> list[Finding]:
    await asyncio.sleep(0.5)  # simulate agent latency
    return [
        Finding(
            id=str(uuid.uuid4()),
            disability_class="visual",
            criterion_number="1.4.3",
            criterion_level="AA",
            criterion_text="Text has a contrast ratio of at least 4.5:1",
            legal_regulations=["ADA Title III"],
            finding_description="Low contrast text detected on submit button",
            disability_impact="Users with low vision may not read button text",
            element_selector="button.submit",
            confidence=0.92,
            status="confirmed",
            critique_verdict="CONFIRMED",
            critique_citation="1.4.3 Contrast (Minimum)",
            impact_score=78,
        )
    ]


async def mock_auditory_agent(dom_html: str) -> list[Finding]:
    await asyncio.sleep(0.3)
    return []


async def mock_motor_agent(dom_html: str) -> list[Finding]:
    await asyncio.sleep(0.4)
    return []


async def mock_cognitive_agent(dom_html: str) -> list[Finding]:
    await asyncio.sleep(0.3)
    return []


async def mock_at_parsing_agent(dom_html: str) -> list[Finding]:
    await asyncio.sleep(0.4)
    return []
"""
TEMPORARY mock critique agent — stand-in for Sreekar's real critique agent.
Delete once real critique agent is wired in (Day 5).
"""

import asyncio
from backend.orchestrator.contracts import Finding


async def mock_critique_agent(findings: list[Finding]) -> list[Finding]:
    await asyncio.sleep(0.3)
    reviewed = []
    for finding in findings:
        finding.critique_verdict = "CONFIRMED"
        finding.status = "confirmed"
        reviewed.append(finding)
    return reviewed
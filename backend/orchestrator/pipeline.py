"""
Orchestrator pipeline: DOM payload -> agents -> critique -> fix engine -> report.
Owned by Mahesh.
"""

import asyncio
from backend.orchestrator.contracts import DOMPayload, ReportJSON, AuditMetadata, Benchmark, FindingWithFix
from backend.orchestrator.mock_agents import (
    mock_visual_agent,
    mock_auditory_agent,
    mock_motor_agent,
    mock_cognitive_agent,
    mock_at_parsing_agent,
)
from backend.orchestrator.mock_critique_agent import mock_critique_agent
from backend.orchestrator.mock_fix_engine import mock_fix_engine


def calculate_impact_score(finding) -> int:
    level_weight = {"A": 40, "AA": 25, "AAA": 10}.get(finding.criterion_level, 15)
    confidence_weight = finding.confidence * 30
    prevalence_weight = {
        "visual": 20, "motor": 15, "cognitive": 15,
        "auditory": 10, "at_parsing": 20,
    }.get(finding.disability_class, 10)
    score = level_weight + confidence_weight + prevalence_weight
    return min(100, round(score))


class AuditPipeline:
    def __init__(self):
        pass

    async def run_audit(self, payload: DOMPayload) -> ReportJSON:
        # Step 1: run all 5 agents in parallel
        results = await asyncio.gather(
            mock_visual_agent(payload.dom_html),
            mock_auditory_agent(payload.dom_html),
            mock_motor_agent(payload.dom_html),
            mock_cognitive_agent(payload.dom_html),
            mock_at_parsing_agent(payload.dom_html),
        )
        all_findings = [finding for agent_findings in results for finding in agent_findings]

        # Step 2: critique agent verifies findings
        reviewed_findings = await mock_critique_agent(all_findings)

        # Step 3: impact-weighting
        for f in reviewed_findings:
            f.impact_score = calculate_impact_score(f)

        # Step 4: fix engine generates patches for each confirmed finding, in parallel
        confirmed = [f for f in reviewed_findings if f.status == "confirmed"]
        fixes = await asyncio.gather(*[mock_fix_engine(f) for f in confirmed])

        findings_with_fix = [
            FindingWithFix(**f.model_dump(), fix=fix)
            for f, fix in zip(confirmed, fixes)
        ]

        return ReportJSON(
            audit_metadata=AuditMetadata(
                url=payload.url,
                timestamp=payload.timestamp,
                wcag_version="2.2",
                spa_detected=payload.meta.spa_detected,
            ),
            findings=findings_with_fix,
            benchmark=Benchmark(axe_findings=0, wave_findings=0, our_findings=len(findings_with_fix), unique=0),
            news_preview=[],
            disclaimer="This is an automated accessibility scan and not a substitute for professional audit.",
        )
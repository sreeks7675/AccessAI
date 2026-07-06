"""
Orchestrator pipeline: DOM payload -> agents -> critique -> fix engine -> report.
Owned by Mahesh.
"""

import asyncio
import logging
from bs4 import BeautifulSoup
from backend.orchestrator.contracts import DOMPayload, ReportJSON, AuditMetadata, Benchmark, FindingWithFix, Fix
from backend.orchestrator.mock_agents import (
    mock_visual_agent,
    mock_auditory_agent,
    mock_motor_agent,
    mock_cognitive_agent,
    mock_at_parsing_agent,
)
from backend.orchestrator.mock_critique_agent import mock_critique_agent
from backend.fix_engine import FixEnginePipeline, FindingObject

logger = logging.getLogger("wcag-audit")

AGENT_TIMEOUT_SECONDS = 15
FIX_TIMEOUT_SECONDS = 15

fix_engine_pipeline = FixEnginePipeline()


def calculate_impact_score(finding) -> int:
    level_weight = {"A": 40, "AA": 25, "AAA": 10}.get(finding.criterion_level, 15)
    confidence_weight = finding.confidence * 30
    prevalence_weight = {
        "visual": 20, "motor": 15, "cognitive": 15,
        "auditory": 10, "at_parsing": 20,
    }.get(finding.disability_class, 10)
    score = level_weight + confidence_weight + prevalence_weight
    return min(100, round(score))


def extract_element_html(dom_html: str, selector: str) -> str:
    """
    Pulls the actual HTML snippet for a given CSS selector out of the full
    page DOM. Charan's fix engine needs the real element, not just the
    selector string.
    """
    try:
        soup = BeautifulSoup(dom_html, "html.parser")
        element = soup.select_one(selector)
        if element:
            return str(element)
    except Exception as e:
        logger.warning(f"Could not extract element for selector '{selector}': {e}")
    # Fallback: a minimal stand-in so the fix engine has *something* to work with
    return f"<div>{selector}</div>"


async def run_agent_safely(agent_fn, agent_name: str, dom_html: str) -> list:
    try:
        return await asyncio.wait_for(agent_fn(dom_html), timeout=AGENT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(f"Agent '{agent_name}' timed out after {AGENT_TIMEOUT_SECONDS}s")
        return []
    except Exception as e:
        logger.error(f"Agent '{agent_name}' failed: {e}")
        return []


async def run_fix_safely(finding, dom_html: str) -> Fix | None:
    """
    Runs Charan's real (synchronous) FixEnginePipeline in a thread so it
    doesn't block the async event loop, with a timeout + failure isolation
    just like the agent calls.
    """
    try:
        finding_obj = FindingObject.from_dict(finding.model_dump())
        element_html = extract_element_html(dom_html, finding.element_selector)

        result = await asyncio.wait_for(
            asyncio.to_thread(fix_engine_pipeline.run, finding_obj, element_html),
            timeout=FIX_TIMEOUT_SECONDS,
        )
        return Fix(**result.to_dict())
    except asyncio.TimeoutError:
        logger.warning(f"Fix engine timed out for finding {finding.id}")
        return None
    except Exception as e:
        logger.error(f"Fix engine failed for finding {finding.id}: {e}")
        return None


class AuditPipeline:
    def __init__(self):
        pass

    async def run_audit(self, payload: DOMPayload) -> ReportJSON:
        results = await asyncio.gather(
            run_agent_safely(mock_visual_agent, "visual", payload.dom_html),
            run_agent_safely(mock_auditory_agent, "auditory", payload.dom_html),
            run_agent_safely(mock_motor_agent, "motor", payload.dom_html),
            run_agent_safely(mock_cognitive_agent, "cognitive", payload.dom_html),
            run_agent_safely(mock_at_parsing_agent, "at_parsing", payload.dom_html),
        )
        all_findings = [finding for agent_findings in results for finding in agent_findings]

        try:
            reviewed_findings = await asyncio.wait_for(
                mock_critique_agent(all_findings), timeout=AGENT_TIMEOUT_SECONDS
            )
        except Exception as e:
            logger.error(f"Critique agent failed: {e}")
            for f in all_findings:
                f.status = "needs_context"
                f.critique_verdict = "NEEDS_CONTEXT"
            reviewed_findings = all_findings

        for f in reviewed_findings:
            f.impact_score = calculate_impact_score(f)

        confirmed = [f for f in reviewed_findings if f.status == "confirmed"]
        fixes = await asyncio.gather(*[run_fix_safely(f, payload.dom_html) for f in confirmed])

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
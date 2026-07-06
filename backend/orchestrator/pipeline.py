"""
Orchestrator pipeline: DOM payload -> agents -> critique -> fix engine -> report.
Owned by Mahesh.
"""

import asyncio
import logging
import uuid
from bs4 import BeautifulSoup
from backend.orchestrator.contracts import (
    DOMPayload, ReportJSON, AuditMetadata, Benchmark, FindingWithFix, Fix, Finding
)
from backend.orchestrator.mock_critique_agent import mock_critique_agent
from backend.fix_engine import FixEnginePipeline, FindingObject

from backend.agents.visual_agent import VisualAgent
from backend.agents.auditory_agent import AuditoryAgent
from backend.agents.motor_agent import MotorAgent
from backend.agents.cognitive_agent import CognitiveAgent
from backend.agents.at_parsing_agent import ATPArsingAgent
from backend.agents.schemas import AuditAgentInput
from backend.rag.vector_store import WCAGVectorStore

logger = logging.getLogger("wcag-audit")

AGENT_TIMEOUT_SECONDS = 30  # real LLM calls are slower than mocks
FIX_TIMEOUT_SECONDS = 15

fix_engine_pipeline = FixEnginePipeline()

# Shared vector store instance across all 5 agents (avoid reloading embeddings 5x)
_vector_store = WCAGVectorStore()

_agents = {
    "visual": VisualAgent(vector_store=_vector_store),
    "auditory": AuditoryAgent(vector_store=_vector_store),
    "motor": MotorAgent(vector_store=_vector_store),
    "cognitive": CognitiveAgent(vector_store=_vector_store),
    "at_parsing": ATPArsingAgent(vector_store=_vector_store),
}


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
    try:
        soup = BeautifulSoup(dom_html, "html.parser")
        element = soup.select_one(selector)
        if element:
            return str(element)
    except Exception as e:
        logger.warning(f"Could not extract element for selector '{selector}': {e}")
    return f"<div>{selector}</div>"


def convert_agent_finding_to_contract(agent_finding) -> Finding:
    """
    Converts Sreekar's AgentFinding (nested criterion object, no critique/impact
    fields yet) into our flat Finding contract. Critique verdict and impact score
    get filled in later in the pipeline once critique runs.
    """
    return Finding(
        id=agent_finding.id,
        disability_class=agent_finding.disability_class.value if hasattr(agent_finding.disability_class, "value") else agent_finding.disability_class,
        criterion_number=agent_finding.criterion.criterion_number,
        criterion_level=agent_finding.criterion.criterion_level,
        criterion_text=agent_finding.criterion.criterion_text,
        legal_regulations=agent_finding.criterion.legal_regulations,
        finding_description=agent_finding.finding_description,
        disability_impact=agent_finding.disability_impact,
        element_selector=agent_finding.element_selector,
        confidence=agent_finding.confidence,
        status="confirmed" if agent_finding.status == "pending" else agent_finding.status,
        critique_verdict="CONFIRMED",  # placeholder until real critique runs
        critique_citation=agent_finding.criterion.criterion_text,  # placeholder
        impact_score=0,  # filled in later by calculate_impact_score
    )


async def run_real_agent_safely(agent_name: str, agent, input_data: AuditAgentInput) -> list:
    """
    Wraps a real agent's .audit() call with timeout + failure isolation.
    Converts his AgentFinding objects into our Finding contract shape.
    """
    try:
        raw_findings = await asyncio.wait_for(agent.audit(input_data), timeout=AGENT_TIMEOUT_SECONDS)
        return [convert_agent_finding_to_contract(f) for f in raw_findings]
    except asyncio.TimeoutError:
        logger.warning(f"Agent '{agent_name}' timed out after {AGENT_TIMEOUT_SECONDS}s")
        return []
    except Exception as e:
        logger.error(f"Agent '{agent_name}' failed: {e}")
        return []


async def run_fix_safely(finding, dom_html: str):
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
        # Build one AuditAgentInput per agent — full DOM as a single chunk for now
        """agent_input = AuditAgentInput(
            disability_class="visual",  # overridden per-agent below via separate inputs
            dom_chunk=payload.dom_html,
            url=payload.url,
            chunk_index=0,
            total_chunks=1,
        )"""

        # Step 1: run all 5 REAL agents in parallel
        tasks = []
        for name, agent in _agents.items():
            per_agent_input = AuditAgentInput(
                disability_class=name,
                dom_chunk=payload.dom_html,
                url=payload.url,
                chunk_index=0,
                total_chunks=1,
            )
            tasks.append(run_real_agent_safely(name, agent, per_agent_input))

        results = await asyncio.gather(*tasks)
        all_findings = [finding for agent_findings in results for finding in agent_findings]

        # Step 2: critique agent (still mocked — Sreekar hasn't built the real one yet)
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

        # Step 3: impact-weighting
        for f in reviewed_findings:
            f.impact_score = calculate_impact_score(f)

        # Step 4: real fix engine (Charan's)
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
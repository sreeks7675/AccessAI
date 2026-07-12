"""
Orchestrator pipeline: DOM payload -> agents -> critique -> fix engine -> report.
Owned by Mahesh.
"""

import asyncio
import logging
import os
from bs4 import BeautifulSoup
from backend.orchestrator.contracts import (
    DOMPayload, ReportJSON, AuditMetadata, Benchmark, FindingWithFix, Fix, Finding
)
from backend.fix_engine import FixEnginePipeline, FindingObject
from backend.agents.pipeline import dom_chunker
from backend.agents.visual_agent import VisualAgent
from backend.agents.auditory_agent import AuditoryAgent
from backend.agents.motor_agent import MotorAgent
from backend.agents.cognitive_agent import CognitiveAgent
from backend.agents.at_parsing_agent import ATPArsingAgent
from backend.agents.critique_agent import CritiqueAgent
from backend.agents.schemas import AuditAgentInput, CritiqueVerdict
from backend.rag.vector_store import WCAGVectorStore

logger = logging.getLogger("wcag-audit")

AGENT_TIMEOUT_SECONDS = int(os.getenv("AGENT_TIMEOUT_SECONDS", "80"))
CRITIQUE_TIMEOUT_SECONDS = int(os.getenv("CRITIQUE_TIMEOUT_SECONDS", "80"))
FIX_TIMEOUT_SECONDS = 15
AGENT_MAX_CONCURRENCY = int(os.getenv("AGENT_MAX_CONCURRENCY", "2"))
CRITIQUE_MAX_CONCURRENCY = int(os.getenv("CRITIQUE_MAX_CONCURRENCY", "2"))

fix_engine_pipeline = FixEnginePipeline()

_vector_store = WCAGVectorStore()

_agents = {
    "visual": VisualAgent(vector_store=_vector_store),
    "auditory": AuditoryAgent(vector_store=_vector_store),
    "motor": MotorAgent(vector_store=_vector_store),
    "cognitive": CognitiveAgent(vector_store=_vector_store),
    "at_parsing": ATPArsingAgent(vector_store=_vector_store),
}

_critique_agent = CritiqueAgent(vector_store=_vector_store)

_agent_semaphore = asyncio.Semaphore(max(1, AGENT_MAX_CONCURRENCY))
_critique_semaphore = asyncio.Semaphore(max(1, CRITIQUE_MAX_CONCURRENCY))


def calculate_impact_score(criterion_level: str, confidence: float, disability_class: str) -> int:
    level_weight = {"A": 40, "AA": 25, "AAA": 10}.get(criterion_level, 15)
    confidence_weight = confidence * 30
    prevalence_weight = {
        "visual": 20, "motor": 15, "cognitive": 15,
        "auditory": 10, "at_parsing": 20,
    }.get(disability_class, 10)
    return min(100, round(level_weight + confidence_weight + prevalence_weight))


def extract_element_html(dom_html: str, selector: str) -> str:
    try:
        soup = BeautifulSoup(dom_html, "html.parser")
        element = soup.select_one(selector)
        if element:
            return str(element)
    except Exception as e:
        logger.warning(f"Could not extract element for selector '{selector}': {e}")
    return f"<div>{selector}</div>"


async def run_real_agent_safely(agent_name: str, agent, input_data: AuditAgentInput) -> list:
    """Returns raw AgentFinding objects (his schema) — NOT converted yet."""
    try:
        async with _agent_semaphore:
            return await asyncio.wait_for(agent.audit(input_data), timeout=AGENT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(f"Agent '{agent_name}' timed out after {AGENT_TIMEOUT_SECONDS}s")
        return []
    except Exception as e:
        logger.error(f"Agent '{agent_name}' failed: {e}")
        return []


async def run_critique_safely(agent_finding, dom_chunk: str):
    """Wraps critique_agent.evaluate() with timeout + failure isolation."""
    try:
        async with _critique_semaphore:
            return await asyncio.wait_for(
                _critique_agent.evaluate(agent_finding, dom_chunk), timeout=CRITIQUE_TIMEOUT_SECONDS
            )
    except Exception as e:
        logger.error(f"Critique agent failed for finding {agent_finding.id}: {e}")
        return None  # treated as NEEDS_CONTEXT fallback below


def merge_finding_and_critique(agent_finding, critique_result) -> Finding | None:
    """
    Combines Sreekar's raw AgentFinding + CritiqueResult into our flat Finding
    contract. Returns None if the finding was REJECTED (dropped entirely) or
    if critique itself failed unexpectedly.
    """
    if critique_result is None:
        # Critique call failed outright — conservative fallback: needs_context
        verdict = "NEEDS_CONTEXT"
        citation = agent_finding.criterion.criterion_text
        status = "needs_context"
    else:
        verdict = critique_result.verdict.value if hasattr(critique_result.verdict, "value") else critique_result.verdict
        if verdict == "REJECTED":
            return None  # drop false positives entirely
        citation = critique_result.citation or critique_result.manual_review_instruction or agent_finding.criterion.criterion_text
        status = "confirmed" if verdict == "CONFIRMED" else "needs_context"

    disability_class = (
        agent_finding.disability_class.value
        if hasattr(agent_finding.disability_class, "value")
        else agent_finding.disability_class
    )

    impact_score = calculate_impact_score(
        agent_finding.criterion.criterion_level, agent_finding.confidence, disability_class
    )

    return Finding(
        id=agent_finding.id,
        disability_class=disability_class,
        criterion_number=agent_finding.criterion.criterion_number,
        criterion_level=agent_finding.criterion.criterion_level,
        criterion_text=agent_finding.criterion.criterion_text,
        legal_regulations=agent_finding.criterion.legal_regulations,
        finding_description=agent_finding.finding_description,
        disability_impact=agent_finding.disability_impact,
        element_selector=agent_finding.element_selector,
        confidence=agent_finding.confidence,
        status=status,
        critique_verdict=verdict,
        critique_citation=citation,
        impact_score=impact_score,
    )


async def run_fix_safely(finding: Finding, dom_html: str):
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
        # Some pages (for example PDF viewers or blocked documents) provide
        # effectively empty DOM payloads. Keep the pipeline resilient by
        # normalizing to a minimal HTML shell that satisfies agent schema.
        dom_html = (payload.dom_html or "").strip()
        if len(dom_html) < 10:
            dom_html = "<html></html>"

        chunk_result = dom_chunker({"full_dom": dom_html, "url": str(payload.url)})
        dom_chunks = chunk_result["dom_chunks"] or [dom_html]

        tasks = []
        task_chunks = []  # parallel list: which chunk each task's findings belong to
        for idx, chunk in enumerate(dom_chunks):
            for name, agent in _agents.items():
                agent_input = AuditAgentInput(
                    disability_class=name,
                    dom_chunk=chunk,
                    chunk_index=idx,
                    total_chunks=len(dom_chunks),
                    url=str(payload.url),
                )
                tasks.append(run_real_agent_safely(name, agent, agent_input))
                task_chunks.append(chunk)

        results = await asyncio.gather(*tasks)

        # Pair each finding with the exact chunk it was found in
        raw_finding_chunk_pairs = []
        for chunk, agent_findings in zip(task_chunks, results):
            for f in agent_findings:
                raw_finding_chunk_pairs.append((f, chunk))

        raw_agent_findings = [f for f, _ in raw_finding_chunk_pairs]
        finding_to_chunk = {f.id: chunk for f, chunk in raw_finding_chunk_pairs}
        # Step 2: critique every raw finding in parallel, using its own source chunk
        critique_results = await asyncio.gather(
            *[run_critique_safely(f, chunk) for f, chunk in raw_finding_chunk_pairs]
        )

        # Step 3: merge finding + critique verdict into our contract, drop REJECTED
        merged = [
            merge_finding_and_critique(f, c)
            for f, c in zip(raw_agent_findings, critique_results)
        ]
        reviewed_findings = [f for f in merged if f is not None]

        # Step 4: real fix engine, confirmed findings only
        confirmed = [f for f in reviewed_findings if f.status == "confirmed"]
        fixes = await asyncio.gather(*[run_fix_safely(f, finding_to_chunk[f.id]) for f in confirmed])

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
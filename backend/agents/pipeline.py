"""
backend/agents/pipeline.py
===========================
WS-3 — LangGraph Audit Pipeline

This file is the central assembly point of all WS-3 work.
It wires every component built so far into a LangGraph StateGraph:

    ┌─────────────────────────────────────────────────────────┐
    │                    PIPELINE GRAPH                       │
    │                                                         │
    │  START → [dom_chunker] → [run_agents] → [run_critique]  │
    │              │               │               │          │
    │         splits DOM      5 agents in     critique each  │
    │         into semantic   parallel via    finding at      │
    │         regions         asyncio.gather  temp=0.0        │
    │                                               │          │
    │                              ┌────────────────┤          │
    │                              ▼                ▼          │
    │                        [route_findings] ──────┤          │
    │                        conditional node       │          │
    │                              │                │          │
    │              ┌───────────────┼───────────────┐│          │
    │              ▼               ▼               ▼│          │
    │        CONFIRMED       NEEDS_CONTEXT      REJECTED       │
    │              │               │            (dropped)      │
    │              └───────────────┘                           │
    │                      │                                   │
    │               [assemble_report]                          │
    │               adds impact scores                         │
    │               packages IP-3 JSON                        │
    │                      │                                   │
    │                     END                                  │
    └─────────────────────────────────────────────────────────┘

State flows through PipelineState TypedDict. Every node receives the full
state and returns ONLY the keys it modified — LangGraph merges the rest.
Lists use Annotated[list, operator.add] so each node APPENDS, never overwrites.

Design Doc References:
    Section 2.3.1 — Audit Orchestrator (full pipeline flow)
    Section 2.4   — Critique Sub-Agent
    Section 5.2.2 — Confidence threshold guardrail
    Section 9.2   — Impact Weighting Formula (4-component)
    Master Doc Section 4.3 — Contract IP-3 (Backend → Extension JSON)

Author : Sreekar (WS-3)
"""

from __future__ import annotations

import asyncio
import json
import logging
import operator
import os
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langgraph.graph import END, StateGraph

from .at_parsing_agent import ATPArsingAgent
from .auditory_agent import AuditoryAgent
from .cognitive_agent import CognitiveAgent
from .critique_agent import CritiqueAgent
from .motor_agent import MotorAgent
from .schemas import (
    AgentFinding,
    AuditAgentInput,
    CritiqueResult,
    CritiqueVerdict,
    DisabilityClass,
)
from .visual_agent import VisualAgent
from ..common.finding_adapter import agent_finding_to_finding_object
from ..fix_engine import FixEnginePipeline
from ..rag.vector_store import WCAGVectorStore

load_dotenv()

logger = logging.getLogger("pipeline")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ── Environment ────────────────────────────────────────────────────────────────
VLLM_ENDPOINT   = os.getenv("VLLM_ENDPOINT", "http://localhost:8000")
CHROMA_DB_PATH  = os.getenv("CHROMA_DB_PATH", "./data/chroma")
TOOL_VERSION    = "1.0.0"
WCAG_VERSION    = "2.2"
CONFORMANCE_TARGET = "AA"

# ── DOM chunking constants ─────────────────────────────────────────────────────
# Semantic landmark elements that define natural chunk boundaries
LANDMARK_TAGS = ["header", "nav", "main", "aside", "footer", "form", "section", "article"]

# Fallback chunk size when no landmarks are found (50KB)
FALLBACK_CHUNK_BYTES = 50 * 1024

# ── Confidence guardrail threshold ────────────────────────────────────────────
# Findings below this go directly to NEEDS_CONTEXT without hitting critique agent
# Design Doc Section 5.2.2
CONFIDENCE_CONTEXT_THRESHOLD = 0.70


# ══════════════════════════════════════════════════════════════════════════════
# LANGGRAPH STATE DEFINITION
# ══════════════════════════════════════════════════════════════════════════════
#
# CRITICAL ARCHITECTURE NOTE:
# LangGraph 0.2 requires state to be a TypedDict (not a Pydantic model).
# We use Annotated[list, operator.add] for every list field so that when
# a node returns {"raw_findings": [new_finding]}, LangGraph APPENDS it to
# the existing list rather than replacing it.
#
# The PipelineState Pydantic model in schemas.py is used for documentation
# and IDE support — this TypedDict is the runtime state for LangGraph.
# ══════════════════════════════════════════════════════════════════════════════

class GraphState(TypedDict):
    """
    LangGraph runtime state — TypedDict with operator.add for list fields.

    Every list field uses Annotated[list, operator.add] so nodes can return
    partial lists that get APPENDED to the accumulated state, not replaced.

    Non-list fields (url, full_dom, etc.) use last-write-wins semantics —
    only the dom_chunker node writes these, so there's no conflict.
    """
    # ── Input (set before graph starts) ───────────────────────────────────────
    url:          str
    full_dom:     str
    dom_metadata: dict[str, Any]

    # ── Populated by dom_chunker ───────────────────────────────────────────────
    dom_chunks: Annotated[list[str], operator.add]

    # ── Populated by run_agents (5 agents append in parallel) ────────────────
    raw_findings: Annotated[list[dict], operator.add]

    # ── Populated by run_critique ─────────────────────────────────────────────
    critique_results: Annotated[list[dict], operator.add]

    # ── Populated by route_findings ───────────────────────────────────────────
    confirmed_findings:    Annotated[list[dict], operator.add]
    manual_review_findings: Annotated[list[dict], operator.add]

    # ── Populated by assemble_report ──────────────────────────────────────────
    final_report: dict[str, Any]

    # ── Pipeline tracking ─────────────────────────────────────────────────────
    errors: Annotated[list[str], operator.add]


def _empty_state(url: str = "", full_dom: str = "", dom_metadata: dict | None = None) -> GraphState:
    """
    Create a fresh empty GraphState.
    Used internally and in tests to initialise the graph invocation.
    """
    return GraphState(
        url=url,
        full_dom=full_dom,
        dom_metadata=dom_metadata or {},
        dom_chunks=[],
        raw_findings=[],
        critique_results=[],
        confirmed_findings=[],
        manual_review_findings=[],
        final_report={},
        errors=[],
    )


# ══════════════════════════════════════════════════════════════════════════════
# SHARED RESOURCES (initialised once, shared across all pipeline runs)
# ══════════════════════════════════════════════════════════════════════════════

# Single vector store instance — shared across all agents to avoid reloading
# the embedding model five times
_vector_store: WCAGVectorStore | None = None


def _get_vector_store() -> WCAGVectorStore:
    """
    Lazy-initialise the shared vector store.
    Called once on first pipeline run, then reused.
    """
    global _vector_store
    if _vector_store is None:
        logger.info("Initialising shared WCAGVectorStore...")
        _vector_store = WCAGVectorStore(chroma_path=CHROMA_DB_PATH)
        _vector_store.rebuild_if_empty()
        logger.info("WCAGVectorStore ready")
    return _vector_store


def _make_agents(vs: WCAGVectorStore) -> tuple[
    VisualAgent, AuditoryAgent, MotorAgent, CognitiveAgent, ATPArsingAgent, CritiqueAgent
]:
    """
    Instantiate all agents sharing the same vector store and vLLM endpoint.
    Called once per pipeline run to ensure clean HTTP client state.
    """
    return (
        VisualAgent   (vllm_endpoint=VLLM_ENDPOINT, vector_store=vs),
        AuditoryAgent (vllm_endpoint=VLLM_ENDPOINT, vector_store=vs),
        MotorAgent    (vllm_endpoint=VLLM_ENDPOINT, vector_store=vs),
        CognitiveAgent(vllm_endpoint=VLLM_ENDPOINT, vector_store=vs),
        ATPArsingAgent(vllm_endpoint=VLLM_ENDPOINT, vector_store=vs),
        CritiqueAgent (vllm_endpoint=VLLM_ENDPOINT, vector_store=vs),
    )


# ══════════════════════════════════════════════════════════════════════════════
# NODE 1 — dom_chunker
# ══════════════════════════════════════════════════════════════════════════════

def dom_chunker(state: GraphState) -> dict:
    """
    Split the full DOM into semantic chunks for agent consumption.

    Strategy 1 — Landmark-based chunking (preferred):
        Find all LANDMARK_TAGS (header, nav, main, aside, footer, form,
        section, article). Serialise each one as its own chunk.
        This gives agents focused, semantically meaningful DOM regions.

    Strategy 2 — Size-based fallback:
        If the page has no landmark elements (rare but happens with
        legacy or non-semantic HTML), split the full serialised DOM
        into FALLBACK_CHUNK_BYTES chunks with 20% overlap to prevent
        context being cut mid-element.

    Parameters
    ----------
    state : GraphState
        Must contain 'full_dom' (raw HTML string from content script).

    Returns
    -------
    dict
        {'dom_chunks': list[str]} — partial state update.
    """
    full_dom = state.get("full_dom", "")
    url      = state.get("url", "")

    if not full_dom:
        logger.warning("dom_chunker: empty full_dom for %s", url)
        return {"dom_chunks": [""], "errors": ["Empty DOM received — audit may be incomplete"]}

    logger.info("dom_chunker: parsing DOM for %s (%d bytes)", url, len(full_dom))

    try:
        soup = BeautifulSoup(full_dom, "lxml")
    except Exception as exc:
        logger.error("dom_chunker: BeautifulSoup parse failed: %s", exc)
        return {
            "dom_chunks": [full_dom[:FALLBACK_CHUNK_BYTES]],
            "errors": [f"DOM parse error: {exc} — using raw fallback chunk"],
        }

    # ── Strategy 1: landmark-based ────────────────────────────────────────────
    landmark_elements = []
    for tag in LANDMARK_TAGS:
        landmark_elements.extend(soup.find_all(tag))

    if landmark_elements:
        chunks = []
        for element in landmark_elements:
            serialised = str(element)
            if serialised.strip():
                chunks.append(serialised)

        logger.info(
            "dom_chunker: landmark strategy — %d chunks from %d landmark elements",
            len(chunks), len(landmark_elements),
        )
        return {"dom_chunks": chunks}

    # ── Strategy 2: size-based fallback ──────────────────────────────────────
    logger.info(
        "dom_chunker: no landmarks found — using %dKB size-based chunking",
        FALLBACK_CHUNK_BYTES // 1024,
    )
    full_text = str(soup)
    chunks    = []
    overlap   = int(FALLBACK_CHUNK_BYTES * 0.20)   # 20% overlap
    step      = FALLBACK_CHUNK_BYTES - overlap
    start     = 0

    while start < len(full_text):
        end = start + FALLBACK_CHUNK_BYTES
        chunks.append(full_text[start:end])
        start += step

    logger.info("dom_chunker: size fallback — %d chunks", len(chunks))
    return {"dom_chunks": chunks}


# ══════════════════════════════════════════════════════════════════════════════
# CHUNK ROUTING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _filter_chunks_for_agent(
    chunks: list[str],
    disability_class: DisabilityClass,
) -> list[str]:
    """
    Route the right DOM chunks to each disability agent.

    Design Doc Section 2.3.1 step 3 specifies which chunks each agent gets:
    - visual + at_parsing : ALL chunks (visual issues can appear anywhere)
    - auditory            : only chunks containing <audio>, <video>, <iframe>
    - motor               : chunks with interactive elements
    - cognitive           : chunks with text content and forms

    Returning ALL chunks as fallback is always safe — agents are designed
    to output [] when no relevant content is present.

    Parameters
    ----------
    chunks : list[str]
        All DOM chunks from dom_chunker.
    disability_class : DisabilityClass
        Which agent's filter to apply.

    Returns
    -------
    list[str]
        Filtered subset of chunks relevant to this disability class.
    """
    if not chunks:
        return []

    if disability_class in (DisabilityClass.VISUAL, DisabilityClass.AT_PARSING):
        # Visual and AT parsing issues can appear in any element
        return chunks

    if disability_class == DisabilityClass.AUDITORY:
        # Auditory agent only needs chunks with time-based media
        AUDIO_VIDEO_MARKERS = ("<audio", "<video", "<iframe", "<object", "<embed")
        return [c for c in chunks if any(m in c.lower() for m in AUDIO_VIDEO_MARKERS)] or chunks[:1]

    if disability_class == DisabilityClass.MOTOR:
        # Motor agent needs interactive elements
        INTERACTIVE_MARKERS = ("<a ", "<button", "<input", "<select", "<textarea",
                               "tabindex", "onclick", "draggable", "role=")
        return [c for c in chunks if any(m in c.lower() for m in INTERACTIVE_MARKERS)] or chunks[:1]

    if disability_class == DisabilityClass.COGNITIVE:
        # Cognitive agent needs text-heavy and form chunks
        COGNITIVE_MARKERS = ("<p", "<form", "<h1", "<h2", "<h3", "<ul", "<ol", "<table", "<label")
        return [c for c in chunks if any(m in c.lower() for m in COGNITIVE_MARKERS)] or chunks[:1]

    # Safe fallback — all chunks
    return chunks


# ══════════════════════════════════════════════════════════════════════════════
# NODE 2 — run_agents
# ══════════════════════════════════════════════════════════════════════════════

async def run_agents(state: GraphState) -> dict:
    """
    Run all five disability agents in parallel using asyncio.gather().

    Each agent receives only the DOM chunks relevant to its disability class
    (via _filter_chunks_for_agent). Within each agent, chunks are processed
    sequentially — the agent's audit() method handles one chunk at a time.

    All five agents run CONCURRENTLY at the top level. Total audit time is
    bounded by the slowest agent × its chunk count, not all agents summed.

    Parameters
    ----------
    state : GraphState
        Must contain 'dom_chunks' (from dom_chunker node).

    Returns
    -------
    dict
        {'raw_findings': list[dict]} — serialised AgentFinding objects.
    """
    chunks = state.get("dom_chunks", [])
    url    = state.get("url", "")

    if not chunks:
        logger.warning("run_agents: no DOM chunks available for %s", url)
        return {"raw_findings": [], "errors": ["run_agents: no chunks to process"]}

    logger.info("run_agents: starting 5 agents on %d chunks for %s", len(chunks), url)

    vs = _get_vector_store()
    visual, auditory, motor, cognitive, at_parsing, _ = _make_agents(vs)

    agent_configs = [
        (visual,     DisabilityClass.VISUAL),
        (auditory,   DisabilityClass.AUDITORY),
        (motor,      DisabilityClass.MOTOR),
        (cognitive,  DisabilityClass.COGNITIVE),
        (at_parsing, DisabilityClass.AT_PARSING),
    ]

    async def _run_single_agent(agent, disability_class: DisabilityClass) -> list[AgentFinding]:
        """Run one agent across its relevant DOM chunks, collect all findings."""
        agent_chunks = _filter_chunks_for_agent(chunks, disability_class)
        findings: list[AgentFinding] = []

        for idx, chunk in enumerate(agent_chunks):
            audit_input = AuditAgentInput(
                disability_class=disability_class,
                dom_chunk=chunk,
                url=url,
                chunk_index=idx,
                total_chunks=len(agent_chunks),
            )
            try:
                chunk_findings = await agent.audit(audit_input)
                findings.extend(chunk_findings)
            except Exception as exc:
                logger.error(
                    "run_agents: %s chunk %d/%d failed: %s",
                    agent.__class__.__name__, idx + 1, len(agent_chunks), exc,
                )

        await agent.close()
        logger.info(
            "run_agents: %s → %d findings across %d chunks",
            agent.__class__.__name__, len(findings), len(agent_chunks),
        )
        return findings

    # Launch all 5 agents concurrently
    all_results = await asyncio.gather(
        *[_run_single_agent(agent, dc) for agent, dc in agent_configs],
        return_exceptions=True,
    )

    # Collect findings, handling any agent-level exceptions
    all_findings: list[dict] = []
    errors: list[str] = []
    agent_names = ["visual", "auditory", "motor", "cognitive", "at_parsing"]

    for agent_name, result in zip(agent_names, all_results):
        if isinstance(result, Exception):
            err_msg = f"run_agents: {agent_name} agent raised exception: {result}"
            logger.error(err_msg)
            errors.append(err_msg)
        elif isinstance(result, list):
            # Serialise AgentFinding objects to dicts for LangGraph state
            for finding in result:
                if isinstance(finding, AgentFinding):
                    all_findings.append(finding.model_dump())
                elif isinstance(finding, dict):
                    all_findings.append(finding)

    logger.info(
        "run_agents: complete — %d total raw findings across all agents",
        len(all_findings),
    )

    output = {"raw_findings": all_findings}
    if errors:
        output["errors"] = errors
    return output


# ══════════════════════════════════════════════════════════════════════════════
# NODE 3 — run_critique
# ══════════════════════════════════════════════════════════════════════════════

async def run_critique(state: GraphState) -> dict:
    """
    Run the Critique Sub-Agent on every raw finding.

    Two-gate system (Design Doc Section 2.4, 5.2.2):

    Gate 1 — Confidence pre-filter (applied BEFORE critique agent):
        If finding.confidence < CONFIDENCE_CONTEXT_THRESHOLD (0.70):
        → Immediately route to NEEDS_CONTEXT without calling the critique agent.
        → This saves LLM inference cost for findings the disability agent
          itself was uncertain about.

    Gate 2 — Critique agent evaluation (for confident findings):
        CritiqueAgent.evaluate() independently verifies the finding against
        the WCAG vector store. Returns CONFIRMED, REJECTED, or NEEDS_CONTEXT.
        The critique agent re-fetches the criterion text independently —
        it never trusts the text in the finding (anti-hallucination guardrail).

    Parameters
    ----------
    state : GraphState
        Must contain 'raw_findings' and 'dom_chunks'.

    Returns
    -------
    dict
        {'critique_results': list[dict]} — serialised CritiqueResult objects.
    """
    raw_findings_dicts = state.get("raw_findings", [])
    dom_chunks         = state.get("dom_chunks", [])
    url                = state.get("url", "")

    if not raw_findings_dicts:
        logger.info("run_critique: no raw findings to critique for %s", url)
        return {"critique_results": []}

    logger.info(
        "run_critique: evaluating %d findings for %s",
        len(raw_findings_dicts), url,
    )

    vs = _get_vector_store()
    _, _, _, _, _, critique = _make_agents(vs)

    # Combine all chunks into one DOM context string for the critique agent
    # (it needs to see the full context to verify element_selector is real)
    dom_context = "\n\n".join(dom_chunks)[:8000]  # cap at 8KB for context window

    critique_results: list[dict] = []
    errors: list[str] = []

    for raw_dict in raw_findings_dicts:
        try:
            # Reconstruct AgentFinding from dict
            finding = AgentFinding(**raw_dict)
        except Exception as exc:
            logger.warning("run_critique: could not parse finding dict: %s", exc)
            errors.append(f"Finding parse error: {exc}")
            continue

        # ── Gate 1: Confidence pre-filter ─────────────────────────────────────
        if finding.confidence < CONFIDENCE_CONTEXT_THRESHOLD:
            logger.debug(
                "run_critique: finding %s confidence=%.2f < %.2f → NEEDS_CONTEXT (no critique call)",
                finding.id[:8], finding.confidence, CONFIDENCE_CONTEXT_THRESHOLD,
            )
            auto_result = CritiqueResult(
                finding_id=finding.id,
                verdict=CritiqueVerdict.NEEDS_CONTEXT,
                citation="",
                manual_review_instruction=(
                    f"This finding was automatically routed to manual review because "
                    f"the disability agent's confidence score ({finding.confidence:.2f}) "
                    f"is below the pipeline threshold of {CONFIDENCE_CONTEXT_THRESHOLD}. "
                    f"Original agent reason: {finding.needs_context_reason or 'N/A'}. "
                    f"Criterion: {finding.criterion.criterion_number} — "
                    f"{finding.criterion.criterion_text[:200]}. "
                    f"Test manually on element: {finding.element_selector}"
                ),
            )
            critique_results.append(auto_result.model_dump())
            continue

        # ── Gate 2: Critique agent evaluation ─────────────────────────────────
        try:
            result = await critique.evaluate(
                finding=finding,
                dom_chunk=dom_context,
            )
            logger.info(
                "run_critique: [%s] %s → %s | citation[:100]='%s'",
                finding.id[:8],
                finding.criterion.criterion_number,
                result.verdict,
                (result.citation or "")[:100],
            )
            critique_results.append(result.model_dump())

        except Exception as exc:
            logger.error(
                "run_critique: critique agent failed for finding %s: %s",
                finding.id[:8], exc,
            )
            # On unexpected error: safe-fail to NEEDS_CONTEXT rather than losing the finding
            safe_result = CritiqueResult(
                finding_id=finding.id,
                verdict=CritiqueVerdict.NEEDS_CONTEXT,
                citation="",
                manual_review_instruction=(
                    f"Critique agent encountered an error evaluating this finding: {exc}. "
                    f"Manual verification required. Criterion: "
                    f"{finding.criterion.criterion_number} on element {finding.element_selector}"
                ),
            )
            critique_results.append(safe_result.model_dump())
            errors.append(f"Critique error for {finding.id[:8]}: {exc}")

    await critique.close()
    logger.info(
        "run_critique: complete — %d results (confirmed+rejected+needs_context)",
        len(critique_results),
    )

    output: dict = {"critique_results": critique_results}
    if errors:
        output["errors"] = errors
    return output


# ══════════════════════════════════════════════════════════════════════════════
# NODE 4 — route_findings (conditional routing node)
# ══════════════════════════════════════════════════════════════════════════════

def route_findings(state: GraphState) -> dict:
    """
    Conditional node: distribute findings based on CritiqueVerdict.

    Routes each finding to one of three outcomes:
        CONFIRMED     → confirmed_findings list (shown in main report)
        NEEDS_CONTEXT → manual_review_findings list (shown with MANUAL_REVIEW badge)
        REJECTED      → silently dropped (logged at DEBUG, never shown to user)

    This node also cross-references raw_findings with critique_results by
    finding_id to attach the critique verdict to each finding before routing.

    Parameters
    ----------
    state : GraphState
        Must contain 'raw_findings' and 'critique_results'.

    Returns
    -------
    dict
        Partial state with 'confirmed_findings' and 'manual_review_findings'.
    """
    raw_findings_dicts    = state.get("raw_findings", [])
    critique_results_dicts = state.get("critique_results", [])
    url                   = state.get("url", "")

    logger.info(
        "route_findings: routing %d findings using %d critique results for %s",
        len(raw_findings_dicts), len(critique_results_dicts), url,
    )

    # Build lookup: finding_id → CritiqueResult dict
    critique_by_id: dict[str, dict] = {
        cr["finding_id"]: cr
        for cr in critique_results_dicts
        if isinstance(cr, dict) and "finding_id" in cr
    }

    confirmed:     list[dict] = []
    manual_review: list[dict] = []
    rejected_count = 0

    for raw_dict in raw_findings_dicts:
        if not isinstance(raw_dict, dict):
            continue

        finding_id = raw_dict.get("id", "")
        critique   = critique_by_id.get(finding_id)

        if critique is None:
            # No critique result for this finding — treat as NEEDS_CONTEXT
            logger.debug(
                "route_findings: no critique result for finding %s — routing to manual review",
                finding_id[:8] if finding_id else "unknown",
            )
            enriched = {**raw_dict, "critique_verdict": "NEEDS_CONTEXT",
                        "critique_citation": "",
                        "manual_review_instruction": "No critique result found — manual verification required"}
            manual_review.append(enriched)
            continue

        verdict = critique.get("verdict", "NEEDS_CONTEXT")

        # Enrich the finding with critique data before routing
        enriched = {
            **raw_dict,
            "critique_verdict":           verdict,
            "critique_citation":          critique.get("citation", ""),
            "critique_rejection_reason":  critique.get("rejection_reason"),
            "manual_review_instruction":  critique.get("manual_review_instruction"),
        }

        if verdict == CritiqueVerdict.CONFIRMED:
            enriched["status"] = "confirmed"
            confirmed.append(enriched)
            logger.debug(
                "route_findings: CONFIRMED [%s] %s",
                finding_id[:8], raw_dict.get("criterion", {}).get("criterion_number", "?"),
            )

        elif verdict == CritiqueVerdict.NEEDS_CONTEXT:
            enriched["status"] = "needs_context"
            manual_review.append(enriched)
            logger.debug(
                "route_findings: NEEDS_CONTEXT [%s]",
                finding_id[:8],
            )

        elif verdict == CritiqueVerdict.REJECTED:
            rejected_count += 1
            logger.debug(
                "route_findings: REJECTED [%s] reason='%s'",
                finding_id[:8],
                critique.get("rejection_reason", "")[:80],
            )
            # REJECTED findings are silently dropped — not added to any output list

        else:
            # Unknown verdict — safe-fail to manual review
            logger.warning(
                "route_findings: unknown verdict '%s' for finding %s — routing to manual review",
                verdict, finding_id[:8],
            )
            enriched["status"] = "needs_context"
            manual_review.append(enriched)

    logger.info(
        "route_findings: confirmed=%d | manual_review=%d | rejected=%d",
        len(confirmed), len(manual_review), rejected_count,
    )

    return {
        "confirmed_findings":     confirmed,
        "manual_review_findings": manual_review,
    }


# ══════════════════════════════════════════════════════════════════════════════
# IMPACT SCORING (Design Doc Section 9.2 — 4-Component Formula)
# ══════════════════════════════════════════════════════════════════════════════

# Component weights (must sum to 1.0)
_W_POPULATION  = 0.40   # affected user population weight
_W_BLOCK       = 0.30   # block severity weight
_W_PAGE        = 0.20   # page criticality weight
_W_LEGAL       = 0.10   # legal exposure weight

# Affected population weights by disability class
# Source: WebAIM Screen Reader Survey 2023 + CDC disability prevalence data
_POPULATION_WEIGHTS: dict[str, float] = {
    "visual":     0.85,   # ~2.2% blind, ~8% low vision + colour blind, ageing vision
    "auditory":   0.90,   # ~15% hearing loss including age-related (largest group)
    "motor":      0.75,   # ~8% motor disability affecting web use
    "cognitive":  0.95,   # ~20% some form of cognitive/learning disability
    "at_parsing": 0.85,   # screen reader users (subset of visual + motor)
}

# Block severity by finding status and confidence
def _compute_block_severity(finding: dict) -> float:
    """
    0.30 = friction (slows user)
    0.60 = barrier (makes it significantly harder)
    1.00 = blocker (prevents task completion)
    """
    criterion_num = finding.get("criterion", {}).get("criterion_number", "")
    confidence    = finding.get("confidence", 0.5)

    # Criteria that typically represent complete blockers
    BLOCKERS = {"1.1.1", "1.2.2", "1.3.1", "2.1.1", "2.1.2", "4.1.2", "4.1.3"}
    BARRIERS = {"1.4.3", "1.4.11", "2.4.1", "2.4.3", "2.4.7", "3.3.1", "3.3.2"}

    if criterion_num in BLOCKERS:
        return min(1.00, confidence * 1.05)
    if criterion_num in BARRIERS:
        return min(0.80, confidence * 0.90)
    return min(0.60, confidence * 0.70)


def _compute_page_criticality(url: str, finding: dict) -> float:
    """
    0.4 = low criticality (footer, decorative region)
    0.7 = medium (navigation, content)
    1.0 = high criticality (checkout, login, main content)

    Inferred from URL path segments and element_selector.
    """
    url_lower      = url.lower()
    selector_lower = finding.get("element_selector", "").lower()

    HIGH_PATTERNS = ("checkout", "login", "signup", "payment", "auth",
                     "account", "cart", "register", "submit")
    LOW_PATTERNS  = ("footer", "cookie", "banner", "ad-", "widget")

    if any(p in url_lower or p in selector_lower for p in HIGH_PATTERNS):
        return 1.0
    if any(p in url_lower or p in selector_lower for p in LOW_PATTERNS):
        return 0.4
    return 0.7


def _compute_legal_exposure(finding: dict) -> float:
    """
    0.0 = no known case law for this criterion
    0.1 = criterion implicated in legal cases
    0.2 = criterion that has been won by plaintiffs in multiple cases

    Based on aggregated case law database (Design Doc Section 8.2).
    Most-litigated WCAG criteria per WebAIM litigation surveys.
    """
    criterion_num = finding.get("criterion", {}).get("criterion_number", "")

    # Most frequently litigated criteria (ADA Title III lawsuits 2018-2024)
    HIGH_LITIGATION = {"1.1.1", "2.4.4", "1.4.3", "2.1.1", "4.1.2", "1.3.1"}
    MED_LITIGATION  = {"2.4.1", "2.4.2", "3.3.2", "1.2.2", "2.4.7", "2.4.3"}

    if criterion_num in HIGH_LITIGATION:
        return 0.2
    if criterion_num in MED_LITIGATION:
        return 0.1
    return 0.0


def compute_impact_score(finding: dict, url: str) -> int:
    """
    Compute the 4-component impact score (0–100) for a confirmed finding.

    Design Doc Section 9.2 formula:
        Impact Score = (
            population_weight  × W_POPULATION  +
            block_severity     × W_BLOCK        +
            page_criticality   × W_PAGE         +
            legal_exposure     × W_LEGAL
        ) × 100

    Parameters
    ----------
    finding : dict
        Enriched confirmed finding dict (includes criterion, confidence, etc.).
    url : str
        Page URL — used for page criticality inference.

    Returns
    -------
    int
        Impact score 0–100. Higher = fix first.
    """
    disability_class = finding.get("disability_class", "visual")

    pop_weight   = _POPULATION_WEIGHTS.get(disability_class, 0.75)
    block_sev    = _compute_block_severity(finding)
    page_crit    = _compute_page_criticality(url, finding)
    legal_exp    = _compute_legal_exposure(finding)

    raw_score = (
        pop_weight * _W_POPULATION +
        block_sev  * _W_BLOCK      +
        page_crit  * _W_PAGE       +
        legal_exp  * _W_LEGAL
    )

    return min(100, max(0, int(raw_score * 100)))


def _default_fix_payload(reason: str) -> dict[str, Any]:
    """Fallback payload that preserves the IP-3 fix object contract."""
    return {
        "patch_html": None,
        "patch_validated": False,
        "diff_html": None,
        "preview_srcdoc": None,
        "requires_human_review": True,
        "review_reason": reason,
    }


def _extract_original_element_html(full_dom: str, selector: str) -> str | None:
    """Best-effort extraction of the target element from full DOM using CSS selector."""
    if not full_dom or not selector:
        return None

    try:
        soup = BeautifulSoup(full_dom, "html.parser")
        node = soup.select_one(selector)
    except Exception:
        return None

    if node is None:
        return None

    return str(node)


def _build_benchmark_summary(url: str, our_findings_count: int, errors: list[str]) -> dict[str, Any]:
    """Best-effort benchmark enrichment using backend.evaluation.benchmark."""
    summary: dict[str, Any] = {
        "axe_findings": None,
        "wave_findings": None,
        "our_findings": our_findings_count,
        "unique_findings": None,
        "note": "Benchmark integration unavailable",
    }

    if not url:
        summary["note"] = "Benchmark skipped: missing URL"
        return summary

    try:
        from ..evaluation.benchmark import count_violations, run_axe_on_url

        axe_results = run_axe_on_url(url)
        axe_breakdown = count_violations(axe_results)
        summary["axe_findings"] = axe_breakdown
        summary["note"] = "Axe benchmark populated from evaluation module"
    except Exception as exc:
        logger.warning("assemble_report: benchmark enrichment failed: %s", exc)
        errors.append(f"Benchmark enrichment failed: {exc}")
        summary["note"] = f"Benchmark fallback used: {exc}"

    return summary


def _build_news_preview(errors: list[str], max_items: int = 5) -> list[dict[str, Any]]:
    """Best-effort accessibility news/legal preview using backend.news modules."""
    try:
        from ..news.aggregator import (
            fetch_courtlistener_cases,
            fetch_feed,
            filter_relevant,
        )
        from ..news.summariser import summarize_article

        w3c_feed = "https://www.w3.org/blog/news/feed/"
        feed_entries = fetch_feed(w3c_feed)
        relevant_feed_entries = filter_relevant(feed_entries)
        legal_entries = fetch_courtlistener_cases(max_results=max_items)

        combined = [*relevant_feed_entries, *legal_entries][:max_items]
        preview: list[dict[str, Any]] = []

        for item in combined:
            try:
                enriched = summarize_article(item)
                preview.append(enriched)
            except Exception as exc:
                logger.warning("assemble_report: failed to summarize news item: %s", exc)
                fallback = dict(item)
                fallback["ai_summary"] = f"Summary unavailable: {exc}"
                preview.append(fallback)

        return preview
    except Exception as exc:
        logger.warning("assemble_report: news enrichment failed: %s", exc)
        errors.append(f"News enrichment failed: {exc}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# NODE 5 — assemble_report
# ══════════════════════════════════════════════════════════════════════════════

def assemble_report(state: GraphState) -> dict:
    """
    Add impact scores to confirmed findings and package into the IP-3 report JSON.

    IP-3 Contract (Master GitHub Strategy Document Section 4.3):
    {
        audit_metadata: { url, timestamp, wcag_version, spa_detected, ... },
        findings: [
            ...confirmed findings with impact_score, fix placeholder...
        ],
        manual_review: [...needs_context findings...],
        benchmark: { axe_findings: null, wave_findings: null, our_findings: N, unique: N },
        news_preview: [],
        disclaimer: "standard text"
    }

    Parameters
    ----------
    state : GraphState

    Returns
    -------
    dict
        {'final_report': dict} — complete IP-3 report ready to send to extension.
    """
    confirmed     = state.get("confirmed_findings", [])
    manual_review = state.get("manual_review_findings", [])
    raw_findings  = state.get("raw_findings", [])
    url           = state.get("url", "")
    dom_metadata  = state.get("dom_metadata", {})
    errors        = list(state.get("errors", []))
    full_dom      = state.get("full_dom", "")

    logger.info(
        "assemble_report: packaging %d confirmed + %d manual_review findings for %s",
        len(confirmed), len(manual_review), url,
    )

    # ── Add impact scores and generate fixes for confirmed findings ───────────
    fix_engine = FixEnginePipeline()
    findings_with_scores: list[dict] = []
    for finding in confirmed:
        enriched = dict(finding)
        enriched["impact_score"] = compute_impact_score(finding, url)

        try:
            finding_obj = agent_finding_to_finding_object(
                enriched,
                {
                    "verdict": enriched.get("critique_verdict", "CONFIRMED"),
                    "citation": enriched.get("critique_citation", ""),
                },
                impact_score=float(enriched["impact_score"]),
            )

            original_element_html = _extract_original_element_html(
                full_dom,
                finding_obj.element_selector,
            )

            if not original_element_html:
                enriched["fix"] = _default_fix_payload(
                    f"Could not locate element in DOM for selector: {finding_obj.element_selector}"
                )
            else:
                fix = fix_engine.run(
                    finding_obj,
                    original_element_html,
                    page_computed_css="",
                )
                enriched["fix"] = fix.to_dict()

        except Exception as exc:
            finding_id = str(enriched.get("id", "unknown"))[:8]
            logger.warning(
                "assemble_report: fix generation failed for finding %s: %s",
                finding_id,
                exc,
            )
            errors.append(f"Fix engine error for {finding_id}: {exc}")
            enriched["fix"] = _default_fix_payload(f"Fix generation failed: {exc}")

        enriched.setdefault("fix", _default_fix_payload("Fix not generated"))
        findings_with_scores.append(enriched)

    # Sort by impact_score descending — highest impact first
    findings_with_scores.sort(key=lambda f: f.get("impact_score", 0), reverse=True)

    # ── Build audit metadata ───────────────────────────────────────────────────
    audit_metadata = {
        "url":                    url,
        "timestamp":              datetime.now(timezone.utc).isoformat(),
        "wcag_version":           WCAG_VERSION,
        "conformance_level_target": CONFORMANCE_TARGET,
        "tool_version":           TOOL_VERSION,
        "pipeline_version":       "1.0.0",
        "spa_detected":           dom_metadata.get("spa_detected", False),
        "dom_size_bytes":         dom_metadata.get("dom_size_bytes", 0),
        "page_title":             dom_metadata.get("page_title", ""),
        "lang_attribute":         dom_metadata.get("lang_attribute", ""),
        "total_findings_raw":     len(raw_findings),
        "total_findings_confirmed": len(findings_with_scores),
        "total_manual_review":    len(manual_review),
        "pipeline_errors":        len(errors),
    }

    # ── Compliance risk summary ────────────────────────────────────────────────
    # Quick breakdown by disability class and conformance level for the UI
    by_class: dict[str, int] = {}
    by_level: dict[str, int] = {"A": 0, "AA": 0, "AAA": 0}
    for f in findings_with_scores:
        dc = f.get("disability_class", "unknown")
        by_class[dc] = by_class.get(dc, 0) + 1
        level = f.get("criterion", {}).get("criterion_level", "A")
        by_level[level] = by_level.get(level, 0) + 1

    # ── Legal regulation coverage ──────────────────────────────────────────────
    all_regulations: set[str] = set()
    for f in findings_with_scores:
        regs = f.get("criterion", {}).get("legal_regulations", [])
        all_regulations.update(regs)

    # ── Standard disclaimer (Design Doc Section 5.2.3) ────────────────────────
    disclaimer = (
        "Automated accessibility audits identify approximately 30–40% of WCAG violations. "
        "This report does not guarantee full WCAG conformance and does not constitute "
        "legal advice. Manual testing with assistive technology users is required for "
        "complete coverage. Formal conformance certification requires a qualified "
        "accessibility professional (CPWA, CPACC). Findings are sorted by estimated "
        "user impact, not WCAG conformance level."
    )

    benchmark_summary = _build_benchmark_summary(url, len(findings_with_scores), errors)
    news_preview = _build_news_preview(errors)

    # ── Assemble final IP-3 report ─────────────────────────────────────────────
    final_report: dict[str, Any] = {
        "audit_metadata":    audit_metadata,
        "findings":          findings_with_scores,
        "manual_review":     manual_review,
        "summary": {
            "by_disability_class": by_class,
            "by_conformance_level": by_level,
            "regulations_implicated": sorted(all_regulations),
            "highest_impact_score": (
                findings_with_scores[0].get("impact_score", 0)
                if findings_with_scores else 0
            ),
        },
        "benchmark": benchmark_summary,
        "news_preview": news_preview,
        "disclaimer":    disclaimer,
        "pipeline_errors": errors,
    }

    logger.info(
        "assemble_report: complete — %d confirmed findings, impact scores %s...",
        len(findings_with_scores),
        [f.get("impact_score") for f in findings_with_scores[:3]],
    )

    return {"final_report": final_report}


# ══════════════════════════════════════════════════════════════════════════════
# GRAPH ASSEMBLY
# ══════════════════════════════════════════════════════════════════════════════

def _build_graph() -> StateGraph:
    """
    Wire all nodes into the LangGraph StateGraph.

    Node wiring:
        START → dom_chunker → run_agents → run_critique
            → route_findings → assemble_report → END

    Note on routing:
        route_findings is NOT a conditional node in LangGraph's sense —
        it reads ALL critique results and populates BOTH confirmed_findings
        and manual_review_findings in one pass. The "conditional" routing
        is implemented INSIDE the node logic (if/elif verdict).

        We use add_conditional_edges only to show the routing decision
        structure in the graph visualisation, pointing both branches to
        assemble_report.

    Returns
    -------
    StateGraph
        Compiled graph ready to call .compile() on.
    """
    graph = StateGraph(GraphState)

    # ── Register nodes ─────────────────────────────────────────────────────────
    graph.add_node("dom_chunker",     dom_chunker)
    graph.add_node("run_agents",      run_agents)
    graph.add_node("run_critique",    run_critique)
    graph.add_node("route_findings",  route_findings)
    graph.add_node("assemble_report", assemble_report)

    # ── Wire edges ─────────────────────────────────────────────────────────────
    graph.set_entry_point("dom_chunker")
    graph.add_edge("dom_chunker",    "run_agents")
    graph.add_edge("run_agents",     "run_critique")
    graph.add_edge("run_critique",   "route_findings")

    # Conditional edges from route_findings:
    # The router function always returns "assemble" — both CONFIRMED and
    # NEEDS_CONTEXT findings are accumulated in state by route_findings node
    # itself. The conditional edge here makes the routing VISIBLE in the graph
    # and allows future extension (e.g. routing blockers to a fast-path report).
    def _routing_decision(state: GraphState) -> str:
        confirmed = state.get("confirmed_findings", [])
        manual    = state.get("manual_review_findings", [])
        if not confirmed and not manual:
            logger.info("route_findings decision: no findings → assemble_report")
        return "assemble"  # always proceed to assemble_report

    graph.add_conditional_edges(
        "route_findings",
        _routing_decision,
        {"assemble": "assemble_report"},
    )

    graph.add_edge("assemble_report", END)

    return graph


# ── Compile once at module load ────────────────────────────────────────────────
_graph    = _build_graph()
_compiled = _graph.compile()

logger.info("LangGraph pipeline compiled — nodes: %s", list(_graph.nodes.keys()))


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

async def run_audit(dom_payload: dict) -> dict:
    """
    Run a complete WCAG accessibility audit on a DOM payload.

    This is the ONLY public function that Mahesh's orchestrator (WS-2) calls.
    Everything else in this file is internal to the pipeline.

    Parameters
    ----------
    dom_payload : dict
        DOM payload from the browser extension content script,
        matching Contract IP-1 (Extension → Backend):
        {
            "url":             "https://example.com",
            "dom_html":        "<html>...</html>",
            "computed_styles": { "button.submit": { "color": "#fff", ... } },
            "meta": {
                "spa_detected":    false,
                "dom_size_bytes":  45200,
                "page_title":      "Example Domain",
                "lang_attribute":  "en"
            }
        }

    Returns
    -------
    dict
        Complete IP-3 report JSON matching Contract IP-3 (Backend → Extension).
        Keys: audit_metadata, findings, manual_review, summary, benchmark,
              news_preview, disclaimer, pipeline_errors.

    Raises
    ------
    ValueError
        If dom_payload is missing required 'url' or 'dom_html' fields.
    """
    # ── Validate input ─────────────────────────────────────────────────────────
    url      = dom_payload.get("url", "").strip()
    dom_html = dom_payload.get("dom_html", "").strip()

    if not url:
        raise ValueError("dom_payload missing required field: 'url'")
    if not dom_html:
        raise ValueError("dom_payload missing required field: 'dom_html'")

    meta = dom_payload.get("meta", {})

    logger.info("=" * 60)
    logger.info("run_audit: starting audit for %s", url)
    logger.info("  DOM size : %d bytes", len(dom_html))
    logger.info("  SPA      : %s", meta.get("spa_detected", False))
    logger.info("=" * 60)

    # ── Build initial state ────────────────────────────────────────────────────
    initial_state = _empty_state(
        url=url,
        full_dom=dom_html,
        dom_metadata=meta,
    )

    # ── Run the graph ──────────────────────────────────────────────────────────
    try:
        final_state = await _compiled.ainvoke(initial_state)
    except Exception as exc:
        logger.exception("run_audit: pipeline failed with unhandled exception: %s", exc)
        # Return a minimal error report rather than raising — the extension
        # must always receive a response it can render
        return {
            "audit_metadata": {
                "url":        url,
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "wcag_version": WCAG_VERSION,
                "tool_version": TOOL_VERSION,
                "error":      str(exc),
            },
            "findings":      [],
            "manual_review": [],
            "summary":       {},
            "benchmark":     {},
            "news_preview":  [],
            "disclaimer":    "Audit failed due to an internal error. Please retry.",
            "pipeline_errors": [str(exc)],
        }

    # ── Extract final report ───────────────────────────────────────────────────
    final_report = final_state.get("final_report", {})

    if not final_report:
        logger.error("run_audit: assemble_report produced empty final_report")
        final_report = {
            "audit_metadata": {"url": url, "error": "Empty report produced"},
            "findings": [],
            "manual_review": [],
            "pipeline_errors": final_state.get("errors", []),
        }

    confirmed_count    = len(final_report.get("findings", []))
    manual_count       = len(final_report.get("manual_review", []))
    pipeline_err_count = len(final_report.get("pipeline_errors", []))

    logger.info("=" * 60)
    logger.info("run_audit: COMPLETE for %s", url)
    logger.info("  Confirmed    : %d findings", confirmed_count)
    logger.info("  Manual review: %d findings", manual_count)
    logger.info("  Errors       : %d", pipeline_err_count)
    logger.info("=" * 60)

    return final_report


# ══════════════════════════════════════════════════════════════════════════════
# WS-3 HEALTH CHECK — called by Mahesh's orchestrator before accepting requests
# ══════════════════════════════════════════════════════════════════════════════

class WS3HealthCheck:
    """
    Health check for WS-3 components.
    Mahesh calls WS3HealthCheck().check() before routing audit requests here.
    """

    def check(self) -> dict:
        """
        Verify all WS-3 dependencies are reachable and loaded.

        Returns
        -------
        dict
            {
                status: "ok" | "error",
                vllm_reachable: bool,
                vector_store_loaded: bool,
                wcag_criteria_count: int,
                graph_compiled: bool,
                errors: list[str]
            }
        """
        errors: list[str] = []

        # Check vLLM endpoint reachability (sync HTTP)
        import httpx as _httpx
        vllm_reachable = False
        try:
            r = _httpx.get(f"{VLLM_ENDPOINT}/v1/models", timeout=5.0)
            vllm_reachable = r.status_code == 200
        except Exception as e:
            errors.append(f"vLLM not reachable at {VLLM_ENDPOINT}: {e}")

        # Check vector store
        vector_store_loaded = False
        wcag_criteria_count = 0
        try:
            vs = _get_vector_store()
            stats = vs.collection_stats()
            wcag_criteria_count = sum(stats.values())
            vector_store_loaded = wcag_criteria_count > 0
            if not vector_store_loaded:
                errors.append("Vector store collections are empty — run wcag_loader first")
        except Exception as e:
            errors.append(f"Vector store error: {e}")

        # Check graph compiled
        graph_compiled = _compiled is not None

        status = "ok" if (vector_store_loaded and graph_compiled and not errors) else "degraded"
        if not vllm_reachable:
            # vLLM unreachable is critical — agents cannot run
            status = "error"

        return {
            "status":               status,
            "vllm_reachable":       vllm_reachable,
            "vector_store_loaded":  vector_store_loaded,
            "wcag_criteria_count":  wcag_criteria_count,
            "graph_compiled":       graph_compiled,
            "errors":               errors,
        }


# ══════════════════════════════════════════════════════════════════════════════
# CLI — run a quick local test audit
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    Quick smoke test — runs a minimal audit on a known bad HTML snippet.

    From repo root:
        python -m backend.agents.pipeline

    Expected: 1-3 confirmed findings (missing alt text, contrast, etc.)
    Note: requires vLLM running at VLLM_ENDPOINT.
    """
    import sys

    TEST_HTML = """
    <!DOCTYPE html>
    <html lang="en">
    <head><title>Test Page</title></head>
    <body>
      <header>
        <nav><a href="/">Home</a> | <a href="/about">About</a></nav>
      </header>
      <main>
        <h1>Welcome</h1>
        <img src="hero.jpg" class="banner">
        <p style="color: #888888; background: #ffffff;">
          Our utilisation of asymmetric cryptographic methodologies necessitates
          the implementation of certificate authority hierarchies.
        </p>
        <button style="outline: none;">Submit</button>
        <form>
          <input type="email" placeholder="Enter email">
        </form>
      </main>
      <footer><p>Copyright 2026</p></footer>
    </body>
    </html>
    """

    payload = {
        "url":      "https://test.example.com/",
        "dom_html": TEST_HTML,
        "meta": {
            "spa_detected":   False,
            "dom_size_bytes": len(TEST_HTML),
            "page_title":     "Test Page",
            "lang_attribute": "en",
        },
    }

    print("\n🔍 Running pipeline smoke test...\n")
    report = asyncio.run(run_audit(payload))

    confirmed    = report.get("findings", [])
    manual       = report.get("manual_review", [])
    meta         = report.get("audit_metadata", {})
    errors       = report.get("pipeline_errors", [])

    print(f"✅ Audit complete for: {meta.get('url')}")
    print(f"   Confirmed findings : {len(confirmed)}")
    print(f"   Manual review      : {len(manual)}")
    print(f"   Pipeline errors    : {len(errors)}")

    if confirmed:
        print("\nTop findings (by impact score):")
        for f in confirmed[:3]:
            crit = f.get("criterion", {})
            print(f"  [{crit.get('criterion_number')}] "
                  f"{crit.get('criterion_title','?')} "
                  f"(Level {crit.get('criterion_level','?')}) "
                  f"— impact: {f.get('impact_score')}/100")

    if errors:
        print(f"\n⚠  Errors: {errors}")

    sys.exit(0 if not errors else 1)
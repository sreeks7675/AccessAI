"""
WS-3 — Base Accessibility Agent

All five disability-specialist agents inherit from BaseAccessibilityAgent.
This class handles everything that is identical across all agents:

    1. Constructor — receives disability_class, vLLM endpoint, vector store
    2. _get_relevant_criteria() — queries the vector store for this agent's
       domain and the current DOM chunk
    3. _build_user_message() — formats the DOM chunk for the LLM
    4. audit() — the main async method:
         a. retrieve relevant criteria from vector store
         b. call _build_system_prompt() (abstract — subclass provides this)
         c. POST to vLLM /v1/chat/completions with guided JSON decoding
         d. parse response into list[AgentFinding] via Pydantic
         e. apply confidence gate guardrail
         f. retry up to 3x on malformed JSON

Subclasses ONLY need to implement _build_system_prompt(criteria).
Everything else is inherited.

Why abstract _build_system_prompt?
    Each disability agent has a different framing, different AT context,
    different few-shot examples, and different WCAG criterion scope. The
    system prompt is the primary differentiator between agents. Keeping it
    abstract forces each subclass to be explicit about its domain.

Author : Sreekar (WS-3)
Design : Section 2.3.2 of WCAG Audit Agent Design Document v1.0
"""

from __future__ import annotations

import abc
import json
import logging
import os
from typing import Any, Optional

import httpx
from dotenv import load_dotenv

from .schemas import (
    AgentFinding,
    AuditAgentInput,
    CritiqueVerdict,
    DisabilityClass,
    WCAGCriterion,
)
from ..rag.vector_store import WCAGVectorStore

load_dotenv()

logger = logging.getLogger("base_agent")

# ── Constants ──────────────────────────────────────────────────────────────────

# Default vLLM endpoint — override via .env VLLM_ENDPOINT
DEFAULT_VLLM_ENDPOINT = os.getenv("VLLM_ENDPOINT", "http://localhost:8000")

# Model name as registered in vLLM — must match --model flag in vLLM startup command
VLLM_MODEL = os.getenv("VLLM_MODEL", "mistralai/Mistral-7B-Instruct-v0.3")

# Max tokens for agent response — enough for a JSON array of ~8 findings
MAX_TOKENS = 4096

# Temperature 0.1 for slight variation while staying deterministic enough
# for structured output. Critique agent uses 0.0 — see critique_agent.py
AGENT_TEMPERATURE = 0.1

# Confidence thresholds (from Design Doc Section 5.2.2)
CONFIDENCE_DROP_THRESHOLD   = 0.50   # findings below this are silently dropped
CONFIDENCE_CONTEXT_THRESHOLD = 0.70  # findings below this become NEEDS_CONTEXT

# Retry config
MAX_JSON_RETRIES = 3

# Number of WCAG criteria to retrieve from vector store per DOM chunk
N_CRITERIA_TO_RETRIEVE = 8


# ── BaseAccessibilityAgent ─────────────────────────────────────────────────────

class BaseAccessibilityAgent(abc.ABC):
    """
    Abstract base class for all five disability-specialist audit agents.

    Concrete subclasses:
        VisualAgent       → backend/agents/visual_agent.py
        AuditoryAgent     → backend/agents/auditory_agent.py
        MotorAgent        → backend/agents/motor_agent.py
        CognitiveAgent    → backend/agents/cognitive_agent.py
        ATPArsingAgent    → backend/agents/at_parsing_agent.py

    Parameters
    ----------
    disability_class : DisabilityClass
        The domain this agent is responsible for.
    vllm_endpoint : str
        Base URL of the vLLM inference server
        (e.g. 'http://GPU_CLUSTER_IP:8000').
    vector_store : WCAGVectorStore
        Shared vector store instance. Pass the same instance to all agents
        to avoid re-loading the embedding model five times.
    """

    def __init__(
        self,
        disability_class: DisabilityClass,
        vllm_endpoint: str = DEFAULT_VLLM_ENDPOINT,
        vector_store: Optional[WCAGVectorStore] = None,
    ) -> None:
        self.disability_class = disability_class
        self.vllm_endpoint    = vllm_endpoint.rstrip("/")
        self.vector_store     = vector_store or WCAGVectorStore()

        # Reusable async HTTP client — connection pooling across audit() calls
        # timeout: 120s because LLM inference can be slow on first token
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

        logger.info(
            "Initialised %s | endpoint: %s",
            self.__class__.__name__, self.vllm_endpoint,
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _get_relevant_criteria(self, dom_chunk: str) -> list[dict]:
        """
        Query the vector store for the top N criteria relevant to this agent's
        disability class and the current DOM content.

        We combine the disability class name with a short summary of the DOM
        chunk as the query. This means an auditory agent processing a chunk
        with a <video> element will retrieve caption-related criteria first.

        Parameters
        ----------
        dom_chunk : str
            The HTML chunk being audited.

        Returns
        -------
        list[dict]
            Up to N_CRITERIA_TO_RETRIEVE criterion dicts from the vector store.
        """
        # Build a semantic query from the DOM chunk
        # Truncate to first 500 chars — enough context for semantic matching
        dom_preview = dom_chunk[:500].replace("\n", " ").strip()
        query = (
            f"Accessibility barriers for {self.disability_class.value} disability users. "
            f"DOM content: {dom_preview}"
        )

        try:
            results = self.vector_store.retrieve_criteria(
                disability_class=self.disability_class.value,
                query=query,
                n_results=N_CRITERIA_TO_RETRIEVE,
            )
            logger.debug(
                "%s retrieved %d criteria from vector store",
                self.__class__.__name__, len(results),
            )
            return results
        except Exception as exc:
            logger.warning(
                "%s failed to retrieve criteria: %s — using empty list",
                self.__class__.__name__, exc,
            )
            return []

    def _format_criteria_for_prompt(self, criteria: list[dict]) -> str:
        """
        Format the retrieved criteria into a readable block for the system prompt.

        The LLM needs to see the criteria inline in the system prompt so it can
        directly cite them. We format as a numbered list with number, level, and
        full criterion text.

        Parameters
        ----------
        criteria : list[dict]
            Criteria dicts from the vector store.

        Returns
        -------
        str
            Formatted string block ready for insertion into system prompt.
        """
        if not criteria:
            return "No specific criteria retrieved. Use your WCAG 2.2 knowledge."

        lines: list[str] = []
        for i, c in enumerate(criteria, 1):
            num   = c.get("criterion_number", "?")
            title = c.get("criterion_title", "Unknown")
            level = c.get("conformance_level", "?")
            text  = c.get("criterion_text", "No text available")
            lines.append(
                f"{i}. [{num}] {title} (Level {level})\n"
                f"   Criterion text: {text}\n"
                f"   Legal: {', '.join(c.get('legal_regulations', []))}"
            )

        return "\n\n".join(lines)

    def _build_user_message(self, dom_chunk: str, url: str) -> str:
        """
        Format the DOM chunk as the user turn of the LLM conversation.

        We include the URL for context and wrap the DOM in clear delimiters
        so the LLM doesn't confuse DOM content with instructions.

        Parameters
        ----------
        dom_chunk : str
            HTML chunk to audit.
        url : str
            Source page URL.

        Returns
        -------
        str
            Formatted user message.
        """
        return (
            f"Page URL: {url}\n\n"
            f"DOM CHUNK TO AUDIT:\n"
            f"```html\n{dom_chunk}\n```\n\n"
            "Identify all accessibility violations in this DOM chunk. "
            "Output ONLY a valid JSON array of finding objects. "
            "If there are no violations, output an empty array: []"
        )

    def _parse_llm_response(self, raw_text: str) -> list[dict]:
        """
        Parse the raw LLM text response into a list of finding dicts.

        Handles common LLM output quirks:
        - JSON wrapped in markdown code fences (```json ... ```)
        - Leading/trailing whitespace or explanation text
        - Single finding dict instead of array (wraps it)

        Parameters
        ----------
        raw_text : str
            Raw text from the LLM completion.

        Returns
        -------
        list[dict]
            Parsed list of finding dicts.

        Raises
        ------
        ValueError
            If the text cannot be parsed as JSON after cleanup.
        """
        text = raw_text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json or ```) and last line (```)
            text = "\n".join(lines[1:-1]).strip()

        # Find the JSON array boundaries
        start = text.find("[")
        end   = text.rfind("]")

        if start != -1 and end != -1:
            text = text[start:end + 1]
        elif text.startswith("{"):
            # Single object — wrap in array
            text = f"[{text}]"
        else:
            raise ValueError(
                f"Response does not contain a JSON array. "
                f"First 200 chars: {raw_text[:200]}"
            )

        parsed = json.loads(text)   # raises json.JSONDecodeError on failure
        if not isinstance(parsed, list):
            parsed = [parsed]

        return parsed

    def _apply_confidence_guardrails(
        self,
        findings: list[AgentFinding],
    ) -> list[AgentFinding]:
        """
        Apply the two confidence-based guardrails from Design Doc Section 5.2.2.

        Guardrail 1 — Drop threshold (confidence < 0.5):
            Finding is silently dropped. Too uncertain to be useful even for
            manual review. Logged at DEBUG so it's traceable but not noisy.

        Guardrail 2 — Context threshold (0.5 ≤ confidence < 0.7):
            Finding is kept but marked as needs_context. The AgentFinding
            model_validator auto-populates needs_context_reason — we just
            ensure status is correct here.

        Note: AgentFinding's model_validator already sets needs_context_reason
        for confidence < 0.70. This method handles the DROP case which the
        model can't handle (you can't return None from a validator).

        Parameters
        ----------
        findings : list[AgentFinding]
            Raw findings from the LLM, already parsed into Pydantic objects.

        Returns
        -------
        list[AgentFinding]
            Filtered and status-tagged findings.
        """
        output: list[AgentFinding] = []

        for f in findings:
            if f.confidence < CONFIDENCE_DROP_THRESHOLD:
                logger.debug(
                    "Dropping finding %s (confidence=%.2f < %.2f)",
                    f.id[:8], f.confidence, CONFIDENCE_DROP_THRESHOLD,
                )
                continue   # silently drop

            if f.confidence < CONFIDENCE_CONTEXT_THRESHOLD:
                f.status = "needs_context"
                # needs_context_reason is already set by AgentFinding model_validator

            output.append(f)

        logger.debug(
            "%s guardrails: %d in → %d out (dropped %d)",
            self.__class__.__name__, len(findings), len(output),
            len(findings) - len(output),
        )
        return output

    def _dict_to_agent_finding(
        self,
        raw: dict,
        attempt: int,
    ) -> Optional[AgentFinding]:
        """
        Convert a raw dict from the LLM into a validated AgentFinding.

        Handles field name variations that LLMs sometimes produce:
        - 'level' instead of 'conformance_level'
        - 'selector' instead of 'element_selector'
        - 'impact' instead of 'disability_impact'
        - Missing optional fields

        Parameters
        ----------
        raw : dict
            Raw dict parsed from LLM JSON output.
        attempt : int
            Current retry attempt number (for logging).

        Returns
        -------
        AgentFinding or None
            None if the dict cannot be coerced into a valid AgentFinding.
        """
        try:
            # Normalise common field name variations from LLM output
            criterion_data = raw.get("criterion", {})

            # Handle flat structure (LLM put criterion fields at top level)
            if not criterion_data and "criterion_number" in raw:
                criterion_data = {
                    "criterion_number": raw.get("criterion_number"),
                    "conformance_level": (
                        raw.get("criterion_level")
                        or raw.get("conformance_level")
                        or raw.get("level", "A")
                    ),
                    "criterion_text": raw.get("criterion_text", ""),
                    "legal_regulations": raw.get("legal_regulations", []),
                }

            # Normalise conformance_level aliases
            if "level" in criterion_data and "conformance_level" not in criterion_data:
                criterion_data["conformance_level"] = criterion_data.pop("level")

            criterion = WCAGCriterion(**criterion_data)

            finding = AgentFinding(
                disability_class=self.disability_class,
                criterion=criterion,
                finding_description=raw.get("finding_description") or raw.get("description", ""),
                disability_impact=raw.get("disability_impact") or raw.get("impact", ""),
                element_selector=(
                    raw.get("element_selector")
                    or raw.get("selector")
                    or raw.get("css_selector", "body")
                ),
                confidence=float(raw.get("confidence", 0.5)),
                needs_context_reason=raw.get("needs_context_reason") or raw.get("manual_review_reason"),
            )
            return finding

        except Exception as exc:
            logger.debug(
                "Could not parse finding dict (attempt %d): %s | raw: %s",
                attempt, exc, str(raw)[:200],
            )
            return None

    async def _call_vllm(
        self,
        system_prompt: str,
        user_message: str,
        extra_error_context: str = "",
    ) -> str:
        """
        Make an async POST request to the vLLM OpenAI-compatible endpoint.

        Uses guided JSON decoding (response_format={"type": "json_object"})
        so the model is constrained to valid JSON output at the sampler level.
        This is more reliable than asking the model to "output only JSON" in
        the prompt alone.

        Parameters
        ----------
        system_prompt : str
            The agent's system prompt (built by _build_system_prompt).
        user_message : str
            The user turn (DOM chunk + instructions).
        extra_error_context : str
            Additional context appended when retrying after a parse failure.

        Returns
        -------
        str
            Raw response text from the LLM.

        Raises
        ------
        httpx.HTTPError
            On network or HTTP errors from the vLLM endpoint.
        RuntimeError
            If the vLLM response is missing expected fields.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ]

        # Append error context for retry attempts
        if extra_error_context:
            messages.append({
                "role": "user",
                "content": (
                    f"CORRECTION NEEDED: Your previous response caused this error: "
                    f"{extra_error_context}\n"
                    "Please output a valid JSON array only. No markdown, no prose."
                ),
            })

        payload = {
            "model":           VLLM_MODEL,
            "messages":        messages,
            "max_tokens":      MAX_TOKENS,
            "temperature":     AGENT_TEMPERATURE,
            "response_format": {"type": "json_object"},   # guided decoding
        }

        response = await self._http_client.post(
            f"{self.vllm_endpoint}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(
                f"vLLM returned empty choices. Full response: {data}"
            )

        return choices[0]["message"]["content"]

    # ── Abstract interface ─────────────────────────────────────────────────────

    @abc.abstractmethod
    def _build_system_prompt(self, criteria: list[dict]) -> str:
        """
        Build the system prompt for this agent.

        Each disability agent provides its own implementation with:
        - Domain framing (who this agent is, what disability it covers)
        - AT context (which assistive technologies users of this disability use)
        - Inline WCAG criteria (the formatted block from _format_criteria_for_prompt)
        - Few-shot examples specific to this disability domain
        - Output format specification (JSON schema)

        Parameters
        ----------
        criteria : list[dict]
            The top N WCAG criteria retrieved from the vector store for this
            DOM chunk. Format with _format_criteria_for_prompt() and embed
            inline in the system prompt.

        Returns
        -------
        str
            Complete system prompt string ready to send to the LLM.
        """
        ...

    # ── Main public method ─────────────────────────────────────────────────────

    async def audit(self, input: AuditAgentInput) -> list[AgentFinding]:
        """
        Run an accessibility audit on one DOM chunk.

        This is the method called by pipeline.py's run_agents node.
        The orchestrator calls this concurrently for all 5 agents via
        asyncio.gather().

        Pipeline inside audit():
            1. Retrieve relevant WCAG criteria from vector store
            2. Build system prompt (subclass implementation)
            3. Build user message (DOM chunk + instructions)
            4. Call vLLM endpoint (with retry loop)
            5. Parse JSON response into AgentFinding objects
            6. Apply confidence guardrails
            7. Return filtered findings

        Parameters
        ----------
        input : AuditAgentInput
            Contains disability_class, dom_chunk, url, chunk_index.

        Returns
        -------
        list[AgentFinding]
            Validated findings that passed confidence guardrails.
            May be empty if the DOM chunk has no relevant violations
            or all findings were below confidence threshold.
        """
        logger.info(
            "%s auditing chunk %d/%d from %s",
            self.__class__.__name__,
            input.chunk_index + 1,
            input.total_chunks,
            input.url[:60],
        )

        # Step 1: Retrieve relevant criteria
        criteria = await self._get_relevant_criteria(input.dom_chunk)
        criteria_text = self._format_criteria_for_prompt(criteria)

        # Step 2: Build prompts
        system_prompt = self._build_system_prompt(criteria)
        user_message  = self._build_user_message(input.dom_chunk, input.url)

        # Step 3: Call LLM with retry loop
        raw_findings: list[dict] = []
        last_error = ""

        for attempt in range(1, MAX_JSON_RETRIES + 1):
            try:
                raw_text = await self._call_vllm(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    extra_error_context=last_error if attempt > 1 else "",
                )
                raw_findings = self._parse_llm_response(raw_text)
                logger.debug(
                    "%s attempt %d: parsed %d raw findings",
                    self.__class__.__name__, attempt, len(raw_findings),
                )
                break   # success — exit retry loop

            except json.JSONDecodeError as exc:
                last_error = f"JSON parse error: {exc}"
                logger.warning(
                    "%s attempt %d/%d JSON error: %s",
                    self.__class__.__name__, attempt, MAX_JSON_RETRIES, exc,
                )
                if attempt == MAX_JSON_RETRIES:
                    logger.error(
                        "%s exhausted retries — returning empty findings for this chunk",
                        self.__class__.__name__,
                    )
                    return []

            except httpx.HTTPError as exc:
                logger.error(
                    "%s HTTP error calling vLLM: %s — returning empty findings",
                    self.__class__.__name__, exc,
                )
                return []

            except Exception as exc:
                logger.error(
                    "%s unexpected error (attempt %d): %s",
                    self.__class__.__name__, attempt, exc,
                )
                last_error = str(exc)
                if attempt == MAX_JSON_RETRIES:
                    return []

        # Step 4: Parse raw dicts into AgentFinding Pydantic objects
        validated_findings: list[AgentFinding] = []
        for raw in raw_findings:
            finding = self._dict_to_agent_finding(raw, attempt=1)
            if finding is not None:
                validated_findings.append(finding)

        if len(validated_findings) < len(raw_findings):
            logger.warning(
                "%s: %d/%d findings failed Pydantic validation",
                self.__class__.__name__,
                len(raw_findings) - len(validated_findings),
                len(raw_findings),
            )

        # Step 5: Apply confidence guardrails
        filtered_findings = self._apply_confidence_guardrails(validated_findings)

        logger.info(
            "%s chunk %d complete: %d findings (raw=%d, validated=%d, filtered=%d)",
            self.__class__.__name__,
            input.chunk_index,
            len(filtered_findings),
            len(raw_findings),
            len(validated_findings),
            len(filtered_findings),
        )

        return filtered_findings

    async def close(self) -> None:
        """
        Close the HTTP client. Call this when the agent is no longer needed.
        In practice, the pipeline manages agent lifecycle — this is called
        by pipeline.py cleanup logic.
        """
        await self._http_client.aclose()
        logger.debug("%s HTTP client closed", self.__class__.__name__)
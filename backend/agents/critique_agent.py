"""
WS-3 — Critique Sub-Agent (Quality Gate)

The Critique Agent is the most important agent in the pipeline.
It receives EVERY finding from the five disability agents and must
independently verify it before it enters the confirmed findings list.

Design Doc Reference: Section 2.4 (entire section — read it in full)

HOW IT DIFFERS FROM THE FIVE DISABILITY AGENTS:
  - Disability agents GENERATE findings from DOM analysis.
  - The critique agent VALIDATES findings against the vector store.
  - It never trusts the criterion text in the finding — it re-fetches
    independently so it cannot be fooled by a hallucinated citation.
  - It outputs one of exactly three verdict types — nothing else.
  - Temperature is 0.0 — fully deterministic, no creativity.

THE HARD RULE (Design Doc Section 2.4.2):
  If verdict is CONFIRMED but verbatim_criterion_text is empty or absent,
  the verdict is overridden to REJECTED in code — before it ever leaves
  this class. This cannot be bypassed. The Pydantic CritiqueResult model
  also enforces this as a second layer of defence.

Author : Sreekar (WS-3)
Design : Section 2.4 — Critique Sub-Agent Architecture Detail
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx
from dotenv import load_dotenv

from .schemas import AgentFinding, CritiqueResult, CritiqueVerdict
from ..rag.vector_store import WCAGVectorStore

load_dotenv()

logger = logging.getLogger("critique_agent")

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_VLLM_ENDPOINT = os.getenv("VLLM_ENDPOINT", "http://localhost:8000")
VLLM_MODEL            = os.getenv("VLLM_MODEL", "mistralai/Mistral-7B-Instruct-v0.3")

# Temperature MUST be 0.0 — deterministic output for a quality-gate role
CRITIQUE_TEMPERATURE = 0.0

# Max tokens — critique output is compact (one verdict JSON object)
MAX_TOKENS = 1024

# Maximum retries on JSON parse failure
MAX_RETRIES = 3

# Minimum citation length to be considered valid (Design Doc Section 2.4.2)
MIN_CITATION_LENGTH = 20


# ── CritiqueAgent ──────────────────────────────────────────────────────────────

class CritiqueAgent:
    """
    Quality gate that validates every AgentFinding before it enters the report.

    Workflow inside evaluate():
        1. Re-fetch the WCAG criterion text from the vector store by number.
           (Never trust the criterion text in the finding itself.)
        2. Build a system prompt framing the LLM as a strict QA reviewer.
        3. Build a user message with the finding claim + DOM context.
        4. Call vLLM at temperature 0.0 (deterministic).
        5. Parse the JSON response into one of three verdict structures.
        6. Apply the HARD RULE: if CONFIRMED with empty citation → override to REJECTED.
        7. Construct and return a validated CritiqueResult.

    Parameters
    ----------
    vllm_endpoint : str
        Base URL of the vLLM inference server.
    vector_store : WCAGVectorStore
        The shared WCAG knowledge base — used to independently re-fetch
        criterion text by number.
    """

    def __init__(
        self,
        vllm_endpoint: str = DEFAULT_VLLM_ENDPOINT,
        vector_store: Optional[WCAGVectorStore] = None,
    ) -> None:
        self.vllm_endpoint = vllm_endpoint.rstrip("/")
        self.vector_store  = vector_store or WCAGVectorStore()

        # Reusable async HTTP client
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

        logger.info(
            "CritiqueAgent initialised | endpoint: %s | temperature: %.1f",
            self.vllm_endpoint, CRITIQUE_TEMPERATURE,
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _fetch_criterion_from_store(self, criterion_number: str) -> Optional[dict]:
        """
        Re-fetch the WCAG criterion from the vector store by number.

        This is the KEY architectural decision of the critique agent:
        it never uses the criterion text inside the AgentFinding because
        that text could have been hallucinated by the disability agent.
        Instead, it independently retrieves the ground-truth text from
        the vector store.

        Parameters
        ----------
        criterion_number : str
            Criterion number e.g. "1.4.3".

        Returns
        -------
        dict or None
            Criterion dict with criterion_text, criterion_title, etc.
            Returns None if the number is not found in the store.
        """
        try:
            criterion = self.vector_store.get_criterion_by_number(criterion_number)
            logger.debug(
                "CritiqueAgent: fetched criterion %s from vector store: '%s...'",
                criterion_number,
                criterion.get("criterion_text", "")[:60],
            )
            return criterion
        except KeyError:
            logger.warning(
                "CritiqueAgent: criterion '%s' not found in vector store — "
                "will route to NEEDS_CONTEXT",
                criterion_number,
            )
            return None
        except Exception as exc:
            logger.error(
                "CritiqueAgent: error fetching criterion '%s': %s",
                criterion_number, exc,
            )
            return None

    def _build_system_prompt(
        self,
        criterion_number: str,
        criterion_text: str,
        criterion_title: str,
        criterion_level: str,
    ) -> str:
        """
        Build the critique agent system prompt.

        The prompt establishes strict QA reviewer identity, provides the
        ground-truth criterion text (from the vector store, not the finding),
        and specifies the exact three JSON output structures.

        Parameters
        ----------
        criterion_number : str
            e.g. "1.4.3"
        criterion_text : str
            Verbatim text re-fetched from the vector store.
        criterion_title : str
            Human-readable title e.g. "Contrast (Minimum)".
        criterion_level : str
            "A", "AA", or "AAA".

        Returns
        -------
        str
            Complete system prompt.
        """
        return f"""You are a strict web accessibility QA reviewer.
Your ONLY job is to evaluate whether an accessibility finding is valid,
given the DOM content and the official WCAG criterion.

You are NOT a creative agent. You do NOT generate findings.
You ONLY judge whether an existing finding claim is correct.

THE WCAG CRITERION UNDER REVIEW (fetched from authoritative source):
  Number : {criterion_number}
  Title  : {criterion_title}
  Level  : {criterion_level}
  Text   : "{criterion_text}"

YOUR VERDICT must be exactly ONE of these three JSON objects:

OPTION A — If the finding is valid and the DOM clearly violates the criterion:
{{
  "verdict": "CONFIRMED",
  "verbatim_criterion_text": "<copy the criterion text above EXACTLY — word for word>",
  "mechanism_of_failure": "<one sentence: how exactly does the DOM element violate this criterion>",
  "affected_element": "<CSS selector of the violating element, repeated from the finding>"
}}

OPTION B — If the finding is a false positive (criterion is NOT violated):
{{
  "verdict": "REJECTED",
  "rejection_reason": "<specific explanation: why the criterion is actually met, or why this DOM pattern is exempt>"
}}

OPTION C — If you cannot determine validity from static DOM alone (runtime/AT testing needed):
{{
  "verdict": "NEEDS_CONTEXT",
  "manual_review_instruction": "<specific instruction: WHAT to test, HOW to test it, WHAT tool or AT to use, WHY static analysis is insufficient>"
}}

HARD RULES — violating these will cause your output to be rejected:
1. Output ONLY one of the three JSON objects above. No other text. No arrays.
2. For CONFIRMED: verbatim_criterion_text MUST be a copy of the criterion text above.
   Do NOT paraphrase it. Copy it character-for-character.
3. For REJECTED: rejection_reason must explain the specific DOM reason — not just say
   "the criterion is met" without explanation.
4. For NEEDS_CONTEXT: manual_review_instruction must name the specific AT tool or
   testing method needed — not just say "manual testing required."
5. Never output CONFIRMED if you have any doubt. NEEDS_CONTEXT is always acceptable
   when static DOM analysis is insufficient.
6. Your entire output must be valid JSON — no markdown fences, no prose."""

    def _build_user_message(
        self,
        finding: AgentFinding,
        dom_chunk: str,
    ) -> str:
        """
        Format the finding claim and DOM context as the user turn.

        Parameters
        ----------
        finding : AgentFinding
            The finding to be evaluated.
        dom_chunk : str
            The DOM chunk the finding was generated from.

        Returns
        -------
        str
            Formatted user message for the LLM.
        """
        return (
            f"FINDING TO EVALUATE:\n"
            f"  Finding ID        : {finding.id}\n"
            f"  Criterion claimed : {finding.criterion.criterion_number}\n"
            f"  Finding description: {finding.finding_description}\n"
            f"  Disability impact : {finding.disability_impact}\n"
            f"  Element selector  : {finding.element_selector}\n"
            f"  Agent confidence  : {finding.confidence}\n\n"
            f"DOM CHUNK WHERE THIS FINDING WAS GENERATED:\n"
            f"```html\n{dom_chunk[:3000]}\n```\n\n"
            "Is this finding valid? Output exactly one of the three JSON verdict objects."
        )

    def _parse_llm_response(self, raw_text: str) -> dict:
        """
        Parse the LLM's response into a verdict dict.

        Handles markdown fences and common formatting quirks.
        Extracts only the first JSON object found.

        Parameters
        ----------
        raw_text : str
            Raw LLM output text.

        Returns
        -------
        dict
            Parsed verdict dict with at least a "verdict" key.

        Raises
        ------
        ValueError
            If no valid JSON object can be extracted.
        """
        text = raw_text.strip()

        # Strip markdown fences
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]).strip()

        # Extract the first complete JSON object
        start = text.find("{")
        end   = text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(
                f"No JSON object found in LLM response. "
                f"First 200 chars: {raw_text[:200]}"
            )

        json_str = text[start:end + 1]
        parsed = json.loads(json_str)

        if "verdict" not in parsed:
            raise ValueError(
                f"Response JSON missing 'verdict' key. Got keys: {list(parsed.keys())}"
            )

        return parsed

    def _apply_hard_rule(self, verdict_dict: dict, finding_id: str) -> dict:
        """
        Apply the HARD RULE from Design Doc Section 2.4.2:
        CONFIRMED with empty or missing verbatim_criterion_text → override to REJECTED.

        This runs in code BEFORE constructing the CritiqueResult so the override
        is logged explicitly. CritiqueResult's model_validator is a second layer.

        Parameters
        ----------
        verdict_dict : dict
            Parsed verdict from the LLM.
        finding_id : str
            Finding UUID for logging.

        Returns
        -------
        dict
            Possibly-modified verdict dict.
        """
        if verdict_dict.get("verdict") != "CONFIRMED":
            return verdict_dict

        citation = verdict_dict.get("verbatim_criterion_text", "").strip()

        if not citation or len(citation) < MIN_CITATION_LENGTH:
            logger.warning(
                "HARD RULE triggered — finding %s: verdict was CONFIRMED but "
                "verbatim_criterion_text is '%s' (len=%d < %d). "
                "Overriding to REJECTED.",
                finding_id[:8], citation[:30], len(citation), MIN_CITATION_LENGTH,
            )
            verdict_dict["verdict"] = "REJECTED"
            verdict_dict["rejection_reason"] = (
                "Critique agent failed to provide a valid verbatim WCAG citation. "
                "Finding automatically rejected per Design Doc Section 2.4.2 hard rule. "
                f"(Citation provided was: '{citation}')"
            )

        return verdict_dict

    def _dict_to_critique_result(
        self,
        verdict_dict: dict,
        finding: AgentFinding,
    ) -> CritiqueResult:
        """
        Convert a parsed verdict dict into a validated CritiqueResult.

        Maps the three verdict structures to CritiqueResult fields:
        - CONFIRMED → citation from verbatim_criterion_text
        - REJECTED  → rejection_reason
        - NEEDS_CONTEXT → manual_review_instruction

        Parameters
        ----------
        verdict_dict : dict
            The parsed and hard-rule-checked verdict dict.
        finding : AgentFinding
            The original finding (used for finding_id linkage).

        Returns
        -------
        CritiqueResult
            Validated Pydantic model — CritiqueResult's own model_validators
            provide a second layer of citation enforcement.
        """
        verdict_str = verdict_dict.get("verdict", "REJECTED")

        # Map verdict string to CritiqueVerdict enum value
        try:
            verdict = CritiqueVerdict(verdict_str)
        except ValueError:
            logger.warning(
                "Unknown verdict value '%s' from LLM — defaulting to REJECTED",
                verdict_str,
            )
            verdict = CritiqueVerdict.REJECTED

        return CritiqueResult(
            finding_id=finding.id,
            verdict=verdict,
            # CONFIRMED fields
            citation=verdict_dict.get("verbatim_criterion_text", ""),
            # REJECTED fields
            rejection_reason=verdict_dict.get("rejection_reason"),
            # NEEDS_CONTEXT fields
            manual_review_instruction=verdict_dict.get("manual_review_instruction"),
        )

    async def _call_vllm(
        self,
        system_prompt: str,
        user_message: str,
        extra_error_context: str = "",
    ) -> str:
        """
        POST to the vLLM /v1/chat/completions endpoint.

        Temperature is always 0.0 for the critique agent — deterministic
        output is essential for a quality-gate role. Same finding evaluated
        twice must produce the same verdict.

        Parameters
        ----------
        system_prompt : str
        user_message : str
        extra_error_context : str
            Appended on retry with the previous error description.

        Returns
        -------
        str
            Raw LLM response text.

        Raises
        ------
        httpx.HTTPError
            On network or HTTP failures.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ]

        if extra_error_context:
            messages.append({
                "role": "user",
                "content": (
                    f"Your previous response caused a parse error: {extra_error_context}\n"
                    "Output ONLY a single valid JSON object matching one of the three "
                    "verdict structures. No markdown, no prose, nothing else."
                ),
            })

        payload = {
            "model":           VLLM_MODEL,
            "messages":        messages,
            "max_tokens":      MAX_TOKENS,
            "temperature":     CRITIQUE_TEMPERATURE,    # 0.0 — fully deterministic
            "response_format": {"type": "json_object"}, # guided JSON decoding
        }

        response = await self._http_client.post(
            f"{self.vllm_endpoint}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()

        data    = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(
                f"vLLM returned empty choices for critique call. Response: {data}"
            )

        return choices[0]["message"]["content"]

    # ── Public API ─────────────────────────────────────────────────────────────

    async def evaluate(
        self,
        finding: AgentFinding,
        dom_chunk: str,
    ) -> CritiqueResult:
        """
        Evaluate one AgentFinding and return a CritiqueResult verdict.

        This is the method called by pipeline.py's run_critique node,
        once per finding in raw_findings.

        Pipeline:
            1. Re-fetch WCAG criterion from vector store by number
               (independent of the criterion text in the finding)
            2. If criterion not found → NEEDS_CONTEXT immediately
            3. Build system prompt with ground-truth criterion text
            4. Build user message with finding claim + DOM context
            5. Call vLLM at temperature 0.0 (retry up to MAX_RETRIES)
            6. Parse JSON response
            7. Apply HARD RULE (CONFIRMED without citation → REJECTED)
            8. Construct and return CritiqueResult

        Parameters
        ----------
        finding : AgentFinding
            The finding to evaluate. finding.id links this result back.
        dom_chunk : str
            The DOM chunk the finding was generated from. Critique agent
            sees the same DOM context the disability agent saw.

        Returns
        -------
        CritiqueResult
            One of:
            - CONFIRMED: finding is valid, citation is verbatim WCAG text
            - REJECTED:  finding is a false positive
            - NEEDS_CONTEXT: cannot determine from static DOM alone
        """
        criterion_number = finding.criterion.criterion_number
        logger.info(
            "CritiqueAgent evaluating finding %s | criterion %s | agent confidence %.2f",
            finding.id[:8], criterion_number, finding.confidence,
        )

        # ── Step 1: Re-fetch criterion from vector store ───────────────────────
        stored_criterion = self._fetch_criterion_from_store(criterion_number)

        if stored_criterion is None:
            # Criterion number not in our knowledge base — cannot verify
            logger.warning(
                "CritiqueAgent: criterion '%s' not in vector store — "
                "routing finding %s to NEEDS_CONTEXT",
                criterion_number, finding.id[:8],
            )
            result = CritiqueResult(
                finding_id=finding.id,
                verdict=CritiqueVerdict.NEEDS_CONTEXT,
                manual_review_instruction=(
                    f"WCAG criterion {criterion_number} could not be found in the "
                    "knowledge base. Manually verify: (1) confirm this criterion number "
                    "is valid in WCAG 2.1 or 2.2; (2) if valid, verify the finding "
                    "against the official WCAG spec at https://www.w3.org/TR/WCAG22/; "
                    "(3) update wcag_criteria.json if the criterion is missing."
                ),
            )
            self._log_verdict(result, finding.id)
            return result

        # Extract ground-truth criterion fields
        gt_criterion_text  = stored_criterion.get("criterion_text", "")
        gt_criterion_title = stored_criterion.get("criterion_title", f"Criterion {criterion_number}")
        gt_criterion_level = stored_criterion.get("conformance_level", "A")

        # ── Step 2: Build prompts ──────────────────────────────────────────────
        system_prompt = self._build_system_prompt(
            criterion_number=criterion_number,
            criterion_text=gt_criterion_text,
            criterion_title=gt_criterion_title,
            criterion_level=gt_criterion_level,
        )
        user_message = self._build_user_message(finding, dom_chunk)

        # ── Step 3: Call vLLM with retry loop ──────────────────────────────────
        last_error    = ""
        verdict_dict  = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                raw_text     = await self._call_vllm(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    extra_error_context=last_error if attempt > 1 else "",
                )
                verdict_dict = self._parse_llm_response(raw_text)
                break  # success

            except json.JSONDecodeError as exc:
                last_error = f"JSON parse error: {exc}"
                logger.warning(
                    "CritiqueAgent attempt %d/%d JSON error for finding %s: %s",
                    attempt, MAX_RETRIES, finding.id[:8], exc,
                )

            except httpx.HTTPError as exc:
                logger.error(
                    "CritiqueAgent HTTP error for finding %s: %s",
                    finding.id[:8], exc,
                )
                # Network failure — cannot verify, route to NEEDS_CONTEXT
                result = CritiqueResult(
                    finding_id=finding.id,
                    verdict=CritiqueVerdict.NEEDS_CONTEXT,
                    manual_review_instruction=(
                        f"Critique agent could not reach the inference server to evaluate "
                        f"this finding (HTTP error: {exc}). Manual review required: "
                        f"check criterion {criterion_number} against the DOM element "
                        f"'{finding.element_selector}' in a browser with accessibility "
                        f"inspector tools."
                    ),
                )
                self._log_verdict(result, finding.id)
                return result

            except Exception as exc:
                last_error = str(exc)
                logger.error(
                    "CritiqueAgent unexpected error (attempt %d) for finding %s: %s",
                    attempt, finding.id[:8], exc,
                )

        # All retries exhausted — conservative fallback to NEEDS_CONTEXT
        if verdict_dict is None:
            logger.error(
                "CritiqueAgent: all %d retries exhausted for finding %s — "
                "routing to NEEDS_CONTEXT",
                MAX_RETRIES, finding.id[:8],
            )
            result = CritiqueResult(
                finding_id=finding.id,
                verdict=CritiqueVerdict.NEEDS_CONTEXT,
                manual_review_instruction=(
                    f"Critique agent failed to produce a valid verdict after "
                    f"{MAX_RETRIES} attempts (last error: {last_error}). "
                    f"Manually verify criterion {criterion_number} against element "
                    f"'{finding.element_selector}'. Finding description: "
                    f"{finding.finding_description[:200]}"
                ),
            )
            self._log_verdict(result, finding.id)
            return result

        # ── Step 4: Apply HARD RULE ────────────────────────────────────────────
        verdict_dict = self._apply_hard_rule(verdict_dict, finding.id)

        # ── Step 5: Construct CritiqueResult (Pydantic validates + re-enforces) ─
        try:
            result = self._dict_to_critique_result(verdict_dict, finding)
        except Exception as exc:
            logger.error(
                "CritiqueAgent: CritiqueResult construction failed for finding %s: %s",
                finding.id[:8], exc,
            )
            result = CritiqueResult(
                finding_id=finding.id,
                verdict=CritiqueVerdict.REJECTED,
                rejection_reason=(
                    f"CritiqueResult construction failed: {exc}. "
                    "Finding rejected as a safety measure."
                ),
            )

        # ── Step 6: Log verdict (Design Doc Section 2.4.1: log every verdict) ──
        self._log_verdict(result, finding.id)

        return result

    def _log_verdict(self, result: CritiqueResult, finding_id: str) -> None:
        """
        Log every verdict with finding_id, verdict, and first 100 chars of citation.
        Design Doc Section 2.4.1 explicitly requires this logging.

        Parameters
        ----------
        result : CritiqueResult
            The completed critique result.
        finding_id : str
            Full UUID of the finding (we log the first 8 chars for readability).
        """
        citation_preview = (result.citation or "")[:100]
        rejection_preview = (result.rejection_reason or "")[:80]
        manual_preview = (result.manual_review_instruction or "")[:80]

        if result.verdict == CritiqueVerdict.CONFIRMED:
            logger.info(
                "VERDICT | finding=%s | CONFIRMED | citation='%s...'",
                finding_id[:8], citation_preview,
            )
        elif result.verdict == CritiqueVerdict.REJECTED:
            logger.info(
                "VERDICT | finding=%s | REJECTED  | reason='%s...'",
                finding_id[:8], rejection_preview,
            )
        else:  # NEEDS_CONTEXT
            logger.info(
                "VERDICT | finding=%s | NEEDS_CTX | instruction='%s...'",
                finding_id[:8], manual_preview,
            )

    async def close(self) -> None:
        """Close the HTTP client. Call during pipeline shutdown."""
        await self._http_client.aclose()
        logger.debug("CritiqueAgent HTTP client closed")
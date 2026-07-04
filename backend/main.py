"""
Owned by Mahesh — FastAPI entrypoint.
"""

import logging
from fastapi import FastAPI
from backend.orchestrator.contracts import DOMPayload, ReportJSON
from backend.orchestrator.pipeline import AuditPipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wcag-audit")

app = FastAPI(title="WCAG Accessibility Audit Agent")
pipeline = AuditPipeline()


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.post("/audit")
async def audit(payload: DOMPayload):
    """
    Day 2-3: validates DOM payload, logs it, returns a stub.
    Day 4+: wires in pipeline.run_audit(payload) for real.
    """
    logger.info(
        f"Received audit request | url={payload.url} "
        f"dom_size={payload.meta.dom_size_bytes} "
        f"spa_detected={payload.meta.spa_detected}"
    )

    # TODO Day 4: return await pipeline.run_audit(payload)
    return {
        "received_url": payload.url,
        "dom_size_bytes": payload.meta.dom_size_bytes,
        "status": "stub — full pipeline not wired yet"
    }
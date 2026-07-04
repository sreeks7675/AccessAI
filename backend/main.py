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
    logger.info(
        f"Received audit request | url={payload.url} "
        f"dom_size={payload.meta.dom_size_bytes}"
    )
    result = await pipeline.run_audit(payload)
    return result
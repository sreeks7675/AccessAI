"""
Owned by Mahesh — FastAPI entrypoint.
"""

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.orchestrator.contracts import DOMPayload, ReportJSON
from backend.orchestrator.pipeline import AuditPipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wcag-audit")

app = FastAPI(title="WCAG Accessibility Audit Agent")

# CORS — needed because the Chrome extension calls this API from a
# different origin. Tighten allow_origins once the extension ID is fixed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
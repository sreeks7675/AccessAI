from fastapi import FastAPI
from backend.orchestrator.contracts import DOMPayload, ReportJSON
from backend.orchestrator.pipeline import AuditPipeline

app = FastAPI(title="WCAG Accessibility Audit Agent")
pipeline = AuditPipeline()


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.post("/audit")
async def audit(payload: DOMPayload):
    return {
        "received_url": payload.url,
        "dom_size_bytes": payload.meta.dom_size_bytes,
        "status": "stub — full pipeline not wired yet"
    }
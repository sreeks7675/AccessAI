"""
Tests for the /audit endpoint and pipeline.
Owned by Mahesh.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from backend.main import app


VALID_PAYLOAD = {
    "url": "https://example.com",
    "timestamp": "2026-07-02T10:30:00Z",
    "dom_html": "<html><body><h1>Test</h1></body></html>",
    "computed_styles": {
        "button.submit": {"color": "#fff", "background-color": "#000"}
    },
    "meta": {
        "spa_detected": False,
        "dom_size_bytes": 45200,
        "page_title": "Example Domain",
        "lang_attribute": "en"
    }
}


@pytest.mark.asyncio
async def test_health_check():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_audit_valid_payload_returns_report():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/audit", json=VALID_PAYLOAD)
    assert response.status_code == 200

    data = response.json()
    assert data["audit_metadata"]["url"] == "https://example.com"
    assert "findings" in data
    assert "benchmark" in data


@pytest.mark.asyncio
async def test_audit_missing_required_field_returns_422():
    bad_payload = VALID_PAYLOAD.copy()
    del bad_payload["url"]  # remove a required field

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/audit", json=bad_payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_audit_produces_findings_with_impact_score_and_fix():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/audit", json=VALID_PAYLOAD)

    data = response.json()
    findings = data["findings"]
    assert len(findings) > 0

    first = findings[0]
    assert 0 <= first["impact_score"] <= 100
    assert first["fix"] is not None
    assert first["critique_verdict"] == "CONFIRMED"
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
    del bad_payload["url"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/audit", json=bad_payload)
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_audit_with_unconfigured_agents_returns_empty_findings():
    """
    With real agents wired in but VLLM_ENDPOINT unset and vector store
    empty, findings should be empty — this is expected until Sreekar's
    infra (vector store + vLLM) is ready. Not a bug.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/audit", json=VALID_PAYLOAD)

    assert response.status_code == 200
    data = response.json()
    assert data["findings"] == []


@pytest.mark.asyncio
async def test_audit_handles_unreachable_agents_gracefully():
    """
    When the real agents can't reach VLLM_ENDPOINT (e.g. no GPU cluster
    running), the pipeline should still return a valid 200 response with
    empty findings instead of crashing with a 500.
    """
    payload = {
        "url": "https://example.com/checkout",
        "timestamp": "2026-07-05T10:15:00.000Z",
        "dom_html": "<html><body><h1>Checkout</h1><button class='submit'>Submit</button></body></html>",
        "computed_styles": {},
        "meta": {
            "spa_detected": False,
            "dom_size_bytes": 45200,
            "page_title": "Checkout - Example",
            "lang_attribute": "en"
        }
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/audit", json=payload)

    assert response.status_code == 200

    data = response.json()
    assert "findings" in data
    assert isinstance(data["findings"], list)
    assert data["benchmark"]["our_findings"] == len(data["findings"])
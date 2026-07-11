# WCAG Accessibility Audit Agent — Backend

Backend orchestrator for the WCAG Accessibility Audit Agent, a Chrome extension that scans live websites for accessibility violations and generates verified, ready-to-apply fixes.

Owned by: **Mahesh (WS-2, Backend Core & Orchestrator)**

## What this does

Receives a serialized DOM payload from the Chrome extension, runs it through 5 parallel AI agents (one per disability category), verifies findings through a critique agent, scores their impact, generates fixes, and returns a complete audit report.

```
Extension → POST /audit → 5 disability agents (parallel) → critique agent →
impact weighting → fix engine → ReportJSON → back to extension
```

## Requirements

- Python 3.12
- Windows users: [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) with the "Desktop development with C++" workload (needed to install `chromadb`)

## Setup

```bash
# From the repo root
cd backend
pip install -r requirements.txt
```

Copy `.env.template` to `.env` and fill in the values:

```
ANTHROPIC_API_KEY=
VLLM_ENDPOINT=http://GPU_CLUSTER_IP:8000
CHROMA_DB_PATH=./data/chroma
COURTLISTENER_API_KEY=
LOG_LEVEL=INFO
```

`.env` is gitignored — never commit it.

## Running the server

From the repo root (not inside `backend/`):

```bash
uvicorn backend.main:app --reload
```

Server runs at `http://127.0.0.1:8000`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check, returns `{"status": "ok"}` |
| POST | `/audit` | Accepts a `DOMPayload`, returns a full `ReportJSON` |
| GET | `/metrics` | Prometheus metrics (request counts, latency) |
| GET | `/docs` | Interactive Swagger UI for testing endpoints manually |

## Architecture

**Pipeline stages** (`backend/orchestrator/pipeline.py`):

1. **5 disability agents** (visual, auditory, motor, cognitive, at_parsing) run in parallel via `asyncio.gather`, each producing a list of `Finding` objects
2. **Critique agent** re-verifies every finding, assigning a verdict: `CONFIRMED`, `REJECTED`, or `NEEDS_CONTEXT`
3. **Impact weighting** scores each confirmed finding 0–100 based on WCAG level, confidence, and disability-class prevalence
4. **Fix engine** generates a patch, validates it, and builds a diff + preview for each confirmed finding
5. Results assemble into a `ReportJSON` and return to the extension

**Failure handling:** every agent call and fix-engine call is wrapped with a timeout and isolated from the others — if one agent fails or times out, the rest of the audit still completes. Failed findings default to `NEEDS_CONTEXT` rather than crashing the request.

## Contracts

All shared data shapes live in `backend/orchestrator/contracts.py`. Do not change field names or types without team sign-off — this file is the interface contract between all workstreams.

| Contract | Direction | Owner |
|---|---|---|
| `DOMPayload` | Extension → Backend (IP-1) | Defined by Mahesh, implemented by Anirudh |
| `Finding` | Agents → Orchestrator → Fix Engine (IP-2) | Defined by Sreekar, implemented by Mahesh + Charan |
| `ReportJSON` | Backend → Extension (IP-3) | Defined by Mahesh, rendered by Anirudh |

## Current status: mocked pipeline

The pipeline currently runs against mock implementations of the agents, critique agent, and fix engine (`backend/orchestrator/mock_*.py`), proving the orchestration logic end-to-end ahead of real components being ready. These will be swapped for real imports from `backend/agents/` (Sreekar) and `backend/fix_engine/` (Charan) as they land — the mock files are marked for deletion once that happens.

## Testing

```bash
pytest backend/tests/test_audit.py -v
```

Covers: health check, valid payload → full report, invalid payload → 422, and correct impact scoring + fix generation on findings.

## Folder ownership

```
backend/
├── main.py                    ← Mahesh
├── requirements.txt           ← Mahesh
├── orchestrator/              ← Mahesh
│   ├── contracts.py
│   ├── pipeline.py
│   ├── mock_agents.py         (temporary)
│   ├── mock_critique_agent.py (temporary)
│   └── mock_fix_engine.py     (temporary)
├── agents/                    ← Sreekar
├── fix_engine/                ← Charan
├── news/                      ← Devanshi
├── evaluation/                ← Devanshi
└── tests/                     ← Mahesh
```

## Known issues / notes

- `criterion_level` in `Finding` is typed as `Literal["A", "AA", "AAA"]` — confirm this matches what Sreekar's vector store actually produces.
- The impact-weighting formula in `pipeline.py` is a draft based on WCAG level + confidence + disability-class prevalence. Design §9.2 should be treated as the source of truth if it specifies something different.
- CORS is currently open (`allow_origins=["*"]`) to unblock extension testing — tighten to the actual extension origin before final submission.
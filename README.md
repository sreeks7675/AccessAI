# AccessAI

An AI-powered accessibility auditing platform that analyzes webpages for WCAG compliance using multiple AI agents, automatically generates accessibility fixes, validates them, and provides an interactive preview through a Chrome Extension.

---

# Project Architecture

```
                 Chrome Extension
              (Extracts DOM Payload)
                      │
                      ▼
              POST /audit API
          (FastAPI + Orchestrator)
                      │
                      ▼
        ┌───────────────────────────────┐
        │   AI Accessibility Agents      │
        │-------------------------------│
        │ • Visual Agent                │
        │ • Auditory Agent              │
        │ • Motor Agent                 │
        │ • Cognitive Agent             │
        │ • AT Parsing Agent            │
        └───────────────────────────────┘
                      │
                      ▼
              Critique Agent
          (Validates Findings)
                      │
                      ▼
            Impact Weighting
          (Severity Scoring)
                      │
                      ▼
               Fix Engine
      (Patch • Validate • Diff • Preview)
                      │
                      ▼
              Report Generator
                      │
                      ▼
          Rendered in Chrome Extension
```

---

# Repository Structure

```
AccessAI/
│
├── extension/
│   ├── manifest.json
│   ├── background.js
│   ├── content_script.js
│   └── side_panel/
│
├── backend/
│   ├── main.py
│   ├── orchestrator/
│   ├── agents/
│   ├── rag/
│   ├── fix_engine/
│   ├── news/
│   └── evaluation/
│
├── data/
│
├── docs/
│
├── tests/
│
├── requirements.txt
└── README.md
```

---

# Project Workflow

### 1. Chrome Extension (WS-1)

Owner: **Anirudh**

- Captures webpage DOM
- Sends webpage information to backend
- Displays audit report
- Shows generated accessibility fixes

---

### 2. Backend Orchestrator (WS-2)

Owner: **Mahesh**

Responsibilities:

- FastAPI server
- `/audit` endpoint
- Coordinates complete audit pipeline
- Routes requests between modules
- Produces final Report JSON

---

### 3. AI Accessibility Agents + RAG (WS-3)

Owner: **Sreekar**

Five specialized AI agents execute in parallel.

- Visual Agent
- Auditory Agent
- Motor Agent
- Cognitive Agent
- AT Parsing Agent

Each agent analyzes the webpage using WCAG guidelines and generates accessibility findings.

The Critique Agent validates these findings before forwarding them.

---

### 4. Impact Weighting (WS-2)

Owner: **Mahesh**

Each accessibility issue is assigned a severity score based on:

- User impact
- WCAG priority
- Accessibility risk

---

### 5. Fix Engine (WS-4)

Owner: **Charan**

Responsibilities:

- Generate accessibility patches
- Validate generated fixes
- Produce HTML token-level diff
- Build preview HTML
- Return structured fix objects

Components:

- Patch Generator
- Fix Validator
- HTML Diff Engine
- Preview Builder

---

### 6. News & Evaluation (WS-5)

Owner: **Devanshi**

Responsibilities:

- Accessibility news aggregation
- WCAG update monitoring
- Benchmark evaluation
- Performance reporting

---

# Technology Stack

## Backend

- Python
- FastAPI

## AI

- Large Language Models (LLM)
- Retrieval-Augmented Generation (RAG)

## Frontend

- Chrome Extension API
- HTML
- CSS
- JavaScript

## Testing

- Pytest

---

# Team Responsibilities

| Workstream | Owner | Responsibility |
|------------|--------|----------------|
| WS-1 | Anirudh | Chrome Extension |
| WS-2 | Mahesh | Backend Orchestrator |
| WS-3 | Sreekar | AI Agents & RAG |
| WS-4 | Charan | Fix Engine |
| WS-5 | Devanshi | News & Evaluation |

---

# Accessibility Pipeline

```
Chrome Extension
        │
        ▼
FastAPI Backend
        │
        ▼
Accessibility Agents
        │
        ▼
Critique Agent
        │
        ▼
Impact Weighting
        │
        ▼
Fix Engine
        │
        ▼
Report Generator
        │
        ▼
Chrome Extension UI
```

---

# Key Features

- Automated WCAG accessibility auditing
- Multi-agent AI architecture
- Retrieval-Augmented Generation (RAG)
- Automatic accessibility fix generation
- Patch validation
- HTML difference visualization
- Accessibility preview generation
- Browser extension integration
- Modular workstream architecture

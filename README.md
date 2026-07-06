# 🚀 AccessAI

An AI-powered accessibility auditing platform that evaluates websites for **WCAG (Web Content Accessibility Guidelines)** compliance using a **multi-agent AI architecture**. The system detects accessibility issues, validates findings, generates compliant fixes, and presents an interactive accessibility report through a Chrome Extension.

---

# 🏗️ System Architecture

```mermaid
flowchart TD

A["🌐 Chrome Extension<br/>WS-1 • Anirudh<br/>Extract DOM & Display Results"]

A --> B["⚡ FastAPI Backend (/audit)<br/>WS-2 • Mahesh<br/>Request Orchestration"]

B --> C

subgraph C["🤖 AI Accessibility Agents (WS-3 • Sreekar)"]
direction LR

V["👁️ Visual Agent"]
AU["🔊 Auditory Agent"]
M["🖱️ Motor Agent"]
COG["🧠 Cognitive Agent"]
AT["⌨️ AT Parsing Agent"]

end

V --> D
AU --> D
M --> D
COG --> D
AT --> D

D["✅ Critique Agent<br/>Validate & Filter Findings"]

D --> E

E["📊 Impact Weighting<br/>WS-2 • Mahesh"]

E --> F

F["🛠️ Fix Engine<br/>WS-4 • Charan"]

subgraph FIX["Fix Engine Components"]
direction LR

PG["Patch Generator"]
FV["Fix Validator"]
DE["HTML Diff Engine"]
PB["Preview Builder"]

end

F --> PG
F --> FV
F --> DE
F --> PB

PG --> G
FV --> G
DE --> G
PB --> G

G["📄 Final Accessibility Report"]

G --> H["📰 News & WCAG Update Monitor<br/>WS-5 • Devanshi"]

H --> I["📈 Benchmark & Evaluation"]

I --> J["🖥️ Chrome Extension UI"]
```

---

# 📂 Repository Structure

```text
AccessAI/
│
├── extension/
│   ├── manifest.json
│   ├── background.js
│   ├── content_script.js
│   ├── assets/
│   └── side_panel/
│       ├── panel.html
│       ├── panel.css
│       └── panel.js
│
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   │
│   ├── orchestrator/
│   │   ├── __init__.py
│   │   ├── pipeline.py
│   │   └── contracts.py
│   │
│   ├── agents/
│   │   ├── visual_agent.py
│   │   ├── auditory_agent.py
│   │   ├── motor_agent.py
│   │   ├── cognitive_agent.py
│   │   ├── at_parsing_agent.py
│   │   └── critique_agent.py
│   │
│   ├── rag/
│   │   ├── vector_store.py
│   │   └── wcag_loader.py
│   │
│   ├── fix_engine/
│   │   ├── patch_generator.py
│   │   ├── fix_validator.py
│   │   └── diff_engine.py
│   │
│   ├── news/
│   │   ├── aggregator.py
│   │   └── summariser.py
│   │
│   └── evaluation/
│       ├── benchmark.py
│       └── wcag_update_monitor.py
│
├── data/
│   ├── wcag_criteria.json
│   └── regulation_mapping.csv
│
├── docs/
│
├── tests/
│
├── README.md
└── .gitignore
```

---

# 🔄 Project Workflow

## 1. Chrome Extension (WS-1)

**Owner:** Anirudh

Responsibilities:

- Capture webpage DOM
- Send webpage data to backend
- Display accessibility report
- Show generated accessibility fixes

---

## 2. Backend Orchestrator (WS-2)

**Owner:** Mahesh

Responsibilities:

- FastAPI backend
- `/audit` endpoint
- Coordinate complete workflow
- Manage communication between modules
- Generate final accessibility report

---

## 3. AI Accessibility Agents & RAG (WS-3)

**Owner:** Sreekar

Five AI agents execute in parallel.

- 👁️ Visual Agent
- 🔊 Auditory Agent
- 🖱️ Motor Agent
- 🧠 Cognitive Agent
- ⌨️ AT Parsing Agent

Each agent evaluates webpages using WCAG guidelines and generates structured accessibility findings.

The **Critique Agent** validates these findings before they proceed further.

---

## 4. Impact Weighting (WS-2)

**Owner:** Mahesh

Assigns severity scores based on:

- User impact
- WCAG priority
- Accessibility risk
- Confidence score

---

## 5. Fix Engine (WS-4)

**Owner:** Charan

Automatically generates accessibility-compliant fixes.

### Components

- Patch Generator
- Fix Validator
- HTML Diff Engine
- Preview Builder

### Outputs

- HTML Patch
- Validated Fix
- HTML Diff
- Preview HTML
- Structured Fix Object

---

## 6. News & Evaluation (WS-5)

**Owner:** Devanshi

Responsibilities

- Accessibility news aggregation
- WCAG update monitoring
- Benchmark evaluation
- Performance reporting

---

# 🛠️ Technology Stack

### Backend

- Python
- FastAPI

### Artificial Intelligence

- Large Language Models (LLMs)
- Multi-Agent Architecture
- Retrieval-Augmented Generation (RAG)

### Frontend

- Chrome Extension API
- HTML
- CSS
- JavaScript

### Testing

- Pytest

### Version Control

- Git
- GitHub

---

# 👥 Team Responsibilities

| Workstream | Owner | Responsibility |
|------------|--------|----------------|
| WS-1 | Anirudh | Chrome Extension |
| WS-2 | Mahesh | Backend Orchestrator & Impact Weighting |
| WS-3 | Sreekar | AI Agents, RAG & Critique Agent |
| WS-4 | Charan | Fix Engine |
| WS-5 | Devanshi | News & Evaluation |

---

# ✨ Key Features

- WCAG 2.x Accessibility Auditing
- Multi-Agent AI Architecture
- Retrieval-Augmented Generation (RAG)
- Automated Accessibility Fix Generation
- HTML Patch Validation
- HTML Difference Visualization
- Accessibility Preview Generation
- Impact-Based Severity Scoring
- Chrome Extension Integration
- Modular Backend Architecture
- Automated Testing with Pytest

---

# 🧪 Testing

Run all tests:

```bash
pytest
```

Run Fix Engine tests:

```bash
pytest backend/tests/test_fix_engine -v
```

---

# 🚀 Getting Started

Clone the repository

```bash
git clone https://github.com/sreeks7675/AccessAI.git
```

Navigate to the project

```bash
cd AccessAI
```

Install dependencies

```bash
pip install -r backend/requirements.txt
```

Run the backend

```bash
python backend/main.py
```

---

# 📄 License

This project is developed for educational and research purposes as part of the **AccessAI** accessibility auditing platform.

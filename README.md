# 🚀 AccessAI

<p align="center">

![Python](https://img.shields.io/badge/Python-3.11-blue?style=for-the-badge&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-green?style=for-the-badge&logo=fastapi)
![Chrome Extension](https://img.shields.io/badge/Chrome-Extension-yellow?style=for-the-badge&logo=googlechrome)
![GitHub](https://img.shields.io/badge/GitHub-Team%20Project-black?style=for-the-badge&logo=github)

</p>

<p align="center">

## ♿ AI-Powered Accessibility Auditing Platform

Automatically detects **WCAG accessibility issues**, generates **AI-powered fixes**, validates them, creates **HTML previews**, and delivers results through an interactive **Chrome Extension**.

</p>

---

# 📖 Overview

AccessAI is an intelligent accessibility auditing platform built using a **Multi-Agent AI Architecture**.

Unlike traditional accessibility scanners that only identify WCAG violations, AccessAI goes a step further by automatically generating standards-compliant fixes, validating them, and providing developers with an interactive preview before applying the changes.

The platform combines specialized AI agents, Retrieval-Augmented Generation (RAG), a FastAPI backend, and an automated Fix Engine to streamline web accessibility remediation.

---

# ✨ Key Highlights

- ♿ Automated WCAG 2.x Accessibility Auditing
- 🤖 Multi-Agent AI Architecture
- 🧠 Retrieval-Augmented Generation (RAG)
- 🛠 Automatic Accessibility Fix Generation
- ✅ Patch Validation
- 📊 Severity & Impact Scoring
- 📄 HTML Diff Generation
- 👀 Accessibility Preview
- 🌐 Chrome Extension Integration
- 📈 Benchmark Evaluation
- 📰 WCAG News Monitoring

---

# 🏗️ Project Architecture

```text
                                        🌐 Chrome Extension
                                   (WS-1 • Anirudh)
                    ┌─────────────────────────────────────────────┐
                    │                                             │
                    │  • Extract DOM                              │
                    │  • Capture User Interaction                 │
                    │  • Display Accessibility Report             │
                    │  • Preview Suggested Fixes                  │
                    └─────────────────────────────────────────────┘
                                        │
                                        ▼
                          ⚡ FastAPI Backend (WS-2 • Mahesh)
                    ┌─────────────────────────────────────────────┐
                    │                                             │
                    │  • /audit Endpoint                          │
                    │  • Request Validation                       │
                    │  • Pipeline Orchestration                   │
                    │  • JSON Contract Handling                   │
                    └─────────────────────────────────────────────┘
                                        │
        ┌───────────────────────────────┼────────────────────────────────┐
        ▼                               ▼                                ▼

 🤖 AI Agents & RAG              📊 Impact Weighting          📰 News & Evaluation
 (WS-3 • Sreekar)                 (WS-2 • Mahesh)             (WS-5 • Devanshi)

┌──────────────────────┐       ┌───────────────────┐      ┌──────────────────────┐
│ 👁️ Visual Agent       │       │ Severity Scoring  │      │ News Aggregator      │
│ 🔊 Auditory Agent     │       │ Priority Ranking  │      │ WCAG Update Monitor  │
│ 🖱️ Motor Agent        │       │ Confidence Score  │      │ Benchmark Evaluation │
│ 🧠 Cognitive Agent    │       └───────────────────┘      └──────────────────────┘
│ ⌨️ AT Parsing Agent   │
│ ✅ Critique Agent     │
│ 📚 WCAG RAG Store     │
└──────────────────────┘
                │
                ▼
        🛠️ Fix Engine (WS-4 • Charan)
┌──────────────────────────────────────────────────┐
│                                                  │
│ 🔧 Patch Generator                               │
│ ✅ Fix Validator                                 │
│ 📄 HTML Diff Engine                              │
│ 👀 Preview Builder                               │
│                                                  │
└──────────────────────────────────────────────────┘
                │
                ▼
       📄 Final Accessibility Report (JSON)
                │
                ▼
          🖥️ Chrome Extension Dashboard
```

---

## 📂 Repository Structure

```text
AccessAI/
│
├── extension/                 # Chrome Extension (WS-1)
│   ├── assets/
│   └── side_panel/
│
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   ├── orchestrator/          # WS-2
│   ├── agents/                # WS-3
│   ├── rag/                   # WS-3
│   ├── fix_engine/            # WS-4
│   ├── news/                  # WS-5
│   ├── evaluation/            # WS-5
│   └── tests/
│
├── data/
├── docs/
├── .github/
├── README.md
├── .gitignore
└── LICENSE
```

---

# ⚙️ Project Workflow

## 🌐 WS-1 — Chrome Extension (Anirudh)

The Chrome Extension acts as the primary interface between the user and the backend.

### Responsibilities

- 🌍 Capture webpage DOM
- 📤 Send webpage data to FastAPI
- 📊 Display accessibility findings
- 👀 Show generated accessibility previews
- 📄 Render final accessibility report

---

## ⚡ WS-2 — Backend Orchestrator (Mahesh)

The backend orchestrator manages the complete accessibility auditing pipeline.

### Responsibilities

- FastAPI Backend
- `/audit` API Endpoint
- Request Routing
- Pipeline Coordination
- Report Assembly
- Impact Weighting

---

## 🤖 WS-3 — AI Accessibility Agents (Sreekar)

The AI layer consists of specialized accessibility agents that execute in parallel.

| Agent | Responsibility |
|-------|----------------|
| 👁️ Visual Agent | Detects color contrast, images, layout, fonts and visual accessibility issues. |
| 🔊 Auditory Agent | Evaluates captions, transcripts and multimedia accessibility. |
| 🖱️ Motor Agent | Checks keyboard navigation, focus order and interactive controls. |
| 🧠 Cognitive Agent | Detects readability, consistency and comprehension issues. |
| ⌨️ AT Parsing Agent | Validates semantic HTML and assistive technology compatibility. |
| ✅ Critique Agent | Validates, filters and confirms findings from all agents. |
| 🧩 Base Agent | Shared implementation used by all accessibility agents. |
| 📑 Schemas | Shared data models exchanged across modules. |

---

## 📚 Retrieval-Augmented Generation (RAG)

The RAG module provides AI agents with structured WCAG knowledge.

### Components

- 📄 WCAG Criteria Loader
- 🗂️ Vector Store
- 🔍 Semantic Search
- 📚 Accessibility Knowledge Base

---

## 🛠️ WS-4 — Fix Engine (Charan)

The Fix Engine automatically repairs validated accessibility issues.

### Components

| Module | Responsibility |
|---------|----------------|
| 🔧 Patch Generator | Generates accessibility-compliant HTML patches |
| ✅ Fix Validator | Validates generated fixes |
| 📄 HTML Diff Engine | Produces token-level HTML differences |
| 👀 Preview Builder | Generates interactive preview HTML |

### Output

- HTML Patch
- Validated Fix
- HTML Difference
- Accessibility Preview
- Review Status
- Structured Fix Object

---

## 📰 WS-5 — News & Evaluation (Devanshi)

Provides accessibility intelligence beyond webpage auditing.

### News Module

- 📰 Accessibility News Aggregation
- 🌍 WCAG Standards Monitoring
- 📢 Latest Accessibility Updates

### Evaluation Module

- 📈 Benchmark Evaluation
- 📊 Performance Metrics
- 📑 Model Evaluation
- 🔍 WCAG Update Monitoring

---

# 💻 Technology Stack

## Backend

- 🐍 Python
- ⚡ FastAPI

## Artificial Intelligence

- 🤖 Large Language Models (LLMs)
- 🧠 Multi-Agent AI Architecture
- 📚 Retrieval-Augmented Generation (RAG)

## Frontend

- 🌐 Chrome Extension API
- HTML
- CSS
- JavaScript

## Data

- WCAG 2.x Guidelines
- Accessibility Knowledge Base
- Regulation Mapping

## Testing

- 🧪 Pytest

## Version Control

- Git
- GitHub

---

# 👥 Team Responsibilities

| Workstream | Owner | Responsibilities |
|------------|--------|------------------|
| 🌐 WS-1 | **Anirudh** | Chrome Extension & User Interface |
| ⚡ WS-2 | **Mahesh** | FastAPI Backend, Orchestrator & Impact Weighting |
| 🤖 WS-3 | **Sreekar** | AI Agents, Critique Agent & RAG |
| 🛠️ WS-4 | **Charan** | Patch Generator, Fix Validator, HTML Diff Engine & Preview Builder |
| 📰 WS-5 | **Devanshi** | News Aggregation, Benchmarking & WCAG Update Monitoring |

---

# ✨ Features

- ♿ Automated WCAG 2.x Accessibility Auditing
- 🤖 Multi-Agent AI Architecture
- 🧠 Retrieval-Augmented Generation (RAG)
- 🔍 Intelligent Accessibility Issue Detection
- 🛠️ Automatic Accessibility Fix Generation
- ✅ Accessibility Patch Validation
- 📊 Severity & Impact Scoring
- 📄 HTML Token-Level Diff Generation
- 👀 Accessibility Preview Generation
- 🌐 Chrome Extension Integration
- 📈 Accessibility Benchmark Evaluation
- 📰 WCAG News & Standards Monitoring
- 🧪 Automated Testing with Pytest
- 📦 Modular Backend Architecture

---

# 🧪 Testing

Run the complete test suite:

```bash
pytest
```

Run only Fix Engine tests:

```bash
pytest backend/tests/test_fix_engine -v
```

Run AI Agent tests:

```bash
pytest backend/tests/test_agents -v
```

Run News & Evaluation tests:

```bash
pytest backend/tests/test_news_eval -v
```

Run Orchestrator tests:

```bash
pytest backend/tests/test_orchestrator -v
```

---

# 🚀 Getting Started

## Clone Repository

```bash
git clone https://github.com/sreeks7675/AccessAI.git
```

---

## Navigate into the Project

```bash
cd AccessAI
```

---

## Install Dependencies

```bash
pip install -r backend/requirements.txt
```

---

## Start the Backend

```bash
python backend/main.py
```

---

## Run Tests

```bash
pytest
```

---

# 📌 Future Enhancements

- 🌍 Support for additional accessibility standards
- 🤖 More specialized AI accessibility agents
- 📱 Mobile accessibility auditing
- ☁️ Cloud deployment
- 📊 Accessibility analytics dashboard
- 🔄 Continuous website monitoring
- 🌐 Browser support beyond Chrome
- 📈 AI-powered accessibility recommendations

---

# 🤝 Contributors

| Name | Workstream |
|------|------------|
| 👨‍💻 Anirudh | Chrome Extension |
| 👨‍💻 Mahesh | Backend & Orchestrator |
| 👨‍💻 Sreekar | AI Agents & RAG |
| 👨‍💻 Charan | Fix Engine |
| 👩‍💻 Devanshi | News & Evaluation |

---

# 📄 License

This project was developed for academic and research purposes as part of the **AccessAI** project.

---

<p align="center">

**⭐ If you found this project useful, consider giving it a star on GitHub! ⭐**

</p>

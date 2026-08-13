# Dyson DFM Analysis Tool

> Automated Design for Manufacturability (DFM) analysis for injection-moulded parts using AI-assisted review.

---

## Overview

The Dyson DFM Analysis Tool is a full-stack web application that takes a 3D CAD model in STEP format, runs it through a multi-stage analysis pipeline, and produces a detailed report on manufacturability compliance. It evaluates every face of the part against DFM rules (wall thickness, undercuts, cosmetic features) and generates an interactive dashboard with findings, AI-powered commentary, and exportable reports.

## Architecture

```
dyson/
├── backend/                    # Python FastAPI server
│   ├── main.py                 # API entry point, CORS, file upload handling
│   ├── models.py               # Pydantic data models (Finding, PartModelFace)
│   ├── pipeline.py             # 5-step analysis pipeline orchestrator
│   ├── step_loader.py          # STEP file ingestion via pythonocc-core
│   ├── features.py             # Geometric feature extraction (normals, draft, thickness)
│   ├── rules_catalog.json      # DFM rules definitions (THK-001, UND-001, COS-001)
│   ├── agents/
│   │   └── orchestrator.py     # LLM interpretive agent + validation checks
│   ├── prompts/
│   │   └── interpretive_rule.md  # LLM prompt template for REVIEW findings
│   └── requirements.txt        # Python dependencies
│
├── frontend/                   # React + Vite application
│   ├── src/
│   │   ├── App.jsx             # Root component, state management, layout
│   │   ├── main.jsx            # Entry point (React 18, MUI ThemeProvider)
│   │   ├── theme.js            # MUI custom theme
│   │   └── components/
│   │       ├── Header.jsx      # Top bar with file upload
│   │       ├── PipelineStepper.jsx    # 5-step animated progress indicator
│   │       ├── SummaryDashboard.jsx   # Compliance stat cards
│   │       ├── ValidationIssuesPanel.jsx  # Warning alerts
│   │       ├── Viewer.jsx     # DFM rules catalog sidebar
│   │       └── FindingsTable.jsx      # Sortable/filterable data table
│   ├── package.json
│   └── vite.config.js          # Dev server proxy to backend
│
└── docs/                       # Documentation
    ├── README.md               # This file
    ├── DEMO.md                 # Step-by-step demo guide
    ├── ARCHITECTURE.md         # Technical deep-dive
    ├── API.md                  # Backend API reference
    └── USER_GUIDE.md           # End-user guide
```

## Quick Start

### Prerequisites

| Dependency | Version | Purpose |
|---|---|---|
| Python | 3.10+ | Backend runtime |
| Conda | Any | Manage pythonocc-core environment |
| Node.js | 18+ | Frontend build tools |
| npm | 9+ | Package management |

### Backend Setup

```bash
# Create conda environment with pythonocc-core
conda create -n dyson python=3.13
conda activate dyson
conda install -c conda-forge pythonocc-core

# Install remaining Python dependencies
cd backend
pip install -r requirements.txt

# Start the backend server
uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

### AI Commentary Setup (optional)

The geometric analysis is fully deterministic and needs no credentials. The
interpretive layer — the engineering summary and the per-finding commentary —
calls a language model through [OpenRouter](https://openrouter.ai).

```bash
cp backend/.env.example backend/.env
# then paste your key into backend/.env:
#   OPENROUTER_API_KEY=sk-or-v1-...
```

Restart the backend afterwards; the file is read once at startup. Check it took
effect at `GET /api/llm_status`, which reports the active model or tells you
what is missing.

| | |
|---|---|
| Default model | `~deepseek/deepseek-v4-flash-latest` (alias — always the newest V4 Flash) |
| Cost | ~$0.0006 per part analysed at current DeepSeek V4 Flash pricing |
| Requests per analysis | 2 — one batched commentary call, one summary call |

**Without a key the tool still runs.** Every rule is still evaluated and every
verdict still produced; the report simply says the AI summary is unavailable
rather than inventing one. Nothing in the deterministic path depends on the
model being reachable, and a timeout or rate limit degrades the report instead
of failing the analysis.

**What is sent:** rule names, measured values, and part-level metadata (size,
wall thickness, face count). The STEP file and its geometry are never uploaded.

To use a different model, set `OPENROUTER_MODEL` to any slug from
[openrouter.ai/models](https://openrouter.ai/models) — the client is
OpenAI-compatible, so no code changes are needed.

### Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

The application will be available at:
- **Frontend**: http://localhost:5173
- **Backend API**: http://127.0.0.1:8001
- **API Docs (Swagger)**: http://127.0.0.1:8001/docs

## Usage

1. Open the frontend in your browser
2. Click **"Upload STEP File"** in the top-right corner
3. Select a `.step` or `.stp` file (max 100 MB)
4. Watch the 5-step pipeline animate through analysis
5. View results: summary dashboard, rules catalog, and detailed findings table
6. Filter by status, search by keyword, sort by column, and export to CSV

## DFM Rules

| Rule ID | Rule Name | Type | Threshold | Guideline |
|---|---|---|---|---|
| THK-001 | Minimum Wall Thickness | Quantitative | >= 1.0mm | DFM-GUIDE-4.2.1 |
| UND-001 | Undercut Detection | Not yet computable | - | DFM-GUIDE-6.1.0 |
| COS-001 | Avoid Unnecessary Cosmetic Features | Qualitative (AI) | - | DFM-GUIDE-2.5.0 |

## Technology Stack

### Backend
- **FastAPI** - Async REST API framework
- **pythonocc-core** - CAD geometry kernel (OpenCascade bindings)
- **Pydantic** - Data validation and schemas
- **Uvicorn** - ASGI server
- **OpenRouter** (via the OpenAI-compatible SDK) - interpretive commentary layer,
  optional at runtime

### Frontend
- **React 18** - UI library with JSX runtime
- **Vite 5** - Build tool and dev server with API proxy
- **Material UI (MUI) v5** - Component library
- **Axios** - HTTP client
- **ESLint** - Code quality

## Testing

The geometry layer is validated against synthetic STEP parts whose dimensions are
known from their construction — a 2 mm shelled box, a cone tapering by exactly
2.862°, a plate with a Ø5 mm hole, a box with a cross-hole undercut. Measured
values are asserted against those constructions, so a regression in the
extractor fails here rather than silently changing every report.

```bash
cd backend && python -m pytest tests/ -q
```

- `tests/test_calibration.py` — geometry: draft, wall thickness, hole
  recognition, undercut detection, normal orientation, unit conversion.
- `tests/test_rule_scoping.py` — rule applicability: material exclusivity,
  face-type scoping, catalog linting, absence of contradictory verdicts.
- `tests/test_feature_naming.py` — the names findings use: what they may claim,
  and the guarantee that one name identifies one feature.
- `tests/test_process_classifier.py` — reading the process off the geometry:
  the machined/moulded distinction, physical disqualifiers, and that a close
  call is reported rather than broken.
- `tests/test_mesh.py` — the 3D view's face numbering, which must match the
  analyser's exactly or the model points at the wrong wall.
- `tests/test_llm_integration.py` — the interpretive layer, entirely offline.
- `tests/test_real_world_parts.py` — the NIST set below. **Skips cleanly when
  that folder is absent**, so a fresh clone still runs the whole suite.

Run this after any change to `features.py`, `step_loader.py` or `rule_scoping.py`.

### The NIST validation set

The real-world tests and the geometry benchmark run against the NIST MBE PMI
validation set: 33 STEP files of real machined test artifacts, in AP203 and
AP242, mixing millimetre and inch units and deliberately containing syntax
errors. It is 67 MB of public data, so it is not committed — download it and
unpack it into `NIST-PMI-STEP-Files/` at the repository root:

<https://www.nist.gov/services-resources/software/mbe-pmi-validation-and-conformance-testing-project>

Everything runs without it; those tests simply skip.

## Development

```bash
# Frontend lint
cd frontend && npm run lint

# Frontend build (production)
cd frontend && npm run build

# Backend runs with --reload for auto-restart on changes
```

## License

Internal use only. Dyson / Motiveminds Consulting Pvt Ltd.

# Technical Architecture

> Deep-dive into the system architecture, data flow, and design decisions.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                            │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    React Frontend                        │   │
│  │                                                          │   │
│  │  App.jsx ──┬── Header (file upload)                     │   │
│  │            ├── PipelineStepper (5-step animation)        │   │
│  │            ├── SummaryDashboard (stat cards)             │   │
│  │            ├── ValidationIssuesPanel (warnings)          │   │
│  │            ├── Viewer (rules catalog)                    │   │
│  │            └── FindingsTable (data table)                │   │
│  │                                                          │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                          │ HTTP POST /api/analyze                │
│                          │ (multipart/form-data)                 │
└──────────────────────────┼──────────────────────────────────────┘
                           │
                    Vite Dev Proxy
                    (/api → localhost:8001)
                           │
┌──────────────────────────┼──────────────────────────────────────┐
│                   FastAPI Backend                                │
│                          │                                       │
│  main.py ────────────────┤                                       │
│  ├── CORS middleware (allow *)                                   │
│  ├── GET / (health check)                                       │
│  └── POST /api/analyze ──┤                                      │
│                           │                                      │
│  pipeline.py ─────────────┤                                      │
│  └── run_analysis_pipeline()                                    │
│       │                                                          │
│       ├─ Step 1: INGEST ──→ step_loader.py                     │
│       │   └── STEPControl_Reader → TopoDS_Shape                 │
│       │                                                          │
│       ├─ Step 2: EXTRACT ──→ features.py                       │
│       │   ├── get_all_faces() → List[TopoDS_Face]              │
│       │   ├── _get_face_normal_and_uv() → gp_Dir               │
│       │   ├── calculate_draft_angle() → float                   │
│       │   └── calculate_wall_thickness() → float                │
│       │   Output: List[PartModelFace]                           │
│       │                                                          │
│       ├─ Step 3: EXECUTE ──→ pipeline.py::execute_rules()     │
│       │   ├── Load rules_catalog.json                           │
│       │   ├── For each rule × each face:                        │
│       │   │   ├── Quantitative: _safe_predicate_eval()          │
│       │   │   ├── Qualitative: → REVIEW                         │
│       │   │   └── Not computable: → REVIEW                      │
│       │   Output: List[Finding]                                 │
│       │                                                          │
│       ├─ Step 4: INTERPRET ──→ orchestrator.py                │
│       │   ├── enrich_review_findings()                          │
│       │   ├── For each REVIEW finding:                          │
│       │   │   ├── Load prompt template                          │
│       │   │   ├── Call LLM (mock_llm_call)                     │
│       │   │   └── Parse JSON → agent_commentary + confidence    │
│       │   Output: List[Finding] (enriched)                     │
│       │                                                          │
│       └─ Step 5: VALIDATE ──→ orchestrator.py                 │
│           ├── validate()                                        │
│           ├── Check: unknown face IDs                           │
│           ├── Check: malformed location strings                 │
│           └── Check: fail verdict without measurement           │
│           Output: List[dict] (validation issues)               │
│                                                                  │
│  Response: { "findings": [...], "validation_issues": [...] }   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

## Data Models

### PartModelFace (Internal - not exposed to frontend)

Represents the geometric properties of a single face extracted from the STEP file.

```python
class PartModelFace(BaseModel):
    face_id: int                              # 1-indexed face identifier
    face_normal: Optional[tuple[float, float, float]]  # (nx, ny, nz) unit vector
    wall_thickness: Optional[float]           # Ray-cast distance to opposite face
    draft_angle: Optional[float]              # Degrees from pull direction (Z-axis)
```

**How each field is computed:**

| Field | Method | Module |
|---|---|---|
| `face_id` | Sequential index from `TopExp_Explorer` | `features.py:177` |
| `face_normal` | `GeomLProp_SLProps.Normal()` at parametric center | `features.py:53-80` |
| `draft_angle` | `face_normal.Angle(pull_direction)` converted to degrees, acute angle | `features.py:93-110` |
| `wall_thickness` | `IntCurvesFace_ShapeIntersector` ray from face center, inverted normal | `features.py:113-159` |

### Finding (Exposed to frontend)

Represents a single DFM rule evaluation result.

```python
class Finding(BaseModel):
    rule_id: str              # e.g., "THK-001"
    rule_name: str            # e.g., "Minimum Wall Thickness"
    guideline_ref: str        # e.g., "DFM-GUIDE-4.2.1"
    status: Literal["COMPLIANT", "NON-COMPLIANT", "REVIEW"]
    location: str             # e.g., "face 123"
    measured: Optional[str]   # e.g., "1.500" (formatted to 3 decimal places)
    agent_commentary: Optional[str]  # AI-generated (max 2000 chars)
    agent_confidence: Optional[float]  # 0.0 - 1.0
```

**Status determination logic:**

```
For quantitative rules (THK-001):
  if metric value is available:
    if predicate(value) is True → COMPLIANT
    if predicate(value) is False → NON-COMPLIANT
  else → REVIEW (no data for this face)

For qualitative rules (COS-001):
  → REVIEW (always, requires human/AI judgment)

For not-yet-computable rules (UND-001):
  → REVIEW (no predicate defined)
```

## Rules Catalog

Located at `backend/rules_catalog.json`, the rules catalog is a JSON array of rule definitions:

```json
{
  "rule_id": "THK-001",
  "rule_name": "Minimum Wall Thickness",
  "guideline_ref": "DFM-GUIDE-4.2.1",
  "kind": "quantitative",
  "predicate": {
    "operator": ">=",
    "threshold": 1.0
  },
  "metric": "wall_thickness",
  "description": "Wall thickness should be at least 1.0mm..."
}
```

**Supported predicate operators:** `>`, `>=`, `<`, `<=`, `==`

**Rule kinds:**

| Kind | Evaluation | Example |
|---|---|---|
| `quantitative` | Deterministic predicate against metric | THK-001: wall_thickness >= 1.0mm |
| `qualitative` | LLM-assisted review | COS-001: cosmetic feature evaluation |
| `quantitative_not_yet_computable` | Placeholder for future rules | UND-001: undercut detection (requires side-pull analysis) |

## Geometric Analysis Details

### Wall Thickness Calculation

The wall thickness measurement uses ray-casting:

1. Get the parametric center (u, v) of the face
2. Compute the 3D point on the surface at (u, v)
3. Cast a ray from that point along the **inverted face normal** (into the part)
4. Use `IntCurvesFace_ShapeIntersector` to find all intersection points
5. Filter out self-intersections (same face)
6. Return the minimum distance to the nearest different face

```
Face A (start) ──────ray──────→ Face B (hit)
                  wall thickness
```

### Draft Angle Calculation

The draft angle is the acute angle between the face normal and the pull direction (Z-axis):

```
angle = face_normal.Angle(pull_direction)  // radians
angle_degrees = degrees(angle)

if angle_degrees > 90:
    angle_degrees = 180 - angle_degrees    // acute angle
```

A draft angle of 0 degrees means the face is parallel to the pull direction (no draft). Typical injection molding requires 1-3 degrees minimum.

## Agent Architecture

The pipeline uses a **dependency injection** pattern for the LLM agent:

```python
# The LLM call function is injected as a parameter
enriched_findings = agents.orchestrator.enrich_review_findings(
    findings, part_context, mock_llm_call  # swappable
)
```

**Current implementation:** `mock_llm_call()` returns a canned response with 85% confidence.

**Production path:** Replace with a real LLM provider (OpenAI, Anthropic, Azure) by providing a different `llm_call` function. The prompt template is in `prompts/interpretive_rule.md`.

### Prompt Template Variables

The interpretive prompt accepts these variables:

| Variable | Source | Example |
|---|---|---|
| `{{part_context}}` | JSON: part_name, num_faces | `{"part_name": "housing.step", "num_faces": 47}` |
| `{{rule_name}}` | Finding.rule_name | "Minimum Wall Thickness" |
| `{{guideline_ref}}` | Finding.guideline_ref | "DFM-GUIDE-4.2.1" |
| `{{location}}` | Finding.location | "face 12" |
| `{{measured}}` | Finding.measured | "0.850" or None |

## Validation Agent

The validation step performs deterministic cross-checks:

| Check | Issue Type | Condition |
|---|---|---|
| Unknown face ID | `"unknown face id"` | `location` references a face_id not in the PartModel |
| Malformed location | `"malformed location string"` | `location` starts with "face" but can't parse the integer |
| Missing measurement | `"fail verdict without measurement"` | `status == "NON-COMPLIANT"` and `measured is None` |

## Frontend Architecture

### State Management

All state lives in `App.jsx` using React hooks:

```javascript
const [findings, setFindings] = useState([]);           // Finding[]
const [validationIssues, setValidationIssues] = useState([]);  // {finding, issue}[]
const [loading, setLoading] = useState(false);
const [error, setError] = useState(null);               // string | null
const [uploadedFile, setUploadedFile] = useState(null); // File | null
const [analysisComplete, setAnalysisComplete] = useState(false);
```

### API Communication

```
Browser → Vite Dev Proxy → FastAPI

POST /api/analyze
Content-Type: multipart/form-data
Body: file=<.step file>

Response:
{
  "findings": [...],
  "validation_issues": [...]
}
```

The Vite dev server proxies `/api` requests to `http://127.0.0.1:8001` (configured in `vite.config.js`).

### Component Hierarchy

```
App
├── Header                    # AppBar, upload button, file info chip
├── PipelineStepper           # Animated 5-step progress (visible during/after analysis)
├── Alert (error)             # Error banner with retry button
├── Paper (empty state)       # Landing page before any analysis
└── [Results Layout]
    ├── SummaryDashboard      # 5 stat cards + compliance bar
    ├── ValidationIssuesPanel # Collapsible warning alerts
    └── Grid (2 columns)
        ├── Viewer            # Rules catalog sidebar (25% width)
        └── FindingsTable     # Data table (75% width)
```

## Performance Characteristics

| Operation | Complexity | Notes |
|---|---|---|
| STEP file loading | O(faces) | PythonOCC STEPControl_Reader |
| Feature extraction | O(faces) | Each face: O(1) normal + O(faces) ray-cast |
| Rule execution | O(rules x faces) | 3 rules x N faces |
| LLM interpretation | O(review_findings) | Sequential API calls (mock: instant) |
| Validation | O(findings) | Single pass with set lookup |
| **Total** | **O(faces^2)** | Dominated by ray-casting in extraction |

For a typical part with 50 faces and 3 rules: ~150 findings generated and returned in ~5-10 seconds.

## Security Considerations

| Aspect | Current State | Production Recommendation |
|---|---|---|
| CORS | Allow all origins (`*`) | Restrict to frontend domain |
| Authentication | None | Add JWT/OAuth2 |
| File validation | Extension check only | Add file magic byte validation |
| File size | 100 MB client-side | Add server-side limit |
| Temp file cleanup | `finally` block | Already handled |
| LLM secrets | Mock implementation | Use environment variables |

# API Reference

> Backend REST API documentation for the DFM Analysis service.

---

## Base URL

```
http://127.0.0.1:8001
```

Interactive API docs are available at:
- **Swagger UI**: http://127.0.0.1:8001/docs
- **ReDoc**: http://127.0.0.1:8001/redoc

---

## Endpoints

### GET /

Health check endpoint.

**Response**

```json
{
  "message": "DFM Analysis API is running. Use the frontend to upload a file to the /analyze endpoint."
}
```

**Status Codes**

| Code | Description |
|---|---|
| 200 | API is running |

---

### POST /api/analyze

Run a complete DFM analysis on an uploaded STEP file. This is the primary endpoint that executes the full 5-step pipeline.

**Request**

| Header | Value |
|---|---|
| Content-Type | `multipart/form-data` |

| Field | Type | Required | Description |
|---|---|---|---|
| `file` | File | Yes | A `.step` or `.stp` CAD file (max 100 MB) |

**Example (cURL)**

```bash
curl -X POST http://127.0.0.1:8001/api/analyze \
  -F "file=@sample_part.step"
```

**Example (JavaScript / Axios)**

```javascript
const formData = new FormData();
formData.append("file", fileObject);

const response = await axios.post("/api/analyze", formData, {
  headers: { "Content-Type": "multipart/form-data" },
});

const { findings, validation_issues } = response.data;
```

**Success Response (200)**

```json
{
  "findings": [
    {
      "rule_id": "THK-001",
      "rule_name": "Minimum Wall Thickness",
      "guideline_ref": "DFM-GUIDE-4.2.1",
      "status": "COMPLIANT",
      "location": "face 1",
      "measured": "2.340",
      "agent_commentary": null,
      "agent_confidence": null
    },
    {
      "rule_id": "COS-001",
      "rule_name": "Avoid Unnecessary Cosmetic Features",
      "guideline_ref": "DFM-GUIDE-2.5.0",
      "status": "REVIEW",
      "location": "face 12",
      "measured": null,
      "agent_commentary": "This is a qualitative rule. Please review the design intent...",
      "agent_confidence": 0.85
    }
  ],
  "validation_issues": [
    {
      "finding": "THK-001",
      "issue": "fail verdict without measurement"
    }
  ]
}
```

**Finding Object Fields**

| Field | Type | Description |
|---|---|---|
| `rule_id` | `string` | Rule identifier (e.g., "THK-001") |
| `rule_name` | `string` | Human-readable rule name |
| `guideline_ref` | `string` | Reference to the DFM guideline document |
| `status` | `string` enum | One of: `COMPLIANT`, `NON-COMPLIANT`, `REVIEW` |
| `location` | `string` | Face reference (e.g., "face 123") |
| `measured` | `string` or `null` | Measured value formatted to 3 decimal places |
| `agent_commentary` | `string` or `null` | AI-generated commentary (only for REVIEW findings) |
| `agent_confidence` | `float` or `null` | AI confidence score 0.0-1.0 (only for REVIEW findings) |

**Validation Issue Object Fields**

| Field | Type | Description |
|---|---|---|
| `finding` | `string` | The rule_id of the finding with the issue |
| `issue` | `string` | Description of the internal consistency issue |

**Status Determination Logic**

```
QUANTITATIVE rules (e.g., THK-001):
  ├── Face has metric value AND predicate passes → COMPLIANT
  ├── Face has metric value AND predicate fails  → NON-COMPLIANT
  └── Face has no metric value                   → REVIEW

QUALITATIVE rules (e.g., COS-001):
  └── Always → REVIEW (requires human/AI judgment)

NOT-YET-COMPUTABLE rules (e.g., UND-001):
  └── Always → REVIEW (no predicate defined)
```

**Predicate Evaluation**

Supported operators in the rules catalog:

| Operator | Meaning | Example |
|---|---|---|
| `>` | Greater than | `value > 1.0` |
| `>=` | Greater than or equal | `value >= 1.0` |
| `<` | Less than | `value < 0.5` |
| `<=` | Less than or equal | `value <= 100` |
| `==` | Equal to | `value == 0` |

**Error Responses**

| Status | Condition | Response |
|---|---|---|
| 400 | Invalid file type (not .step/.stp) | `{"detail": "Invalid file type. Please upload a .step or .stp file."}` |
| 500 | File not found during processing | `{"detail": "<error message>"}` |
| 500 | Unexpected server error | `{"detail": "An unexpected server error occurred: <message>"}` |

**Example Error (cURL)**

```bash
# Upload wrong file type
curl -X POST http://127.0.0.1:8001/api/analyze \
  -F "file=@document.pdf"

# Response: 400
{
  "detail": "Invalid file type. Please upload a .step or .stp file."
}
```

---

## Pipeline Steps

The `/api/analyze` endpoint executes these steps sequentially:

| Step | Module | Duration | Description |
|---|---|---|---|
| 1. Ingest | `step_loader.py` | ~0.5s | Parse STEP file into OpenCascade TopoDS_Shape |
| 2. Extract | `features.py` | ~1-3s | Compute face normals, draft angles, wall thickness |
| 3. Execute | `pipeline.py` | ~0.1s | Evaluate DFM rules against each face |
| 4. Interpret | `orchestrator.py` | ~0s (mock) | Generate AI commentary for REVIEW findings |
| 5. Validate | `orchestrator.py` | ~0.05s | Cross-check findings for internal consistency |

**Total typical duration**: 2-10 seconds depending on part complexity (number of faces).

---

## Rules Catalog

The rules evaluated during Step 3 are defined in `backend/rules_catalog.json`:

### THK-001: Minimum Wall Thickness

| Property | Value |
|---|---|
| Type | Quantitative |
| Metric | `wall_thickness` |
| Predicate | `>= 1.0` (mm) |
| Guideline | DFM-GUIDE-4.2.1 |
| Description | Wall thickness should be at least 1.0mm to ensure proper mold filling. |

### UND-001: Undercut Detection

| Property | Value |
|---|---|
| Type | Not yet computable |
| Guideline | DFM-GUIDE-6.1.0 |
| Description | Identifies undercuts that would prevent the part from being ejected. |

### COS-001: Avoid Unnecessary Cosmetic Features

| Property | Value |
|---|---|
| Type | Qualitative (AI Review) |
| Guideline | DFM-GUIDE-2.5.0 |
| Description | Complex textures or features can increase mold cost and wear. |

---

## CORS Configuration

The API currently allows all origins for development:

```python
allow_origins=["*"]
allow_credentials=True
allow_methods=["*"]
allow_headers=["*"]
```

**Production**: Restrict `allow_origins` to the frontend domain.

---

## Configuration

| Constant | Value | Location |
|---|---|---|
| Pull Direction | Z-axis `(0, 0, 1)` | `main.py:29` |
| Rules Catalog Path | `backend/rules_catalog.json` | `main.py:26` |
| Server Host | `127.0.0.1` | CLI argument |
| Server Port | `8001` | CLI argument |
| Vite Proxy Target | `http://127.0.0.1:8001` | `vite.config.js:10` |

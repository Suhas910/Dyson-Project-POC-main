# Demo Guide

> Step-by-step guide for presenting the Dyson DFM Analysis Tool to stakeholders.

---

## Pre-Demo Checklist

- [ ] Backend server running at `http://127.0.0.1:8001`
- [ ] Frontend dev server running at `http://localhost:5173`
- [ ] Browser open to `http://localhost:5173`
- [ ] At least one sample `.step` file ready for upload
- [ ] Screen shared / projector connected

## Demo Script

### Act 1: Introduction (2 minutes)

**Narrate while showing the landing page:**

> "This is the Dyson DFM Analysis Tool - a web-based system that automates Design for Manufacturability review for injection-moulded parts. Instead of an engineer manually checking each face of a 3D model against dozens of manufacturing guidelines, this tool does it automatically using a 5-stage analysis pipeline with AI-assisted commentary."

**Key talking points:**
- Upload a STEP file, get instant DFM compliance analysis
- 5-step pipeline: Ingest, Extract, Execute, Interpret, Validate
- AI agent provides expert commentary for edge cases
- Dashboard gives at-a-glance compliance overview

**Show the landing page features:**
- The empty-state dashboard with the "DFM Analysis Dashboard" heading
- The three rule badges at the bottom (THK-001, UND-001, COS-001)
- The "Upload STEP File" button in the top-right header

---

### Act 2: File Upload & Pipeline (3 minutes)

**Action:** Click "Upload STEP File" and select a sample `.step` file.

**Narrate during upload:**

> "I'm uploading a STEP file - this is the standard CAD exchange format used by SolidWorks, CATIA, NX, and other major CAD tools. The file is validated on both the client side (file type + size check) and the server side."

**Show the animated pipeline stepper:**

> "Watch the 5-step pipeline progress across the top of the dashboard."

Walk through each step as it animates:

| Step | What it does | Talk track |
|---|---|---|
| **Ingest** | Parses STEP geometry via OpenCascade | "First, the system reads the STEP file and loads it into our CAD geometry kernel - OpenCascade." |
| **Extract** | Computes face normals, draft angles, wall thickness | "Then it analyzes every single face - computing normal vectors, draft angles relative to the pull direction, and wall thickness using ray-casting." |
| **Execute** | Evaluates DFM rules against each face | "Each face is checked against our DFM rules catalog. Wall thickness must be at least 1mm, undercuts are flagged, cosmetic features are flagged for review." |
| **Interpret** | AI generates commentary for REVIEW items | "For edge cases that need human judgment, the AI agent generates expert commentary explaining why the feature needs review." |
| **Validate** | Cross-checks findings for consistency | "Finally, the system validates its own findings - checking for unknown face IDs, missing measurements on non-compliant items, and other data integrity issues." |

---

### Act 3: Summary Dashboard (2 minutes)

**Point to the summary cards that appeared:**

> "Immediately after analysis, you get a high-level overview."

Walk through each card:

- **Total Findings**: "Every face x every rule generates a finding. So 50 faces x 3 rules = 150 findings."
- **Compliant (green)**: "These faces pass the rule threshold."
- **Non-Compliant (red)**: "These faces fail - they need design changes."
- **Under Review (amber)**: "These need human judgment - the AI provides commentary."
- **Overall Verdict**: "PASS if zero non-compliant findings, FAIL otherwise."

**Point to the compliance bar:**
> "The compliance rate bar gives you an instant visual gauge of manufacturability."

---

### Act 4: Rules Catalog Sidebar (2 minutes)

**Point to the left panel:**

> "On the left, you see the DFM rules that were applied. Each card shows:"

For each rule (THK-001, UND-001, COS-001):
- **Rule ID and name** with status icon (check/X/hourglass)
- **Guideline reference** (e.g., DFM-GUIDE-4.2.1)
- **Description** of what the rule checks
- **Predicate** for quantitative rules (e.g., ">= 1.0mm")
- **Rule type badge**: Quantitative, Qualitative (AI Review), or Not Yet Computable
- **Per-rule statistics**: How many faces checked, compliant, non-compliant, review

---

### Act 5: Findings Table (5 minutes)

**This is the core of the demo. Point to the right panel:**

> "The detailed findings table shows every individual check."

**Demonstrate each feature:**

#### Status Filter Tabs
> "Click the tabs to filter by status - All, Compliant, Non-Compliant, or Review. The counts update in real-time."

Click each tab to demonstrate.

#### Search
> "Type in the search box to filter across all fields - rule IDs, names, locations, guidelines, measurements, even AI commentary."

Type "THK" to show only wall thickness findings.
Clear the search.

#### Column Sorting
> "Click any column header to sort. Click again to reverse."

Click "Status" header to sort by status.
Click "Measured" header to show thinnest faces first.

#### Finding Row Detail
Point to a specific non-compliant row (highlighted in light red):

> "For this non-compliant finding, you can see:"
- **Rule ID**: "THK-001" in a monospace chip
- **Rule Name**: "Minimum Wall Thickness"
- **Guideline**: "DFM-GUIDE-4.2.1"
- **Status**: Red "NON-COMPLIANT" chip
- **Location**: "face 42" - exact face in the 3D model
- **Measured**: "0.650mm" - the actual wall thickness

Point to a REVIEW finding:
> "For review items, you also get AI commentary and confidence score."
- **AI Commentary**: Truncated with tooltip on hover
- **AI Confidence**: Color-coded chip (green >= 70%, amber >= 40%, red < 40%)

#### CSV Export
> "Click the download icon to export the currently filtered results to a CSV file for sharing with the team or importing into your PLM system."

Click the download button to demonstrate.

#### Pagination
> "The table paginates with configurable rows per page - 5, 10, 15, 25, or 50."

---

### Act 6: Validation Issues (1 minute)

**If validation issues exist, point to the warning panel:**

> "The system also validates its own output. If there are data integrity issues - like a finding referencing a face that doesn't exist, or a non-compliant finding without a measurement - they appear here as warnings."

This shows the self-checking capability of the pipeline.

---

### Act 7: Error Handling (1 minute)

**Demonstrate error states:**

1. **Wrong file type**: "If I try to upload a PDF..." (show the client-side alert)
2. **Server error**: "If the backend isn't running..." (show the network error message with Retry button)

> "The system provides clear, actionable error messages with a one-click retry."

---

### Act 8: Closing (1 minute)

**Summarize:**

> "In summary, this tool:
> 1. **Automates** what would take hours of manual review into seconds
> 2. **Standardizes** DFM checking with a configurable rules catalog
> 3. **Augments** human judgment with AI commentary for edge cases
> 4. **Validates** its own output for data integrity
> 5. **Exports** results for downstream workflows

> The rules catalog is extensible - adding new DFM rules is as simple as adding a JSON entry. The AI agent architecture supports swapping in real LLM providers for production use."

---

## Key Metrics to Highlight

| Metric | Value |
|---|---|
| Analysis time | ~5-10 seconds for typical parts |
| Rules evaluated per face | 3 (expandable) |
| Pipeline steps | 5 (Ingest, Extract, Execute, Interpret, Validate) |
| Finding fields | 8 (rule_id, rule_name, guideline_ref, status, location, measured, agent_commentary, agent_confidence) |
| Export formats | CSV |
| File size limit | 100 MB |
| Supported formats | .step, .stp |

## Troubleshooting During Demo

| Issue | Solution |
|---|---|
| Backend not connecting | Ensure `uvicorn main:app --host 127.0.0.1 --port 8001` is running from `backend/` |
| Port 5173 in use | Vite auto-selects next port (5174, 5175...) - check terminal output |
| Slow analysis | Large STEP files (100+ faces) take longer - use a simpler demo part |
| No findings showing | Check browser console for CORS or proxy errors |

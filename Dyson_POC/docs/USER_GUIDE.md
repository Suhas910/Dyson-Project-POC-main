# User Guide

> How to use the Dyson DFM Analysis Dashboard.

---

## Getting Started

1. Open the application in your browser: **http://localhost:5173**
2. You will see the **DFM Analysis Dashboard** landing page with an empty state
3. Click **"Upload STEP File"** in the top-right corner to begin

## Uploading a File

1. Click the **"Upload STEP File"** button (teal, top-right)
2. Select a file from your computer:
   - **Supported formats**: `.step` or `.stp`
   - **Maximum size**: 100 MB
3. The file name and size will appear in the header bar as a chip
4. Analysis begins automatically

**File validation:**
- If you select a non-STEP file, an alert will tell you to choose `.step` or `.stp`
- If the file exceeds 100 MB, an alert will tell you the file is too large
- The server also validates the file type before processing

## Watching the Pipeline

During analysis, the **Pipeline Stepper** appears showing 5 stages:

```
● Ingest → ● Extract → ● Execute → ● Interpret → ● Validate
```

| Step | What It Does |
|---|---|
| **Ingest** | Loads your STEP file geometry |
| **Extract** | Computes face normals, draft angles, and wall thickness |
| **Execute** | Evaluates DFM rules against every face |
| **Interpret** | Generates AI commentary for edge cases |
| **Validate** | Cross-checks findings for consistency |

Each step animates with a pulsing icon while active, then shows a green checkmark when complete. The entire process typically takes 2-10 seconds.

## Understanding the Results

### Summary Dashboard

After analysis completes, the **Summary Dashboard** shows five cards:

| Card | Meaning |
|---|---|
| **Total Findings** | Number of rule evaluations (faces x rules) |
| **Compliant** | Faces that pass all applicable rules (green) |
| **Non-Compliant** | Faces that fail a rule and need design changes (red) |
| **Under Review** | Faces needing human or AI judgment (amber) |
| **Overall Verdict** | **PASS** if 0 non-compliant, **FAIL** otherwise |

Below the cards, a **Compliance Rate** progress bar shows the overall compliance percentage.

### Validation Issues Panel

If internal consistency issues are detected, a **collapsible warning panel** appears:

- Click the panel header to expand/collapse
- Each issue shows the affected rule ID and the problem description
- Common issues: "fail verdict without measurement"

### DFM Rules Catalog (Left Panel)

The left sidebar shows the three DFM rules that were applied:

Each rule card displays:
- **Rule ID** (e.g., THK-001) and **name**
- **Status icon**: checkmark (all compliant), X (any non-compliant), hourglass (any review)
- **Guideline reference** (e.g., DFM-GUIDE-4.2.1)
- **Description** of what the rule checks
- **Predicate** for quantitative rules (e.g., ">= 1.0mm")
- **Type badge**: Quantitative, Qualitative (AI Review), or Not Yet Computable
- **Per-rule statistics**: faces checked, compliant, non-compliant, review counts

### Findings Table (Right Panel)

The main table shows every individual finding with 8 columns:

| Column | Description |
|---|---|
| **Rule ID** | Monospace chip showing the rule identifier |
| **Rule Name** | Human-readable rule name |
| **Guideline** | Reference to the DFM guideline document |
| **Status** | Color-coded chip: green (COMPLIANT), red (NON-COMPLIANT), amber (REVIEW) |
| **Location** | The face in the 3D model (e.g., "face 42") |
| **Measured** | The measured value, or "N/A" if not available |
| **AI Commentary** | Truncated text with hover tooltip (REVIEW findings only) |
| **AI Confidence** | Percentage chip, color-coded by confidence level |

**Non-compliant rows** are highlighted with a subtle red background.

## Interacting with the Table

### Filtering by Status

Use the **tabs** above the table to filter:

| Tab | Shows |
|---|---|
| **All (N)** | Every finding |
| **Compliant (N)** | Only passing findings |
| **Non-Compliant (N)** | Only failing findings |
| **Review (N)** | Only findings needing review |

The counts in parentheses update based on current results.

### Searching

Type in the **search box** to filter across all text fields:
- Rule ID, rule name, guideline reference
- Location, measured value
- AI commentary text

The search is case-insensitive and matches partial strings.

### Sorting

**Click any column header** to sort:
- First click: ascending order
- Second click: descending order
- Third click: back to ascending

The active sort column shows an arrow indicator.

### Pagination

The table paginates automatically. Use the **pagination controls** at the bottom to:
- Navigate between pages
- Change rows per page: 5, 10, 15 (default), 25, or 50

### Exporting to CSV

Click the **download icon** (arrow-down) in the table header to export the **currently filtered** results to a CSV file.

The exported file `dfm_analysis_report.csv` includes all 8 columns and respects your current search/filter state.

## Clearing Results

Click the **X** on the file chip in the header to clear the current analysis and return to the landing page.

## Retrying After Errors

If an error occurs during analysis:
- An **error banner** appears with the error message
- Click the **"Retry"** button to re-run the analysis on the same file
- The backend must be running at `http://127.0.0.1:8001` for analysis to work

## Error Messages

| Error | Meaning | Solution |
|---|---|---|
| "Cannot connect to the analysis server" | Backend is not running | Start the backend: `cd backend && uvicorn main:app --host 127.0.0.1 --port 8001` |
| "Invalid file type" | Uploaded file is not .step/.stp | Select a STEP format file |
| "File is too large" | File exceeds 100 MB | Use a smaller STEP file |
| "Server encountered an error" | Internal backend error | Check backend logs, retry |

## Tips

- **Simple parts first**: Start with a small STEP file (10-30 faces) for faster results
- **Focus on NON-COMPLIANT**: These are the findings that require design changes
- **Use the search**: Type a face number (e.g., "face 42") to find all rules for that face
- **Export for review**: Export CSV and share with the design team for offline review
- **Check validation issues**: They indicate data quality problems that may affect the analysis

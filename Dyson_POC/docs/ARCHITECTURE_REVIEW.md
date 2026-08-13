# Dyson DFM POC — Architecture Review & Improvement Report

**Date:** 2026-08-11 · **Scope:** full-stack review of validation methodology, agent architecture, UI, and PLM/version-control roadmap.

---

## 1. Executive Summary

The POC has a **sound skeleton**: a clean 5-step pipeline (ingest → extract → execute → interpret → validate), a well-structured 249-rule catalog, deterministic rule verdicts that the LLM cannot overturn (a genuinely good invariant), and a working version-compare feature. The separation `step_loader → features → pipeline → orchestrator` is the right shape.

However, the review found **three serious correctness defects in the geometry methodology** (face-orientation-blind normals, a wrong draft-angle formula, and untrimmed-surface sampling), a **rule-execution model that produces contradictory and inflated findings** (every rule × every face, material-specific rules applied simultaneously), and an **"AI" layer that is currently cosmetic** (a mock returning identical canned text for every finding). A client demo in the current state would not survive scrutiny of any single findings row.

None of these are fatal. Section-by-section fixes below, ordered by what matters for the client demo.

**Verdict on "is it doing something intelligent?"** — Not yet. Today it is a deterministic geometry checker with a placeholder where the intelligence should be. Section 5 gives a concrete path to make it *genuinely* intelligent (not just appear so), most of which is achievable in days, not months.

---

## 2. Geometry Validation Methodology — Is the Method Right?

### 2.1 CONFIRMED DEFECT — Face normals ignore topological orientation
**Where:** `features.py:59-86` (`_get_face_normal_and_uv`)

The normal is taken from the *geometric surface* (`GeomLProp_SLProps`). In a B-rep solid, roughly half the faces have `TopAbs_REVERSED` orientation, meaning the true outward normal is the **opposite** of the surface normal. The code never checks `face.Orientation()` — yet `calculate_cylindrical_properties` (features.py:250) *does* check it to distinguish holes from bosses, so the codebase is internally inconsistent about this.

**Consequences:**
- The wall-thickness ray (`face_normal.Reversed()`, features.py:327) points **outward instead of inward** on reversed faces.
- Draft angle direction/sign is lost — you cannot tell a 2° drafted wall from a 2° **undercut**. This is precisely why `UND-001` is marked "not yet computable": with correctly oriented normals, first-order undercut detection *becomes computable* (any face whose outward normal has a negative component along the pull direction, i.e. visible only from below, is an undercut candidate).

**Fix (small):**
```python
normal = props.Normal()
if face.Orientation() == TopAbs_REVERSED:
    normal.Reverse()
```

### 2.2 CONFIRMED DEFECT — Draft angle formula is wrong
**Where:** `features.py:99-116` (`calculate_draft_angle`)

The code returns the angle between the face normal and the pull direction, folded into [0°, 90°]. By convention, **draft angle is measured between the wall surface and the pull direction**, i.e. `draft = 90° − angle(normal, pull)`:

- A vertical wall (zero draft — the case rules exist to catch) has its normal **perpendicular** to pull → code reports **90°**, sailing past any "draft ≥ 1°" rule.
- A horizontal top face (draft is meaningless) → code reports 0° → flagged NON-COMPLIANT.

**The current implementation inverts every draft verdict.** Fix:
```python
theta = math.degrees(face_normal.Angle(pull_direction))  # oriented normal from 2.1
draft = 90.0 - theta      # negative ⇒ undercut; ~90 ⇒ horizontal face, exempt from draft rules
```
Signed draft also gives you undercut detection for free (draft < 0), plus the ability to *exempt* faces perpendicular to pull instead of failing them.

### 2.3 CONFIRMED DEFECT — Sample point may not lie on the face
**Where:** `features.py:70-74`

`surface.Bounds()` returns the bounds of the **untrimmed underlying surface**, not the trimmed face. For a face with a central hole, an L-shaped trim, or a small face on a large plane, the midpoint of the surface's parametric domain can fall **outside the face entirely** — thickness and draft are then measured at a point that isn't on the part.

**Fix:** use `BRepTools.UVBounds(face)` for the trimmed bounds, then verify with `BRepClass_FaceClassifier` that the (u,v) point is `TopAbs_IN`; if not, walk a small grid until an interior point is found.

### 2.4 DEFECT — Wall thickness: single ray, unbounded parameter range, chord ≠ thickness
**Where:** `features.py:119-165`

1. **Single sample per face.** A face is thin *somewhere*, not everywhere. One midpoint sample misses sink-mark ribs, local thinning, and thick bosses on the same face. Minimum: sample a 3×3–5×5 UV grid of interior points and report min/median.
2. **`intersector.Perform(ray, -1e6, 1e6)`** (features.py:148) accepts intersections **behind** the start point (negative parameters). A face behind the origin (outside the material) can produce a bogus "thickness". Use `0.0` (plus epsilon) as the lower bound.
3. **Ray distance is a chord, not thickness.** Near corners and on curved/tapered walls, ray-along-normal overestimates true (perpendicular) thickness. The industry-standard method is the **rolling-sphere** (largest inscribed sphere) measure used by NX/CATIA wall-thickness checkers. A pragmatic middle ground: cast the normal ray *and* rays at ±15° cones and take the minimum; or mesh the part (`BRepMesh_IncrementalMesh`) and use a mesh-based inscribed-sphere via VTK (already installed with pythonocc).

### 2.5 DEFECT — Rule execution: every rule × every face, no applicability model
**Where:** `pipeline.py:100-102`, `execute_rules`

Three compounding problems:

1. **Material is not an input.** IM-001 (ABS: 1.14–3.56 mm), IM-002 (PC), IM-003 (PP) are *alternatives* — a part is one material — yet all are evaluated against every face simultaneously. The same wall can be COMPLIANT (PP) and NON-COMPLIANT (ABS) in the same report. The validation step doesn't catch this contradiction. **Fix:** add a material selector to the upload form; filter rules by `(process_family, material)`. The catalog already encodes material in `rule_name` — promote it to a first-class field.
2. **No feature applicability.** Hole rules run against non-hole faces, bend-radius rules against flat faces, etc. Each inapplicable evaluation becomes a `REVIEW` row. A 500-face part under Injection Moulding (70 rules) yields **35,000 findings**, most meaningless. Rules need an `applies_to` field (`face_class: hole | fillet | wall | boss | rib | any`) checked before evaluating — "metric not present" should yield **no finding**, not a REVIEW finding.
3. **`REVIEW` conflates three different states:** (a) genuinely qualitative rules needing judgment, (b) `quantitative_not_yet_computable` rules (43 of them — a capability gap, not a per-face question), (c) evaluation errors. Split the status enum: `COMPLIANT / NON-COMPLIANT / NEEDS_REVIEW / NOT_EVALUATED / ERROR`. Only `NEEDS_REVIEW` ever reaches the LLM.

### 2.6 Smaller methodology issues
- **Substring metric mapping** (`pipeline.py:110-143`): `"depth" in metric_key` style matching is order-dependent and will silently mis-map future metrics (e.g. `thread_depth` → `hole_depth`). Replace with an explicit `metric → extractor` registry dict; unknown metric = `NOT_EVALUATED` + a startup warning listing unmapped metrics.
- **~~No unit normalization~~ — CORRECTED, not a defect.** The initial draft of this review flagged inch-authored STEP files as a silent 25.4× risk. Testing disproved it: a hand-crafted STEP file declaring `CONVERSION_BASED_UNIT('INCH')` imports at exactly 645.16 mm for a 25.4-inch box, so OCCT's translator does convert to the cascade unit as documented. The residual risk is only that `xstep.cascade.unit` is a process-global that other code could change, so it is now pinned explicitly and the file's declared unit is reported as metadata. Covered by `test_unit_conversion_from_inches`.
- **`reader.OneShape()`** merges all roots: a multi-body file or small assembly gets fused, and thickness rays can cross *between* bodies. Iterate roots/solids and analyze per-solid.
- **No shape healing:** run `ShapeFix_Shape` on import and record whether the shape is a closed solid — a moulded-part analysis on an open shell is meaningless, and "is this actually a solid?" belongs in the validation report.
- **Fillets classified as holes:** any reversed cylindrical face is treated as a hole (`features.py:340`). An internal fillet then gets `hole_to_edge_distance` and hole-depth rules. Minimum discriminator: axis orientation + adjacency (a hole's cylinder is bounded by two edge loops on roughly parallel planar faces; a fillet is tangent to its neighbors).

### 2.7 NEW — The rule catalog itself contains defective predicates

Found while implementing the fixes. These are not code bugs; the **data** is wrong, and every one of them produces a confident but incorrect verdict. The catalog is generated from a spreadsheet (`dfx_rules_catalog.csv` → `convert_csv_to_json.py`), and the conversion lost information in three distinct ways:

| Class | Count | Example | Consequence |
|---|---|---|---|
| Spreadsheet date corruption | 2 | `MC-018` source text `"Mar-40"` — Excel's reading of `"3-40"` — parsed to `threshold: -40.0` | A negative threshold on a width; the rule can never fail |
| Sign / percentage mis-parse | 1 | `MIM-012` `"±0.3% to ±0.5%"` → `min: -0.5, max: -0.3` | Range of negative tolerances; meaningless |
| Ratio rules with the reference dropped | 19 | `SM-001` `"1xT"` (one material thickness) → `threshold: 1.0` | Compared against an absolute mm measurement — a 3 mm bend radius on 3 mm sheet passes a rule it should exactly meet, and a 1.5 mm radius on 0.5 mm sheet fails one it passes |

A fourth class is a **semantic** loss rather than a parse error: the catalog does not distinguish a **limit** from a **recommendation**. `IM-011` "Standard draft for typical surfaces" carries range 1–2°, but a wall with 3° of draft is *better* than typical, not non-compliant. Evaluated as a hard range it fails correct designs — and it was doing exactly that, contradicting `IM-010` (min 0.5°) on the same face until advisory handling was added.

**Fix implemented:** a catalog linter (`rule_scoping.lint_catalog`) runs at analysis time and refuses to let a rule with a corrupt predicate produce a verdict, reporting `NOT_EVALUATED` with the reason instead. Ratio rules whose reference can be reconstructed (bend radius vs. material thickness, corner radius vs. pocket depth) are evaluated as ratios against a part-level nominal thickness; the rest are explicitly blocked. Advisory metrics can raise `NEEDS_REVIEW` but never `NON-COMPLIANT`.

**Still recommended:** fix the source CSV. The linter prevents wrong verdicts but cannot recover the lost thresholds — `MC-018`'s real range is unknowable from `"Mar-40"`. Re-export the spreadsheet with all columns as text, and add a `reference` column (`absolute` / `material_thickness` / `hole_diameter` / `depth`) plus a `rule_type` column (`limit` / `recommendation`) so both losses become impossible.

### 2.8 How to *prove* the method is right — calibration test suite (highest-leverage item)
You asked "I want to check if the method is right." The answer for geometry code is a **golden-part regression suite** — you cannot eyeball correctness from findings tables:

1. Generate synthetic STEP parts with *known* answers using OCC primitives (`BRepPrimAPI`): a 2 mm-wall open box, a plate with a Ø5 mm hole 3 mm from the edge, a drafted boss at exactly 1.5°, a block with a deliberate undercut.
2. `pytest` asserts each measured value within tolerance (e.g. `assert abs(t - 2.0) < 0.05`).
3. Run in CI on every change to `features.py`.

This suite would have caught defects 2.1–2.4 immediately, and it doubles as client-facing evidence: "our checker is validated against parts with known ground truth" is itself a credibility feature. Put a "Calibration: 24/24 checks passing" badge in the report footer.

---

## 3. Deterministic vs. AI — What Should Validate What

Your instinct is right: **verify values with code, reserve agents for judgment.** The decision rule:

> *If a rule has a measurable metric and a numeric threshold → deterministic code. If a rule requires design intent, aesthetics, or trade-off judgment → agent. Never let the agent produce a pass/fail verdict on a measurable rule.*

The codebase already honors the second half (orchestrator.py's "verdict key from the model is IGNORED by design" — keep and *advertise* this invariant to the client as "AI-assisted, never AI-decided").

| Layer | Technology | What it validates | Status |
|---|---|---|---|
| File & metadata | Pure Python + regex + Pydantic | STEP header (schema, units, author, timestamp), filename/revision patterns, catalog schema validation on startup | **Missing — add** (cheap, quick win) |
| Geometry metrics | Python + OCCT (pythonocc) | thickness, draft, radii, distances — the 138 quantitative rules | Present, defective per §2 |
| Predicate evaluation | Pure Python (`_safe_predicate_eval`) | threshold/range checks | Present, sound |
| Feature recognition | Python + OCCT adjacency analysis (optional ML later) | hole vs fillet vs boss vs rib classification — unlocks `applies_to` scoping | **Missing — add** |
| Qualitative judgment | LLM agent (real, not mock) | the 68 qualitative rules, at part/feature level | Mocked |
| Cross-check audit | Deterministic + LLM spot-check | contradictions, coverage, unit sanity | Minimal (orchestrator.py:62 checks only face-id validity and fail-without-measurement) |

**On Rust:** not justified here. OCCT is already C++ under Python bindings; the bottleneck is algorithm choice (single ray vs sphere method), not language. Revisit only if you later need to sweep thousands of assemblies server-side — and even then, profile first. Adding Rust now would add build complexity with zero demo value.

**On regex:** regex cannot parse B-rep geometry, but it *is* the right tool for the metadata layer: validating `original_csv_text` threshold strings when ingesting the rules CSV, extracting part number/revision from filenames (`^(?P<part>[A-Z]{2,4}-\d{4,6})[-_](?P<rev>[A-Z]\d?)`), and STEP header fields. Add a `catalog_lint.py` that regex-validates all 249 rules at startup and refuses to boot on malformed predicates — right now a bad rule silently degrades to REVIEW (pipeline.py:163-167).

**Strengthen the validation step** (orchestrator.validate) with: contradictory-verdict detection (same face, same metric, conflicting material rules), unit sanity (median wall thickness of 0.04 mm or 40 mm ⇒ probable unit error), solid-closure check, and a coverage summary ("138 quantitative rules: 61 evaluated, 9 not applicable to this geometry, 68 not evaluated — no extractor"). Coverage honesty reads as rigor in front of a client.

---

## 4. Agent Architecture Review

### What's wrong today
1. **The mock is a demo landmine** (`pipeline.py:24-34`): every REVIEW row shows the *identical* sentence and the identical 0.85 confidence. One client question — "why do all the comments say the same thing?" — collapses the illusion.
2. **The prompt cannot produce intelligence** (`prompts/interpretive_rule.md`): the agent receives only rule name, guideline ref, "face 214", and part name + face count. Even a real frontier model can only emit generic filler from that context. Garbage in, generic out.
3. **Per-finding calls don't scale:** the orchestrator loops one blocking LLM call per REVIEW finding. Because qualitative rules currently fire per *face* (§2.5), a real integration would make thousands of calls per upload.
4. **It isn't an agent.** `agents/orchestrator.py` is a prompt-template filler: one-shot, no tools, no ability to inspect geometry, no cross-finding reasoning.

### What's right (keep these)
- The **injected `llm_call`** dependency — swappable provider, testable with a stub.
- The **verdict invariant** — LLM enriches, never decides.
- Constrained JSON output with graceful parse-failure handling (`agent-output-unparseable; kept for human review` is exactly right).

### Target architecture
```
Tier 0  Deterministic verdicts        (exists — fix per §2)
Tier 1  Feature recognition           (new, deterministic) → semantic features:
        "Boss B3, Ø8mm, 12mm tall, near parting line" instead of "face 214"
Tier 2  Interpretive agent            (per feature-cluster, not per face)
        Context: full face metrics + neighboring faces + rendered snapshot
        image of the feature (multimodal) + rule text + guideline excerpt (RAG)
        Batched, async, cached by (rule_id, feature_signature)
Tier 3  Part-review agent             (one call per part)
        Input: all findings + part context. Output: executive summary,
        top-5 prioritized risks, cross-finding insights ("the thin wall at
        F12 and the deep rib at F31 will jointly cause sink marks"), with
        tool access to request extra measurements (an actual agentic loop)
Audit   Deterministic cross-checks + LLM spot-audit of a sample of verdicts
```
Use Claude (`claude-sonnet-5` for Tier 2 volume, `claude-opus-5` for the Tier 3 summary) with **structured outputs / tool-forced JSON** instead of parse-and-pray, and **vision**: pythonocc can render off-screen face snapshots; a multimodal agent that *comments on a picture of the actual feature* is both genuinely smarter and visibly impressive.

---

## 5. Is the POC Actually Intelligent? How to Make It So

**Honest assessment:** No. Today the pipeline is: parse file → measure numbers → compare with thresholds → attach one canned sentence. The intelligence is aspirational. That is *fine* for a skeleton, but the demo story must change before the client sees it.

Ranked by demo-impact per engineering day:

1. **Real LLM part-level executive summary (Tier 3).** One API call. Transforms the top of the report from stat cards into a prioritized engineering narrative naming the top risks, their manufacturing consequences (sink, warp, tooling cost), and suggested design changes. This single feature does more for perceived intelligence than everything else combined.
2. **Kill the identical-comment problem.** Even before full Tier 2: batch REVIEW findings into one real LLM call with actual measured context per finding, so every comment is specific and distinct.
3. **Feature recognition + human language.** "Boss near parting line" vs "face 214" — reads as understanding, is genuinely understanding.
4. **3D viewer with color-coded faces.** `three.js` is already in the frontend's dependency tree. Mesh the shape server-side (`BRepMesh` → glTF, keeping face IDs), render it with findings painted red/amber/green, click a face → jump to its findings. This is the "wow" moment of any DFM demo and turns face IDs from noise into navigation.
5. **"Ask about your part" chat panel.** A chat box grounded in the findings JSON ("Which walls fail for ABS and what should we change?"). Cheap to build on the same findings context; extremely demo-able.
6. **RAG over DFM guidelines.** Commentary that quotes the actual guideline paragraph (with ref) reads as expert, not generic.
7. **Real pipeline progress.** The stepper is a timer animation (`PipelineStepper.jsx:83-100`). Run analysis as a background job, stream real step events via SSE; the stepper then reflects the actual pipeline — also fixes the blocking-endpoint problem (the CPU-bound pipeline currently runs inside the async handler, freezing the server during analysis, `main.py:103`).
8. **Confidence & severity everywhere.** The catalog already has `severity` — surface it (it is currently dropped before the frontend). Sort the table by severity, not rule order.

---

## 6. Siemens Integration, Version Control & Semantic Search

### 6.1 Simulating the Siemens/Teamcenter source (now) → real APIs (later)
Build a small **mock PLM connector** service (`plm_connector/`, FastAPI, port 8002) exposing Teamcenter-shaped endpoints:

```
GET  /plm/items                     → part list (item id, name, owner)
GET  /plm/items/{id}/revisions      → [{rev: "A", status: "Released", modified_by, date}, ...]
GET  /plm/items/{id}/revisions/{rev}/files → STEP dataset download
```
Back it with a seeded folder of STEP files (rev A = flawed part, rev B = corrected part — author these with OCC scripts so the fix story is scripted and repeatable). In the UI, add an **"Import from PLM"** dialog beside Upload: browse items → pick revision → analyze. Demo line: *"today this is a stub; in production the same adapter interface calls Teamcenter's Active Workspace REST gateway (`/tc/restful`) or consumes PLMXML exports — the pipeline doesn't change."* Define a Python `PLMAdapter` protocol (`list_items / list_revisions / fetch_file`) with `MockTeamcenterAdapter` now and `TeamcenterAdapter` later, so that sentence is literally true.

### 6.2 File version control — layered identity, metadata first
Your instinct is correct: **metadata confirms identity; semantic search is the fallback, not the primary.** Layer the checks, cheapest first:

| Layer | Mechanism | Answers |
|---|---|---|
| 1. Content hash | SHA-256 of file bytes at upload | "Exact same file already analyzed?" → dedupe, reuse results |
| 2. Declared metadata | Part number + revision from PLM metadata or filename regex; STEP header (`FILE_NAME` author/date) | "Which part, which revision?" → lineage |
| 3. Geometry fingerprint | Vector of invariants: volume, surface area, bbox dims (sorted), face/edge/solid counts, face-type histogram (plane/cylinder/cone/spline %), inertia moments | "Is this a rework of a known part despite a renamed file?" → nearest-neighbor over stored fingerprints; >0.98 similarity ⇒ suggest linking as new revision |
| 4. Semantic search | Embeddings of title/description/notes in sqlite-vec or FAISS | Fuzzy discovery: "find parts similar to X" |

For CAD, **layer 3 outperforms text embeddings** — geometry invariants are cheap to compute with OCCT (`GProp_GProps`) and robust to renames; text embeddings only see filenames. Implement 1–3 for the POC; mention 4 as roadmap unless part descriptions exist.

Schema (replacing the flat `analysis_versions` table):
```sql
parts(id, part_number, title, source)                -- source: 'upload' | 'plm'
part_revisions(id, part_id, revision_label, file_sha256,
               file_path, geometry_fingerprint_json, created_at, author)
analyses(id, revision_id, process_family, material, created_at,
         findings_json, validation_json, summary_text)
```
**Store the STEP blobs** (content-addressed: `blobs/<sha256>.step`). Today the file is deleted after analysis (`main.py:142`), so re-analysis after a rules improvement, geometry diffing, and audit trails are all impossible.

### 6.3 Showing improvement between revisions (the demo payoff)
Current compare (`database.compare_analyses`) diffs rule-level status only — good start, keep it as the headline ("5 fixed, 2 still open, 1 new"). To show *where* the part improved, add **face matching across revisions**: face IDs are enumeration order and not stable across CAD edits, so match faces between rev A and rev B by nearest-neighbor on (surface type, centroid, area, normal) — Hungarian assignment on a composite distance; unmatched faces are added/removed geometry. Then render the money view:

> *Wall thickness, rear housing wall: 0.82 mm → 1.30 mm ✅ resolved (IM-001 min 1.14) · Draft, side wall: 0.0° → 1.5° ✅ resolved · New: Ø2 mm hole 1.1 mm from edge ⚠ new issue*

Side-by-side 3D viewers (rev A | rev B) with fixed faces pulsing green completes the "design engineers reworked it, our tool proves the improvement" story — combined with the PLM import, this is the strongest demo sequence available: *pull rev A from Teamcenter → findings → engineer reworks → rev B appears → delta report shows exactly what improved.*

---

## 7. UI Redesign — Purple / Dark Palette on White (Pantone-referenced) ✅ IMPLEMENTED

*Implemented 2026-08-12. Two adjustments were made against the spec below, both for contrast — see §7.4.*

Direction: white background, deep purple as the brand primary, dark navy for structure/text, Pantone-derived dark red/amber/green strictly reserved for finding status. (Hex values are standard sRGB approximations of the Pantone references.)

### 7.1 Palette
| Role | Pantone ref | Hex | Usage |
|---|---|---|---|
| Primary | Violet C | `#440099` | Buttons, active states, links, focus rings |
| Primary dark | 2685 C | `#330072` | Hover states, header gradient end |
| Primary tint | — | `#F3EEFA` | Selected rows, chip backgrounds, hover fills |
| Structure/ink | 282 C | `#041E42` | Header bar, headings, body text (instead of pure black) |
| Interactive blue | 2935 C | `#0057B8` | Secondary actions, info accents |
| Error / NON-COMPLIANT | 187 C | `#A6192E` | Status chips, error alerts (tint bg `#F9E9EC`) |
| Warning / REVIEW | 124 C | `#EAAA00` | Review chips — always with dark text (tint bg `#FCF4DE`) |
| Success / COMPLIANT | 348 C | `#00843D` | Compliant chips (tint bg `#E6F3EC`) |
| Background | — | `#FFFFFF` | Page background (replace current `#f4f6f8`) |
| Surface alt | — | `#F8F6FB` | Table header rows, side panels — faint purple-tinted gray |
| Border | — | `#E3DEEE` | Card and table borders |

Rules of use: purple = interaction and brand; navy = structure and text; red/amber/green = *finding status only* — never decorate with them, so a red chip always means a failed rule. Check contrast: `#440099`, `#041E42`, `#A6192E`, `#00843D` all pass WCAG AA on white; `#EAAA00` requires dark text on the chip.

### 7.2 Drop-in `theme.js` palette
```js
palette: {
  mode: "light",
  primary:   { main: "#440099", dark: "#330072", light: "#6B33AD",
               contrastText: "#FFFFFF" },
  secondary: { main: "#0057B8", dark: "#003E83", light: "#3379C6" },
  error:     { main: "#A6192E", dark: "#8A1526", light: "#C24759" },
  warning:   { main: "#EAAA00", dark: "#C79100", light: "#EEBB33",
               contrastText: "#041E42" },
  success:   { main: "#00843D", dark: "#00602C", light: "#339D63" },
  background:{ default: "#FFFFFF", paper: "#FFFFFF" },
  text:      { primary: "#041E42", secondary: "#4A5568" },
  divider:   "#E3DEEE",
  status: { compliant: "#00843D", nonCompliant: "#A6192E", review: "#EAAA00" },
},
```

### 7.3 Component-level instructions
- **Header** (`Header.jsx`): gradient `linear-gradient(90deg, #041E42 0%, #330072 60%, #440099 100%)`; white title; Upload button filled white with purple text (inverted) so it pops on the dark bar.
- **Status chips** (`FindingsTable.jsx`): tinted style — background = tint color, text = full status color, 1px border of the status color at ~40% opacity. Reads cleaner than solid chips at table density.
- **Table**: header row on `#F8F6FB` with navy 700-weight text; row hover `#F3EEFA`; NON-COMPLIANT rows get a 3px `#A6192E` left border for scan-ability.
- **Stat cards** (`SummaryDashboard.jsx`): white cards, `#E3DEEE` border, thin top accent strip in the status color; big navy numbers, muted labels. Add a severity-weighted "Compliance score" ring in purple as the first card.
- **Stepper** (`PipelineStepper.jsx`): completed = `#440099`, active = `#0057B8` with pulse, pending = `#B9AEDD`; connector fills purple as steps complete (once wired to real progress per §5.7).
- **Mode toggle / selects**: active toggle filled `#440099`; focused selects use purple focus ring.
- **Typography**: keep Inter; headings navy `#041E42`; drop pure-black text everywhere in favor of navy.
- **Empty state**: replace the emoji magnifier with a line-art icon in `#B9AEDD`, title in navy, chips bordered `#E3DEEE` with navy text.

### 7.4 Implementation notes — where the spec needed correcting

**Status colours needed a separate text variant.** The Pantone values are correct for fills, but measured against their own tints two of them fall just under WCAG AA for label-size text: green `#00843D` on `#E6F3EC` is 4.21:1 and grey `#6B7280` on `#F3F4F6` is 4.39:1, against the 4.5:1 required. Each status token therefore carries both `main` (fills, accent strips, progress bars — keeps the Pantone identity where the colour is a large area) and `text` (chip labels, tab labels). All 16 foreground/background pairs in the interface now measure ≥ 4.5:1.

**Heading colours must not be pinned in the theme.** Setting `color: NAVY` on the `h4/h5/h6` typography variants — as a literal reading of "headings in navy" suggests — renders the header title navy-on-navy and invisible, because it overrides the AppBar's inherited white. Weight is set on the variants; colour comes from `text.primary`, so headings are navy on light surfaces and white on the dark gradient automatically.

**One semantic fix beyond colour.** The compliance-rate bar was specified to reflect the overall verdict, which paints the *passing* share of the bar red whenever anything fails. The filled portion is the compliant checks, so it takes the compliant colour regardless of verdict.

**A functional bug surfaced during the reskin.** `Viewer.jsx` still tested for the pre-P0 status string `"REVIEW"`, so the rules-catalog sidebar silently reported zero review findings for every rule. Now fixed and extended to show not-evaluated counts; IM-011 correctly reads "8 to review".

All colours now resolve through `theme.js` — no component holds a hardcoded hex. Status tokens are exported as `STATUS_TOKENS` / `statusToken()` so a status renders identically everywhere it appears.

---

## 8. Prioritized Action Plan

**P0 — correctness (before any demo): ✅ IMPLEMENTED — see §9 for verification.**
1. ✅ Orientation-aware normals (§2.1) · 2. ✅ Corrected draft formula + occlusion-based undercut detection (§2.2) · 3. ✅ Trimmed-UV sampling with interior-point classification and adaptive refinement (§2.3) · 4. ✅ Material input + rule scoping; split status enum, REVIEW spam eliminated (§2.5) · 5. ✅ Calibration test suite, 37 tests (§2.8) · 6. ✅ Unit handling verified and pinned (§2.6) · 7. ✅ Catalog linter for defective predicates (§2.7).

**P1 — demo credibility:**
7. ✅ Real LLM integration — part-level executive summary + batched per-finding commentary (§4, §5.1-5.2). Implemented against **OpenRouter / DeepSeek V4 Flash** rather than Claude, at the client's direction; see §10. · 8. ✅ UI reskin (§7) · 9. Persist STEP files + hash dedupe + parts/revisions schema (§6.2) · 10. Background job + SSE → real stepper progress (§5.7) · 11. ✅ Surface rule `severity` in the table.

**P2 — the wow layer:**
12. 3D viewer with color-coded findings (§5.4) · 13. Mock Teamcenter connector + "Import from PLM" (§6.1) · 14. Face-matched revision diff view (§6.3) · 15. Feature recognition (§2.6, §4 Tier 1) · 16. "Ask about your part" chat (§5.5).

**P3 — production track:** RAG over guideline documents, geometry-fingerprint near-duplicate detection, sphere-method thickness, coverage dashboard, real Teamcenter adapter.

---

---

## 9. P0 Implementation — What Changed and How It Was Verified

All P0 items are implemented and covered by 37 passing tests (`cd backend && python -m pytest tests/ -q`).

### 9.1 Measured before/after on parts with known dimensions

The old and new extractors were run against the same generated STEP files, whose true dimensions come from their construction:

| Measurement | Ground truth | Old code | New code |
|---|---|---|---|
| Draft on a cone tapering by atan(1/20) | **2.862°** | 87.138° (the complementary angle) | **2.862°** ✅ |
| Wall thickness, 2 mm shelled box | **2.000 mm** on all 8 walls | 2.0 mm on some walls, **40.0 mm** on others | **2.000 mm** on all ✅ |
| Vertical wall draft | **0°** (zero-draft, must fail min-draft rules) | 90° — passed every draft rule | **0°** ✅ |
| Cross-hole undercut | present | not detectable | **detected** ✅ |

The 87.138° result is the signature of the inverted formula: it is exactly 90 − 2.862. The 40.0 mm result is the signature of the orientation bug — the ray fired outward and crossed the entire part.

### 9.2 Findings volume and quality, same part

Analysing an 11-face part under Injection Moulding / ABS:

| | Old engine | New engine |
|---|---|---|
| Total findings | 770 (70 rules × 11 faces, unconditionally) | **94** |
| Indeterminate rows | ~760 `REVIEW` | 24 `NEEDS_REVIEW` + 51 `NOT_EVALUATED`, **each with a stated reason** |
| Material rules applied at once | 7 (contradictory) | **1** (the selected material) |
| Contradictory verdicts | present | **0** (checked by the validation agent) |

### 9.3 New capabilities

- **Undercut detection is now deterministic.** Ray-occlusion against each face's own mould half makes `undercut_presence` (DC-015, MIM-009, PM-004) a computed result rather than a qualitative prompt — these were previously listed in the catalog as not computable.
- **Feature classification.** Cylindrical faces are separated into holes, bosses, internal fillets and external rounds by angular sweep and orientation, so hole rules no longer fire against blend faces. Hole depth and diameter now come from the trimmed parametric bounds (exact) rather than a bounding-box projection (approximate).
- **Coverage reporting.** Every analysis returns how many rules were evaluated, skipped for material, not computable, or not applicable to the geometry, plus the list of unmapped metrics — so the report states its own limits instead of implying full coverage.
- **Part metadata.** Declared units, solid count, bounding box, healing status and validity are reported and surfaced as warnings (open shells, multi-body files, implausible overall size).

### 9.4 Files added or substantially rewritten

`backend/features.py` (rewritten), `backend/step_loader.py` (rewritten), `backend/pipeline.py` (rewritten), `backend/models.py`, `backend/agents/orchestrator.py`, `backend/rule_scoping.py` (new — metric registry, material scoping, catalog linter), `backend/tests/` (new — fixtures, calibration tests, scoping tests), plus material selection through `main.py`, `database.py`, `Header.jsx`, `FindingsTable.jsx`, `SummaryDashboard.jsx`, `App.jsx`.

### 9.5 Known limitations, stated honestly

- **Thickness is a ray measurement, not a rolling-sphere measurement.** On curved or tapered walls it returns a chord ≥ the true perpendicular thickness, and on a face like a shelled box's top rim it measures the vertical extent (20 mm) rather than a wall thickness. Sampling is capped at 25 points per face.
- **Coverage is genuinely partial**: of 70 Injection Moulding rules, ~19 produce verdicts on a simple part. The rest need feature recognition (ribs, bosses, gates), process inputs (surface finish, shutoff faces), or a corrected catalog. This is now visible in the UI rather than hidden behind indeterminate rows.
- **`hole_to_edge_distance` is unavailable on closed solids** by construction, since a solid has no free edges. Reported as unavailable rather than guessed.
- **The LLM layer is still the original mock** — unchanged in P0, and still returns identical text for every finding. This is P1 item 7 and must be done before the tool is presented as AI-assisted.

---

## 10. Interpretive Layer — Implemented on OpenRouter / DeepSeek

Section 4 recommended a tiered agent architecture. Tiers 2 and 3 are now built,
against **OpenRouter** with `~deepseek/deepseek-v4-flash-latest` as the default
model (an alias that always resolves to the newest DeepSeek V4 Flash release).
OpenRouter exposes an OpenAI-compatible API, so the transport is the OpenAI SDK
with the base URL repointed — swapping providers or models is an environment
variable, not a code change.

### 10.1 What replaced the mock

| | Before | After |
|---|---|---|
| Provider | `mock_llm_call`, hardcoded | OpenRouter, env-configured |
| Commentary | One identical sentence, confidence 0.85 on every row | Distinct per finding, grounded in the measured value |
| Requests | One per finding (would have been thousands) | **2 per analysis** — one batched commentary call, one summary call |
| Context sent | Part name + face count | Measured value, severity, category, guideline, plus part-level bbox / nominal thickness / undercut count |
| Output contract | Parse-and-pray on free text | Strict JSON schema (structured outputs) |
| Part-level summary | None | Headline, assessment, ranked risks with recommendations, coverage note |

### 10.2 Design decisions worth keeping

**Batching is not only about cost.** One request carrying the whole set lets the
model see that eight faces failed the same rule and write about the *pattern*.
The summary agent goes further and deduplicates by rule before sending, so a
rule that failed on eight faces occupies one slot rather than crowding a
different failure out of the budget.

**The verdict invariant is now structural.** §4 praised the original
"LLM enriches, never decides" rule, but it was enforced only by a comment and a
discarded dict key. The response schemas have no field capable of expressing a
verdict and set `additionalProperties: false`, so a model that returned
`{"status": "COMPLIANT"}` has nowhere to put it. Covered by a test that sends
exactly that and asserts the finding's status is unchanged.

**Degradation is a first-class path, not an error case.** With no API key the
pipeline produces every geometric verdict and the report states plainly that
the AI summary is unavailable. A timeout, rate limit, or malformed response
costs commentary, never findings. This matters more than it sounds: a DFM
result that took real geometry to compute must not be lost because a text
service was slow.

**Provenance is visible.** Model-written text carries an "AI-generated ·
`<model>`" chip, and `/api/llm_status` reports whether the layer is live. An
engineer acting on a manufacturing recommendation needs to know whether it came
from a measurement or from a language model.

### 10.3 Cost and privacy

At DeepSeek V4 Flash pricing ($0.072/M input, $0.144/M output), a full analysis
costs roughly **$0.0006**. Cost is not a constraint at any plausible demo or
pilot volume.

Only rule names, measured values, and part-level metadata leave the machine.
**The STEP file and its geometry are never uploaded.** If Dyson restricts where
design data may go, that distinction is the conversation to have — and note it
is a property of this design, not of the provider, so it holds equally if the
provider is later swapped.

### 10.4 Still open

The tiering stops at Tier 2/3. **Tier 1 — feature recognition** (§4) is not
built, so commentary still refers to "face 214" rather than "the boss near the
parting line". That remains the biggest available gain in perceived
intelligence, and it is deterministic work, not model work.

---

## 11. Validation Against Real CAD — the NIST PMI Test Set

The calibration suite in §2.8 proves the measurements are numerically correct.
It cannot prove the code survives what real CAD produces, because parts built
from modelling-kernel primitives are far tidier than parts exported by CAD.
Running the **NIST MBE PMI validation set** (33 STEP files, AP203 and AP242,
real machined test artifacts) found five defects the synthetic suite could not
reach — three of them severe.

### 11.1 What broke, and why synthetic parts missed it

| # | Defect | Impact on a real part | Why the synthetic suite missed it |
|---|---|---|---|
| 1 | **Tessellated geometry segfaulted the process** | A faceted STEP file killed the Python process outright — exit 139, no exception. In the API this takes down the worker, not the request. It also silently killed a benchmark run mid-way. | OCC primitives always carry an analytic surface; a faceted import has none, and the OCCT calls that take one abort rather than raise. A Python `try` cannot catch it. |
| 2 | **Every hole classified as a blend fillet** | 57 cylindrical faces on NIST CTC-01, **zero** recognised as holes. Every hole rule silently never ran. | A primitive cylinder is one seamed 2π face. Real CAD splits it at the seam into halves or quadrants, so each piece looks like a partial sweep. |
| 3 | **"0.8 rec / 0.5 min" read as a range** | MC-007 reported a 50 mm machined section as violating a *minimum* wall rule — **60 critical failures on a part with none**. | The synthetic parts never exercised Machining rules. |
| 4 | **"4xD rec / 10xD max" read as a range** | MC-012 failed every hole for being *shallower* than recommended. A shallow hole is easier to drill, not a defect. | Same. |
| 5 | **Angle unit reported as the length unit** | Parts reported as measured in `degree`. The geometry was still correct (OCCT converts), but the metadata was wrong. | The synthetic file declares one unit; real CAD declares several, and `DEGREE` comes first. |

Defects 3 and 4 are the same class as the ratio and advisory problems in §2.7:
the CSV conversion flattened a *directional pair* — two floors, or two ceilings
— into a range with an end at each. Both are now normalised before evaluation
and covered by tests that also assert a genuinely thin wall and a genuinely deep
hole still fail.

### 11.2 Before and after on NIST CTC-01

| | Before | After |
|---|---|---|
| Reported units | `degree` | `millimetre` |
| Holes recognised | 0 | **20** (Ø25, Ø20, Ø35) |
| Rules producing a verdict | 1 | **3** |
| Non-compliant findings | **60 (all false)** | 0 |
| Compliant findings | 0 | 100 |
| Validation issues | 0 | 0 |

Zero failures is the correct answer here: these are NIST *reference* artifacts,
and the summary says so without overclaiming — *"passes all computed wall and
hole checks, but 20 of 27 rules were not evaluated, so manufacturability is not
yet established."*

Unit handling was confirmed on the set's genuinely mixed files: an inch-declared
part reports Ø4.763 mm and Ø7.938 mm holes — exactly 3/16" and 5/16", standard
imperial drill sizes, correctly converted.

### 11.3 Performance

Extraction scaled super-linearly: a 664-face part took 26.8 s. Two changes:

- **Early exit on undercut detection.** An undercut is a boolean, so the first
  blocked sample settles it; the remaining ray casts cannot change the answer.
  26.8 s → 22.1 s, identical results.
- **Gate undercut detection on the catalog.** An undercut asks whether a mould
  half can withdraw, so on a machined or sheet-metal part the answer is
  computed and consumed by nobody. Whether to run it is derived from the rules
  in the selected family, not hardcoded. 22.1 s → **12.4 s**.

End-to-end analysis of a real part now runs in 12–18 s including both model
calls, down from 45–65 s.

### 11.4 Regression coverage

`backend/tests/test_real_world_parts.py` locks in every defect above against
the actual NIST files, and skips cleanly when the folder is absent. Suite total:
**79 tests**.

The general lesson is worth stating plainly for the client: synthetic fixtures
verify that arithmetic is right, and real files verify that the assumptions
behind the arithmetic are right. Both are needed, and the second class of bug
is the one that reaches a demo.

---

*Review conducted against commit state of 2026-08-11; P0 implemented 2026-08-12. Line references to the original code: `features.py`, `pipeline.py`, `main.py`, `agents/orchestrator.py`, `database.py`, `frontend/src/theme.js`, `frontend/src/components/*`.*

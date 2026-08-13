"""pipeline.py - Core orchestrator for the DFX Design-Review POC.

This script executes the main analysis pipeline:
1. Ingest: Loads and normalises the STEP file.
2. Extract: Measures geometric features for each face.
3. Execute: Evaluates applicable DFM rules against those measurements.
4. Interpret: Uses an LLM to add commentary to findings needing judgement.
5. Validate: Performs internal consistency checks.

The Execute step answers three questions per rule -- is the metric computable,
does the rule apply to this material, does it apply to this face -- before it
produces a verdict. A rule that fails any of them yields a single
NOT_EVALUATED finding explaining why, instead of one indeterminate finding per
face. That distinction is the difference between a report with a few hundred
meaningful rows and one with tens of thousands of rows that say nothing.
"""

import logging
import json
import operator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from typing import Optional

import step_loader
import feature_naming
import features
import process_classifier
import mesh
import rule_scoping
import llm as llm_provider
from models import Finding, PartModel, PartModelFace, RuleCoverage
import agents.orchestrator
from OCC.Core.gp import gp_Dir


# The value `process_family` takes when the caller wants the process read off
# the geometry rather than chosen by hand.
AUTO_FAMILY = "auto"


def _merge_coverage(by_family: dict[str, dict]) -> dict:
    """Totals across families, with the per-family breakdown kept alongside.

    The totals answer "how much of the catalogue did this run test"; the
    breakdown answers "and was the family that matters among it". Only the
    second question is worth much, so the breakdown is not optional detail.
    """
    totals: dict[str, int] = {}
    unmapped: list[str] = []
    for coverage in by_family.values():
        for key, value in coverage.items():
            if key == "unmapped_metrics":
                unmapped.extend(value)
            elif isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    totals["unmapped_metrics"] = sorted(set(unmapped))
    totals["by_family"] = by_family
    return totals


_COMPARISONS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
}


def _safe_predicate_eval(
    predicate: dict, value: float, value_max: Optional[float] = None
) -> bool:
    """Evaluates a catalog predicate against a measurement.

    `value` is the worst-case (smallest) measurement and `value_max` the largest
    where a face was sampled at several points. A range predicate tests the
    whole interval: a wall that runs from 1.2 mm to 5.0 mm violates a
    1.14-3.56 mm rule even though its thinnest point is inside the range.
    """
    predicate_type = predicate.get("type")

    if predicate_type == "simple" or (
        predicate_type is None and "operator" in predicate and "threshold" in predicate
    ):
        operator_symbol = predicate["operator"]
        comparison = _COMPARISONS.get(operator_symbol)
        if comparison is None:
            raise ValueError(f"Unsupported operator in predicate: {operator_symbol}")
        return comparison(value, predicate["threshold"])

    if predicate_type == "range":
        minimum, maximum = predicate.get("min"), predicate.get("max")
        upper = value_max if value_max is not None else value
        if minimum is not None and value < minimum:
            return False
        if maximum is not None and upper > maximum:
            return False
        return True

    raise ValueError(f"Unknown or malformed predicate structure: {predicate}")


def _format_measurement(
    value: float, value_max: Optional[float], spec: rule_scoping.MetricSpec
) -> str:
    """Renders a measurement for the report, including its unit."""
    if spec.is_boolean:
        return "yes" if value else "no"

    suffix = {"mm": " mm", "deg": "°", "ratio": "×", "": ""}.get(
        spec.unit, ""
    )
    if (
        value_max is not None
        and spec.interval_max_attribute
        and abs(value_max - value) > 1e-6
    ):
        return f"{value:.3f}–{value_max:.3f}{suffix}"
    return f"{value:.3f}{suffix}"


def nominal_wall_thickness(part_model: PartModel) -> Optional[float]:
    """Estimates the part's characteristic wall thickness.

    Rules written as a multiple of material thickness ("1xT") need a single
    part-level reference. The per-face ray measurement is the wrong thing to
    use for a hole: a ray from a hole's cylindrical surface travels sideways
    through the part, not across the sheet. The median across planar faces is
    a stable stand-in for nominal stock thickness.
    """
    thicknesses = [
        face.wall_thickness
        for face in part_model.faces
        if face.wall_thickness and face.surface_type == "plane"
    ]
    if not thicknesses:
        thicknesses = [
            face.wall_thickness for face in part_model.faces if face.wall_thickness
        ]
    return median(thicknesses) if thicknesses else None


def _resolve_reference(reference: Optional[str], nominal_thickness: Optional[float]):
    """Returns (divisor, reason_if_unavailable) for a ratio-style rule."""
    if reference is None:
        return 1.0, None
    if reference == "nominal_thickness":
        if nominal_thickness and nominal_thickness > 1e-6:
            return nominal_thickness, None
        return None, "nominal wall thickness could not be established for this part"
    return None, f"unknown reference '{reference}'"


def _part_level_finding(rule: dict, status: str, reason: str) -> Finding:
    """One finding that explains why a whole rule produced no per-face verdict."""
    return Finding(
        process_family=rule.get("process_family"),
        rule_id=rule["rule_id"],
        rule_name=rule["rule_name"],
        guideline_ref=rule["guideline_ref"],
        status=status,
        location="part",
        measured=None,
        severity=rule.get("severity"),
        category=rule.get("category"),
        reason=reason,
    )


def execute_rules(
    part_model: PartModel,
    rules: list[dict],
    process_family: str,
    material: Optional[str] = None,
    blocked_rules: Optional[dict[str, str]] = None,
) -> tuple[list[Finding], RuleCoverage]:
    """Evaluates applicable rules against the part model.

    Args:
        part_model: The measured faces from the Extract step.
        rules: Rule definitions already filtered to one process family.
        process_family: The selected process family, for material scoping.
        material: The selected material key, or None if unspecified.
        blocked_rules: Rule ids whose predicates the linter found unreliable,
            mapped to the reason.

    Returns:
        The findings, and a coverage summary stating how much of the catalog
        could actually be tested.
    """
    blocked_rules = blocked_rules or {}
    findings: list[Finding] = []
    coverage = RuleCoverage(rules_after_material_filter=0)
    unmapped_metrics: set[str] = set()

    nominal_thickness = nominal_wall_thickness(part_model)

    for rule in rules:
        rule_id = rule["rule_id"]

        # --- Does this rule apply to the chosen material? ---
        material_applies, material_reason = rule_scoping.rule_material_applies(
            rule_id, process_family, material
        )
        if not material_applies:
            findings.append(
                _part_level_finding(rule, "NOT_EVALUATED", material_reason)
            )
            continue
        coverage.rules_after_material_filter += 1

        # --- Is the predicate trustworthy? ---
        if rule_id in blocked_rules:
            findings.append(
                _part_level_finding(rule, "NOT_EVALUATED", blocked_rules[rule_id])
            )
            coverage.rules_not_computable += 1
            continue

        # --- Can we measure it? ---
        spec = rule_scoping.resolve_metric(rule.get("metric"))

        if spec is None:
            metric_name = rule.get("metric")
            if rule.get("kind") == "qualitative" or not metric_name:
                # Genuinely a judgement call: one finding for the rule, to be
                # enriched by the interpretive agent.
                findings.append(
                    _part_level_finding(
                        rule,
                        "NEEDS_REVIEW",
                        "qualitative rule; requires engineering judgement",
                    )
                )
                coverage.rules_needing_review += 1
            else:
                unmapped_metrics.add(metric_name)
                findings.append(
                    _part_level_finding(
                        rule,
                        "NOT_EVALUATED",
                        f"no geometric extractor implemented for metric "
                        f"'{metric_name}'",
                    )
                )
                coverage.rules_not_computable += 1
            continue

        predicate = rule_scoping.normalize_predicate(rule.get("predicate"))
        if not predicate and not spec.is_boolean:
            findings.append(
                _part_level_finding(
                    rule,
                    "NOT_EVALUATED",
                    "rule declares a measurable metric but carries no threshold",
                )
            )
            coverage.rules_not_computable += 1
            continue

        divisor, reference_problem = _resolve_reference(spec.reference, nominal_thickness)
        if reference_problem:
            findings.append(
                _part_level_finding(rule, "NOT_EVALUATED", reference_problem)
            )
            coverage.rules_not_computable += 1
            continue

        # --- Evaluate against every face the rule is meaningful for ---
        rule_findings = _evaluate_rule_on_faces(
            rule, spec, predicate, part_model.faces, divisor
        )

        if not rule_findings:
            findings.append(
                _part_level_finding(
                    rule,
                    "NOT_EVALUATED",
                    "no face in this part carries the feature this rule measures",
                )
            )
            coverage.rules_not_applicable_to_geometry += 1
            continue

        findings.extend(rule_findings)
        coverage.rules_evaluated += 1

    coverage.rules_in_family = len(rules)
    coverage.unmapped_metrics = sorted(unmapped_metrics)
    return findings, coverage


def _evaluate_rule_on_faces(
    rule: dict,
    spec: rule_scoping.MetricSpec,
    predicate: Optional[dict],
    faces: list[PartModelFace],
    divisor: float,
) -> list[Finding]:
    """Produces a finding for each face the rule is meaningful for.

    Faces the rule does not apply to produce no finding at all. A hole rule has
    nothing to say about a flat wall, and saying so thousands of times obscures
    the findings that matter.
    """
    rule_findings: list[Finding] = []

    for face in faces:
        applies, _ = rule_scoping.metric_applies_to_face(spec, face)
        if not applies:
            continue

        raw_value = getattr(face, spec.attribute, None)
        if raw_value is None:
            continue

        value_max = None
        if spec.interval_max_attribute:
            value_max = getattr(face, spec.interval_max_attribute, None)

        status = "NEEDS_REVIEW"
        reason = None
        measured = None

        try:
            if spec.is_boolean:
                # Presence flags: the catalog wording for all of these is
                # "flag" or "avoid", so a detected feature is the violation.
                measured = _format_measurement(bool(raw_value), None, spec)
                status = "NON-COMPLIANT" if raw_value else "COMPLIANT"
            else:
                value = float(raw_value) / divisor
                scaled_max = (
                    float(value_max) / divisor if value_max is not None else None
                )
                compliant = _safe_predicate_eval(predicate, value, scaled_max)
                measured = _format_measurement(value, scaled_max, spec)
                if compliant:
                    status = "COMPLIANT"
                elif spec.is_advisory:
                    # Guidance, not a limit: departing from typical practice is
                    # worth a look but is not a non-compliance.
                    status = "NEEDS_REVIEW"
                    reason = (
                        "outside typical practice for this process; advisory "
                        "guidance rather than a limit"
                    )
                else:
                    status = "NON-COMPLIANT"
        except Exception as exc:
            logging.error(
                f"Error evaluating rule {rule['rule_id']} on face "
                f"{face.face_id}: {exc}"
            )
            status = "ERROR"
            reason = f"predicate evaluation failed: {exc}"

        rule_findings.append(
            Finding(
                process_family=rule.get("process_family"),
                rule_id=rule["rule_id"],
                rule_name=rule["rule_name"],
                guideline_ref=rule["guideline_ref"],
                status=status,
                location=f"face {face.face_id}",
                feature_label=face.label,
                measured=measured,
                severity=rule.get("severity"),
                category=rule.get("category"),
                reason=reason,
            )
        )

    return rule_findings


def _build_part_context(
    part_path: Path,
    part_model: PartModel,
    loaded: "step_loader.LoadedPart",
    process_family: str,
    material: Optional[str],
) -> dict:
    """Assembles the measured context handed to the interpretive agents.

    Commentary quality is bounded by what the model can see. Passing only the
    part name and a face count -- as the original mock prompt did -- makes
    generic commentary the best possible outcome, so the real measurements go
    in: overall size, nominal thickness, the thinnest wall, undercut count.
    """
    thicknesses = [f.wall_thickness for f in part_model.faces if f.wall_thickness]
    undercuts = part_model.undercut_face_ids

    context = {
        "part_name": part_path.name,
        "process_family": process_family,
        "material": material,
        "face_count": len(part_model.faces),
        "nominal_wall_thickness_mm": nominal_wall_thickness(part_model),
        "bounding_box_mm": loaded.bounding_box_mm,
        "source_units": loaded.source_units,
        "is_valid_solid": loaded.is_valid_solid,
        "undercut_face_count": len(undercuts),
        # What the part is made of, feature-wise. "12 holes, 8 internal fillets,
        # 6 side walls" is how an engineer would open a description of it, and
        # it lets the summary describe the part rather than just its statistics.
        "feature_inventory": feature_naming.inventory(part_model.faces),
    }
    if thicknesses:
        context["thinnest_wall_mm"] = round(min(thicknesses), 3)
        context["thickest_wall_mm"] = round(max(thicknesses), 3)
    if loaded.warnings:
        context["ingest_warnings"] = loaded.warnings
    return context


def run_analysis_pipeline(
    part_path: Path,
    rules_path: Path,
    pull_direction: gp_Dir,
    process_family: str,
    material: Optional[str] = None,
    llm_client=None,
) -> dict:
    """Runs the analysis pipeline and returns the results.

    Args:
        part_path: Path to the input STEP file.
        rules_path: Path to the JSON rules catalog.
        pull_direction: The mould's pull direction, for draft and undercuts.
        process_family: The manufacturing process to filter rules by, or "auto"
            to read the process off the geometry and analyse every family the
            part could plausibly belong to.
        material: The selected material key, used to scope material-specific rules.
        llm_client: An `llm.LLMClient` for the interpretive step. Defaults to the
            process-wide client; injected in tests so the suite never makes a
            network call.

    Returns:
        A dict with findings, validation issues, applied rules, coverage, part
        metadata, the executive summary and catalog issues.
    """
    automatic = process_family == AUTO_FAMILY
    # --- Step 1: Ingest ---
    logging.info(f"Step 1: Ingesting {part_path}...")
    loaded = step_loader.load_step(part_path)

    # Rules are loaded before extraction because what the catalog asks about
    # decides how much measuring is worth doing -- see detect_undercuts below.
    with rules_path.open("r", encoding="utf-8") as fh:
        all_rules = json.load(fh)

    catalog_issues = rule_scoping.lint_catalog(all_rules)
    blocked_rules = rule_scoping.unreliable_rule_ids(all_rules)
    if catalog_issues:
        logging.warning(
            f"Rules catalog lint found {len(catalog_issues)} issue(s); "
            f"{len(blocked_rules)} rule(s) will not produce verdicts."
        )

    catalog_families = sorted(
        {r["process_family"] for r in all_rules if r.get("process_family")}
    )

    if automatic:
        # Which families to check is not known yet -- it depends on measurements
        # that have not been taken. Undercut detection is therefore switched on
        # for the classifying pass: it is itself one of the strongest signals
        # (rigid punches cannot withdraw from an undercut) and skipping it would
        # mean measuring the part twice.
        selected_families = catalog_families
        detect_undercuts = True
    else:
        selected_families = [process_family]
        detect_undercuts = rule_scoping.family_needs_undercuts(
            [r for r in all_rules if r.get("process_family") == process_family]
        )
        if not detect_undercuts:
            logging.info(
                f"No rule in '{process_family}' asks about undercuts; skipping "
                "occlusion testing."
            )

    # --- Step 2: Extract ---
    logging.info("Step 2: Extracting features...")
    part_model = features.build_part_model(
        loaded.shape, pull_direction, detect_undercuts=detect_undercuts
    )
    logging.info(f"Extraction complete. Part model has {len(part_model.faces)} faces.")

    # --- Step 2b: Classify ---
    # Geometry is measured once; deciding which catalogues apply is a reading of
    # those measurements, not another pass over the part.
    classification = None
    if automatic:
        logging.info("Step 2b: Reading the process from the geometry...")
        classification = process_classifier.classify(
            part_model, loaded, catalog_families
        )
        selected_families = classification.families_to_analyse
        logging.info(
            "Classified as: "
            + ", ".join(
                f"{c.process_family} ({c.score:.2f})"
                for c in classification.candidates
                if c.basis == "detected"
            )
        )

    applicable_rules = [
        rule for rule in all_rules if rule.get("process_family") in selected_families
    ]
    logging.info(
        f"Found {len(applicable_rules)} rules across {len(selected_families)} "
        f"family/families out of {len(all_rules)} total."
    )

    # --- Step 3: Execute ---
    logging.info("Step 3: Executing rules...")

    # Rules are executed one family at a time so coverage stays per-family: a
    # composite report that merged them would report "19 of 250 rules evaluated"
    # and tell the reader nothing about whether the family that matters was
    # covered.
    findings: list[Finding] = []
    coverage_by_family: dict[str, dict] = {}
    for family in selected_families:
        family_rules = [
            r for r in all_rules if r.get("process_family") == family
        ]
        if not family_rules:
            continue
        family_findings, family_coverage = execute_rules(
            part_model, family_rules, family, material, blocked_rules
        )
        findings.extend(family_findings)
        coverage_by_family[family] = family_coverage.model_dump()

    coverage = _merge_coverage(coverage_by_family)
    logging.info(f"Execution complete. Generated {len(findings)} findings.")

    # --- Step 4: Interpret ---
    logging.info("Step 4: Interpreting findings that need review...")
    part_context = _build_part_context(
        part_path,
        part_model,
        loaded,
        ", ".join(selected_families) if automatic else process_family,
        material,
    )
    if classification:
        part_context["process_read_from_geometry"] = [
            {
                "process_family": c.process_family,
                "confidence": c.confidence,
                "why": c.evidence_for[:3],
            }
            for c in classification.candidates
            if c.basis == "detected" and c.score >= process_classifier.ANALYSIS_THRESHOLD
        ]

    client = llm_client if llm_client is not None else llm_provider.get_client()
    if not client.is_available:
        logging.info(
            "No language model configured; the report will carry deterministic "
            "results only."
        )

    # The two agents are independent -- the summary reads verdicts and
    # measurements, never the commentary -- so they run concurrently rather
    # than one after the other. Both are network-bound, and serialising them
    # doubles the wait the user sits through for no benefit.
    #
    # Tessellation rides along in the same pool. It is CPU-bound where the two
    # agents are network-bound, so it costs no extra wall-clock at all: the mesh
    # is built while the requests are in flight. Nothing else reads the shape by
    # this point, so there is no contention on it.
    with ThreadPoolExecutor(max_workers=4) as pool:
        commentary_task = pool.submit(
            agents.orchestrator.enrich_review_findings,
            findings,
            part_context,
            client,
        )
        summary_task = pool.submit(
            agents.orchestrator.generate_executive_summary,
            findings,
            part_context,
            coverage,
            client,
        )
        # Only when the process was read rather than chosen: explaining a
        # choice the user made themselves would be telling them what they
        # already know, at the cost of a request.
        reading_task = (
            pool.submit(
                agents.orchestrator.explain_classification,
                classification.as_dict(),
                client,
            )
            if classification
            else None
        )
        mesh_task = pool.submit(
            mesh.build,
            loaded.shape,
            {f.face_id: f.label for f in part_model.faces if f.label},
        )
        enriched_findings = commentary_task.result()
        summary = summary_task.result()
        part_mesh = mesh_task.result()
        process_reading = reading_task.result() if reading_task else None
    logging.info("Interpretation complete.")

    # --- Step 5: Validate ---
    logging.info("Step 5: Validating findings...")
    validation_issues = agents.orchestrator.validate(
        enriched_findings, part_model.faces
    )
    validation_issues.extend(
        {"finding": "ingest", "issue": warning} for warning in loaded.warnings
    )

    return {
        "findings": enriched_findings,
        "validation_issues": validation_issues,
        "rules_applied": applicable_rules,
        "coverage": coverage,
        "part_metadata": loaded.as_metadata(),
        "process_families": selected_families,
        "classification": (
            {**classification.as_dict(), "reading": process_reading}
            if classification
            else None
        ),
        "part_model": part_model,
        "mesh": part_mesh,
        "summary": summary,
        "llm": client.usage.as_metadata(),
        "catalog_issues": [
            {"rule_id": i.rule_id, "severity": i.severity, "message": i.message}
            for i in catalog_issues
        ],
    }

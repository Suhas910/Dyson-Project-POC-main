"""Tests for rule applicability: material scoping, face scoping, catalog linting.

These cover the failure mode where the geometry is measured correctly but the
wrong rule is applied to it -- which produces a confident, wrong verdict rather
than an obviously broken number.
"""

import json
from pathlib import Path

import pytest

import pipeline
import rule_scoping
from models import PartModel, PartModelFace

RULES_PATH = Path(__file__).resolve().parent.parent / "rules_catalog.json"


@pytest.fixture(scope="module")
def catalog() -> list[dict]:
    with RULES_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def injection_rules(catalog) -> list[dict]:
    return [r for r in catalog if r.get("process_family") == "Injection Moulding"]


# --- Material scoping ------------------------------------------------------


def test_material_specific_wall_rules_are_mutually_exclusive(injection_rules):
    """Only one material's wall-thickness rule may be evaluated at a time.

    ABS, PC and PP have overlapping but different limits. Evaluating them all
    against the same wall lets one report call that wall both compliant and
    non-compliant.
    """
    wall_rules = [r for r in injection_rules if r["rule_id"] in rule_scoping.RULE_MATERIAL_SCOPE]
    assert len(wall_rules) >= 5

    applied = [
        r["rule_id"]
        for r in wall_rules
        if rule_scoping.rule_material_applies(r["rule_id"], "Injection Moulding", "ABS")[0]
    ]
    assert applied == ["IM-001"]


def test_unselected_material_blocks_material_specific_rules():
    applies, reason = rule_scoping.rule_material_applies(
        "IM-001", "Injection Moulding", None
    )
    assert applies is False
    assert "no material was selected" in reason


def test_material_class_rules_follow_the_class_not_the_grade():
    """Machining minimum-wall rules split by metal vs plastic, not by grade."""
    metal_applies, _ = rule_scoping.rule_material_applies("MC-007", "Machining", "ALUMINIUM")
    plastic_applies, _ = rule_scoping.rule_material_applies("MC-007", "Machining", "ABS")
    assert metal_applies is True
    assert plastic_applies is False


def test_unscoped_rules_apply_to_every_material():
    applies, reason = rule_scoping.rule_material_applies(
        "IM-010", "Injection Moulding", "PP"
    )
    assert applies is True
    assert reason is None


# --- Face scoping ----------------------------------------------------------


def _face(**kwargs) -> PartModelFace:
    defaults = {"face_id": 1, "sample_count": 5, "surface_type": "plane",
                "feature_class": "plane"}
    defaults.update(kwargs)
    return PartModelFace(**defaults)


def test_hole_metrics_do_not_apply_to_flat_walls():
    spec = rule_scoping.resolve_metric("hole_diameter_min")
    applies, reason = rule_scoping.metric_applies_to_face(spec, _face())
    assert applies is False
    assert "does not apply" in reason


def test_draft_does_not_apply_to_faces_perpendicular_to_pull():
    """A flat top is exempt from draft rules rather than failing them."""
    spec = rule_scoping.resolve_metric("draft_angle_min")
    face = _face(draft_angle=90.0, is_perpendicular_to_pull=True)
    applies, reason = rule_scoping.metric_applies_to_face(spec, face)
    assert applies is False
    assert "perpendicular" in reason


def test_unsampled_faces_are_not_evaluated():
    spec = rule_scoping.resolve_metric("wall_thickness_min")
    applies, _ = rule_scoping.metric_applies_to_face(spec, _face(sample_count=0))
    assert applies is False


def test_unknown_metrics_resolve_to_nothing_rather_than_guessing():
    """The old substring matcher mapped 'thread_depth' onto hole depth."""
    assert rule_scoping.resolve_metric("thread_depth") is None
    assert rule_scoping.resolve_metric("sintering_shrinkage_linear") is None
    assert rule_scoping.resolve_metric(None) is None


# --- Catalog linting -------------------------------------------------------


def test_linter_catches_spreadsheet_date_corruption(catalog):
    """'Mar-40' is a spreadsheet's reading of '3-40', and parsed to -40.0."""
    issues = {i.rule_id: i for i in rule_scoping.lint_catalog(catalog) if i.severity == "error"}
    assert "MC-018" in issues


def test_linter_catches_negative_thresholds(catalog):
    errors = [
        i for i in rule_scoping.lint_catalog(catalog)
        if i.severity == "error" and "negative" in i.message
    ]
    assert {i.rule_id for i in errors} >= {"MIM-012", "MC-018"}


def test_linter_catches_inverted_ranges():
    """A range whose minimum exceeds its maximum can never be satisfied."""
    rule = {
        "rule_id": "TEST-001",
        "kind": "quantitative",
        "metric": "wall_thickness_min",
        "predicate": {"type": "range", "min": 5.0, "max": 2.0},
    }
    issues = rule_scoping.lint_rule(rule)
    assert any("exceeds maximum" in i.message for i in issues)


def test_advisory_rules_never_produce_a_non_compliance():
    """Typical-practice ranges guide; they do not fail a design.

    IM-011 recommends 1-2 degrees of draft. A wall with 3 degrees is better
    than the recommendation, and reporting it as a violation would send an
    engineer to 'fix' a feature that is already right.
    """
    spec = rule_scoping.resolve_metric("draft_angle_typical")
    assert spec is not None and spec.is_advisory is True


def test_rules_needing_unidentifiable_face_types_are_not_measured():
    """Textured and shutoff draft rules apply to faces geometry cannot identify.

    Surface finish and metal-to-metal shutoffs are tooling decisions. Applying
    their higher draft requirements to every wall would fail correct designs.
    """
    for metric in (
        "draft_angle_textured_min",
        "draft_angle_shutoff_min",
        "draft_angle_deep_feature",
    ):
        assert rule_scoping.resolve_metric(metric) is None


def test_unreliable_rules_are_blocked_from_producing_verdicts(catalog):
    blocked = rule_scoping.unreliable_rule_ids(catalog)
    # Corrupted predicate, and a rule whose threshold is a multiple of material
    # thickness rather than an absolute distance.
    assert "MC-018" in blocked
    assert "SM-009" in blocked


# --- End-to-end rule execution ---------------------------------------------


def _two_wall_part() -> PartModel:
    """A part with two walls: one at 1.0 mm, one at 2.5 mm."""
    return PartModel(
        faces=[
            _face(face_id=1, wall_thickness=1.0, wall_thickness_max=1.0, draft_angle=0.0),
            _face(face_id=2, wall_thickness=2.5, wall_thickness_max=2.5, draft_angle=3.0),
        ]
    )


def test_execution_produces_no_contradictory_verdicts(injection_rules, catalog):
    """The headline regression: one wall, one verdict per category."""
    import agents.orchestrator as orchestrator

    part = _two_wall_part()
    findings, _ = pipeline.execute_rules(
        part,
        injection_rules,
        "Injection Moulding",
        material="ABS",
        blocked_rules=rule_scoping.unreliable_rule_ids(catalog),
    )
    contradictions = [
        issue
        for issue in orchestrator.validate(findings, part.faces)
        if "disagree" in issue["issue"]
    ]
    assert contradictions == []


def test_inapplicable_rules_report_once_not_once_per_face(injection_rules, catalog):
    """A rule that cannot run must not emit a finding for every face.

    Previously each rule was evaluated against every face and anything
    unmeasurable became a REVIEW row, so a 500-face part produced tens of
    thousands of findings that said nothing.
    """
    part = PartModel(faces=[_face(face_id=i, wall_thickness=2.0) for i in range(1, 51)])
    findings, coverage = pipeline.execute_rules(
        part,
        injection_rules,
        "Injection Moulding",
        material="ABS",
        blocked_rules=rule_scoping.unreliable_rule_ids(catalog),
    )

    not_evaluated = [f for f in findings if f.status == "NOT_EVALUATED"]
    locations = {f.location for f in not_evaluated}
    assert locations == {"part"}

    # With 50 faces and 70 injection-moulding rules, the old engine produced
    # 3,500 findings. The bound here is far below that.
    assert len(findings) < 500
    assert coverage.rules_evaluated > 0


def test_every_unevaluated_finding_explains_itself(injection_rules, catalog):
    """Coverage gaps must be explainable, not silent."""
    part = _two_wall_part()
    findings, _ = pipeline.execute_rules(
        part,
        injection_rules,
        "Injection Moulding",
        material="ABS",
        blocked_rules=rule_scoping.unreliable_rule_ids(catalog),
    )
    for finding in findings:
        if finding.status == "NOT_EVALUATED":
            assert finding.reason, f"{finding.rule_id} gives no reason"


def test_thin_wall_is_flagged_for_abs(injection_rules, catalog):
    """A 1.0 mm wall is below the ABS minimum of 1.14 mm and must fail."""
    part = _two_wall_part()
    findings, _ = pipeline.execute_rules(
        part, injection_rules, "Injection Moulding", material="ABS",
        blocked_rules=rule_scoping.unreliable_rule_ids(catalog),
    )
    abs_findings = {f.location: f for f in findings if f.rule_id == "IM-001"}
    assert abs_findings["face 1"].status == "NON-COMPLIANT"
    assert abs_findings["face 2"].status == "COMPLIANT"


def test_same_wall_passes_under_a_more_permissive_material(injection_rules, catalog):
    """The 1.0 mm wall is legal in PP (min 0.89 mm) but not in ABS.

    This is the behaviour that makes material selection meaningful rather than
    decorative.
    """
    part = _two_wall_part()
    findings, _ = pipeline.execute_rules(
        part, injection_rules, "Injection Moulding", material="PP",
        blocked_rules=rule_scoping.unreliable_rule_ids(catalog),
    )
    pp_findings = {f.location: f for f in findings if f.rule_id == "IM-003"}
    assert pp_findings["face 1"].status == "COMPLIANT"
    # And the ABS rule must not have been evaluated at all.
    assert all(
        f.status == "NOT_EVALUATED" for f in findings if f.rule_id == "IM-001"
    )


def test_zero_draft_wall_fails_minimum_draft_rule(injection_rules, catalog):
    """IM-010 requires at least 0.5 degrees on vertical faces."""
    part = _two_wall_part()
    findings, _ = pipeline.execute_rules(
        part, injection_rules, "Injection Moulding", material="ABS",
        blocked_rules=rule_scoping.unreliable_rule_ids(catalog),
    )
    draft_findings = {f.location: f for f in findings if f.rule_id == "IM-010"}
    assert draft_findings["face 1"].status == "NON-COMPLIANT"  # 0.0 deg
    assert draft_findings["face 2"].status == "COMPLIANT"  # 3.0 deg


# --- "recommended / minimum" pairs are floors, not bands -------------------


def test_recommended_min_pair_is_normalized_to_a_floor(catalog):
    """A 50 mm machined wall must not violate a *minimum* wall rule.

    MC-007 reads "0.8 rec / 0.5 min" — two floors. Stored as a range it made
    every generous section a critical failure; on a real NIST test part that
    fired 60 times on one part.
    """
    mc007 = next(r for r in catalog if r["rule_id"] == "MC-007")
    assert rule_scoping.is_floor_pair_predicate(mc007["predicate"])

    normalized = rule_scoping.normalize_predicate(mc007["predicate"])
    assert normalized["type"] == "simple"
    assert normalized["operator"] == ">="
    assert normalized["threshold"] == 0.5
    assert normalized["advisory_threshold"] == 0.8


def test_thick_wall_passes_a_minimum_wall_rule(catalog):
    machining = [r for r in catalog if r.get("process_family") == "Machining"]
    part = PartModel(
        faces=[_face(face_id=1, wall_thickness=50.0, wall_thickness_max=50.0)]
    )
    findings, _ = pipeline.execute_rules(
        part, machining, "Machining", material="ALUMINIUM",
        blocked_rules=rule_scoping.unreliable_rule_ids(catalog),
    )
    mc007 = [f for f in findings if f.rule_id == "MC-007"]
    assert mc007 and all(f.status == "COMPLIANT" for f in mc007)


def test_a_genuinely_thin_wall_still_fails(catalog):
    """The floor must still be enforced — normalisation is not a mute button."""
    machining = [r for r in catalog if r.get("process_family") == "Machining"]
    part = PartModel(
        faces=[_face(face_id=1, wall_thickness=0.2, wall_thickness_max=0.2)]
    )
    findings, _ = pipeline.execute_rules(
        part, machining, "Machining", material="ALUMINIUM",
        blocked_rules=rule_scoping.unreliable_rule_ids(catalog),
    )
    mc007 = [f for f in findings if f.rule_id == "MC-007"]
    assert mc007 and all(f.status == "NON-COMPLIANT" for f in mc007)


def test_a_real_range_is_left_alone(catalog):
    """IM-001 (ABS 1.14–3.56 mm) is a genuine band and must not be rewritten."""
    im001 = next(r for r in catalog if r["rule_id"] == "IM-001")
    assert not rule_scoping.is_floor_pair_predicate(im001["predicate"])
    assert rule_scoping.normalize_predicate(im001["predicate"]) is im001["predicate"]


def test_recommended_max_pair_is_normalized_to_a_ceiling(catalog):
    """MC-012 reads "4xD rec / 10xD max" — both are upper bounds.

    Read as a range, a hole shallower than recommended failed. A shallow hole
    is easier to drill, not a defect.
    """
    mc012 = next(r for r in catalog if r["rule_id"] == "MC-012")
    assert rule_scoping.directional_pair_kind(mc012["predicate"]) == "ceiling"

    normalized = rule_scoping.normalize_predicate(mc012["predicate"])
    assert normalized["operator"] == "<="
    assert normalized["threshold"] == 10.0


def test_a_shallow_hole_passes_a_depth_ratio_rule(catalog):
    machining = [r for r in catalog if r.get("process_family") == "Machining"]
    part = PartModel(faces=[_face(
        face_id=1, feature_class="hole", surface_type="cylinder",
        hole_diameter=25.0, hole_depth=50.0, hole_depth_to_diameter_ratio=2.0,
    )])
    findings, _ = pipeline.execute_rules(
        part, machining, "Machining", material="ALUMINIUM",
        blocked_rules=rule_scoping.unreliable_rule_ids(catalog),
    )
    mc012 = [f for f in findings if f.rule_id == "MC-012"]
    assert mc012 and all(f.status == "COMPLIANT" for f in mc012)


def test_a_genuinely_deep_hole_still_fails(catalog):
    machining = [r for r in catalog if r.get("process_family") == "Machining"]
    part = PartModel(faces=[_face(
        face_id=1, feature_class="hole", surface_type="cylinder",
        hole_diameter=5.0, hole_depth=90.0, hole_depth_to_diameter_ratio=18.0,
    )])
    findings, _ = pipeline.execute_rules(
        part, machining, "Machining", material="ALUMINIUM",
        blocked_rules=rule_scoping.unreliable_rule_ids(catalog),
    )
    mc012 = [f for f in findings if f.rule_id == "MC-012"]
    assert mc012 and all(f.status == "NON-COMPLIANT" for f in mc012)


def test_undercut_detection_is_gated_on_the_catalog(catalog):
    """Occlusion testing costs a ray cast per face and only moulding uses it."""
    moulding = [r for r in catalog if r.get("process_family") == "Die Casting"]
    machining = [r for r in catalog if r.get("process_family") == "Machining"]
    assert rule_scoping.family_needs_undercuts(moulding) is True
    assert rule_scoping.family_needs_undercuts(machining) is False

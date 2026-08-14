import pytest

from models import PartModelFace, Finding
from pipeline import _evaluate_rule_on_faces
import rule_scoping


def test_range_rule_violation_on_upper_bound_selects_max_point():
    """
    Verifies that when a range rule is violated because the maximum value is
    out of bounds, the finding correctly attaches the 'max_point' as the
    measurement_point.
    """
    # This face is compliant on its thinnest wall (2.0mm > 1.0mm min) but
    # non-compliant on its thickest wall (5.0mm > 4.0mm max).
    mock_face = PartModelFace(
        face_id=123,
        label="test_face_for_range_rule",
        wall_thickness=2.0,
        wall_thickness_max=5.0,
        wall_thickness_point=(1.0, 1.0, 1.0),  # Point for the min value
        wall_thickness_max_point=(2.0, 2.0, 2.0),  # Point for the max value
        feature_class="plane",
        sample_count=10,
    )

    # A rule that checks for wall thickness between 1.0 and 4.0 mm.
    range_rule = {
        "rule_id": "IM-TEST-RANGE",
        "rule_name": "Test Wall Thickness Range",
        "metric": "wall_thickness_range",
        "process_family": "Injection Moulding",
        "guideline_ref": "test",
        "severity": "critical",
        "category": "Wall Thickness",
        "predicate": {"type": "range", "min": 1.0, "max": 4.0},
    }

    spec = rule_scoping.METRIC_REGISTRY.get("wall_thickness_range")
    assert spec is not None

    # Execute the rule on the mock face
    findings = _evaluate_rule_on_faces(
        rule=range_rule,
        spec=spec,
        predicate=range_rule["predicate"],
        faces=[mock_face],
        divisor=1.0,
    )

    # --- Assertions ---
    assert len(findings) == 1, "Expected exactly one finding"
    finding = findings[0]

    assert finding.status == "NON-COMPLIANT", "Finding should be non-compliant"
    assert finding.measured == "2.000–5.000 mm", (
        "Measured string should show the full range"
    )

    # This is the critical assertion: the point must be from the max value.
    assert finding.measurement_point == (
        2.0,
        2.0,
        2.0,
    ), "The measurement_point should be the wall_thickness_max_point"


def test_simple_rule_violation_attaches_point():
    """
    Verifies that a simple rule violation (e.g., draft_angle < min)
    correctly attaches the measurement_point from the corresponding
    `_point` attribute.
    """
    # This face has a draft angle of 0.2 degrees, which is below the rule's
    # threshold of 0.5 degrees.
    mock_face = PartModelFace(
        face_id=789,
        label="test_face_for_draft_rule",
        draft_angle=0.2,
        draft_angle_point=(6.0, 7.0, 8.0),  # The point of shallowest draft
        feature_class="plane",
        sample_count=10,
    )

    # A rule that checks for minimum draft. This is a real rule from the catalog.
    draft_rule = {
        "rule_id": "IM-010",
        "rule_name": "Minimum draft on all vertical faces",
        "metric": "draft_angle_min",
        "process_family": "Injection Moulding",
        "guideline_ref": "test",
        "severity": "critical",
        "category": "Draft",
        "predicate": {"type": "simple", "operator": ">=", "threshold": 0.5},
    }

    spec = rule_scoping.METRIC_REGISTRY.get("draft_angle_min")
    assert spec is not None

    findings = _evaluate_rule_on_faces(
        rule=draft_rule, spec=spec, predicate=draft_rule["predicate"], faces=[mock_face], divisor=1.0
    )

    assert len(findings) == 1
    finding = findings[0]

    assert finding.status == "NON-COMPLIANT"
    assert finding.measured == "0.200°"
    assert finding.measurement_point == (6.0, 7.0, 8.0)

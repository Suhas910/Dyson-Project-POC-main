"""Calibration tests: measure parts whose dimensions are known exactly.

Each test states the expected value from the part's construction, not from a
previous run of the measurement code, so a regression in the geometry layer
fails here rather than silently changing every customer report.
"""

import math

import pytest

import features
import step_loader
from tests import geometry_fixtures
from tests.conftest import PULL_DIRECTION

# Ray-cast measurements on exact primitives agree with the construction to well
# under a micron; the tolerance is loose enough to survive tessellation noise
# but far tighter than any DFM threshold in the catalog.
LENGTH_TOLERANCE_MM = 0.01
ANGLE_TOLERANCE_DEG = 0.05


def horizontal_planar_faces(model):
    """Planar faces whose normal is perpendicular to the pull direction."""
    return [
        face
        for face in model.faces
        if face.surface_type == "plane"
        and face.face_normal
        and abs(face.face_normal[2]) < 1e-6
    ]


def faces_perpendicular_to_pull(model):
    return [face for face in model.faces if face.is_perpendicular_to_pull]


# --- Ingest ----------------------------------------------------------------


def test_unit_conversion_from_inches(parts_dir):
    """A file declaring inches must arrive as millimetres.

    Without this conversion every threshold in the catalog is compared against a
    number 25.4 times too small, and nothing in the report would look wrong.
    """
    path, expected = geometry_fixtures.inch_declared_plate(parts_dir)
    loaded = step_loader.load_step(path)

    assert loaded.source_units == "inch"
    assert loaded.bounding_box_mm is not None
    assert loaded.bounding_box_mm[0] == pytest.approx(
        expected["expected_mm"], abs=LENGTH_TOLERANCE_MM
    )


def test_loader_reports_a_closed_solid(built_parts):
    loaded = built_parts["plain_box"]["loaded"]
    assert loaded.solid_count == 1
    assert loaded.is_valid_solid is True
    assert loaded.face_count == 6


def test_every_face_yields_a_sample_point(built_parts):
    """A face with no interior sample is a face that was never measured."""
    for name, part in built_parts.items():
        unmeasured = [f.face_id for f in part["model"].faces if f.sample_count == 0]
        assert not unmeasured, f"{name}: faces {unmeasured} produced no sample point"


# --- Draft angle -----------------------------------------------------------


def test_vertical_walls_have_zero_draft(built_parts):
    """The case minimum-draft rules exist to catch.

    A wall parallel to the pull direction has no draft at all. Measuring the
    normal-to-pull angle instead would report 90 degrees here and pass every
    minimum-draft rule in the catalog.
    """
    model = built_parts["plain_box"]["model"]
    walls = horizontal_planar_faces(model)

    assert len(walls) == 4
    for wall in walls:
        assert wall.draft_angle == pytest.approx(0.0, abs=ANGLE_TOLERANCE_DEG)


def test_faces_perpendicular_to_pull_are_exempt_not_failed(built_parts):
    """A flat top and bottom must be exempt from draft rules, not failed by them."""
    model = built_parts["plain_box"]["model"]
    perpendicular = faces_perpendicular_to_pull(model)

    assert len(perpendicular) == 2
    for face in perpendicular:
        assert face.draft_angle == pytest.approx(90.0, abs=ANGLE_TOLERANCE_DEG)


def test_drafted_wall_reports_its_construction_angle(built_parts):
    """A cone tapering by a known angle must measure that angle."""
    part = built_parts["drafted_frustum"]
    expected = part["expected"]["draft_deg"]
    cone_faces = [f for f in part["model"].faces if f.surface_type == "cone"]

    assert len(cone_faces) == 1
    assert cone_faces[0].draft_angle == pytest.approx(expected, abs=ANGLE_TOLERANCE_DEG)
    # Guards against measuring the complementary angle, which would also be
    # "close to a plausible number" but wrong by 90 degrees.
    assert cone_faces[0].draft_angle < 45.0


def test_draft_is_measured_against_each_face_own_mould_half(built_parts):
    """Draft is never negative and never exceeds 90 degrees.

    Both halves of the mould are treated symmetrically, so a downward-facing
    face reports its true draft rather than the supplement of it.
    """
    for name, part in built_parts.items():
        for face in part["model"].faces:
            if face.draft_angle is None:
                continue
            assert -1e-9 <= face.draft_angle <= 90.0 + 1e-9, (
                f"{name} face {face.face_id} reported draft {face.draft_angle}"
            )


# --- Wall thickness --------------------------------------------------------


def test_shelled_box_walls_measure_their_construction_thickness(built_parts):
    """Every wall of a 2 mm shelled box must measure 2 mm.

    This is the test that catches an inverted normal: a thickness ray fired
    outward instead of inward either finds nothing or reports the distance
    across the whole part.
    """
    part = built_parts["shelled_box"]
    expected = part["expected"]["wall_thickness"]
    walls = horizontal_planar_faces(part["model"])

    # Four outer walls and four cavity walls.
    assert len(walls) == 8
    for wall in walls:
        assert wall.wall_thickness is not None
        assert wall.wall_thickness == pytest.approx(expected, abs=LENGTH_TOLERANCE_MM)


def test_solid_box_thickness_spans_the_part(built_parts):
    """On a solid block the ray crosses the whole part, in each direction."""
    part = built_parts["plain_box"]
    model = part["model"]

    walls = horizontal_planar_faces(model)
    for wall in walls:
        assert wall.wall_thickness == pytest.approx(
            part["expected"]["width"], abs=LENGTH_TOLERANCE_MM
        )

    for face in faces_perpendicular_to_pull(model):
        assert face.wall_thickness == pytest.approx(
            part["expected"]["height"], abs=LENGTH_TOLERANCE_MM
        )


def test_thickness_is_never_negative_or_zero(built_parts):
    for name, part in built_parts.items():
        for face in part["model"].faces:
            if face.wall_thickness is None:
                continue
            assert face.wall_thickness > 0, (
                f"{name} face {face.face_id} reported non-positive thickness"
            )


# --- Holes and feature classification --------------------------------------


def test_through_hole_is_recognised_with_correct_dimensions(built_parts):
    part = built_parts["plate_with_hole"]
    expected = part["expected"]
    holes = [f for f in part["model"].faces if f.feature_class == "hole"]

    assert len(holes) == expected["hole_count"]
    hole = holes[0]
    assert hole.hole_diameter == pytest.approx(
        expected["hole_diameter"], abs=LENGTH_TOLERANCE_MM
    )
    assert hole.hole_depth == pytest.approx(
        expected["hole_depth"], abs=LENGTH_TOLERANCE_MM
    )
    assert hole.hole_depth_to_diameter_ratio == pytest.approx(
        expected["hole_depth"] / expected["hole_diameter"], abs=0.01
    )


def test_plain_box_contains_no_holes(built_parts):
    """Guards against classifying arbitrary faces as holes."""
    model = built_parts["plain_box"]["model"]
    assert [f for f in model.faces if f.feature_class == "hole"] == []


def test_closed_solid_reports_no_edge_distance(built_parts):
    """A closed solid has no free edges, so hole-to-edge distance is unavailable.

    Reporting it as unavailable is correct; inventing a number here is what
    would put a wrong verdict in front of an engineer.
    """
    part = built_parts["plate_with_hole"]
    holes = [f for f in part["model"].faces if f.feature_class == "hole"]
    assert holes[0].hole_to_edge_distance is None


# --- Undercuts -------------------------------------------------------------


def test_cross_hole_is_detected_as_an_undercut(built_parts):
    """A bore perpendicular to pull cannot be moulded without a side action."""
    model = built_parts["box_with_side_hole"]["model"]
    undercuts = [f for f in model.faces if f.is_undercut]

    assert undercuts, "cross hole was not detected as an undercut"
    assert any(f.surface_type == "cylinder" for f in undercuts)


def test_plain_box_has_no_undercuts(built_parts):
    """The control: a plain box is fully mouldable in a two-plate tool.

    Without this, an undercut check that simply returns True everywhere would
    pass the test above.
    """
    model = built_parts["plain_box"]["model"]
    assert [f.face_id for f in model.faces if f.is_undercut] == []


def test_open_box_has_no_undercuts(built_parts):
    """A shelled box is a draw-direction shape and needs no side actions."""
    model = built_parts["shelled_box"]["model"]
    assert [f.face_id for f in model.faces if f.is_undercut] == []


# --- Orientation -----------------------------------------------------------


def test_normals_point_out_of_the_material(built_parts):
    """Every reported normal must point away from the solid.

    Checked directly rather than through a derived measurement: for a box
    centred on its own bounding box, the outward normal of each face must have a
    positive component along the direction from the centre to the face.
    """
    part = built_parts["plain_box"]
    model = part["model"]
    centre = (
        geometry_fixtures.PLAIN_BOX["width"] / 2,
        geometry_fixtures.PLAIN_BOX["depth"] / 2,
        geometry_fixtures.PLAIN_BOX["height"] / 2,
    )

    for face in model.faces:
        assert face.face_normal is not None
        assert face.face_centroid is not None
        outward = [face.face_centroid[i] - centre[i] for i in range(3)]
        dot = sum(outward[i] * face.face_normal[i] for i in range(3))
        assert dot > 0, (
            f"face {face.face_id} normal {face.face_normal} points into the solid"
        )

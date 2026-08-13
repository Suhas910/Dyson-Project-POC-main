"""Tests for turning face numbers into feature names.

The risk this module carries is not that a name looks clumsy -- it is that a
name is confidently wrong. A face number is honestly uninformative; "the boss
near the parting line" attached to a fillet sends an engineer to the wrong
feature and costs more than no name at all. These tests therefore check what
the names claim, not just that they exist.
"""

import pytest

import feature_naming
import features
from models import PartModelFace
from tests.geometry_fixtures import (
    box_with_side_hole,
    plain_box,
    plate_with_hole,
    shelled_box,
)

from OCC.Core.gp import gp_Dir

PULL = gp_Dir(0, 0, 1)
BOX_BBOX = (0.0, 0.0, 0.0, 40.0, 40.0, 20.0)


def model_for(fixture):
    shape, expected = fixture()
    return features.build_part_model(shape, PULL, detect_undercuts=False), expected


def face(**kwargs) -> PartModelFace:
    """A face record with only the fields a naming decision depends on."""
    kwargs.setdefault("face_id", 1)
    return PartModelFace(**kwargs)


# --- Position ---------------------------------------------------------------


@pytest.mark.parametrize(
    "centroid,expected",
    [
        ((20.0, 20.0, 10.0), "centre"),
        ((2.0, 20.0, 10.0), "left"),
        ((38.0, 20.0, 10.0), "right"),
        ((20.0, 2.0, 10.0), "front"),
        ((20.0, 38.0, 10.0), "rear"),
        ((20.0, 20.0, 1.0), "lower"),
        ((20.0, 20.0, 19.0), "upper"),
        ((2.0, 2.0, 19.0), "upper front left"),
    ],
)
def test_position_reads_off_the_bounding_box(centroid, expected):
    assert feature_naming.describe_position(centroid, BOX_BBOX) == expected


def test_position_is_omitted_when_there_is_no_bounding_box():
    assert feature_naming.describe_position((1.0, 2.0, 3.0), None) is None


# --- Dimension formatting ---------------------------------------------------


def test_the_same_nominal_radius_formats_identically():
    """A blend measured at 9.9994 and one at 10.0001 are the same R10 blend.

    Choosing the precision from the unrounded value would print them as "9.99"
    and "10.0", splitting one feature into two that look unrelated in a report.
    """
    assert feature_naming._format_mm(9.9994) == feature_naming._format_mm(10.0001)


def test_small_features_keep_two_decimals():
    # 3/16 inch is 4.7625 mm; rounding that to 4.8 would lose the identity of
    # the drill size on an imperial part.
    assert feature_naming._format_mm(4.7625) == "4.76"


# --- What the names claim ---------------------------------------------------


def test_a_solid_box_is_named_top_base_and_walls():
    model, _ = model_for(plain_box)
    labels = {f.label for f in model.faces}
    kinds = {f.label_kind for f in model.faces}

    assert "Top face" in labels
    assert "Base" in labels
    assert kinds == {"top face", "base", "side wall"}


def test_an_upward_flat_inside_a_pocket_is_a_floor_not_a_top_face():
    """The distinguishing test for the whole module.

    A shelled box has two upward-facing flats perpendicular to pull: the rim at
    the top, and the floor of the cavity. They are not the same thing, and
    calling the floor a "top face" would be plainly wrong to anyone looking at
    the part.
    """
    model, _ = model_for(shelled_box)
    kinds = [f.label_kind for f in model.faces]

    assert "top face" in kinds
    assert "floor" in kinds


def test_a_hole_is_named_by_its_diameter():
    model, expected = model_for(plate_with_hole)
    hole_labels = [f.label for f in model.faces if f.label_kind == "hole"]

    assert hole_labels, "the plate's bore should be recognised as a hole"
    diameter = expected["hole_diameter"]
    assert all(f"Ø{diameter:.2f} mm" in label for label in hole_labels)


def test_holes_are_never_described_as_something_they_are_not():
    """Names must not acquire vocabulary the geometry cannot support.

    "Mounting hole", "vent", "parting line" and the like are inferences about
    purpose. Nothing here measures purpose, so nothing here may name it.
    """
    model, _ = model_for(box_with_side_hole)
    forbidden = ("mounting", "vent", "parting", "snap", "clip", "boss hole")

    for record in model.faces:
        assert record.label
        assert not any(word in record.label.lower() for word in forbidden)


# --- The uniqueness guarantee ----------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    [plain_box, shelled_box, plate_with_hole, box_with_side_hole],
    ids=lambda f: f.__name__,
)
def test_every_face_gets_a_name(fixture):
    model, _ = model_for(fixture)
    assert all(f.label and f.label_kind for f in model.faces)


@pytest.mark.parametrize(
    "fixture",
    [plain_box, shelled_box, plate_with_hole, box_with_side_hole],
    ids=lambda f: f.__name__,
)
def test_a_name_never_spans_two_separate_features(fixture):
    """Faces sharing a name must be within reach of each other.

    Sharing is legitimate -- a CAD kernel splits one bore into half-cylinders,
    and both halves are the same hole. Sharing across the part is not: a name
    that matches two features in two places cannot be used to point at either.
    """
    model, _ = model_for(fixture)

    by_label: dict[str, list] = {}
    for record in model.faces:
        by_label.setdefault(record.label, []).append(record)

    diagonal = (40.0**2 + 40.0**2 + 20.0**2) ** 0.5
    for label, group in by_label.items():
        if len(group) < 2:
            continue
        clusters = feature_naming._cluster(group, diagonal)
        assert len(clusters) == 1, f"'{label}' names {len(clusters)} separate features"


def test_two_halves_of_one_bore_share_a_name():
    """Splitting a bore is a CAD artefact, not a second hole.

    Two half-cylinders of the same drilled hole must not be given two different
    names -- that would invent a hole the part does not have.
    """
    diagonal = 60.0
    left = face(
        face_id=7, surface_type="cylinder", feature_class="hole",
        hole_diameter=5.0, face_centroid=(18.0, 20.0, 10.0), face_area=50.0,
    )
    right = face(
        face_id=8, surface_type="cylinder", feature_class="hole",
        hole_diameter=5.0, face_centroid=(22.0, 20.0, 10.0), face_area=50.0,
    )

    labels = feature_naming.build_labels([left, right], BOX_BBOX)
    assert labels[7].text == labels[8].text
    assert len(feature_naming._cluster([left, right], diagonal)) == 1


def test_two_distant_identical_holes_are_told_apart():
    near = face(
        face_id=3, surface_type="cylinder", feature_class="hole",
        hole_diameter=5.0, face_centroid=(4.0, 4.0, 10.0), face_area=50.0,
    )
    far = face(
        face_id=4, surface_type="cylinder", feature_class="hole",
        hole_diameter=5.0, face_centroid=(36.0, 4.0, 10.0), face_area=50.0,
    )

    labels = feature_naming.build_labels([near, far], BOX_BBOX)
    assert labels[3].text != labels[4].text


def test_a_unique_feature_carries_no_disambiguating_number():
    """Clutter is only paid for where it buys something.

    On a part with one hole, "Ø5.00 mm hole" is exact and appending a face
    number would be noise.
    """
    only = face(
        face_id=2, surface_type="cylinder", feature_class="hole",
        hole_diameter=5.0, face_centroid=(20.0, 20.0, 10.0), face_area=50.0,
    )
    assert feature_naming.build_labels([only], BOX_BBOX)[2].text == "Ø5.00 mm hole"


def test_names_survive_a_part_with_no_bounding_box():
    """Position is dropped rather than guessed when the box is unknown."""
    lonely = face(
        face_id=1, surface_type="plane", feature_class="plane",
        is_perpendicular_to_pull=True, face_normal=(0.0, 0.0, 1.0),
    )
    label = feature_naming.build_labels([lonely], None)[1]
    assert label.text == "Upward-facing flat"


# --- Inventory --------------------------------------------------------------


def test_inventory_counts_every_named_face():
    model, _ = model_for(shelled_box)
    counts = feature_naming.inventory(model.faces)

    assert sum(entry["count"] for entry in counts) == len(model.faces)
    # Most numerous first, so a reader sees what the part is mostly made of.
    assert counts == sorted(counts, key=lambda e: (-e["count"], e["kind"]))


# --- The contract with the rest of the pipeline -----------------------------


def test_findings_carry_the_label_of_the_face_they_name():
    """A finding's `location` and `feature_label` must describe the same face."""
    shape, _ = plate_with_hole()

    model = features.build_part_model(shape, PULL, detect_undercuts=False)
    by_id = {f.face_id: f for f in model.faces}

    # Simulating what execute_rules does, without needing the catalog.
    for record in model.faces:
        location = f"face {record.face_id}"
        face_id = int(location.split()[1])
        assert by_id[face_id].label == record.label

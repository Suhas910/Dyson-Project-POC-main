"""Tests for reading a part's process off its geometry.

The failure this guards against is quiet. Classify a moulded housing as
machined and every draft rule is skipped; the report comes back clean and says
nothing about the defect that mattered. So these tests check that the classifier
tells the two apart on real geometry, and that when it cannot, it says so rather
than guessing.
"""

import pytest

import features
import process_classifier
import step_loader
from tests.geometry_fixtures import (
    drafted_frustum,
    plain_box,
    plate_with_hole,
    shelled_box,
)

from OCC.Core.gp import gp_Dir

PULL = gp_Dir(0, 0, 1)
ALL_FAMILIES = [
    "DFA (Assembly)",
    "Die Casting",
    "Dimensional Capability",
    "Injection Moulding",
    "Machining",
    "Metal Injection Moulding",
    "Powder Metallurgy",
    "Serviceability",
    "Sheet Metal",
    "Standards-Derived Geometry",
]


def classify(fixture):
    shape, expected = fixture()
    model = features.build_part_model(shape, PULL, detect_undercuts=True)
    loaded = step_loader.LoadedPart(
        shape=shape,
        source_units="millimetre",
        solid_count=1,
        shell_count=1,
        face_count=len(model.faces),
        is_valid_solid=True,
        was_healed=False,
        bounding_box_mm=_bbox(shape),
        warnings=[],
    )
    return process_classifier.classify(model, loaded, ALL_FAMILIES), expected


def _bbox(shape):
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib

    box = Bnd_Box()
    brepbndlib.Add(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return [xmax - xmin, ymax - ymin, zmax - zmin]


def scored(result, family):
    return next(c.score for c in result.candidates if c.process_family == family)


# --- The distinction that matters ------------------------------------------


def test_an_undrafted_prismatic_part_does_not_read_as_moulded():
    """Zero draft is the strongest evidence against a cast-to-shape process."""
    result, _ = classify(plain_box)
    assert scored(result, "Machining") > scored(result, "Injection Moulding")


def test_a_drafted_part_reads_as_moulded_rather_than_machined():
    """The drafted wall here is a cone, not a plane.

    Measuring draft on planar faces only would report "no drafted walls" for a
    part that is drafted throughout -- the worst possible answer from the
    signal that separates moulding from machining most sharply.
    """
    result, _ = classify(drafted_frustum)
    assert result.signals["draft_coverage"].value == 1.0
    assert scored(result, "Injection Moulding") > scored(result, "Machining")


def test_a_bore_wall_is_not_counted_as_an_undrafted_wall():
    """A drilled hole is a cylinder parallel to pull; its draft is zero by
    construction. Counting those zeroes would make every drilled part -- moulded
    or not -- look machined."""
    result, _ = classify(plate_with_hole)
    coverage = result.signals["draft_coverage"]
    assert "hole" not in coverage.display


def test_a_family_recognised_only_by_compatibility_cannot_claim_confidence():
    """Pressing passes on any small, simple, prismatic, undrafted part.

    So can machining. Scoring pressing at full marks would present "nothing
    rules this out" as "this is what it is".
    """
    result, _ = classify(plain_box)
    pressing = next(
        c for c in result.candidates if c.process_family == "Powder Metallurgy"
    )
    assert pressing.confidence == "possible"
    assert any("not ruled out" in reason for reason in pressing.evidence_against)


# --- Disqualifiers ---------------------------------------------------------


def test_a_part_whose_section_varies_cannot_be_sheet_metal():
    """Forming moves sheet about; it cannot change the gauge it started from."""
    result, _ = classify(plain_box)
    sheet = next(c for c in result.candidates if c.process_family == "Sheet Metal")

    assert sheet.score == 0.0
    assert any("gauge" in reason for reason in sheet.evidence_against)


def test_the_absence_of_an_undercut_is_not_evidence_for_pressing():
    """Almost nothing has an undercut, so its absence argues for nothing.

    Counting it as a point in favour is how a scorer ends up ranking every
    simple part as a pressing.
    """
    result, _ = classify(plain_box)
    pressing = next(
        c for c in result.candidates if c.process_family == "Powder Metallurgy"
    )
    assert not any("undercut" in reason for reason in pressing.evidence_for)


# --- What is always analysed ----------------------------------------------


def test_universal_families_are_always_analysed():
    """Geometry standards hold whatever made the part, so they never opt out."""
    result, _ = classify(plate_with_hole)
    for family in process_classifier.UNIVERSAL_FAMILIES:
        assert family in result.families_to_analyse


def test_assembly_families_are_skipped_for_a_single_solid():
    result, _ = classify(plain_box)
    for family in process_classifier.ASSEMBLY_FAMILIES:
        assert family not in result.families_to_analyse


def test_only_families_the_catalogue_covers_are_offered():
    """A section the report cannot fill must never be promised."""
    shape, _ = plain_box()
    model = features.build_part_model(shape, PULL, detect_undercuts=True)
    loaded = step_loader.LoadedPart(
        shape=shape, source_units="millimetre", solid_count=1, shell_count=1,
        face_count=len(model.faces), is_valid_solid=True, was_healed=False,
        bounding_box_mm=_bbox(shape), warnings=[],
    )
    result = process_classifier.classify(model, loaded, ["Machining"])

    assert result.families_to_analyse == ["Machining"]
    assert {c.process_family for c in result.candidates} == {"Machining"}


# --- Honesty ---------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture", [plain_box, shelled_box, plate_with_hole, drafted_frustum],
    ids=lambda f: f.__name__,
)
def test_every_score_is_accompanied_by_its_reasoning(fixture):
    """A score with no evidence behind it cannot be checked or argued with."""
    result, _ = classify(fixture)
    for candidate in result.candidates:
        if candidate.basis != "detected":
            continue
        assert candidate.evidence_for or candidate.evidence_against


@pytest.mark.parametrize(
    "fixture", [plain_box, shelled_box, plate_with_hole, drafted_frustum],
    ids=lambda f: f.__name__,
)
def test_a_close_call_is_reported_rather_than_broken(fixture):
    """Two families within the tie margin must produce a note saying so."""
    result, _ = classify(fixture)
    detected = [
        c for c in result.candidates
        if c.basis == "detected" and c.score >= process_classifier.ANALYSIS_THRESHOLD
    ]
    if len(detected) < 2:
        return
    gap = detected[0].score - detected[1].score
    if gap <= process_classifier.TIE_MARGIN:
        assert result.notes, "a tie this close must be stated, not silently ranked"


def test_an_unmeasurable_signal_neither_helps_nor_hurts():
    """A missing measurement is not the same as a failed test.

    Counting it as failure would make a part whose walls could not be measured
    score as "definitely not moulded", which is the opposite of what the
    missing measurement means.
    """
    scorer = process_classifier._Scorer()
    scorer.test(3.0, None, "passed", "failed")
    assert scorer.weight == 0.0
    assert scorer.score() == 0.0
    assert not scorer.evidence_for and not scorer.evidence_against

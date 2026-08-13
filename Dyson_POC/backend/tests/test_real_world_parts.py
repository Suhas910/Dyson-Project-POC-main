"""Regression tests against real CAD exports, not synthetic primitives.

The synthetic calibration suite proves the measurements are numerically right.
It cannot prove the code survives what real CAD produces, because parts built
from modelling-kernel primitives are far tidier than parts exported by CAD:
a primitive cylinder comes back as one seamed face, exact surfaces are always
present, and units are always millimetres.

Every test here was written after a real file broke something.

Files come from the NIST MBE PMI validation set. They are large and not part of
the source tree, so each test skips when the folder is absent.
"""

from pathlib import Path

import pytest

from OCC.Core.gp import gp_Dir

import features
import step_loader

NIST = Path(__file__).resolve().parents[2] / "NIST-PMI-STEP-Files"
PULL = gp_Dir(0, 0, 1)

pytestmark = pytest.mark.skipif(
    not NIST.is_dir(), reason="NIST PMI STEP files not present"
)


def _load(relative: str):
    path = NIST / relative
    if not path.exists():
        pytest.skip(f"{relative} not present")
    return step_loader.load_step(path)


def test_tessellated_geometry_does_not_crash_the_process():
    """A tessellated STEP file used to segfault, killing the whole process.

    Faces in a faceted export have no analytic surface behind them, and the
    OCCT calls that take one do not raise on null -- they abort the process.
    A Python `try` cannot catch that, so such faces have to be excluded before
    any measurement touches them.
    """
    loaded = _load("nist_ftc_08_asme1_ap242-e1-tg.stp")
    model = features.build_part_model(loaded.shape, PULL, detect_undercuts=False)

    assert len(model.faces) > 0
    assert all(f.surface_type == "tessellated" for f in model.faces)
    # Nothing measurable, and nothing pretending to be measured.
    assert all(f.sample_count == 0 for f in model.faces)
    assert all(f.wall_thickness is None for f in model.faces)


def test_tessellated_file_is_reported_as_unusable_not_silently_analysed():
    loaded = _load("nist_ftc_08_asme1_ap242-e1-tg.stp")
    assert loaded.solid_count == 0
    assert loaded.is_valid_solid is False
    assert any("closed solid" in w for w in loaded.warnings)


def test_holes_split_across_faces_are_still_recognised_as_holes():
    """CAD splits a cylinder at its seam; primitives do not.

    Judging "full revolution?" one face at a time classified every real drilled
    hole as a blend fillet -- 57 cylinders on this part, zero holes -- so hole
    rules never ran. Synthetic fixtures cannot catch this.
    """
    loaded = _load("nist_ctc_01_asme1_ap242-e1.stp")
    model = features.build_part_model(loaded.shape, PULL, detect_undercuts=False)

    holes = [f for f in model.faces if f.feature_class == "hole"]
    assert holes, "no holes found on a part that is full of them"

    diameters = sorted({round(f.hole_diameter, 1) for f in holes})
    assert all(d > 0 for d in diameters)
    # Every hole must carry the measurements its rules need.
    for hole in holes:
        assert hole.hole_depth is not None
        assert hole.hole_depth_to_diameter_ratio is not None


def test_length_unit_is_read_not_the_angle_unit():
    """Real CAD declares several units; the first one is usually DEGREE.

    Matching the first CONVERSION_BASED_UNIT reported this part as being
    measured in degrees.
    """
    loaded = _load("nist_ctc_01_asme1_ap242-e1.stp")
    assert loaded.source_units == "millimetre"


def test_inch_parts_are_identified_as_inch():
    """The NIST set genuinely mixes units, so this is not hypothetical."""
    loaded = _load("nist_ctc_05_asme1_ap242-e1.stp")
    assert loaded.source_units == "inch"


def test_every_face_of_a_real_part_gets_sampled():
    """No face may be silently skipped on a well-formed part."""
    loaded = _load("nist_ctc_01_asme1_ap242-e1.stp")
    model = features.build_part_model(loaded.shape, PULL, detect_undercuts=False)
    unmeasured = [f.face_id for f in model.faces if f.sample_count == 0]
    assert not unmeasured, f"faces {unmeasured[:10]} produced no sample point"


def test_hole_to_edge_is_not_computed_on_a_closed_solid():
    """The metric measures distance to an open sheet's boundary.

    A closed solid has no such boundary, and healing can leave spurious free
    edges behind — one real part had 159 cylinders and 164 of them, which is
    26,000 exact distance solves and about nine minutes for one file.
    """
    loaded = _load("nist_ctc_01_asme1_ap242-e1.stp")
    assert loaded.solid_count > 0
    model = features.build_part_model(loaded.shape, PULL, detect_undercuts=False)
    holes = [f for f in model.faces if f.feature_class == "hole"]
    assert holes
    assert all(f.hole_to_edge_distance is None for f in holes)


def test_a_dense_part_extracts_in_seconds_not_minutes():
    """Guards the quadratic edge-distance regression."""
    import time

    loaded = _load("nist_ftc_10_asme1_ap242-e2.stp")
    started = time.monotonic()
    model = features.build_part_model(loaded.shape, PULL, detect_undercuts=False)
    elapsed = time.monotonic() - started

    assert len(model.faces) > 200
    # Generous: it was ~540s before the bounding-box prefilter.
    assert elapsed < 60, f"extraction took {elapsed:.1f}s"

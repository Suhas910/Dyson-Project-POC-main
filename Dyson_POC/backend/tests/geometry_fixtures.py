"""geometry_fixtures.py - Synthetic STEP parts with known dimensions.

Geometry code cannot be validated by looking at a findings table: a wall
thickness of 1.87 mm looks equally plausible whether it is right or wrong. The
only way to know the measurements are correct is to measure parts whose answers
are known in advance.

Every part here is built from primitives with exact dimensions, so each test
can assert against a number derived from the construction rather than from a
previous run of the same code.
"""

import math
from pathlib import Path

from OCC.Core.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCone,
    BRepPrimAPI_MakeCylinder,
)
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCC.Core.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCC.Core.TopoDS import TopoDS_Shape


def write_step(shape: TopoDS_Shape, path: Path) -> Path:
    """Writes a shape to a STEP file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    status = writer.Write(str(path))
    if status != 1:  # IFSelect_RetDone
        raise IOError(f"Failed to write STEP file to {path} (status {status})")
    return path


# --- Part definitions ------------------------------------------------------
#
# Each builder returns (shape, expected_values) so the assertions in the tests
# read against the construction rather than against magic numbers.


PLAIN_BOX = {"width": 40.0, "depth": 40.0, "height": 20.0}


def plain_box() -> tuple[TopoDS_Shape, dict]:
    """A solid box: six planar faces, no undercuts, zero draft on the walls.

    The control case. Its side walls are exactly parallel to the pull direction,
    so they must report 0 degrees of draft -- the condition minimum-draft rules
    exist to catch. Its top and bottom are perpendicular to pull and must be
    reported as exempt rather than failed.
    """
    box = BRepPrimAPI_MakeBox(
        PLAIN_BOX["width"], PLAIN_BOX["depth"], PLAIN_BOX["height"]
    ).Shape()
    return box, {
        "face_count": 6,
        "side_wall_draft_deg": 0.0,
        "perpendicular_face_count": 2,  # top and bottom
        "undercut_count": 0,
        # A ray from the top face travels the full height; from a side wall it
        # crosses the full width.
        "height": PLAIN_BOX["height"],
        "width": PLAIN_BOX["width"],
    }


SHELLED_BOX = {"outer": 40.0, "height": 20.0, "wall": 2.0}


def shelled_box() -> tuple[TopoDS_Shape, dict]:
    """An open box with walls of exactly known thickness.

    The inner cavity is cut all the way through the top, leaving four side walls
    and a floor of uniform thickness. This is the primary wall-thickness
    calibration: every wall face must measure the wall dimension exactly.
    """
    wall = SHELLED_BOX["wall"]
    outer = SHELLED_BOX["outer"]
    height = SHELLED_BOX["height"]

    solid = BRepPrimAPI_MakeBox(outer, outer, height).Shape()
    # Extends above the top face so the cavity is open rather than enclosed.
    cavity = BRepPrimAPI_MakeBox(
        gp_Pnt(wall, wall, wall), outer - 2 * wall, outer - 2 * wall, height
    ).Shape()
    shape = BRepAlgoAPI_Cut(solid, cavity).Shape()

    return shape, {"wall_thickness": wall, "floor_thickness": wall}


PLATE_WITH_HOLE = {
    "width": 50.0,
    "depth": 50.0,
    "thickness": 3.0,
    "hole_radius": 2.5,
}


def plate_with_hole() -> tuple[TopoDS_Shape, dict]:
    """A flat plate with a single through hole of known diameter.

    Calibrates hole recognition: the cylindrical face must be classified as a
    hole (not a fillet), and its diameter and depth must match the drill and the
    plate thickness respectively.
    """
    spec = PLATE_WITH_HOLE
    plate = BRepPrimAPI_MakeBox(spec["width"], spec["depth"], spec["thickness"]).Shape()
    axis = gp_Ax2(
        gp_Pnt(spec["width"] / 2, spec["depth"] / 2, -1.0), gp_Dir(0, 0, 1)
    )
    drill = BRepPrimAPI_MakeCylinder(
        axis, spec["hole_radius"], spec["thickness"] + 2.0
    ).Shape()
    shape = BRepAlgoAPI_Cut(plate, drill).Shape()

    return shape, {
        "hole_count": 1,
        "hole_diameter": spec["hole_radius"] * 2,
        "hole_depth": spec["thickness"],
        "plate_thickness": spec["thickness"],
    }


DRAFTED_FRUSTUM = {"base_radius": 10.0, "top_radius": 9.0, "height": 20.0}


def drafted_frustum() -> tuple[TopoDS_Shape, dict]:
    """A truncated cone whose wall leans by an exactly known angle.

    Over its height the radius shrinks by (base - top), so the wall leans away
    from the pull direction by atan((base - top) / height). This is the draft
    calibration, and it is the test that distinguishes a correct draft
    convention from one measuring the complementary angle.
    """
    spec = DRAFTED_FRUSTUM
    cone = BRepPrimAPI_MakeCone(
        spec["base_radius"], spec["top_radius"], spec["height"]
    ).Shape()
    expected_draft = math.degrees(
        math.atan((spec["base_radius"] - spec["top_radius"]) / spec["height"])
    )
    return cone, {"draft_deg": expected_draft}


SIDE_HOLE_BOX = {"size": 40.0, "height": 20.0, "hole_radius": 3.0}


def box_with_side_hole() -> tuple[TopoDS_Shape, dict]:
    """A box with a hole bored perpendicular to the pull direction.

    A cross hole cannot be formed by a mould opening along Z: material sits
    directly above the upper half of the bore, so it needs a side action. This
    is the undercut calibration, and it is exactly the case the catalog's
    undercut rules describe.
    """
    spec = SIDE_HOLE_BOX
    box = BRepPrimAPI_MakeBox(spec["size"], spec["size"], spec["height"]).Shape()
    axis = gp_Ax2(
        gp_Pnt(-1.0, spec["size"] / 2, spec["height"] / 2), gp_Dir(1, 0, 0)
    )
    bore = BRepPrimAPI_MakeCylinder(axis, spec["hole_radius"], spec["size"] + 2.0).Shape()
    shape = BRepAlgoAPI_Cut(box, bore).Shape()
    return shape, {"has_undercut": True}


def inch_declared_plate(tmp_dir: Path) -> tuple[Path, dict]:
    """Writes a STEP file whose geometry is in millimetres but declared as inches.

    OCCT converts a file's declared unit to millimetres on import. This fixture
    exists to prove that conversion actually happens for this build, because a
    silent failure would scale every threshold comparison by 25.4.
    """
    size = 10.0
    box = BRepPrimAPI_MakeBox(size, size, size).Shape()
    millimetre_file = tmp_dir / "_mm_source.step"
    write_step(box, millimetre_file)

    text = millimetre_file.read_text()
    metric_unit = "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )"
    inch_unit = (
        "( CONVERSION_BASED_UNIT('INCH',#9902) LENGTH_UNIT() NAMED_UNIT(#9900) )"
    )
    if metric_unit not in text:
        raise AssertionError("Unexpected unit block in generated STEP file")

    supporting_entities = (
        "\n#9900 = DIMENSIONAL_EXPONENTS(1.,0.,0.,0.,0.,0.,0.);"
        "\n#9901 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );"
        "\n#9902 = LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(25.4),#9901);"
    )
    text = text.replace(metric_unit, inch_unit, 1)
    text = text.replace("ENDSEC;\nEND-ISO-10303-21;", supporting_entities + "\nENDSEC;\nEND-ISO-10303-21;")

    inch_file = tmp_dir / "inch_plate.step"
    inch_file.write_text(text)
    return inch_file, {"declared_size": size, "expected_mm": size * 25.4}


ALL_PARTS = {
    "plain_box": plain_box,
    "shelled_box": shelled_box,
    "plate_with_hole": plate_with_hole,
    "drafted_frustum": drafted_frustum,
    "box_with_side_hole": box_with_side_hole,
}

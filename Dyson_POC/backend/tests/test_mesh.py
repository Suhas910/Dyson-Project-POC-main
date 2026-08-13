"""Tests for the tessellation that feeds the 3D view.

The contract that actually matters here is the face numbering. A finding says
"face 7"; the viewer paints face 7 and resolves a click back to face 7. If the
mesher and the analyser ever disagree about which face that is, the view will
still look perfectly plausible while pointing at the wrong wall -- a failure
that no amount of looking at the screen would catch. So the numbering is pinned
by test rather than by convention.
"""

import json
import struct

import pytest

import features
import mesh
from tests.geometry_fixtures import (
    PLAIN_BOX,
    box_with_side_hole,
    plain_box,
    plate_with_hole,
    shelled_box,
)


def unpack(blob: bytes) -> tuple[dict, dict]:
    """Reads the packed mesh back, the way the browser does."""
    assert blob[:8] == mesh.MAGIC

    header_length = struct.unpack_from("<I", blob, 8)[0]
    header = json.loads(blob[12 : 12 + header_length])

    vertices = header["vertexCount"]
    triangles = header["triangleCount"]

    offset = 12 + header_length
    positions = struct.unpack_from(f"<{vertices * 3}f", blob, offset)
    offset += vertices * 3 * 4
    normals = struct.unpack_from(f"<{vertices * 3}f", blob, offset)
    offset += vertices * 3 * 4
    face_ids = struct.unpack_from(f"<{vertices}I", blob, offset)
    offset += vertices * 4
    indices = struct.unpack_from(f"<{triangles * 3}I", blob, offset)
    offset += triangles * 3 * 4

    # Nothing left over: a trailing byte would mean the reader and writer
    # disagree about the layout, and the browser would read garbage.
    assert offset == len(blob)

    return header, {
        "positions": positions,
        "normals": normals,
        "face_ids": face_ids,
        "indices": indices,
    }


ALL_FIXTURES = [plain_box, shelled_box, plate_with_hole, box_with_side_hole]


@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.__name__)
def test_face_ids_match_the_analysis(fixture):
    """Every face the analyser numbers appears in the mesh under that number."""
    shape, _ = fixture()

    analysed = features.get_all_faces(shape)
    header, arrays = unpack(mesh.build(shape))

    meshed = set(arrays["face_ids"])
    assert meshed == set(range(1, len(analysed) + 1))
    assert header["faceCount"] == len(analysed)
    assert header["skippedFaces"] == 0


@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.__name__)
def test_buffers_are_internally_consistent(fixture):
    shape, _ = fixture()
    header, arrays = unpack(mesh.build(shape))

    vertices = header["vertexCount"]
    assert len(arrays["face_ids"]) == vertices
    assert len(arrays["positions"]) == vertices * 3
    assert len(arrays["normals"]) == vertices * 3
    assert max(arrays["indices"]) < vertices


@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.__name__)
def test_every_triangle_belongs_to_exactly_one_face(fixture):
    """The viewer reads a triangle's face from any one of its vertices.

    That shortcut is only sound if a triangle never spans two faces, which holds
    because OCCT tessellates each face separately and nothing merges the buffers
    afterwards. Worth asserting: a future optimisation that welded shared
    vertices would break picking silently.
    """
    shape, _ = fixture()
    _, arrays = unpack(mesh.build(shape))

    face_ids = arrays["face_ids"]
    indices = arrays["indices"]
    for i in range(0, len(indices), 3):
        a, b, c = indices[i], indices[i + 1], indices[i + 2]
        assert face_ids[a] == face_ids[b] == face_ids[c]


@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.__name__)
def test_normals_are_unit_length(fixture):
    shape, _ = fixture()
    _, arrays = unpack(mesh.build(shape))

    normals = arrays["normals"]
    for i in range(0, len(normals), 3):
        length = (normals[i] ** 2 + normals[i + 1] ** 2 + normals[i + 2] ** 2) ** 0.5
        assert length == pytest.approx(1.0, abs=1e-4)


def test_normals_point_out_of_the_solid():
    """A normal pointing inward renders the face black under any lighting.

    Checked on the plain box because its outward directions are known exactly:
    each face normal must agree with the direction from the centre of the box to
    that face.
    """
    shape, _expected = plain_box()
    _, arrays = unpack(mesh.build(shape))

    positions = arrays["positions"]
    normals = arrays["normals"]

    centre = (
        PLAIN_BOX["width"] / 2,
        PLAIN_BOX["depth"] / 2,
        PLAIN_BOX["height"] / 2,
    )

    for i in range(0, len(normals), 3):
        outward = [positions[i + axis] - centre[axis] for axis in range(3)]
        dot = sum(outward[axis] * normals[i + axis] for axis in range(3))
        # Every vertex of a box lies on a corner, so the outward direction and
        # the face normal are never more than 60 degrees apart.
        assert dot > 0


def test_bounding_box_matches_the_geometry():
    shape, _expected = plain_box()
    header, _ = unpack(mesh.build(shape))

    assert header["bbox"]["min"] == pytest.approx([0.0, 0.0, 0.0], abs=1e-6)
    assert header["bbox"]["max"] == pytest.approx(
        [PLAIN_BOX["width"], PLAIN_BOX["depth"], PLAIN_BOX["height"]], abs=1e-6
    )


def test_a_shape_that_cannot_be_meshed_returns_none():
    """Meshing failure must never take the analysis down with it."""
    from OCC.Core.TopoDS import TopoDS_Shape

    assert mesh.build(TopoDS_Shape()) is None

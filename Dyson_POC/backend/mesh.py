"""mesh.py - Turns the analysed solid into a triangle mesh the browser can draw.

The point of this module is not to render a pretty picture; it is to make the
findings locatable. Every finding names a face ("face 214"), and a face id is
meaningless to a person reading a table. Tessellating the same faces, in the
same order, with the id carried through to the vertex level means the viewer can
paint each face by its worst finding and let a click on the model select the
findings that belong to it.

The traversal below therefore has to match `features.get_all_faces` exactly. Both
walk `TopExp_Explorer(shape, TopAbs_FACE)` and both number faces from one, so the
ids agree by construction rather than by a lookup that could drift.
"""

import json
import logging
import struct
from typing import Optional

from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.TopoDS import TopoDS_Shape, topods

import features

MAGIC = b"DFMMESH1"

# Chord height as a fraction of the part's bounding diagonal. At 1/500 a 200 mm
# housing is tessellated to 0.4 mm, which is smooth on screen without producing
# a mesh too large to send. Relative rather than absolute so a 5 mm clip and a
# 500 mm housing both come out looking equally refined.
DEFLECTION_RATIO = 1.0 / 500.0

# Cap on how far a triangle may turn. Without it a cylinder tessellates into a
# visibly faceted tube however fine the chord tolerance is, because a short
# chord across a small-radius bore is still a large angle.
ANGULAR_DEFLECTION = 0.35

# Above this the payload stops being worth sending to a browser. Parts this
# dense are rare; when one appears the viewer is skipped rather than the whole
# analysis failing, and the reason is recorded.
MAX_TRIANGLES = 900_000


class MeshTooLarge(Exception):
    """The tessellation exceeded what is reasonable to send to the browser."""


def _accumulate_normals(positions, indices, normals):
    """Area-weighted vertex normals, accumulated per triangle.

    Vertices are never shared between faces here, so averaging within the buffer
    smooths a cylinder along its length while leaving a hard crease at every
    face boundary. That is exactly the right look for a CAD part: curvature
    reads as curvature, and edges stay edges.
    """
    for i in range(0, len(indices), 3):
        a, b, c = indices[i] * 3, indices[i + 1] * 3, indices[i + 2] * 3

        ux = positions[b] - positions[a]
        uy = positions[b + 1] - positions[a + 1]
        uz = positions[b + 2] - positions[a + 2]
        vx = positions[c] - positions[a]
        vy = positions[c + 1] - positions[a + 1]
        vz = positions[c + 2] - positions[a + 2]

        # Not normalised: the cross product's length is twice the triangle
        # area, which is the weighting we want anyway.
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx

        for base in (a, b, c):
            normals[base] += nx
            normals[base + 1] += ny
            normals[base + 2] += nz


def _normalise(normals):
    for i in range(0, len(normals), 3):
        x, y, z = normals[i], normals[i + 1], normals[i + 2]
        length = (x * x + y * y + z * z) ** 0.5
        if length > 1e-12:
            normals[i] = x / length
            normals[i + 1] = y / length
            normals[i + 2] = z / length
        else:
            # A degenerate triangle fan leaves a zero normal. Pointing it up is
            # arbitrary but keeps the shader from producing a black pixel.
            normals[i + 2] = 1.0


def tessellate(shape: TopoDS_Shape) -> dict:
    """Meshes every face and returns flat buffers plus the face id per vertex.

    Returns a dict of Python lists rather than numpy arrays; the caller packs
    them straight into bytes, and adding a numpy dependency for one loop is not
    worth it.
    """
    diagonal = features._shape_diagonal(shape)
    deflection = max(1e-3, diagonal * DEFLECTION_RATIO)

    # `True` for isRelative would make OCCT scale the tolerance per edge, which
    # under-tessellates small features on a large part. The deflection is
    # already scaled to the part, so absolute is what we want.
    BRepMesh_IncrementalMesh(shape, deflection, False, ANGULAR_DEFLECTION, True)

    positions: list[float] = []
    normals: list[float] = []
    face_ids: list[int] = []
    indices: list[int] = []

    face_ranges: dict[int, tuple[int, int]] = {}
    skipped = 0

    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    face_id = 0
    while explorer.More():
        face_id += 1
        face = topods.Face(explorer.Current())
        explorer.Next()

        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation(face, location)
        if triangulation is None:
            # A face OCCT could not mesh. Recorded rather than aborted: the rest
            # of the part is still worth looking at, and the findings table
            # remains the authority on what was measured.
            skipped += 1
            continue

        transform = location.Transformation()
        vertex_base = len(positions) // 3
        triangle_start = len(indices) // 3

        node_count = triangulation.NbNodes()
        for node in range(1, node_count + 1):
            point = triangulation.Node(node).Transformed(transform)
            positions.extend((point.X(), point.Y(), point.Z()))
            normals.extend((0.0, 0.0, 0.0))
            face_ids.append(face_id)

        # A reversed face has its triangles wound the other way round. Left
        # alone, its computed normals point into the solid and it renders as a
        # hole in the surface under any lighting model.
        reversed_face = face.Orientation() == TopAbs_REVERSED

        for triangle_index in range(1, triangulation.NbTriangles() + 1):
            a, b, c = triangulation.Triangle(triangle_index).Get()
            if reversed_face:
                a, c = c, a
            indices.extend(
                (vertex_base + a - 1, vertex_base + b - 1, vertex_base + c - 1)
            )

        face_ranges[face_id] = (triangle_start, len(indices) // 3)

        if len(indices) // 3 > MAX_TRIANGLES:
            raise MeshTooLarge(
                f"{len(indices) // 3} triangles exceeds the {MAX_TRIANGLES} limit"
            )

    if not indices:
        raise ValueError("The shape produced no triangles.")

    _accumulate_normals(positions, indices, normals)
    _normalise(normals)

    if skipped:
        logging.warning(f"{skipped} face(s) could not be tessellated for the viewer.")

    return {
        "positions": positions,
        "normals": normals,
        "face_ids": face_ids,
        "indices": indices,
        "face_ranges": face_ranges,
        "skipped": skipped,
        "deflection": deflection,
    }


def pack(mesh: dict, labels: Optional[dict] = None) -> bytes:
    """Serialises the mesh as one binary blob the browser can view directly.

    A JSON payload of the same data is roughly six times larger and has to be
    parsed a number at a time; this lands in typed arrays with no copying. The
    layout is a magic string, a length-prefixed JSON header, then the four
    arrays back to back in the order the header declares.
    """
    positions = mesh["positions"]
    normals = mesh["normals"]
    face_ids = mesh["face_ids"]
    indices = mesh["indices"]

    vertex_count = len(positions) // 3

    xs = positions[0::3]
    ys = positions[1::3]
    zs = positions[2::3]

    header = {
        "vertexCount": vertex_count,
        "triangleCount": len(indices) // 3,
        "faceCount": len(mesh["face_ranges"]),
        "skippedFaces": mesh["skipped"],
        # Feature names, keyed by face id. Carried here rather than derived in
        # the browser from the findings, because a face with no findings still
        # has a name -- and those are exactly the faces a user clicks when
        # asking "what is this, and why did nothing check it?".
        "faceLabels": {str(k): v for k, v in (labels or {}).items()},
        "deflection": round(mesh["deflection"], 6),
        # The viewer needs these to frame the camera before it has looked at a
        # single vertex.
        "bbox": {
            "min": [min(xs), min(ys), min(zs)],
            "max": [max(xs), max(ys), max(zs)],
        },
        # Declared rather than implied, so a reader of the format does not have
        # to recompute offsets to know what follows.
        "layout": [
            {"name": "positions", "type": "Float32", "components": 3},
            {"name": "normals", "type": "Float32", "components": 3},
            {"name": "faceIds", "type": "Uint32", "components": 1},
            {"name": "indices", "type": "Uint32", "components": 1},
        ],
    }

    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")

    # Pad the header so the typed arrays that follow start on a 4-byte
    # boundary; an unaligned Float32Array cannot be created without copying.
    padding = (-len(header_bytes)) % 4
    header_bytes += b" " * padding

    return b"".join(
        [
            MAGIC,
            struct.pack("<I", len(header_bytes)),
            header_bytes,
            struct.pack(f"<{len(positions)}f", *positions),
            struct.pack(f"<{len(normals)}f", *normals),
            struct.pack(f"<{len(face_ids)}I", *face_ids),
            struct.pack(f"<{len(indices)}I", *indices),
        ]
    )


def build(shape: TopoDS_Shape, labels: Optional[dict] = None) -> Optional[bytes]:
    """Tessellates and packs, returning None if the part cannot be shown.

    A part that will not mesh is a viewer problem, never an analysis problem, so
    every failure here is swallowed and logged. The findings stand on their own.
    """
    try:
        return pack(tessellate(shape), labels)
    except MeshTooLarge as exc:
        logging.warning(f"Skipping the 3D view: {exc}")
    except Exception as exc:
        logging.warning(f"Could not build the 3D view: {type(exc).__name__}: {exc}")
    return None

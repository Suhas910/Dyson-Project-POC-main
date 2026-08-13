"""features.py - Extracts geometric properties from an OCCT shape.

This is Step 2 of the pipeline. It takes the TopoDS_Shape from the ingest step
and builds a `PartModel`: one `PartModelFace` per face, carrying the measured
geometry that the rule engine compares against thresholds.

Three conventions here are load-bearing, and getting any of them wrong silently
inverts rule verdicts:

Outward normals. In a B-rep solid roughly half the faces carry TopAbs_REVERSED
orientation, meaning the underlying surface normal points *into* the material.
Every normal produced here is flipped to face outward, so "inward" always means
the same thing for thickness rays and draft.

Draft angle. Draft is the angle between the wall and the direction the mould
opens, i.e. `90 - angle(outward_normal, pull)`, not the normal-to-pull angle.
A vertical wall -- the zero-draft case that draft rules exist to catch -- is 0
degrees here. A face perpendicular to pull (a flat top) is 90 degrees and is
exempt from draft rules rather than failing them. Each face is measured against
the mould half that actually forms it, so the bottom of a part is not reported
as a 180-degree error.

Sampling. Measurements are taken at a grid of points verified to lie inside the
*trimmed* face. The parametric centre of the underlying surface can fall
outside the face entirely (a face with a hole, or an L-shaped trim), which
would otherwise measure a point that is not on the part.
"""

import logging
import math
from statistics import median
from typing import List

from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepTools import breptools
from OCC.Core.BRepClass import BRepClass_FaceClassifier
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import (
    GeomAbs_Plane,
    GeomAbs_Cylinder,
    GeomAbs_Cone,
    GeomAbs_Sphere,
    GeomAbs_Torus,
)
from OCC.Core.GeomLProp import GeomLProp_SLProps
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Face, TopoDS_Edge, topods
from OCC.Core.TopExp import TopExp_Explorer, topexp
from OCC.Core.gp import gp_Dir, gp_Pnt, gp_Pnt2d, gp_Lin, gp_Vec
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.TopAbs import (
    TopAbs_FACE,
    TopAbs_EDGE,
    TopAbs_SOLID,
    TopAbs_REVERSED,
    TopAbs_IN,
)
from OCC.Core.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCC.Core.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape

import feature_naming
from models import PartModelFace, PartModel


# A face whose draft is within this margin of 90 degrees lies perpendicular to
# the pull direction (a flat top or bottom). Draft is meaningless there, so such
# faces are exempt from minimum-draft rules rather than being failed by them.
PERPENDICULAR_TO_PULL_TOLERANCE_DEG = 1.0

# Angular sweep above which a cylindrical face is treated as a full hole or
# boss rather than a fillet. A blend between two walls typically sweeps 90
# degrees; a drilled hole sweeps the full 360.
FULL_REVOLUTION_TOLERANCE_RAD = math.radians(15.0)

# Grid resolution for per-face sampling. 5x5 gives 25 candidate points, of
# which only those inside the trimmed face are used.
DEFAULT_SAMPLE_GRID = 5

# Upper bound on measurement points per face, so refining the grid on an
# awkward face does not turn into hundreds of ray casts.
MAX_SAMPLES_PER_FACE = 25

# Exact edge-distance solves attempted per hole, after bounding-box ranking.
# The nearest edge is almost always among the first few, and the loop exits
# early once the remaining bounds cannot beat the best result so far.
MAX_EDGE_CANDIDATES = 12


def get_all_faces(shape: TopoDS_Shape) -> List[TopoDS_Face]:
    """Traverses a TopoDS_Shape and returns a list of all its faces."""
    faces = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        faces.append(topods.Face(explorer.Current()))
        explorer.Next()
    logging.info(f"Found {len(faces)} faces in the shape.")
    return faces


def has_analytic_surface(face: TopoDS_Face) -> bool:
    """Whether a face carries an underlying surface that can be measured.

    Tessellated STEP files describe faces as triangle meshes with no analytic
    surface behind them. `BRep_Tool.Surface` returns null for those, and the
    downstream OCCT calls that take a surface do not raise on null -- they
    segfault, taking the whole process down rather than the one face. A Python
    `try` cannot catch that, so such faces must be excluded before they reach
    any measurement, not after.
    """
    try:
        return BRep_Tool.Surface(face) is not None
    except Exception:
        return False


def classify_surface(face: TopoDS_Face) -> str:
    """Names the underlying surface type of a face."""
    try:
        surface_type = BRepAdaptor_Surface(face).GetType()
    except Exception:
        return "unknown"
    return {
        GeomAbs_Plane: "plane",
        GeomAbs_Cylinder: "cylinder",
        GeomAbs_Cone: "cone",
        GeomAbs_Sphere: "sphere",
        GeomAbs_Torus: "torus",
    }.get(surface_type, "freeform")


def _classify_grid(
    face: TopoDS_Face, bounds: tuple[float, float, float, float], grid: int, tolerance: float
) -> list[tuple[float, float]]:
    """Returns the cell-centre points of a grid that lie inside the trimmed face."""
    umin, umax, vmin, vmax = bounds
    inside: list[tuple[float, float]] = []
    for i in range(grid):
        for j in range(grid):
            # Cell centres, so no sample sits exactly on the boundary.
            u = umin + (umax - umin) * (i + 0.5) / grid
            v = vmin + (vmax - vmin) * (j + 0.5) / grid
            try:
                classifier = BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), tolerance)
                if classifier.State() == TopAbs_IN:
                    inside.append((u, v))
            except Exception:
                continue
    return inside


def interior_uv_samples(
    face: TopoDS_Face, grid: int = DEFAULT_SAMPLE_GRID
) -> list[tuple[float, float]]:
    """Returns (u, v) points that provably lie inside the trimmed face.

    `breptools.UVBounds` gives the bounds of the trimmed face, but a rectangular
    grid over those bounds can still land in a trimmed-away region, so every
    candidate is classified before being accepted.

    A narrow face can defeat a coarse grid entirely: the top rim of a shelled
    box is a square annulus a few millimetres wide, and every point of a 5x5
    grid over its bounds falls inside the opening. The grid is therefore
    refined until it finds interior points, so such a face is measured rather
    than skipped. Refinement only costs anything on the faces that need it.
    """
    try:
        umin, umax, vmin, vmax = breptools.UVBounds(face)
    except Exception as exc:
        logging.warning(f"Could not read UV bounds for face: {exc}")
        return []

    if not all(math.isfinite(x) for x in (umin, umax, vmin, vmax)):
        return []

    bounds = (umin, umax, vmin, vmax)
    tolerance = max(BRep_Tool.Tolerance(face), 1e-7)

    for resolution in (grid, grid * 3, grid * 7):
        inside = _classify_grid(face, bounds, resolution, tolerance)
        if inside:
            # Cap the count so a refined grid on a large face does not fire
            # hundreds of rays; an even stride keeps the samples spread out.
            if len(inside) > MAX_SAMPLES_PER_FACE:
                stride = len(inside) / MAX_SAMPLES_PER_FACE
                inside = [
                    inside[int(k * stride)] for k in range(MAX_SAMPLES_PER_FACE)
                ]
            return inside

    return []


def outward_normal_at(face: TopoDS_Face, u: float, v: float) -> gp_Dir | None:
    """Returns the outward-pointing normal of a face at the given parameters.

    The surface normal is flipped when the face carries TopAbs_REVERSED
    orientation, so the result always points out of the material.
    """
    surface = BRep_Tool.Surface(face)
    if surface is None:
        return None

    props = GeomLProp_SLProps(surface, u, v, 1, 1e-6)
    if not props.IsNormalDefined():
        return None

    surface_normal = props.Normal()
    normal = gp_Dir(surface_normal.X(), surface_normal.Y(), surface_normal.Z())
    if face.Orientation() == TopAbs_REVERSED:
        normal.Reverse()
    return normal


def point_at(face: TopoDS_Face, u: float, v: float) -> gp_Pnt | None:
    """Gets the 3D point on a face at the given UV parameters."""
    surface = BRep_Tool.Surface(face)
    if surface is None:
        return None
    point = gp_Pnt()
    surface.D0(u, v, point)
    return point


def mould_half_direction(outward_normal: gp_Dir, pull_direction: gp_Dir) -> gp_Dir:
    """Returns the opening direction of the mould half that forms this face.

    A face is formed by whichever half it faces: an upward-facing face is formed
    by the half that withdraws along +pull, a downward-facing one by the half
    that withdraws along -pull.
    """
    if outward_normal.Dot(pull_direction) >= 0:
        return pull_direction
    return pull_direction.Reversed()


def calculate_draft_angle(outward_normal: gp_Dir, pull_direction: gp_Dir) -> float:
    """Calculates the draft angle of a face, in degrees, from 0 to 90.

    Draft is measured between the face and the direction its own mould half
    withdraws in:

      * 0 degrees  - a wall parallel to the pull direction (zero draft, the
                     condition minimum-draft rules exist to catch).
      * 90 degrees - a face perpendicular to pull (a flat top or bottom), where
                     draft does not apply.

    Args:
        outward_normal: The outward normal of the face (see `outward_normal_at`).
        pull_direction: The mould's pull direction.
    """
    angle_to_pull = math.degrees(outward_normal.Angle(pull_direction))
    # Measure against the mould half that forms this face, so a downward-facing
    # face reports its true draft rather than its supplement.
    if angle_to_pull > 90.0:
        angle_to_pull = 180.0 - angle_to_pull
    return 90.0 - angle_to_pull


def is_face_occluded(
    intersector: IntCurvesFace_ShapeIntersector,
    point: gp_Pnt,
    outward_normal: gp_Dir,
    open_direction: gp_Dir,
    offset: float,
) -> bool:
    """Tests whether material blocks this point from its mould half.

    The ray starts just outside the surface -- otherwise it would immediately
    register a hit on the face it came from -- and is cast along the direction
    the forming mould half withdraws in. Any material along that path means the
    mould cannot be withdrawn without colliding, which is what an undercut is.
    """
    start = gp_Pnt(
        point.X() + outward_normal.X() * offset,
        point.Y() + outward_normal.Y() * offset,
        point.Z() + outward_normal.Z() * offset,
    )
    ray = gp_Lin(start, open_direction)
    intersector.Perform(ray, 0.0, 1e7)
    if not intersector.IsDone():
        return False
    for i in range(1, intersector.NbPnt() + 1):
        if start.Distance(intersector.Pnt(i)) > offset * 2.0:
            return True
    return False


def measure_thickness_at(
    intersector: IntCurvesFace_ShapeIntersector,
    origin_face: TopoDS_Face,
    start_point: gp_Pnt,
    inward_direction: gp_Dir,
) -> float | None:
    """Measures wall thickness by casting a ray into the material.

    The ray is cast from a point on the face along the inward normal, and the
    thickness is the distance to the first face it meets on the far side.

    The parameter range starts at zero: allowing negative parameters would let
    the ray report a hit on geometry *behind* the start point, which is outside
    the material and not a wall thickness at all.

    This is a ray measurement, so on curved or tapered walls it returns a chord
    that is greater than or equal to the true perpendicular thickness. It is
    therefore optimistic near corners -- see the calibration tests for the
    tolerances this holds to on known geometry.
    """
    ray = gp_Lin(start_point, inward_direction)
    intersector.Perform(ray, 1e-7, 1e7)
    if not intersector.IsDone():
        return None

    closest = None
    for i in range(1, intersector.NbPnt() + 1):
        if intersector.Face(i).IsSame(origin_face):
            continue
        distance = start_point.Distance(intersector.Pnt(i))
        if distance > 1e-6 and (closest is None or distance < closest):
            closest = distance
    return closest


def get_free_edges(shape: TopoDS_Shape) -> List[TopoDS_Edge]:
    """Finds edges bounded by only one face.

    These form the open perimeter of a sheet or surface model. A closed solid
    has none by definition, so this returns an empty list for most moulded
    parts and any metric derived from it is reported as unavailable rather
    than guessed.
    """
    free_edges = []
    edge_face_map = TopTools_IndexedDataMapOfShapeListOfShape()
    topexp.MapShapesAndAncestors(shape, TopAbs_EDGE, TopAbs_FACE, edge_face_map)

    for i in range(1, edge_face_map.Size() + 1):
        if edge_face_map.FindFromIndex(i).Size() == 1:
            free_edges.append(topods.Edge(edge_face_map.FindKey(i)))

    logging.info(f"Found {len(free_edges)} free edges in the shape.")
    return free_edges


def _is_closed_solid(shape: TopoDS_Shape) -> bool:
    """True when the shape contains at least one solid."""
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    return explorer.More()


def _bbox_of(shape: TopoDS_Shape) -> Bnd_Box:
    box = Bnd_Box()
    brepbndlib.Add(shape, box)
    return box


def calculate_distance_to_nearest_edge(
    face: TopoDS_Face,
    edges: List[TopoDS_Edge],
    edge_boxes: List[Bnd_Box] | None = None,
    candidates: int = MAX_EDGE_CANDIDATES,
) -> float | None:
    """Minimum distance from a face to the nearest of the given edges.

    The exact solver is expensive, and calling it once per (hole, edge) pair is
    quadratic: one real part carried 159 cylinders and 164 free edges, which is
    26,000 exact distance computations and roughly nine minutes for a single
    file.

    Bounding boxes give a cheap lower bound on the distance to each edge, so
    only the nearest few candidates need the exact solver, and the loop stops
    as soon as the best exact distance is already below every remaining bound.
    """
    if not edges:
        return None

    face_box = _bbox_of(face)
    if edge_boxes is None:
        edge_boxes = [_bbox_of(edge) for edge in edges]

    # Bnd_Box.Distance is a true lower bound on the real distance, so an edge
    # cannot be closer than its box.
    ranked = sorted(
        range(len(edges)),
        key=lambda i: face_box.Distance(edge_boxes[i]),
    )

    min_dist = float("inf")
    for rank, index in enumerate(ranked):
        if rank >= candidates:
            break
        lower_bound = face_box.Distance(edge_boxes[index])
        if lower_bound >= min_dist:
            # Every remaining edge is at least this far away.
            break
        calculator = BRepExtrema_DistShapeShape(face, edges[index])
        calculator.Perform()
        if calculator.IsDone():
            min_dist = min(min_dist, calculator.Value())

    return min_dist if min_dist != float("inf") else None


def calculate_cylindrical_properties(
    face: TopoDS_Face, merged: dict | None = None
) -> dict:
    """Measures a cylindrical face and decides what kind of feature it is.

    The angular sweep of the trimmed face distinguishes the two cases that
    share a cylindrical surface: a drilled hole or a boss sweeps a full circle,
    while a blend between two walls sweeps only part of one (typically a
    quarter). Treating a fillet as a hole would apply hole rules -- depth to
    diameter, edge distance -- to a feature that has none of those properties.

    Returns a dict with radius, diameter, axial depth, angular sweep and the
    derived feature class; empty for non-cylindrical faces.
    """
    if classify_surface(face) != "cylinder":
        return {}

    adaptor = BRepAdaptor_Surface(face)
    cylinder = adaptor.Cylinder()
    radius = cylinder.Radius()

    try:
        umin, umax, vmin, vmax = breptools.UVBounds(face)
    except Exception:
        return {}

    # For a cylindrical surface U is the angle around the axis and V the
    # distance along it, so both dimensions come straight from the trimmed
    # bounds -- no bounding-box approximation needed.
    angular_sweep = abs(umax - umin)
    axial_depth = abs(vmax - vmin)

    # Judge the whole bore, not this one face of it: CAD kernels routinely
    # split a cylinder into halves or quadrants, and each piece alone looks
    # like a partial sweep -- i.e. like a blend fillet rather than a hole.
    if merged:
        angular_sweep = merged["total_sweep"]
        axial_depth = max(axial_depth, merged["axial_depth"])
        is_full_revolution = merged["is_full_revolution"]
    else:
        is_full_revolution = angular_sweep >= (
            2 * math.pi - FULL_REVOLUTION_TOLERANCE_RAD
        )

    # A reversed cylindrical face is concave: material lies outside it, so it is
    # a hole or an internal blend rather than a boss or an external round.
    is_internal = face.Orientation() == TopAbs_REVERSED

    if is_internal:
        feature = "hole" if is_full_revolution else "internal_fillet"
    else:
        feature = "boss" if is_full_revolution else "external_round"

    return {
        "radius": radius,
        "diameter": radius * 2.0,
        "axial_depth": axial_depth,
        "angular_sweep": angular_sweep,
        "is_internal": is_internal,
        "feature": feature,
    }


def cylinder_axis_key(face: TopoDS_Face) -> tuple | None:
    """Identifies the infinite cylinder a face lies on.

    Two faces that are halves of the same bore share an axis line and a radius,
    so rounding those to a tolerance gives a key that groups them together.
    """
    if classify_surface(face) != "cylinder":
        return None
    try:
        cylinder = BRepAdaptor_Surface(face).Cylinder()
    except Exception:
        return None

    axis = cylinder.Axis()
    location = axis.Location()
    direction = axis.Direction()

    # A cylinder's axis has no inherent sense, so faces on the same bore can
    # report opposite directions. Normalising the sign keeps them in one group.
    dx, dy, dz = direction.X(), direction.Y(), direction.Z()
    for component in (dz, dy, dx):
        if abs(component) > 1e-9:
            if component < 0:
                dx, dy, dz = -dx, -dy, -dz
            break

    return (
        round(cylinder.Radius(), 4),
        round(dx, 4), round(dy, 4), round(dz, 4),
        round(location.X(), 3), round(location.Y(), 3), round(location.Z(), 3),
    )


def group_cylindrical_faces(faces: List[TopoDS_Face]) -> dict[int, dict]:
    """Sums the angular sweep of cylindrical faces that share an axis.

    A drilled hole is one feature, but CAD kernels rarely export it as one
    face: most split the cylinder at its seam into two halves, and some into
    quadrants. Judging "is this a full revolution?" per face therefore
    classifies real holes and bosses as blend fillets -- which then get
    measured against fillet rules and never against hole rules.

    Synthetic primitives hide this, because a cylinder built by the modelling
    kernel comes back as a single seamed face. It only appears on CAD exports.

    Returns a map from face index to the merged properties of its group.
    """
    groups: dict[tuple, list[int]] = {}
    sweeps: dict[int, float] = {}
    extents: dict[int, float] = {}

    for index, face in enumerate(faces):
        if not has_analytic_surface(face):
            continue
        key = cylinder_axis_key(face)
        if key is None:
            continue
        try:
            umin, umax, vmin, vmax = breptools.UVBounds(face)
        except Exception:
            continue
        sweeps[index] = abs(umax - umin)
        extents[index] = abs(vmax - vmin)
        groups.setdefault(key, []).append(index)

    merged: dict[int, dict] = {}
    for key, members in groups.items():
        total_sweep = sum(sweeps.get(i, 0.0) for i in members)
        merged_depth = max((extents.get(i, 0.0) for i in members), default=0.0)
        is_full = total_sweep >= (2 * math.pi - FULL_REVOLUTION_TOLERANCE_RAD)
        for i in members:
            merged[i] = {
                "radius": key[0],
                "total_sweep": total_sweep,
                "is_full_revolution": is_full,
                "axial_depth": merged_depth,
                "face_count": len(members),
            }
    return merged


def _face_area(face: TopoDS_Face) -> float | None:
    try:
        props = GProp_GProps()
        brepgprop.SurfaceProperties(face, props)
        return props.Mass()
    except Exception:
        return None


def _face_centroid(face: TopoDS_Face) -> tuple[float, float, float] | None:
    try:
        props = GProp_GProps()
        brepgprop.SurfaceProperties(face, props)
        centre = props.CentreOfMass()
        return (centre.X(), centre.Y(), centre.Z())
    except Exception:
        return None


def _shape_diagonal(shape: TopoDS_Shape) -> float:
    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    if bbox.IsVoid():
        return 1.0
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    diagonal = math.sqrt(
        (xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2
    )
    return diagonal if diagonal > 0 else 1.0


def build_part_model(
    shape: TopoDS_Shape,
    pull_direction: gp_Dir = gp_Dir(0, 0, 1),
    detect_undercuts: bool = True,
) -> PartModel:
    """Builds the PartModel by measuring every face of the shape.

    Args:
        shape: The TopoDS_Shape to analyse.
        pull_direction: The mould's pull direction, used for draft and undercuts.
        detect_undercuts: Run the occlusion test. An undercut is a moulding and
            casting concern -- it asks whether a tool half can withdraw -- so on
            a machined or sheet-metal part the answer is computed, reported to
            nobody, and paid for in ray casts. Off, the whole pass is skipped.
    """
    all_faces = get_all_faces(shape)

    # Hole-to-edge distance measures how close a hole sits to the boundary of
    # an open sheet. A closed solid has no boundary in that sense, so the
    # metric is meaningless there -- and healing can leave spurious free edges
    # on a solid, which would make it both meaningless and very expensive.
    free_edges = [] if _is_closed_solid(shape) else get_free_edges(shape)
    free_edge_boxes = [_bbox_of(edge) for edge in free_edges]

    # Computed once for the whole shape, because deciding whether a cylindrical
    # face belongs to a full bore needs every other face on the same axis.
    merged_cylinders = group_cylindrical_faces(all_faces)

    # Loading the intersector is expensive, so it is built once and reused for
    # every thickness ray and occlusion test on this shape.
    intersector = IntCurvesFace_ShapeIntersector()
    intersector.Load(shape, 1e-6)

    # Rays for the undercut test start slightly off the surface to avoid
    # registering a hit on their own face; the offset scales with the part so it
    # is meaningful for both a 5 mm clip and a 500 mm housing.
    ray_offset = max(1e-6, _shape_diagonal(shape) * 1e-5)

    faces: List[PartModelFace] = []
    unmeasurable = 0

    for index, face in enumerate(all_faces):
        face_id = index + 1

        # Faces with no analytic surface (tessellated imports) are recorded so
        # the face count still matches the model, but nothing is measured on
        # them -- every measurement below would be meaningless, and some would
        # crash the process outright.
        if not has_analytic_surface(face):
            unmeasurable += 1
            faces.append(
                PartModelFace(
                    face_id=face_id, surface_type="tessellated",
                    feature_class="tessellated", sample_count=0,
                )
            )
            continue

        surface_type = classify_surface(face)
        samples = interior_uv_samples(face)

        draft_angles: list[float] = []
        thicknesses: list[float] = []
        occluded_samples = 0
        representative_normal: tuple[float, float, float] | None = None

        for u, v in samples:
            normal = outward_normal_at(face, u, v)
            point = point_at(face, u, v)
            if normal is None or point is None:
                continue

            if representative_normal is None:
                representative_normal = (
                    float(normal.X()),
                    float(normal.Y()),
                    float(normal.Z()),
                )

            draft_angles.append(calculate_draft_angle(normal, pull_direction))

            thickness = measure_thickness_at(
                intersector, face, point, normal.Reversed()
            )
            if thickness is not None:
                thicknesses.append(thickness)

            # Undercut is a boolean: one blocked sample settles it. Testing the
            # rest costs a ray cast each and cannot change the answer, and on a
            # 664-face part that is thousands of wasted intersections.
            if detect_undercuts and occluded_samples == 0:
                if is_face_occluded(
                    intersector,
                    point,
                    normal,
                    mould_half_direction(normal, pull_direction),
                    ray_offset,
                ):
                    occluded_samples += 1

        # Worst case drives the verdict: a wall is too thin if it is too thin
        # anywhere, and a face lacks draft if any part of it does.
        wall_thickness = min(thicknesses) if thicknesses else None
        wall_thickness_max = max(thicknesses) if thicknesses else None
        wall_thickness_median = median(thicknesses) if thicknesses else None
        draft_angle = min(draft_angles) if draft_angles else None

        is_perpendicular_to_pull = (
            draft_angle is not None
            and draft_angle >= 90.0 - PERPENDICULAR_TO_PULL_TOLERANCE_DEG
        )
        is_undercut = occluded_samples > 0

        cylinder = calculate_cylindrical_properties(face, merged_cylinders.get(index))
        feature = cylinder.get("feature")
        internal_radius = cylinder.get("radius") if cylinder.get("is_internal") else None
        external_radius = (
            cylinder.get("radius") if cylinder.get("is_internal") is False else None
        )
        hole_diameter = cylinder.get("diameter") if feature == "hole" else None
        hole_depth = cylinder.get("axial_depth") if feature == "hole" else None

        # Only genuine holes are measured to the open boundary; an internal
        # blend has no meaningful edge distance.
        hole_to_edge_distance = None
        if feature == "hole" and free_edges:
            hole_to_edge_distance = calculate_distance_to_nearest_edge(
                face, free_edges, free_edge_boxes
            )

        internal_radius_ratio = None
        external_radius_ratio = None
        if wall_thickness and wall_thickness > 1e-6:
            if internal_radius:
                internal_radius_ratio = internal_radius / wall_thickness
            if external_radius:
                external_radius_ratio = external_radius / wall_thickness

        hole_depth_to_diameter_ratio = None
        if hole_diameter and hole_diameter > 1e-6 and hole_depth:
            hole_depth_to_diameter_ratio = hole_depth / hole_diameter

        radius_to_depth_ratio = None
        if internal_radius and hole_depth and hole_depth > 1e-6:
            radius_to_depth_ratio = internal_radius / hole_depth

        faces.append(
            PartModelFace(
                face_id=face_id,
                surface_type=surface_type,
                feature_class=feature or surface_type,
                face_normal=representative_normal,
                face_area=_face_area(face),
                face_centroid=_face_centroid(face),
                sample_count=len(samples),
                wall_thickness=wall_thickness,
                wall_thickness_max=wall_thickness_max,
                wall_thickness_median=wall_thickness_median,
                draft_angle=draft_angle,
                is_perpendicular_to_pull=is_perpendicular_to_pull,
                is_undercut=is_undercut,
                hole_diameter=hole_diameter,
                hole_depth=hole_depth,
                internal_radius=internal_radius,
                external_radius=external_radius,
                internal_radius_ratio=internal_radius_ratio,
                external_radius_ratio=external_radius_ratio,
                hole_to_edge_distance=hole_to_edge_distance,
                hole_depth_to_diameter_ratio=hole_depth_to_diameter_ratio,
                radius_to_depth_ratio=radius_to_depth_ratio,
            )
        )

    if unmeasurable:
        logging.warning(
            f"{unmeasurable} of {len(all_faces)} faces carry no analytic surface "
            "(tessellated geometry); they were skipped rather than measured."
        )

    unmeasured = [f.face_id for f in faces if f.sample_count == 0]
    if unmeasured:
        logging.warning(
            f"{len(unmeasured)} face(s) yielded no interior sample point and "
            f"were not measured: {unmeasured[:10]}"
        )

    # Naming happens last because a name is only unique once every face is
    # known: whether "Top face" needs a position appended depends on whether any
    # other face would answer to the same description.
    bounding_box = None
    box = _bbox_of(shape)
    if not box.IsVoid():
        bounding_box = box.Get()

    labels = feature_naming.build_labels(faces, bounding_box)
    for face in faces:
        label = labels.get(face.face_id)
        if label:
            face.label = label.text
            face.label_kind = label.kind

    return PartModel(faces=faces)

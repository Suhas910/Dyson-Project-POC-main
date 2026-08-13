"""feature_naming.py - Turns face numbers into descriptions an engineer recognises.

"Face 214 fails minimum draft" is a true statement that nobody can act on. The
same finding against "the Ø5.00 mm hole, front left" is one a design engineer
can locate in their own CAD session without opening ours.

Everything here is derived from measurements the geometry engine already took --
surface type, orientation, radius, centroid. Nothing is guessed and nothing is
asked of a language model, because a confidently wrong name is worse than a
number: a number is honestly uninformative, whereas "the boss near the parting
line" sends somebody to the wrong feature entirely.

Two rules keep the names trustworthy:

  * Only say what was measured. There is no "near the parting line" here,
    because the parting line is not computed. Position is stated against the
    bounding box, which is.
  * Labels are unique within a part. A name that matches two features is a name
    that cannot be used to point at either, so position is added when a base
    name collides, and the face number is appended if that still is not enough.
"""

from dataclasses import dataclass
from typing import Optional, Sequence

# Below this share of the part's largest dimension a planar face is trim rather
# than structure -- the flat left by a chamfer, the top of a rib. Calling those
# "walls" would bury the actual walls in a list of near-identical names.
SMALL_FACE_FRACTION = 0.06

# A face is treated as sitting at one end of an axis rather than in the middle
# when its centroid is outside the central 40% of the part along that axis.
# Tighter than a third, so "front" means recognisably towards the front.
EDGE_BAND = 0.30


@dataclass(frozen=True)
class FeatureLabel:
    """A face's human-readable identity."""

    face_id: int
    text: str
    # The bare feature kind, without size or position. Useful for grouping in
    # the report, where "3 holes" is more use than three separate rows.
    kind: str


def _format_mm(value: float) -> str:
    """Two decimals under 10 mm, one above.

    A 4.76 mm hole and a 4.78 mm hole are different holes; a 120.4 mm wall and a
    120.44 mm wall are the same wall described with false precision.

    The rounding happens before the threshold test, not after. A radius that
    comes back as 9.9994 would otherwise print as "9.99" while its neighbour at
    10.0001 printed as "10.0", splitting one nominal R10 blend into two
    different-looking features.
    """
    rounded = round(value, 2)
    return f"{rounded:.2f}" if rounded < 10 else f"{rounded:.1f}"


def _axis_position(value: float, low: float, high: float, names: tuple[str, str]) -> Optional[str]:
    """Where a coordinate sits along one axis of the bounding box."""
    span = high - low
    if span <= 1e-9:
        return None
    fraction = (value - low) / span
    if fraction < EDGE_BAND:
        return names[0]
    if fraction > 1.0 - EDGE_BAND:
        return names[1]
    return None


def describe_position(
    centroid: Optional[Sequence[float]], bbox: Optional[Sequence[float]]
) -> Optional[str]:
    """Plain-language position of a point within the part's bounding box.

    Deliberately coarse. "Front left" is checkable by eye against a model on
    screen; "at x=41.2" is not, and a position precise enough to be wrong is
    worse than one broad enough to be reliable.
    """
    if not centroid or not bbox or len(bbox) != 6:
        return None

    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    parts = [
        _axis_position(centroid[2], zmin, zmax, ("lower", "upper")),
        _axis_position(centroid[1], ymin, ymax, ("front", "rear")),
        _axis_position(centroid[0], xmin, xmax, ("left", "right")),
    ]
    words = [p for p in parts if p]
    return " ".join(words) if words else "centre"


def _height_fraction(face, bounding_box: Optional[Sequence[float]]) -> Optional[float]:
    """Where the face sits between the bottom and top of the part, 0 to 1."""
    if not bounding_box or len(bounding_box) != 6 or not face.face_centroid:
        return None
    zmin, zmax = bounding_box[2], bounding_box[5]
    if zmax - zmin <= 1e-9:
        return None
    return (face.face_centroid[2] - zmin) / (zmax - zmin)


def _planar_kind(face, largest_dimension: float, height: Optional[float]) -> str:
    """What a flat face is, from its orientation and where it sits.

    A face perpendicular to the pull direction is not automatically the top of
    the part: an upward-facing flat down inside a pocket is a floor, and calling
    it "top face, lower" would be both contradictory and wrong. Height decides
    between the two, and height is measured.
    """
    area = face.face_area or 0.0
    # Comparing a length against an area needs the area's square root; a face
    # 6% of the part's size in each direction is 0.36% of its area.
    is_small = (
        largest_dimension > 0
        and area > 0
        and (area ** 0.5) < largest_dimension * SMALL_FACE_FRACTION
    )

    if face.is_perpendicular_to_pull:
        normal = face.face_normal
        # Without a height the safe answer is the orientation alone; claiming
        # "top" for something that might be a pocket floor is the error worth
        # avoiding.
        if normal and normal[2] > 0:
            if height is None:
                return "upward-facing flat"
            return "top face" if height > 1.0 - EDGE_BAND else "floor"
        if normal and normal[2] < 0:
            if height is None:
                return "downward-facing flat"
            return "base" if height < EDGE_BAND else "underside"
        return "flat face"

    if is_small:
        return "small flat"
    return "side wall"


def _kind_and_size(
    face, largest_dimension: float, height: Optional[float] = None
) -> tuple[str, Optional[str]]:
    """The feature a face belongs to, and the dimension worth naming it by."""
    feature = face.feature_class or face.surface_type or "face"

    if feature == "hole" and face.hole_diameter:
        return "hole", f"Ø{_format_mm(face.hole_diameter)} mm"
    if feature == "hole":
        return "hole", None

    if feature == "boss":
        size = f"Ø{_format_mm(face.external_radius * 2)} mm" if face.external_radius else None
        return "boss", size

    if feature == "internal_fillet":
        size = f"R{_format_mm(face.internal_radius)} mm" if face.internal_radius else None
        return "internal fillet", size

    if feature == "external_round":
        size = f"R{_format_mm(face.external_radius)} mm" if face.external_radius else None
        return "external round", size

    if feature == "tessellated":
        # Honest about the one case where nothing could be measured.
        return "unmeasured face", None

    if feature == "cone":
        # A cone's half-angle is not carried on the face model, so the split
        # between chamfer and tapered wall is made on size alone: a break-edge
        # is small, a drafted wall is not.
        area = face.face_area or 0.0
        if (
            largest_dimension > 0
            and area > 0
            and (area ** 0.5) < largest_dimension * SMALL_FACE_FRACTION
        ):
            return "chamfer", None
        return "tapered wall", None

    if feature == "sphere":
        return "spherical face", None
    if feature == "torus":
        return "toroidal blend", None
    if feature == "freeform":
        return "freeform surface", None
    if feature in ("plane", "flat"):
        return _planar_kind(face, largest_dimension, height), None
    if feature == "cylinder":
        # A cylindrical face that reached neither the hole nor the fillet test.
        return "cylindrical face", None

    return feature.replace("_", " "), None


def _feature_scale(face, part_diagonal: float) -> float:
    """How far apart two faces of the same feature can reasonably sit.

    A CAD kernel routinely splits one bore into two half-cylinders whose
    centroids lie a full diameter apart, on opposite sides of the axis. Anything
    within the feature's own size is treated as part of it.
    """
    if face.hole_diameter:
        span = face.hole_diameter
    elif face.internal_radius:
        span = face.internal_radius * 2
    elif face.external_radius:
        span = face.external_radius * 2
    else:
        span = 0.0
    # A floor for features with no characteristic size, and a guard against a
    # feature so large that everything on the part clusters into it.
    return min(max(span * 1.2, part_diagonal * 0.02), part_diagonal * 0.25)


def _cluster(faces, part_diagonal: float) -> list[list]:
    """Single-linkage grouping of faces by centroid proximity.

    Used only to decide whether two faces sharing a description are one feature
    described twice, or two features that happen to look alike. Groups are small
    -- the faces that collide on a name -- so the quadratic pass is cheap.
    """
    clusters: list[list] = []
    for face in faces:
        if not face.face_centroid:
            clusters.append([face])
            continue

        reach = _feature_scale(face, part_diagonal)
        joined = None
        for cluster in clusters:
            for other in cluster:
                if not other.face_centroid:
                    continue
                distance = sum(
                    (face.face_centroid[i] - other.face_centroid[i]) ** 2
                    for i in range(3)
                ) ** 0.5
                if distance <= max(reach, _feature_scale(other, part_diagonal)):
                    joined = cluster
                    break
            if joined:
                break

        if joined is None:
            clusters.append([face])
        else:
            joined.append(face)
    return clusters


def _compose(size: Optional[str], kind: str, position: Optional[str]) -> str:
    head = f"{size} {kind}" if size else kind
    text = f"{head}, {position}" if position else head
    return text[0].upper() + text[1:]


def build_labels(faces, bounding_box: Optional[Sequence[float]] = None) -> dict:
    """Names every face, guaranteeing each name identifies exactly one feature.

    The guarantee is per *feature*, not per face, and the difference matters. A
    CAD kernel splits one bore into two half-cylinders; both are the same hole
    and both should carry the same name. Handing them different names would
    invent a second hole that does not exist. The face number remains the
    machine key and is displayed alongside, so nothing is lost.

    Args:
        faces: the `PartModelFace` records from the geometry engine.
        bounding_box: (xmin, ymin, zmin, xmax, ymax, zmax) in millimetres. Names
            are still produced without it, just without position.

    Returns:
        face_id -> FeatureLabel.
    """
    largest_dimension = 0.0
    part_diagonal = 0.0
    if bounding_box and len(bounding_box) == 6:
        xmin, ymin, zmin, xmax, ymax, zmax = bounding_box
        spans = (xmax - xmin, ymax - ymin, zmax - zmin)
        largest_dimension = max(spans)
        part_diagonal = sum(s * s for s in spans) ** 0.5

    described = []
    for face in faces:
        kind, size = _kind_and_size(
            face, largest_dimension, _height_fraction(face, bounding_box)
        )
        position = describe_position(face.face_centroid, bounding_box)
        described.append((face, kind, size, position))

    # Position is only worth stating where the plain name is ambiguous. On a
    # simple box "Top face" is exact and "Top face, upper centre" is noise; on a
    # plate with four identical holes, position is the only thing separating
    # them.
    base_counts: dict[str, int] = {}
    for _, kind, size, _ in described:
        base = _compose(size, kind, None)
        base_counts[base] = base_counts.get(base, 0) + 1

    # Faces that still share a description after position is added: either two
    # halves of one feature, or two genuinely separate features that look alike.
    # Proximity tells them apart.
    by_text: dict[str, list] = {}
    resolved: list[tuple] = []
    for face, kind, size, position in described:
        base = _compose(size, kind, None)
        text = _compose(size, kind, position) if base_counts[base] > 1 else base
        by_text.setdefault(text, []).append(face)
        resolved.append((face, kind, text))

    # face_id -> the suffix it needs, if any.
    suffixes: dict[int, str] = {}
    for text, group in by_text.items():
        if len(group) < 2:
            continue
        clusters = _cluster(group, part_diagonal or 1.0)
        if len(clusters) < 2:
            # One feature, described by several faces. They keep one name.
            continue
        for cluster in clusters:
            # The lowest face number in the cluster names the whole feature, so
            # both halves of a bore read identically and the name still resolves
            # to one thing.
            representative = min(f.face_id for f in cluster)
            for face in cluster:
                suffixes[face.face_id] = f" (face {representative})"

    labels: dict[int, FeatureLabel] = {}
    for face, kind, text in resolved:
        labels[face.face_id] = FeatureLabel(
            face_id=face.face_id,
            text=text + suffixes.get(face.face_id, ""),
            kind=kind,
        )

    return labels


def inventory(faces) -> list[dict]:
    """Counts of each kind of feature on the part, most numerous first.

    Gives the report and the model prompt a one-line description of the part --
    "12 holes, 8 internal fillets, 6 side walls" -- which is roughly what an
    engineer would say first when handed it.
    """
    counts: dict[str, int] = {}
    for face in faces:
        if face.label_kind:
            counts[face.label_kind] = counts.get(face.label_kind, 0) + 1
    return [
        {"kind": kind, "count": count}
        for kind, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

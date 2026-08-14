from typing import Optional, Literal, List
from pydantic import BaseModel, Field


# A finding's status says what the engine was able to establish, and the five
# cases are deliberately distinct. Collapsing them (as an earlier version did,
# by reporting everything unmeasurable as REVIEW) buries the handful of findings
# that need an engineer's judgement under thousands that simply were not
# evaluated, and makes the tool look far less certain than it is.
FindingStatus = Literal[
    "COMPLIANT",  # measured, and it satisfies the rule
    "NON-COMPLIANT",  # measured, and it violates the rule
    "NEEDS_REVIEW",  # requires human or AI judgement; no numeric test exists
    "NOT_EVALUATED",  # rule does not apply here, or the metric is not computable
    "ERROR",  # evaluation was attempted and failed
]


class PartModelFace(BaseModel):
    """One face of the part, with everything measured about it."""

    face_id: int

    # --- Identity and classification ---
    surface_type: Optional[str] = Field(
        None, description="Underlying surface: plane, cylinder, cone, sphere, torus."
    )
    feature_class: Optional[str] = Field(
        None,
        description="What the face represents: hole, boss, internal_fillet, plane.",
    )
    face_normal: Optional[tuple[float, float, float]] = Field(
        None, description="Outward normal at the representative sample point."
    )
    face_area: Optional[float] = None
    face_centroid: Optional[tuple[float, float, float]] = Field(
        None, description="Area centroid; used to match faces between revisions."
    )
    sample_count: int = Field(
        0, description="Interior points measured. Zero means the face was not measured."
    )
    label: Optional[str] = Field(
        None,
        description="Human-readable identity of the feature this face belongs to, "
        'e.g. "Ø5.00 mm hole, front left". Derived from measurements only, and '
        "unique within the part so it can be used to point at one face.",
    )
    label_kind: Optional[str] = Field(
        None,
        description='The bare feature kind behind `label` -- "hole", "side wall", '
        '"internal fillet". Kept separate so features can be counted and grouped '
        "without parsing the display text back apart.",
    )

    # --- Wall thickness ---
    # The primary value is the thinnest measurement on the face, since a wall is
    # too thin if it is too thin anywhere. The max is kept so range rules can
    # test both ends of the interval.
    wall_thickness: Optional[float] = None
    wall_thickness_max: Optional[float] = None
    wall_thickness_median: Optional[float] = None

    # --- Mould geometry ---
    draft_angle: Optional[float] = Field(
        None, description="Draft in degrees, 0 (vertical wall) to 90 (perpendicular)."
    )
    # ── NEW: companion coordinates for critical measurements ──────────
    wall_thickness_point: Optional[tuple[float, float, float]] = Field(
        None,
        description="XYZ of the thinnest sample. The point that produced wall_thickness.",
    )
    wall_thickness_max_point: Optional[tuple[float, float, float]] = Field(
        None,
        description="XYZ of the thickest sample. Used when a range rule tests the upper bound.",
    )
    draft_angle_point: Optional[tuple[float, float, float]] = Field(
        None,
        description="XYZ of the shallowest draft sample. The point that produced draft_angle.",
    )

    is_perpendicular_to_pull: bool = Field(
        False, description="Flat top or bottom face; exempt from draft rules."
    )
    is_undercut: bool = Field(
        False, description="Occluded from its mould half; needs a side action."
    )

    # --- Holes, bosses and blends ---
    hole_diameter: Optional[float] = None
    hole_depth: Optional[float] = None
    internal_radius: Optional[float] = None
    external_radius: Optional[float] = None
    internal_radius_ratio: Optional[float] = None
    external_radius_ratio: Optional[float] = None
    hole_to_edge_distance: Optional[float] = None
    hole_depth_to_diameter_ratio: Optional[float] = None
    radius_to_depth_ratio: Optional[float] = None


class PartModel(BaseModel):
    """The analysed part: its faces and any part-level metrics."""

    faces: List[PartModelFace]

    # --- Assembly & Serviceability Metrics (Placeholders) ---
    # These are assembly-level metrics requiring a product structure rather than
    # a single part, and are placeholders for future DFA expansion.
    part_count: Optional[int] = Field(
        None, description="Total number of parts in the assembly."
    )
    fastener_count: Optional[int] = Field(
        None, description="Total number of discrete fasteners."
    )
    insertion_clearance_mm: Optional[float] = Field(
        None, description="Minimum clearance during part insertion."
    )
    assembly_tool_clearance: Optional[float] = Field(
        None, description="Minimum clearance for assembly tooling."
    )

    @property
    def undercut_face_ids(self) -> List[int]:
        return [face.face_id for face in self.faces if face.is_undercut]


class Finding(BaseModel):
    rule_id: str
    rule_name: str
    guideline_ref: str
    status: FindingStatus
    process_family: Optional[str] = Field(
        None,
        description="Which process family's catalogue produced this finding. Set "
        "on every finding once a part can be analysed against several families at "
        "once, so a composite report can be split back apart.",
    )
    location: str  # e.g. "face 123", or "part" for part-level rules
    feature_label: Optional[str] = Field(
        None,
        description="What `location` refers to in engineering terms. Carried "
        "alongside the face number rather than replacing it: the number stays "
        "the stable key that the 3D view and any external tool can match on.",
    )
    measured: Optional[str] = None
    measurement_point: Optional[tuple[float, float, float]] = Field(
        None,
        description=(
            "XYZ coordinate of the sample that produced the measured value. "
            "Null for boolean flags (is_undercut), part-level rules, "
            "and metrics like hole_diameter that have no single critical point."
        ),
    )

    severity: Optional[str] = Field(
        None, description="Rule severity from the catalog: critical, major, minor."
    )
    category: Optional[str] = None
    reason: Optional[str] = Field(
        None,
        description="Why a finding is NOT_EVALUATED or ERROR, so gaps in coverage "
        "are explainable rather than silent.",
    )

    agent_commentary: Optional[str] = None
    agent_confidence: Optional[float] = None


class RuleCoverage(BaseModel):
    """How much of the catalog the engine could actually test on this part.

    Reported alongside the findings so the coverage of an analysis is explicit:
    a report that silently omits two thirds of its rules looks more complete
    than it is.
    """

    rules_in_family: int = 0
    rules_after_material_filter: int = 0
    rules_evaluated: int = 0
    rules_needing_review: int = 0
    rules_not_computable: int = 0
    rules_not_applicable_to_geometry: int = 0
    unmapped_metrics: List[str] = Field(default_factory=list)

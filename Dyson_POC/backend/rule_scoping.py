"""rule_scoping.py - Decides which rules apply, and to what.

The rule catalog names ~200 distinct metrics; the feature extractor computes a
much smaller set. Three questions have to be answered before a rule can produce
a verdict, and this module owns all three:

1. Is the metric computable?  An explicit registry maps catalog metric names to
   measured face attributes. An earlier version matched metric names by
   substring (`"depth" in metric_key`), which is order-dependent and silently
   mis-maps new metrics -- `thread_depth` would have been measured as
   `hole_depth`. Unmapped metrics are reported as NOT_EVALUATED with a reason
   rather than guessed at.

2. Does the rule apply to this material?  Wall-thickness limits for ABS, PC and
   PP are alternatives, not a set. Evaluating all of them at once lets the same
   wall be COMPLIANT and NON-COMPLIANT in one report.

3. Does the rule apply to this face?  A hole rule against a flat wall, or a
   draft rule against a face perpendicular to the pull direction, is not a
   finding -- it is a question that was never asked.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from models import PartModelFace


# --- Metric registry -------------------------------------------------------
#
# `reference` handles rules expressed as a multiple of another dimension. The
# catalog's CSV source wrote these as "1xT" or ">=0.33x depth"; the conversion
# to JSON kept the number and dropped what it multiplied, turning "at least one
# material thickness" into "at least 1.0 mm". Rules whose reference we can
# reconstruct are evaluated as ratios; the rest are refused rather than
# evaluated against the wrong scale.


@dataclass(frozen=True)
class MetricSpec:
    """How to measure one catalog metric against a face."""

    attribute: str
    label: str
    # Feature classes this metric is meaningful for; empty means any face.
    applies_to: frozenset[str] = frozenset()
    # Second attribute for interval metrics, so a range rule can test both ends.
    interval_max_attribute: Optional[str] = None
    # Divide the measurement by this part-level reference before comparing.
    reference: Optional[str] = None
    # Boolean presence flags compare against "is this true" rather than a number.
    is_boolean: bool = False
    # Guidance rather than a limit. The catalog mixes hard constraints with
    # typical-practice ranges, and the CSV conversion recorded both as plain
    # ranges. Failing a design for carrying *more* draft than the typical
    # 1-2 degrees would be wrong, so advisory rules can raise a question but
    # never a non-compliance.
    is_advisory: bool = False
    unit: str = "mm"


_ANY_FACE: frozenset[str] = frozenset()
_WALLS = frozenset({"plane", "cone", "freeform", "cylinder", "boss", "torus", "sphere"})
_HOLES = frozenset({"hole"})
_INTERNAL_BLENDS = frozenset({"internal_fillet"})
_EXTERNAL_BLENDS = frozenset({"external_round"})


METRIC_REGISTRY: dict[str, MetricSpec] = {
    # --- Wall thickness ---
    "wall_thickness_range": MetricSpec(
        "wall_thickness",
        "Wall thickness",
        applies_to=_WALLS,
        interval_max_attribute="wall_thickness_max",
    ),
    "wall_thickness_min": MetricSpec(
        "wall_thickness", "Minimum wall thickness", applies_to=_WALLS
    ),
    "wall_thickness_max": MetricSpec(
        "wall_thickness_max", "Maximum wall thickness", applies_to=_WALLS
    ),
    "wall_thickness_recommended": MetricSpec(
        "wall_thickness",
        "Recommended wall thickness",
        applies_to=_WALLS,
        interval_max_attribute="wall_thickness_max",
        is_advisory=True,
    ),
    "wall_thickness_optimum": MetricSpec(
        "wall_thickness",
        "Optimum wall thickness",
        applies_to=_WALLS,
        interval_max_attribute="wall_thickness_max",
        is_advisory=True,
    ),
    "wall_thickness_min_metal": MetricSpec(
        "wall_thickness", "Minimum wall thickness (metal)", applies_to=_WALLS
    ),
    "wall_thickness_min_plastic": MetricSpec(
        "wall_thickness", "Minimum wall thickness (plastic)", applies_to=_WALLS
    ),
    # --- Draft ---
    # Faces perpendicular to the pull direction are filtered out separately, in
    # `metric_applies_to_face`, since draft has no meaning there.
    "draft_angle_min": MetricSpec(
        "draft_angle", "Draft angle", applies_to=_WALLS, unit="deg"
    ),
    # "Typical" practice is a recommendation: a wall with more draft than the
    # typical range is better, not worse.
    "draft_angle_typical": MetricSpec(
        "draft_angle",
        "Draft angle (typical practice)",
        applies_to=_WALLS,
        unit="deg",
        is_advisory=True,
    ),
    "draft_angle_straight_wall": MetricSpec(
        "draft_angle", "Straight-wall draft", applies_to=_WALLS, unit="deg"
    ),
    # Deliberately absent: draft_angle_textured_min, draft_angle_shutoff_min,
    # draft_angle_deep_feature, draft_angle_min_internal/_external. Each applies
    # to a subset of faces we cannot identify from geometry alone -- surface
    # finish and shutoff faces are tooling decisions, not shape. Applying them
    # to every wall would fail correct designs, so they are reported as
    # not evaluated until the extractor can tell those faces apart.
    "draft_angle_boss_min": MetricSpec(
        "draft_angle",
        "Boss draft angle",
        applies_to=frozenset({"boss"}),
        unit="deg",
    ),
    "boss_draft_min": MetricSpec(
        "draft_angle",
        "Boss draft angle",
        applies_to=frozenset({"boss"}),
        unit="deg",
    ),
    # --- Undercuts ---
    # The catalog classes these as qualitative because no earlier version could
    # compute them. Ray-occlusion testing against each face's own mould half
    # makes presence a deterministic result.
    "undercut_presence": MetricSpec(
        "is_undercut", "Undercut present", is_boolean=True, unit=""
    ),
    # --- Corners, fillets and bend radii ---
    "internal_radius_ratio_min": MetricSpec(
        "internal_radius_ratio",
        "Internal radius / wall thickness",
        applies_to=_INTERNAL_BLENDS,
        unit="ratio",
    ),
    "external_radius_ratio": MetricSpec(
        "external_radius_ratio",
        "External radius / wall thickness",
        applies_to=_EXTERNAL_BLENDS,
        unit="ratio",
    ),
    "internal_fillet_radius_min": MetricSpec(
        "internal_radius", "Internal fillet radius", applies_to=_INTERNAL_BLENDS
    ),
    "external_fillet_radius_min": MetricSpec(
        "external_radius", "External fillet radius", applies_to=_EXTERNAL_BLENDS
    ),
    "fillet_radius_min": MetricSpec(
        "internal_radius", "Fillet radius", applies_to=_INTERNAL_BLENDS
    ),
    # "1xT" -- a multiple of material thickness, not an absolute millimetre value.
    "bend_radius_min": MetricSpec(
        "internal_radius",
        "Inside bend radius / material thickness",
        applies_to=_INTERNAL_BLENDS,
        reference="nominal_thickness",
        unit="ratio",
    ),
    "internal_corner_radius_min": MetricSpec(
        "radius_to_depth_ratio",
        "Corner radius / pocket depth",
        applies_to=_INTERNAL_BLENDS,
        unit="ratio",
    ),
    # --- Holes ---
    "hole_diameter_min": MetricSpec(
        "hole_diameter", "Hole diameter", applies_to=_HOLES
    ),
    "boss_bore_diameter": MetricSpec(
        "hole_diameter", "Boss bore diameter", applies_to=_HOLES
    ),
    "blind_hole_depth_to_diameter_max": MetricSpec(
        "hole_depth_to_diameter_ratio",
        "Hole depth / diameter",
        applies_to=_HOLES,
        unit="ratio",
    ),
    "through_hole_depth_to_diameter_max": MetricSpec(
        "hole_depth_to_diameter_ratio",
        "Hole depth / diameter",
        applies_to=_HOLES,
        unit="ratio",
    ),
    "hole_depth_to_diameter_max": MetricSpec(
        "hole_depth_to_diameter_ratio",
        "Hole depth / diameter",
        applies_to=_HOLES,
        unit="ratio",
    ),
    "hole_aspect_ratio_max": MetricSpec(
        "hole_depth_to_diameter_ratio",
        "Hole depth / diameter",
        applies_to=_HOLES,
        unit="ratio",
    ),
    # "1xT" -- see bend_radius_min.
    "hole_to_edge_distance_min": MetricSpec(
        "hole_to_edge_distance",
        "Hole-to-edge distance / material thickness",
        applies_to=_HOLES,
        reference="nominal_thickness",
        unit="ratio",
    ),
}


# Rules whose catalog text expresses a multiple of another dimension that we
# cannot reconstruct. Evaluating these against an absolute measurement would
# compare, for example, a 12 mm distance against a "4 x material thickness"
# requirement as though the threshold were 4 mm.
UNRECONSTRUCTABLE_RATIO_RULES: dict[str, str] = {
    "IM-037": "compound rule: requires both hole diameter and local wall thickness as references",
    "SM-005": "threshold is a multiple of material thickness for a relief feature we do not recognise",
    "SM-009": "requires bend-line recognition to measure hole-to-bend distance",
    "SM-011": "requires hole-pair adjacency analysis",
    "SM-012": "threshold is a multiple of material thickness; needs sheet-thickness inference",
    "SM-014": "requires tab feature recognition",
    "SM-015": "requires flange feature recognition",
    "SM-016": "requires flange feature recognition and bend radius",
    "SM-017": "requires hem feature recognition",
    "MC-004": "threshold is a multiple of cutting-tool diameter, which is not a part property",
    "MC-006": "threshold is a multiple of cutting-tool diameter, which is not a part property",
    "DC-009": "threshold is a multiple of local wall thickness for a rib we do not recognise",
}


# --- Material scoping ------------------------------------------------------

PLASTIC = "plastic"
METAL = "metal"


@dataclass(frozen=True)
class Material:
    key: str
    label: str
    material_class: str


MATERIALS_BY_FAMILY: dict[str, list[Material]] = {
    "Injection Moulding": [
        Material("ABS", "ABS", PLASTIC),
        Material("PC", "Polycarbonate (PC)", PLASTIC),
        Material("PP", "Polypropylene (PP)", PLASTIC),
        Material("PA", "Nylon (PA)", PLASTIC),
        Material("POM", "Acetal (POM)", PLASTIC),
        Material("PBT", "PBT", PLASTIC),
        Material("PC/ABS", "PC/ABS blend", PLASTIC),
    ],
    "Machining": [
        Material("ALUMINIUM", "Aluminium", METAL),
        Material("STEEL", "Steel", METAL),
        Material("STAINLESS", "Stainless steel", METAL),
        Material("BRASS", "Brass", METAL),
        Material("ABS", "ABS", PLASTIC),
        Material("PC", "Polycarbonate (PC)", PLASTIC),
        Material("POM", "Acetal (POM)", PLASTIC),
    ],
}


# Rules that hold for exactly one material. Everything not listed applies
# regardless of material choice.
RULE_MATERIAL_SCOPE: dict[str, str] = {
    "IM-001": "ABS",
    "IM-002": "PC",
    "IM-003": "PP",
    "IM-004": "PA",
    "IM-005": "POM",
    "IM-006": "PBT",
    "IM-007": "PC/ABS",
}

# Rules that hold for a whole class of materials rather than a single grade.
RULE_MATERIAL_CLASS_SCOPE: dict[str, str] = {
    "MC-007": METAL,
    "MC-008": PLASTIC,
}


def materials_for_family(process_family: str) -> list[Material]:
    return MATERIALS_BY_FAMILY.get(process_family, [])


def material_class(process_family: str, material_key: Optional[str]) -> Optional[str]:
    if not material_key:
        return None
    for material in materials_for_family(process_family):
        if material.key == material_key:
            return material.material_class
    return None


def rule_material_applies(
    rule_id: str, process_family: str, material_key: Optional[str]
) -> tuple[bool, Optional[str]]:
    """Decides whether a material-specific rule applies to the chosen material.

    Returns (applies, reason_if_not).
    """
    required_material = RULE_MATERIAL_SCOPE.get(rule_id)
    required_class = RULE_MATERIAL_CLASS_SCOPE.get(rule_id)

    if required_material is None and required_class is None:
        return True, None

    if not material_key:
        target = required_material or required_class
        return False, f"rule is specific to {target}; no material was selected"

    if required_material is not None:
        if required_material == material_key:
            return True, None
        return False, f"rule applies to {required_material}, not {material_key}"

    selected_class = material_class(process_family, material_key)
    if selected_class == required_class:
        return True, None
    return False, f"rule applies to {required_class} materials, not {material_key}"


# --- Face applicability ----------------------------------------------------


def metric_applies_to_face(spec: MetricSpec, face: PartModelFace) -> tuple[bool, str]:
    """Decides whether a metric is meaningful for a given face.

    Returns (applies, reason_if_not).
    """
    if face.sample_count == 0:
        return False, "face could not be sampled"

    if spec.applies_to and (face.feature_class or "") not in spec.applies_to:
        return False, f"metric does not apply to a {face.feature_class} face"

    # Draft is undefined on a face perpendicular to the pull direction: it is
    # formed by the end of the mould half, not by a wall that has to slide out.
    if spec.attribute == "draft_angle" and face.is_perpendicular_to_pull:
        return False, "face is perpendicular to the pull direction; draft does not apply"

    return True, ""


def family_needs_undercuts(rules: list[dict]) -> bool:
    """Whether any rule in this family actually asks about undercuts.

    Derived from the catalog rather than a hardcoded list of moulding families,
    so adding an undercut rule to a process enables the detection for it
    automatically. Detection costs a ray cast per face, and on a machined part
    nothing consumes the answer.
    """
    for rule in rules:
        spec = resolve_metric(rule.get("metric"))
        if spec is not None and spec.attribute == "is_undercut":
            return True
    return False


def resolve_metric(metric_name: Optional[str]) -> Optional[MetricSpec]:
    """Looks up a catalog metric name. Returns None if we cannot measure it."""
    if not metric_name:
        return None
    return METRIC_REGISTRY.get(metric_name.strip())


# --- Catalog linting -------------------------------------------------------

# Several catalog entries pair a recommended value with a hard limit -- "0.8
# rec / 0.5 min", "4xD rec / 10xD max" -- and the CSV conversion stored both
# numbers as the ends of a range. They are not a band: both point the same way.
#
# Read as a range they fail correct designs from either side. On a real NIST
# machined test part, MC-007 ("0.8 rec / 0.5 min") reported a 50 mm section as
# violating a *minimum* wall rule 60 times, and MC-012 ("4xD rec / 10xD max")
# failed every hole for being shallower than recommended -- which is easier to
# drill, not a defect.
_REC = r"\brec(ommended)?\b"
_FLOOR_PAIR = re.compile(
    rf"({_REC}.*\bmin(imum)?\b)|(\bmin(imum)?\b.*{_REC})", re.IGNORECASE
)
_CEILING_PAIR = re.compile(
    rf"({_REC}.*\bmax(imum)?\b)|(\bmax(imum)?\b.*{_REC})", re.IGNORECASE
)


def directional_pair_kind(predicate: Optional[dict]) -> Optional[str]:
    """Returns 'floor', 'ceiling', or None for a range predicate.

    'floor' means both stored numbers are lower bounds; 'ceiling' means both
    are upper bounds. None means the range is a genuine band and is left alone.
    """
    if not predicate or predicate.get("type") != "range":
        return None
    source = predicate.get("original_csv_text") or ""
    if not source:
        return None
    # Checked before floor: "min / max" wording is a real band, and only a
    # pairing with an explicit recommendation indicates a single direction.
    if _CEILING_PAIR.search(source):
        return "ceiling"
    if _FLOOR_PAIR.search(source):
        return "floor"
    return None


def is_floor_pair_predicate(predicate: dict) -> bool:
    """True when a range predicate is really two minimums, not a band."""
    return directional_pair_kind(predicate) == "floor"


def normalize_predicate(predicate: Optional[dict]) -> Optional[dict]:
    """Rewrites predicates whose stored shape misstates the rule.

    Applied before evaluation so the fix lives in one place rather than in
    every caller, and so the original text stays available for the report.
    """
    kind = directional_pair_kind(predicate)
    if kind is None:
        return predicate

    numeric = [
        b for b in (predicate.get("min"), predicate.get("max"))
        if isinstance(b, (int, float))
    ]
    if not numeric:
        return predicate

    if kind == "floor":
        operator_symbol, threshold, advisory = ">=", min(numeric), max(numeric)
    else:
        operator_symbol, threshold, advisory = "<=", max(numeric), min(numeric)

    return {
        "type": "simple",
        "operator": operator_symbol,
        "threshold": threshold,
        "original_csv_text": predicate.get("original_csv_text"),
        "normalized_from": f"range ({kind} pair)",
        "advisory_threshold": advisory,
    }


_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_DATE_MANGLED = re.compile(rf"{_MONTH}[-/ ]?\d|\d[-/ ]?{_MONTH}", re.IGNORECASE)
_RATIO_TEXT = re.compile(r"[x×]\s*(T\b|thickness|depth|diameter|D\b|wall)", re.IGNORECASE)


@dataclass
class CatalogIssue:
    rule_id: str
    severity: str
    message: str


def lint_rule(rule: dict) -> list[CatalogIssue]:
    """Checks one rule for predicates that would produce a wrong verdict.

    The catalog is generated from a spreadsheet, and spreadsheet conversion
    introduces failure modes that are invisible in the JSON: a cell reading
    "3-40" becomes the date "Mar-40" and then the number -40. A rule that
    silently degrades to a nonsense threshold is worse than one that refuses to
    run, so these are surfaced at load time.
    """
    issues: list[CatalogIssue] = []
    rule_id = rule.get("rule_id", "<unknown>")
    predicate = rule.get("predicate") or {}
    source_text = predicate.get("original_csv_text") or ""

    if source_text and _DATE_MANGLED.search(source_text):
        issues.append(
            CatalogIssue(
                rule_id,
                "error",
                f"source text {source_text!r} looks like a spreadsheet date "
                "conversion of a numeric range; the parsed threshold is unreliable",
            )
        )

    for key in ("threshold", "min", "max"):
        value = predicate.get(key)
        if isinstance(value, (int, float)) and value < 0:
            issues.append(
                CatalogIssue(
                    rule_id,
                    "error",
                    f"predicate {key} is negative ({value}); source text "
                    f"{source_text!r} did not parse into a usable threshold",
                )
            )

    if predicate.get("type") == "range":
        low, high = predicate.get("min"), predicate.get("max")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)) and low > high:
            issues.append(
                CatalogIssue(
                    rule_id, "error", f"range minimum ({low}) exceeds maximum ({high})"
                )
            )

    if source_text and _RATIO_TEXT.search(source_text):
        spec = resolve_metric(rule.get("metric"))
        if rule_id not in UNRECONSTRUCTABLE_RATIO_RULES and (
            spec is None or spec.reference is None and spec.unit != "ratio"
        ):
            issues.append(
                CatalogIssue(
                    rule_id,
                    "warning",
                    f"source text {source_text!r} expresses a multiple of another "
                    "dimension, but the predicate stores it as an absolute value",
                )
            )

    if rule.get("kind") == "quantitative" and not predicate:
        issues.append(
            CatalogIssue(
                rule_id, "warning", "declared quantitative but carries no predicate"
            )
        )

    return issues


def lint_catalog(rules: list[dict]) -> list[CatalogIssue]:
    """Lints every rule and returns all issues found."""
    issues: list[CatalogIssue] = []
    for rule in rules:
        issues.extend(lint_rule(rule))
    return issues


def unreliable_rule_ids(rules: list[dict]) -> dict[str, str]:
    """Rules whose predicate cannot be trusted to produce a verdict.

    These are evaluated as NOT_EVALUATED with the linter's explanation rather
    than being allowed to emit a confident but wrong COMPLIANT or NON-COMPLIANT.
    """
    blocked: dict[str, str] = dict(UNRECONSTRUCTABLE_RATIO_RULES)
    for issue in lint_catalog(rules):
        if issue.severity == "error":
            blocked.setdefault(issue.rule_id, issue.message)
    return blocked

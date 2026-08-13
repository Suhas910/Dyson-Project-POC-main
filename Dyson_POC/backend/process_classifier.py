"""process_classifier.py - Works out how a part was meant to be made.

Choosing the process family by hand is the one step in this pipeline where a
user can be silently, expensively wrong. Pick "Machining" for a moulded housing
and every draft rule in the catalogue is skipped: the report comes back clean
and says nothing about the defect that matters most. Nothing in the output warns
you, because from the engine's point of view you asked a different question.

So the process is read off the geometry instead. A moulded part and a machined
part do not look alike:

  * A moulded part has thin walls of near-constant thickness, and its walls are
    drafted so the tool can open.
  * A machined part has walls at whatever thickness the pocket left, no draft at
    all, and internal corners rounded to a cutter radius.
  * A sheet-metal part is a shell of one constant thickness, far thinner than
    anything else about it.
  * An assembly is more than one solid.

Every score below is a weighted count of such tests, and every test contributes a
sentence to `evidence_for` or `evidence_against`. That is deliberate: a
classification a user cannot check is one they will either over-trust or ignore.

What this cannot do is worth stating plainly. Geometry does not carry material,
so injection moulding and die casting are indistinguishable here -- both are
thin-walled, drafted, cast-to-shape. When both score highly the result says so
rather than picking one, and the material selector settles it.
"""

import math
from dataclasses import dataclass, field
from statistics import median
from typing import Optional

# --- Families ---------------------------------------------------------------

# Rules that hold for any solid regardless of how it is made: general geometry
# standards and dimensional capability. Always analysed, never "detected".
UNIVERSAL_FAMILIES = ("Dimensional Capability", "Standards-Derived Geometry")

# Families about how parts go together rather than how one is formed. They need
# an assembly, and saying so is more use than running them on a single solid and
# reporting that nothing could be evaluated.
ASSEMBLY_FAMILIES = ("DFA (Assembly)", "Serviceability")

PROCESS_FAMILIES = (
    "Injection Moulding",
    "Die Casting",
    "Metal Injection Moulding",
    "Machining",
    "Sheet Metal",
    "Powder Metallurgy",
)

# A family is analysed when it scores at least this well. Set low on purpose:
# the cost of running an extra family is a few milliseconds of rule evaluation
# and a clearly-labelled section nobody has to read, while the cost of skipping
# the right one is a report that misses the defect it existed to find.
ANALYSIS_THRESHOLD = 0.35

# Above this a family is presented as the part's likely process rather than as
# a possibility worth checking.
CONFIDENT_THRESHOLD = 0.65

# Two families within this of each other are not meaningfully distinguishable by
# the evidence available, and are reported as a tie rather than ranked.
TIE_MARGIN = 0.10

# A wall counts as drafted at half a degree. Below that the angle is within what
# a CAD kernel and a sampling grid can disagree about, so calling it draft would
# read intent into noise.
DRAFT_PRESENT_DEG = 0.5

# Wall thickness spread, as a coefficient of variation, below which walls are
# "near-constant". Moulded parts are designed to a nominal wall; machined parts
# are whatever the pockets left behind.
UNIFORM_WALL_CV = 0.35


@dataclass
class Signal:
    """One measured property of the part, with a form fit for display."""

    name: str
    value: Optional[float]
    display: str


@dataclass
class Candidate:
    """One process family, scored, with the reasoning attached."""

    process_family: str
    score: float
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)
    # Universal and assembly families are included for reasons other than
    # having been detected, and should not be presented as detections.
    basis: str = "detected"

    @property
    def confidence(self) -> str:
        if self.basis != "detected":
            return "always applicable" if self.basis == "universal" else "assembly"
        if self.score >= CONFIDENT_THRESHOLD:
            return "likely"
        if self.score >= ANALYSIS_THRESHOLD:
            return "possible"
        return "unlikely"

    def as_dict(self) -> dict:
        return {
            "process_family": self.process_family,
            "score": round(self.score, 3),
            "confidence": self.confidence,
            "basis": self.basis,
            "evidence_for": self.evidence_for,
            "evidence_against": self.evidence_against,
        }


@dataclass
class Classification:
    signals: dict
    candidates: list[Candidate]
    families_to_analyse: list[str]
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "signals": {
                name: {"value": s.value, "display": s.display}
                for name, s in self.signals.items()
            },
            "candidates": [c.as_dict() for c in self.candidates],
            "families_to_analyse": self.families_to_analyse,
            "notes": self.notes,
        }


# --- Measurement ------------------------------------------------------------


def _coefficient_of_variation(values: list[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    if mean <= 1e-9:
        return None
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance) / mean


def measure_signals(part_model, loaded) -> dict:
    """Reduces the part model to the handful of numbers that decide process.

    Everything here is already measured; this only aggregates. Keeping the
    aggregation separate from the scoring means the evidence shown to a user is
    the same evidence the score was computed from, rather than a re-description
    of it.
    """
    faces = part_model.faces
    face_count = len(faces)

    planar = [f for f in faces if f.surface_type == "plane"]
    wall_thicknesses = [f.wall_thickness for f in planar if f.wall_thickness]
    nominal = median(wall_thicknesses) if wall_thicknesses else None
    spread = _coefficient_of_variation(wall_thicknesses)

    # Draft is measured on every wall, not just the flat ones. A moulded part's
    # drafted surfaces are routinely conical or freeform -- a tapered boss, a
    # swept housing wall -- and looking only at planes reports "no drafted walls"
    # for a part that is drafted throughout, which is the worst possible answer
    # from the signal that matters most.
    #
    # Bore walls are excluded rather than counted as undrafted. A drilled hole
    # is a cylinder parallel to pull, so its draft is zero by construction; on a
    # part with twenty holes those zeroes would swamp the statistic and make
    # every drilled part look machined.
    draftable = [
        f
        for f in faces
        if not f.is_perpendicular_to_pull
        and f.draft_angle is not None
        and f.feature_class not in ("hole", "boss", "tessellated")
    ]
    drafted = [f for f in draftable if f.draft_angle >= DRAFT_PRESENT_DEG]
    draft_fraction = len(drafted) / len(draftable) if draftable else None

    holes = [f for f in faces if f.feature_class == "hole"]
    fillets = [f for f in faces if f.feature_class == "internal_fillet"]
    fillet_radii = [f.internal_radius for f in fillets if f.internal_radius]
    freeform = [f for f in faces if f.surface_type == "freeform"]

    bbox = loaded.bounding_box_mm
    largest = max(bbox) if bbox else None
    smallest = min(bbox) if bbox else None

    # A sheet-metal part is a shell: one thickness, and that thickness far
    # smaller than anything else about the part.
    slenderness = None
    if nominal and nominal > 1e-6 and smallest:
        slenderness = smallest / nominal

    def signal(name, value, display):
        return name, Signal(name=name, value=value, display=display)

    return dict(
        [
            signal("solid_count", loaded.solid_count, f"{loaded.solid_count}"),
            signal("face_count", face_count, f"{face_count}"),
            signal(
                "largest_dimension_mm",
                largest,
                f"{largest:.1f} mm" if largest else "unknown",
            ),
            signal(
                "nominal_wall_mm",
                nominal,
                f"{nominal:.2f} mm" if nominal else "not established",
            ),
            signal(
                "wall_uniformity",
                spread,
                "not measurable" if spread is None
                else f"{spread:.0%} variation across {len(wall_thicknesses)} walls",
            ),
            signal(
                "draft_coverage",
                draft_fraction,
                "no drafted walls to measure" if draft_fraction is None
                else f"{len(drafted)} of {len(draftable)} walls drafted",
            ),
            signal("hole_count", len(holes), f"{len(holes)}"),
            signal(
                "internal_fillet_count", len(fillets), f"{len(fillets)}"
            ),
            signal(
                "fillet_radius_spread",
                _coefficient_of_variation(fillet_radii),
                "not measurable" if len(fillet_radii) < 2
                else f"{_coefficient_of_variation(fillet_radii):.0%} variation "
                     f"across {len(fillet_radii)} fillets",
            ),
            signal(
                "freeform_fraction",
                len(freeform) / face_count if face_count else None,
                f"{len(freeform)} of {face_count} faces",
            ),
            signal(
                "undercut_count",
                len(part_model.undercut_face_ids),
                f"{len(part_model.undercut_face_ids)}",
            ),
            signal(
                "shell_slenderness",
                slenderness,
                "not measurable" if slenderness is None
                else f"part is {slenderness:.0f}x its wall thickness at its thinnest",
            ),
        ]
    )


# --- Scoring ----------------------------------------------------------------


class _Scorer:
    """Accumulates weighted tests and the sentence each one contributes.

    A test with an unmeasurable input is dropped from both the numerator and the
    denominator rather than counted as a failure -- otherwise a part whose walls
    could not be measured would score as "definitely not moulded", which is the
    opposite of what the missing measurement means.
    """

    def __init__(self):
        self.weight = 0.0
        self.earned = 0.0
        self.evidence_for: list[str] = []
        self.evidence_against: list[str] = []
        self.disqualified: Optional[str] = None
        self.ceiling = 1.0

    def test(self, weight: float, passed: Optional[bool], when_true: str, when_false: str):
        if passed is None:
            return
        self.weight += weight
        if passed:
            self.earned += weight
            self.evidence_for.append(when_true)
        else:
            self.evidence_against.append(when_false)

    def cap(self, ceiling: float, reason: str):
        """Limits how confident a family can be when its evidence is weak.

        Some families are recognised only by what they are compatible with --
        small, simple, prismatic, undrafted -- rather than by anything that
        points at them specifically. Every one of those tests can pass on a part
        made some other way. Scoring such a family at full marks would present
        "nothing rules this out" as "this is what it is", which is a different
        claim entirely.
        """
        self.ceiling = min(self.ceiling, ceiling)
        self.evidence_against.append(reason)

    def disqualify(self, reason: str):
        """Rules the process out entirely, for a physical impossibility.

        Distinct from a failed test, and used only where the process could not
        produce the part at all -- forming cannot change the gauge of the sheet
        it started from, and a rigid punch cannot withdraw from an undercut.

        The inverse is deliberately *not* evidence. That a part has no undercut
        does not argue it was pressed; almost nothing has an undercut. Counting
        the absence of an impossibility as a point in favour is how a scorer
        ends up ranking every simple part as a pressing.
        """
        self.disqualified = reason
        self.evidence_against.append(reason)

    def score(self) -> float:
        if self.disqualified:
            return 0.0
        raw = self.earned / self.weight if self.weight else 0.0
        return min(raw, self.ceiling)


def _value(signals: dict, name: str) -> Optional[float]:
    signal = signals.get(name)
    return signal.value if signal else None


def _moulded_scorer(signals: dict, wall_range: tuple[float, float]) -> _Scorer:
    """The tests shared by every cast-or-moulded-to-shape process.

    Injection moulding, die casting and MIM differ in wall range and part size,
    not in kind: all three fill a cavity and all three need draft to release.
    """
    scorer = _Scorer()
    nominal = _value(signals, "nominal_wall_mm")
    uniformity = _value(signals, "wall_uniformity")
    draft = _value(signals, "draft_coverage")

    low, high = wall_range
    scorer.test(
        3.0,
        None if nominal is None else low <= nominal <= high,
        f"sections average {signals['nominal_wall_mm'].display}, inside the "
        f"{low}-{high} mm band this process is designed around",
        f"sections average {signals['nominal_wall_mm'].display}, outside the "
        f"{low}-{high} mm band typical of this process",
    )
    scorer.test(
        2.0,
        None if uniformity is None else uniformity <= UNIFORM_WALL_CV,
        f"wall thickness is near-constant ({signals['wall_uniformity'].display}), "
        "as a part designed to a nominal wall would be",
        f"wall thickness varies a lot ({signals['wall_uniformity'].display}), which "
        "a part designed to a nominal wall would not",
    )
    scorer.test(
        3.0,
        None if draft is None else draft >= 0.5,
        f"most walls carry draft ({signals['draft_coverage'].display}), so the part "
        "was drawn to release from a tool",
        f"walls carry no draft ({signals['draft_coverage'].display}); a part formed "
        "in a tool would need it to release",
    )
    return scorer


def _score_injection_moulding(signals: dict) -> Candidate:
    scorer = _moulded_scorer(signals, (0.5, 6.0))
    freeform = _value(signals, "freeform_fraction")
    scorer.test(
        1.0,
        None if freeform is None else freeform > 0.02,
        "the part carries freeform surfaces, which are cheap in a mould and "
        "expensive anywhere else",
        "the part is entirely analytic surfaces, so nothing about it requires a "
        "mould",
    )
    return Candidate("Injection Moulding", scorer.score(), scorer.evidence_for, scorer.evidence_against)


def _score_die_casting(signals: dict) -> Candidate:
    # Die castings tolerate, and generally carry, thicker walls than an
    # injection moulding of the same size.
    scorer = _moulded_scorer(signals, (1.0, 8.0))
    return Candidate("Die Casting", scorer.score(), scorer.evidence_for, scorer.evidence_against)


def _score_metal_injection_moulding(signals: dict) -> Candidate:
    scorer = _moulded_scorer(signals, (0.3, 6.0))
    largest = _value(signals, "largest_dimension_mm")
    # MIM is a small-part process: the powder-binder feedstock has to debind all
    # the way through, which puts a hard ceiling on section and size.
    scorer.test(
        3.0,
        None if largest is None else largest <= 100.0,
        f"the part is small ({signals['largest_dimension_mm'].display}), within the "
        "size range MIM is used for",
        f"the part is {signals['largest_dimension_mm'].display} across, larger than "
        "MIM is normally used for",
    )
    return Candidate(
        "Metal Injection Moulding", scorer.score(), scorer.evidence_for, scorer.evidence_against
    )


def _score_machining(signals: dict) -> Candidate:
    scorer = _Scorer()
    draft = _value(signals, "draft_coverage")
    uniformity = _value(signals, "wall_uniformity")
    fillets = _value(signals, "internal_fillet_count")
    fillet_spread = _value(signals, "fillet_radius_spread")
    freeform = _value(signals, "freeform_fraction")

    scorer.test(
        3.0,
        None if draft is None else draft < 0.25,
        f"walls are square to the pull direction ({signals['draft_coverage'].display}), "
        "which is what a cutter leaves and a mould could not",
        f"most walls are drafted ({signals['draft_coverage'].display}), which "
        "machining would have no reason to produce",
    )
    scorer.test(
        1.5,
        None if uniformity is None else uniformity > UNIFORM_WALL_CV,
        f"wall thickness varies freely ({signals['wall_uniformity'].display}), as it "
        "does when material is removed rather than filled",
        f"wall thickness is near-constant ({signals['wall_uniformity'].display}), "
        "which suggests a designed nominal wall rather than removed material",
    )
    scorer.test(
        1.5,
        None if fillet_spread is None else fillet_spread < 0.25,
        f"internal corners share a radius ({signals['fillet_radius_spread'].display}), "
        "the signature of a single cutter",
        f"internal corner radii vary ({signals['fillet_radius_spread'].display}), so "
        "they were not left by one cutter",
    )
    scorer.test(
        0.5,
        None if fillets is None else fillets > 0,
        "internal corners are radiused rather than sharp, as a cutter leaves them",
        "internal corners are sharp, which a rotating cutter cannot produce",
    )
    scorer.test(
        1.0,
        None if freeform is None else freeform < 0.10,
        "the part is prismatic, which is what machining is cheapest at",
        "the part is largely freeform, which is expensive to machine",
    )
    return Candidate("Machining", scorer.score(), scorer.evidence_for, scorer.evidence_against)


def _score_sheet_metal(signals: dict) -> Candidate:
    scorer = _Scorer()
    slenderness = _value(signals, "shell_slenderness")
    uniformity = _value(signals, "wall_uniformity")
    nominal = _value(signals, "nominal_wall_mm")

    scorer.test(
        3.0,
        None if slenderness is None else slenderness >= 8.0,
        f"the part is a thin shell ({signals['shell_slenderness'].display}), the "
        "proportions of formed sheet",
        f"the part is not shell-like ({signals['shell_slenderness'].display}); sheet "
        "metal is thin compared with everything else about it",
    )
    # Bending and forming move sheet about; they cannot make it thicker in one
    # place and thinner in another. A part whose section varies this much did
    # not start as a sheet, whatever else is true of its proportions.
    if uniformity is not None and uniformity > 0.25:
        scorer.disqualify(
            f"section varies by {signals['wall_uniformity'].display.split(' ')[0]}; "
            "forming cannot change the gauge of the sheet it started from"
        )
    else:
        scorer.test(
            3.0,
            None if uniformity is None else uniformity <= 0.15,
            f"thickness is essentially constant ({signals['wall_uniformity'].display}), "
            "as it must be when the part starts as one sheet",
            f"thickness is only roughly constant ({signals['wall_uniformity'].display})",
        )
    scorer.test(
        1.0,
        None if nominal is None else nominal <= 6.0,
        f"at {signals['nominal_wall_mm'].display} the section is within normal sheet "
        "gauges",
        f"at {signals['nominal_wall_mm'].display} the section is thicker than sheet "
        "stock",
    )
    return Candidate("Sheet Metal", scorer.score(), scorer.evidence_for, scorer.evidence_against)


def _score_powder_metallurgy(signals: dict) -> Candidate:
    scorer = _Scorer()
    undercuts = _value(signals, "undercut_count")
    largest = _value(signals, "largest_dimension_mm")
    draft = _value(signals, "draft_coverage")
    freeform = _value(signals, "freeform_fraction")
    faces = _value(signals, "face_count")

    # Pressed-and-sintered parts are compacted between two rigid punches on one
    # axis. An undercut is not awkward here, it is impossible -- so it rules the
    # process out rather than costing it points. The absence of one is not
    # evidence in return: almost no part has an undercut.
    if undercuts:
        scorer.disqualify(
            f"{signals['undercut_count'].display} face(s) are undercut, which rigid "
            "punches pressing on one axis cannot form"
        )

    scorer.test(
        2.0,
        None if largest is None else largest <= 120.0,
        f"the part is small enough ({signals['largest_dimension_mm'].display}) for a "
        "press of ordinary tonnage",
        f"at {signals['largest_dimension_mm'].display} the part is large for pressing",
    )
    scorer.test(
        1.0,
        None if draft is None else draft < 0.4,
        "walls run straight along the pressing axis, as compaction requires",
        "walls are drafted, which pressing between flat punches does not produce",
    )
    scorer.test(
        1.0,
        None if freeform is None else freeform < 0.05,
        "the part is prismatic, which suits a two-punch tool",
        "the part is freeform, which a two-punch tool cannot form",
    )
    scorer.test(
        2.0,
        None if faces is None else faces <= 60,
        f"the part is simple ({signals['face_count'].display} faces), which is what "
        "pressing is chosen for",
        f"at {signals['face_count'].display} faces the part is more intricate than "
        "pressing economically produces",
    )
    # Every test above is a compatibility check: a small, simple, prismatic,
    # undrafted part *could* be pressed, but so could it be machined, and
    # nothing here distinguishes the two. Pressing is therefore offered as a
    # possibility to check rather than as a reading of the part.
    scorer.cap(
        CONFIDENT_THRESHOLD - 0.01,
        "nothing about the geometry points specifically at pressing; the "
        "evidence is that pressing is not ruled out, which is a weaker claim",
    )
    return Candidate(
        "Powder Metallurgy", scorer.score(), scorer.evidence_for, scorer.evidence_against
    )


_SCORERS = {
    "Injection Moulding": _score_injection_moulding,
    "Die Casting": _score_die_casting,
    "Metal Injection Moulding": _score_metal_injection_moulding,
    "Machining": _score_machining,
    "Sheet Metal": _score_sheet_metal,
    "Powder Metallurgy": _score_powder_metallurgy,
}


# --- Entry point ------------------------------------------------------------


def classify(part_model, loaded, available_families: Optional[list[str]] = None) -> Classification:
    """Reads the part's likely process families off its geometry.

    Args:
        part_model: the measured faces.
        loaded: the ingest result, for solid count and overall size.
        available_families: families the catalogue actually has rules for.
            Anything else is dropped, so the result never promises a section the
            report cannot fill.

    Returns:
        A `Classification` carrying the signals, every family's score with its
        reasoning, and the list of families worth analysing.
    """
    signals = measure_signals(part_model, loaded)
    catalogue = set(available_families) if available_families else None

    candidates = [scorer(signals) for scorer in _SCORERS.values()]
    candidates.sort(key=lambda c: -c.score)

    notes: list[str] = []

    # Universal families are analysed because their rules hold for any solid,
    # not because anything about this part suggested them.
    for family in UNIVERSAL_FAMILIES:
        candidates.append(
            Candidate(
                family,
                1.0,
                ["applies to any solid, whatever process makes it"],
                [],
                basis="universal",
            )
        )

    solids = _value(signals, "solid_count") or 1
    for family in ASSEMBLY_FAMILIES:
        if solids > 1:
            candidates.append(
                Candidate(
                    family,
                    1.0,
                    [f"the file contains {int(solids)} solids, so it is an assembly"],
                    [],
                    basis="assembly",
                )
            )
        else:
            candidates.append(
                Candidate(
                    family,
                    0.0,
                    [],
                    ["the file contains a single solid, so there is nothing to assemble"],
                    basis="assembly",
                )
            )

    if catalogue is not None:
        candidates = [c for c in candidates if c.process_family in catalogue]

    selected = [
        c.process_family
        for c in candidates
        if c.basis != "detected" and c.score > 0
    ] + [
        c.process_family
        for c in candidates
        if c.basis == "detected" and c.score >= ANALYSIS_THRESHOLD
    ]

    detected = [c for c in candidates if c.basis == "detected"]
    if not any(c.score >= ANALYSIS_THRESHOLD for c in detected):
        notes.append(
            "No manufacturing process scored well enough to be confident. The "
            "universal geometry rules still apply; choose a process by hand to "
            "check it against process-specific rules."
        )

    # Ties are reported rather than broken. Picking a winner from a margin this
    # small would present a coin toss as a conclusion.
    top = [c for c in detected if c.score >= ANALYSIS_THRESHOLD]
    if len(top) >= 2 and (top[0].score - top[1].score) <= TIE_MARGIN:
        pair = {top[0].process_family, top[1].process_family}
        # The moulded processes are a special case: they are not merely close
        # here, they are indistinguishable in principle, because what separates
        # them is the material and geometry does not carry it.
        if pair <= {"Injection Moulding", "Die Casting", "Metal Injection Moulding"}:
            why = (
                "What separates these processes is the material, which a STEP file "
                "does not carry, so they cannot be told apart from geometry at all. "
                "Choosing a material decides which section applies."
            )
        else:
            why = (
                "The evidence does not separate them, so both are analysed and both "
                "sections are reported rather than one being chosen for you."
            )
        notes.append(
            f"{top[0].process_family} and {top[1].process_family} score within "
            f"{TIE_MARGIN:.0%} of each other. {why}"
        )

    # Preserve a stable, readable order: detected families by score, then the
    # families included on other grounds.
    ordered = sorted(
        candidates,
        key=lambda c: (0 if c.basis == "detected" else 1, -c.score, c.process_family),
    )

    return Classification(
        signals=signals,
        candidates=ordered,
        families_to_analyse=[f for f in dict.fromkeys(selected)],
        notes=notes,
    )

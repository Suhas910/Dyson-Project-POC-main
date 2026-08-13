"""step_loader.py - Ingests a STEP file into an OCCT shape.

This is the first step in the DFX review pipeline. It reads a STEP file from
disk, normalises it to millimetres, optionally heals minor topological defects,
and reports what it found so downstream steps (and the client-facing report)
can state the provenance of every measurement.

Two facts matter for the correctness of every downstream measurement:

1. Units. OCCT's STEP translator converts the file's declared length unit into
   the "cascade unit" (`xstep.cascade.unit`, default MM). We set it explicitly
   rather than trusting a process-global default, and we report the unit the
   file declared so a mm/inch mix-up is visible instead of silent.
2. Solidity. Wall-thickness rays assume a closed solid. An open shell or a
   surface-only model can still be measured, but the numbers mean much less,
   so the loader reports solidity instead of letting the pipeline assume it.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_ReturnStatus
from OCC.Core.Interface import Interface_Static
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.ShapeFix import ShapeFix_Shape
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib


# Maps a STEP length-unit declaration to (canonical name, millimetres per unit).
# Used for reporting only -- OCCT performs the actual conversion to millimetres.
_SI_PREFIX_TO_MM = {
    "MILLI": ("millimetre", 1.0),
    "CENTI": ("centimetre", 10.0),
    "DECI": ("decimetre", 100.0),
    "KILO": ("kilometre", 1_000_000.0),
    "MICRO": ("micrometre", 0.001),
}

_CONVERSION_UNIT_TO_MM = {
    "INCH": ("inch", 25.4),
    "INCHES": ("inch", 25.4),
    "FOOT": ("foot", 304.8),
    "FEET": ("foot", 304.8),
    "MIL": ("mil", 0.0254),
    "MILLIMETRE": ("millimetre", 1.0),
    "MILLIMETER": ("millimetre", 1.0),
    "CENTIMETRE": ("centimetre", 10.0),
    "CENTIMETER": ("centimetre", 10.0),
    "METRE": ("metre", 1000.0),
    "METER": ("metre", 1000.0),
}


@dataclass
class LoadedPart:
    """A STEP file that has been read, normalised and inspected."""

    shape: TopoDS_Shape
    source_units: str = "unknown"
    solid_count: int = 0
    shell_count: int = 0
    face_count: int = 0
    is_valid_solid: bool = False
    was_healed: bool = False
    bounding_box_mm: tuple[float, float, float] | None = None
    warnings: list[str] = field(default_factory=list)

    def as_metadata(self) -> dict:
        """A JSON-serialisable summary for the API response and the report."""
        return {
            "source_units": self.source_units,
            "solid_count": self.solid_count,
            "shell_count": self.shell_count,
            "face_count": self.face_count,
            "is_valid_solid": self.is_valid_solid,
            "was_healed": self.was_healed,
            "bounding_box_mm": (
                [round(d, 3) for d in self.bounding_box_mm]
                if self.bounding_box_mm
                else None
            ),
            "warnings": self.warnings,
        }


def detect_declared_units(file_path: Path) -> str:
    """Reads the length unit a STEP file declares, for reporting.

    OCCT converts geometry to millimetres on import; this tells the user what
    it converted *from*. Only the header and the first part of the data section
    are scanned, since the unit context appears well before the geometry.
    """
    try:
        with file_path.open("r", errors="ignore") as fh:
            text = fh.read(2_000_000)
    except OSError as exc:
        logging.warning(f"Could not read {file_path} for unit detection: {exc}")
        return "unknown"

    # A STEP file declares several units -- length, angle, solid angle -- each
    # as its own entity. Matching the first CONVERSION_BASED_UNIT in the file
    # reports whichever happens to appear first, which on real CAD exports is
    # usually 'DEGREE'. Only an entity that also carries LENGTH_UNIT() is the
    # one being asked about, so entities are split apart and filtered.
    for entity in text.split(";"):
        if "CONVERSION_BASED_UNIT" not in entity.upper():
            continue
        if "LENGTH_UNIT" not in entity.upper():
            continue
        named = re.search(r"CONVERSION_BASED_UNIT\(\s*'([^']+)'", entity, re.IGNORECASE)
        if not named:
            continue
        name = named.group(1).strip().upper()
        if name in _CONVERSION_UNIT_TO_MM:
            return _CONVERSION_UNIT_TO_MM[name][0]
        return name.lower()

    si = re.search(
        r"LENGTH_UNIT\(\)[^;]*?SI_UNIT\s*\(\s*(\.(\w+)\.|\$)\s*,\s*\.METRE\.",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if si:
        prefix = (si.group(2) or "").upper()
        if not prefix:
            return "metre"
        return _SI_PREFIX_TO_MM.get(prefix, (prefix.lower(), None))[0]

    return "unknown"


def _count(shape: TopoDS_Shape, shape_type) -> int:
    explorer = TopExp_Explorer(shape, shape_type)
    n = 0
    while explorer.More():
        n += 1
        explorer.Next()
    return n


def _bounding_box(shape: TopoDS_Shape) -> tuple[float, float, float] | None:
    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    if bbox.IsVoid():
        return None
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    return (xmax - xmin, ymax - ymin, zmax - zmin)


def load_step(file_path: str | Path, heal: bool = True) -> LoadedPart:
    """Loads a STEP file and returns the shape together with its provenance.

    Args:
        file_path: Path to the .step or .stp file.
        heal: Run ShapeFix on the imported shape to repair minor defects
            (small gaps, wrong orientations) that would otherwise corrupt
            thickness and draft measurements.

    Returns:
        A LoadedPart carrying the shape in millimetres plus the metadata
        needed to judge how much the measurements can be trusted.

    Raises:
        FileNotFoundError: If the file does not exist.
        IOError: If the file cannot be read or parsed.
        ValueError: If the file contains no usable shape.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"STEP file not found at path: {file_path}")

    warnings: list[str] = []

    # Pin the target unit rather than relying on the process-wide default,
    # which any other OCCT caller could have changed.
    Interface_Static.SetCVal("xstep.cascade.unit", "MM")
    source_units = detect_declared_units(file_path)

    reader = STEPControl_Reader()
    status = reader.ReadFile(str(file_path))
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise IOError(f"Error reading STEP file: {file_path}. Status: {status}")

    if not reader.TransferRoots():
        raise IOError(f"Error transferring roots from STEP file: {file_path}")

    root_count = reader.NbRootsForTransfer()
    shape = reader.OneShape()
    if shape is None or shape.IsNull():
        raise ValueError(f"No valid shape found in STEP file: {file_path}")

    if root_count > 1:
        warnings.append(
            f"File contains {root_count} roots that were merged into one shape; "
            "thickness rays may cross between separate bodies."
        )

    was_healed = False
    if heal:
        analyzer = BRepCheck_Analyzer(shape)
        if not analyzer.IsValid():
            fixer = ShapeFix_Shape(shape)
            fixer.Perform()
            fixed = fixer.Shape()
            if fixed is not None and not fixed.IsNull():
                shape = fixed
                was_healed = True
                warnings.append(
                    "Imported geometry was invalid and has been healed with "
                    "ShapeFix; verify measurements against the source CAD."
                )

    solid_count = _count(shape, TopAbs_SOLID)
    shell_count = _count(shape, TopAbs_SHELL)
    face_count = _count(shape, TopAbs_FACE)
    is_valid_solid = solid_count > 0 and BRepCheck_Analyzer(shape).IsValid()

    if solid_count == 0:
        warnings.append(
            "No closed solid found (surface or shell model). Wall-thickness "
            "measurements assume a closed solid and should be treated as "
            "indicative only."
        )
    if solid_count > 1:
        warnings.append(
            f"{solid_count} separate solids found; this looks like an assembly. "
            "Rules are written for single parts."
        )

    bbox = _bounding_box(shape)
    if bbox and max(bbox) > 0:
        # A part measured in metres or inches but read as millimetres shows up
        # as an implausible overall size long before any rule is evaluated.
        largest = max(bbox)
        if largest < 1.0:
            warnings.append(
                f"Overall size is only {largest:.3f} mm; the file may declare "
                "the wrong unit."
            )
        elif largest > 5000.0:
            warnings.append(
                f"Overall size is {largest:.0f} mm; the file may declare the "
                "wrong unit."
            )

    logging.info(
        f"Loaded {file_path.name}: {face_count} faces, {solid_count} solid(s), "
        f"declared units '{source_units}', healed={was_healed}"
    )

    return LoadedPart(
        shape=shape,
        source_units=source_units,
        solid_count=solid_count,
        shell_count=shell_count,
        face_count=face_count,
        is_valid_solid=is_valid_solid,
        was_healed=was_healed,
        bounding_box_mm=bbox,
        warnings=warnings,
    )


def load_step_file(file_path: str | Path) -> TopoDS_Shape:
    """Backwards-compatible helper returning only the shape."""
    return load_step(file_path).shape

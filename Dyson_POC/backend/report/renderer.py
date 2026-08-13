"""renderer.py - Builds the analysis report as HTML, and as PDF from that HTML.

One template produces both. The browser view and the PDF are the same markup
rendered by different engines, so they cannot drift apart and there is a single
place to change the design. That is the whole reason for choosing an HTML-to-PDF
converter over a drawing library: a drawing library would mean maintaining the
layout twice.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from report import charts

TEMPLATE_DIR = Path(__file__).parent / "templates"
RULES_PATH = Path(__file__).parent.parent / "rules_catalog.json"

# Charts of measurement against limit are only worth drawing for rules with
# enough measured faces to show a distribution.
MIN_VALUES_FOR_DISTRIBUTION = 3
MAX_DISTRIBUTION_CHARTS = 3

_LEADING_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

_environment = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

_rules_cache: Optional[dict] = None


def _rules_by_id() -> dict:
    global _rules_cache
    if _rules_cache is None:
        try:
            with RULES_PATH.open(encoding="utf-8") as fh:
                _rules_cache = {r["rule_id"]: r for r in json.load(fh)}
        except Exception as exc:
            logging.error(f"Could not load the rules catalog for the report: {exc}")
            _rules_cache = {}
    return _rules_cache


def _measured_value(measured: Optional[str]) -> Optional[float]:
    """Pulls the leading number out of a formatted measurement.

    Findings store a display string ("0.820 mm", "1.20-3.40 mm", "2.000x") so
    the table needs no formatting logic. Charting needs the number back; for a
    range the first value is the worst case, which is the one the rule turned on.
    """
    if not measured:
        return None
    match = _LEADING_NUMBER.search(measured)
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _unit_of(measured: Optional[str]) -> str:
    if not measured:
        return ""
    if "°" in measured:
        return "deg"
    if "×" in measured:
        return "ratio"
    if "mm" in measured:
        return "mm"
    return ""


def _distribution_charts(findings: list[dict]) -> list[str]:
    """Draws measurement-against-limit for the rules that carry real numbers.

    Failing rules come first: seeing how far outside the band a part sits is
    most useful where it is actually outside.
    """
    grouped: dict[str, list[dict]] = {}
    for finding in findings:
        if finding.get("status") not in ("NON-COMPLIANT", "COMPLIANT", "NEEDS_REVIEW"):
            continue
        if _measured_value(finding.get("measured")) is None:
            continue
        grouped.setdefault(finding["rule_id"], []).append(finding)

    rules = _rules_by_id()
    candidates = []
    for rule_id, group in grouped.items():
        if len(group) < MIN_VALUES_FOR_DISTRIBUTION:
            continue
        predicate = (rules.get(rule_id) or {}).get("predicate") or {}
        lower = upper = None
        if predicate.get("type") == "range":
            lower, upper = predicate.get("min"), predicate.get("max")
        elif predicate.get("operator") in (">=", ">"):
            lower = predicate.get("threshold")
        elif predicate.get("operator") in ("<=", "<"):
            upper = predicate.get("threshold")
        if lower is None and upper is None:
            continue

        failing = sum(1 for f in group if f["status"] == "NON-COMPLIANT")
        candidates.append((failing, len(group), rule_id, group, lower, upper))

    candidates.sort(key=lambda c: (-c[0], -c[1]))

    rendered = []
    for _, _, rule_id, group, lower, upper in candidates[:MAX_DISTRIBUTION_CHARTS]:
        values = [_measured_value(f.get("measured")) for f in group]
        svg = charts.measurement_against_limit(
            findings=group,
            rule_id=rule_id,
            rule_name=group[0].get("rule_name", ""),
            unit=_unit_of(group[0].get("measured")),
            values=[v for v in values if v is not None],
            lower=lower,
            upper=upper,
        )
        if svg:
            rendered.append(svg)
    return rendered


def _sections_by_family(findings: list[dict], classification: Optional[dict]) -> list[dict]:
    """Splits a composite analysis into one section per rule set.

    A part checked against six families produces one document, not six. But
    merging their findings into a single table would make it impossible to
    answer the question the reader actually has -- "what does this look like if
    it turns out to be machined?" -- so the findings are grouped by the
    catalogue that produced them, in the order the classifier ranked them.
    """
    by_family: dict[str, list[dict]] = {}
    for finding in findings:
        by_family.setdefault(finding.get("process_family") or "Unassigned", []).append(
            finding
        )

    if len(by_family) <= 1:
        return []

    # Ranked as the classifier ranked them, so the likeliest process leads.
    order = {
        c["process_family"]: index
        for index, c in enumerate((classification or {}).get("candidates", []))
    }

    sections = []
    for family, group in sorted(
        by_family.items(), key=lambda kv: (order.get(kv[0], 999), kv[0])
    ):
        counts: dict[str, int] = {}
        for finding in group:
            counts[finding["status"]] = counts.get(finding["status"], 0) + 1
        candidate = next(
            (
                c
                for c in (classification or {}).get("candidates", [])
                if c["process_family"] == family
            ),
            None,
        )
        sections.append(
            {
                "process_family": family,
                "confidence": candidate["confidence"] if candidate else None,
                "why": (candidate or {}).get("evidence_for", [])[:2],
                "counts": counts,
                "open": counts.get("NON-COMPLIANT", 0) + counts.get("ERROR", 0),
                "rows": _group_findings(group),
            }
        )
    return sections


def _group_findings(findings: list[dict]) -> list[dict]:
    """Collapses a rule's findings into one row per rule and status.

    A rule failing on thirty faces is one problem worth reading once, with the
    locations listed, rather than thirty rows to scroll past.
    """
    order = {"NON-COMPLIANT": 0, "ERROR": 1, "NEEDS_REVIEW": 2,
             "COMPLIANT": 3, "NOT_EVALUATED": 4}
    severity_order = {"critical": 0, "major": 1, "minor": 2}

    grouped: dict[tuple, dict] = {}
    for finding in findings:
        key = (finding.get("rule_id"), finding.get("status"))
        row = grouped.get(key)
        if row is None:
            row = {
                "rule_id": finding.get("rule_id"),
                "rule_name": finding.get("rule_name"),
                "status": finding.get("status"),
                "severity": finding.get("severity"),
                "guideline_ref": finding.get("guideline_ref"),
                "reason": finding.get("reason"),
                "commentary": finding.get("agent_commentary"),
                "confidence": finding.get("agent_confidence"),
                "locations": [],
                "measurements": [],
            }
            grouped[key] = row
        # The feature name where geometry produced one, the face number where it
        # did not. A report reader has no way to act on "face 214".
        if finding.get("location") and finding["location"] != "part":
            row["locations"].append(
                finding.get("feature_label") or finding["location"]
            )
        if finding.get("measured"):
            row["measurements"].append(finding["measured"])
        # Carry the first commentary that exists for the group.
        if not row["commentary"] and finding.get("agent_commentary"):
            row["commentary"] = finding["agent_commentary"]
            row["confidence"] = finding.get("agent_confidence")

    rows = []
    for row in grouped.values():
        # Counted by distinct feature, not by face, so it agrees with the list
        # beside it: one bore split into four faces is one place to go and fix.
        row["count"] = max(len(set(row["locations"])), 1)
        row["location_summary"] = _summarise(row["locations"])
        row["measurement_summary"] = _summarise_measurements(row["measurements"])
        rows.append(row)

    rows.sort(key=lambda r: (
        order.get(r["status"], 9),
        severity_order.get((r["severity"] or "").lower(), 9),
        r["rule_id"] or "",
    ))
    return rows


def _summarise(locations: list[str], limit: int = 6) -> str:
    """Lists where a rule applied, without repeating one feature many times.

    Several faces of one bore carry the same name, so de-duplicating first turns
    "Ø5.00 mm hole, front left, Ø5.00 mm hole, front left, ..." back into the one
    hole it actually is.
    """
    if not locations:
        return "part"
    unique = list(dict.fromkeys(locations))
    if len(unique) <= limit:
        return ", ".join(unique)
    return f"{', '.join(unique[:limit])} and {len(unique) - limit} more"


def _summarise_measurements(measurements: list[str]) -> str:
    if not measurements:
        return ""
    unique = list(dict.fromkeys(measurements))
    if len(unique) == 1:
        return unique[0]
    numeric = [(_measured_value(m), m) for m in unique]
    numeric = [(v, m) for v, m in numeric if v is not None]
    if not numeric:
        return unique[0]
    lowest = min(numeric)[1]
    highest = max(numeric)[1]
    return f"{lowest} to {highest}"


def build_context(analysis: dict) -> dict:
    """Assembles everything the template needs from a stored analysis."""
    findings = analysis.get("findings") or []
    coverage = analysis.get("coverage") or {}
    summary = analysis.get("summary")
    metadata = analysis.get("part_metadata") or {}

    counts: dict[str, int] = {}
    for finding in findings:
        status = finding.get("status", "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1

    decided = counts.get("COMPLIANT", 0) + counts.get("NON-COMPLIANT", 0)
    compliance = (
        round(100 * counts.get("COMPLIANT", 0) / decided) if decided else None
    )

    created = analysis.get("created_at")
    try:
        created_display = datetime.fromisoformat(created).strftime(
            "%d %B %Y at %H:%M"
        )
    except (TypeError, ValueError):
        created_display = created or ""

    return {
        "analysis": analysis,
        "generated_at": datetime.now().strftime("%d %B %Y at %H:%M"),
        "created_display": created_display,
        "metadata": metadata,
        "summary": summary,
        "coverage": coverage,
        "counts": counts,
        "decided": decided,
        "compliance": compliance,
        "open_findings": counts.get("NON-COMPLIANT", 0) + counts.get("ERROR", 0),
        "rows": _group_findings(findings),
        "classification": analysis.get("classification"),
        "sections": _sections_by_family(findings, analysis.get("classification")),
        "validation_issues": analysis.get("validation_issues") or [],
        "charts": {
            "status": charts.status_breakdown(findings),
            "severity": charts.severity_strip(findings),
            "coverage": charts.coverage_bar(coverage),
            "rules": charts.top_rules(findings),
            "distributions": _distribution_charts(findings),
        },
    }


def render_html(analysis: dict, for_pdf: bool = False) -> str:
    context = build_context(analysis)
    context["for_pdf"] = for_pdf
    return _environment.get_template("report.html.j2").render(**context)


def render_pdf(analysis: dict) -> bytes:
    """Renders the same template through WeasyPrint.

    Imported lazily: WeasyPrint pulls in native libraries, and an environment
    without them should still be able to serve the HTML report rather than
    failing to start.
    """
    from weasyprint import HTML

    document = render_html(analysis, for_pdf=True)
    return HTML(string=document).write_pdf()


def pdf_available() -> tuple[bool, Optional[str]]:
    """Whether PDF rendering can run here, and why not if it cannot."""
    try:
        import weasyprint  # noqa: F401
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, None

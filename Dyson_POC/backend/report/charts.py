"""charts.py - Chart rendering for the analysis report.

Every chart is hand-built SVG rather than a plotting library or a JavaScript
component, for one reason: the same markup has to work in the browser view and
inside the PDF. A JS chart renders in neither PDF nor a static file, and a
raster image from a plotting library goes blurry when the PDF is zoomed or
printed. Inline SVG is vector in print, styleable by the report's own tokens,
and needs no runtime.

The charts are deliberately few. A count of findings rendered as a pie is
decoration; what an engineer reasons with is where a measurement sits relative
to the limit that governs it, and how much of the catalogue was actually
tested.
"""

import html
import math
from typing import Iterable, Optional, Sequence

# Kept in step with frontend/src/theme.js so the report, the interface and the
# PDF describe a status with the same colour.
INK = "#041E42"
INK_MUTED = "#5A6478"
INK_FAINT = "#8A90A0"
BORDER = "#E3DEEE"
SURFACE = "#F8F6FB"
VIOLET = "#440099"
VIOLET_TINT = "#F3EEFA"

STATUS_COLOURS = {
    "NON-COMPLIANT": ("#A6192E", "#F9E9EC"),
    "NEEDS_REVIEW": ("#8A6400", "#FCF4DE"),
    "COMPLIANT": ("#006B31", "#E6F3EC"),
    "NOT_EVALUATED": ("#565E6B", "#F1F2F5"),
    "ERROR": ("#A6192E", "#F9E9EC"),
}

STATUS_LABELS = {
    "NON-COMPLIANT": "Non-compliant",
    "NEEDS_REVIEW": "Needs review",
    "COMPLIANT": "Compliant",
    "NOT_EVALUATED": "Not evaluated",
    "ERROR": "Error",
}

SEVERITY_COLOURS = {
    "critical": "#A6192E",
    "major": "#8A6400",
    "minor": "#565E6B",
}

_FONT = (
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, "
    "Helvetica, Arial, sans-serif"
)
_MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _text(x, y, content, size=11, colour=INK_MUTED, anchor="start",
          weight="400", mono=False) -> str:
    family = _MONO if mono else _FONT
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" '
        f'font-size="{size}" fill="{colour}" text-anchor="{anchor}" '
        f'font-weight="{weight}">{_esc(content)}</text>'
    )


def _svg(width: int, height: int, body: str, title: str) -> str:
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'height="{height}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="{_esc(title)}" '
        f'style="max-width:{width}px;display:block">{body}</svg>'
    )


def status_breakdown(findings: Sequence[dict], width: int = 620) -> str:
    """A single stacked bar of finding statuses, most severe first.

    A stacked bar rather than a pie: the eye compares lengths far better than
    angles, and it keeps the severity ordering visible left to right.
    """
    order = ["NON-COMPLIANT", "ERROR", "NEEDS_REVIEW", "COMPLIANT", "NOT_EVALUATED"]
    counts = {s: 0 for s in order}
    for finding in findings:
        status = finding.get("status")
        if status in counts:
            counts[status] += 1
    total = sum(counts.values())
    if not total:
        return ""

    present = [(s, n) for s, n in counts.items() if n]
    bar_y, bar_h = 8, 30
    parts = [f'<rect x="0" y="{bar_y}" width="{width}" height="{bar_h}" '
             f'rx="5" fill="{SURFACE}"/>']

    x = 0.0
    for status, count in present:
        w = width * count / total
        colour, _ = STATUS_COLOURS[status]
        parts.append(
            f'<rect x="{x:.1f}" y="{bar_y}" width="{max(w, 1.2):.1f}" '
            f'height="{bar_h}" fill="{colour}"/>'
        )
        # Only label a segment wide enough to hold the number legibly.
        if w > 26:
            parts.append(
                _text(x + w / 2, bar_y + 20, count, size=12,
                      colour="#FFFFFF", anchor="middle", weight="700")
            )
        x += w

    legend_y = bar_y + bar_h + 22
    lx = 0.0
    for status, count in present:
        colour, _ = STATUS_COLOURS[status]
        label = f"{STATUS_LABELS[status]} ({count})"
        parts.append(
            f'<rect x="{lx:.1f}" y="{legend_y - 8}" width="9" height="9" '
            f'rx="2" fill="{colour}"/>'
        )
        parts.append(_text(lx + 14, legend_y, label, size=11, colour=INK_MUTED))
        lx += 15 + len(label) * 6.0

    return _svg(width, legend_y + 12, "".join(parts), "Findings by status")


def coverage_bar(coverage: dict, width: int = 620) -> str:
    """How much of the catalogue actually produced a verdict.

    This is the honesty chart. A report that looks complete when it tested a
    third of the rules is worse than one that shows the gap.
    """
    if not coverage:
        return ""

    evaluated = coverage.get("rules_evaluated", 0)
    review = coverage.get("rules_needing_review", 0)
    not_computable = coverage.get("rules_not_computable", 0)
    not_applicable = coverage.get("rules_not_applicable_to_geometry", 0)
    segments = [
        ("Produced a verdict", evaluated, "#006B31"),
        ("Needs judgement", review, "#8A6400"),
        ("Not applicable here", not_applicable, "#8A90A0"),
        ("No extractor yet", not_computable, "#565E6B"),
    ]
    total = sum(n for _, n, _ in segments)
    if not total:
        return ""

    bar_y, bar_h = 8, 26
    parts = [f'<rect x="0" y="{bar_y}" width="{width}" height="{bar_h}" '
             f'rx="5" fill="{SURFACE}"/>']
    x = 0.0
    for _, count, colour in segments:
        if not count:
            continue
        w = width * count / total
        parts.append(
            f'<rect x="{x:.1f}" y="{bar_y}" width="{max(w, 1.2):.1f}" '
            f'height="{bar_h}" fill="{colour}"/>'
        )
        if w > 24:
            parts.append(_text(x + w / 2, bar_y + 18, count, size=11,
                               colour="#FFFFFF", anchor="middle", weight="700"))
        x += w

    y = bar_y + bar_h + 22
    lx = 0.0
    for label, count, colour in segments:
        if not count:
            continue
        text = f"{label} ({count})"
        parts.append(f'<rect x="{lx:.1f}" y="{y - 8}" width="9" height="9" '
                     f'rx="2" fill="{colour}"/>')
        parts.append(_text(lx + 14, y, text, size=11, colour=INK_MUTED))
        lx += 15 + len(text) * 6.0

    pct = round(100 * evaluated / total) if total else 0
    parts.append(_text(0, y + 22,
                       f"{evaluated} of {total} applicable rules produced a "
                       f"verdict ({pct}%)",
                       size=11, colour=INK_FAINT))
    return _svg(width, y + 34, "".join(parts), "Rule coverage")


def top_rules(findings: Sequence[dict], limit: int = 8, width: int = 620) -> str:
    """The rules generating the most findings, worst status first.

    Answers "what is the pattern" — one rule failing across thirty faces is a
    systematic design decision, not thirty separate problems.
    """
    rank = {"NON-COMPLIANT": 0, "ERROR": 1, "NEEDS_REVIEW": 2}
    grouped: dict[tuple, dict] = {}
    for finding in findings:
        status = finding.get("status")
        if status not in rank:
            continue
        key = (finding.get("rule_id"), status)
        entry = grouped.setdefault(
            key,
            {
                "rule_id": finding.get("rule_id"),
                "name": finding.get("rule_name", ""),
                "status": status,
                "count": 0,
            },
        )
        entry["count"] += 1

    rows = sorted(
        grouped.values(), key=lambda r: (rank[r["status"]], -r["count"])
    )[:limit]
    if not rows:
        return ""

    label_w = 210
    plot_w = width - label_w - 44
    row_h, gap = 22, 7
    biggest = max(r["count"] for r in rows)

    parts = []
    y = 14
    for row in rows:
        colour, tint = STATUS_COLOURS[row["status"]]
        bar_w = max(plot_w * row["count"] / biggest, 2)
        name = row["name"]
        if len(name) > 30:
            name = name[:29] + "…"
        parts.append(_text(0, y + 14, row["rule_id"], size=10.5,
                           colour=colour, weight="700", mono=True))
        parts.append(_text(56, y + 14, name, size=11, colour=INK_MUTED))
        parts.append(f'<rect x="{label_w}" y="{y + 3}" width="{plot_w}" '
                     f'height="{row_h - 6}" rx="3" fill="{tint}"/>')
        parts.append(f'<rect x="{label_w}" y="{y + 3}" width="{bar_w:.1f}" '
                     f'height="{row_h - 6}" rx="3" fill="{colour}"/>')
        parts.append(_text(label_w + plot_w + 8, y + 14, row["count"],
                           size=11, colour=INK, weight="700", mono=True))
        y += row_h + gap

    return _svg(width, y + 4, "".join(parts), "Findings by rule")


def measurement_against_limit(
    findings: Sequence[dict],
    rule_id: str,
    rule_name: str,
    unit: str,
    values: Sequence[float],
    lower: Optional[float] = None,
    upper: Optional[float] = None,
    width: int = 620,
) -> str:
    """Where the measured values sit relative to the rule's acceptable band.

    The chart an engineer actually reasons with. A count of failures says a
    rule was broken; this says by how much, and whether the part is marginal or
    nowhere near — which is the difference between a tolerance discussion and a
    redesign.
    """
    values = [v for v in values if v is not None and math.isfinite(v)]
    if not values:
        return ""

    span_lo = min(list(values) + ([lower] if lower is not None else []))
    span_hi = max(list(values) + ([upper] if upper is not None else []))
    if span_hi <= span_lo:
        span_hi = span_lo + 1.0
    pad = (span_hi - span_lo) * 0.12
    span_lo -= pad
    span_hi += pad

    left, right = 8, width - 8
    plot_w = right - left
    axis_y, track_h = 72, 34

    def to_x(value: float) -> float:
        return left + plot_w * (value - span_lo) / (span_hi - span_lo)

    parts = [_text(0, 14, f"{rule_id} · {rule_name}", size=11.5,
                   colour=INK, weight="700")]

    band_lo = to_x(lower) if lower is not None else left
    band_hi = to_x(upper) if upper is not None else right
    parts.append(f'<rect x="{left}" y="{axis_y - track_h}" width="{plot_w}" '
                 f'height="{track_h}" rx="4" fill="{SURFACE}"/>')
    parts.append(
        f'<rect x="{band_lo:.1f}" y="{axis_y - track_h}" '
        f'width="{max(band_hi - band_lo, 1):.1f}" height="{track_h}" rx="4" '
        f'fill="#E6F3EC"/>'
    )
    for bound in (lower, upper):
        if bound is None:
            continue
        bx = to_x(bound)
        parts.append(f'<line x1="{bx:.1f}" y1="{axis_y - track_h - 4}" '
                     f'x2="{bx:.1f}" y2="{axis_y + 4}" stroke="#006B31" '
                     f'stroke-width="1.5" stroke-dasharray="3 2"/>')
        parts.append(_text(bx, axis_y - track_h - 9, f"{bound:g}",
                           size=10, colour="#006B31", anchor="middle",
                           weight="700", mono=True))

    # One mark per measured face. Low opacity so a cluster reads as density
    # rather than a single value, which is the point on a part with many faces.
    for value in values:
        inside = (lower is None or value >= lower) and (
            upper is None or value <= upper
        )
        colour = "#006B31" if inside else "#A6192E"
        parts.append(
            f'<circle cx="{to_x(value):.1f}" cy="{axis_y - track_h / 2:.1f}" '
            f'r="4" fill="{colour}" fill-opacity="0.5"/>'
        )

    parts.append(f'<line x1="{left}" y1="{axis_y + 6}" x2="{right}" '
                 f'y2="{axis_y + 6}" stroke="{BORDER}" stroke-width="1"/>')
    parts.append(_text(left, axis_y + 22, f"{span_lo:.2f}", size=10,
                       colour=INK_FAINT, mono=True))
    parts.append(_text(right, axis_y + 22, f"{span_hi:.2f} {unit}".strip(),
                       size=10, colour=INK_FAINT, anchor="end", mono=True))

    outside = sum(
        1 for v in values
        if (lower is not None and v < lower) or (upper is not None and v > upper)
    )
    caption = (
        f"{len(values)} measured · {outside} outside the limit"
        if outside else f"{len(values)} measured · all within the limit"
    )
    parts.append(_text(left, axis_y + 38, caption, size=11, colour=INK_MUTED))

    return _svg(width, axis_y + 50, "".join(parts),
                f"{rule_name} against its limit")


def severity_strip(findings: Sequence[dict], width: int = 620) -> str:
    """Open findings by severity — what to fix first."""
    counts = {"critical": 0, "major": 0, "minor": 0}
    for finding in findings:
        if finding.get("status") not in ("NON-COMPLIANT", "ERROR"):
            continue
        severity = (finding.get("severity") or "minor").lower()
        if severity in counts:
            counts[severity] += 1
    total = sum(counts.values())
    if not total:
        return ""

    x, y, h = 0.0, 6, 22
    parts = []
    for severity in ("critical", "major", "minor"):
        count = counts[severity]
        if not count:
            continue
        w = width * count / total
        parts.append(f'<rect x="{x:.1f}" y="{y}" width="{max(w, 1.2):.1f}" '
                     f'height="{h}" fill="{SEVERITY_COLOURS[severity]}"/>')
        if w > 60:
            parts.append(_text(x + w / 2, y + 15, f"{severity} {count}",
                               size=10.5, colour="#FFFFFF", anchor="middle",
                               weight="700"))
        x += w
    return _svg(width, y + h + 6, "".join(parts), "Open findings by severity")

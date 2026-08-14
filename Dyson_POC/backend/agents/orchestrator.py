"""orchestrator.py - Agent flow controller.

Sequence per part (see docs/codegraph.md):
  1. ingest      (deterministic)   step_loader
  2. extract     (deterministic)   features.build_part_model
  3. execute     (deterministic)   pipeline.execute_rules -> Findings
  4. interpret   (LLM)             this module: enrich NEEDS_REVIEW findings only
  5. validate    (deterministic+LLM) cross-checks, conflict flags
  6. report      (deterministic)   run_poc.write_excel

Invariant (AGENTS.md #1): this module reads Findings; it never creates,
mutates, or overturns a verdict. It only fills commentary on findings that
need review and attaches recommendations. The schemas below enforce that
structurally -- there is no field a model could use to express a verdict, so a
model that tried to overturn one would have nowhere to put it.

Findings are enriched one request per *rule*, not per finding, and those
requests are batched. A rule needing review on 161 faces is one engineering
question asked 161 times -- the advice is identical -- so it is asked once and
the answer is applied to all of them. That is both far cheaper and better
output: the model is told the rule turned on 161 faces and writes about the
pattern, which is what an engineer needs to read and what the report shows
anyway, since it groups findings by rule.
"""

import json
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from models import Finding, PartModelFace

PROMPT_DIR = Path(__file__).parent.parent / "prompts"

# Findings per request. Deliberately small, for two reasons that both showed up
# in testing at 25:
#
# Output length, not context, is the binding constraint. Twenty-four
# commentaries of two or three sentences each overran the response token
# ceiling, and the JSON truncated mid-string -- costing every commentary in the
# batch, not just the overflow. A batch this size cannot approach the ceiling.
#
# Latency is dominated by tokens generated, so several small batches issued
# concurrently finish far sooner than one large batch generated serially.
BATCH_SIZE = 8

# Concurrent commentary requests. Enough to cover a typical part in one wave
# without opening an unreasonable number of sockets on a large one.
MAX_CONCURRENT_BATCHES = 4

# Only this many findings are described to the summary agent. They are chosen
# worst-first, so the summary sees every failure before it sees any advisory.
SUMMARY_FINDING_LIMIT = 60


COMMENTARY_SCHEMA = {
    "type": "object",
    "properties": {
        "commentaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "commentary": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["index", "commentary", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["commentaries"],
    "additionalProperties": False,
}


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "assessment": {"type": "string"},
        "key_risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "recommendation": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "major", "minor"],
                    },
                },
                "required": [
                    "title",
                    "why_it_matters",
                    "recommendation",
                    "severity",
                ],
                "additionalProperties": False,
            },
        },
        "coverage_note": {"type": "string"},
    },
    "required": ["headline", "assessment", "key_risks", "coverage_note"],
    "additionalProperties": False,
}


SYSTEM_PROMPT = (
    "You are a Design for Manufacturability reviewer embedded in an automated "
    "geometry-checking pipeline. The pipeline measures parts and owns every "
    "compliance verdict; you supply engineering interpretation only. Never "
    "state that a feature passes or fails, and never invent a measurement that "
    "was not provided to you. Respond only with JSON matching the requested "
    "schema."
)


def load_prompt(name: str, **kw) -> str:
    tpl = (PROMPT_DIR / name).read_text()
    for k, v in kw.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl


def _span_payload(group: list[Finding]) -> dict:
    """How far a rule reaches, for commentary written to cover the whole group.

    Without this the model describes the one face it was shown, and that
    description is then attached to every other face the rule turned on -- text
    that reads as specific while being true of only one of them.
    """
    if len(group) < 2:
        return {}

    features = [f.feature_label or f.location for f in group if f.location != "part"]
    measured = [f.measured for f in group if f.measured]
    span: dict = {"applies_to_count": len(group)}
    if features:
        unique = list(dict.fromkeys(features))
        span["applies_to"] = unique[:6]
        if len(unique) > 6:
            span["applies_to_more"] = len(unique) - 6
    if measured:
        unique_m = list(dict.fromkeys(measured))
        span["measured_range"] = (
            unique_m[0] if len(unique_m) == 1 else f"{min(unique_m)} to {max(unique_m)}"
        )
    return span


def _finding_payload(
    index: int, finding: Finding, group: Optional[list] = None
) -> dict:
    """Describes one finding to the model.

    Carries the measurement and the reason, not just the rule name. Commentary
    written from a rule name alone is generic by construction -- that was the
    defect in the original mock, and no amount of prompt tuning fixes it if the
    numbers never reach the model.
    """
    payload = {
        "index": index,
        "rule_id": finding.rule_id,
        "rule_name": finding.rule_name,
        "status": finding.status,
        "location": finding.location,
    }
    # The feature's name, where geometry could derive one. Given to the model so
    # commentary can say "the Ø5.00 mm hole, front left" instead of "face 214" --
    # the same fact, in the terms the reader works in. It is a measured
    # description, not a licence to speculate about the feature's purpose.
    if finding.feature_label:
        payload["feature"] = finding.feature_label
    if finding.measured:
        payload["measured"] = finding.measured
    if finding.measurement_point:  # only add when we actually have it
        pt = finding.measurement_point
        payload["measurement_point"] = {"x": pt[0], "y": pt[1], "z": pt[2]}
    if finding.severity:
        payload["severity"] = finding.severity
    if finding.category:
        payload["category"] = finding.category
    if finding.reason:
        payload["note"] = finding.reason
    if finding.guideline_ref:
        payload["guideline"] = finding.guideline_ref
    if group:
        payload.update(_span_payload(group))
    return payload


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def enrich_review_findings(
    findings: list[Finding],
    part_context: dict,
    llm=None,
    llm_call: Optional[Callable[[str], str]] = None,
) -> list[Finding]:
    """Attaches commentary to the findings that need engineering judgement.

    Args:
        findings: All findings from the execute step.
        part_context: Measured part-level context passed to the model.
        llm: An `llm.LLMClient`. If it is unavailable (no API key) or a request
            fails, findings keep their deterministic content and no commentary
            is invented for them.
        llm_call: Legacy single-prompt callable, retained so existing tests and
            callers that inject a stub keep working.

    Only NEEDS_REVIEW findings are sent. A measured finding already has a
    deterministic verdict and needs no interpretation; a NOT_EVALUATED one
    carries a reason instead, and asking a model to comment on a rule that
    never ran invites it to invent the context it is missing.
    """
    targets = [f for f in findings if f.status == "NEEDS_REVIEW"]
    if not targets:
        return findings

    if llm is None or not getattr(llm, "is_available", False):
        if llm_call is not None:
            _enrich_with_legacy_callable(targets, part_context, llm_call)
        else:
            logging.info(
                f"No language model available; {len(targets)} finding(s) left "
                "without commentary."
            )
        return findings

    # One request per rule, not per finding. A rule that needs review on 161
    # faces is one engineering question asked 161 times: the geometry differs
    # only in which face it was measured on, and the advice is the same for all
    # of them. Sending each separately cost 161 request slots and produced 161
    # near-identical paragraphs.
    #
    # On a 306-face part checked against five process families this was the
    # difference between 45 sequential waves of requests and 6 -- minutes of
    # apparent hang against seconds. The commentary is also better for it: the
    # model is told the rule turned on 161 faces and writes about the pattern,
    # which is what the report shows anyway, since it groups findings by rule.
    groups = _group_for_commentary(targets)
    representatives = [group[0] for group in groups]
    logging.info(
        f"{len(targets)} finding(s) needing review collapse to {len(groups)} "
        f"distinct rule(s) for commentary."
    )

    by_representative = {id(group[0]): group for group in groups}
    batches = list(_chunks(representatives, BATCH_SIZE))
    workers = min(MAX_CONCURRENT_BATCHES, len(batches))

    # Each batch is independent, and the work is entirely network-bound, so
    # issuing them together turns a sum of round trips into roughly one.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for batch, result in zip(
            batches,
            pool.map(
                lambda b: _request_commentary(b, part_context, llm, by_representative),
                batches,
            ),
        ):
            _apply_commentary(batch, result)

    # Fan each rule's commentary back across every finding it was written for.
    # The representative already carries it; the rest of its group takes a copy.
    for group in groups:
        source = group[0]
        if not source.agent_commentary:
            continue
        for finding in group[1:]:
            finding.agent_commentary = source.agent_commentary
            finding.agent_confidence = source.agent_confidence

    return findings


def _group_for_commentary(targets: list[Finding]) -> list[list[Finding]]:
    """Groups findings that would receive the same commentary.

    Keyed on the rule and the catalogue it came from -- two families can ask
    related questions, and their answers are not interchangeable. Insertion
    order is preserved so the worst-ranked rule is still described first.
    """
    grouped: dict[tuple, list[Finding]] = {}
    for finding in targets:
        grouped.setdefault((finding.process_family, finding.rule_id), []).append(
            finding
        )
    return list(grouped.values())


def _request_commentary(
    batch: list[Finding],
    part_context: dict,
    llm,
    groups: Optional[dict] = None,
) -> Optional[dict]:
    """Asks for commentary on one batch of findings."""
    # Index within the batch, so the model never sees (and cannot mis-echo) an
    # index that maps outside the batch it was given.
    payload = [
        _finding_payload(i, f, (groups or {}).get(id(f))) for i, f in enumerate(batch)
    ]
    prompt = load_prompt(
        "interpretive_rule.md",
        process_family=part_context.get("process_family", "unspecified"),
        material=part_context.get("material") or "unspecified",
        part_context=json.dumps(part_context, indent=2, default=str),
        findings=json.dumps(payload, indent=2, default=str),
    )
    return llm.complete_json(
        SYSTEM_PROMPT, prompt, COMMENTARY_SCHEMA, "finding_commentary"
    )


def _apply_commentary(batch: list[Finding], result: Optional[dict]) -> None:
    """Writes one batch's commentary onto the findings it describes."""
    if not result:
        return

    for entry in result.get("commentaries", []):
        try:
            index = int(entry["index"])
            target = batch[index]
        except (KeyError, TypeError, ValueError, IndexError):
            logging.warning(f"Discarding commentary with a bad index: {entry}")
            continue

        commentary = str(entry.get("commentary", "")).strip()
        if not commentary:
            continue
        target.agent_commentary = commentary[:2000]
        try:
            target.agent_confidence = max(
                0.0, min(1.0, float(entry.get("confidence", 0.0)))
            )
        except (TypeError, ValueError):
            target.agent_confidence = None


def _enrich_with_legacy_callable(
    targets: list[Finding], part_context: dict, llm_call: Callable[[str], str]
) -> None:
    """One prompt per finding, for injected stubs and older callers.

    Mutates the findings in place; the caller returns the full list.
    """
    for finding in targets:
        prompt = load_prompt(
            "interpretive_rule.md",
            process_family=part_context.get("process_family", "unspecified"),
            material=part_context.get("material") or "unspecified",
            part_context=json.dumps(part_context, default=str),
            findings=json.dumps([_finding_payload(0, finding)], default=str),
        )
        raw = llm_call(prompt)
        try:
            out = json.loads(raw)
            # Constrained output: commentary + confidence only. A 'verdict'
            # key from the model is IGNORED by design.
            if "commentaries" in out:
                out = out["commentaries"][0]
            finding.agent_commentary = str(out.get("commentary", ""))[:2000]
            finding.agent_confidence = float(out.get("confidence", 0.0))
        except (json.JSONDecodeError, ValueError, KeyError, IndexError, TypeError):
            finding.agent_commentary = "agent-output-unparseable; kept for human review"
            finding.agent_confidence = 0.0


PROCESS_READING_SCHEMA = {
    "type": "object",
    "properties": {
        "reading": {"type": "string"},
        "caveat": {"type": "string"},
    },
    "required": ["reading", "caveat"],
    "additionalProperties": False,
}


def explain_classification(classification: dict, llm=None) -> Optional[dict]:
    """Turns the classifier's scores into a reading an engineer would recognise.

    The scoring is deterministic and stays that way: this writes prose about a
    conclusion already reached, exactly as the commentary agent writes prose
    about verdicts already reached. It cannot change which families are
    analysed, which is what stops a confident-sounding sentence from quietly
    removing a section of the report.

    Returns None when no model is configured or the request fails. The evidence
    sentences the classifier produced are readable on their own, so the section
    degrades to a list of measurements rather than disappearing.
    """
    if llm is None or not getattr(llm, "is_available", False):
        return None
    if not classification:
        return None

    # Only the families that were actually shortlisted. Handing over the ones
    # that scored zero invites the model to argue about processes nobody is
    # going to read a section for.
    candidates = [
        c
        for c in classification.get("candidates", [])
        if c.get("basis") == "detected" and c.get("score", 0) > 0
    ]
    if not candidates:
        return None

    notes = classification.get("notes") or []
    prompt = load_prompt(
        "process_reading.md",
        signals=json.dumps(
            {k: v["display"] for k, v in classification.get("signals", {}).items()},
            indent=2,
        ),
        candidates=json.dumps(candidates, indent=2, default=str),
        notes=("## Also worth knowing\n\n" + "\n\n".join(notes)) if notes else "",
    )

    result = llm.complete_json(
        SYSTEM_PROMPT, prompt, PROCESS_READING_SCHEMA, "process_reading"
    )
    if not result:
        return None

    return {
        "reading": str(result.get("reading", "")).strip(),
        "caveat": str(result.get("caveat", "")).strip() or None,
    }


def generate_executive_summary(
    findings: list[Finding],
    part_context: dict,
    coverage: dict,
    llm=None,
) -> Optional[dict]:
    """Writes the part-level summary that opens the report.

    One request for the whole part. This is the only place in the pipeline that
    sees every finding at once, which is what lets it say "the part was modelled
    with vertical walls throughout" instead of repeating one draft failure per
    face.

    Returns None when no model is configured or the request fails; the report
    then renders without a summary rather than with a fabricated one.
    """
    if llm is None or not getattr(llm, "is_available", False):
        return None

    ranked = _rank_findings_for_summary(findings)
    if not ranked:
        return None

    payload = [_finding_payload(i, f) for i, f in enumerate(ranked)]
    prompt = load_prompt(
        "executive_summary.md",
        process_family=part_context.get("process_family", "unspecified"),
        material=part_context.get("material") or "unspecified",
        part_context=json.dumps(part_context, indent=2, default=str),
        coverage=json.dumps(coverage, indent=2, default=str),
        findings=json.dumps(payload, indent=2, default=str),
    )

    # The model occasionally returns a response that satisfies the schema but
    # carries nothing -- empty strings and an empty risk list. That is not a
    # summary, and passing it on produces a report with a headed, bordered,
    # empty box where the assessment should be. One retry, then give up and let
    # the deterministic fallback render, which at least says something true.
    result = None
    for attempt in range(2):
        candidate = llm.complete_json(
            SYSTEM_PROMPT, prompt, SUMMARY_SCHEMA, "executive_summary"
        )
        if candidate and str(candidate.get("headline", "")).strip():
            result = candidate
            break
        if candidate:
            logging.warning(
                "Executive summary came back empty"
                + (" -- retrying." if attempt == 0 else " twice; falling back.")
            )
    if not result:
        return None

    risks = []
    for risk in result.get("key_risks", [])[:5]:
        if not isinstance(risk, dict):
            continue
        risks.append(
            {
                "title": str(risk.get("title", "")).strip(),
                "why_it_matters": str(risk.get("why_it_matters", "")).strip(),
                "recommendation": str(risk.get("recommendation", "")).strip(),
                "severity": str(risk.get("severity", "minor")).strip().lower(),
            }
        )

    return {
        "headline": str(result.get("headline", "")).strip(),
        "assessment": str(result.get("assessment", "")).strip(),
        "key_risks": risks,
        "coverage_note": str(result.get("coverage_note", "")).strip(),
    }


def _rank_findings_for_summary(findings: list[Finding]) -> list[Finding]:
    """Orders findings worst-first and caps the list.

    Deduplicated by (rule, status): a rule that failed on eight faces is one
    problem, and spending the budget on eight copies of it would crowd out a
    different failure entirely. The face count rides along so the summary can
    still say how widespread each one is.
    """
    status_rank = {"NON-COMPLIANT": 0, "ERROR": 1, "NEEDS_REVIEW": 2, "COMPLIANT": 3}
    severity_rank = {"critical": 0, "major": 1, "minor": 2}

    grouped: dict[tuple, list[Finding]] = defaultdict(list)
    for finding in findings:
        if finding.status == "NOT_EVALUATED":
            continue
        grouped[(finding.rule_id, finding.status)].append(finding)

    representatives = []
    for group in grouped.values():
        first = group[0]
        representative = first.model_copy()
        if len(group) > 1:
            locations = ", ".join(f.location for f in group[:6])
            if len(group) > 6:
                locations += ", ..."
            representative.location = f"{len(group)} locations ({locations})"
        representatives.append(representative)

    representatives.sort(
        key=lambda f: (
            status_rank.get(f.status, 9),
            severity_rank.get((f.severity or "").lower(), 9),
            f.rule_id,
        )
    )
    return representatives[:SUMMARY_FINDING_LIMIT]


def validate(findings: list[Finding], part_model: list[PartModelFace]) -> list[dict]:
    """Validation agent (deterministic part): flag internal conflicts.

    These checks exist to catch the failure mode where the engine is confidently
    wrong rather than merely incomplete: a verdict with no measurement behind
    it, a location that does not exist, or two rules disagreeing about the same
    face. A contradiction is a signal that rule scoping is wrong somewhere, and
    it should be visible in the report rather than left for the client to spot.
    """
    issues = []
    face_ids = {face.face_id for face in part_model}

    for f in findings:
        location = f.location
        if location.startswith("face"):
            try:
                face_id = int(location.split()[1])
                if face_id not in face_ids:
                    issues.append({"finding": f.rule_id, "issue": "unknown face id"})
            except (IndexError, ValueError):
                issues.append(
                    {"finding": f.rule_id, "issue": "malformed location string"}
                )
        elif location != "part":
            issues.append(
                {"finding": f.rule_id, "issue": f"unrecognised location '{location}'"}
            )

        if f.status == "NON-COMPLIANT" and f.measured is None:
            issues.append(
                {"finding": f.rule_id, "issue": "fail verdict without measurement"}
            )

        if f.status == "NOT_EVALUATED" and not f.reason:
            issues.append(
                {
                    "finding": f.rule_id,
                    "issue": "rule was not evaluated but gives no reason",
                }
            )

    issues.extend(_find_contradictions(findings))
    return issues


def _find_contradictions(findings: list[Finding]) -> list[dict]:
    """Flags faces where two rules in the same category disagree.

    Material-specific wall-thickness limits are alternatives, so if two of them
    reach opposite verdicts on the same face, more than one material's rules are
    being applied at once and the report cannot be trusted.
    """
    by_face_and_category: dict[tuple[str, str], list[Finding]] = defaultdict(list)
    for f in findings:
        if f.status in ("COMPLIANT", "NON-COMPLIANT") and f.category:
            by_face_and_category[(f.location, f.category)].append(f)

    issues = []
    for (location, category), group in by_face_and_category.items():
        verdicts = {f.status for f in group}
        if len(verdicts) > 1:
            rule_ids = sorted({f.rule_id for f in group})
            issues.append(
                {
                    "finding": ", ".join(rule_ids),
                    "issue": (
                        f"rules in category '{category}' disagree on {location}; "
                        "check material and feature scoping"
                    ),
                }
            )
    return issues

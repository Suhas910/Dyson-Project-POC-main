"""Tests for the interpretive layer.

Every test here uses a stub client — the suite must never make a network call,
both so it runs offline and so a missing API key can never turn into a red
build. What is being tested is the wiring around the model: that batching maps
commentary back to the right finding, that failures degrade instead of raising,
and that the model cannot reach a compliance verdict.
"""

import json
import threading

import pytest

import agents.orchestrator as orchestrator
import llm as llm_module
from models import Finding


class StubLLM:
    """Stands in for `llm.LLMClient`, recording what it was asked."""

    def __init__(self, responses=None, available=True, fail=False):
        self._responses = list(responses or [])
        self._available = available
        self._fail = fail
        # Commentary batches are issued concurrently, so the stub is called
        # from several threads at once.
        self._lock = threading.Lock()
        self.prompts = []
        self.usage = llm_module.LLMUsage(model="stub", enabled=available)

    @property
    def is_available(self):
        return self._available

    def complete_json(self, system_prompt, user_prompt, schema, schema_name="response"):
        with self._lock:
            self.prompts.append(
                {
                    "system": system_prompt,
                    "user": user_prompt,
                    "schema": schema,
                    "name": schema_name,
                }
            )
            if self._fail or not self._responses:
                return None
            return self._responses.pop(0)


def _finding(rule_id="IM-011", status="NEEDS_REVIEW", **kwargs):
    defaults = {
        "rule_id": rule_id,
        "rule_name": "Standard draft for typical surfaces",
        "guideline_ref": "DFM-GUIDE-2.5.0",
        "status": status,
        "location": "face 1",
        "measured": "0.000°",
        "severity": "major",
        "category": "Draft",
    }
    defaults.update(kwargs)
    return Finding(**defaults)


PART_CONTEXT = {
    "part_name": "housing.step",
    "process_family": "Injection Moulding",
    "material": "ABS",
    "face_count": 11,
    "nominal_wall_thickness_mm": 2.0,
}


# --- Batching --------------------------------------------------------------


def test_all_findings_go_out_in_one_request():
    """One request for the whole set, not one per finding.

    Beyond cost, a single request is what lets the model see that eight faces
    failed the same rule and write about the pattern.
    """
    findings = [_finding(location=f"face {i}") for i in range(1, orchestrator.BATCH_SIZE + 1)]
    stub = StubLLM(
        [
            {
                "commentaries": [
                    {"index": i, "commentary": f"note {i}", "confidence": 0.5}
                    for i in range(orchestrator.BATCH_SIZE)
                ]
            }
        ]
    )

    orchestrator.enrich_review_findings(findings, PART_CONTEXT, llm=stub)

    assert len(stub.prompts) == 1
    assert all(f.agent_commentary for f in findings)


def test_large_sets_are_chunked():
    """Distinct rules, so nothing collapses -- this is about batch size alone."""
    count = orchestrator.BATCH_SIZE * 2 + 3
    findings = [
        _finding(rule_id=f"IM-{i:03d}", location=f"face {i}") for i in range(count)
    ]
    stub = StubLLM([{"commentaries": []} for _ in range(3)])

    orchestrator.enrich_review_findings(findings, PART_CONTEXT, llm=stub)

    assert len(stub.prompts) == 3


def test_commentary_lands_on_the_finding_it_describes():
    """The headline correctness risk in batching.

    An off-by-one here silently attributes one face's commentary to another —
    it looks perfectly plausible in the table and is wrong.
    """
    findings = [
        _finding(rule_id="IM-011", location="face 1"),
        _finding(rule_id="IM-029", location="face 2"),
        _finding(rule_id="IM-044", location="face 3"),
    ]
    stub = StubLLM(
        [
            {
                "commentaries": [
                    {"index": 2, "commentary": "third", "confidence": 0.3},
                    {"index": 0, "commentary": "first", "confidence": 0.9},
                    {"index": 1, "commentary": "second", "confidence": 0.6},
                ]
            }
        ]
    )

    orchestrator.enrich_review_findings(findings, PART_CONTEXT, llm=stub)

    assert findings[0].agent_commentary == "first"
    assert findings[1].agent_commentary == "second"
    assert findings[2].agent_commentary == "third"


def test_out_of_range_index_is_discarded_not_applied():
    findings = [_finding()]
    stub = StubLLM(
        [{"commentaries": [{"index": 99, "commentary": "bogus", "confidence": 1.0}]}]
    )

    orchestrator.enrich_review_findings(findings, PART_CONTEXT, llm=stub)

    assert findings[0].agent_commentary is None


# --- Scoping ---------------------------------------------------------------


def test_only_findings_needing_review_are_sent():
    """A measured finding has a deterministic verdict and needs no comment.

    A NOT_EVALUATED one carries a reason instead; asking a model to comment on
    a rule that never ran invites it to invent the context it is missing.
    """
    findings = [
        _finding(status="COMPLIANT"),
        _finding(status="NON-COMPLIANT"),
        _finding(status="NOT_EVALUATED", reason="no extractor"),
        _finding(status="NEEDS_REVIEW"),
    ]
    stub = StubLLM([{"commentaries": []}])

    orchestrator.enrich_review_findings(findings, PART_CONTEXT, llm=stub)

    payload = stub.prompts[0]["user"]
    assert payload.count('"status": "NEEDS_REVIEW"') == 1
    assert "COMPLIANT" not in payload.split("```json")[1]


def test_no_request_is_made_when_nothing_needs_review():
    findings = [_finding(status="COMPLIANT"), _finding(status="NON-COMPLIANT")]
    stub = StubLLM([{"commentaries": []}])

    orchestrator.enrich_review_findings(findings, PART_CONTEXT, llm=stub)

    assert stub.prompts == []


def test_measurements_reach_the_model():
    """Commentary quality is bounded by what the model can see.

    The original mock prompt passed only a part name and a face count, which
    makes generic commentary the best achievable outcome.
    """
    findings = [_finding(measured="0.820 mm")]
    stub = StubLLM([{"commentaries": []}])

    orchestrator.enrich_review_findings(findings, PART_CONTEXT, llm=stub)

    prompt = stub.prompts[0]["user"]
    assert "0.820 mm" in prompt
    assert "nominal_wall_thickness_mm" in prompt
    assert "ABS" in prompt


# --- Degradation -----------------------------------------------------------


def test_unavailable_model_leaves_findings_untouched():
    """No key must not mean no report — and must not mean invented commentary."""
    findings = [_finding()]

    result = orchestrator.enrich_review_findings(
        findings, PART_CONTEXT, llm=StubLLM(available=False)
    )

    assert result is findings
    assert findings[0].agent_commentary is None
    assert findings[0].status == "NEEDS_REVIEW"


def test_request_failure_does_not_raise():
    """A DFM finding that took real geometry to compute must survive a timeout."""
    findings = [_finding()]

    orchestrator.enrich_review_findings(findings, PART_CONTEXT, llm=StubLLM(fail=True))

    assert findings[0].agent_commentary is None
    assert findings[0].status == "NEEDS_REVIEW"


def test_confidence_is_clamped_to_its_range():
    # Distinct rules, so both findings are sent rather than one standing in for
    # the other -- the clamp is what is under test here, not the grouping.
    findings = [
        _finding(rule_id="IM-011", location="face 1"),
        _finding(rule_id="IM-012", location="face 2"),
    ]
    stub = StubLLM(
        [
            {
                "commentaries": [
                    {"index": 0, "commentary": "a", "confidence": 7.5},
                    {"index": 1, "commentary": "b", "confidence": -3},
                ]
            }
        ]
    )

    orchestrator.enrich_review_findings(findings, PART_CONTEXT, llm=stub)

    assert findings[0].agent_confidence == 1.0
    assert findings[1].agent_confidence == 0.0


# --- The verdict invariant -------------------------------------------------


def test_the_schema_gives_a_model_nowhere_to_put_a_verdict():
    """Structural enforcement of the never-overturn-a-verdict rule.

    A prompt instruction can be ignored; a strict schema with no verdict field
    and additionalProperties disabled cannot.
    """
    item = orchestrator.COMMENTARY_SCHEMA["properties"]["commentaries"]["items"]
    assert set(item["properties"]) == {"index", "commentary", "confidence"}
    assert item["additionalProperties"] is False

    summary_props = set(orchestrator.SUMMARY_SCHEMA["properties"])
    assert not summary_props & {"status", "verdict", "compliant", "pass_fail"}


def test_a_model_returning_a_verdict_cannot_change_one():
    findings = [_finding(status="NEEDS_REVIEW")]
    stub = StubLLM(
        [
            {
                "commentaries": [
                    {
                        "index": 0,
                        "commentary": "looks fine",
                        "confidence": 0.9,
                        "status": "COMPLIANT",
                        "verdict": "pass",
                    }
                ]
            }
        ]
    )

    orchestrator.enrich_review_findings(findings, PART_CONTEXT, llm=stub)

    assert findings[0].status == "NEEDS_REVIEW"
    assert findings[0].agent_commentary == "looks fine"


# --- Executive summary -----------------------------------------------------


SUMMARY_RESPONSE = {
    "headline": "Four walls have no draft and will not release from the tool.",
    "assessment": "The part was modelled with vertical walls throughout.",
    "key_risks": [
        {
            "title": "Zero draft on all vertical walls",
            "why_it_matters": "The part will scuff or stick on ejection.",
            "recommendation": "Add at least 1 degree of draft to every vertical face.",
            "severity": "critical",
        }
    ],
    "coverage_note": "19 of 70 injection moulding rules produced a verdict.",
}


def test_summary_is_one_request_for_the_whole_part():
    findings = [_finding(status="NON-COMPLIANT", location=f"face {i}") for i in range(8)]
    stub = StubLLM([SUMMARY_RESPONSE])

    summary = orchestrator.generate_executive_summary(
        findings, PART_CONTEXT, {"rules_evaluated": 19}, llm=stub
    )

    assert len(stub.prompts) == 1
    assert summary["headline"].startswith("Four walls")
    assert summary["key_risks"][0]["severity"] == "critical"


def test_summary_deduplicates_a_rule_that_failed_on_many_faces():
    """One problem, not eight.

    Sending eight copies of the same rule would crowd a different failure out
    of the budget entirely.
    """
    findings = [
        _finding(rule_id="IM-010", status="NON-COMPLIANT", location=f"face {i}")
        for i in range(8)
    ] + [_finding(rule_id="IM-001", status="NON-COMPLIANT", location="face 3")]
    stub = StubLLM([SUMMARY_RESPONSE])

    orchestrator.generate_executive_summary(findings, PART_CONTEXT, {}, llm=stub)

    payload = stub.prompts[0]["user"]
    assert payload.count('"rule_id": "IM-010"') == 1
    assert payload.count('"rule_id": "IM-001"') == 1
    assert "8 locations" in payload


def test_summary_ranks_failures_above_advisories():
    findings = [
        _finding(rule_id="IM-011", status="NEEDS_REVIEW", severity="minor"),
        _finding(rule_id="IM-010", status="NON-COMPLIANT", severity="critical"),
    ]
    stub = StubLLM([SUMMARY_RESPONSE])

    orchestrator.generate_executive_summary(findings, PART_CONTEXT, {}, llm=stub)

    payload = stub.prompts[0]["user"]
    assert payload.index("IM-010") < payload.index("IM-011")


def test_summary_is_absent_rather_than_fabricated_on_failure():
    findings = [_finding(status="NON-COMPLIANT")]

    assert (
        orchestrator.generate_executive_summary(
            findings, PART_CONTEXT, {}, llm=StubLLM(fail=True)
        )
        is None
    )
    assert (
        orchestrator.generate_executive_summary(
            findings, PART_CONTEXT, {}, llm=StubLLM(available=False)
        )
        is None
    )


# --- Client configuration --------------------------------------------------


def test_client_without_a_key_reports_itself_unavailable(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = llm_module.LLMClient(llm_module.LLMSettings(api_key=None))

    assert client.is_available is False
    assert client.usage.enabled is False
    # And it must be safe to call anyway.
    assert client.complete_json("s", "u", {"type": "object"}) is None


def test_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENROUTER_MODEL", "some/other-model")
    monkeypatch.setenv("OPENROUTER_REASONING_EFFORT", "high")

    settings = llm_module.LLMSettings.from_env()

    assert settings.api_key == "sk-or-test"
    assert settings.model == "some/other-model"
    assert settings.reasoning_effort == "high"
    assert settings.base_url == llm_module.DEFAULT_BASE_URL


def test_default_model_is_the_configured_deepseek_alias():
    assert llm_module.DEFAULT_MODEL == "~deepseek/deepseek-v4-flash-latest"


def test_bad_numeric_env_falls_back_instead_of_crashing(monkeypatch):
    """A typo in .env must not take the server down at import time."""
    monkeypatch.setenv("OPENROUTER_TIMEOUT_SECONDS", "not-a-number")
    monkeypatch.setenv("OPENROUTER_MAX_RETRIES", "")

    settings = llm_module.LLMSettings.from_env()

    assert settings.timeout_seconds == llm_module.LLMSettings.timeout_seconds
    assert settings.max_retries == llm_module.LLMSettings.max_retries


# --- Response parsing ------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Here is the result:\n{"a": 1}',
    ],
)
def test_json_survives_common_wrappers(raw):
    """Structured outputs should make these unnecessary — but a fenced or
    prefaced response should cost one finding's commentary, not the batch."""
    assert llm_module._extract_json(raw) == {"a": 1}


@pytest.mark.parametrize("raw", ["", "   ", "no json here", None])
def test_unparseable_responses_raise_for_the_caller_to_catch(raw):
    with pytest.raises(ValueError):
        llm_module._extract_json(raw)


# --- One request per rule, not per finding --------------------------------


def test_one_rule_on_many_faces_costs_one_request():
    """The fix for a 306-face part appearing to hang.

    A rule needing review on 161 faces is one engineering question asked 161
    times: the advice is identical, and sending each separately cost 161 request
    slots and produced 161 near-identical paragraphs.
    """
    findings = [_finding(location=f"face {i}") for i in range(161)]
    stub = StubLLM(
        [{"commentaries": [{"index": 0, "commentary": "Add draft.", "confidence": 0.8}]}]
    )

    orchestrator.enrich_review_findings(findings, PART_CONTEXT, llm=stub)

    assert len(stub.prompts) == 1


def test_every_finding_of_a_rule_receives_its_commentary():
    """Collapsing the requests must not collapse the results."""
    findings = [_finding(location=f"face {i}") for i in range(20)]
    stub = StubLLM(
        [{"commentaries": [{"index": 0, "commentary": "Add draft.", "confidence": 0.8}]}]
    )

    orchestrator.enrich_review_findings(findings, PART_CONTEXT, llm=stub)

    assert all(f.agent_commentary == "Add draft." for f in findings)
    assert all(f.agent_confidence == 0.8 for f in findings)


def test_the_model_is_told_how_many_faces_a_rule_covers():
    """Otherwise it describes one face, and that text is applied to all of them.

    The result would read as specific while being true of only one.
    """
    findings = [
        _finding(location=f"face {i}", feature_label=f"Side wall {i}")
        for i in range(30)
    ]
    stub = StubLLM([{"commentaries": []}])

    orchestrator.enrich_review_findings(findings, PART_CONTEXT, llm=stub)

    sent = stub.prompts[0]["user"]
    assert '"applies_to_count": 30' in sent


def test_rules_from_different_families_are_never_merged():
    """Two catalogues can ask related questions; the answers are not shared."""
    findings = [
        _finding(rule_id="X-001", process_family="Injection Moulding"),
        _finding(rule_id="X-001", process_family="Die Casting"),
    ]
    stub = StubLLM([{"commentaries": []}])

    orchestrator.enrich_review_findings(findings, PART_CONTEXT, llm=stub)

    sent = stub.prompts[0]["user"]
    assert sent.count('"rule_id": "X-001"') == 2

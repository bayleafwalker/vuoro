"""Supplied joins must not manufacture acceptance, capture or cheap outcomes."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
import socket

import pytest

from vuoro_evidence.experiment import ExperimentError, ExperimentRecord, population_digest
from vuoro_evidence.owner_outcome_intake import (
    SCHEMA_VERSION, SOURCE_VERIFICATION, intake_owner_outcome,
)
from vuoro_evidence.run import ObservedProfile, RunManifest


def case(case_id="c1", run_id="r1"):
    return {
        "schema_version": SCHEMA_VERSION, "case_id": case_id, "run_ids": [run_id],
        "owner_record_ref": "sprintctl:repo/7/item/12@immutable-revision",
        "evidence_refs": ["artifact:sha256:" + "a" * 64],
        "evidence_class": "synthetic-supplied-owner-join", "captured": True,
        "repository_id": "sprintctl", "item_id": 12,
        "item": {"repository_id": "sprintctl", "id": 12, "status": "done", "resolution": "accepted",
                 "terminal_decision_id": 22},
        "decision": {"repository_id": "sprintctl", "id": 22, "work_item_id": 12, "kind": "accept",
                     "release_digest": "a" * 64},
        "release": {"repository_id": "sprintctl", "work_item_id": 12, "release_digest": "a" * 64},
    }


def measurement(value=60, unit="seconds", **changes):
    return {"value": value, "unit": unit, "coverage": "complete",
            "source_ref": "owner:measurement@immutable1",
            "window": {"start": "2026-10-17T12:00:00Z", "end": "2026-10-17T12:05:00+00:00"},
            **changes}


def test_structurally_matching_supplied_acceptance_never_authenticates_sources(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("pure intake attempted network access")
    monkeypatch.setattr(socket, "socket", forbidden)
    source = case()
    original = deepcopy(source)
    result = intake_owner_outcome(source)
    assert result.join_status == "consistent"
    assert result.observation.disposition == "accepted"
    assert result.observation.owner_release_digest == "a" * 64
    assert result.observation.source_qualification is None
    assert result.source_verification == SOURCE_VERIFICATION == "not_performed_by_this_package"
    assert result.reason_codes == ()
    assert source == original
    source["decision"]["release_digest"] = "b" * 64
    assert result.supplied_records["decision"]["release_digest"] == "a" * 64
    assert any("not recomputed" in limit for limit in result.limitations)


@pytest.mark.parametrize("section,field,value,reason", [
    ("item", "id", 99, "item-identity-mismatch"),
    ("item", "repository_id", "other", "item-identity-mismatch"),
    ("decision", "id", 99, "terminal-decision-id-mismatch"),
    ("decision", "work_item_id", 99, "decision-item-mismatch"),
    ("decision", "repository_id", "other", "decision-item-mismatch"),
    ("decision", "kind", "reject", "decision-kind-resolution-mismatch"),
    ("release", "work_item_id", 99, "release-item-mismatch"),
    ("release", "repository_id", "other", "release-item-mismatch"),
    ("release", "release_digest", "b" * 64, "release-digest-mismatch"),
])
def test_contradictory_supplied_joins_are_unknown_but_still_captured(section, field, value, reason):
    source = case()
    source[section][field] = value
    result = intake_owner_outcome(source)
    assert result.join_status == "conflicting"
    assert reason in result.reason_codes
    assert result.observation.disposition == "unknown"
    assert result.observation.captured is True
    assert result.observation.run_ids == ("r1",)


@pytest.mark.parametrize("absent,reason", [("decision", "terminal-decision-missing"),
                                         ("release", "release-record-missing")])
def test_absent_terminal_join_is_represented_without_inventing_evidence(absent, reason):
    source = case()
    del source[absent]
    result = intake_owner_outcome(source)
    assert result.join_status == "incomplete"
    assert result.observation.disposition == "unknown"
    assert reason in result.reason_codes


@pytest.mark.parametrize("resolution,kind,reason", [
    ("legacy", None, "legacy-terminal-unqualified"),
    ("withdrawn", "withdraw", "terminal-disposition-unmapped"),
    ("superseded", "supersede", "terminal-disposition-unmapped"),
])
def test_other_terminal_dispositions_are_not_promoted(resolution, kind, reason):
    source = case()
    source["item"]["resolution"] = resolution
    if kind is None:
        source["item"]["terminal_decision_id"] = None
        source.pop("decision")
    else:
        source["decision"]["kind"] = kind
    result = intake_owner_outcome(source)
    assert result.observation.disposition == "unknown"
    assert reason in result.reason_codes


def test_open_and_rejected_records_keep_their_distinct_owner_meaning():
    source = case()
    source["item"].update(status="pending", resolution=None, terminal_decision_id=None)
    source.pop("decision")
    assert intake_owner_outcome(source).observation.disposition == "pending"
    source["item"]["terminal_decision_id"] = 22
    assert intake_owner_outcome(source).join_status == "conflicting"
    source = case()
    source["item"]["resolution"] = "rejected"
    source["decision"].update(kind="reject", release_digest=None)
    source.pop("release")  # Owner rejection need not reference a Release.
    assert intake_owner_outcome(source).observation.disposition == "rejected"


def test_measurement_coverage_and_provenance_control_scalar_mapping_not_join_status():
    source = case()
    source["measurements"] = {
        "review_seconds": measurement(0),
        "rework_seconds": measurement(30, coverage="partial"),
        "recovery_seconds": measurement(300, source_ref=None),
        "known_cost": measurement("0.10", "EUR"),
        "acceptance_seconds": measurement(200),
    }
    result = intake_owner_outcome(source)
    assert result.join_status == "consistent"
    assert result.observation.review_seconds == 0
    assert result.observation.rework_seconds is None
    assert result.observation.recovery_seconds is None
    assert result.observation.known_cost == Decimal("0.10")
    assert result.observation.currency == "EUR"
    assert result.observation.acceptance_seconds == 200
    assert set(result.reason_codes) == {"measurement-coverage-incomplete", "measurement-provenance-missing"}
    assert next(p for p in result.measurement_provenance if p["name"] == "rework_seconds")["supplied"]["value"] == 30
    source["decision"]["id"] = 99
    result = intake_owner_outcome(source)
    assert result.observation.acceptance_seconds is None
    assert result.observation.review_seconds == 0
    assert result.observation.known_cost == Decimal("0.10")
    assert "acceptance-measurement-unqualified" in result.reason_codes


@pytest.mark.parametrize("changes", [
    {"value": True}, {"value": -1}, {"unit": "minutes"},
    {"coverage": "estimated"}, {"source_ref": ""},
    {"window": {"start": "2026-10-17T12:00:00", "end": "2026-10-17T12:05:00"}},
    {"window": {"start": "2026-10-17T12:00:00Z", "end": "2026-10-17T11:00:00Z"}},
])
def test_malformed_measurements_fail_instead_of_disappearing(changes):
    source = case()
    source["measurements"] = {"review_seconds": measurement(**changes)}
    with pytest.raises(ExperimentError):
        intake_owner_outcome(source)


@pytest.mark.parametrize("value", [0.1, "NaN", "Infinity", "-1", True])
def test_cost_never_uses_lossy_or_negative_values(value):
    source = case()
    source["measurements"] = {"known_cost": measurement(value, "EUR")}
    with pytest.raises(ExperimentError):
        intake_owner_outcome(source)


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(repository_id=True),
    lambda c: c.update(captured=1),
    lambda c: c.update(run_ids=["r1", "r1"]),
    lambda c: c.update(evidence_refs=[]),
    lambda c: c.update(provider_completed=True),
    lambda c: c["item"].update(status="completed"),
    lambda c: c["decision"].update(release_digest="sha256:" + "a" * 64),
])
def test_malformed_envelopes_cannot_infer_acceptance_from_completion(mutation):
    source = case()
    mutation(source)
    with pytest.raises(ExperimentError):
        intake_owner_outcome(source)


def test_mixed_intake_keeps_unknown_cases_capture_cost_and_expected_population_separate():
    supplied = [case("c1", "r1"), case("c2", "r2")]
    supplied[0]["measurements"] = {"known_cost": measurement("0.10", "EUR")}
    supplied[1]["decision"]["id"] = 99
    supplied[1]["measurements"] = {"known_cost": measurement("0.20", "USD"),
                                    "recovery_seconds": measurement(300)}
    results = [intake_owner_outcome(c) for c in supplied]
    population = ("c1", "c2", "missing")
    record = ExperimentRecord(
        "exp", "recipe-rev1", "bounded", "owner:population@immutable1", population,
        tuple(r.observation for r in results),
        tuple(RunManifest(r, "native", "build1", "model1", "recipe-rev1", ObservedProfile("sha256:profile"))
              for r in ("r1", "r2")),
        tuple(dict.fromkeys(limit for r in results for limit in r.limitations)),
        revision_kind="recipe_id", population_sha256=population_digest(
            population_ref="owner:population@immutable1", task_group="bounded", expected_case_ids=population,
            revision_kind="recipe_id", revision_ref="recipe-rev1"),
        population_registered_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        population_registration_ref="owner:registration@immutable1")
    summary = record.summarize(min_accepted_cases=2, min_capture=Fraction(1),
                               review_policy_ref="owner:policy@immutable1", reviewer_ref="reviewer:1")
    assert summary["expected_cases"] == 3
    assert summary["observed_cases"] == 2
    assert summary["accepted_cases"] == 0
    assert summary["source_qualified_accepted_cases"] == 0
    assert summary["owner_reported_accepted_cases"] == 1
    assert "too_few_accepted_cases" in summary["inconclusive_reasons"]
    assert "source_qualification_incomplete" in summary["inconclusive_reasons"]
    assert summary["dispositions"]["unknown"] == 1
    assert summary["capture"]["captured_cases"] == 2
    assert summary["capture"]["unknown_cases"] == 1
    assert summary["population_burden"]["cost"] == {
        "known_totals": {"EUR": "0.10", "USD": "0.20"}, "known_cases": 2, "unknown_cases": 1}
    assert summary["accepted_cost"]["known_totals"] == {}
    assert summary["owner_reported_accepted_cost"]["known_totals"] == {"EUR": "0.10"}
    assert summary["population_burden"]["effort"]["recovery_seconds"] == {
        "known_total": 300, "known_cases": 1, "unknown_cases": 2}
    assert summary["comparison_readiness"] == "inconclusive"
    assert all(r.source_verification == SOURCE_VERIFICATION for r in results)


@pytest.mark.parametrize("control", ["\x00", "\x7f", "\x85", "\x9b"])
@pytest.mark.parametrize("target", ["owner_record_ref", "evidence_refs", "source_ref"])
def test_control_characters_are_rejected_in_supplied_references(control, target):
    source = case()
    if target == "source_ref":
        source["measurements"] = {"review_seconds": measurement(source_ref="owner:" + control)}
    elif target == "evidence_refs":
        source[target] = ["owner:" + control]
    else:
        source[target] += control
    with pytest.raises(ExperimentError, match="control characters"):
        intake_owner_outcome(source)


@pytest.mark.parametrize("coverage", ["partial", "unknown"])
def test_unknown_and_partial_coverage_remain_sidecars(coverage):
    source = case()
    source["measurements"] = {"review_seconds": measurement(coverage=coverage)}
    result = intake_owner_outcome(source)
    assert result.observation.review_seconds is None
    assert result.measurement_provenance[0]["supplied"]["coverage"] == coverage


def test_missing_window_is_not_inferred_from_other_timestamps():
    source = case()
    row = measurement()
    del row["window"]
    source["measurements"] = {"review_seconds": row}
    result = intake_owner_outcome(source)
    assert result.observation.review_seconds is None
    assert result.reason_codes == ("measurement-provenance-missing",)

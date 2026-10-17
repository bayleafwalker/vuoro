"""Learning must preserve missing capture/cost and owner acceptance boundaries."""
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
import json

import pytest

from vuoro_evidence.experiment import ExperimentError, ExperimentRecord, OutcomeObservation
from vuoro_evidence.run import ObservedProfile, RunManifest


def run(run_id="r1", model="model-a"):
    return RunManifest(run_id, "native", "immutable-build", model, "recipe-rev1",
                       ObservedProfile("sha256:observed-instructions"))


def outcome(case_id="c1", run_id="r1", **kwargs):
    return OutcomeObservation(case_id, (run_id,), "accepted", "owner:acceptance/1@rev1",
                              ("artifact:sha256:exact",), "independent_observation", True, **kwargs)


def record(observations=None, manifests=None, population=("c1", "c2")):
    return ExperimentRecord("exp1", "recipe:rev1", "bounded-household-task", "owner:population@rev1",
                            population, (outcome(),) if observations is None else observations,
                            (run(),) if manifests is None else manifests,
                            ("Supplied owner records are not authenticated here; selection and confounding remain.",))


def test_missing_population_is_not_zero_cost_or_success_and_capture_keeps_denominator():
    summary = record().summarize(min_accepted_cases=2, min_capture=Fraction(3, 4))
    assert summary["expected_cases"] == 2 and summary["accepted_cases"] == 1
    assert summary["missing_case_ids"] == ["c2"]
    assert summary["capture"] == {"captured_cases": 1, "expected_cases": 2,
                                  "uncaptured_cases": 0, "unknown_cases": 1}
    assert summary["accepted_cost"] == {"known_totals": {}, "known_cases": 0, "unknown_cases": 1}
    assert summary["accepted_effort"]["review_seconds"] == {
        "known_total": None, "known_cases": 0, "unknown_cases": 1}
    assert summary["comparison_readiness"] == "inconclusive"
    assert set(summary["inconclusive_reasons"]) == {"too_few_accepted_cases", "insufficient_capture"}


def test_rejected_and_pending_outcomes_never_become_acceptance():
    rejected = replace(outcome("c2", "r2"), disposition="rejected")
    pending = replace(outcome("c3", "r3"), disposition="pending", captured=None)
    summary = record((outcome(), rejected, pending), (run(), run("r2"), run("r3")),
                     ("c1", "c2", "c3")).summarize(min_accepted_cases=2, min_capture=Fraction(1, 2))
    assert summary["accepted_cases"] == 1
    assert summary["dispositions"]["rejected"] == 1
    assert summary["dispositions"]["pending"] == 1
    assert summary["inconclusive_reasons"] == ["too_few_accepted_cases"]
    assert [r["case_id"] for r in summary["accepted_evidence"]] == ["c1"]
    with pytest.raises(ExperimentError, match="completion"):
        replace(outcome(), disposition="completed")


def test_cost_is_exact_and_currency_is_never_silently_converted():
    observations = (outcome(known_cost=Decimal("0.10"), currency="EUR", review_seconds=0),
                    outcome("c2", "r2", known_cost=Decimal("0.20"), currency="USD"),
                    outcome("c3", "r3", known_cost=Decimal("0.20"), currency="EUR"))
    s = record(observations, (run(), run("r2"), run("r3")), ("c1", "c2", "c3")).summarize(
        min_accepted_cases=3, min_capture=Fraction(1))
    assert s["accepted_cost"]["known_totals"] == {"EUR": "0.30", "USD": "0.20"}
    assert s["accepted_effort"]["review_seconds"] == {"known_total": 0, "known_cases": 1, "unknown_cases": 2}
    assert s["comparison_readiness"] == "eligible_for_review"
    assert s["causal_attribution"] == "not_established"
    json.dumps(s)  # Portable summary contains no lossy floating-point amounts.


def test_run_identity_and_observed_composition_survive_summary_without_changing_manifests():
    manifest = run()
    before = manifest.composition()
    s = record(manifests=(manifest,), population=("c1",)).summarize(
        min_accepted_cases=1, min_capture=Fraction(1))
    assert s["run_compositions"]["r1"] == {"digest": manifest.composition_digest(), "composition": before}
    assert manifest.composition() == before and not manifest.grant_ids
    changed = replace(manifest, model_id="model-b")
    assert changed.composition_digest() != s["run_compositions"]["r1"]["digest"]


def test_retries_cannot_inflate_the_case_population():
    with pytest.raises(ExperimentError, match="distinct cases"):
        record((outcome(), outcome()), (run(),))
    with pytest.raises(ExperimentError, match="inflate"):
        record((outcome(), outcome("c2", "r1")), (run(),))
    with pytest.raises(ExperimentError, match="unique population"):
        record(population=("c1", "c1"))
    with pytest.raises(ExperimentError, match="unavailable RunManifest"):
        record(manifests=())
    with pytest.raises(ExperimentError, match="unreferenced"):
        record(manifests=(run(), run("unused")))


def test_empty_observation_window_remains_inconclusive_with_explicit_population():
    s = record((), ()).summarize(min_accepted_cases=1, min_capture=Fraction(0))
    assert s["accepted_cases"] == 0 and s["capture"]["unknown_cases"] == 2
    assert s["comparison_readiness"] == "inconclusive"


@pytest.mark.parametrize("field,value", [("known_cost", Decimal("NaN")), ("known_cost", Decimal("Infinity")),
    ("known_cost", Decimal("-1")), ("known_cost", 0.1), ("review_seconds", -1),
    ("review_seconds", True), ("review_seconds", 1.2), ("captured", 1)])
def test_invalid_measurement_refused(field, value):
    with pytest.raises(ExperimentError):
        replace(outcome(), **{field: value})


def test_cost_and_acceptance_require_their_own_qualifiers():
    with pytest.raises(ExperimentError):
        outcome(known_cost=Decimal(0))
    with pytest.raises(ExperimentError):
        outcome(currency="EUR")
    with pytest.raises(ExperimentError):
        replace(outcome(acceptance_seconds=10), disposition="pending")


@pytest.mark.parametrize("minimum,coverage", [(0, Fraction(1)), (True, Fraction(1)),
                                                (1, 0.5), (1, Fraction(2))])
def test_no_implicit_or_malformed_comparison_policy(minimum, coverage):
    with pytest.raises(ExperimentError):
        record().summarize(min_accepted_cases=minimum, min_capture=coverage)

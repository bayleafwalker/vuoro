"""Learning must preserve missing capture/cost and owner acceptance boundaries."""
from dataclasses import replace
from decimal import Decimal
from datetime import datetime, timezone
from fractions import Fraction
import json

import pytest

from vuoro_evidence.experiment import (ExperimentError, ExperimentRecord, OutcomeObservation,
                                      OwnerOutcomeQualification, population_digest, run_revision)
from vuoro_evidence.run import ObservedProfile, RunManifest


def run(run_id="r1", model="model-a"):
    return RunManifest(run_id, "native", "immutable-build", model, "recipe-rev1",
                       ObservedProfile("sha256:observed-instructions"))


def outcome(case_id="c1", run_id="r1", **kwargs):
    owner_ref = "owner:acceptance/1@rev1"
    release_digest = "a" * 64
    qualification = OwnerOutcomeQualification(
        case_id=case_id, owner_record_ref=owner_ref, status="qualified",
        qualification_ref=f"owner:qualification/{case_id}@rev1",
        source_snapshot_ref=f"owner:snapshot/{case_id}@immutable1",
        source_snapshot_sha256="sha256:" + "f" * 64,
        owner_revision_ref=f"sprintctl:item/{case_id}@revision1",
        release_digest=release_digest,
        independent_verification_ref=f"check:{case_id}@immutable1",
        actual_use_ref=f"use:{case_id}@immutable1",
    )
    return OutcomeObservation(case_id, (run_id,), "accepted", owner_ref,
                              ("artifact:sha256:exact",), "independent_observation", True,
                              owner_release_digest=release_digest,
                              source_qualification=qualification, **kwargs)


def record(observations=None, manifests=None, population=("c1", "c2")):
    return ExperimentRecord("exp1", "recipe-rev1", "bounded-household-task", "owner:population@rev1",
                            population, (outcome(),) if observations is None else observations,
                            (run(),) if manifests is None else manifests,
                            ("Supplied owner records are not authenticated here; selection and confounding remain.",),
                            revision_kind="recipe_id", population_sha256=population_digest(
                                population_ref="owner:population@rev1", task_group="bounded-household-task",
                                expected_case_ids=population, revision_kind="recipe_id", revision_ref="recipe-rev1"),
                            population_registered_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                            population_registration_ref="owner:registration@immutable1")


def test_missing_population_is_not_zero_cost_or_success_and_capture_keeps_denominator():
    summary = record().summarize(review_policy_ref="owner:review-policy@rev1", reviewer_ref="reviewer:1", min_accepted_cases=2, min_capture=Fraction(3, 4))
    assert summary["schema_version"] == "vuoro-experiment-summary/v2"
    assert summary["expected_cases"] == 2 and summary["accepted_cases"] == 1
    assert summary["source_qualified_accepted_cases"] == 1
    assert summary["missing_case_ids"] == ["c2"]
    assert summary["capture"] == {"captured_cases": 1, "expected_cases": 2,
                                  "uncaptured_cases": 0, "unknown_cases": 1}
    assert summary["accepted_cost"] == {"known_totals": {}, "known_cases": 0, "unknown_cases": 1}
    assert summary["accepted_effort"]["review_seconds"] == {
        "known_total": None, "known_cases": 0, "unknown_cases": 1}
    assert summary["comparison_readiness"] == "inconclusive"
    assert set(summary["inconclusive_reasons"]) == {
        "too_few_accepted_cases", "insufficient_capture"}


def test_rejected_and_pending_outcomes_never_become_acceptance():
    rejected = replace(outcome("c2", "r2"), disposition="rejected")
    pending = replace(outcome("c3", "r3"), disposition="pending", captured=None)
    summary = record((outcome(), rejected, pending), (run(), run("r2"), run("r3")),
                     ("c1", "c2", "c3")).summarize(review_policy_ref="owner:review-policy@rev1", reviewer_ref="reviewer:1", min_accepted_cases=2, min_capture=Fraction(1, 2))
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
    s = record(observations, (run(), run("r2"), run("r3")), ("c1", "c2", "c3")).summarize(review_policy_ref="owner:review-policy@rev1", reviewer_ref="reviewer:1",
        min_accepted_cases=3, min_capture=Fraction(1))
    assert s["accepted_cost"]["known_totals"] == {"EUR": "0.30", "USD": "0.20"}
    assert s["accepted_effort"]["review_seconds"] == {"known_total": 0, "known_cases": 1, "unknown_cases": 2}
    assert s["comparison_readiness"] == "eligible_for_review"
    assert s["causal_attribution"] == "not_established"
    json.dumps(s)  # Portable summary contains no lossy floating-point amounts.


def test_run_identity_and_observed_composition_survive_summary_without_changing_manifests():
    manifest = run()
    before = manifest.composition()
    s = record(manifests=(manifest,), population=("c1",)).summarize(review_policy_ref="owner:review-policy@rev1", reviewer_ref="reviewer:1",
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
    s = record((), ()).summarize(review_policy_ref="owner:review-policy@rev1", reviewer_ref="reviewer:1", min_accepted_cases=1, min_capture=Fraction(0))
    assert s["accepted_cases"] == 0 and s["capture"]["unknown_cases"] == 2
    assert s["comparison_readiness"] == "inconclusive"


def test_owner_reported_acceptance_without_qualification_is_not_eligible_for_review():
    unqualified = replace(outcome(), source_qualification=None)
    summary = record((unqualified,)).summarize(
        min_accepted_cases=1, min_capture=Fraction(0),
        review_policy_ref="owner:policy@rev1", reviewer_ref="reviewer:1")
    assert summary["accepted_cases"] == 0
    assert summary["source_qualified_accepted_cases"] == 0
    assert summary["owner_reported_accepted_cases"] == 1
    assert summary["comparison_readiness"] == "inconclusive"
    assert summary["inconclusive_reasons"] == [
        "too_few_accepted_cases", "source_qualification_incomplete"]
    assert summary["source_qualification"]["unqualified_case_ids"] == ["c1", "c2"]


def test_qualified_source_report_requires_immutable_verification_inputs():
    with pytest.raises(ExperimentError, match="immutable snapshot"):
        OwnerOutcomeQualification("c1", "owner:record@rev1", "qualified")
    with pytest.raises(ExperimentError, match="require a reason"):
        OwnerOutcomeQualification("c1", "owner:record@rev1", "stale")


@pytest.mark.parametrize("owner_digest,qualification_digest", [(None, None), ("a" * 64, None), (None, "a" * 64)])
def test_accepted_outcome_requires_a_matching_release_digest(owner_digest, qualification_digest):
    observation = outcome()
    qualification = replace(observation.source_qualification, release_digest=qualification_digest)
    observation = replace(observation, owner_release_digest=owner_digest,
                          source_qualification=qualification)
    assert observation.comparison_qualified is False
    assert "release-digest-missing" in observation.source_qualification_reasons


@pytest.mark.parametrize("status", [None, 1, {}])
def test_source_qualification_refuses_nontext_status(status):
    with pytest.raises(ExperimentError, match="qualification status"):
        OwnerOutcomeQualification("c1", "owner:record@rev1", status)


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
        record().summarize(review_policy_ref="owner:review-policy@rev1", reviewer_ref="reviewer:1", min_accepted_cases=minimum, min_capture=coverage)


def test_arm_binding_refuses_relabelled_or_mixed_run_revisions():
    with pytest.raises(ExperimentError, match="named revision arm"):
        record(manifests=(replace(run(), recipe_id="another-recipe"),))
    profile = run().observed_profile
    changed_profile = replace(profile, skill_digests=(("review", "sha256:changed"),))
    assert run_revision(run(), "observed_profile") != run_revision(
        replace(run(), observed_profile=changed_profile), "observed_profile")


def test_population_snapshot_prevents_silent_reselection_and_labels_unverified_registration():
    original = record()
    with pytest.raises(ExperimentError, match="bound snapshot digest"):
        replace(original, expected_case_ids=("c1", "different-case"))
    with pytest.raises(ExperimentError, match="aware"):
        replace(original, population_registered_at=datetime(2026, 10, 1))
    s = original.summarize(min_accepted_cases=1, min_capture=Fraction(1, 2),
                           review_policy_ref="owner:policy@immutable1", reviewer_ref="reviewer:human1")
    assert s["population_binding"]["sha256"] == original.population_sha256
    assert s["population_binding"]["registration_authenticity_and_pre_run_order"] == "not_verified_by_this_package"
    assert s["review_thresholds"]["review_policy_ref"] == "owner:policy@immutable1"
    assert s["review_thresholds"]["predeclaration"] == "not_verified_by_this_package"


def test_rejected_pending_and_missing_work_remain_in_population_burden():
    rejected = replace(outcome("c2", "r2", known_cost=Decimal("10.00"), currency="EUR",
                               review_seconds=60, rework_seconds=120), disposition="rejected")
    pending = replace(outcome("c3", "r3", known_cost=Decimal("3.00"), currency="USD",
                              recovery_seconds=300), disposition="pending")
    s = record((outcome(known_cost=Decimal("1.00"), currency="EUR"), rejected, pending),
               (run(), run("r2"), run("r3")), ("c1", "c2", "c3", "c4")).summarize(
        min_accepted_cases=1, min_capture=Fraction(1, 2), review_policy_ref="owner:policy@rev1", reviewer_ref="reviewer:1")
    assert s["accepted_cost"]["known_totals"] == {"EUR": "1.00"}
    assert s["population_burden"]["cost"] == {
        "known_totals": {"EUR": "11.00", "USD": "3.00"}, "known_cases": 3, "unknown_cases": 1}
    assert s["population_burden"]["effort"]["review_seconds"] == {
        "known_total": 60, "known_cases": 1, "unknown_cases": 3}
    assert s["burden_by_disposition"]["rejected"]["cost"]["known_totals"] == {"EUR": "10.00"}
    assert s["observations"][1]["owner_record_ref"] == rejected.owner_record_ref
    assert s["observations"][1]["known_cost"] == "10.00"
    assert s["observations"][2]["disposition"] == "pending"
    assert s["burden_by_disposition"]["pending"]["effort"]["recovery_seconds"]["known_total"] == 300


@pytest.mark.parametrize("currency", ["eur", "Euro", "", " EUR", "E1R"])
def test_currency_codes_do_not_split_or_conflate_units(currency):
    with pytest.raises(ExperimentError, match="currency"):
        outcome(known_cost=Decimal("1"), currency=currency)

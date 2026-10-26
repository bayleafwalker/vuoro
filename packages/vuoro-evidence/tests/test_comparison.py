"""Two-arm comparison keeps every case in the denominator and refuses thin evidence."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path

import pytest

from vuoro_evidence.comparison import CaseSet, ExperimentComparison, load_case_set
from vuoro_evidence.experiment import (ExperimentError, ExperimentRecord, OutcomeObservation,
                                       OwnerOutcomeQualification, population_digest, run_revision)
from vuoro_evidence.run import ObservedProfile, RunManifest

FIXTURE = Path(__file__).parent / "fixtures" / "case_set_synthetic.json"
CASES = load_case_set(FIXTURE)
POLICY = {"review_policy_ref": "owner:policy@rev1", "reviewer_ref": "reviewer:1"}


def run(run_id, instructions, model="model-a", recipe="recipe-1", harness_build="build-1"):
    return RunManifest(run_id, "native", harness_build, model, recipe, ObservedProfile(instructions))


_UNSET = object()


def qualified(case_id, owner_ref, release_digest, disposition):
    accepted = disposition == "accepted"
    return OwnerOutcomeQualification(
        case_id=case_id,
        owner_record_ref=owner_ref,
        status="qualified",
        qualification_ref=f"owner:qualification/{case_id}@rev1",
        source_snapshot_ref=f"owner:snapshot/{case_id}@immutable1",
        source_snapshot_sha256="sha256:" + "f" * 64,
        owner_revision_ref=f"sprintctl:item/{case_id}@revision1",
        release_digest=release_digest,
        independent_verification_ref=f"check:{case_id}@immutable1" if accepted else None,
        actual_use_ref=f"use:{case_id}@immutable1" if accepted else None,
    )


def obs(case_id, run_id, disposition="accepted", captured=True, **kwargs):
    owner_ref = f"owner:{case_id}@rev1"
    owner_release_digest = kwargs.pop("owner_release_digest", "a" * 64 if disposition == "accepted" else None)
    source_qualification = kwargs.pop("source_qualification", _UNSET)
    if source_qualification is _UNSET:
        source_qualification = qualified(case_id, owner_ref, owner_release_digest, disposition)
    return OutcomeObservation(case_id, (run_id,), disposition, f"owner:{case_id}@rev1",
                              ("artifact:sha256:x",), "independent_observation", captured,
                              owner_release_digest=owner_release_digest,
                              source_qualification=source_qualification, **kwargs)


def arm(name, rows, case_set=CASES, population=None, *, revision_kind="observed_profile",
        model="model-a", recipe="recipe-1", profile=None, harness_build="build-1"):
    """rows: {case_id: (disposition, captured, kwargs)}"""
    profile = profile or f"sha256:{name}-instructions"
    manifests = tuple(run(f"{name}-{c}", profile, model, recipe, harness_build) for c in rows)
    observations = tuple(obs(c, f"{name}-{c}", d, cap, **kw) for c, (d, cap, kw) in rows.items())
    revision = run("probe", profile, model, recipe, harness_build)
    rev = run_revision(revision, revision_kind)
    population = case_set.case_ids if population is None else population
    return ExperimentRecord(
        f"exp-{name}", rev, "bounded-task", case_set.ref, population, observations, manifests,
        ("Synthetic arm.",), revision_kind=revision_kind,
        population_sha256=population_digest(population_ref=case_set.ref, task_group="bounded-task",
                                            expected_case_ids=population, revision_kind=revision_kind,
                                            revision_ref=rev),
        population_registered_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        population_registration_ref="owner:registration@1")


A = ("accepted", True, {})


def comparison(base_rows, cand_rows, **kwargs):
    return ExperimentComparison("cmp-1", CASES, arm("base", base_rows), arm("cand", cand_rows),
                                ("Synthetic comparison.",), **kwargs)


def test_case_set_is_versioned_and_content_addressed():
    assert CASES.version == 1 and CASES.digest.startswith("sha256:")
    assert CASES.ref == f"case-set:synthetic-comparison-fixture@v1+{CASES.digest}"
    reordered = replace(CASES, cases=tuple(reversed(CASES.cases)))
    assert reordered.digest == CASES.digest
    edited = replace(CASES, cases=(replace(CASES.cases[0], workload="other"),) + CASES.cases[1:])
    assert edited.digest != CASES.digest
    assert replace(CASES, version=2).ref != CASES.ref
    assert CaseSet.from_dict(json.loads(json.dumps(CASES.as_dict()))) == CASES
    assert load_case_set(FIXTURE, expected_digest=CASES.digest) == CASES
    with pytest.raises(ExperimentError, match="pinned digest"):
        load_case_set(FIXTURE, expected_digest="sha256:" + "0" * 64)


@pytest.mark.parametrize("mutate,match", [
    (lambda b: b.update(schema_version="vuoro-case-set/v0"), "schema_version"),
    (lambda b: b.update(extra=1), "unexpected"),
    (lambda b: b["cases"][0].update(note="x"), "unexpected"),
    (lambda b: b["cases"][0].update(observed_on="20260903"), "YYYY-MM-DD"),
    (lambda b: b["cases"][0].update(observed_on="2026-02-30"), "calendar"),
    (lambda b: b["cases"][1].update(case_id="c1"), "unique"),
    (lambda b: b["cases"][0]["criteria"].update(kind="vibes"), "kind"),
    (lambda b: b["cases"][2]["criteria"].update(pass_score=11), "pass_score"),
    (lambda b: b["cases"][0]["criteria"].update(max_score=5), "cannot carry a score"),
    (lambda b: b.update(version=0), "version"),
    (lambda b: b.update(limitations=[]), "limitations"),
])
def test_malformed_case_set_refused(mutate, match):
    body = CASES.as_dict()
    mutate(body)
    with pytest.raises(ExperimentError, match=match):
        CaseSet.from_dict(body)


def test_full_paired_comparison_computes_exact_deltas_and_strata():
    base = {"c1": A, "c2": ("rejected", True, {"review_seconds": 60}),
            "c3": ("accepted", True, {"review_seconds": 30, "known_cost": Decimal("1.00"), "currency": "EUR"}),
            "c4": ("rejected", False, {})}
    cand = {"c1": A, "c2": ("accepted", True, {"review_seconds": 20}),
            "c3": ("accepted", True, {"review_seconds": 10, "known_cost": Decimal("0.40"), "currency": "EUR"}),
            "c4": ("accepted", True, {})}
    s = comparison(base, cand).compare(min_cases=2, min_coverage=Fraction(1, 2), **POLICY)
    assert s["status"] == "computed" and s["suppression_reasons"] == []
    assert s["schema_version"] == "vuoro-experiment-comparison/v2"
    assert s["comparison_readiness"] == "eligible_for_review"
    assert s["source_qualification"]["qualified_paired_cases"] == 4
    assert s["source_qualification"]["authentication"] == "not_performed_by_this_package"
    acc = s["metrics"]["acceptance_rate"]
    assert (acc["baseline_rate"], acc["candidate_rate"], acc["delta"]) == ("1/2", "1", "1/2")
    assert acc["delta_bounds"] == {"lower": "1/2", "upper": "1/2"}
    assert s["transitions"] == {"accepted->accepted": 2, "rejected->accepted": 2}
    review = s["metrics"]["review_seconds"]
    assert review["known_pairs"] == 2 and review["unknown_cases"] == 2
    assert review["delta_total"] == "-60" and review["delta_mean_per_known_pair"] == "-30"
    # Cost is paired in one currency only; one known pair is below min_cases.
    eur = s["metrics"]["known_cost"]["by_currency"]["EUR"]
    assert eur["status"] == "suppressed" and "known_measurements_below_minimum" in eur["suppression_reasons"]
    assert set(s["strata"]["workload"]) == {"code", "docs"}
    assert set(s["strata"]["observed_month"]) == {"2026-09", "2026-10", "unknown"}
    # Single-case month strata are suppressed by the same minimum.
    assert s["strata"]["observed_month"]["2026-10"]["status"] == "suppressed"
    assert s["strata"]["observed_month"]["2026-09"]["metrics"]["acceptance_rate"]["delta"] == "1/2"
    assert s["strata"]["coverage"]["one_arm_captured"]["cases"] == 1
    assert s["causal_attribution"] == "not_established"
    assert s["decision"] == "not_recorded_by_this_package"
    assert s["case_set"]["digest"] == CASES.digest
    json.dumps(s)


def test_failures_and_unknowns_stay_in_the_denominator():
    base = {"c1": A, "c2": A, "c3": ("rejected", True, {}), "c4": ("unknown", None, {})}
    cand = {"c1": A, "c2": ("pending", None, {}), "c3": ("rejected", False, {})}  # c4 missing
    s = comparison(base, cand).compare(min_cases=1, min_coverage=Fraction(1, 2), **POLICY)
    acc = s["metrics"]["acceptance_rate"]
    assert acc["denominator"] == 4
    assert acc["baseline"] == {"count": 2, "unresolved": 1}
    assert acc["candidate"] == {"count": 1, "unresolved": 2}
    assert acc["delta"] == "-1/4"
    # Unresolved cases widen the bounds instead of being dropped.
    assert acc["delta_bounds"] == {"lower": "-1/2", "upper": "1/4"}
    assert s["metrics"]["rejection_rate"]["baseline_rate"] == "1/4"
    assert s["arms"]["candidate"]["states"] == {"accepted": 1, "rejected": 1, "pending": 1,
                                                "unknown": 0, "unqualified": 0, "missing": 1}
    assert s["arms"]["candidate"]["capture"]["unknown_or_missing"] == 2
    assert s["transitions"]["unknown->missing"] == 1
    assert s["metrics"]["capture_rate"]["denominator"] == 4


def test_inadequate_n_suppresses_comparison_with_reason_and_no_delta():
    rows = {c: A for c in CASES.case_ids}
    s = comparison(rows, rows).compare(min_cases=5, min_coverage=Fraction(0), **POLICY)
    assert s["status"] == "suppressed"
    assert "cases_below_minimum" in s["suppression_reasons"]
    for name in ("acceptance_rate", "rejection_rate", "capture_rate"):
        metric = s["metrics"][name]
        assert metric["status"] == "suppressed" and metric["delta"] is None
        assert metric["delta_bounds"] is None and metric["baseline_rate"] is None
        assert metric["denominator"] == 4  # the denominator is still reported
    assert s["metrics"]["review_seconds"]["delta_total"] is None
    assert all(v["status"] == "suppressed" for axis in s["strata"].values() for v in axis.values())


def test_inadequate_coverage_suppresses_comparison():
    base = {c: A for c in CASES.case_ids}
    cand = {"c1": A}
    s = comparison(base, cand).compare(min_cases=1, min_coverage=Fraction(1, 2), **POLICY)
    assert s["status"] == "suppressed"
    assert "paired_coverage_below_minimum" in s["suppression_reasons"]
    assert s["paired_coverage"] == "1/4"
    assert s["metrics"]["acceptance_rate"]["delta"] is None
    s2 = comparison(base, cand).compare(min_cases=2, min_coverage=Fraction(0), **POLICY)
    assert s2["suppression_reasons"] == ["paired_observations_below_minimum",
                                        "source_qualified_pairs_below_minimum"]


def test_measure_with_sparse_known_values_is_suppressed_separately():
    base = {c: ("accepted", True, {"rework_seconds": 5} if c == "c1" else {}) for c in CASES.case_ids}
    cand = {c: ("accepted", True, {"rework_seconds": 3} if c == "c1" else {}) for c in CASES.case_ids}
    s = comparison(base, cand).compare(min_cases=1, min_coverage=Fraction(1, 2), **POLICY)
    assert s["status"] == "computed"
    rework = s["metrics"]["rework_seconds"]
    assert rework["status"] == "suppressed"
    assert rework["suppression_reasons"] == ["measurement_coverage_below_minimum"]
    assert rework["unknown_cases"] == 3 and rework["delta_total"] is None


def test_mixed_currency_pairs_are_never_converted():
    base = {"c1": ("accepted", True, {"known_cost": Decimal("1"), "currency": "EUR"})}
    cand = {"c1": ("accepted", True, {"known_cost": Decimal("1"), "currency": "USD"})}
    s = comparison(base, cand).compare(min_cases=1, min_coverage=Fraction(0), **POLICY)
    cost = s["metrics"]["known_cost"]
    assert cost["currency_mismatch_cases"] == 1 and cost["conversion"] == "none"
    assert all(v["known_pairs"] == 0 and v["delta_total"] is None for v in cost["by_currency"].values())


def test_arms_must_bind_the_same_committed_case_set_and_differ_by_one_revision():
    base, cand = arm("base", {"c1": A}), arm("cand", {"c1": A})
    other = replace(CASES, version=2)
    with pytest.raises(ExperimentError, match="committed case set"):
        ExperimentComparison("cmp", CASES, arm("base", {"c1": A}, case_set=other), cand, ("x",))
    with pytest.raises(ExperimentError, match="enumerate exactly"):
        ExperimentComparison("cmp", CASES, arm("base", {"c1": A}, population=("c1", "c2")), cand, ("x",))
    with pytest.raises(ExperimentError, match="same revision"):
        ExperimentComparison("cmp", CASES, base, replace(base, experiment_id="exp-other"), ("x",))
    with pytest.raises(ExperimentError, match="distinct experiment"):
        ExperimentComparison("cmp", CASES, base, replace(cand, experiment_id=base.experiment_id), ("x",))
    with pytest.raises(ExperimentError, match="limitations"):
        ExperimentComparison("cmp", CASES, base, cand, ())


def test_arms_must_match_all_named_composition_axes_outside_selected_revision():
    base = arm("base", {"c1": A}, revision_kind="model_id", model="model-a",
               profile="sha256:shared-instructions")
    candidate = arm("candidate", {"c1": A}, revision_kind="model_id", model="model-b",
                    profile="sha256:shared-instructions")
    ExperimentComparison("model-cmp", CASES, base, candidate, ("Synthetic comparison.",))

    confounded = arm("confounded", {"c1": A}, revision_kind="model_id", model="model-b",
                     recipe="recipe-2", profile="sha256:shared-instructions")
    with pytest.raises(ExperimentError, match="outside the selected revision"):
        ExperimentComparison("confounded-cmp", CASES, base, confounded,
                             ("Synthetic comparison.",))


@pytest.mark.parametrize("minimum,coverage", [(0, Fraction(1)), (True, Fraction(1)),
                                                (1, 0.5), (1, Fraction(3, 2))])
def test_no_implicit_or_malformed_thresholds(minimum, coverage):
    with pytest.raises(ExperimentError):
        comparison({"c1": A}, {"c1": A}).compare(min_cases=minimum, min_coverage=coverage, **POLICY)


def test_unqualified_owner_sources_are_explicit_and_cannot_produce_acceptance_delta():
    rows = {case_id: A for case_id in CASES.case_ids}
    stale = OwnerOutcomeQualification(
        case_id="c1", owner_record_ref="owner:c1@rev1", status="stale",
        release_digest="a" * 64,
        reason="the terminal item revision advanced after the supplied snapshot",
    )
    base = dict(rows)
    base["c1"] = ("accepted", True, {"source_qualification": stale})
    s = comparison(base, rows).compare(min_cases=4, min_coverage=Fraction(3, 4), **POLICY)

    assert s["status"] == "suppressed"
    assert s["comparison_readiness"] == "inconclusive"
    assert "source_qualified_pairs_below_minimum" in s["inconclusive_reasons"]
    assert s["source_qualification"]["baseline"]["unqualified_cases"] == 1
    assert s["source_qualification"]["baseline"]["cases"][0]["reason_codes"] == ["source-stale"]
    assert "advanced" in s["source_qualification"]["baseline"]["cases"][0]["qualification_reason"]
    assert s["transitions"]["unqualified->accepted"] == 1
    assert s["metrics"]["acceptance_rate"]["delta"] is None


def test_unresolved_intake_rows_suppress_the_paired_owner_outcome_comparison():
    rows = {case_id: A for case_id in CASES.case_ids}
    rows["c1"] = ("accepted", True, {"source_qualification": None})
    s = comparison(rows, rows).compare(min_cases=4, min_coverage=Fraction(1), **POLICY)

    assert s["comparison_readiness"] == "inconclusive"
    assert s["source_qualification"]["qualified_paired_cases"] == 3
    assert s["source_qualification"]["baseline"]["cases"][0]["reason_codes"] == [
        "source-qualification-missing"]
    assert s["metrics"]["acceptance_rate"]["delta"] is None


def test_outcome_rates_use_only_the_same_source_qualified_pairs():
    base = {case_id: A for case_id in CASES.case_ids}
    candidate = dict(base)
    candidate["c1"] = ("accepted", True, {"source_qualification": None})
    s = comparison(base, candidate).compare(min_cases=3, min_coverage=Fraction(3, 4), **POLICY)

    assert s["comparison_readiness"] == "eligible_for_review"
    acceptance = s["metrics"]["acceptance_rate"]
    assert acceptance["baseline"] == {"count": 3, "unresolved": 1}
    assert acceptance["candidate"] == {"count": 3, "unresolved": 1}
    assert acceptance["delta"] == "0"
    assert acceptance["delta_bounds"] == {"lower": "-1/4", "upper": "1/4"}
    assert s["source_qualification"]["candidate"]["cases"][0]["owner_disposition"] == "accepted"


def test_acceptance_needs_matching_release_independent_check_and_actual_use():
    incomplete = OwnerOutcomeQualification(
        case_id="c1", owner_record_ref="owner:c1@rev1", status="qualified",
        qualification_ref="owner:qualification/c1@rev1",
        source_snapshot_ref="owner:snapshot/c1@immutable1",
        source_snapshot_sha256="sha256:" + "f" * 64,
        owner_revision_ref="sprintctl:item/c1@revision1",
        release_digest="b" * 64,
    )
    observation = obs("c1", "r1", source_qualification=incomplete)
    assert observation.comparison_qualified is False
    assert observation.source_qualification_reasons == (
        "release-digest-mismatch", "independent-verification-missing", "actual-use-missing")
    mismatched_owner = qualified("c1", "owner:c1@rev1", "a" * 64, "accepted")
    mismatched_owner = replace(mismatched_owner, owner_record_ref="owner:other@rev1")
    observation = obs("c1", "r1", source_qualification=mismatched_owner)
    assert observation.comparison_qualified is False
    assert observation.source_qualification_reasons == ("qualification-owner-record-mismatch",)


def test_unqualified_pair_coverage_suppresses_all_outcome_and_cost_deltas():
    measured = ("accepted", True, {"review_seconds": 10,
                                    "known_cost": Decimal("1.00"), "currency": "EUR"})
    rows = {case_id: measured for case_id in CASES.case_ids}
    unverified = OwnerOutcomeQualification(
        case_id="c1", owner_record_ref="owner:c1@rev1", status="unverified",
        reason="the owner source has not been resolved",
    )
    base = dict(rows)
    cand = dict(rows)
    base["c1"] = ("accepted", True, {"source_qualification": unverified})
    cand["c1"] = ("accepted", True, {"source_qualification": unverified})
    s = comparison(base, cand).compare(min_cases=4, min_coverage=Fraction(1), **POLICY)

    assert s["status"] == "suppressed"
    assert s["comparison_readiness"] == "inconclusive"
    assert "source_qualification_coverage_below_minimum" in s["inconclusive_reasons"]
    assert s["source_qualification"]["qualified_paired_coverage"] == "3/4"
    assert s["metrics"]["acceptance_rate"]["delta"] is None
    assert s["metrics"]["review_seconds"]["delta_total"] is None
    assert s["metrics"]["known_cost"]["by_currency"]["EUR"]["delta_total"] is None
    assert s["metrics"]["capture_rate"]["status"] == "computed"
    assert s["metrics"]["capture_rate"]["delta"] == "0"

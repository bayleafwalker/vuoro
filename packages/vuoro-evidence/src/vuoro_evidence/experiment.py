"""Read-only learning records over externally accepted outcomes.

These objects neither accept work nor authenticate the supplied owner references.
They retain the comparison population and unknown measurements without changing
profiles, grants, provisioning or policy. All thresholds are reviewer inputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from datetime import datetime, timezone
import hashlib
import json
import re

from .run import RunManifest, index_runs


class ExperimentError(ValueError):
    """An observation cannot be interpreted without inventing missing facts."""


def _text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentError(f"{name} must be a nonempty reference or label")


def _count(name: str, value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ExperimentError(f"{name} must be a nonnegative integer or unknown")


def population_digest(*, population_ref: str, task_group: str,
                      expected_case_ids: tuple[str, ...], revision_kind: str,
                      revision_ref: str) -> str:
    """Bind the supplied population/arm snapshot, not its authenticity or timing."""
    body = {"population_ref": population_ref, "task_group": task_group,
            "expected_case_ids": sorted(expected_case_ids),
            "revision_kind": revision_kind, "revision_ref": revision_ref}
    return "sha256:" + hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode()).hexdigest()


def run_revision(manifest: RunManifest, kind: str) -> str:
    if kind in {"recipe_id", "model_id", "harness_build"}:
        return getattr(manifest, kind)
    if kind == "observed_profile":
        return "sha256:" + hashlib.sha256(json.dumps(
            manifest.observed_profile.as_dict(), sort_keys=True,
            separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    raise ExperimentError("revision_kind must select recipe_id, model_id, harness_build or observed_profile")


@dataclass(frozen=True)
class OwnerOutcomeQualification:
    """Caller-supplied source check for one owner outcome.

    This is a joinable report from the owner-side source resolver, not an
    authentication performed by this package. An accepted outcome also needs
    separate independent-check and actual-use references before it can count
    as comparison-qualified.
    """

    case_id: str
    owner_record_ref: str
    status: str  # qualified | unverified | unavailable | stale | conflicting
    qualification_ref: str | None = None
    source_snapshot_ref: str | None = None
    source_snapshot_sha256: str | None = None
    owner_revision_ref: str | None = None
    release_digest: str | None = None
    independent_verification_ref: str | None = None
    actual_use_ref: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("case_id", "owner_record_ref"):
            _text(name, getattr(self, name))
        if not isinstance(self.status, str) or self.status not in {
                "qualified", "unverified", "unavailable", "stale", "conflicting"}:
            raise ExperimentError("qualification status must identify a source check result")
        for name in ("qualification_ref", "source_snapshot_ref", "owner_revision_ref",
                     "independent_verification_ref", "actual_use_ref", "reason"):
            value = getattr(self, name)
            if value is not None:
                _text(name, value)
        if self.source_snapshot_sha256 is not None and (
                not isinstance(self.source_snapshot_sha256, str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", self.source_snapshot_sha256)):
            raise ExperimentError("source_snapshot_sha256 must be a sha256 reference")
        if self.release_digest is not None and (
                not isinstance(self.release_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.release_digest)):
            raise ExperimentError("release_digest must be 64 lowercase hexadecimal characters or unknown")
        if self.status == "qualified":
            if not all((self.qualification_ref, self.source_snapshot_ref,
                        self.source_snapshot_sha256, self.owner_revision_ref)):
                raise ExperimentError("qualified sources require a report, immutable snapshot and current owner revision")
        elif self.reason is None:
            raise ExperimentError("unqualified source checks require a reason")


@dataclass(frozen=True)
class OutcomeObservation:
    """A supplied owner observation, never inferred from session completion.

    One case represents one task in the predeclared population. Repeated runs
    belong in run_ids; they must not manufacture additional accepted cases.
    Acceptance provenance is kept even for pending/rejected observations.
    """

    case_id: str
    run_ids: tuple[str, ...]
    disposition: str  # accepted, rejected, pending, unknown (not 'completed')
    owner_record_ref: str
    evidence_refs: tuple[str, ...]
    evidence_class: str
    captured: bool | None
    acceptance_seconds: int | None = None
    review_seconds: int | None = None
    rework_seconds: int | None = None
    recovery_seconds: int | None = None
    known_cost: Decimal | None = None
    currency: str | None = None
    owner_release_digest: str | None = field(default=None, kw_only=True)
    source_qualification: OwnerOutcomeQualification | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        for name in ("case_id", "owner_record_ref", "evidence_class"):
            _text(name, getattr(self, name))
        if self.disposition not in {"accepted", "rejected", "pending", "unknown"}:
            raise ExperimentError("disposition must be an owner acceptance state, not a completion event")
        for name in ("run_ids", "evidence_refs"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not values or len(set(values)) != len(values):
                raise ExperimentError(f"{name} must be a nonempty tuple of unique references")
            for value in values:
                _text(name, value)
        if self.captured is not None and type(self.captured) is not bool:
            raise ExperimentError("captured must be true, false or unknown")
        for name in ("acceptance_seconds", "review_seconds", "rework_seconds", "recovery_seconds"):
            _count(name, getattr(self, name))
        if self.disposition != "accepted" and self.acceptance_seconds is not None:
            raise ExperimentError("time to acceptance requires accepted disposition")
        if self.known_cost is not None:
            if not isinstance(self.known_cost, Decimal) or not self.known_cost.is_finite() or self.known_cost < 0:
                raise ExperimentError("known_cost must be a finite nonnegative Decimal or unknown")
            if not isinstance(self.currency, str) or not re.fullmatch(r"[A-Z]{3}", self.currency):
                raise ExperimentError("currency must be a uppercase three-letter currency identifier")
        elif self.currency is not None:
            raise ExperimentError("currency without known cost is ambiguous")
        if self.owner_release_digest is not None and (
                not isinstance(self.owner_release_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.owner_release_digest)):
            raise ExperimentError("owner_release_digest must be 64 lowercase hexadecimal characters or unknown")
        if self.source_qualification is not None and not isinstance(
                self.source_qualification, OwnerOutcomeQualification):
            raise ExperimentError("source_qualification must be an OwnerOutcomeQualification or unknown")

    @property
    def source_qualification_reasons(self) -> tuple[str, ...]:
        """Stable reasons this supplied outcome cannot enter a qualified comparison."""
        qualification = self.source_qualification
        if qualification is None:
            return ("source-qualification-missing",)
        reasons = []
        if qualification.case_id != self.case_id:
            reasons.append("qualification-case-mismatch")
        if qualification.owner_record_ref != self.owner_record_ref:
            reasons.append("qualification-owner-record-mismatch")
        if qualification.status != "qualified":
            reasons.append(f"source-{qualification.status}")
            return tuple(reasons)
        if self.disposition == "accepted" and (
                self.owner_release_digest is None or qualification.release_digest is None):
            reasons.append("release-digest-missing")
        elif qualification.release_digest != self.owner_release_digest:
            reasons.append("release-digest-missing" if self.owner_release_digest is None
                           else "release-digest-mismatch")
        if self.disposition == "accepted":
            if qualification.independent_verification_ref is None:
                reasons.append("independent-verification-missing")
            if qualification.actual_use_ref is None:
                reasons.append("actual-use-missing")
        return tuple(reasons)

    @property
    def comparison_qualified(self) -> bool:
        """Whether caller-supplied qualification is structurally complete for this outcome."""
        return not self.source_qualification_reasons


@dataclass(frozen=True)
class ExperimentRecord:
    """One revision and task grouping with an explicit, bounded denominator.

    Missing population members are unknown; all cases, including rejected and
    unobserved cases, remain in the denominator. Revision refs identify external
    recipe/profile revisions. They cannot compile or select runtime behavior.
    """

    experiment_id: str
    revision_ref: str
    task_group: str
    population_ref: str
    expected_case_ids: tuple[str, ...]
    observations: tuple[OutcomeObservation, ...]
    manifests: tuple[RunManifest, ...]
    limitations: tuple[str, ...]
    revision_kind: str = field(kw_only=True)
    population_sha256: str = field(kw_only=True)
    population_registered_at: datetime = field(kw_only=True)
    population_registration_ref: str = field(kw_only=True)

    def __post_init__(self) -> None:
        for name in ("experiment_id", "revision_ref", "task_group", "population_ref"):
            _text(name, getattr(self, name))
        if (not isinstance(self.expected_case_ids, tuple) or not self.expected_case_ids
                or len(set(self.expected_case_ids)) != len(self.expected_case_ids)):
            raise ExperimentError("expected_case_ids must name a nonempty unique population")
        for case_id in self.expected_case_ids:
            _text("case_id", case_id)
        if not isinstance(self.observations, tuple) or not isinstance(self.manifests, tuple):
            raise ExperimentError("observations and manifests must be tuples")
        if not isinstance(self.limitations, tuple) or not self.limitations:
            raise ExperimentError("limitations must explicitly describe attribution and selection limits")
        for limit in self.limitations:
            _text("limitation", limit)
        if any(not isinstance(run, RunManifest) for run in self.manifests):
            raise ExperimentError("manifests must contain RunManifest records")
        _text("population_registration_ref", self.population_registration_ref)
        if (not isinstance(self.population_registered_at, datetime)
                or self.population_registered_at.utcoffset() is None):
            raise ExperimentError("population_registered_at must be an aware supplied owner timestamp")
        if self.population_sha256 != population_digest(
                population_ref=self.population_ref, task_group=self.task_group,
                expected_case_ids=self.expected_case_ids, revision_kind=self.revision_kind,
                revision_ref=self.revision_ref):
            raise ExperimentError("declared population/arm does not match its bound snapshot digest")
        if self.revision_kind not in {"recipe_id", "model_id", "harness_build", "observed_profile"}:
            raise ExperimentError("unsupported revision_kind")
        if any(run_revision(manifest, self.revision_kind) != self.revision_ref for manifest in self.manifests):
            raise ExperimentError("run composition differs from the named revision arm")
        runs = index_runs(self.manifests)
        seen: set[str] = set()
        used_runs: set[str] = set()
        for observation in self.observations:
            if not isinstance(observation, OutcomeObservation):
                raise ExperimentError("observations must contain OutcomeObservation records")
            if observation.case_id not in self.expected_case_ids or observation.case_id in seen:
                raise ExperimentError("observations must name distinct cases within the declared population")
            seen.add(observation.case_id)
            if set(observation.run_ids) - set(runs):
                raise ExperimentError("observation references an unavailable RunManifest")
            if used_runs.intersection(observation.run_ids):
                raise ExperimentError("one run cannot inflate multiple case denominators")
            used_runs.update(observation.run_ids)
        if set(runs) != used_runs:
            raise ExperimentError("unreferenced manifests cannot be silently included")

    def summarize(self, *, min_accepted_cases: int, min_capture: Fraction,
                  review_policy_ref: str, reviewer_ref: str) -> dict:
        """Produce bounded review inputs; never rank revisions or claim causality."""
        if type(min_accepted_cases) is not int or min_accepted_cases < 1:
            raise ExperimentError("reviewer must declare a positive minimum accepted case count")
        if not isinstance(min_capture, Fraction) or not 0 <= min_capture <= 1:
            raise ExperimentError("reviewer must declare a capture fraction between zero and one")
        _text("review_policy_ref", review_policy_ref)
        _text("reviewer_ref", reviewer_ref)
        n = len(self.expected_case_ids)
        owner_reported_accepted = [o for o in self.observations if o.disposition == "accepted"]
        accepted = [o for o in owner_reported_accepted if o.comparison_qualified]
        captured = sum(o.captured is True for o in self.observations)
        reasons = []
        if len(accepted) < min_accepted_cases:
            reasons.append("too_few_accepted_cases")
        if len(owner_reported_accepted) > len(accepted):
            reasons.append("source_qualification_incomplete")
        if Fraction(captured, n) < min_capture:
            reasons.append("insufficient_capture")
        def burden(rows, denominator):
            totals = {}
            for name in ("review_seconds", "rework_seconds", "recovery_seconds"):
                known = [getattr(o, name) for o in rows if getattr(o, name) is not None]
                totals[name] = {"known_total": sum(known) if known else None,
                                "known_cases": len(known), "unknown_cases": denominator - len(known)}
            units = sorted({o.currency for o in rows if o.known_cost is not None})
            costs = {unit: str(sum((o.known_cost for o in rows
                        if o.currency == unit and o.known_cost is not None), Decimal(0))) for unit in units}
            return {"expected_cases": denominator, "observed_cases": len(rows), "effort": totals,
                    "cost": {"known_totals": costs,
                             "known_cases": sum(o.known_cost is not None for o in rows),
                             "unknown_cases": denominator - sum(o.known_cost is not None for o in rows)}}

        def accepted_effort(rows):
            effort = {}
            for name in ("acceptance_seconds", "review_seconds", "rework_seconds", "recovery_seconds"):
                known = [getattr(o, name) for o in rows if getattr(o, name) is not None]
                effort[name] = {"known_total": sum(known) if known else None,
                                "known_cases": len(known), "unknown_cases": len(rows) - len(known)}
            return effort

        def accepted_cost(rows):
            units = sorted({o.currency for o in rows if o.known_cost is not None})
            return {"known_totals": {currency: str(sum(
                        (o.known_cost for o in rows
                         if o.currency == currency and o.known_cost is not None), Decimal(0)))
                        for currency in units},
                    "known_cases": sum(o.known_cost is not None for o in rows),
                    "unknown_cases": len(rows) - sum(o.known_cost is not None for o in rows)}

        accepted_effort_by_source = accepted_effort(accepted)
        owner_reported_effort = accepted_effort(owner_reported_accepted)
        qualified_cost = accepted_cost(accepted)
        owner_reported_cost = accepted_cost(owner_reported_accepted)
        by_case = {o.case_id: o for o in self.observations}
        unqualified_case_ids = sorted(
            case_id for case_id in self.expected_case_ids
            if case_id not in by_case or not by_case[case_id].comparison_qualified)
        qualification_cases = []
        for case_id in sorted(self.expected_case_ids):
            observation = by_case.get(case_id)
            if observation is None:
                qualification_cases.append({"case_id": case_id, "status": "missing_observation",
                                           "reason_codes": ["observation-missing"]})
                continue
            qualification = observation.source_qualification
            qualification_cases.append({
                "case_id": case_id,
                "status": qualification.status if qualification is not None else "missing",
                "qualified_for_comparison": observation.comparison_qualified,
                "reason_codes": list(observation.source_qualification_reasons),
                "qualification_ref": qualification.qualification_ref if qualification is not None else None,
                "source_snapshot_ref": qualification.source_snapshot_ref if qualification is not None else None,
                "source_snapshot_sha256": qualification.source_snapshot_sha256 if qualification is not None else None,
                "owner_revision_ref": qualification.owner_revision_ref if qualification is not None else None,
                "owner_release_digest": observation.owner_release_digest,
                "qualification_release_digest": qualification.release_digest if qualification is not None else None,
                "qualification_reason": qualification.reason if qualification is not None else None,
                "independent_verification_ref": (
                    qualification.independent_verification_ref if qualification is not None else None),
                "actual_use_ref": qualification.actual_use_ref if qualification is not None else None,
            })
        return {
            "schema_version": "vuoro-experiment-summary/v2",
            "experiment_id": self.experiment_id, "revision_ref": self.revision_ref,
            "task_group": self.task_group, "population_ref": self.population_ref,
            "revision_kind": self.revision_kind,
            "population_binding": {"sha256": self.population_sha256,
                "registered_at": self.population_registered_at.astimezone(timezone.utc).isoformat(),
                "registration_ref": self.population_registration_ref,
                "registration_authenticity_and_pre_run_order": "not_verified_by_this_package"},
            "expected_cases": n, "observed_cases": len(self.observations),
            "accepted_cases": len(accepted),
            "source_qualified_accepted_cases": len(accepted),
            "owner_reported_accepted_cases": len(owner_reported_accepted),
            "dispositions": {state: sum(o.disposition == state for o in self.observations)
                             for state in ("accepted", "rejected", "pending", "unknown")},
            "missing_case_ids": sorted(set(self.expected_case_ids) - {o.case_id for o in self.observations}),
            "capture": {"captured_cases": captured, "expected_cases": n,
                        "uncaptured_cases": sum(o.captured is False for o in self.observations),
                        "unknown_cases": n - sum(o.captured is not None for o in self.observations)},
            "review_thresholds": {"min_accepted_cases": min_accepted_cases,
                                  "min_capture": str(min_capture),
                                  "review_policy_ref": review_policy_ref, "reviewer_ref": reviewer_ref,
                                  "predeclaration": "not_verified_by_this_package"},
            "source_qualification": {
                "qualified_cases": sum(o.comparison_qualified for o in self.observations),
                "unqualified_cases": n - sum(o.comparison_qualified for o in self.observations),
                "unqualified_case_ids": unqualified_case_ids,
                "cases": qualification_cases,
                "basis": "caller_supplied",
                "authentication": "not_performed_by_this_package",
            },
            "comparison_readiness": "inconclusive" if reasons else "eligible_for_review",
            "inconclusive_reasons": reasons,
            "accepted_effort": accepted_effort_by_source,
            "owner_reported_accepted_effort": owner_reported_effort,
            "population_burden": burden(self.observations, n),
            "burden_by_disposition": {state: burden(
                [o for o in self.observations if o.disposition == state],
                sum(o.disposition == state for o in self.observations))
                for state in ("accepted", "rejected", "pending", "unknown")},
            "accepted_cost": qualified_cost,
            "owner_reported_accepted_cost": owner_reported_cost,
            "observations": [{"case_id": o.case_id, "disposition": o.disposition,
                "owner_record_ref": o.owner_record_ref, "run_ids": list(o.run_ids),
                "evidence_refs": list(o.evidence_refs), "evidence_class": o.evidence_class,
                "captured": o.captured, "acceptance_seconds": o.acceptance_seconds,
                "review_seconds": o.review_seconds, "rework_seconds": o.rework_seconds,
                "recovery_seconds": o.recovery_seconds,
                "known_cost": str(o.known_cost) if o.known_cost is not None else None,
                "currency": o.currency} for o in self.observations],
            "accepted_evidence": [{"case_id": o.case_id, "owner_record_ref": o.owner_record_ref,
                                   "evidence_class": o.evidence_class, "evidence_refs": list(o.evidence_refs),
                                   "run_ids": list(o.run_ids)} for o in accepted],
            "run_compositions": {r.run_id: {"digest": r.composition_digest(), "composition": r.composition()}
                                 for r in self.manifests},
            "limitations": list(self.limitations),
            "authority": "read_only_supplied_owner_observations",
            "causal_attribution": "not_established",
        }

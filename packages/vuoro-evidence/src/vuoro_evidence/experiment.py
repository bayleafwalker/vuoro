"""Read-only learning records over externally accepted outcomes.

These objects neither accept work nor authenticate the supplied owner references.
They retain the comparison population and unknown measurements without changing
profiles, grants, provisioning or policy. All thresholds are reviewer inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from .run import RunManifest, index_runs


class ExperimentError(ValueError):
    """An observation cannot be interpreted without inventing missing facts."""


def _text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentError(f"{name} must be a nonempty reference or label")


def _count(name: str, value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ExperimentError(f"{name} must be a nonnegative integer or unknown")


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
            _text("currency", self.currency)
        elif self.currency is not None:
            raise ExperimentError("currency without known cost is ambiguous")


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

    def summarize(self, *, min_accepted_cases: int, min_capture: Fraction) -> dict:
        """Produce bounded review inputs; never rank revisions or claim causality."""
        if type(min_accepted_cases) is not int or min_accepted_cases < 1:
            raise ExperimentError("reviewer must declare a positive minimum accepted case count")
        if not isinstance(min_capture, Fraction) or not 0 <= min_capture <= 1:
            raise ExperimentError("reviewer must declare a capture fraction between zero and one")
        n = len(self.expected_case_ids)
        accepted = [o for o in self.observations if o.disposition == "accepted"]
        captured = sum(o.captured is True for o in self.observations)
        reasons = []
        if len(accepted) < min_accepted_cases:
            reasons.append("too_few_accepted_cases")
        if Fraction(captured, n) < min_capture:
            reasons.append("insufficient_capture")
        currencies = sorted({o.currency for o in accepted if o.known_cost is not None})
        effort = {}
        for name in ("acceptance_seconds", "review_seconds", "rework_seconds", "recovery_seconds"):
            known = [getattr(o, name) for o in accepted if getattr(o, name) is not None]
            effort[name] = {"known_total": sum(known) if known else None,
                            "known_cases": len(known), "unknown_cases": len(accepted) - len(known)}
        return {
            "schema_version": "vuoro-experiment-summary/v1",
            "experiment_id": self.experiment_id, "revision_ref": self.revision_ref,
            "task_group": self.task_group, "population_ref": self.population_ref,
            "expected_cases": n, "observed_cases": len(self.observations),
            "accepted_cases": len(accepted),
            "dispositions": {state: sum(o.disposition == state for o in self.observations)
                             for state in ("accepted", "rejected", "pending", "unknown")},
            "missing_case_ids": sorted(set(self.expected_case_ids) - {o.case_id for o in self.observations}),
            "capture": {"captured_cases": captured, "expected_cases": n,
                        "uncaptured_cases": sum(o.captured is False for o in self.observations),
                        "unknown_cases": n - sum(o.captured is not None for o in self.observations)},
            "review_thresholds": {"min_accepted_cases": min_accepted_cases,
                                  "min_capture": str(min_capture)},
            "comparison_readiness": "inconclusive" if reasons else "eligible_for_review",
            "inconclusive_reasons": reasons,
            "accepted_effort": effort,
            "accepted_cost": {"known_totals": {currency: str(sum(
                (o.known_cost for o in accepted if o.currency == currency and o.known_cost is not None), Decimal(0)))
                for currency in currencies},
                "known_cases": sum(o.known_cost is not None for o in accepted),
                "unknown_cases": sum(o.known_cost is None for o in accepted)},
            "accepted_evidence": [{"case_id": o.case_id, "owner_record_ref": o.owner_record_ref,
                                   "evidence_class": o.evidence_class, "evidence_refs": list(o.evidence_refs),
                                   "run_ids": list(o.run_ids)} for o in accepted],
            "run_compositions": {r.run_id: {"digest": r.composition_digest(), "composition": r.composition()}
                                 for r in self.manifests},
            "limitations": list(self.limitations),
            "authority": "read_only_supplied_owner_observations",
            "causal_attribution": "not_established",
        }

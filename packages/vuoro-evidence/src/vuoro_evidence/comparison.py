"""Two-arm comparison of experiment records over one committed case set.

A `CaseSet` is a versioned, content-addressed list of real tasks with their
acceptance criteria. An `ExperimentComparison` pairs a baseline and a candidate
`ExperimentRecord` that both enumerate exactly that case set and differ only in
the declared revision selector. `compare()` computes per-metric deltas with
every case kept in the denominator: rejected, pending, unknown and missing
cases are never dropped. When the case count or paired coverage is below the
reviewer's declared minimum, the comparison is suppressed with explicit
reasons and no delta.

This module neither runs the arms nor accepts work, ranks revisions, records a
decision or establishes causal attribution. All thresholds are reviewer inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
from typing import Callable

from .experiment import ExperimentError, ExperimentRecord, OutcomeObservation, _text

CASE_SET_SCHEMA = "vuoro-case-set/v1"
COMPARISON_SCHEMA = "vuoro-experiment-comparison/v1"
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MEASURES = ("acceptance_seconds", "review_seconds", "rework_seconds", "recovery_seconds")
_STATES = ("accepted", "rejected", "pending", "unknown", "missing")


def _canonical(body: object) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def _keys(name: str, body: object, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(body, dict):
        raise ExperimentError(f"{name} must be an object")
    missing, extra = required - body.keys(), body.keys() - required - optional
    if missing or extra:
        raise ExperimentError(f"{name} keys differ from {CASE_SET_SCHEMA}: "
                              f"missing={sorted(missing)} unexpected={sorted(extra)}")
    return body


@dataclass(frozen=True)
class CaseCriteria:
    """How the owner decides a case: pass/fail, or a score with a pass mark."""

    kind: str  # pass_fail | scored
    statement: str
    max_score: int | None = None
    pass_score: int | None = None

    def __post_init__(self) -> None:
        _text("criteria statement", self.statement)
        if self.kind == "pass_fail":
            if self.max_score is not None or self.pass_score is not None:
                raise ExperimentError("pass_fail criteria cannot carry a score")
        elif self.kind == "scored":
            for name in ("max_score", "pass_score"):
                value = getattr(self, name)
                if type(value) is not int or value < 0:
                    raise ExperimentError(f"scored criteria require a nonnegative integer {name}")
            if not 0 < self.max_score or self.pass_score > self.max_score:
                raise ExperimentError("scored criteria require 0 <= pass_score <= max_score and max_score > 0")
        else:
            raise ExperimentError("criteria kind must be pass_fail or scored")

    def as_dict(self) -> dict:
        body = {"kind": self.kind, "statement": self.statement}
        if self.kind == "scored":
            body.update(max_score=self.max_score, pass_score=self.pass_score)
        return body


@dataclass(frozen=True)
class CaseSpec:
    """One real task. `observed_on` is when the original work happened, if known."""

    case_id: str
    workload: str
    source_ref: str
    criteria: CaseCriteria
    observed_on: date | None = None

    def __post_init__(self) -> None:
        for name in ("case_id", "workload", "source_ref"):
            _text(name, getattr(self, name))
        if not isinstance(self.criteria, CaseCriteria):
            raise ExperimentError("criteria must be CaseCriteria")
        if self.observed_on is not None and (type(self.observed_on) is not date):
            raise ExperimentError("observed_on must be a calendar date or unknown")

    def as_dict(self) -> dict:
        return {"case_id": self.case_id, "workload": self.workload, "source_ref": self.source_ref,
                "criteria": self.criteria.as_dict(),
                "observed_on": self.observed_on.isoformat() if self.observed_on else None}


@dataclass(frozen=True)
class CaseSet:
    """A committed, versioned case set addressed by the digest of its content."""

    name: str
    version: int
    description: str
    cases: tuple[CaseSpec, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise ExperimentError("case set name must be a lowercase slug")
        if type(self.version) is not int or self.version < 1:
            raise ExperimentError("case set version must be a positive integer")
        _text("description", self.description)
        if (not isinstance(self.cases, tuple) or not self.cases
                or any(not isinstance(case, CaseSpec) for case in self.cases)):
            raise ExperimentError("cases must be a nonempty tuple of CaseSpec")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ExperimentError("case ids must be unique within a case set")
        if not isinstance(self.limitations, tuple) or not self.limitations:
            raise ExperimentError("limitations must describe how the cases were selected")
        for limit in self.limitations:
            _text("limitation", limit)

    def as_dict(self) -> dict:
        return {"schema_version": CASE_SET_SCHEMA, "name": self.name, "version": self.version,
                "description": self.description,
                "cases": [c.as_dict() for c in sorted(self.cases, key=lambda c: c.case_id)],
                "limitations": list(self.limitations)}

    @property
    def digest(self) -> str:
        """Order-insensitive over cases; any change to a case or criterion changes it."""
        return "sha256:" + hashlib.sha256(_canonical(self.as_dict())).hexdigest()

    @property
    def ref(self) -> str:
        """The population reference both arms' ExperimentRecords must carry."""
        return f"case-set:{self.name}@v{self.version}+{self.digest}"

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases)

    @classmethod
    def from_dict(cls, body: object) -> "CaseSet":
        body = _keys("case set", body, {"schema_version", "name", "version", "description",
                                         "cases", "limitations"})
        if body["schema_version"] != CASE_SET_SCHEMA:
            raise ExperimentError(f"case set schema_version must be {CASE_SET_SCHEMA}")
        if not isinstance(body["cases"], list) or not isinstance(body["limitations"], list):
            raise ExperimentError("cases and limitations must be lists")
        cases = []
        for raw in body["cases"]:
            raw = _keys("case", raw, {"case_id", "workload", "source_ref", "criteria"}, {"observed_on"})
            crit = _keys("criteria", raw["criteria"], {"kind", "statement"}, {"max_score", "pass_score"})
            observed = raw.get("observed_on")
            if observed is not None:
                if not isinstance(observed, str) or not _DATE.fullmatch(observed):
                    raise ExperimentError("observed_on must be an ISO YYYY-MM-DD date or null")
                try:
                    observed = date.fromisoformat(observed)
                except ValueError as exc:
                    raise ExperimentError("observed_on must be a valid calendar date") from exc
            cases.append(CaseSpec(raw["case_id"], raw["workload"], raw["source_ref"],
                                  CaseCriteria(crit["kind"], crit["statement"], crit.get("max_score"),
                                               crit.get("pass_score")), observed))
        return cls(body["name"], body["version"], body["description"], tuple(cases),
                   tuple(body["limitations"]))


def _refuse_constant(value: str):
    raise ExperimentError(f"case set cannot contain non-finite number {value}")


def load_case_set(path: str | Path, *, expected_digest: str | None = None) -> CaseSet:
    """Load a committed case-set file; refuse it when it differs from a pinned digest."""
    try:
        body = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_refuse_constant)
    except json.JSONDecodeError as exc:
        raise ExperimentError(f"case set is not valid JSON: {exc}") from exc
    case_set = CaseSet.from_dict(body)
    if expected_digest is not None and case_set.digest != expected_digest:
        raise ExperimentError("case set content differs from the pinned digest")
    return case_set


def _state(observation: OutcomeObservation | None) -> str:
    return "missing" if observation is None else observation.disposition


def _q(value: Fraction | None) -> str | None:
    return None if value is None else str(value)


class _Cohort:
    """Per-case observations for both arms restricted to one set of cases."""

    def __init__(self, case_ids: list[str], base: dict, cand: dict) -> None:
        self.case_ids = case_ids
        self.n = len(case_ids)
        self.base = {c: base.get(c) for c in case_ids}
        self.cand = {c: cand.get(c) for c in case_ids}
        self.paired = [c for c in case_ids if self.base[c] is not None and self.cand[c] is not None]

    def gate(self, min_cases: int, min_coverage: Fraction) -> list[str]:
        reasons = []
        if self.n < min_cases:
            reasons.append("cases_below_minimum")
        if len(self.paired) < min_cases:
            reasons.append("paired_observations_below_minimum")
        if self.n == 0 or Fraction(len(self.paired), self.n) < min_coverage:
            reasons.append("paired_coverage_below_minimum")
        return reasons


def _arm(rows: dict) -> dict:
    observed = [o for o in rows.values() if o is not None]
    return {"expected_cases": len(rows), "observed_cases": len(observed),
            "states": {s: sum(_state(o) == s for o in rows.values()) for s in _STATES},
            "capture": {"captured": sum(o.captured is True for o in observed),
                        "uncaptured": sum(o.captured is False for o in observed),
                        "unknown_or_missing": sum(o is None or o.captured is None for o in rows.values())}}


def _rate(cohort: _Cohort, reasons: list[str], hit: Callable[[OutcomeObservation], bool],
          resolved: Callable[[OutcomeObservation], bool]) -> dict:
    n = cohort.n

    def counts(rows):
        return {"count": sum(o is not None and hit(o) for o in rows.values()),
                "unresolved": sum(o is None or not resolved(o) for o in rows.values())}

    base, cand = counts(cohort.base), counts(cohort.cand)
    out = {"denominator": n, "baseline": base, "candidate": cand,
           "status": "suppressed" if reasons else "computed", "suppression_reasons": list(reasons),
           "baseline_rate": None, "candidate_rate": None, "delta": None, "delta_bounds": None}
    if not reasons:
        out.update(baseline_rate=_q(Fraction(base["count"], n)),
                   candidate_rate=_q(Fraction(cand["count"], n)),
                   delta=_q(Fraction(cand["count"] - base["count"], n)),
                   # Every unresolved case could still go either way.
                   delta_bounds={"lower": _q(Fraction(cand["count"] - base["count"] - base["unresolved"], n)),
                                 "upper": _q(Fraction(cand["count"] + cand["unresolved"] - base["count"], n))})
    return out


def _paired(cohort: _Cohort, reasons: list[str], min_cases: int, min_coverage: Fraction,
            value: Callable[[OutcomeObservation], object], render: Callable[[object], str]) -> dict:
    known = [c for c in cohort.paired
             if value(cohort.base[c]) is not None and value(cohort.cand[c]) is not None]
    own = list(reasons)
    if len(known) < min_cases:
        own.append("known_measurements_below_minimum")
    if cohort.n == 0 or Fraction(len(known), cohort.n) < min_coverage:
        own.append("measurement_coverage_below_minimum")
    out = {"denominator": cohort.n, "known_pairs": len(known), "unknown_cases": cohort.n - len(known),
           "status": "suppressed" if own else "computed", "suppression_reasons": own,
           "baseline_known_total": None, "candidate_known_total": None,
           "delta_total": None, "delta_mean_per_known_pair": None}
    if not own:
        base = sum((value(cohort.base[c]) for c in known), type(value(cohort.base[known[0]]))(0))
        cand = sum((value(cohort.cand[c]) for c in known), type(value(cohort.cand[known[0]]))(0))
        out.update(baseline_known_total=render(base), candidate_known_total=render(cand),
                   delta_total=render(cand - base),
                   delta_mean_per_known_pair=_q(Fraction(cand - base) / len(known)))
    return out


def _cost(cohort: _Cohort, reasons: list[str], min_cases: int, min_coverage: Fraction) -> dict:
    mismatch = [c for c in cohort.paired if cohort.base[c].known_cost is not None
                and cohort.cand[c].known_cost is not None
                and cohort.base[c].currency != cohort.cand[c].currency]
    currencies = sorted({o.currency for c in cohort.paired for o in (cohort.base[c], cohort.cand[c])
                         if o.known_cost is not None})
    by_currency = {}
    for unit in currencies:
        def value(o, unit=unit):
            return o.known_cost if o.known_cost is not None and o.currency == unit else None
        by_currency[unit] = _paired(cohort, reasons, min_cases, min_coverage, value, str)
    return {"denominator": cohort.n, "currency_mismatch_cases": len(mismatch),
            "conversion": "none", "by_currency": by_currency}


def _transitions(cohort: _Cohort) -> dict:
    counts: dict[str, int] = {}
    for c in cohort.case_ids:
        key = f"{_state(cohort.base[c])}->{_state(cohort.cand[c])}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _metrics(cohort: _Cohort, min_cases: int, min_coverage: Fraction) -> dict:
    reasons = cohort.gate(min_cases, min_coverage)
    disposition_resolved = lambda o: o.disposition in {"accepted", "rejected"}  # noqa: E731
    return {
        "cases": cohort.n, "paired_observed_cases": len(cohort.paired),
        "paired_coverage": _q(Fraction(len(cohort.paired), cohort.n)) if cohort.n else None,
        "status": "suppressed" if reasons else "computed", "suppression_reasons": reasons,
        "arms": {"baseline": _arm(cohort.base), "candidate": _arm(cohort.cand)},
        "transitions": _transitions(cohort),
        "metrics": {
            "acceptance_rate": _rate(cohort, reasons, lambda o: o.disposition == "accepted",
                                     disposition_resolved),
            "rejection_rate": _rate(cohort, reasons, lambda o: o.disposition == "rejected",
                                    disposition_resolved),
            "capture_rate": _rate(cohort, reasons, lambda o: o.captured is True,
                                  lambda o: o.captured is not None),
            **{name: _paired(cohort, reasons, min_cases, min_coverage,
                             lambda o, name=name: getattr(o, name), str) for name in _MEASURES},
            "known_cost": _cost(cohort, reasons, min_cases, min_coverage),
        },
    }


@dataclass(frozen=True)
class ExperimentComparison:
    """Baseline versus candidate revision over the same committed case set."""

    comparison_id: str
    case_set: CaseSet
    baseline: ExperimentRecord
    candidate: ExperimentRecord
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        _text("comparison_id", self.comparison_id)
        if not isinstance(self.case_set, CaseSet):
            raise ExperimentError("case_set must be a CaseSet")
        if not isinstance(self.limitations, tuple) or not self.limitations:
            raise ExperimentError("limitations must describe attribution and selection limits")
        for limit in self.limitations:
            _text("limitation", limit)
        for arm in ("baseline", "candidate"):
            record = getattr(self, arm)
            if not isinstance(record, ExperimentRecord):
                raise ExperimentError(f"{arm} must be an ExperimentRecord")
            if record.population_ref != self.case_set.ref:
                raise ExperimentError(f"{arm} must reference the committed case set {self.case_set.ref}")
            if sorted(record.expected_case_ids) != sorted(self.case_set.case_ids):
                raise ExperimentError(f"{arm} must enumerate exactly the committed case set")
        if self.baseline.task_group != self.candidate.task_group:
            raise ExperimentError("arms must share one task group")
        if self.baseline.revision_kind != self.candidate.revision_kind:
            raise ExperimentError("arms must vary one declared revision selector")
        if self.baseline.revision_ref == self.candidate.revision_ref:
            raise ExperimentError("baseline and candidate name the same revision")
        if self.baseline.experiment_id == self.candidate.experiment_id:
            raise ExperimentError("baseline and candidate must be distinct experiment records")
        base_runs = {r.run_id for r in self.baseline.manifests}
        if base_runs.intersection(r.run_id for r in self.candidate.manifests):
            raise ExperimentError("one run cannot belong to both arms")

    def compare(self, *, min_cases: int, min_coverage: Fraction,
                review_policy_ref: str, reviewer_ref: str) -> dict:
        """Compute deltas, or suppress them; never rank revisions or claim causality."""
        if type(min_cases) is not int or min_cases < 1:
            raise ExperimentError("reviewer must declare a positive minimum case count")
        if not isinstance(min_coverage, Fraction) or not 0 <= min_coverage <= 1:
            raise ExperimentError("reviewer must declare a coverage fraction between zero and one")
        _text("review_policy_ref", review_policy_ref)
        _text("reviewer_ref", reviewer_ref)
        base = {o.case_id: o for o in self.baseline.observations}
        cand = {o.case_id: o for o in self.candidate.observations}
        ordered = sorted(self.case_set.case_ids)
        specs = {case.case_id: case for case in self.case_set.cases}

        def coverage_label(c):
            captured = sum(o is not None and o.captured is True for o in (base.get(c), cand.get(c)))
            return ("neither_captured_or_unknown", "one_arm_captured", "both_captured")[captured]

        keys = {"workload": lambda c: specs[c].workload,
                "observed_month": lambda c: (specs[c].observed_on.strftime("%Y-%m")
                                             if specs[c].observed_on else "unknown"),
                "coverage": coverage_label}
        strata = {}
        for axis, key in keys.items():
            groups: dict[str, list[str]] = {}
            for c in ordered:
                groups.setdefault(key(c), []).append(c)
            strata[axis] = {label: _metrics(_Cohort(cases, base, cand), min_cases, min_coverage)
                            for label, cases in sorted(groups.items())}
        overall = _metrics(_Cohort(ordered, base, cand), min_cases, min_coverage)

        def arm(record):
            return {"experiment_id": record.experiment_id, "revision_kind": record.revision_kind,
                    "revision_ref": record.revision_ref, "population_sha256": record.population_sha256,
                    "run_ids": sorted(r.run_id for r in record.manifests)}

        return {
            "schema_version": COMPARISON_SCHEMA,
            "comparison_id": self.comparison_id,
            "case_set": {"name": self.case_set.name, "version": self.case_set.version,
                         "digest": self.case_set.digest, "ref": self.case_set.ref},
            "task_group": self.baseline.task_group,
            "baseline": arm(self.baseline), "candidate": arm(self.candidate),
            "delta_direction": "candidate_minus_baseline",
            "thresholds": {"min_cases": min_cases, "min_coverage": str(min_coverage),
                           "review_policy_ref": review_policy_ref, "reviewer_ref": reviewer_ref,
                           "predeclaration": "not_verified_by_this_package"},
            **overall,
            "strata": strata,
            "limitations": list(self.limitations) + list(self.case_set.limitations)
                           + list(self.baseline.limitations) + list(self.candidate.limitations),
            "authority": "read_only_supplied_owner_observations",
            "decision": "not_recorded_by_this_package",
            "causal_attribution": "not_established",
        }

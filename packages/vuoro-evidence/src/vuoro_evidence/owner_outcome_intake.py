"""Structural joins of supplied owner projections, without resolving sources.

This adapter does not authenticate acceptance or recompute a Release digest.
Keep the intake sidecar alongside ExperimentRecord's derived summary.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import re
import unicodedata

from .experiment import ExperimentError, OutcomeObservation

SCHEMA_VERSION = "vuoro-owner-outcome-intake/v1"
SOURCE_VERIFICATION = "not_performed_by_this_package"
LIMITATIONS = (
    "Owner projections and references are supplied, not resolved or authenticated here.",
    "Matching Release digests are compared as supplied, not recomputed or certified.",
    "Measurement provenance and windows are supplied; retain this intake sidecar with the summary.",
    "Selection, capture coverage and causal attribution require independent review.",
)
_METRICS = {"acceptance_seconds", "review_seconds", "rework_seconds", "recovery_seconds", "known_cost"}


@dataclass(frozen=True)
class OwnerOutcomeIntake:
    observation: OutcomeObservation
    join_status: str
    reason_codes: tuple[str, ...]
    source_verification: str
    measurement_provenance: tuple[dict, ...]
    supplied_records: dict
    limitations: tuple[str, ...] = LIMITATIONS


def _fields(value, required, optional=()):
    if not isinstance(value, Mapping) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ExperimentError(f"expected fields {sorted(required)} with optional {sorted(optional)}")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or any(unicodedata.category(c) == "Cc" for c in value):
        raise ExperimentError(f"{label} must be nonempty text without control characters")
    return value


def _identity(value):
    if type(value) is not int or value < 1:
        raise ExperimentError("owner identifiers must be positive integers")


def _digest(value):
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)):
        raise ExperimentError("Release digest must be 64 lowercase hexadecimal characters or null")


def _timestamp(value):
    if not isinstance(value, str):
        raise ExperimentError("measurement window requires UTC timestamps")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExperimentError("measurement window requires UTC timestamps") from exc
    if result.utcoffset() != timedelta(0):
        raise ExperimentError("measurement window requires UTC timestamps")
    return result


def _measurements(measurements, disposition, reasons):
    if not isinstance(measurements, Mapping) or set(measurements) - _METRICS:
        raise ExperimentError("unknown measurement name")
    scalars, provenance = {}, []
    for name in sorted(measurements):
        row = _fields(measurements[name], {"value", "unit", "coverage"}, {"source_ref", "window"})
        if row["coverage"] not in {"complete", "partial", "unknown"}:
            raise ExperimentError("measurement coverage must be complete, partial or unknown")
        value = row["value"]
        if name == "known_cost":
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
                raise ExperimentError("known_cost requires an exact nonnegative decimal string")
            value = Decimal(value)
            if not isinstance(row["unit"], str) or not re.fullmatch(r"[A-Z]{3}", row["unit"]):
                raise ExperimentError("known_cost unit requires an uppercase three-letter currency")
        elif type(value) is not int or value < 0 or row["unit"] != "seconds":
            raise ExperimentError("effort requires nonnegative integer seconds")
        source = row.get("source_ref")
        window = row.get("window")
        if source is not None:
            _text(source, "measurement source_ref")
        if window is not None:
            _fields(window, {"start", "end"})
            if _timestamp(window["end"]) < _timestamp(window["start"]):
                raise ExperimentError("measurement window ends before it starts")
        exclusions = []
        if source is None or window is None:
            exclusions.append("measurement-provenance-missing")
        if row["coverage"] != "complete":
            exclusions.append("measurement-coverage-incomplete")
        if name == "acceptance_seconds" and disposition != "accepted":
            exclusions.append("acceptance-measurement-unqualified")
        if not exclusions:
            scalars[name] = value
            if name == "known_cost":
                scalars["currency"] = row["unit"]
        reasons.extend(exclusions)
        provenance.append({"name": name, "supplied": deepcopy(dict(row)),
                           "mapped": not exclusions, "exclusion_reasons": exclusions})
    return scalars, tuple(provenance)


def intake_owner_outcome(case: Mapping[str, object]) -> OwnerOutcomeIntake:
    """Map one supplied Sprintctl projection to an existing learning observation.

    Optional missing terminal records are an unknown outcome, not an omitted case.
    Malformed projections fail instead of silently shrinking a population. Values
    in owner_record_ref/evidence_refs/source_ref remain opaque caller references;
    their immutability and authenticity must be checked outside this package.
    """
    _fields(case, {"schema_version", "case_id", "run_ids", "owner_record_ref", "evidence_refs",
                   "evidence_class", "captured", "repository_id", "item_id", "item"},
            {"decision", "release", "measurements"})
    if case["schema_version"] != SCHEMA_VERSION:
        raise ExperimentError("unsupported owner intake schema_version")
    _text(case["repository_id"], "repository_id")
    _identity(case["item_id"])
    for name in ("case_id", "owner_record_ref", "evidence_class"):
        _text(case[name], name)
    for name in ("run_ids", "evidence_refs"):
        values = case[name]
        if not isinstance(values, (tuple, list)) or not values:
            raise ExperimentError(f"{name} must be a nonempty array")
        for value in values:
            _text(value, name)
        if len(set(values)) != len(values):
            raise ExperimentError(f"{name} must be unique")
    if case["captured"] is not None and type(case["captured"]) is not bool:
        raise ExperimentError("captured must be true, false or null")
    item = _fields(case["item"], {"repository_id", "id", "status", "resolution", "terminal_decision_id"})
    decision, release = case.get("decision"), case.get("release")
    for record in (item, decision, release):
        if record is None:
            continue
        if record is decision:
            _fields(record, {"repository_id", "id", "work_item_id", "kind", "release_digest"})
        elif record is release:
            _fields(record, {"repository_id", "work_item_id", "release_digest"})
        _text(record["repository_id"], "repository_id")
        _identity(record["id"] if record is item else record["work_item_id"])
    if item["terminal_decision_id"] is not None:
        _identity(item["terminal_decision_id"])
    if item["status"] not in {"pending", "active", "blocked", "done"}:
        raise ExperimentError("unsupported supplied item status")
    if item["resolution"] not in {None, "accepted", "rejected", "withdrawn", "superseded", "legacy"}:
        raise ExperimentError("unsupported supplied item resolution")
    if decision is not None:
        _identity(decision["id"])
        if decision["kind"] not in {"accept", "reject", "withdraw", "supersede", "revise"}:
            raise ExperimentError("unsupported supplied Decision kind")
        _digest(decision["release_digest"])
    if release is not None:
        _digest(release["release_digest"])
        if release["release_digest"] is None:
            raise ExperimentError("a supplied Release requires its digest")

    conflicts, missing = [], []
    if item["repository_id"] != case["repository_id"] or item["id"] != case["item_id"]:
        conflicts.append("item-identity-mismatch")
    if decision is not None:
        if decision["repository_id"] != case["repository_id"] or decision["work_item_id"] != case["item_id"]:
            conflicts.append("decision-item-mismatch")
        if decision["id"] != item["terminal_decision_id"]:
            conflicts.append("terminal-decision-id-mismatch")
    if release is not None and (release["repository_id"] != case["repository_id"] or release["work_item_id"] != case["item_id"]):
        conflicts.append("release-item-mismatch")
    terminal = item["status"] == "done"
    disposition = "unknown"
    if not terminal:
        if item["resolution"] is not None or item["terminal_decision_id"] is not None or decision is not None:
            conflicts.append("open-item-terminal-state-conflict")
        else:
            disposition = "pending"
    elif item["resolution"] == "legacy":
        missing.append("legacy-terminal-unqualified")
    elif item["resolution"] in {"withdrawn", "superseded"}:
        missing.append("terminal-disposition-unmapped")
    elif item["resolution"] in {"accepted", "rejected"}:
        if item["terminal_decision_id"] is None or decision is None:
            missing.append("terminal-decision-missing")
        if decision is not None:
            expected_kind = {"accepted": "accept", "rejected": "reject"}[item["resolution"]]
            if decision["kind"] != expected_kind:
                conflicts.append("decision-kind-resolution-mismatch")
            # Rejection need not cite a Release. Acceptance must cite one.
            if decision["release_digest"] is not None or expected_kind == "accept":
                if release is None:
                    missing.append("release-record-missing")
                elif decision["release_digest"] != release["release_digest"]:
                    conflicts.append("release-digest-mismatch")
        disposition = item["resolution"]
    else:
        missing.append("terminal-disposition-unmapped")
    if conflicts or missing:
        disposition = "unknown"
    reasons = conflicts + missing
    scalars, provenance = _measurements(case.get("measurements", {}), disposition, reasons)
    observation = OutcomeObservation(
        case["case_id"], tuple(case["run_ids"]), disposition, case["owner_record_ref"],
        tuple(case["evidence_refs"]), case["evidence_class"], case["captured"],
        owner_release_digest=release["release_digest"] if release is not None else None,
        **scalars)
    return OwnerOutcomeIntake(
        observation, "conflicting" if conflicts else "incomplete" if missing else "consistent",
        tuple(dict.fromkeys(reasons)), SOURCE_VERIFICATION, provenance,
        deepcopy({key: case.get(key) for key in ("repository_id", "item_id", "item", "decision", "release")}))

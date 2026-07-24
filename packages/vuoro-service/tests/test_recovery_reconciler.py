from __future__ import annotations

import pytest
from pydantic import ValidationError

from vuoro_service.recovery import (
    ReconciliationError,
    RecoveryReconciler,
    RecoveryRecord,
)


def _record(**overrides: object) -> RecoveryRecord:
    fields = {
        "record_id": "rec-1",
        "incident_id": "incident-1",
        "record_kind": "observation",
        "created_at": "2026-07-24T00:00:00Z",
        "basis_revision": "rev-1",
        "summary": "service unreachable, noted local state",
    }
    fields.update(overrides)
    return RecoveryRecord(**fields)


def test_requested_command_kind_requires_payload() -> None:
    with pytest.raises(ValidationError, match="requested_command is required"):
        RecoveryRecord(
            record_id="rec-2",
            incident_id="incident-1",
            record_kind="requested-command",
            created_at="2026-07-24T00:00:00Z",
            summary="ask for a manual rollback",
        )


def test_observation_kind_forbids_requested_command_payload() -> None:
    with pytest.raises(ValidationError, match="must be omitted"):
        RecoveryRecord(
            record_id="rec-3",
            incident_id="incident-1",
            record_kind="observation",
            created_at="2026-07-24T00:00:00Z",
            summary="just looking",
            requested_command={"command_type": "noop", "params": {}},
        )


def test_import_records_a_matching_basis_record() -> None:
    reconciler = RecoveryReconciler()
    outcome = reconciler.import_record(_record(), current_revision="rev-1")
    assert outcome.state == "recorded"
    assert outcome.conflict_reason is None


def test_import_is_idempotent() -> None:
    reconciler = RecoveryReconciler()
    first = reconciler.import_record(_record(), current_revision="rev-1")
    second = reconciler.import_record(_record(), current_revision="rev-99")
    assert first == second
    assert len(reconciler.history("incident-1")) == 1


def test_stale_basis_is_flagged_not_applied() -> None:
    reconciler = RecoveryReconciler()
    outcome = reconciler.import_record(
        _record(basis_revision="rev-0"), current_revision="rev-1"
    )
    assert outcome.state == "conflict-pending"
    assert outcome.conflict_reason == "stale-basis"


def test_competing_production_advance_detected_across_records() -> None:
    reconciler = RecoveryReconciler()
    first = reconciler.import_record(
        _record(record_id="rec-a", basis_revision="rev-1"), current_revision="rev-1"
    )
    assert first.state == "recorded"

    second = reconciler.import_record(
        _record(record_id="rec-b", basis_revision="rev-2"), current_revision="rev-2"
    )
    assert second.state == "conflict-pending"
    assert second.conflict_reason == "competing-production-advance"


def test_accepted_and_rejected_histories_are_recorded_distinctly() -> None:
    reconciler = RecoveryReconciler()
    reconciler.import_record(
        _record(record_id="rec-accept", basis_revision="rev-0"),
        current_revision="rev-1",
    )
    reconciler.import_record(
        _record(record_id="rec-reject", basis_revision="rev-0"),
        current_revision="rev-1",
    )

    accepted = reconciler.accept(
        "rec-accept", decided_by="dev", decided_at="2026-07-24T01:00:00Z"
    )
    rejected = reconciler.reject(
        "rec-reject",
        decided_by="dev",
        decided_at="2026-07-24T01:00:00Z",
        rejection_reason="stale evidence, superseded by live state",
    )

    assert accepted.state == "accepted"
    assert rejected.state == "rejected"
    assert rejected.rejection_reason
    history = {outcome.record_id: outcome for outcome in reconciler.history("incident-1")}
    assert history["rec-accept"].state == "accepted"
    assert history["rec-reject"].state == "rejected"


def test_reject_without_reason_is_rejected_by_the_api() -> None:
    reconciler = RecoveryReconciler()
    reconciler.import_record(
        _record(basis_revision="rev-0"), current_revision="rev-1"
    )
    with pytest.raises(ReconciliationError, match="rejection_reason is required"):
        reconciler.reject(
            "rec-1", decided_by="dev", decided_at="2026-07-24T01:00:00Z", rejection_reason=""
        )


def test_cannot_reconcile_a_record_with_no_conflict() -> None:
    reconciler = RecoveryReconciler()
    reconciler.import_record(_record(), current_revision="rev-1")
    with pytest.raises(ReconciliationError, match="only conflict-pending"):
        reconciler.accept("rec-1", decided_by="dev", decided_at="2026-07-24T01:00:00Z")


def test_cannot_reconcile_twice() -> None:
    reconciler = RecoveryReconciler()
    reconciler.import_record(
        _record(basis_revision="rev-0"), current_revision="rev-1"
    )
    reconciler.accept("rec-1", decided_by="dev", decided_at="2026-07-24T01:00:00Z")
    with pytest.raises(ReconciliationError, match="only conflict-pending"):
        reconciler.reject(
            "rec-1",
            decided_by="dev",
            decided_at="2026-07-24T01:01:00Z",
            rejection_reason="second look",
        )


def test_unknown_record_id_cannot_be_reconciled() -> None:
    reconciler = RecoveryReconciler()
    with pytest.raises(ReconciliationError, match="unknown record_id"):
        reconciler.accept("nope", decided_by="dev", decided_at="2026-07-24T01:00:00Z")

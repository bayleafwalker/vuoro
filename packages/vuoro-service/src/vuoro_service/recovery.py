"""Marked disaster-recovery records and authority reconciliation.

A recovery record is evidence gathered by a disconnected client during an
incident. It is never a grant, an accepted decision, or a claim that competes
with an unavailable production authority (vuoro-served-substrate-plan.md,
"Recovery authority"). Import is idempotent and offline-safe; conflicts are
reported, never auto-resolved, and require an explicit human reconciliation
decision before an authority transition.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from vuoro_service.contracts import StrictModel


RecordKind = Literal["observation", "requested-command"]
ConflictReason = Literal["stale-basis", "competing-production-advance"]
ReconciliationState = Literal["recorded", "conflict-pending", "accepted", "rejected"]


class RecoveryRecord(StrictModel):
    """One incident-scoped observation or requested command.

    ``record_kind`` is a closed, recovery-only vocabulary: it never shares a
    namespace with normal grants, accepted decisions, or claims.
    """

    record_id: str = Field(min_length=1, max_length=256)
    incident_id: str = Field(min_length=1, max_length=256)
    record_kind: RecordKind
    created_at: str = Field(min_length=1)
    basis_revision: str | None = Field(default=None, min_length=1, max_length=256)
    summary: str = Field(min_length=1)
    detail: dict[str, Any] = Field(default_factory=dict)
    requested_command: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _requested_command_matches_kind(self) -> RecoveryRecord:
        if self.record_kind == "requested-command" and self.requested_command is None:
            raise ValueError(
                "requested_command is required when record_kind is requested-command"
            )
        if self.record_kind == "observation" and self.requested_command is not None:
            raise ValueError(
                "requested_command must be omitted when record_kind is observation"
            )
        return self


class ReconciliationOutcome(StrictModel):
    """The current reconciliation status of one imported record.

    Only a ``conflict-pending`` outcome may transition, and only to
    ``accepted`` or ``rejected`` via an explicit, separate call -- never as a
    side effect of import.
    """

    record_id: str
    incident_id: str
    state: ReconciliationState
    conflict_reason: ConflictReason | None = None
    imported_basis_revision: str | None = None
    current_revision_at_import: str
    decided_by: str | None = None
    decided_at: str | None = None
    rejection_reason: str | None = None


class ReconciliationError(ValueError):
    """Raised on an invalid reconciliation transition."""


class RecoveryReconciler:
    """In-memory, idempotent recovery-record importer.

    Mirrors :class:`vuoro_service.catalog.CatalogRegistry` in staying a plain
    injectable object rather than owning persistence; a deployment wires its
    own durable store around this class the same way it wires the catalog.
    """

    def __init__(self) -> None:
        self._outcomes: dict[str, ReconciliationOutcome] = {}
        self._incident_baseline_revision: dict[str, str] = {}

    def outcome(self, record_id: str) -> ReconciliationOutcome | None:
        return self._outcomes.get(record_id)

    def history(self, incident_id: str) -> list[ReconciliationOutcome]:
        return sorted(
            (
                outcome
                for outcome in self._outcomes.values()
                if outcome.incident_id == incident_id
            ),
            key=lambda outcome: outcome.record_id,
        )

    def import_record(
        self, record: RecoveryRecord, *, current_revision: str
    ) -> ReconciliationOutcome:
        """Import ``record`` against ``current_revision``. Idempotent.

        Re-importing an already-known ``record_id`` returns the existing
        outcome unchanged -- never an error, never a duplicate entry.
        """
        existing = self._outcomes.get(record.record_id)
        if existing is not None:
            return existing

        baseline = self._incident_baseline_revision.get(record.incident_id)
        conflict_reason: ConflictReason | None = None
        if baseline is None:
            # First record seen for this incident: staleness is judged only
            # against the record's own claimed basis.
            self._incident_baseline_revision[record.incident_id] = current_revision
            if (
                record.basis_revision is not None
                and record.basis_revision != current_revision
            ):
                conflict_reason = "stale-basis"
        elif baseline != current_revision:
            # Production advanced via a non-recovery path since this
            # incident's reconciliation began: a stronger conflict than a
            # single record's own stale basis.
            conflict_reason = "competing-production-advance"
        elif (
            record.basis_revision is not None
            and record.basis_revision != current_revision
        ):
            conflict_reason = "stale-basis"

        outcome = ReconciliationOutcome(
            record_id=record.record_id,
            incident_id=record.incident_id,
            state="conflict-pending" if conflict_reason else "recorded",
            conflict_reason=conflict_reason,
            imported_basis_revision=record.basis_revision,
            current_revision_at_import=current_revision,
        )
        self._outcomes[record.record_id] = outcome
        return outcome

    def accept(
        self, record_id: str, *, decided_by: str, decided_at: str
    ) -> ReconciliationOutcome:
        return self._decide(
            record_id, state="accepted", decided_by=decided_by, decided_at=decided_at
        )

    def reject(
        self,
        record_id: str,
        *,
        decided_by: str,
        decided_at: str,
        rejection_reason: str,
    ) -> ReconciliationOutcome:
        if not rejection_reason:
            raise ReconciliationError("rejection_reason is required to reject a record")
        return self._decide(
            record_id,
            state="rejected",
            decided_by=decided_by,
            decided_at=decided_at,
            rejection_reason=rejection_reason,
        )

    def _decide(
        self,
        record_id: str,
        *,
        state: Literal["accepted", "rejected"],
        decided_by: str,
        decided_at: str,
        rejection_reason: str | None = None,
    ) -> ReconciliationOutcome:
        existing = self._outcomes.get(record_id)
        if existing is None:
            raise ReconciliationError(f"unknown record_id: {record_id}")
        if existing.state != "conflict-pending":
            raise ReconciliationError(
                f"record {record_id} is {existing.state!r}; only conflict-pending "
                "records can be reconciled"
            )
        decided = existing.model_copy(
            update={
                "state": state,
                "decided_by": decided_by,
                "decided_at": decided_at,
                "rejection_reason": rejection_reason,
            }
        )
        self._outcomes[record_id] = decided
        return decided


__all__ = [
    "ConflictReason",
    "RecordKind",
    "ReconciliationError",
    "ReconciliationOutcome",
    "ReconciliationState",
    "RecoveryReconciler",
    "RecoveryRecord",
]

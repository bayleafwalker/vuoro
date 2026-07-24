"""Local, offline incident-scoped recovery record namespace.

A disconnected workstation stays useful during an outage without becoming a
split-brain claimant: it may only append observations and requested
commands to a separate, incident-scoped namespace (never normal claims or
accepted decisions), and that namespace is export-only -- nothing here talks
to a service or mutates production authority.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4


RecordKind = Literal["observation", "requested-command"]


@dataclass(frozen=True)
class RecoveryRecordEntry:
    record_id: str
    incident_id: str
    record_kind: RecordKind
    created_at: str
    basis_revision: str | None
    summary: str
    detail: dict[str, Any]
    requested_command: dict[str, Any] | None

    def to_json(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "incident_id": self.incident_id,
            "record_kind": self.record_kind,
            "created_at": self.created_at,
            "basis_revision": self.basis_revision,
            "summary": self.summary,
            "detail": self.detail,
            "requested_command": self.requested_command,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> RecoveryRecordEntry:
        return cls(
            record_id=payload["record_id"],
            incident_id=payload["incident_id"],
            record_kind=payload["record_kind"],
            created_at=payload["created_at"],
            basis_revision=payload.get("basis_revision"),
            summary=payload["summary"],
            detail=payload.get("detail", {}),
            requested_command=payload.get("requested_command"),
        )


class RecoveryLog:
    """Append-only, restart-safe local record store for one incident.

    Records live at ``<root>/<incident_id>/records.jsonl``. Reopening the
    same ``incident_id`` after a process restart resumes the same namespace:
    nothing is lost or duplicated, because every append is a single atomic
    line write and every entry carries its own stable ``record_id``.
    """

    def __init__(self, root: Path, incident_id: str) -> None:
        self.root = root
        self.incident_id = incident_id
        self._dir = root / incident_id
        self._path = self._dir / "records.jsonl"

    @property
    def path(self) -> Path:
        return self._path

    def begin(self) -> RecoveryLog:
        """Open (or resume) the incident namespace. Idempotent."""
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)
        return self

    def _require_begun(self) -> None:
        if not self._path.exists():
            raise FileNotFoundError(
                f"recovery incident {self.incident_id!r} has not been begun "
                f"under {self.root}"
            )

    def append(
        self,
        *,
        record_kind: RecordKind,
        summary: str,
        created_at: str,
        basis_revision: str | None = None,
        detail: dict[str, Any] | None = None,
        requested_command: dict[str, Any] | None = None,
        record_id: str | None = None,
    ) -> RecoveryRecordEntry:
        self._require_begun()
        if record_kind == "requested-command" and requested_command is None:
            raise ValueError(
                "requested_command is required when record_kind is requested-command"
            )
        if record_kind == "observation" and requested_command is not None:
            raise ValueError(
                "requested_command must be omitted when record_kind is observation"
            )
        entry = RecoveryRecordEntry(
            record_id=record_id or str(uuid4()),
            incident_id=self.incident_id,
            record_kind=record_kind,
            created_at=created_at,
            basis_revision=basis_revision,
            summary=summary,
            detail=detail or {},
            requested_command=requested_command,
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_json(), sort_keys=True))
            handle.write("\n")
        return entry

    def records(self) -> Iterator[RecoveryRecordEntry]:
        self._require_begun()
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                yield RecoveryRecordEntry.from_json(json.loads(line))

    def export(self) -> list[dict[str, Any]]:
        """Render every record for hand-off to a service import path.

        Deliberately the only way this module ever surfaces its records:
        recovery records are export-only, per the plan's rollback clause.
        There is no method here that submits, applies, or otherwise mutates
        production authority.
        """
        return [entry.to_json() for entry in self.records()]


__all__ = ["RecoveryLog", "RecoveryRecordEntry"]

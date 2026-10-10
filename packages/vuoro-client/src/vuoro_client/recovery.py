"""Local, offline incident-scoped recovery record namespace.

A disconnected workstation stays useful during an outage without becoming a
split-brain claimant: it may only append observations and requested
commands to a separate, incident-scoped namespace (never normal claims or
accepted decisions), and that namespace is export-only -- nothing here talks
to a service or mutates production authority.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Literal
from uuid import uuid4


RecordKind = Literal["observation", "requested-command"]


def _locking():
    # Keep transport imports usable on other platforms. Recovery durability is
    # deliberately a POSIX-local-filesystem capability, checked when used.
    try:
        import fcntl
    except ImportError as error:
        raise RuntimeError("local recovery requires POSIX file locking") from error
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY")):
        raise RuntimeError("local recovery requires POSIX safe file descriptors")
    return fcntl


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field; preserve and inspect history")
        result[key] = value
    return result


def _json_keys(value):
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("recovery JSON object keys must be strings")
        for child in value.values():
            _json_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _json_keys(child)


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
    stable-ID retries with the same authored payload return the original
    record. Appends are locked and fsynced before successful return. A crash
    during a write can leave an incomplete tail: preserve it for inspection,
    rather than silently repairing or treating it as committed.
    """

    def __init__(self, root: Path, incident_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", incident_id):
            raise ValueError("incident_id must be one bounded local namespace name")
        self.root = root
        self.incident_id = incident_id
        self._dir = root / incident_id
        self._path = self._dir / "records.jsonl"

    @property
    def path(self) -> Path:
        return self._path

    def begin(self) -> RecoveryLog:
        """Open (or resume) the incident namespace. Idempotent."""
        _locking()
        self._check_directories()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:
                os.mkdir(self.incident_id, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            with self._namespace_fd(root_fd=root_fd) as directory:
                descriptor = self._store_fd(directory, os.O_CREAT | os.O_APPEND | os.O_RDWR)
                try:
                    os.fchmod(descriptor, 0o600)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.fsync(directory)
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
        return self

    def _check_directories(self) -> None:
        if any(path.is_symlink() for path in (self._dir, self.root, *self.root.absolute().parents)):
            raise ValueError("recovery directories must not be symlinks")

    @contextmanager
    def _namespace_fd(self, *, root_fd=None):
        descriptor = os.open(self.incident_id if root_fd is not None else self._dir,
                             os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            if os.fstat(descriptor).st_uid != os.getuid():
                raise ValueError("recovery namespace must belong to the current user")
            os.fchmod(descriptor, 0o700)
            yield descriptor
        finally:
            os.close(descriptor)

    def _store_fd(self, directory: int, flags: int) -> int:
        descriptor = os.open("records.jsonl", flags | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=directory)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise ValueError("recovery store must be a singly linked regular file owned by the current user")
            os.fchmod(descriptor, 0o600)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def _require_begun(self) -> None:
        _locking()
        self._check_directories()
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
        if record_kind not in ("observation", "requested-command"):
            raise ValueError("unsupported recovery record kind")
        if record_id is not None and (not isinstance(record_id, str) or not record_id or len(record_id) > 128):
            raise ValueError("record_id must be a bounded nonempty string")
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
            detail={} if detail is None else detail,
            requested_command=requested_command,
        )
        self._validate_entry(entry)
        # Serialize before opening: invalid authored data cannot partially append.
        _json_keys(entry.to_json())
        data = json.dumps(entry.to_json(), sort_keys=True, allow_nan=False) + "\n"
        with self._namespace_fd() as directory:
            descriptor = self._store_fd(directory, os.O_APPEND | os.O_RDWR)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
            locking = _locking()
            locking.flock(handle, locking.LOCK_EX)
            handle.seek(0)
            existing = self._read_records(handle)
            for previous in existing:
                if previous.record_id == entry.record_id:
                    if json.dumps(previous.to_json(), sort_keys=True, allow_nan=False) + "\n" != data:
                        raise ValueError("record_id conflicts with an authored recovery record")
                    return previous
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    def _validate_entry(self, entry: RecoveryRecordEntry) -> None:
        if (entry.incident_id != self.incident_id or
                entry.record_kind not in ("observation", "requested-command") or
                not isinstance(entry.record_id, str) or not 1 <= len(entry.record_id) <= 128 or
                not isinstance(entry.created_at, str) or not entry.created_at or
                not isinstance(entry.summary, str) or
                (entry.basis_revision is not None and not isinstance(entry.basis_revision, str)) or
                not isinstance(entry.detail, dict) or
                (entry.record_kind == "observation" and entry.requested_command is not None) or
                (entry.record_kind == "requested-command" and not isinstance(entry.requested_command, dict))):
            raise ValueError("invalid recovery record; preserve and inspect history")

    def _read_records(self, handle: Any) -> list[RecoveryRecordEntry]:
        result = []
        seen = set()
        for line in handle:
            if not line.endswith("\n"):
                raise ValueError("incomplete recovery record; preserve and inspect history")
            try:
                payload = json.loads(line, object_pairs_hook=_unique_object,
                                     parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                if not isinstance(payload, dict) or set(payload) != set(RecoveryRecordEntry.__dataclass_fields__):
                    raise ValueError("unexpected recovery record fields")
                entry = RecoveryRecordEntry.from_json(payload)
                self._validate_entry(entry)
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("invalid recovery history; preserve and inspect history") from error
            if entry.record_id in seen:
                raise ValueError("duplicate authored record IDs; preserve and inspect history")
            seen.add(entry.record_id)
            result.append(entry)
        return result

    def records(self) -> Iterator[RecoveryRecordEntry]:
        self._require_begun()
        with self._namespace_fd() as directory:
            descriptor = self._store_fd(directory, os.O_RDONLY)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            locking = _locking()
            locking.flock(handle, locking.LOCK_SH)
            yield from self._read_records(handle)

    def export(self) -> list[dict[str, Any]]:
        """Render every record for explicit owner review.

        Deliberately the only way this module ever surfaces its records:
        recovery records are export-only, per the plan's rollback clause.
        There is no method here that submits, applies, or otherwise mutates
        production authority.
        """
        return [entry.to_json() for entry in self.records()]


__all__ = ["RecoveryLog", "RecoveryRecordEntry"]

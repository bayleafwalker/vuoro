"""Typed domain errors. ``code`` is the stable, served error identifier."""

from __future__ import annotations

from typing import Any

FORBIDDEN = "forbidden"
NOT_FOUND = "not-found"
INVALID = "invalid-argument"
STALE_BASE = "stale-base"
IDEMPOTENCY_CONFLICT = "idempotency-conflict"
STATE_CONFLICT = "state-conflict"
OBJECT_MISSING = "object-missing"
RESERVATION_EXPIRED = "reservation-expired"
INVALID_CURSOR = "invalid-cursor"
IMPORT_INVALID = "import-invalid"
TOO_LARGE = "payload-too-large"


class WorkspaceError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}

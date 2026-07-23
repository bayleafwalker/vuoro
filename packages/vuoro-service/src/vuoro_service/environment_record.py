"""Loader for environment-record/v1 files (schema owned by agentops).

Only the bounded, non-secret fields (``constraints`` and ``runbook_refs``)
are extracted for serving over the handshake. ``roles``, ``capabilities``,
and ``identity_bindings`` are intentionally never parsed here -- identity
binding stays a server-local concern and must not be echoed over the wire.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_SCHEMA_VERSION = "environment-record/v1"
_ENVIRONMENT_CLASSES = ("local", "development", "production", "recovery")
_FORBIDDEN_KEY_MARKERS = ("secret", "credential", "password", "token")


class EnvironmentRecordError(ValueError):
    """Raised when an environment-record file is missing or malformed."""


@dataclass(frozen=True)
class EnvironmentRecord:
    id: str
    environment_class: Literal["local", "development", "production", "recovery"]
    constraints: tuple[str, ...]
    runbook_refs: tuple[str, ...]


def load_environment_record(path: Path) -> EnvironmentRecord:
    try:
        text = path.read_text()
    except FileNotFoundError as error:
        raise EnvironmentRecordError(f"environment record not found: {path}") from error

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise EnvironmentRecordError(f"environment record is not valid JSON: {path}") from error

    if not isinstance(raw, dict):
        raise EnvironmentRecordError(f"environment record must be a JSON object: {path}")

    for key in raw:
        lowered = key.lower()
        if any(marker in lowered for marker in _FORBIDDEN_KEY_MARKERS):
            raise EnvironmentRecordError(
                f"environment record {path} contains a disallowed key {key!r}; "
                "secrets/credentials must never be embedded in an environment record"
            )

    if raw.get("schema_version") != _SCHEMA_VERSION:
        raise EnvironmentRecordError(
            f"environment record {path} has schema_version {raw.get('schema_version')!r}, "
            f"expected {_SCHEMA_VERSION!r}"
        )

    try:
        record_id = raw["id"]
        environment_class = raw["environment_class"]
        constraints = tuple(raw["constraints"])
        runbook_refs = tuple(raw["runbook_refs"])
    except KeyError as error:
        raise EnvironmentRecordError(
            f"environment record {path} is missing required field {error}"
        ) from error

    if environment_class not in _ENVIRONMENT_CLASSES:
        raise EnvironmentRecordError(
            f"environment record {path} has unknown environment_class {environment_class!r}"
        )
    if not isinstance(record_id, str) or not record_id:
        raise EnvironmentRecordError(f"environment record {path} has an invalid id")
    if not all(isinstance(item, str) for item in constraints):
        raise EnvironmentRecordError(f"environment record {path} constraints must be strings")
    if not all(isinstance(item, str) for item in runbook_refs):
        raise EnvironmentRecordError(f"environment record {path} runbook_refs must be strings")

    return EnvironmentRecord(
        id=record_id,
        environment_class=environment_class,
        constraints=constraints,
        runbook_refs=runbook_refs,
    )


__all__ = ["EnvironmentRecord", "EnvironmentRecordError", "load_environment_record"]

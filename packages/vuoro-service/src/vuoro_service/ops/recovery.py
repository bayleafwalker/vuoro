"""Recovery export: ``vuoro_client.recovery.RecoveryLog.export`` with a stamp.

Export only.  The namespace is never begun (created) here: a missing
incident is reported, not initialized.  Requested commands remain text for
owner review and are never executed.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .result import DiagnosticResult


def default_recovery_root() -> Path:
    override = os.environ.get("VUORO_RECOVERY_ROOT")
    return Path(override) if override else Path.home() / ".vuoro" / "recovery"


def recovery_export(incident_id: str, *, root: Path | None = None,
                    work_refs: list[dict[str, str]] | None = None) -> DiagnosticResult:
    refs = list(work_refs or []) + [{"kind": "incident", "id": incident_id}]
    try:
        from vuoro_client.recovery import RecoveryLog
    except ImportError:
        return DiagnosticResult("recovery-export", "1", "error",
                                {"incident_id": incident_id, "error": "vuoro-client is not installed"},
                                work_refs=refs, unknowns=["vuoro-client-not-installed"])
    root = root or default_recovery_root()
    try:
        log = RecoveryLog(root, incident_id)
    except ValueError as error:
        return DiagnosticResult("recovery-export", "1", "error",
                                {"incident_id": incident_id, "error": str(error)}, work_refs=refs)
    if not log.path.is_file():
        return DiagnosticResult("recovery-export", "1", "error",
                                {"incident_id": incident_id, "root": str(root), "error": "incident namespace not found"},
                                work_refs=refs)
    try:
        records = log.export()
    except (OSError, ValueError, RuntimeError) as error:
        return DiagnosticResult("recovery-export", "1", "error",
                                {"incident_id": incident_id, "root": str(root), "error": str(error)[:300]},
                                work_refs=refs)
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    kinds: dict[str, int] = {}
    for record in records:
        kinds[record["record_kind"]] = kinds.get(record["record_kind"], 0) + 1
        if record.get("basis_revision"):
            refs.append({"kind": "basis-revision", "id": record["basis_revision"]})
    requested = [record for record in records if record["record_kind"] == "requested-command"]
    return DiagnosticResult(
        "recovery-export", "1", "attention" if requested else "ok",
        {
            "incident_id": incident_id,
            "path": str(log.path),
            "record_count": len(records),
            "record_kinds": dict(sorted(kinds.items())),
            "records_sha256": hashlib.sha256(canonical).hexdigest(),
            "requested_commands_pending_review": len(requested),
            "records": records,
        },
        work_refs=refs,
    )

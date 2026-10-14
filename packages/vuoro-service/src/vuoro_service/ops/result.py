"""The ``vuoro-diagnostic-result/v1`` envelope and its composition stamp."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from vuoro_service import __version__

RESULT_SCHEMA_VERSION = "vuoro-diagnostic-result/v1"
STATUSES = ("ok", "attention", "error")
_DEFAULT_INSTALLED_ATTESTATION = Path("/opt/vuoro/composition/installed-composition.json")
_IMAGE_MANIFEST = Path("/opt/vuoro/composition/adapter-pins.json")


def _source_manifest() -> Path:
    # src/vuoro_service/ops/result.py -> packages/vuoro-service/composition
    return Path(__file__).resolve().parents[3] / "composition" / "adapter-pins.json"


def default_manifest_path(environ: dict[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    configured = environ.get("VUORO_COMPOSITION_MANIFEST")
    if configured:
        return Path(configured)
    source = _source_manifest()
    return source if source.is_file() else _IMAGE_MANIFEST


def default_attestation_path(environ: dict[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    configured = environ.get("VUORO_INSTALLED_COMPOSITION_PATH")
    return Path(configured) if configured else _DEFAULT_INSTALLED_ATTESTATION


def load_pins(manifest_path: Path) -> dict[str, Any]:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def composition_pins_sha256(manifest_path: Path) -> str | None:
    try:
        return hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    except OSError:
        return None


def composition_stamp(
    manifest_path: Path | None = None, attestation_path: Path | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Digest the pinned composition and report the installed attestation.

    Returns the stamp and any unknowns.  An absent or unreadable attestation is
    an unknown rather than an error: a workstation has no installed image.
    """
    manifest_path = manifest_path or default_manifest_path()
    attestation_path = attestation_path or default_attestation_path()
    unknowns: list[str] = []
    stamp: dict[str, Any] = {
        "pins_path": str(manifest_path),
        "pins_sha256": None,
        "pins_schema_version": None,
        "locks": [],
        "runtime_descriptors": [],
        "manifest_valid": None,
        "service_version": __version__,
        "installed_attestation": {"path": str(attestation_path), "state": "absent"},
    }
    try:
        raw_bytes = manifest_path.read_bytes()
    except OSError:
        unknowns.append("composition-pins-unreadable")
        return stamp, unknowns
    stamp["pins_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError:
        unknowns.append("composition-pins-not-json")
        return stamp, unknowns
    stamp["pins_schema_version"] = raw.get("schema_version")
    stamp["locks"] = [
        {
            "lock_id": lock.get("lock_id"),
            "distribution": lock.get("distribution"),
            "distribution_version": lock.get("distribution_version"),
            "artifact_sha256": lock.get("artifact_sha256"),
        }
        for lock in raw.get("release_locks", [])
        if isinstance(lock, dict)
    ]
    stamp["runtime_descriptors"] = [
        {
            "domain": item.get("domain"),
            "lock_id": item.get("lock_id"),
            "api_version": item.get("api_version"),
            "schema_version": item.get("schema_version"),
        }
        for item in raw.get("runtime_descriptors", [])
        if isinstance(item, dict)
    ]
    try:
        from vuoro_service.composition import CompositionError, CompositionManifest

        CompositionManifest.load(manifest_path)
        stamp["manifest_valid"] = True
    except CompositionError as error:
        stamp["manifest_valid"] = False
        stamp["manifest_error"] = str(error)

    try:
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        unknowns.append("installed-composition-attestation-absent")
        return stamp, unknowns
    except (OSError, json.JSONDecodeError):
        stamp["installed_attestation"]["state"] = "unreadable"
        unknowns.append("installed-composition-attestation-unreadable")
        return stamp, unknowns
    matches = attestation.get("manifest_sha256") == stamp["pins_sha256"]
    stamp["installed_attestation"] = {
        "path": str(attestation_path),
        "state": "present",
        "schema_version": attestation.get("schema_version"),
        "verified": attestation.get("verified") is True,
        "manifest_sha256": attestation.get("manifest_sha256"),
        "matches_pins": matches,
        "distributions": [
            {
                "distribution": item.get("distribution"),
                "installed_version": item.get("installed_version"),
                "installed_files_sha256": item.get("installed_files_sha256"),
            }
            for item in attestation.get("distributions", [])
            if isinstance(item, dict)
        ],
    }
    return stamp, unknowns


def observed_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def result_schema() -> dict[str, Any]:
    resource = files("vuoro_service.ops").joinpath("diagnostic-result-v1.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


@dataclass
class DiagnosticResult:
    """One operation's structured outcome; ``status`` drives the exit code."""

    operation: str
    version: str
    status: str
    result: dict[str, Any]
    work_refs: list[dict[str, str]] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)

    def document(
        self,
        *,
        manifest_path: Path | None = None,
        attestation_path: Path | None = None,
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        if self.status not in STATUSES:
            raise ValueError(f"unknown diagnostic status {self.status!r}")
        stamp, stamp_unknowns = composition_stamp(manifest_path, attestation_path)
        unknowns = list(dict.fromkeys([*self.unknowns, *stamp_unknowns]))
        document = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "operation": self.operation,
            "version": self.version,
            "status": self.status,
            "composition": stamp,
            "work_refs": _dedupe_refs(self.work_refs),
            "observed_at": observed_at or observed_now(),
            "result": self.result,
            "unknowns": unknowns,
        }
        Draft202012Validator(result_schema()).validate(document)
        return document


def _dedupe_refs(refs: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for ref in refs:
        key = (ref["kind"], ref["id"])
        if key not in seen:
            seen.add(key)
            unique.append({"kind": ref["kind"], "id": ref["id"]})
    return unique


def parse_work_ref(value: str) -> dict[str, str]:
    kind, separator, identifier = value.partition(":")
    if not separator or not kind or not identifier:
        raise ValueError("work refs are KIND:ID, e.g. work-item:2600 or incident:2026-10-14-a")
    return {"kind": kind, "id": identifier}


__all__ = [
    "RESULT_SCHEMA_VERSION",
    "DiagnosticResult",
    "composition_stamp",
    "default_attestation_path",
    "default_manifest_path",
    "load_pins",
    "observed_now",
    "parse_work_ref",
    "result_schema",
]

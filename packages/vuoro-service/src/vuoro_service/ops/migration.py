"""Migration preflight: the recovery class, computed from schema ranges.

The class is never asserted by the caller.  It is derived from three
observations: the schema range each image's pinned owners admit at startup
(read from the immutable images themselves), the current schema version, and
the target.

* ``cancel`` -- the target is not yet applied: stopping now leaves the prior
  image serving the unchanged schema.
* ``image-revert`` -- the target is (or would be) committed and the prior image
  still admits it, so reverting the image digest is a compatible rollback.
* ``forward-fix-or-restore`` -- the committed schema is outside the prior
  image's range: recovery is a compatible forward image or a coordinated
  restore, never an old-image rollback.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .result import DiagnosticResult
from .runtime import ImageRunner, ProbeError, probe_source, run_in_image

RECOVERY_CLASSES = ("cancel", "image-revert", "forward-fix-or-restore")
#: The historical #2092 proof hard-codes work 5 -> 6; it proves nothing else.
STARTUP_PROOF_TARGET = 6


def admits(schema_range: Mapping[str, Any] | None, version: int | None) -> bool | None:
    if version is None or not schema_range or "minimum" not in schema_range:
        return None
    return schema_range["minimum"] <= version <= schema_range["maximum"]


def recovery_class(current: int | None, target: int, prior: Mapping[str, Any] | None) -> tuple[str | None, str]:
    """The recovery class at the observed ``current`` schema."""
    if current is None:
        return None, "current schema unknown"
    if current < target:
        return "cancel", f"schema {current} < target {target}: no migration applied yet"
    prior_admits = admits(prior, current)
    if prior_admits is None:
        return None, "prior image schema range unknown"
    if current > target:
        return ("image-revert" if prior_admits else "forward-fix-or-restore",
                f"schema {current} is beyond target {target}")
    if prior_admits:
        return "image-revert", f"prior image admits committed schema {current}"
    return "forward-fix-or-restore", (
        f"committed schema {current} is outside the prior image range "
        f"{prior['minimum']}..{prior['maximum']}"
    )


def _deduce_current(served: Mapping[str, Any] | None, served_range: Mapping[str, Any] | None,
                    domain: str) -> tuple[int | None, str]:
    """A served compatible handshake proves current is inside the served image's range."""
    if not served:
        return None, "no served observation"
    state = ((served.get("compatibility") or {}).get("domains") or {}).get(domain, {})
    if (state or {}).get("state") != "compatible":
        return None, f"served {domain} domain is not reported compatible"
    if not served_range or "minimum" not in served_range:
        return None, "served image schema range unknown"
    if served_range["minimum"] == served_range["maximum"]:
        return served_range["minimum"], (
            f"served image admits exactly {served_range['minimum']} and reports {domain} compatible"
        )
    return None, f"served image admits {served_range['minimum']}..{served_range['maximum']}; not a single version"


def startup_proof(image: str, target: int, script: Path | None) -> dict[str, Any]:
    """Wrap ``scripts/verify_pre_migration_startup.py`` only where it applies."""
    if target != STARTUP_PROOF_TARGET:
        return {"state": "not-applicable",
                "reason": f"the #2092 proof's states are fixed at work 5 -> {STARTUP_PROOF_TARGET}"}
    if script is None or not script.is_file():
        return {"state": "unavailable", "reason": "verify_pre_migration_startup.py not found"}
    if shutil.which("podman") is None:
        return {"state": "unavailable", "reason": "the proof requires podman"}
    completed = subprocess.run(["python3", str(script), image], capture_output=True, text=True,
                               timeout=1800, check=False)
    return {"state": "passed" if completed.returncode == 0 else "failed",
            "tail": completed.stdout.strip().splitlines()[-5:]}


def migration_preflight(
    *,
    candidate_image: str,
    prior_image: str,
    target_schema: int,
    domain: str = "work",
    current_schema: int | None = None,
    served_handshake: Mapping[str, Any] | None = None,
    served_image: str | None = None,
    image_runner: ImageRunner | None = None,
    run_startup_proof: bool = False,
    startup_proof_script: Path | None = None,
    work_refs: list[dict[str, str]] | None = None,
) -> DiagnosticResult:
    runner = image_runner or run_in_image
    unknowns: list[str] = []
    refs = list(work_refs or [])
    images: dict[str, dict[str, Any]] = {}
    for role, image in (("candidate", candidate_image), ("prior", prior_image),
                        *((("served", served_image),) if served_image else ())):
        refs.append({"kind": f"{role}-image", "id": image})
        if role == "served" and image in (candidate_image, prior_image):
            images[role] = images["prior" if image == prior_image else "candidate"]
            continue
        try:
            observation = runner(image, probe_source("image_composition"), {})
        except ProbeError as error:
            images[role] = {"error": str(error)[:300]}
            unknowns.append(f"{role}-image-unreadable")
            continue
        images[role] = observation

    def schema_range(role: str) -> dict[str, Any] | None:
        value = ((images.get(role) or {}).get("schema_ranges") or {}).get(domain)
        return value if value and "minimum" in value else None

    candidate_range, prior_range = schema_range("candidate"), schema_range("prior")
    served_range = schema_range("served") if served_image else prior_range
    current_source = "operator-supplied" if current_schema is not None else None
    if current_schema is None:
        current_schema, current_source = _deduce_current(served_handshake, served_range, domain)
        if current_schema is None:
            unknowns.append(f"current-{domain}-schema-unknown")
    gates = {
        "candidate_admits_target": admits(candidate_range, target_schema),
        "prior_admits_current": admits(prior_range, current_schema),
        "prior_admits_target": admits(prior_range, target_schema),
        "target_not_behind_current": None if current_schema is None else target_schema >= current_schema,
    }
    for name, value in gates.items():
        if value is None:
            unknowns.append(f"gate-unknown:{name}")
    now_class, now_reason = recovery_class(current_schema, target_schema, prior_range)
    after_class, after_reason = recovery_class(target_schema, target_schema, prior_range)
    blocked = [name for name in ("candidate_admits_target", "target_not_behind_current") if gates[name] is False]

    proof = startup_proof(candidate_image, target_schema, startup_proof_script) if run_startup_proof else {
        "state": "not-requested"}
    status = "error" if blocked or now_class is None or after_class is None else (
        "attention" if after_class == "forward-fix-or-restore" else "ok")
    result = {
        "domain": domain,
        "target_schema": target_schema,
        "current_schema": current_schema,
        "current_schema_source": current_source,
        "images": {
            role: {
                "ref": ref,
                "service_version": (images.get(role) or {}).get("service_version"),
                "distributions": (images.get(role) or {}).get("distributions"),
                "manifest_sha256": (images.get(role) or {}).get("manifest_sha256"),
                "attestation": (images.get(role) or {}).get("attestation"),
                "schema_range": schema_range(role),
                **({"error": images[role]["error"]} if "error" in (images.get(role) or {}) else {}),
            }
            for role, ref in (("candidate", candidate_image), ("prior", prior_image),
                              *((("served", served_image),) if served_image else ()))
        },
        "gates": gates,
        "blocked_by": blocked,
        "recovery_class": now_class,
        "recovery_class_reason": now_reason,
        "recovery_class_after_commit": after_class,
        "recovery_class_after_commit_reason": after_reason,
        "startup_proof": proof,
    }
    return DiagnosticResult("migration-preflight", "1", status, result, work_refs=refs, unknowns=unknowns)

"""Command-capture ingress: an outctl capture manifest under an auditctl-style
validity window → claims.

auditctl has no command-capture event type; captures are `immutableRef
kind=artifact` material. The validity window is declared by the collector
(auditctl `harness.baseline` pattern: bounded, or valid until named input
digests move) and passed in, never inferred from the capture.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from ..core.model import Claim, ClaimType, EvidenceItem, ValidityBasis, ValidityWindow
from .registry import register

COLLECTOR = "vuoro-evidence/command-capture"


def decode(manifest: Mapping[str, Any], *, subject: str, grant_id: str | None = None,
           collected_at: datetime | None = None, valid_until: datetime | None = None,
           component_digests: Mapping[str, str] | None = None, ref: str | None = None) -> EvidenceItem:
    at = collected_at or datetime.now(timezone.utc)
    if component_digests:
        window = ValidityWindow(ValidityBasis.UNTIL_INPUTS_CHANGE, at, None, dict(component_digests))
    elif valid_until is not None:
        window = ValidityWindow(ValidityBasis.BOUNDED, at, valid_until)
    else:
        window = ValidityWindow(ValidityBasis.INDEFINITE, at)
    cmd = manifest["command"]
    digest = "sha256:" + hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    cid = manifest["capture_id"]
    claims: list[Claim] = []
    if not cmd.get("started", False):
        claims.append(Claim(ClaimType.EFFECT_NOT_INVOKED, subject, grant_id))
    elif cmd.get("exit_code") is None or cmd.get("timed_out") or cmd.get("cancelled"):
        # the process ran but its result is not attributable: neither success nor clean failure
        claims.append(Claim(ClaimType.EFFECT_UNCERTAIN, subject, grant_id, detail=dict(cmd)))
    elif cmd["exit_code"] == 0:
        claims.append(Claim(ClaimType.EFFECT_COMPLETED, subject, grant_id, detail={"exit_code": 0}))
    else:
        claims.append(Claim(ClaimType.EFFECT_FAILED, subject, grant_id, detail={"exit_code": cmd["exit_code"]}))
    for name, s in manifest.get("streams", {}).items():
        claims.append(Claim(ClaimType.EVIDENCE_PERSISTED, subject, grant_id,
                            detail={"ref": f"outctl://capture/{cid}/{name}", "sha256": s.get("sha256"), "bytes": s.get("bytes")}))
    return EvidenceItem(ref or f"capture:{cid}", "command-capture", ref or f"outctl://capture/{cid}", digest,
                        COLLECTOR, window, tuple(claims),
                        {"capture_status": manifest.get("capture_status"), "termination": manifest.get("termination")})


register("command-capture", decode)

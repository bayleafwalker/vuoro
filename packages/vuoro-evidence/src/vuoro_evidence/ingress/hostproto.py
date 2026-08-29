"""HostProto ingress: receipts, errors, observations and evidence refs → claims.

HostProto is the reference ingress format for host-interaction claims and
effect-receipt evidence (hostproto-semantics ADR-0013). Every object decoded
here becomes a *claim*; grant correlation is supplied by the caller, never
read off the wire.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator, Mapping

from ..core.model import (Claim, ClaimType, EvidenceItem, Freshness, ValidityBasis,
                          ValidityWindow)
from .registry import register

COLLECTOR = "vuoro-evidence/hostproto"
_STALE = {"target_invalidated", "handle_expired", "writer_fenced", "writer_conflict", "integrity_mismatch"}
_CAPABILITY = {"capability_unsupported", "capability_blocked", "capability_degraded"}
_OUTCOME = {"completed": ClaimType.EFFECT_COMPLETED, "failed": ClaimType.EFFECT_FAILED,
            "stopped": ClaimType.EFFECT_FAILED, "superseded": ClaimType.EFFECT_FAILED,
            "unknown": ClaimType.EFFECT_UNCERTAIN}


def canonical_digest(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _now(collected_at: datetime | None) -> datetime:
    return collected_at or datetime.now(timezone.utc)


def _indefinite(at: datetime) -> ValidityWindow:
    return ValidityWindow(ValidityBasis.INDEFINITE, at)


def decode(payload: Mapping[str, Any], *, subject: str | None = None, grant_id: str | None = None,
           collected_at: datetime | None = None, expected_state: str | None = None,
           confirm: Callable[[Mapping[str, Any]], bool] | None = None, ref: str | None = None) -> EvidenceItem:
    sv = payload.get("schema_version")
    at = _now(collected_at)
    digest = canonical_digest(payload)
    prov = {"schema_version": sv, "provider": payload.get("provider")}
    if sv == "hostproto.receipt/v1":
        subj = subject or payload["action_id"]
        fresh = Freshness(payload["surface"], int(payload["revision_after"]))
        claims: list[Claim] = []
        if not payload["attempted"] or not payload["accepted"]:
            claims.append(Claim(ClaimType.EFFECT_NOT_INVOKED, subj, grant_id, fresh))
        else:
            claims.append(Claim(_OUTCOME[payload["outcome"]], subj, grant_id, fresh,
                                detail={"verified": payload["verified"], "executed": payload["executed"],
                                        "state_after": payload.get("state_after"), "effects": payload.get("effects", [])}))
        for ev in payload.get("evidence", []):
            claims.append(Claim(ClaimType.EVIDENCE_PERSISTED, subj, grant_id, detail={"ref": ev.get("ref"), "media_type": ev.get("media_type")}))
        return EvidenceItem(ref or f"receipt:{payload['receipt_id']}", "host-receipt", ref or payload["receipt_id"],
                            digest, COLLECTOR, _indefinite(at), tuple(claims), prov)
    if sv == "hostproto.error/v1":
        if subject is None:
            raise ValueError("an error carries no action id; pass subject=")
        code = payload["code"]
        t = (ClaimType.TARGET_STALE if code in _STALE else ClaimType.PRECONDITION_REFUSED if code == "precondition_failed"
             else ClaimType.CAPABILITY_UNAVAILABLE if code in _CAPABILITY else ClaimType.EFFECT_FAILED)
        claims = [Claim(t, subject, grant_id, detail={"code": code, **dict(payload.get("data", {}))})]
        if not payload["host_invoked"]:
            claims.append(Claim(ClaimType.EFFECT_NOT_INVOKED, subject, grant_id, detail={"code": code}))
        return EvidenceItem(ref or f"error:{subject}:{digest[7:19]}", "host-error", ref or digest, digest, COLLECTOR,
                            _indefinite(at), tuple(claims), prov)
    if sv == "hostproto.observation/v1":
        if subject is None:
            raise ValueError("an observation is about a subject; pass subject=")
        state = canonical_digest(payload["data"])
        confirms = (state == expected_state) if expected_state is not None else (bool(confirm(payload["data"])) if confirm else None)
        fresh = Freshness(payload["surface"], int(payload["revision"]))
        claim = Claim(ClaimType.OBSERVATION, subject, grant_id, fresh, confirms,
                      detail={"state": state, "expected_state": expected_state, "cursor": dict(payload["cursor"])})
        return EvidenceItem(ref or f"observation:{payload['surface']}@{payload['revision']}", "host-observation",
                            ref or digest, digest, COLLECTOR, _indefinite(at), (claim,), prov)
    if sv == "hostproto.evidence-ref/v1":
        if subject is None:
            raise ValueError("pass subject=")
        claim = Claim(ClaimType.EVIDENCE_PERSISTED, subject, grant_id,
                      detail={"ref": payload["ref"], "media_type": payload["media_type"], "size_bytes": payload["size_bytes"]})
        return EvidenceItem(ref or payload["ref"], "host-evidence", payload["ref"], payload["ref"], COLLECTOR,
                            _indefinite(at), (claim,), prov)
    raise ValueError(f"not a HostProto object: {sv!r}")


def session_log(lines: Iterable[str | Mapping[str, Any]], *, grant_for_action: Mapping[str, str] | None = None,
                observation_subject: Callable[[Mapping[str, Any]], tuple[str, Callable[[Mapping[str, Any]], bool] | None] | None] | None = None,
                collected_at: datetime | None = None) -> Iterator[EvidenceItem]:
    """Replay a recorded MCP session (one JSON object per line: tool, args,
    is_error, structured) as evidence items. Errors take their subject from the
    intent's action_id; observations are about whatever subject the caller's
    correlator names, with an optional confirmation predicate over the data."""
    grants = grant_for_action or {}
    for n, raw in enumerate(lines):
        rec = json.loads(raw) if isinstance(raw, str) else raw
        sc = rec.get("structured")
        if not isinstance(sc, Mapping) or "schema_version" not in sc:
            continue
        sv = sc["schema_version"]
        ref = f"session:{rec.get('seq', n)}"
        if sv == "hostproto.receipt/v1":
            yield decode(sc, grant_id=grants.get(sc["action_id"]), collected_at=collected_at, ref=ref)
        elif sv == "hostproto.error/v1":
            action = (rec.get("args") or {}).get("action_id")
            if action:
                yield decode(sc, subject=action, grant_id=grants.get(action), collected_at=collected_at, ref=ref)
        elif sv in ("hostproto.observation/v1", "hostproto.evidence-ref/v1") and observation_subject:
            named = observation_subject(rec)
            if named:
                subject, confirm = named
                yield decode(sc, subject=subject, grant_id=grants.get(subject), collected_at=collected_at, confirm=confirm, ref=ref)


register("hostproto", decode)

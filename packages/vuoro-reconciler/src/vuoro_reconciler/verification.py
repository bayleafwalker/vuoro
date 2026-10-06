"""Check immutable protected owner receipt bindings before external effects.

These checks verify the relationship of an accepted assertion to this artifact;
they do not execute the named checks or attest their execution. Only the native
owner authenticates the verifier and authorizes acceptance.
"""
import hashlib
import json
import re

from .intents import EffectIntent

_SHA = re.compile(r"sha256:[0-9a-f]{64}")


class PreflightRefused(ValueError):
    """A stable refusal code; never an upstream error or arbitrary claim text."""


def validate_verification(intent: EffectIntent, *, required: bool) -> None:
    acceptance = intent.acceptance or {}
    proof = acceptance.get("verification")
    if proof is None:
        if required:
            raise PreflightRefused("effect-verification-required")
        return
    try:
        if set(proof) != {"run_id", "item_id", "evidence_digest", "entry_digest",
                          "verifier_principal", "workspace_id", "client_id", "grant_id", "receipt"}:
            raise ValueError
        if (intent.release_digest is None or proof["verifier_principal"] != acceptance["acceptor_principal"]
                or not all(isinstance(proof[key], str) and proof[key] for key in ("run_id", "item_id", "workspace_id", "verifier_principal"))
                or any(proof[key] is not None and not isinstance(proof[key], str) for key in ("client_id", "grant_id"))
                or not all(isinstance(proof[key], str) and _SHA.fullmatch(proof[key]) for key in ("evidence_digest", "entry_digest"))):
            raise ValueError
        detail = proof["receipt"]
        if (set(detail) != {"schema", "intent_id", "intent_revision", "canonical_intent_digest", "release_digest", "artifact", "checks"}
                or detail["schema"] != "sprintctl-protected-artifact-verification/v1"
                or detail["intent_id"] != intent.intent_id
                or type(detail["intent_revision"]) is not int or detail["intent_revision"] != intent.revision
                or detail["canonical_intent_digest"] != intent.canonical_intent_digest
                or detail["release_digest"] != intent.release_digest):
            raise ValueError
        artifact = detail["artifact"]
        raw_digest = "sha256:" + hashlib.sha256(intent.unified_diff.encode("utf-8")).hexdigest()
        if artifact != {"domain": "utf8-unified-diff/v1", "digest": raw_digest}:
            raise ValueError
        checks = detail["checks"]
        if not isinstance(checks, list) or not 1 <= len(checks) <= 64:
            raise ValueError
        names = set()
        for check in checks:
            if (set(check) != {"name", "revision", "status"}
                    or not isinstance(check["name"], str) or not check["name"].strip()
                    or len(check["name"]) > 200 or check["name"] in names
                    or not isinstance(check["revision"], str) or not _SHA.fullmatch(check["revision"])
                    or check["status"] != "passed"):
                raise ValueError
            names.add(check["name"])
        encoded = json.dumps(detail, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        if proof["evidence_digest"] != "sha256:" + hashlib.sha256(encoded).hexdigest():
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise PreflightRefused("effect-verification-refused") from None

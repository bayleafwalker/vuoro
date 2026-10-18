"""Disposable signed-artifact fixture semantics; this key is test-only."""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

DOMAIN = "vuoro-ec00-artifact-decision/v1"
KEY_ID = "ec00-test-ed25519-v1"
# Public deterministic test material only. Never reuse outside this fixture.
_TEST_SEED = bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
_TEST_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(_TEST_SEED)
TEST_PUBLIC_KEY = _TEST_PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def make_signed_artifact(*, workspace_id: str, request_id: str, material_revision: int,
                         material_digest: str, original_bytes: bytes,
                         intent: str = "accept", domain: str = DOMAIN) -> dict[str, Any]:
    artifact_digest = "sha256:" + hashlib.sha256(original_bytes).hexdigest()
    envelope = {
        "artifact_digest": artifact_digest,
        "domain": domain,
        "intent": intent,
        "key_id": KEY_ID,
        "material_digest": material_digest,
        "request_id": request_id,
        "material_revision": material_revision,
        "workspace_id": workspace_id,
    }
    envelope_bytes = canonical_bytes(envelope)
    signature = _TEST_PRIVATE_KEY.sign(envelope_bytes)
    return {
        "artifact_digest": artifact_digest,
        "key_id": KEY_ID,
        "original_bytes": original_bytes,
        "envelope_bytes": envelope_bytes,
        "signature": signature,
    }


def verify_signed_artifact(*, workspace_id: str, request_id: str, material_revision: int,
                           material_digest: str, artifact_digest: str, key_id: str,
                           original_bytes: bytes, envelope_bytes: bytes,
                           signature: bytes) -> None:
    computed_digest = "sha256:" + hashlib.sha256(original_bytes).hexdigest()
    if computed_digest != artifact_digest or key_id != KEY_ID:
        raise ValueError("artifact bytes, digest, or key identifier mismatch")
    expected = canonical_bytes({
        "artifact_digest": computed_digest,
        "domain": DOMAIN,
        "intent": "accept",
        "key_id": KEY_ID,
        "material_digest": material_digest,
        "request_id": request_id,
        "material_revision": material_revision,
        "workspace_id": workspace_id,
    })
    if envelope_bytes != expected:
        raise ValueError("signed envelope does not bind the exact artifact and owner context")
    try:
        Ed25519PublicKey.from_public_bytes(TEST_PUBLIC_KEY).verify(signature, envelope_bytes)
    except Exception as exc:
        raise ValueError("artifact signature verification failed") from exc


def transport_fields(artifact: dict[str, Any]) -> dict[str, Any]:
    """Convert an oracle artifact record to the OpenAPI base64 wire shape."""
    return {
        "artifactDigest": artifact["artifact_digest"],
        "artifactKeyId": artifact["key_id"],
        "artifactBytes": base64.b64encode(artifact["original_bytes"]).decode("ascii"),
        "artifactEnvelope": base64.b64encode(artifact["envelope_bytes"]).decode("ascii"),
        "artifactSignature": base64.b64encode(artifact["signature"]).decode("ascii"),
    }

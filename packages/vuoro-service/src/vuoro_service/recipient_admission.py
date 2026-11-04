"""Unconnected verifier for a proposed recipient-admission assertion.

This local v1 wire schema is not yet an interoperable issuer contract. The
owner must atomically enforce one use of each jti in its durable delivery
ledger; successful verification alone does not admit or authorize a delivery.
Trust and expected bindings must come exclusively from trusted configuration
and owner state, never from the requester. The provisional schema uses exact
JSON claim names from AdmissionBinding, integer Unix seconds, a distinct issuer
and audience, and a set of original Cloud OAuth scopes. Its effective_scope is
the admitted internal work:read ceiling, not an OAuth scope. This schema has no
provider interoperability guarantee. The scope vocabulary is pinned to the
currently admitted Cloud MCP scopes; a vocabulary change requires review.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
from dataclasses import dataclass, fields, replace
from typing import Mapping

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

AUDIENCE = "vuoro-recipient-admission/v1"
OPERATION = "recipient-admission/v1"
_CLOUD_SCOPES = frozenset({"vuoro:work.read", "vuoro:evidence.record",
                           "vuoro:effect.read", "vuoro:work.claim"})
_MAX_INTEGER = 2**53 - 1


class AdmissionVerificationError(ValueError):
    """Generic verification failure; do not expose token or claim contents."""


@dataclass(frozen=True)
class AdmissionBinding:
    operation: str
    caller: str
    request: str
    resolution: str
    work: str
    binding: str
    recipient_principal: str
    recipient_instance: str
    workspace: str
    repo: str
    gateway_issuer: str
    subject: str
    grant: str
    client: str
    epoch: int
    resource: str
    scopes: tuple[str, ...]
    effective_scope: str


@dataclass(frozen=True)
class VerifiedAdmission:
    binding: AdmissionBinding
    jti: str
    issued_at: int
    expires_at: int
    authority_read_start: int
    authority_read_end: int


def _text(value: object) -> bool:
    return (isinstance(value, str) and bool(value) and value == value.strip())


def _integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX_INTEGER


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _decode_part(part: str) -> bytes:
    if not part or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in part):
        raise ValueError("encoding")
    raw = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    if base64.urlsafe_b64encode(raw).decode().rstrip("=") != part:
        raise ValueError("noncanonical encoding")
    return raw


def _json_part(part: str) -> dict[str, object]:
    value = json.loads(_decode_part(part), object_pairs_hook=_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if not isinstance(value, dict):
        raise ValueError("object")
    return value


def _valid_binding(binding: AdmissionBinding) -> bool:
    if not isinstance(binding, AdmissionBinding):
        return False
    for field in fields(binding):
        value = getattr(binding, field.name)
        if field.name == "epoch":
            if not _integer(value):
                return False
        elif field.name == "scopes":
            if (not isinstance(value, tuple) or not value
                    or not all(_text(scope) for scope in value) or len(set(value)) != len(value)):
                return False
        elif not _text(value):
            return False
    return (binding.operation == OPERATION and binding.effective_scope == "work:read"
            and set(binding.scopes) <= _CLOUD_SCOPES
            and {"vuoro:work.read", "vuoro:evidence.record"} <= set(binding.scopes))


def verify_recipient_admission(
    token: str, *, keys: Mapping[str, Ed25519PublicKey], issuer: str,
    audience: str, expected: AdmissionBinding, now: float,
) -> VerifiedAdmission:
    """Verify a signed assertion against trusted inputs; raise one generic error.

    ``now`` is wall-clock Unix seconds, supplied by the trusted caller. This
    function does not provide replay protection, grant checks, or admission.
    """
    try:
        if (not isinstance(token, str) or not _text(issuer)
                or audience != AUDIENCE or not _valid_binding(expected)
                or isinstance(now, bool) or not isinstance(now, (int, float))
                or not math.isfinite(now) or now < 0):
            raise ValueError()
        parts = token.split(".")
        if len(parts) != 3 or len(_decode_part(parts[2])) != 64:
            raise ValueError()
        header, claims = _json_part(parts[0]), _json_part(parts[1])
        if set(header) != {"alg", "typ", "kid"} or header["alg"] != "EdDSA" or header["typ"] != "JWT":
            raise ValueError()
        kid = header["kid"]
        if not _text(kid) or not isinstance(keys.get(kid), Ed25519PublicKey):
            raise ValueError()
        binding_names = {f.name for f in fields(AdmissionBinding)}
        if set(claims) != binding_names | {"iss", "aud", "jti", "iat", "exp", "authority_read_start", "authority_read_end"}:
            raise ValueError()
        jwt.decode(token, keys[kid], algorithms=["EdDSA"], options={
            "verify_exp": False, "verify_iat": False, "verify_nbf": False,
            "verify_aud": False, "verify_iss": False,
        })
        if claims["iss"] != issuer or claims["aud"] != audience or not _text(claims["jti"]):
            raise ValueError()
        scopes = claims["scopes"]
        if not isinstance(scopes, list):
            raise ValueError()
        binding = AdmissionBinding(**{name: tuple(scopes) if name == "scopes" else claims[name] for name in binding_names})
        if (not _valid_binding(binding) or set(binding.scopes) != set(expected.scopes)
                or replace(binding, scopes=expected.scopes) != expected):
            raise ValueError()
        iat, exp, start, end = (claims[n] for n in ("iat", "exp", "authority_read_start", "authority_read_end"))
        if not all(_integer(v) for v in (iat, exp, start, end)):
            raise ValueError()
        if not (iat < exp and exp - iat <= 30 and now < exp and iat <= now + 2
                and start <= end <= now + 2 and start >= now - 30
                and iat >= end - 2 and end < exp):
            raise ValueError()
        return VerifiedAdmission(binding, claims["jti"], iat, exp, start, end)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, UnicodeError, binascii.Error, jwt.PyJWTError):
        raise AdmissionVerificationError("recipient admission assertion invalid") from None

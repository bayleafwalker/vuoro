"""Verify Cloud's independent service identity for private owner routes.

This identity is deliberately distinct from ``X-Vuoro-Identity``: the latter
names the end user and is still verified by Sprintctl's gateway assertion
resolver.  This verifier authenticates only the Cloud delivery adapter process.
It is useful only when paired with the tenant NetworkPolicy and an owner route
that requires both guards.
"""

from __future__ import annotations

import hashlib
import ipaddress
import math
import re
import time
from collections.abc import Iterable
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Request

from .replay import ReplayCache, ReplayCacheFull

SERVICE_ASSERTION_HEADER = "x-vuoro-cloud-service-assertion"
SERVICE_AUDIENCE = "sprintctl-trusted-cloud-adapter/v1"
SERVICE_SUBJECT = "urn:vuoro:service:cloud:operator-request-delivery"
SERVICE_PATHS = frozenset(
    {
        "/internal/operator-request-delivery/v1",
        "/internal/operator-request-delivery/v1/resolve",
        "/internal/operator-request-delivery/v1/receipts",
    }
)
_KID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_CLAIMS = frozenset(
    {
        "aud",
        "body_sha256",
        "exp",
        "iat",
        "iss",
        "jti",
        "method",
        "nbf",
        "path",
        "sub",
        "workspace_id",
    }
)
_SKEW_SECONDS = 2
_MAX_LIFETIME_SECONDS = 30


class CloudServiceAssertionVerifier:
    """Verify a short-lived, route-bound assertion from the Cloud adapter."""

    def __init__(
        self,
        public_key_file: Path,
        *,
        issuer: str,
        workspace_id: str,
        key_id: str,
        forbidden_public_keys: Iterable[Ed25519PublicKey] = (),
        replay_not_before: float | None = None,
        replay_cache: ReplayCache | None = None,
        clock=time.time,
    ) -> None:
        if not isinstance(public_key_file, Path):
            raise TypeError("Cloud service verification key path is required")
        if not isinstance(issuer, str) or not issuer.strip():
            raise ValueError("Cloud service issuer is required")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("owner workspace identity is required")
        if not isinstance(key_id, str) or not _KID.fullmatch(key_id):
            raise ValueError("Cloud service key id is invalid")
        try:
            key = serialization.load_pem_public_key(public_key_file.read_bytes())
        except (OSError, ValueError) as error:
            raise ValueError("Cloud service verification key is unavailable") from error
        if not isinstance(key, Ed25519PublicKey):
            raise TypeError("Cloud service verification key must be Ed25519")
        service_key_bytes = key.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        if any(
            not isinstance(other, Ed25519PublicKey)
            or other.public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
            == service_key_bytes
            for other in forbidden_public_keys
        ):
            raise ValueError(
                "Cloud service key must be distinct from user and admission keys"
            )
        self._key = key
        self._issuer = issuer
        self._workspace_id = workspace_id
        self._key_id = key_id
        self._clock = clock
        self._replay_not_before = (
            self._clock() - 1 if replay_not_before is None else replay_not_before
        )
        if (
            isinstance(self._replay_not_before, bool)
            or not isinstance(self._replay_not_before, (int, float))
            or not math.isfinite(self._replay_not_before)
        ):
            raise ValueError("Cloud service replay watermark is invalid")
        self._replay_cache = (
            replay_cache
            if replay_cache is not None
            else ReplayCache(name="cloud-service-assertion")
        )

    def __call__(self, request: Request) -> bool:
        """Return true only for a signed, fresh Cloud service request."""
        peer = request.client
        if peer is None:
            return False
        try:
            if not ipaddress.ip_address(peer.host).is_loopback:
                return False
        except ValueError:
            return False
        values = [
            value.decode("latin-1")
            for name, value in request.scope.get("headers", [])
            if name.lower() == SERVICE_ASSERTION_HEADER.encode("ascii")
        ]
        if len(values) != 1 or not values[0] or len(values[0]) > 8192:
            return False
        if request.method != "POST" or request.url.path not in SERVICE_PATHS:
            return False
        token = values[0]
        try:
            header = jwt.get_unverified_header(token)
            if header != {"alg": "EdDSA", "kid": self._key_id, "typ": "JWT"}:
                return False
            claims = jwt.decode(
                token,
                self._key,
                algorithms=["EdDSA"],
                issuer=self._issuer,
                audience=SERVICE_AUDIENCE,
                leeway=_SKEW_SECONDS,
                options={"require": sorted(_CLAIMS)},
            )
        except (jwt.PyJWTError, TypeError, ValueError):
            return False
        if (
            set(claims) != _CLAIMS
            or type(claims.get("aud")) is not str
            or claims.get("aud") != SERVICE_AUDIENCE
            or claims.get("sub") != SERVICE_SUBJECT
            or claims.get("workspace_id") != self._workspace_id
            or claims.get("method") != request.method
            or claims.get("path") != request.url.path
        ):
            return False
        bounded_body = getattr(request.state, "vuoro_bounded_body", None)
        if (
            not isinstance(bounded_body, bytes)
            or claims.get("body_sha256") != hashlib.sha256(bounded_body).hexdigest()
        ):
            return False
        iat, nbf, exp = claims.get("iat"), claims.get("nbf"), claims.get("exp")
        if any(type(value) is not int for value in (iat, nbf, exp)):
            return False
        now = self._clock()
        if (
            exp <= iat
            or exp - iat > _MAX_LIFETIME_SECONDS
            or iat > now + _SKEW_SECONDS
            or nbf > now + _SKEW_SECONDS
            or exp <= now
            or iat < self._replay_not_before
        ):
            return False
        jti = claims.get("jti")
        if not isinstance(jti, str) or not jti or len(jti) > 128:
            return False
        try:
            return self._replay_cache.consume(
                hashlib.sha256(jti.encode("utf-8")).digest(), exp + _SKEW_SECONDS
            )
        except ReplayCacheFull:
            return False


def load_cloud_service_assertion_verifier(
    environ: dict[str, str],
    *,
    workspace_id: str,
    forbidden_public_keys: Iterable[Ed25519PublicKey] = (),
) -> CloudServiceAssertionVerifier:
    """Load exact deployment trust; missing or malformed values fail closed."""
    try:
        path = Path(environ["VUORO_CLOUD_SERVICE_PUBLIC_KEY_FILE"])
        issuer = environ["VUORO_CLOUD_SERVICE_ISSUER"]
        key_id = environ["VUORO_CLOUD_SERVICE_KEY_ID"]
    except KeyError as error:
        raise ValueError(
            "Cloud service verification configuration is incomplete"
        ) from error
    return CloudServiceAssertionVerifier(
        path,
        issuer=issuer,
        workspace_id=workspace_id,
        key_id=key_id,
        forbidden_public_keys=forbidden_public_keys,
    )

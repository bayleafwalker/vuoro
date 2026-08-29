"""Gateway-signed identity assertions for hosted Vuoro runtimes."""

from __future__ import annotations

from base64 import urlsafe_b64decode
import binascii
from collections.abc import Mapping
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Request

from vuoro_service.identity import Identity, IdentityResolutionError


_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_REPO_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_PRINCIPAL_SUBJECT = re.compile(r"^[A-Za-z0-9._-]+$")
_NBF_CLOCK_SKEW_SECONDS = 2
_REQUIRED_CLAIMS = (
    "actor",
    "authorities",
    "principal_epoch",
    "subject",
    "exp",
    "iat",
    "iss",
    "jti",
    "nbf",
    "repo_ids",
    "request_id",
    "sub",
    "workspace_id",
)


class GatewayAssertionConfigurationError(ValueError):
    """The mounted gateway verification key or trust configuration is invalid."""


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise IdentityResolutionError(f"gateway identity assertion has invalid {field}")
    return value


def _bounded_strings(value: Any, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise IdentityResolutionError(f"gateway identity assertion has invalid {field}")
    if not allow_empty and not value:
        raise IdentityResolutionError(f"gateway identity assertion has invalid {field}")
    result = tuple(_required_text(item, field) for item in value)
    if len(result) != len(set(result)):
        raise IdentityResolutionError(f"gateway identity assertion has duplicate {field}")
    return result


def _single_header(request: Request, name: str) -> str | None:
    values = [
        value.decode("latin-1")
        for key, value in request.scope.get("headers", [])
        if key.lower() == name.encode("ascii")
    ]
    if len(values) > 1:
        raise IdentityResolutionError(f"gateway identity request has duplicate {name}")
    return values[0] if values else None


def _decode_unverified_header(token: str) -> Mapping[str, Any]:
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise IdentityResolutionError("gateway identity assertion is not a compact JWT")
    try:
        padding = "=" * (-len(parts[0]) % 4)
        header = json.loads(urlsafe_b64decode(parts[0] + padding))
    except (ValueError, binascii.Error, json.JSONDecodeError) as error:
        raise IdentityResolutionError("gateway identity assertion header is invalid") from error
    if not isinstance(header, dict):
        raise IdentityResolutionError("gateway identity assertion header is invalid")
    return header


class GatewayAssertionIdentityResolver:
    """Resolve Cloud's short-lived Ed25519 assertion into service identity.

    The resolver trusts only the mounted public key.  The gateway remains the
    owner of external token and workspace state; Vuoro verifies the signed
    actor, authority, repository, and request scope before its normal service
    authorization checks run.
    """

    def __init__(
        self,
        public_key: Ed25519PublicKey,
        *,
        issuer: str,
        audience: str,
        environment: str,
        expected_workspace_id: str,
        allowed_repo_ids: frozenset[str],
        key_id: str,
    ) -> None:
        self._public_key = public_key
        self._issuer = issuer
        self._audience = audience
        self._environment_name = environment
        self._expected_workspace_id = expected_workspace_id
        self._allowed_repo_ids = allowed_repo_ids
        self._key_id = key_id

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        issuer: str,
        audience: str = "vuoro-service",
        environment: str,
        expected_workspace_id: str,
        allowed_repo_ids: frozenset[str],
        key_id: str = "gateway-2026-01",
        trusted_root: Path | None = None,
    ) -> "GatewayAssertionIdentityResolver":
        if (
            not issuer
            or issuer != issuer.strip()
            or not audience
            or audience != audience.strip()
            or not environment
            or environment != environment.strip()
            or not key_id
            or key_id != key_id.strip()
            or not _ULID.fullmatch(expected_workspace_id)
            or not allowed_repo_ids
        ):
            raise GatewayAssertionConfigurationError(
                "gateway assertion trust configuration is invalid"
            )
        try:
            resolved = path.resolve(strict=True)
            if trusted_root is not None:
                root = trusted_root.resolve(strict=True)
                try:
                    resolved.relative_to(root)
                except ValueError as error:
                    raise GatewayAssertionConfigurationError(
                        "gateway assertion key resolves outside its trusted root"
                    ) from error
            key = serialization.load_pem_public_key(resolved.read_bytes())
        except (OSError, ValueError, TypeError) as error:
            raise GatewayAssertionConfigurationError(
                "cannot load gateway assertion verification key"
            ) from error
        if not isinstance(key, Ed25519PublicKey):
            raise GatewayAssertionConfigurationError(
                "gateway assertion verification key must be Ed25519"
            )
        return cls(
            key,
            issuer=issuer,
            audience=audience,
            environment=environment,
            expected_workspace_id=expected_workspace_id,
            allowed_repo_ids=allowed_repo_ids,
            key_id=key_id,
        )

    def __call__(self, request: Request) -> Identity:
        token = _single_header(request, "x-vuoro-identity")
        request_id = _single_header(request, "x-request-id")
        if not token or not request_id:
            raise IdentityResolutionError("gateway identity assertion is required")
        header = _decode_unverified_header(token)
        if (
            header.get("typ") != "JWT"
            or header.get("alg") != "EdDSA"
            or header.get("kid") != self._key_id
        ):
            raise IdentityResolutionError("gateway identity assertion header is invalid")
        try:
            claims = jwt.decode(
                token,
                self._public_key,
                algorithms=["EdDSA"],
                audience=self._audience,
                issuer=self._issuer,
                leeway=_NBF_CLOCK_SKEW_SECONDS,
                options={"require": list(_REQUIRED_CLAIMS)},
            )
        except jwt.InvalidTokenError as error:
            raise IdentityResolutionError("gateway identity assertion is invalid") from error
        if not isinstance(claims, dict):
            raise IdentityResolutionError("gateway identity assertion is invalid")

        actor = _required_text(claims.get("actor"), "actor")
        sub = _required_text(claims.get("sub"), "sub")
        if sub != actor:
            raise IdentityResolutionError("gateway identity actor and subject disagree")
        # The principal id is composed from `subject`, the opaque colon-free id
        # Vuoro Cloud mints per user/connector -- never from the actor string,
        # which contains a colon and would make `{iss}:{actor}:{epoch}` ambiguous
        # when parsed (vuoro-cloud principal_epoch backlog, D-2 / E-8).
        subject = _required_text(claims.get("subject"), "subject")
        if not _PRINCIPAL_SUBJECT.fullmatch(subject):
            raise IdentityResolutionError("gateway identity assertion has invalid subject")
        # The claim that makes ownership survivable. `iat` is per *request* on a
        # 30-second assertion, so nothing else in this claim set can distinguish
        # a reissued actor from the original -- and federation ownership is a
        # comparison against a stored principal id with no transfer operation.
        # An epoch that increments on reissue makes a reissued actor a different
        # principal by construction. Vuoro Cloud mints and persists it per
        # subject; this side only refuses an assertion without one.
        epoch = claims.get("principal_epoch")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
            raise IdentityResolutionError(
                "gateway identity assertion has invalid principal_epoch"
            )
        workspace_id = _required_text(claims.get("workspace_id"), "workspace_id")
        if not _ULID.fullmatch(workspace_id) or workspace_id != self._expected_workspace_id:
            raise IdentityResolutionError("gateway identity assertion has invalid workspace_id")
        authorities = _bounded_strings(claims.get("authorities"), "authorities")
        if any(authority == "*" for authority in authorities):
            raise IdentityResolutionError("gateway identity assertion has invalid authorities")
        repo_ids = _bounded_strings(claims.get("repo_ids"), "repo_ids", allow_empty=True)
        if (
            not repo_ids
            or any(repo_id == "*" or not _REPO_ID.fullmatch(repo_id) for repo_id in repo_ids)
            or not set(repo_ids) <= self._allowed_repo_ids
        ):
            raise IdentityResolutionError("gateway identity assertion has invalid repo_ids")
        signed_request_id = _required_text(claims.get("request_id"), "request_id")
        signed_jti = _required_text(claims.get("jti"), "jti")
        invocation_request_id = getattr(
            request.state, "vuoro_invocation_request_id", None
        )
        if (
            signed_request_id != request_id
            or signed_jti != request_id
            or not isinstance(invocation_request_id, str)
            or signed_request_id != invocation_request_id
        ):
            raise IdentityResolutionError("gateway identity request correlation failed")
        for field in ("iat", "nbf", "exp"):
            value = claims.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise IdentityResolutionError(f"gateway identity assertion has invalid {field}")
        if claims["exp"] <= datetime.now(UTC).timestamp():
            raise IdentityResolutionError("gateway identity assertion has expired")
        if (
            claims["nbf"] > claims["iat"]
            or claims["iat"] - claims["nbf"] > _NBF_CLOCK_SKEW_SECONDS
        ):
            raise IdentityResolutionError("gateway identity nbf skew is invalid")
        if claims["exp"] <= claims["iat"] or claims["exp"] - claims["iat"] > 30:
            raise IdentityResolutionError("gateway identity assertion lifetime is invalid")
        return Identity(
            actor=actor,
            environment=self._environment_name,
            authorities=frozenset(authorities),
            repo_ids=frozenset(repo_ids),
            workspace_id=workspace_id,
            principal_id=f"{self._issuer}:{subject}:{epoch}",
        )

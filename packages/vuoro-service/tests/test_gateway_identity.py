from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.requests import Request

from vuoro_service.gateway_identity import (
    GatewayAssertionConfigurationError,
    GatewayAssertionIdentityResolver,
)
from vuoro_service.identity import IdentityResolutionError


WORKSPACE_ID = "01K11111111111111111111111"
ENVIRONMENT = "vuoro-cloud-ws-01k111111111"
REQUEST_ID = "01K33333333333333333333333"


def _key_file(tmp_path: Path) -> tuple[Path, Ed25519PrivateKey]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    path = tmp_path / "gateway-public.pem"
    path.write_bytes(public)
    return path, private


def _claims(**overrides: object) -> dict:
    now = datetime.now(UTC).replace(microsecond=0).timestamp()
    claims = {
        "iss": "vuoro-cloud",
        "aud": "vuoro-service",
        "sub": "github:123",
        "workspace_id": WORKSPACE_ID,
        "actor": "github:123",
        "authorities": ["work:read", "work:write"],
        "repo_ids": ["repo-a"],
        "request_id": REQUEST_ID,
        "iat": now,
        "nbf": now - 1,
        "exp": now + 30,
        "jti": REQUEST_ID,
    }
    claims.update(overrides)
    return claims


def _request(token: str, request_id: str = REQUEST_ID, *, duplicate: bool = False) -> Request:
    headers = [
        (b"x-vuoro-identity", token.encode()),
        (b"x-request-id", request_id.encode()),
    ]
    if duplicate:
        headers.append((b"x-vuoro-identity", token.encode()))
    return Request({"type": "http", "headers": headers})


def _resolver(path: Path) -> GatewayAssertionIdentityResolver:
    return GatewayAssertionIdentityResolver.from_file(
        path,
        environment=ENVIRONMENT,
        expected_workspace_id=WORKSPACE_ID,
        allowed_repo_ids=frozenset({"repo-a", "repo-b"}),
    )


def _token(private: Ed25519PrivateKey, **overrides: object) -> str:
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return jwt.encode(
        _claims(**overrides),
        private_pem,
        algorithm="EdDSA",
        headers={"typ": "JWT", "kid": "gateway-2026-01"},
    )


def test_gateway_assertion_resolver_accepts_cloud_contract(tmp_path: Path) -> None:
    path, private = _key_file(tmp_path)
    identity = _resolver(path)(_request(_token(private)))

    assert identity.actor == "github:123"
    assert identity.environment == ENVIRONMENT
    assert identity.workspace_id == WORKSPACE_ID
    assert identity.authorities == frozenset({"work:read", "work:write"})
    assert identity.authorizes_repo("repo-a")
    assert not identity.authorizes_repo("repo-c")


@pytest.mark.parametrize(
    "overrides",
    [
        {"workspace_id": "01K22222222222222222222222"},
        {"sub": "github:other"},
        {"authorities": []},
        {"repo_ids": ["repo-c"]},
        {"request_id": "01K44444444444444444444444", "jti": "01K44444444444444444444444"},
        {"jti": "01K44444444444444444444444"},
    ],
)
def test_gateway_assertion_resolver_rejects_scope_or_correlation_disagreement(
    tmp_path: Path, overrides: dict
) -> None:
    path, private = _key_file(tmp_path)
    with pytest.raises(IdentityResolutionError):
        _resolver(path)(_request(_token(private, **overrides)))


def test_gateway_assertion_resolver_rejects_expired_bad_signature_unknown_key_and_duplicate_header(
    tmp_path: Path,
) -> None:
    path, private = _key_file(tmp_path)
    resolver = _resolver(path)
    expired = _token(private, exp=0)
    with pytest.raises(IdentityResolutionError):
        resolver(_request(expired))

    other = Ed25519PrivateKey.generate()
    with pytest.raises(IdentityResolutionError):
        resolver(_request(_token(other)))

    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    unknown_kid = jwt.encode(
        _claims(),
        private_pem,
        algorithm="EdDSA",
        headers={"typ": "JWT", "kid": "gateway-rotated"},
    )
    with pytest.raises(IdentityResolutionError):
        resolver(_request(unknown_kid))
    with pytest.raises(IdentityResolutionError):
        resolver(_request(_token(private), duplicate=True))


def test_gateway_assertion_resolver_rejects_bad_configuration(tmp_path: Path) -> None:
    path, _ = _key_file(tmp_path)
    with pytest.raises(GatewayAssertionConfigurationError):
        GatewayAssertionIdentityResolver.from_file(
            path,
            environment=ENVIRONMENT,
            expected_workspace_id="not-a-workspace",
            allowed_repo_ids=frozenset({"repo-a"}),
        )
    with pytest.raises(GatewayAssertionConfigurationError):
        GatewayAssertionIdentityResolver.from_file(
            tmp_path / "missing.pem",
            environment=ENVIRONMENT,
            expected_workspace_id=WORKSPACE_ID,
            allowed_repo_ids=frozenset({"repo-a"}),
        )


def test_gateway_assertion_resolver_does_not_accept_json_or_empty_key(tmp_path: Path) -> None:
    path = tmp_path / "key.pem"
    path.write_text(json.dumps({"kty": "OKP"}), encoding="utf-8")
    with pytest.raises(GatewayAssertionConfigurationError):
        GatewayAssertionIdentityResolver.from_file(
            path,
            environment=ENVIRONMENT,
            expected_workspace_id=WORKSPACE_ID,
            allowed_repo_ids=frozenset({"repo-a"}),
        )

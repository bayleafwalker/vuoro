from __future__ import annotations

from datetime import UTC, datetime
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
USER_SUBJECT = "01K44444444444444444444444"
CLOUD_ISSUER = "vuoro-cloud-control"


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
        "iss": CLOUD_ISSUER,
        "aud": "vuoro-service",
        "sub": "github:123",
        "workspace_id": WORKSPACE_ID,
        "actor": "github:123",
        "subject": USER_SUBJECT,
        "principal_epoch": 0,
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


def _request(
    token: str,
    request_id: str = REQUEST_ID,
    *,
    invocation_request_id: str | None = REQUEST_ID,
    duplicate: bool = False,
    duplicate_request_id: bool = False,
) -> Request:
    headers = [
        (b"x-vuoro-identity", token.encode()),
        (b"x-request-id", request_id.encode()),
    ]
    if duplicate:
        headers.append((b"x-vuoro-identity", token.encode()))
    if duplicate_request_id:
        headers.append((b"x-request-id", request_id.encode()))
    request = Request({"type": "http", "headers": headers})
    if invocation_request_id is not None:
        request.state.vuoro_invocation_request_id = invocation_request_id
    return request


def _resolver(path: Path) -> GatewayAssertionIdentityResolver:
    return GatewayAssertionIdentityResolver.from_file(
        path,
        issuer=CLOUD_ISSUER,
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


def _token_with_headers(
    private: Ed25519PrivateKey, headers: dict[str, str], **overrides: object
) -> str:
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return jwt.encode(
        _claims(**overrides), private_pem, algorithm="EdDSA", headers=headers
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
        {"iss": "other-cloud"},
        {"aud": "other-service"},
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
    with pytest.raises(IdentityResolutionError):
        resolver(_request(_token(private), duplicate_request_id=True))


def test_gateway_assertion_resolver_rejects_non_ed25519_and_invalid_headers(
    tmp_path: Path,
) -> None:
    path, private = _key_file(tmp_path)
    resolver = _resolver(path)
    bad_type = _token_with_headers(private, {"typ": "JWS", "kid": "gateway-2026-01"})
    with pytest.raises(IdentityResolutionError):
        resolver(_request(bad_type))
    hs256 = jwt.encode(
        _claims(), "not-an-ed25519-key" * 2, algorithm="HS256", headers={"typ": "JWT"}
    )
    with pytest.raises(IdentityResolutionError):
        resolver(_request(hs256))


@pytest.mark.parametrize("lifetime,accepted", [(30, True), (31, False)])
def test_gateway_assertion_resolver_enforces_exact_thirty_second_lifetime(
    tmp_path: Path, lifetime: int, accepted: bool
) -> None:
    path, private = _key_file(tmp_path)
    now = int(datetime.now(UTC).timestamp())
    token = _token(private, iat=now, nbf=now - 2, exp=now + lifetime)
    if accepted:
        assert _resolver(path)(_request(token)).actor == "github:123"
    else:
        with pytest.raises(IdentityResolutionError):
            _resolver(path)(_request(token))


@pytest.mark.parametrize("skew,accepted", [(2, True), (3, False)])
def test_gateway_assertion_resolver_enforces_cloud_nbf_skew(
    tmp_path: Path, skew: int, accepted: bool
) -> None:
    path, private = _key_file(tmp_path)
    now = datetime.now(UTC).timestamp()
    token = _token(private, iat=now, nbf=now - skew, exp=now + 30)
    if accepted:
        assert _resolver(path)(_request(token)).actor == "github:123"
    else:
        with pytest.raises(IdentityResolutionError):
            _resolver(path)(_request(token))


def test_gateway_assertion_resolver_rejects_bad_configuration(tmp_path: Path) -> None:
    path, _ = _key_file(tmp_path)
    with pytest.raises(GatewayAssertionConfigurationError):
        GatewayAssertionIdentityResolver.from_file(
            path,
            issuer=CLOUD_ISSUER,
            environment=ENVIRONMENT,
            expected_workspace_id="not-a-workspace",
            allowed_repo_ids=frozenset({"repo-a"}),
        )
    with pytest.raises(GatewayAssertionConfigurationError):
        GatewayAssertionIdentityResolver.from_file(
            tmp_path / "missing.pem",
            issuer=CLOUD_ISSUER,
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
            issuer=CLOUD_ISSUER,
            environment=ENVIRONMENT,
            expected_workspace_id=WORKSPACE_ID,
            allowed_repo_ids=frozenset({"repo-a"}),
        )


def test_gateway_assertion_resolver_rejects_missing_invocation_request_binding(
    tmp_path: Path,
) -> None:
    path, private = _key_file(tmp_path)
    with pytest.raises(IdentityResolutionError):
        _resolver(path)(_request(_token(private), invocation_request_id=None))
    with pytest.raises(IdentityResolutionError):
        _resolver(path)(
            _request(_token(private), invocation_request_id="different-body-id")
        )


def test_gateway_assertion_resolver_handles_key_symlinks_fail_closed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    target = root / "data.pem"
    _, private = _key_file(tmp_path)
    target.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    inside = root / "gateway.pem"
    inside.symlink_to(Path("data.pem"))
    resolver = GatewayAssertionIdentityResolver.from_file(
        inside,
        issuer=CLOUD_ISSUER,
        environment=ENVIRONMENT,
        expected_workspace_id=WORKSPACE_ID,
        allowed_repo_ids=frozenset({"repo-a"}),
        trusted_root=root,
    )
    assert resolver(_request(_token(private))).actor == "github:123"

    outside = tmp_path / "outside.pem"
    outside.write_bytes(target.read_bytes())
    escaped = root / "escaped.pem"
    escaped.symlink_to(outside)
    with pytest.raises(GatewayAssertionConfigurationError):
        GatewayAssertionIdentityResolver.from_file(
            escaped,
            issuer=CLOUD_ISSUER,
            environment=ENVIRONMENT,
            expected_workspace_id=WORKSPACE_ID,
            allowed_repo_ids=frozenset({"repo-a"}),
            trusted_root=root,
        )
    broken = root / "broken.pem"
    broken.symlink_to(root / "missing.pem")
    with pytest.raises(GatewayAssertionConfigurationError):
        GatewayAssertionIdentityResolver.from_file(
            broken,
            issuer=CLOUD_ISSUER,
            environment=ENVIRONMENT,
            expected_workspace_id=WORKSPACE_ID,
            allowed_repo_ids=frozenset({"repo-a"}),
            trusted_root=root,
        )


def test_the_assertion_must_carry_a_principal_epoch(tmp_path: Path) -> None:
    """The claim that makes federation ownership survivable.

    `iat` is per request on a 30-second assertion, so nothing else in the claim
    set distinguishes a reissued actor from the original -- and ownership is a
    comparison against a stored principal id with no transfer operation. An
    assertion without an epoch is refused rather than defaulted, because every
    available default is the actor string, which is the failure being prevented.
    """
    path, private = _key_file(tmp_path)
    resolver = _resolver(path)
    claims = _claims()
    del claims["principal_epoch"]
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    token = jwt.encode(claims, private_pem, algorithm="EdDSA",
                       headers={"typ": "JWT", "kid": "gateway-2026-01"})
    with pytest.raises(IdentityResolutionError):
        resolver(_request(token))

    for invalid in ("0", -1, 1.5, True):
        with pytest.raises(IdentityResolutionError, match="principal_epoch"):
            resolver(_request(_token(private, principal_epoch=invalid)))
    # A null claim is a missing claim to PyJWT's require check, so it fails one
    # step earlier -- still refused, just not by the epoch check itself.
    with pytest.raises(IdentityResolutionError, match="assertion is invalid"):
        resolver(_request(_token(private, principal_epoch=None)))


def test_a_reissued_actor_is_a_different_principal(tmp_path: Path) -> None:
    """The whole point of the epoch, stated as the property it buys.

    Same actor string, incremented epoch: a different principal_id, so the
    ownership comparison against a resource created by the first holder cannot
    succeed for the second. No transfer operation is needed because no transfer
    happens.
    """
    path, private = _key_file(tmp_path)
    resolver = _resolver(path)
    first = resolver(_request(_token(private, principal_epoch=0)))
    reissued = resolver(_request(_token(private, principal_epoch=1)))
    assert first.actor == reissued.actor
    assert first.principal_id != reissued.principal_id
    assert first.principal_id == f"{CLOUD_ISSUER}:{USER_SUBJECT}:0"
    assert reissued.principal_id == f"{CLOUD_ISSUER}:{USER_SUBJECT}:1"


def test_the_principal_id_is_composed_from_the_colon_free_subject(tmp_path: Path) -> None:
    """`prod:github:123:0` is ambiguous between (prod, github:123) and
    (prod:github, 123). The subject claim is an opaque id with no colon, so the
    composed id parses from the right exactly once."""
    path, private = _key_file(tmp_path)
    resolver = _resolver(path)
    identity = resolver(_request(_token(private)))
    issuer, subject, epoch = identity.principal_id.rsplit(":", 2)
    assert (issuer, subject, epoch) == (CLOUD_ISSUER, USER_SUBJECT, "0")
    for bad in ("github:123", "", "a b", "prod:x"):
        with pytest.raises(IdentityResolutionError, match="subject"):
            resolver(_request(_token(private, subject=bad)))
    claims = _claims()
    del claims["subject"]
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    token = jwt.encode(claims, private_pem, algorithm="EdDSA",
                       headers={"typ": "JWT", "kid": "gateway-2026-01"})
    with pytest.raises(IdentityResolutionError):
        resolver(_request(token))

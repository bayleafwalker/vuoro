"""Replay protection for gateway assertions and edge proofs (agentops#2519)."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import logging
from pathlib import Path
import secrets
import sys
import threading
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from starlette.requests import Request

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.contracts import DomainCompatibility, OperationDefinition
from vuoro_service.edge_proof import (
    KEY_BYTES,
    PROOF_HEADER,
    PROOF_TTL_MS,
    EdgeProofConfigurationError,
    EdgeProofError,
    EdgeProofReplayed,
    EdgeProofVerifier,
    load_or_create_proof_key,
    mint_edge_proof,
)
from vuoro_service.gateway_identity import GatewayAssertionIdentityResolver
from vuoro_service.identity import (
    IdentityReplayCapacityError,
    IdentityReplayedError,
    IdentityResolutionError,
)
from vuoro_service.replay import ReplayCache, ReplayCacheFull


WORKSPACE_ID = "01K11111111111111111111111"
ENVIRONMENT = "vuoro-cloud-ws-01k111111111"
REQUEST_ID = "01K33333333333333333333333"
SUBJECT = "01K44444444444444444444444"
ISSUER = "vuoro-cloud-control"
INVOKE_PATH = "/api/invoke/v1"
_SCHEMA = "https://json-schema.org/draft/2020-12/schema"


class _Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# -- the cache ------------------------------------------------------------


def test_a_key_is_accepted_once_and_refused_the_second_time() -> None:
    cache = ReplayCache(clock=_Clock())
    assert cache.consume(b"k", 1_000_030.0) is True
    assert cache.consume(b"k", 1_000_030.0) is False
    assert cache.consume(b"other", 1_000_030.0) is True


def test_an_expired_entry_is_evicted_and_the_cache_stays_bounded() -> None:
    clock = _Clock()
    cache = ReplayCache(max_entries=3, clock=clock)
    for index in range(3):
        assert cache.consume(bytes([index]), clock.now + 10 + index)
    assert len(cache) == 3
    # Full of unexpired entries: a new key is refused, none is evicted.
    with pytest.raises(ReplayCacheFull):
        cache.consume(b"new", clock.now + 30)
    assert cache.consume(bytes([0]), clock.now + 10) is False
    # The first entry expires; its slot frees and it is gone.
    clock.now += 10
    assert len(cache) == 2
    assert cache.consume(b"new", clock.now + 30) is True
    assert len(cache) == 3
    # Everything expires; the cache empties rather than growing.
    clock.now += 100
    assert len(cache) == 0
    for index in range(1_000):
        assert cache.consume(index.to_bytes(4, "big"), clock.now + 1)
        clock.now += 1
        assert len(cache) <= 3


def test_at_capacity_new_keys_are_refused_and_logged(caplog) -> None:
    clock = _Clock()
    cache = ReplayCache(max_entries=1, name="test-cache", clock=clock)
    cache.consume(b"a", clock.now + 30)
    with caplog.at_level(logging.ERROR, logger="vuoro_service.replay"):
        for _ in range(5):
            with pytest.raises(ReplayCacheFull):
                cache.consume(b"b", clock.now + 30)
    # Refused every time, logged once (throttled).
    records = [r for r in caplog.records if "replay cache is full" in r.getMessage()]
    assert len(records) == 1
    assert records[0].replay_cache == "test-cache"


def test_a_key_is_held_by_the_route_that_claimed_it() -> None:
    clock = _Clock()
    cache = ReplayCache(clock=clock)
    assert cache.claim(b"direct", clock.now + 10, route="direct")
    assert not cache.claim(b"direct", clock.now + 10, route="edge-proof", max_uses=7)
    assert cache.claim(b"proofed", clock.now + 10, route="edge-proof", max_uses=3)
    assert not cache.claim(b"proofed", clock.now + 10, route="direct")
    assert cache.claim(b"proofed", clock.now + 20, route="edge-proof", max_uses=3)
    assert cache.claim(b"proofed", clock.now + 20, route="edge-proof", max_uses=3)
    assert not cache.claim(b"proofed", clock.now + 20, route="edge-proof", max_uses=3)
    # The later expiry wins: still held after the first one passes.
    clock.now += 15
    assert not cache.claim(b"proofed", clock.now + 20, route="direct")
    clock.now += 10
    assert cache.claim(b"proofed", clock.now + 20, route="direct")


@pytest.fixture
def frequent_thread_switches():
    """Switch threads often, so concurrent resolver calls interleave."""

    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(previous)


def test_a_concurrent_duplicate_is_accepted_exactly_once() -> None:
    """Deterministic: the race-window hook sleeps between the lookup and the
    insert, so without the lock every thread passes the lookup before any
    inserts, and more than one is accepted every time."""

    workers = 8
    cache = ReplayCache()
    cache._race_window = lambda: time.sleep(0.02)
    barrier = threading.Barrier(workers)

    def attempt(_: int) -> bool:
        barrier.wait()
        return cache.consume(b"same", 2**40)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(attempt, range(workers)))
    assert results.count(True) == 1, f"{results.count(True)} accepted"


# -- the gateway assertion resolver --------------------------------------


def _key_file(tmp_path: Path) -> tuple[Path, Ed25519PrivateKey]:
    private = Ed25519PrivateKey.generate()
    path = tmp_path / "gateway-public.pem"
    path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    return path, private


def _token(private: Ed25519PrivateKey, **overrides: object) -> str:
    now = datetime.now(UTC).replace(microsecond=0).timestamp()
    claims = {
        "iss": ISSUER,
        "aud": "vuoro-service",
        "sub": "github:123",
        "actor": "github:123",
        "subject": SUBJECT,
        "principal_epoch": 0,
        "workspace_id": WORKSPACE_ID,
        "authorities": ["work:read"],
        "repo_ids": ["repo-a"],
        "request_id": REQUEST_ID,
        "jti": "jti-" + secrets.token_hex(12),
        "iat": now,
        "nbf": now - 1,
        "exp": now + 30,
    }
    claims.update(overrides)
    return jwt.encode(
        claims, private, algorithm="EdDSA", headers={"typ": "JWT", "kid": "gateway-2026-01"}
    )


def _resolver(path: Path, **kwargs: object) -> GatewayAssertionIdentityResolver:
    return GatewayAssertionIdentityResolver.from_file(
        path,
        issuer=ISSUER,
        environment=ENVIRONMENT,
        expected_workspace_id=WORKSPACE_ID,
        allowed_repo_ids=frozenset({"repo-a"}),
        **kwargs,
    )


def _request(
    token: str, *, proof: str | None = None, body: bytes | None = None
) -> Request:
    headers = [(b"x-vuoro-identity", token.encode()), (b"x-request-id", REQUEST_ID.encode())]
    if proof is not None:
        headers.append((PROOF_HEADER.encode(), proof.encode()))
    request = Request(
        {"type": "http", "method": "POST", "path": INVOKE_PATH, "headers": headers}
    )
    request.state.vuoro_invocation_request_id = REQUEST_ID
    if body is not None:
        request.state.vuoro_invocation_body = body
    return request


def test_the_same_assertion_is_accepted_once_and_refused_the_second_time(
    tmp_path: Path,
) -> None:
    path, private = _key_file(tmp_path)
    resolver = _resolver(path)
    token = _token(private)
    assert resolver(_request(token)).actor == "github:123"
    with pytest.raises(IdentityReplayedError, match="already used") as excinfo:
        resolver(_request(token))
    assert excinfo.value.code == "identity-replayed"
    assert excinfo.value.http_status == 401
    # A fresh assertion for the same caller is fine.
    assert resolver(_request(_token(private))).actor == "github:123"


def test_replay_protection_does_not_depend_on_jti_equal_to_request_id(
    tmp_path: Path,
) -> None:
    """The gateway will mint its own jti (vuoro-cloud follow-up); the key is
    (subject, jti), with request_id bound separately as before."""

    path, private = _key_file(tmp_path)
    resolver = _resolver(path)
    token = _token(private, jti="gateway-minted-" + secrets.token_hex(8))
    resolver(_request(token))
    with pytest.raises(IdentityReplayedError):
        resolver(_request(token))
    # Two assertions that share a request_id (a client retry) but carry
    # distinct jtis are both accepted.
    resolver(_request(_token(private)))
    resolver(_request(_token(private)))
    # A jti reused by a different subject is not the same credential.
    shared = "shared-" + secrets.token_hex(8)
    resolver(_request(_token(private, jti=shared)))
    resolver(_request(_token(private, jti=shared, subject="01K66666666666666666666666")))


def test_a_refused_assertion_does_not_burn_its_jti(tmp_path: Path) -> None:
    path, private = _key_file(tmp_path)
    resolver = _resolver(path)
    jti = "jti-" + secrets.token_hex(8)
    with pytest.raises(IdentityResolutionError):
        resolver(_request(_token(private, jti=jti, repo_ids=["repo-z"])))
    resolver(_request(_token(private, jti=jti)))


def test_a_concurrent_duplicate_assertion_is_accepted_exactly_once(
    tmp_path: Path, frequent_thread_switches
) -> None:
    path, private = _key_file(tmp_path)
    resolver = _resolver(path)
    workers = 8
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in range(25):
            token = _token(private)
            barrier = threading.Barrier(workers)

            def attempt(_: int) -> str:
                barrier.wait()
                try:
                    resolver(_request(token))
                except IdentityReplayedError:
                    return "replayed"
                return "accepted"

            results = list(pool.map(attempt, range(workers)))
            assert results.count("accepted") == 1
            assert results.count("replayed") == workers - 1


def test_at_capacity_new_assertions_are_refused_fail_closed(tmp_path: Path) -> None:
    path, private = _key_file(tmp_path)
    resolver = _resolver(path, replay_cache=ReplayCache(max_entries=1))
    resolver(_request(_token(private)))
    with pytest.raises(IdentityReplayCapacityError) as excinfo:
        resolver(_request(_token(private)))
    assert excinfo.value.code == "identity-replay-capacity"


def test_an_assertion_issued_before_the_verifier_started_is_refused(tmp_path: Path) -> None:
    """A restart empties the cache; an assertion the previous instance may
    have accepted is refused, not guessed about."""

    path, private = _key_file(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0).timestamp()
    resolver = _resolver(path, replay_not_before=now)
    with pytest.raises(IdentityReplayedError, match="replay window"):
        resolver(_request(_token(private, iat=now - 3, nbf=now - 4, exp=now + 27)))
    assert resolver(_request(_token(private, iat=now, nbf=now - 1))).actor == "github:123"


# -- edge proofs ------------------------------------------------------------


KEY = bytes(range(KEY_BYTES))
BODY = b'{"request_id":"01K33333333333333333333333","operation":"x"}'


def _mint(**overrides: object) -> str:
    fields: dict[str, object] = {
        "method": "POST",
        "path": INVOKE_PATH,
        "assertion": "a.b.c",
        "body": BODY,
    }
    fields.update(overrides)
    return mint_edge_proof(KEY, **fields)  # type: ignore[arg-type]


def _verify(verifier: EdgeProofVerifier, proof: str, **overrides: object) -> None:
    fields: dict[str, object] = {
        "method": "POST",
        "path": INVOKE_PATH,
        "assertion": "a.b.c",
        "body": BODY,
    }
    fields.update(overrides)
    verifier.verify(proof, **fields)  # type: ignore[arg-type]


def test_an_edge_proof_is_accepted_once() -> None:
    verifier = EdgeProofVerifier(KEY)
    proof = _mint()
    _verify(verifier, proof)
    with pytest.raises(EdgeProofReplayed):
        _verify(verifier, proof)
    _verify(verifier, _mint())


@pytest.mark.parametrize(
    "overrides",
    [
        {"body": BODY.replace(b'"x"', b'"y"')},
        {"body": b""},
        {"assertion": "a.b.d"},
        {"path": "/api/catalog/v1"},
        {"method": "GET"},
    ],
)
def test_an_edge_proof_for_a_different_request_is_refused(overrides: dict) -> None:
    verifier = EdgeProofVerifier(KEY)
    proof = _mint()
    with pytest.raises(EdgeProofError, match="does not verify") as excinfo:
        _verify(verifier, proof, **overrides)
    assert not isinstance(excinfo.value, EdgeProofReplayed)
    # The refusal consumed nothing: the proof still works for its own request.
    _verify(verifier, proof)


def test_an_edge_proof_under_another_key_is_refused() -> None:
    verifier = EdgeProofVerifier(bytes(KEY_BYTES))
    with pytest.raises(EdgeProofError, match="does not verify"):
        _verify(verifier, _mint())


def test_an_edge_proof_expires_and_cannot_come_from_the_future() -> None:
    clock = _Clock(1_700_000_000.0)
    verifier = EdgeProofVerifier(KEY, clock=clock)
    proof = _mint(now=clock.now)
    clock.now += PROOF_TTL_MS / 1000
    with pytest.raises(EdgeProofError, match="expired"):
        _verify(verifier, proof)
    with pytest.raises(EdgeProofError, match="not yet valid"):
        _verify(verifier, _mint(now=clock.now + 5))


def test_an_edge_proof_minted_before_the_verifier_started_is_refused() -> None:
    clock = _Clock(1_700_000_000.0)
    proof = _mint(now=clock.now - 1)
    verifier = EdgeProofVerifier(KEY, not_before=clock.now, clock=clock)
    with pytest.raises(EdgeProofReplayed, match="replay window"):
        _verify(verifier, proof)


@pytest.mark.parametrize(
    "mangle",
    [
        lambda p: p.replace("v1.", "v2.", 1),
        lambda p: p + ".x",
        lambda p: ".".join(p.split(".")[:4]),
        lambda p: p.replace(p.split(".")[2], "+" + p.split(".")[2]),
        lambda p: p.replace(p.split(".")[1], p.split(".")[1] + "A"),
        lambda p: "",
    ],
)
def test_a_malformed_edge_proof_is_refused(mangle) -> None:
    verifier = EdgeProofVerifier(KEY)
    with pytest.raises(EdgeProofError):
        _verify(verifier, mangle(_mint()))


def test_the_proof_key_is_created_once_and_shared(tmp_path: Path) -> None:
    path = tmp_path / "edge-proof" / "key"
    path.parent.mkdir()
    workers = 8
    barrier = threading.Barrier(workers)

    def load(_: int) -> bytes:
        barrier.wait()
        return load_or_create_proof_key(path)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        keys = set(pool.map(load, range(workers)))
    assert len(keys) == 1
    (key,) = keys
    assert len(key) == KEY_BYTES
    assert path.stat().st_mode & 0o777 == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["key"]


def test_a_malformed_or_relative_proof_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "key"
    path.write_bytes(b"short")
    with pytest.raises(EdgeProofConfigurationError, match="32 bytes"):
        load_or_create_proof_key(path)
    with pytest.raises(EdgeProofConfigurationError, match="absolute"):
        load_or_create_proof_key(Path("relative/key"))


# -- the resolver on the edge proof route -----------------------------------


def _proof_resolver(path: Path, **kwargs: object) -> GatewayAssertionIdentityResolver:
    return _resolver(path, edge_proofs=EdgeProofVerifier(KEY), **kwargs)


def _proof(token: str, body: bytes = BODY) -> str:
    return mint_edge_proof(KEY, method="POST", path=INVOKE_PATH, assertion=token, body=body)


def test_one_assertion_serves_several_proofed_calls_then_no_direct_use(
    tmp_path: Path,
) -> None:
    path, private = _key_file(tmp_path)
    resolver = _proof_resolver(path)
    token = _token(private)
    for _ in range(7):  # append_evidence's worst case: resolve + 3 x (tail, append)
        resolver(_request(token, proof=_proof(token), body=BODY))
    # The edge route marked the jti: the same assertion cannot go direct.
    with pytest.raises(IdentityReplayedError):
        resolver(_request(token))


def test_a_replayed_or_rebound_proof_is_refused(tmp_path: Path) -> None:
    path, private = _key_file(tmp_path)
    resolver = _proof_resolver(path)
    token = _token(private)
    proof = _proof(token)
    resolver(_request(token, proof=proof, body=BODY))
    with pytest.raises(IdentityReplayedError, match="edge proof was already used"):
        resolver(_request(token, proof=proof, body=BODY))
    other = _proof(token)
    with pytest.raises(IdentityResolutionError, match="does not verify") as excinfo:
        resolver(_request(token, proof=other, body=BODY + b" "))
    assert not isinstance(excinfo.value, IdentityReplayedError)
    with pytest.raises(IdentityResolutionError, match="does not verify"):
        resolver(_request(_token(private), proof=other, body=BODY))
    with pytest.raises(IdentityResolutionError, match="cannot be bound"):
        resolver(_request(token, proof=other, body=None))


def test_a_proof_without_a_configured_verifier_is_just_a_direct_request(
    tmp_path: Path,
) -> None:
    path, private = _key_file(tmp_path)
    resolver = _resolver(path)
    token = _token(private)
    resolver(_request(token, proof=_proof(token), body=BODY))
    with pytest.raises(IdentityReplayedError):
        resolver(_request(token, proof=_proof(token), body=BODY))


def test_a_direct_mcp_assertion_is_refused_while_the_audience_is_shared(
    tmp_path: Path,
) -> None:
    path, private = _key_file(tmp_path)
    resolver = _proof_resolver(path)
    oauth = _token(private, client_id="claude-connector", grant_id="grant-1")
    with pytest.raises(IdentityResolutionError, match="only through the MCP edge") as excinfo:
        resolver(_request(oauth))
    assert excinfo.value.code == "identity-edge-proof-required"
    # Through the edge it is fine; a workspace-token assertion is fine direct.
    resolver(_request(oauth, proof=_proof(oauth), body=BODY))
    resolver(_request(_token(private)))


def test_a_separate_edge_audience_splits_the_routes(tmp_path: Path) -> None:
    path, private = _key_file(tmp_path)
    resolver = _proof_resolver(path, edge_audience="vuoro-mcp")
    edge_token = _token(private, aud="vuoro-mcp", client_id="c", grant_id="g")
    shell_token = _token(private, client_id="c", grant_id="g")
    # An edge-audience assertion is accepted only with a proof ...
    resolver(_request(edge_token, proof=_proof(edge_token), body=BODY))
    with pytest.raises(IdentityResolutionError, match="invalid"):
        resolver(_request(_token(private, aud="vuoro-mcp")))
    # ... and a shell-audience one only without: the interim client_id rule
    # is off once the audiences differ.
    with pytest.raises(IdentityResolutionError, match="invalid"):
        resolver(_request(shell_token, proof=_proof(shell_token), body=BODY))
    resolver(_request(shell_token))


# -- the shell's HTTP answers -----------------------------------------------


def _shell(resolver: GatewayAssertionIdentityResolver):
    registry = CatalogRegistry()
    registry.register(
        OperationDefinition(
            name="work.public.list-v1",
            owning_domain="work",
            input_schema={"$schema": _SCHEMA, "type": "object"},
            result_schema={"$schema": _SCHEMA, "type": "object"},
            required_authority="work:read",
            execution_semantics="read",
            idempotency="not-allowed",
            repo_scoped=True,
        ),
        lambda arguments, context: {"ok": True},
    )
    return create_app(
        settings=ServiceSettings(
            environment_name=ENVIRONMENT,
            environment_class="development",
            compatibility_state="compatible",
            domains={
                "work": DomainCompatibility(
                    api_version="work/v1", schema_version="work-schema/1", state="compatible"
                )
            },
        ),
        registry=registry,
        identity_resolver=resolver,
    )


def _envelope() -> dict[str, object]:
    return {
        "schema_version": "invocation/v1",
        "request_id": REQUEST_ID,
        "operation": "work.public.list-v1",
        "arguments": {},
        "catalog_revision": None,
        "basis_revision": None,
        "idempotency_key": None,
        "repo_id": "repo-a",
    }


def _headers(token: str) -> dict[str, str]:
    return {
        "X-Vuoro-Identity": token,
        "X-Request-Id": REQUEST_ID,
        "X-Vuoro-Client-Protocol": "1",
    }


def test_the_shell_answers_a_replay_with_a_distinct_401(tmp_path: Path) -> None:
    path, private = _key_file(tmp_path)
    client = TestClient(_shell(_resolver(path)))
    token = _token(private)
    first = client.post(INVOKE_PATH, headers=_headers(token), json=_envelope())
    assert first.status_code == 200, first.text
    second = client.post(INVOKE_PATH, headers=_headers(token), json=_envelope())
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "identity-replayed"
    invalid = client.post(INVOKE_PATH, headers=_headers(token + "x"), json=_envelope())
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "identity-required"


def test_the_shell_accepts_a_concurrent_duplicate_exactly_once(tmp_path: Path) -> None:
    path, private = _key_file(tmp_path)
    shell = _shell(_resolver(path))
    token = _token(private)

    async def run() -> list[int]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=shell), base_url="http://shell"
        ) as client:
            responses = await asyncio.gather(
                *(
                    client.post(INVOKE_PATH, headers=_headers(token), json=_envelope())
                    for _ in range(12)
                )
            )
        return [response.status_code for response in responses]

    statuses = asyncio.run(run())
    assert statuses.count(200) == 1
    assert statuses.count(401) == 11


def test_the_shell_verifies_a_proof_against_the_exact_body(tmp_path: Path) -> None:
    import json

    path, private = _key_file(tmp_path)
    client = TestClient(_shell(_proof_resolver(path)))
    token = _token(private)
    body = json.dumps(_envelope()).encode()
    headers = {**_headers(token), "Content-Type": "application/json"}
    for _ in range(3):
        response = client.post(
            INVOKE_PATH, headers={**headers, PROOF_HEADER: _proof(token, body)}, content=body
        )
        assert response.status_code == 200, response.text
    proof = _proof(token, body)
    client.post(INVOKE_PATH, headers={**headers, PROOF_HEADER: proof}, content=body)
    replayed = client.post(INVOKE_PATH, headers={**headers, PROOF_HEADER: proof}, content=body)
    assert replayed.status_code == 401
    assert replayed.json()["error"]["code"] == "identity-replayed"
    # Same envelope, re-serialized with different whitespace: the proof is
    # bound to bytes, not to a parse.
    respaced = json.dumps(_envelope(), indent=1).encode()
    rebound = client.post(
        INVOKE_PATH, headers={**headers, PROOF_HEADER: _proof(token, body)}, content=respaced
    )
    assert rebound.status_code == 401
    assert rebound.json()["error"]["code"] == "identity-required"


def test_the_shell_answers_capacity_with_503(tmp_path: Path) -> None:
    path, private = _key_file(tmp_path)
    client = TestClient(_shell(_resolver(path, replay_cache=ReplayCache(max_entries=1))))
    client.post(INVOKE_PATH, headers=_headers(_token(private)), json=_envelope())
    response = client.post(INVOKE_PATH, headers=_headers(_token(private)), json=_envelope())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "identity-replay-capacity"


# -- routes and the proofed-use cap (review of bfb6718) ----------------------


def test_a_jti_consumed_directly_is_refused_on_the_proof_route(tmp_path: Path) -> None:
    """The cross-process replay: the shell consumed a REST assertion directly;
    the edge (a separate cache) accepts it and forwards it with valid proofs."""

    path, private = _key_file(tmp_path)
    resolver = _proof_resolver(path)
    token = _token(private)
    resolver(_request(token))
    with pytest.raises(IdentityReplayedError, match="already used directly"):
        resolver(_request(token, proof=_proof(token), body=BODY))


def test_proofed_uses_are_capped_at_the_largest_tool_call(tmp_path: Path) -> None:
    from vuoro_service.edge_proof import MAX_PROOFED_USES_PER_ASSERTION

    path, private = _key_file(tmp_path)
    resolver = _proof_resolver(path)
    token = _token(private)
    for _ in range(MAX_PROOFED_USES_PER_ASSERTION):
        resolver(_request(token, proof=_proof(token), body=BODY))
    with pytest.raises(IdentityReplayedError, match="more times than one tool call needs"):
        resolver(_request(token, proof=_proof(token), body=BODY))
    # A fresh assertion for the next tool call starts its own count.
    other = _token(private)
    resolver(_request(other, proof=_proof(other), body=BODY))


def test_the_edge_role_requires_an_oauth_grant_without_burning_the_jti(
    tmp_path: Path,
) -> None:
    path, private = _key_file(tmp_path)
    resolver = _resolver(path, require_oauth_grant=True)
    jti = "jti-" + secrets.token_hex(8)
    for claims in ({}, {"client_id": "c"}, {"grant_id": "g"}):
        with pytest.raises(IdentityResolutionError, match="OAuth") as excinfo:
            resolver(_request(_token(private, jti=jti, **claims)))
        assert excinfo.value.code == "identity-oauth-grant-required"
    resolver(_request(_token(private, jti=jti, client_id="c", grant_id="g")))


# -- restart watermark and lifetime bound (agentops#2530) -------------------


def test_a_shell_restart_does_not_reset_the_proofed_use_cap(tmp_path: Path) -> None:
    """The use count is in memory: a restarted shell must not grant a captured
    assertion another cap's worth of proofed uses."""

    from vuoro_service.edge_proof import MAX_PROOFED_USES_PER_ASSERTION

    path, private = _key_file(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0).timestamp()
    token = _token(private, iat=now - 3, nbf=now - 4, exp=now + 27)
    before = _proof_resolver(path, replay_not_before=now - 10)
    for _ in range(MAX_PROOFED_USES_PER_ASSERTION):
        before(_request(token, proof=_proof(token), body=BODY))
    # The restarted shell: an empty cache and a later watermark.
    after = _proof_resolver(path, replay_not_before=now)
    with pytest.raises(IdentityReplayedError, match="replay window") as excinfo:
        after(_request(token, proof=_proof(token), body=BODY))
    assert excinfo.value.code == "identity-replayed"
    # An assertion minted after the restart is served as usual.
    fresh = _token(private, iat=now, nbf=now - 1)
    after(_request(fresh, proof=_proof(fresh), body=BODY))


def test_a_long_lived_assertion_never_reaches_the_cache(tmp_path: Path) -> None:
    """The cache cap is sized for 30 s assertions; a longer one would pin its
    slot for longer, so it is refused before the claim."""

    from vuoro_service.gateway_identity import _MAX_ASSERTION_LIFETIME_SECONDS

    assert _MAX_ASSERTION_LIFETIME_SECONDS == 30
    path, private = _key_file(tmp_path)
    cache = ReplayCache(max_entries=1)
    resolver = _proof_resolver(path, replay_cache=cache)
    now = datetime.now(UTC).replace(microsecond=0).timestamp()
    for lifetime in (_MAX_ASSERTION_LIFETIME_SECONDS + 1, 3600):
        token = _token(private, iat=now, nbf=now - 1, exp=now + lifetime)
        with pytest.raises(IdentityResolutionError, match="lifetime"):
            resolver(_request(token))
        with pytest.raises(IdentityResolutionError, match="lifetime"):
            resolver(_request(token, proof=_proof(token), body=BODY))
    assert len(cache) == 0
    token = _token(private, iat=now, nbf=now - 1, exp=now + _MAX_ASSERTION_LIFETIME_SECONDS)
    resolver(_request(token))
    assert len(cache) == 1


def test_the_watermark_is_checked_before_the_proof_nonce_is_spent(tmp_path: Path) -> None:
    path, private = _key_file(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0).timestamp()
    token = _token(private, iat=now - 3, nbf=now - 4, exp=now + 27)
    proofs = EdgeProofVerifier(KEY)
    proof = _proof(token)
    restarted = _resolver(path, edge_proofs=proofs, replay_not_before=now)
    with pytest.raises(IdentityReplayedError, match="replay window"):
        restarted(_request(token, proof=proof, body=BODY))
    # The refused call left the nonce unspent: the same proof still verifies.
    _resolver(path, edge_proofs=proofs)(_request(token, proof=proof, body=BODY))

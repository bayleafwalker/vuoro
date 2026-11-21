import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from threading import BoundedSemaphore, Event

import httpx
import jwt
import pytest

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry, SCHEMA_DIALECT
from vuoro_service.contracts import DomainCompatibility, OperationDefinition
from vuoro_service.identity import Identity, StaticBearerIdentityResolver
from vuoro_service.kubernetes_identity import KubernetesIdentityResolver, WORKLOAD_OPERATION


CA_PATHS = (Path("/etc/ssl/certs/ca-certificates.crt"), Path("/etc/ssl/certs/ca-bundle.crt"))
ISSUER = "https://cluster.example.test"
AUDIENCE = "https://work.example.test/kubernetes"
ACCOUNT = "system:serviceaccount:lanectl:reference-reader"
UID = "dcbdb702-279d-48b6-9b65-fd37f88a5547"
WORKSPACE = "lanectl-pilot"
REPO = "lanectl"
OTHER_OPERATION = "work.public.list-v1"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def assertion(**overrides):
    claims = {
        "iss": ISSUER, "aud": [AUDIENCE], "sub": ACCOUNT,
        "exp": int((datetime.now(UTC) + timedelta(minutes=10)).timestamp()),
    }
    return jwt.encode(claims | overrides, "fixture-only-signing-key-32-bytes", algorithm="HS256")


def status(**overrides):
    return {
        "authenticated": True, "audiences": [AUDIENCE],
        "user": {
            "username": ACCOUNT, "uid": UID,
            "extra": {"authentication.kubernetes.io/pod-uid": ["fixture-bound-pod"]},
        },
    } | overrides


@pytest.fixture
def reviewer(tmp_path):
    key = tmp_path / "reviewer-token"
    key.write_text("reviewer.credential.one\n")
    path = tmp_path / "binding.json"
    path.write_text(json.dumps({
        "schema_version": "vuoro-kubernetes-identity/v1",
        "api_server": "https://apiserver.example.test:6443",
        "ca_file": str(next(path for path in CA_PATHS if path.is_file())),
        "reviewer_token_file": str(key),
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "service_account": ACCOUNT,
        "service_account_uid": UID,
        "repo_id": REPO,
        "workspace_id": WORKSPACE,
        "principal_id": "appservice-kubernetes:lanectl-reference-reader:1",
        "environment": "vuoro-dev",
    }))
    static = StaticBearerIdentityResolver({
        "static.opaque.credential": Identity(
            actor="static", environment="vuoro-dev",
            authorities=frozenset({"work:read"}), repo_ids=frozenset({REPO}),
        )
    })
    return KubernetesIdentityResolver(static, config_path=path, environment="vuoro-dev"), key


@pytest.fixture
async def invocation(reviewer):
    resolver, _ = reviewer
    registry = CatalogRegistry()
    received = []
    schema = {"$schema": SCHEMA_DIALECT, "type": "object"}

    def owner(arguments, context):
        received.append((arguments, context.identity))
        return {}

    for name in (WORKLOAD_OPERATION, OTHER_OPERATION):
        registry.register(OperationDefinition(
            name=name, owning_domain="work", input_schema=schema, result_schema=schema,
            required_authority="work:read", execution_semantics="read",
            idempotency="not-allowed", repo_scoped=True,
        ), owner)
    app = create_app(
        settings=ServiceSettings(
            environment_name="vuoro-dev", environment_class="development", compatibility_state="compatible",
            domains={"work": DomainCompatibility(api_version="work/v1", schema_version="work-schema/1",
                                                   state="compatible")},
        ),
        registry=registry,
        identity_resolver=resolver,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        async def invoke(token, operation=WORKLOAD_OPERATION, repo_id=REPO):
            return await client.post(
                "/api/invoke/v1",
                headers={"X-Vuoro-Client-Protocol": "1", "Authorization": f"Bearer {token}"},
                json={
                    "schema_version": "invocation/v1", "request_id": "tokenreview-reference",
                    "operation": operation, "repo_id": repo_id, "arguments": {"work_id": 1},
                    "catalog_revision": registry.revision, "idempotency_key": None,
                },
            )
        yield invoke, received


@pytest.mark.anyio
async def test_reference_read_preserves_owner_scope_and_opaque_credentials(reviewer, invocation, monkeypatch):
    resolver, _ = reviewer
    invoke, received = invocation
    reviews = []

    def review(token):
        reviews.append(token)
        return status()

    monkeypatch.setattr(resolver, "_review", review)
    token = assertion()
    assert (await invoke(token)).status_code == 200
    assert received[0][1].workspace_id == WORKSPACE
    assert received[0][1].principal_id == "appservice-kubernetes:lanectl-reference-reader:1"
    assert received[0][1].expires_at > datetime.now(UTC)
    assert (await invoke(token, OTHER_OPERATION)).status_code == 403
    assert (await invoke(token, repo_id="other")).status_code == 403
    assert len(received) == 1
    assert len(reviews) == 3
    assert (await invoke("static.opaque.credential", OTHER_OPERATION)).status_code == 200
    assert len(reviews) == 3


@pytest.mark.anyio
@pytest.mark.parametrize("changed", [
    {"authenticated": False},
    {"audiences": ["https://wrong.example.test"]},
    {"user": status()["user"] | {"username": "system:serviceaccount:lanectl:other"}},
    {"user": status()["user"] | {"uid": "different-uid"}},
    {"user": status()["user"] | {"extra": {}}},
])
async def test_invalid_live_reviews_refuse_without_owner_calls(reviewer, invocation, monkeypatch, changed):
    resolver, _ = reviewer
    invoke, received = invocation
    monkeypatch.setattr(resolver, "_review", lambda _: status(**changed))
    response = await invoke(assertion())
    assert response.status_code == 401
    assert not received


@pytest.mark.anyio
async def test_revocation_after_success_is_seen_on_next_request(reviewer, invocation, monkeypatch):
    resolver, _ = reviewer
    invoke, received = invocation
    reviews = iter([status(), status(authenticated=False)])
    monkeypatch.setattr(resolver, "_review", lambda _: next(reviews))
    token = assertion()
    assert (await invoke(token)).status_code == 200
    assert (await invoke(token)).status_code == 401
    assert len(received) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("changed", [
    {"aud": [AUDIENCE, "https://other.example.test"]},
    {"iss": "https://other.example.test"},
    {"exp": 1},
])
async def test_invalid_assertion_claims_refuse_before_review(reviewer, invocation, monkeypatch, changed):
    resolver, _ = reviewer
    invoke, received = invocation

    def unexpected_review(_):
        pytest.fail("invalid assertion reached TokenReview")

    monkeypatch.setattr(resolver, "_review", unexpected_review)
    assert (await invoke(assertion(**changed))).status_code == 401
    assert not received


@pytest.mark.anyio
async def test_outage_does_not_fall_back_and_token_is_not_exposed(reviewer, invocation, monkeypatch):
    resolver, _ = reviewer
    invoke, received = invocation
    token = assertion()

    def offline(_):
        raise OSError(token)

    monkeypatch.setattr(resolver, "_review", offline)
    result = await invoke(token)
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "identity-review-unavailable"
    assert token not in result.text
    assert not received


@pytest.mark.anyio
async def test_review_capacity_is_fail_closed_until_worker_finishes(reviewer, invocation, monkeypatch):
    resolver, _ = reviewer
    invoke, received = invocation
    started, release = Event(), Event()

    def slow_review(_):
        started.set()
        assert release.wait(timeout=5)
        return status()

    monkeypatch.setattr(resolver, "_review_slots", BoundedSemaphore(1))
    monkeypatch.setattr(resolver, "_review", slow_review)
    token = assertion()
    first = asyncio.create_task(invoke(token))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        result = await asyncio.wait_for(invoke(token), timeout=1)
        assert result.status_code == 503
        assert result.json()["error"]["code"] == "identity-review-unavailable"
        assert token not in result.text
        assert not received
    finally:
        release.set()


def test_review_uses_explicit_tls_and_rereads_reviewer_token(reviewer, monkeypatch):
    resolver, key = reviewer
    calls = []

    class Connection:
        def __init__(self, host, port, *, context, timeout):
            assert (host, port, timeout) == ("apiserver.example.test", 6443, 3)
            assert context.check_hostname

        def request(self, method, path, body, headers):
            assert (method, path) == ("POST", "/apis/authentication.k8s.io/v1/tokenreviews")
            assert json.loads(body)["spec"] == {"token": "subject.jwt.token", "audiences": [AUDIENCE]}
            calls.append(headers["Authorization"])

        def getresponse(self):
            return type("Response", (), {
                "status": 201,
                "read": lambda self, size: json.dumps({"status": status()}).encode(),
            })()

        def close(self):
            pass

    monkeypatch.setattr("vuoro_service.kubernetes_identity.http.client.HTTPSConnection", Connection)
    assert resolver._review("subject.jwt.token") == status()
    key.write_text("reviewer.credential.two\n")
    assert resolver._review("subject.jwt.token") == status()
    assert calls == ["Bearer reviewer.credential.one", "Bearer reviewer.credential.two"]

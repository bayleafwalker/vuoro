"""Shell half of the depth-1 run credential contract; owner enforces again."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.contracts import OperationDefinition
from vuoro_service.identity import Identity, StaticBearerIdentityResolver
from vuoro_service.run_delegation import DelegatedBearerIdentityResolver, DelegatedCredentialInvalid

TOKEN = "vrd_" + "A" * 52
OP = "work.run.resolve-v1"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@dataclass(frozen=True)
class Scope:
    delegation_id: str = "dlg_fixture"
    run_id: str = "run_fixture"
    operations: tuple = (OP,)


def resolved(**overrides):
    values = dict(delegation=Scope(), repo_id="repo-a", principal_id="test:parent:0",
                  workspace_id="ws-a", client_id="client-a", grant_id="grant-a",
                  authorities=frozenset({"work:read", "work:evidence", "work:admin"}),
                  expires_at_datetime=datetime.now(timezone.utc) + timedelta(minutes=5))
    return SimpleNamespace(**(values | overrides))


def request(token=TOKEN):
    return Request({"type": "http", "headers": [(b"authorization", ("Bearer " + token).encode())]})


def primary():
    return StaticBearerIdentityResolver({"ordinary": Identity("parent", "dev", principal_id="test:parent:0")})


@pytest.mark.anyio
async def test_only_owner_digest_is_sent_and_identity_keeps_exact_binding():
    digests = []
    owner = resolved()
    resolver = DelegatedBearerIdentityResolver(primary(), environment="dev",
        lookup=lambda digest: digests.append(digest) or owner)
    identity = await resolver(request())
    assert digests == [hashlib.sha256(TOKEN.encode()).hexdigest()]
    assert identity.actor == "delegation:dlg_fixture"
    assert identity.delegation is owner.delegation
    assert identity.repo_ids == frozenset({"repo-a"})
    assert identity.authorities == frozenset({"work:read", "work:evidence"})
    assert (identity.principal_id, identity.workspace_id, identity.client_id, identity.grant_id) == (
        "test:parent:0", "ws-a", "client-a", "grant-a")


@pytest.mark.anyio
async def test_primary_static_or_async_modes_are_unchanged_for_unreserved_credentials():
    async def async_primary(req):
        return primary()(req)
    for base in (primary(), async_primary):
        resolver = DelegatedBearerIdentityResolver(base, environment="dev", lookup=None)
        assert (await resolver(request("ordinary"))).actor == "parent"


@pytest.mark.anyio
async def test_unsupported_malformed_or_failed_delegation_never_falls_back_or_leaks_owner_error():
    calls = []
    def forbidden_primary(req):
        calls.append("primary")
        raise AssertionError("must not be called")
    def failed(digest):
        raise RuntimeError("private owner failure and token " + TOKEN)
    for token, lookup in ((TOKEN, None), ("vrd_bad", failed), (TOKEN, failed)):
        resolver = DelegatedBearerIdentityResolver(forbidden_primary, environment="dev", lookup=lookup)
        with pytest.raises(DelegatedCredentialInvalid) as error:
            await resolver(request(token))
        assert TOKEN not in str(error.value) and "private" not in str(error.value)
    assert not calls


@pytest.mark.anyio
@pytest.mark.parametrize("overrides", [
    {"expires_at_datetime": datetime.now(timezone.utc) - timedelta(seconds=1)},
    {"repo_id": "*"}, {"repo_id": ""}, {"workspace_id": ""},
    {"delegation": Scope(operations=("work.message.send-v1",))},
    {"delegation": Scope(operations=(OP, OP))},
])
async def test_invalid_owner_projection_fails_closed(overrides):
    resolver = DelegatedBearerIdentityResolver(primary(), environment="dev", lookup=lambda _: resolved(**overrides))
    with pytest.raises(DelegatedCredentialInvalid):
        await resolver(request())


def service(owner_lookup):
    calls = []
    registry = CatalogRegistry()
    for operation in (OP, "work.lease.currentness-v1", "work.message.send-v1", "work.run.delegate-v1"):
        registry.register(OperationDefinition(
            name=operation, owning_domain="work", input_schema={"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
            result_schema={"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}, required_authority="work:read",
            execution_semantics="read", idempotency="not-allowed", repo_scoped=True),
            lambda arguments, context: calls.append(context) or {"ok": True})
    resolver = DelegatedBearerIdentityResolver(primary(), environment="dev", lookup=owner_lookup)
    return create_app(settings=ServiceSettings(environment_name="dev", environment_class="development"),
                      registry=registry, identity_resolver=resolver), calls


@pytest.mark.anyio
async def test_http_allowed_run_and_repo_reach_handler_but_other_scopes_do_not():
    app, calls = service(lambda _: resolved())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        async def invoke(operation, run_id="run_fixture", repo="repo-a", arguments=None):
            return await client.post("/api/invoke/v1", headers={"authorization": "Bearer " + TOKEN,
                "X-Vuoro-Client-Protocol": "1"}, json={"schema_version": "invocation/v1",
                "request_id": "req1", "operation": operation, "repo_id": repo,
                "arguments": {"run_id": run_id} if arguments is None else arguments})
        assert (await invoke(OP)).status_code == 200
        for response in (await invoke(OP, "run_other"), await invoke("work.message.send-v1"),
                         await invoke("work.run.delegate-v1"), await invoke(OP, arguments=[])):
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "delegation-operation-not-allowed"
        assert (await invoke(OP, repo="repo-b")).status_code == 403
    assert len(calls) == 1 and calls[0].identity.delegation.run_id == "run_fixture"


@pytest.mark.anyio
async def test_every_invocation_resolves_current_owner_state_without_caching():
    state = [resolved()]
    lookup_calls = []
    def lookup(digest):
        lookup_calls.append(digest)
        if state[0] is None:
            raise ValueError("revoked")
        return state[0]
    resolver = DelegatedBearerIdentityResolver(primary(), environment="dev", lookup=lookup)
    assert (await resolver(request())).delegation.run_id == "run_fixture"
    state[0] = None
    with pytest.raises(DelegatedCredentialInvalid):
        await resolver(request())
    assert len(lookup_calls) == 2


@pytest.mark.anyio
async def test_hosted_resolver_cannot_cross_workspace_or_project_repo_boundary():
    for record in (resolved(workspace_id="another-workspace"), resolved(repo_id="another-repo")):
        resolver = DelegatedBearerIdentityResolver(primary(), environment="dev", lookup=lambda _: record,
            expected_workspace_id="ws-a", allowed_repo_ids=frozenset({"repo-a"}))
        with pytest.raises(DelegatedCredentialInvalid):
            await resolver(request())


@pytest.mark.anyio
async def test_only_exact_owner_currentness_read_is_added_to_delegated_surface():
    operation="work.lease.currentness-v1"
    owner=resolved(delegation=Scope(operations=(operation,)),authorities=frozenset({"work:read"}))
    app,calls=service(lambda _:owner)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test",
                                 headers={"Authorization":"Bearer "+TOKEN,"X-Vuoro-Client-Protocol":"1"}) as client:
        for name,run in ((operation,"run_fixture"),(OP,"run_fixture"),
                         ("work.run.delegate-v1","run_fixture"),(operation,"run_other")):
            response=await client.post('/api/invoke/v1',json={"schema_version":"invocation/v1","request_id":"fixture-currentness","operation":name,
                "arguments":{"run_id":run},"repo_id":"repo-a"})
            assert response.status_code==(200 if name==operation and run=="run_fixture" else 403)
        assert len(calls)==1
        assert calls[0].identity.delegation is owner.delegation
        assert calls[0].identity.authorities==frozenset({"work:read"})
    old,calls=service(lambda _:resolved())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=old),base_url="http://test",
                                 headers={"Authorization":"Bearer "+TOKEN,"X-Vuoro-Client-Protocol":"1"}) as client:
        response=await client.post('/api/invoke/v1',json={"schema_version":"invocation/v1","request_id":"fixture-currentness-old","operation":operation,
            "arguments":{"run_id":"run_fixture"},"repo_id":"repo-a"})
        assert response.status_code==403 and not calls

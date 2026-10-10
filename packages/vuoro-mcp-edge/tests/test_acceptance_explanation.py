"""Exercise the published projection through the production caller transport."""
import asyncio
import json

import httpx
import pytest

from acceptance_support import records
from operator_projection import reconstruction as p1
from vuoro_mcp_edge.acceptance_explanation import build_spec
from vuoro_mcp_edge.contract import REQUIRED_OPERATIONS
from vuoro_mcp_edge.errors import WorkSourceUnavailable
from vuoro_mcp_edge.toolsets import ToolFailure
from vuoro_mcp_edge.work_source import ForwardedIdentity, ShellWorkSource

CALLER = ForwardedIdentity(assertion="caller-assertion", request_id="request", repo_id="repo-a")


def catalog(revision="rev1", *, changed=None, missing=None):
    return {"revision": revision, "operations": [
        {"name": op, "execution_semantics": "write" if op == changed else "read"}
        for op in sorted(p1.READ_OPS | set(REQUIRED_OPERATIONS)) if op != missing]}


class Owner:
    def __init__(self):
        self.values = records()
        self.posts = []
        self.catalog = catalog()
        self.refreshed = None
        self.denied = None

    def handle(self, request):
        if request.method == "GET":
            return httpx.Response(200, json=self.catalog)
        body = json.loads(request.content)
        self.posts.append((body, dict(request.headers)))
        op = body["operation"]
        if self.refreshed:
            self.catalog, self.refreshed = self.refreshed, None
            return httpx.Response(409, json={"error": {"code": "stale-catalog"}})
        if op == self.denied:
            return httpx.Response(403, json={"error": {"code": "forbidden", "message": "PRIVATE-OWNER-ERROR"}})
        return httpx.Response(200, json={"status": "accepted", "operation": op, "result": self.values[op]})


async def explain(owner):
    source = ShellWorkSource(transport=httpx.MockTransport(owner.handle))
    try:
        spec = build_spec(source)
        return await spec.run(spec.parse({"intent_id": "effect_demo"}), CALLER)
    finally:
        await source.aclose()


def test_complete_protected_links_forward_the_same_caller_for_four_reads():
    owner = Owner()
    result = asyncio.run(explain(owner))
    dto = result["presentation"]
    assert dto["status"] == "complete" and dto["missing"] == []
    assert dto["authorizes_effects"] is False
    assert set(dto["provenance"].values()) == {"live-owner-reads", "unknown"}
    assert len(owner.posts) == 4
    assert {body["operation"] for body, _ in owner.posts} == p1.READ_OPS
    for body, headers in owner.posts:
        assert body["catalog_revision"] == "rev1" and body["repo_id"] == "repo-a"
        assert body["request_id"] == "request"
        assert headers["x-vuoro-identity"] == "caller-assertion"
    raw = json.dumps(result)
    for private in ("Review the exact bytes", "--- a/demo", "issuer:reviewer:0", "clean-patch-application", "/pull/1"):
        assert private not in raw


@pytest.mark.parametrize("op", sorted(p1.READ_OPS))
def test_denied_owner_read_is_explicit_without_upstream_error(op):
    owner = Owner()
    owner.denied = op
    result = asyncio.run(explain(owner))
    dto = result["presentation"]
    assert next(row for row in dto["sources"] if row["operation"] == op)["status"] == "unavailable"
    # Frozen protected evidence establishes its link independently of the
    # current Decision list. The owner projector defines link completeness.
    assert dto["status"] == ("complete" if op == p1.DECISIONS else "incomplete")
    assert "PRIVATE-OWNER-ERROR" not in json.dumps(result)
    assert len(owner.posts) == (1 if op == p1.EFFECT else 4)


def test_missing_receipt_and_changed_release_do_not_become_complete():
    owner = Owner()
    owner.values[p1.EFFECT]["intent"]["application"] = None
    assert "effect_receipt" in asyncio.run(explain(owner))["presentation"]["missing"]
    owner = Owner()
    owner.values[p1.RELEASE]["release"]["release_digest"] = "0" * 64
    assert asyncio.run(explain(owner))["presentation"]["status"] == "conflict"


@pytest.mark.parametrize("field,value", [("repo_id", "foreign"), ("intent_id", "other"),
                                         ("item_id", True), ("item_id", 2**63)])
def test_invalid_effect_binding_stops_before_related_reads(field, value):
    owner = Owner()
    row = owner.values[p1.EFFECT] if field == "repo_id" else owner.values[p1.EFFECT]["intent"]
    row[field] = value
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(explain(owner))
    assert caught.value.code == "owner-response-invalid"
    assert len(owner.posts) == 1


@pytest.mark.parametrize("mode", ["write", "missing"])
def test_initial_non_read_catalog_refuses_before_any_owner_invocation(mode):
    owner = Owner()
    owner.catalog = catalog(**{ "changed" if mode == "write" else "missing": p1.RELEASE})
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(explain(owner))
    assert caught.value.code == "owner-read-unavailable"
    assert owner.posts == []


@pytest.mark.parametrize("operations", [7, "bad", {}, None, [None],
    [{"name": []}], [{"name": p1.EFFECT, "execution_semantics": "read"},
                      {"name": p1.EFFECT, "execution_semantics": "write"}]])
def test_malformed_or_ambiguous_catalog_fails_closed(operations):
    owner = Owner()
    owner.catalog["operations"] = operations
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(explain(owner))
    assert caught.value.code == "owner-read-unavailable"
    assert owner.posts == []


@pytest.mark.parametrize("mode", ["write", "missing"])
def test_stale_catalog_cannot_retry_an_operation_that_is_no_longer_read(mode):
    owner = Owner()
    owner.refreshed = catalog("rev2", **{"changed" if mode == "write" else "missing": p1.EFFECT})
    async def invoke():
        source = ShellWorkSource(transport=httpx.MockTransport(owner.handle))
        try:
            await source._invoke(p1.EFFECT, {"intent_id": "effect_demo"}, CALLER, require_read=True)
        finally:
            await source.aclose()
    with pytest.raises(WorkSourceUnavailable):
        asyncio.run(invoke())
    assert len(owner.posts) == 1


def test_stale_read_retries_once_with_fresh_checked_revision():
    owner = Owner()
    owner.refreshed = catalog("rev2")
    assert asyncio.run(explain(owner))["presentation"]["status"] == "complete"
    assert [body["catalog_revision"] for body, _ in owner.posts] == ["rev1", "rev2", "rev2", "rev2", "rev2"]


def test_concurrent_refresh_cannot_replace_the_checked_read_revision():
    owner = Owner()
    class RefreshingSource(ShellWorkSource):
        async def _post(self, operation, arguments, identity, **basis):
            # Another request finishes refreshing shared metadata after this
            # invocation checked its semantics, before the transport emits it.
            owner.catalog = catalog("rev2", changed=p1.EFFECT)
            await self._ensure_catalog(force_refresh=True)
            return await super()._post(operation, arguments, identity, **basis)
    async def invoke():
        source = RefreshingSource(transport=httpx.MockTransport(owner.handle))
        try:
            await source._invoke(p1.EFFECT, {"intent_id": "effect_demo"}, CALLER, require_read=True)
        finally:
            await source.aclose()
    asyncio.run(invoke())
    assert len(owner.posts) == 1
    assert owner.posts[0][0]["catalog_revision"] == "rev1"


@pytest.mark.parametrize("args", [{}, {"intent_id": "x", "profile": "admin"},
                                  {"intent_id": "x" * 129}, {"intent_id": "../secret"}])
def test_arguments_cannot_choose_credentials_scope_or_unbounded_identifiers(args):
    source = ShellWorkSource()
    try:
        with pytest.raises(ToolFailure):
            build_spec(source).parse(args)
    finally:
        asyncio.run(source.aclose())


def real_stack(tmp_path):
    """Real shell authorization and proof verification; synthetic owner only."""
    import secrets
    from fastapi.testclient import TestClient
    from edge_support import ENVIRONMENT, key_pair, resolver
    from test_edge_replay_protection import _definition, _Recording
    from vuoro_mcp_edge.edge_proof_auth import EdgeProofAuth
    from vuoro_mcp_edge.read_tools import build_toolset
    from vuoro_mcp_edge.runs import UnavailableRunRegistry
    from vuoro_mcp_edge.server import create_edge_app
    from vuoro_mcp_edge.toolsets import ToolsetContext
    from vuoro_service.app import ServiceSettings, create_app
    from vuoro_service.catalog import CatalogRegistry
    from vuoro_service.contracts import DomainCompatibility
    from vuoro_service.edge_proof import EdgeProofVerifier

    keys = key_pair(tmp_path)
    proof_key = secrets.token_bytes(32)
    values, calls = records(), []
    registry = CatalogRegistry()
    for operation in sorted(p1.READ_OPS | set(REQUIRED_OPERATIONS)):
        def handle(arguments, context, op=operation):
            calls.append((op, context.repo_id, context.identity.principal_id))
            return values.get(op, {})
        registry.register(_definition(operation, "work.effect.get" if operation == p1.EFFECT else "work:read", "read"), handle)
    shell = create_app(settings=ServiceSettings(
        environment_name=ENVIRONMENT, environment_class="development", compatibility_state="compatible",
        domains={"work": DomainCompatibility(api_version="work/v1", schema_version="work-schema/1", state="compatible")}),
        registry=registry, identity_resolver=resolver(keys[0], edge_proofs=EdgeProofVerifier(proof_key)))
    transport = _Recording(httpx.ASGITransport(app=shell))
    source = ShellWorkSource(transport=transport, auth=EdgeProofAuth(proof_key))
    toolset = build_toolset(ToolsetContext(env={}, work_source=source, runs=UnavailableRunRegistry()))
    edge = create_edge_app(identity_resolver=resolver(keys[0]), work_source=source, toolsets=(toolset,))
    return TestClient(edge), keys[1], calls, transport, shell


def test_real_shell_four_reads_use_distinct_body_bound_proofs_and_replay_is_refused(tmp_path):
    from edge_support import assertion, call, identity_headers
    from vuoro_service.edge_proof import PROOF_HEADER
    client, private, calls, transport, shell = real_stack(tmp_path)
    headers = identity_headers(assertion(private, authorities=["work:read", "work.effect.get"],
                                         client_id="claude-connector", grant_id="grant-1"))
    with client:
        result = client.post("/mcp", headers=headers, json=call("explain_acceptance", {"intent_id": "effect_demo"})).json()["result"]
        assert not result.get("isError"), result
        assert result["structuredContent"]["presentation"]["status"] == "complete"
        sent = [row for row in transport.sent if row[0] == "POST"]
        assert len(sent) == len(calls) == 4
        assert len({row[2][PROOF_HEADER.lower()] for row in sent}) == 4
        assert {row[2]["x-vuoro-identity"] for row in sent} == {headers["X-Vuoro-Identity"]}
        assert {row[1] for row in calls} == {"repo-a"}
        from fastapi.testclient import TestClient
        with TestClient(shell) as upstream:
            replay = upstream.post(sent[0][1], headers=sent[0][2], content=sent[0][3])
            no_proof = {key: value for key, value in sent[0][2].items() if key != PROOF_HEADER.lower()}
            no_proof["x-vuoro-identity"] = assertion(private, authorities=["work:read", "work.effect.get"],
                                                     client_id="claude-connector", grant_id="grant-1")
            refused = upstream.post(sent[0][1], headers=no_proof, content=sent[0][3])
        assert replay.status_code in {401, 403}
        assert refused.status_code in {401, 403}
        assert len(calls) == 4


@pytest.mark.parametrize("authorities", [["work:read"], ["work.effect.get"], []])
def test_real_edge_requires_both_authorities_before_owner_reads(tmp_path, authorities):
    from edge_support import assertion, call, identity_headers
    client, private, calls, transport, _ = real_stack(tmp_path)
    with client:
        headers = identity_headers(assertion(private, authorities=authorities))
        response = client.post("/mcp", headers=headers, json=call("explain_acceptance", {"intent_id": "effect_demo"}))
        if not authorities:
            assert response.status_code == 401
        else:
            result = response.json()["result"]
            assert result["isError"]
            assert result["structuredContent"]["error"]["code"] == "authority-required"
        assert calls == []
        assert not any(row[0] == "POST" for row in transport.sent)


def test_real_edge_refuses_foreign_workspace_before_owner_reads(tmp_path):
    from edge_support import assertion, call, identity_headers
    client, private, calls, transport, _ = real_stack(tmp_path)
    with client:
        headers = identity_headers(assertion(private, authorities=["work:read", "work.effect.get"],
                                              workspace_id="01K99999999999999999999999"))
        response = client.post("/mcp", headers=headers, json=call("explain_acceptance", {"intent_id": "effect_demo"}))
        assert response.status_code == 401
        assert calls == []
        assert not any(row[0] == "POST" for row in transport.sent)

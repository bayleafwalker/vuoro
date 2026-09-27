"""Replay protection across the edge and the real runtime shell (agentops#2519).

The shell is the unmodified protocol-v1 app with the gateway verifier and an
edge proof verifier, over ASGI; the record bucket runs against it through the
production `SprintctlRecordStore`, signed by `EdgeProofAuth`.  A small
in-memory stand-in plays sprintctl's `work.run.*` / `work.evidence.*` /
`work.session-note.*` operations behind the shell's catalog.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

import httpx
import pytest
from edge_support import (
    ENVIRONMENT,
    assertion,
    call,
    identity_headers,
    list_record,
    list_result,
    resolver,
)
from fastapi.testclient import TestClient
from vuoro_mcp_edge import record_tools
from vuoro_mcp_edge.edge_proof_auth import EdgeProofAuth
from vuoro_mcp_edge.record_tools import SprintctlRecordStore
from vuoro_mcp_edge.server import MCP_PATH, create_edge_app
from vuoro_mcp_edge.toolsets import ToolsetContext
from vuoro_mcp_edge.work_source import ShellWorkSource
from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry, OperationRejectedError
from vuoro_service.contracts import DomainCompatibility, OperationDefinition
from vuoro_service.edge_proof import (
    MAX_PROOFED_USES_PER_ASSERTION,
    PROOF_HEADER,
    EdgeProofVerifier,
    mint_edge_proof,
)

_SCHEMA = "https://json-schema.org/draft/2020-12/schema"
AUTHORITIES = ["work:read", "work:evidence"]
VALIDITY = {
    "basis": "indefinite",
    "valid_from": "2026-09-26T00:00:00Z",
    "valid_until": None,
    "component_digests": {},
}
REGISTER_ARGUMENTS = {
    "harness_id": "claude-code",
    "harness_build": "1.0.0",
    "model_id": "model-1",
    "recipe_id": "recipe-1",
    "observed_profile": {"instruction_digest": "sha256:" + "a" * 64, "skill_digests": []},
    "idempotency_key": "register-key-0001",
}


class _Sprintctl:
    """The record operations' observable behaviour, in memory."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.chain: list[dict[str, Any]] = []
        self.notes: list[str] = []
        #: Appends to refuse with evidence-chain-conflict, each after a
        #: concurrent writer takes the slot.
        self.conflicts = 0
        self.calls: list[str] = []

    def _binding(self, context) -> dict[str, Any]:
        identity = context.identity
        return {
            "principal_id": identity.principal_id,
            "workspace_id": identity.workspace_id,
            "client_id": identity.client_id,
            "grant_id": identity.grant_id,
        }

    def register(self, arguments, context):
        self.calls.append("register")
        run_id = "run_" + "".join(secrets.choice("0123456789ABCDEFGHJKMNPQRSTVWXYZ") for _ in range(26))
        self.runs[run_id] = self._binding(context)
        return {
            "run": {
                "run_id": run_id,
                **{key: arguments[key] for key in ("harness_id", "harness_build", "model_id", "recipe_id", "observed_profile")},
                "grant_ids": [],
                "claim_ids": [],
            }
        }

    def resolve(self, arguments, context):
        self.calls.append("resolve")
        binding = self.runs.get(arguments["run_id"])
        if binding != self._binding(context):
            raise OperationRejectedError("run-not-found", "no such run", http_status=404)
        return {"repo_id": context.repo_id, "run_id": arguments["run_id"], **binding}

    def tail(self, arguments, context):
        self.calls.append("tail")
        return {
            "repo_id": context.repo_id,
            "run_id": arguments["run_id"],
            "item": self.chain[-1] if self.chain else None,
        }

    def _item(self, seq: int, item_id: str, prev: str | None) -> dict[str, Any]:
        return {
            "item_id": item_id, "kind": "test", "ref": f"ref-{seq}",
            "digest": "sha256:" + "c" * 64, "collector": "tester", "validity": VALIDITY,
            "claims": [], "provenance": {}, "chain_seq": seq, "chain_prev_digest": prev,
        }

    def append(self, arguments, context):
        self.calls.append("append")
        if self.conflicts:
            self.conflicts -= 1
            self.chain.append(self._item(len(self.chain), f"evi_other{len(self.chain)}", None))
        if arguments["chain_seq"] != len(self.chain):
            raise OperationRejectedError("evidence-chain-conflict", "stale link", http_status=409)
        item = self._item(arguments["chain_seq"], arguments["item_id"], arguments["chain_prev_digest"])
        self.chain.append(item)
        return {"repo_id": context.repo_id, "run_id": arguments["run_id"], "item": item}

    def note(self, arguments, context):
        self.calls.append("note")
        self.notes.append(arguments["note"])
        return {"repo_id": context.repo_id, "run_id": arguments["run_id"], "note_id": "note_1"}

    def list(self, arguments, context):
        self.calls.append("list")
        return list_result(list_record(1))


def _definition(name: str, authority: str, semantics: str) -> OperationDefinition:
    return OperationDefinition(
        name=name,
        owning_domain="work",
        input_schema={"$schema": _SCHEMA, "type": "object"},
        result_schema={"$schema": _SCHEMA, "type": "object"},
        required_authority=authority,
        execution_semantics=semantics,
        idempotency="not-allowed",
        repo_scoped=True,
    )


class _Counting:
    """Counts verifications, so the trace in the PR is a measured number."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        return self.inner(request)


class _Recording(httpx.AsyncBaseTransport):
    """Records every edge -> shell request as sent (headers and bytes)."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.sent: list[tuple[str, str, dict[str, str], bytes]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        self.sent.append((request.method, request.url.path, dict(request.headers), body))
        return await self.inner.handle_async_request(request)


class _Stack:
    def __init__(self, keys, *, proofs: bool = True, require_oauth_grant: bool = False) -> None:
        self.private = keys[1]
        self.sprintctl = sprintctl = _Sprintctl()
        registry = CatalogRegistry()
        for name, handler, authority, semantics in (
            ("work.public.list-v1", sprintctl.list, "work:read", "read"),
            ("work.public.item-v1", sprintctl.list, "work:read", "read"),
            (record_tools.OPERATION_RUN_REGISTER, sprintctl.register, "work:evidence", "write"),
            (record_tools.OPERATION_RUN_RESOLVE, sprintctl.resolve, "work:evidence", "read"),
            (record_tools.OPERATION_EVIDENCE_TAIL, sprintctl.tail, "work:evidence", "read"),
            (record_tools.OPERATION_EVIDENCE_APPEND, sprintctl.append, "work:evidence", "write"),
            (record_tools.OPERATION_SESSION_NOTE_WRITE, sprintctl.note, "work:evidence", "write"),
        ):
            registry.register(_definition(name, authority, semantics), handler)
        key = secrets.token_bytes(32)
        self.proof_key = key
        self.shell_verifier = _Counting(resolver(keys[0], edge_proofs=EdgeProofVerifier(key)))
        self.shell = create_app(
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
            identity_resolver=self.shell_verifier,
        )
        self.transport = _Recording(httpx.ASGITransport(app=self.shell))
        auth = EdgeProofAuth(key) if proofs else None
        source = ShellWorkSource(
            base_url="http://127.0.0.1:8080", transport=self.transport, auth=auth
        )
        store = SprintctlRecordStore(
            base_url="http://127.0.0.1:8080", timeout=5.0, transport=self.transport, auth=auth
        )
        toolset = record_tools.build_toolset(
            ToolsetContext(env={}, work_source=source, runs=store)
        )
        self.edge_verifier = _Counting(
            resolver(keys[0], require_oauth_grant=require_oauth_grant)
        )
        self.edge = create_edge_app(
            identity_resolver=self.edge_verifier, work_source=source, toolsets=(toolset,)
        )
        self.client = TestClient(self.edge)

    def headers(self, **claims: Any) -> dict[str, str]:
        return identity_headers(assertion(self.private, authorities=AUTHORITIES, **claims))

    def call(self, tool: str, arguments: dict[str, Any], headers: dict[str, str] | None = None):
        edge_before, shell_before = self.edge_verifier.calls, self.shell_verifier.calls
        response = self.client.post(MCP_PATH, headers=headers or self.headers(), json=call(tool, arguments))
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        counts = (
            self.edge_verifier.calls - edge_before,
            self.shell_verifier.calls - shell_before,
        )
        return result, counts


def _append_arguments(run_id: str, key: str = "evidence-key-0001") -> dict[str, Any]:
    return {
        "run_id": run_id, "kind": "test", "ref": "ref-x", "digest": "sha256:" + "d" * 64,
        "collector": "tester", "validity": VALIDITY, "idempotency_key": key,
    }


def test_every_multi_call_flow_works_and_each_assertion_is_verified_as_traced(keys) -> None:
    stack = _Stack(keys)

    listed, counts = stack.call("list_ready_work", {})
    assert listed["isError"] is False, listed
    assert counts == (1, 1)

    registered, counts = stack.call("register_run", REGISTER_ARGUMENTS)
    assert registered["isError"] is False, registered
    assert counts == (1, 1)
    run_id = registered["structuredContent"]["run_id"]

    # resolve -> tail -> append: one edge verification, three shell calls
    # on the one forwarded assertion.
    appended, counts = stack.call("append_evidence", _append_arguments(run_id))
    assert appended["isError"] is False, appended
    assert appended["structuredContent"]["chain_seq"] == 0
    assert counts == (1, 3)

    # The chain-tail retry: resolve, tail, append (conflict), tail, append.
    stack.sprintctl.conflicts = 1
    relinked, counts = stack.call("append_evidence", _append_arguments(run_id, "evidence-key-0002"))
    assert relinked["isError"] is False, relinked
    assert relinked["structuredContent"]["chain_seq"] == 2
    assert counts == (1, 5)

    # Worst case: every attempt conflicts (resolve + 3 x (tail, append)).
    stack.sprintctl.conflicts = record_tools.CHAIN_ATTEMPTS
    exhausted, counts = stack.call("append_evidence", _append_arguments(run_id, "evidence-key-0003"))
    assert exhausted["structuredContent"]["error"]["code"] == "evidence-chain-conflict"
    assert counts == (1, 1 + 2 * record_tools.CHAIN_ATTEMPTS)

    noted, counts = stack.call(
        "write_session_note", {"run_id": run_id, "note": "hello", "idempotency_key": "note-key-0001"}
    )
    assert noted["isError"] is False, noted
    assert counts == (1, 2)
    assert stack.sprintctl.notes == ["hello"]

    # Every assertion-carrying shell call carried its own, distinct proof.
    proofs = [headers.get(PROOF_HEADER) for _, path, headers, _ in stack.transport.sent if path == "/api/invoke/v1"]
    assert all(proofs) and len(set(proofs)) == len(proofs)


def test_without_edge_proofs_a_multi_call_tool_is_refused_as_a_replay(keys) -> None:
    """The measurement behind the design: a shell that accepts each jti once
    breaks every tool that reuses the forwarded assertion."""

    stack = _Stack(keys, proofs=False)
    registered, _ = stack.call("register_run", REGISTER_ARGUMENTS)
    assert registered["isError"] is False  # one shell call: survives
    run_id = registered["structuredContent"]["run_id"]
    appended, counts = stack.call("append_evidence", _append_arguments(run_id))
    assert appended["isError"] is True
    assert counts == (1, 2)  # resolve accepted, tail refused
    assert stack.sprintctl.calls[-1] == "resolve"


def test_a_captured_assertion_or_internal_proof_cannot_be_replayed(keys) -> None:
    stack = _Stack(keys)
    headers = stack.headers(client_id="claude-connector", grant_id="grant-1")
    registered, _ = stack.call("register_run", REGISTER_ARGUMENTS, headers=headers)
    assert registered["isError"] is False, registered
    run_id = registered["structuredContent"]["run_id"]

    # 1. The inbound assertion again, at the edge.
    again = stack.client.post(MCP_PATH, headers=headers, json=call("register_run", REGISTER_ARGUMENTS))
    assert again.status_code == 401
    assert again.json()["error"]["code"] == -32003

    method, path, sent_headers, body = stack.transport.sent[-1]
    assert path == "/api/invoke/v1" and PROOF_HEADER in sent_headers
    shell = TestClient(stack.shell)
    passthrough = {
        name: value
        for name, value in sent_headers.items()
        if name in ("x-vuoro-identity", "x-request-id", "x-vuoro-client-protocol", "content-type", PROOF_HEADER)
    }

    # 2. The captured shell request, byte for byte.
    replayed = shell.post(path, headers=passthrough, content=body)
    assert replayed.status_code == 401
    assert replayed.json()["error"]["code"] == "identity-replayed"

    # 3. The captured proof on a different body (a write it never covered).
    forged = json.loads(body)
    forged["operation"] = record_tools.OPERATION_SESSION_NOTE_WRITE
    forged["arguments"] = {"run_id": run_id, "note": "forged", "idempotency_key": "note-key-0009"}
    rebound = shell.post(path, headers=passthrough, content=json.dumps(forged).encode())
    assert rebound.status_code == 401
    assert rebound.json()["error"]["code"] == "identity-required"

    # 4. The captured assertion straight at the shell, without the edge.
    direct = {name: value for name, value in passthrough.items() if name != PROOF_HEADER}
    bare = shell.post(path, headers=direct, content=json.dumps(forged).encode())
    assert bare.status_code == 401
    assert bare.json()["error"]["code"] == "identity-edge-proof-required"
    assert stack.sprintctl.notes == []

    # A workspace-token-shaped assertion (no client/grant) the edge used is
    # also refused direct: the proof route marked its jti.
    plain = stack.headers()
    stack.call("list_ready_work", {}, headers=plain)
    _, path, sent_headers, body = stack.transport.sent[-1]
    direct = {
        name: value
        for name, value in sent_headers.items()
        if name in ("x-vuoro-identity", "x-request-id", "x-vuoro-client-protocol", "content-type")
    }
    marked = shell.post(path, headers=direct, content=body)
    assert marked.status_code == 401
    assert marked.json()["error"]["code"] == "identity-replayed"


def test_the_edge_accepts_a_concurrent_duplicate_exactly_once(keys) -> None:
    stack = _Stack(keys)
    headers = stack.headers()

    async def run() -> list[httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=stack.edge), base_url="http://edge"
        ) as client:
            return await asyncio.gather(
                *(client.post(MCP_PATH, headers=headers, json=call("list_ready_work")) for _ in range(12))
            )

    responses = asyncio.run(run())
    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200] + [401] * 11
    refused = [r.json()["error"]["code"] for r in responses if r.status_code == 401]
    assert set(refused) == {-32003}
    assert stack.sprintctl.calls == ["list"]


@pytest.mark.parametrize("method", ["initialize", "tools/list"])
def test_a_replay_is_refused_before_any_method_runs(keys, method) -> None:
    stack = _Stack(keys)
    headers = stack.headers()
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}
    assert stack.client.post(MCP_PATH, headers=headers, json=body).status_code == 200
    again = stack.client.post(MCP_PATH, headers=headers, json=body)
    assert again.status_code == 401
    assert again.json()["error"]["code"] == -32003


# -- cross-process replay (review of bfb6718) ---------------------------------


def _send_directly_to_the_shell(stack: _Stack, headers: dict[str, str]) -> httpx.Response:
    """The REST path: the gateway sends the assertion straight to the shell,
    which consumes its jti.  An attacker in the pod captures it on the way."""

    envelope = {
        "schema_version": "invocation/v1",
        "request_id": headers["X-Request-Id"],
        "operation": "work.public.list-v1",
        "arguments": {},
        "catalog_revision": None,
        "basis_revision": None,
        "idempotency_key": None,
        "repo_id": "repo-a",
    }
    return TestClient(stack.shell).post(
        "/api/invoke/v1",
        headers={**headers, "X-Vuoro-Client-Protocol": "1"},
        json=envelope,
    )


@pytest.mark.parametrize("require_oauth_grant", [False, True])
def test_a_rest_assertion_consumed_by_the_shell_cannot_be_replayed_at_mcp(
    keys, require_oauth_grant
) -> None:
    """The exploit from the review: a REST assertion (no client_id) consumed
    directly by the shell, then replayed at /mcp, wrote a session note.

    Both fixes close it on their own: the shell refuses on the proof route a
    jti its direct route consumed (require_oauth_grant=False, as once the
    audiences are split), and the edge refuses an assertion with no OAuth
    grant while the audiences are shared (require_oauth_grant=True).
    """

    stack = _Stack(keys, require_oauth_grant=require_oauth_grant)
    owner = dict(client_id="claude-connector", grant_id="grant-1") if require_oauth_grant else {}
    registered, _ = stack.call("register_run", REGISTER_ARGUMENTS, headers=stack.headers(**owner))
    assert registered["isError"] is False, registered
    run_id = registered["structuredContent"]["run_id"]

    rest = stack.headers()  # no client_id / grant_id: a workspace-token assertion
    consumed = _send_directly_to_the_shell(stack, rest)
    assert consumed.status_code == 200, consumed.text

    response = stack.client.post(
        MCP_PATH,
        headers=rest,
        json=call(
            "write_session_note",
            {"run_id": run_id, "note": "replayed", "idempotency_key": "note-key-0666"},
        ),
    )
    if require_oauth_grant:
        assert response.status_code == 401
        assert response.json()["error"]["code"] == -32001
    else:
        result = response.json()["result"]
        assert result["isError"] is True, result
    assert stack.sprintctl.notes == []
    assert "note" not in stack.sprintctl.calls


def test_an_assertion_used_through_the_edge_cannot_then_go_direct(keys) -> None:
    stack = _Stack(keys)
    headers = stack.headers()
    listed, _ = stack.call("list_ready_work", {}, headers=headers)
    assert listed["isError"] is False
    direct = _send_directly_to_the_shell(stack, headers)
    assert direct.status_code == 401
    assert direct.json()["error"]["code"] == "identity-replayed"


def test_the_proofed_use_cap_matches_the_largest_tool_call() -> None:
    """The shell's cap is computed from this package's code: append_evidence
    makes one resolve plus CHAIN_ATTEMPTS rounds of tail + append."""

    assert MAX_PROOFED_USES_PER_ASSERTION == 1 + 2 * record_tools.CHAIN_ATTEMPTS


def test_the_flow_at_the_proofed_use_cap_passes_and_one_more_is_refused(keys) -> None:
    stack = _Stack(keys)
    registered, _ = stack.call("register_run", REGISTER_ARGUMENTS)
    run_id = registered["structuredContent"]["run_id"]

    # Exactly the cap: every append attempt conflicts, all 7 calls reach the
    # owner, and the tool reports the conflict (not a refused identity).
    headers = stack.headers()
    stack.sprintctl.conflicts = record_tools.CHAIN_ATTEMPTS
    exhausted, counts = stack.call(
        "append_evidence", _append_arguments(run_id, "evidence-key-0100"), headers=headers
    )
    assert counts == (1, MAX_PROOFED_USES_PER_ASSERTION)
    assert exhausted["structuredContent"]["error"]["code"] == "evidence-chain-conflict"
    assert stack.sprintctl.calls.count("append") == record_tools.CHAIN_ATTEMPTS

    # One more proofed use of the same assertion, with a freshly minted,
    # otherwise valid proof (what a leaked pod key allows), is refused.
    method, path, sent_headers, body = stack.transport.sent[-1]

    key = stack.proof_key
    extra = {
        name: value
        for name, value in sent_headers.items()
        if name in ("x-vuoro-identity", "x-request-id", "x-vuoro-client-protocol", "content-type")
    }
    extra[PROOF_HEADER] = mint_edge_proof(
        key, method=method, path=path, assertion=extra["x-vuoro-identity"], body=body
    )
    before = list(stack.sprintctl.calls)
    refused = TestClient(stack.shell).post(path, headers=extra, content=body)
    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "identity-replayed"
    assert stack.sprintctl.calls == before

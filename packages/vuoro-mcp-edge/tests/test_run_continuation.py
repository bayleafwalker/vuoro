"""agentops#2525 (M1-5, TS-8 second route): `register_run` takes a
`predecessor_run_id`, and a successor on a different identity reads the
predecessor's session notes and evidence.

Eligibility (agentops#253 R4 decision 3): the successor shares the
predecessor's workspace AND repository AND carries `work:read`; its
principal, OAuth client and grant may differ.  Continuation transfers
context, not authority: nothing the predecessor could do becomes something
the successor can do.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest
from edge_support import (
    ISSUER,
    REPO_ID,
    SUBJECT,
    WORKSPACE_ID,
    FakeShell,
    assertion,
    call,
    edge_client,
    identity_headers,
    rpc,
)
from vuoro_service.identity import Identity
from vuoro_mcp_edge import effect_tools
from vuoro_mcp_edge.effect_tools import InMemoryIntentStore, RepositoryEffectPolicy
from vuoro_mcp_edge.record_tools import (
    RECORD_OWNER_INCOMPATIBLE,
    SprintctlRecordStore,
    build_toolset,
)
from vuoro_mcp_edge.runs import (
    PREDECESSOR_NOT_ELIGIBLE,
    InMemoryRunRegistry,
    RunBinding,
)
from vuoro_mcp_edge.server import MCP_PATH
from vuoro_mcp_edge.toolsets import ToolFailure, ToolsetContext
from vuoro_mcp_edge.work_source import ForwardedIdentity, ShellWorkSource

REQUEST_ID = "01K33333333333333333333333"
PREDECESSOR_PRINCIPAL = f"{ISSUER}:{SUBJECT}:0"
SUCCESSOR_SUBJECT = "01K55555555555555555555555"
SUCCESSOR_PRINCIPAL = f"{ISSUER}:{SUCCESSOR_SUBJECT}:0"
OTHER_WORKSPACE = "01K22222222222222222222222"
OTHER_REPO = "repo-b"

OBSERVED_PROFILE = {"instruction_digest": "sha256:" + "a" * 64, "skill_digests": []}
_MANIFEST = {
    "harness_id": "h", "harness_build": "1", "model_id": "m", "recipe_id": "r",
    "observed_profile": OBSERVED_PROFILE,
}


class ReferenceRecordStore(InMemoryRunRegistry):
    """The record owner's continuation behaviour, in memory: runs from the
    reference registry, plus the notes and evidence the record tools write,
    read back only through the successor's own run."""

    supports_continuation = True

    def __init__(self) -> None:
        super().__init__()
        self.notes: dict[str, list[dict[str, Any]]] = {}
        self.evidence: dict[str, list[dict[str, Any]]] = {}

    async def evidence_tail(self, run_id: str, *, forwarded: Any) -> dict[str, Any] | None:
        items = self.evidence.get(run_id) or []
        return items[-1] if items else None

    async def append_evidence(self, run_id: str, *, forwarded: Any, **item: Any) -> dict[str, Any]:
        item.pop("idempotency_key")
        self.evidence.setdefault(run_id, []).append(item)
        return item

    async def write_session_note(
        self, run_id: str, *, forwarded: Any, note: str, idempotency_key: str
    ) -> dict[str, Any]:
        written = {"note_id": f"note-{len(self.notes.get(run_id, [])) + 1}", "note": note}
        self.notes.setdefault(run_id, []).append(written)
        return {"run_id": run_id, **written}

    async def read_predecessor_context(
        self, run_id: str, *, forwarded: Any, page: Any = None
    ) -> dict[str, Any]:
        if page:
            # Unpaged: refuse paging rather than silently ignore it (a
            # caller would read a first page as the whole).
            raise ToolFailure(
                "invalid-arguments", "this record store does not page predecessor context"
            )
        predecessor = self.predecessor_of(run_id)
        if predecessor is None:
            return {"predecessor_run_id": None, "session_notes": [], "evidence": []}
        return {
            "predecessor_run_id": predecessor,
            "session_notes": list(self.notes.get(predecessor, [])),
            "evidence": list(self.evidence.get(predecessor, [])),
        }


def _forwarded(
    *,
    principal_id: str = PREDECESSOR_PRINCIPAL,
    workspace_id: str = WORKSPACE_ID,
    repo_id: str = REPO_ID,
    client_id: str | None = "claude-connector",
    grant_id: str | None = "grant-1",
    authorities: frozenset[str] = frozenset({"work:read", "work:evidence"}),
) -> ForwardedIdentity:
    identity = Identity(
        actor="github:123",
        environment="vuoro-dev",
        authorities=authorities,
        repo_ids=frozenset({repo_id}),
        workspace_id=workspace_id,
        principal_id=principal_id,
        client_id=client_id,
        grant_id=grant_id,
    )
    return ForwardedIdentity(
        assertion="a.b.c", request_id=REQUEST_ID, repo_id=repo_id, identity=identity
    )


#: A different harness: another principal, OAuth client and grant, in the
#: predecessor's workspace and repository.
SUCCESSOR = dict(
    principal_id=SUCCESSOR_PRINCIPAL, client_id="other-harness", grant_id="grant-2"
)


def _toolset(store: Any) -> dict[str, Any]:
    context = ToolsetContext(
        env={}, work_source=ShellWorkSource(base_url="http://127.0.0.1:8080"), runs=store
    )
    toolset = build_toolset(context)
    assert toolset is not None
    return {spec.name: spec for spec in toolset.tools}


def _invoke(specs: dict[str, Any], name: str, arguments: dict[str, Any], forwarded: Any) -> Any:
    spec = specs[name]
    return asyncio.run(spec.run(spec.parse(arguments), forwarded))


def _register(
    specs: dict[str, Any], forwarded: Any, *, key: str, predecessor: str | None = None
) -> str:
    arguments: dict[str, Any] = {**_MANIFEST, "idempotency_key": key}
    if predecessor is not None:
        arguments["predecessor_run_id"] = predecessor
    return _invoke(specs, "register_run", arguments, forwarded)["run_id"]


def _refused(code: str, specs: dict[str, Any], name: str, arguments: dict[str, Any], forwarded: Any) -> ToolFailure:
    with pytest.raises(ToolFailure) as refused:
        _invoke(specs, name, arguments, forwarded)
    assert refused.value.code == code
    return refused.value


def _predecessor_with_history(store: ReferenceRecordStore) -> tuple[dict[str, Any], str]:
    specs = _toolset(store)
    predecessor = _forwarded()
    run_id = _register(specs, predecessor, key="pred-run-0001")
    _invoke(
        specs, "write_session_note",
        {"run_id": run_id, "note": "stopped after step 3", "idempotency_key": "note-key-0001"},
        predecessor,
    )
    _invoke(
        specs, "append_evidence",
        {
            "run_id": run_id, "kind": "test-report", "ref": "ci://run/1",
            "digest": "sha256:" + "c" * 64, "collector": "pytest",
            "validity": {"basis": "indefinite", "valid_from": "2026-09-27T00:00:00Z"},
            "idempotency_key": "evidence-key-0001",
        },
        predecessor,
    )
    return specs, run_id


# -- the reference registry -----------------------------------------------------


def test_the_registry_records_an_eligible_predecessor_and_binds_nothing_else() -> None:
    registry = InMemoryRunRegistry()
    predecessor = RunBinding(
        principal_id="github:1:0", workspace_id="w1", repo_id="r1",
        client_id="claude-connector", grant_id="g1",
    )
    successor = RunBinding(
        principal_id="github:2:0", workspace_id="w1", repo_id="r1",
        client_id="other-harness", grant_id="g2",
    )

    async def scenario() -> None:
        first = await registry.register(
            predecessor, idempotency_key="key-0001", forwarded=None, manifest=_MANIFEST
        )
        second = await registry.register(
            successor, idempotency_key="key-0002", forwarded=None, manifest=_MANIFEST,
            predecessor_run_id=first,
        )
        assert registry.predecessor_of(second) == first
        assert registry.predecessor_of(first) is None
        # Same key, same predecessor: the same run.  Another predecessor: a conflict.
        assert await registry.register(
            successor, idempotency_key="key-0002", forwarded=None, manifest=_MANIFEST,
            predecessor_run_id=first,
        ) == second
        with pytest.raises(ToolFailure) as conflict:
            await registry.register(
                successor, idempotency_key="key-0002", forwarded=None, manifest=_MANIFEST,
            )
        assert conflict.value.code == "idempotency-conflict"
        # Each run resolves to its own binding only, in both directions.
        assert await registry.resolve(second, successor, forwarded=None) == successor
        for run_id, caller in ((first, successor), (second, predecessor)):
            with pytest.raises(ToolFailure) as refused:
                await registry.resolve(run_id, caller, forwarded=None)
            assert refused.value.code == "run-not-found"

    asyncio.run(scenario())


def test_the_registry_refuses_another_workspace_repository_or_unknown_id_alike() -> None:
    registry = InMemoryRunRegistry()
    predecessor = RunBinding(principal_id="github:1:0", workspace_id="w1", repo_id="r1")

    async def scenario() -> None:
        first = await registry.register(
            predecessor, idempotency_key="key-0001", forwarded=None, manifest=_MANIFEST
        )
        refusals = []
        for caller, predecessor_run_id in (
            (replace(predecessor, workspace_id="w2"), first),
            (replace(predecessor, repo_id="r2"), first),
            (predecessor, "run_" + "0" * 26),
            (predecessor, "not-a-run"),
        ):
            with pytest.raises(ToolFailure) as refused:
                await registry.register(
                    caller, idempotency_key="key-0009", forwarded=None, manifest=_MANIFEST,
                    predecessor_run_id=predecessor_run_id,
                )
            refusals.append((refused.value.code, refused.value.message))
        # One code and one message: a refusal does not say whether the id exists.
        assert set(refusals) == {(PREDECESSOR_NOT_ELIGIBLE, refusals[0][1])}
        # Nothing was minted for any refused call.
        assert len(registry._runs) == 1

    asyncio.run(scenario())


# -- the record tools --------------------------------------------------------------


def test_a_different_identity_successor_reads_the_predecessors_notes_and_evidence() -> None:
    store = ReferenceRecordStore()
    specs, predecessor_run = _predecessor_with_history(store)
    successor = _forwarded(**SUCCESSOR)

    successor_run = _register(specs, successor, key="succ-run-0001", predecessor=predecessor_run)
    assert successor_run != predecessor_run
    context = _invoke(specs, "read_predecessor_context", {"run_id": successor_run}, successor)

    assert context["run_id"] == successor_run
    assert context["predecessor_run_id"] == predecessor_run
    assert [note["note"] for note in context["session_notes"]] == ["stopped after step 3"]
    [item] = context["evidence"]
    assert (item["kind"], item["ref"], item["chain_seq"]) == ("test-report", "ci://run/1", 0)


def test_a_run_without_a_predecessor_reads_an_empty_context() -> None:
    store = ReferenceRecordStore()
    specs = _toolset(store)
    run_id = _register(specs, _forwarded(), key="solo-run-0001")
    assert _invoke(specs, "read_predecessor_context", {"run_id": run_id}, _forwarded()) == {
        "run_id": run_id, "predecessor_run_id": None, "session_notes": [], "evidence": [],
        "next_after_note_id": None, "next_after_chain_seq": None,
    }


@pytest.mark.parametrize(
    "overrides",
    [{"workspace_id": OTHER_WORKSPACE}, {"repo_id": OTHER_REPO}],
    ids=["different-workspace", "different-repository"],
)
def test_a_successor_outside_the_predecessors_workspace_or_repository_is_refused(
    overrides: dict[str, str],
) -> None:
    store = ReferenceRecordStore()
    specs, predecessor_run = _predecessor_with_history(store)
    runs_before = dict(store._runs)

    failure = _refused(
        PREDECESSOR_NOT_ELIGIBLE, specs, "register_run",
        {**_MANIFEST, "idempotency_key": "succ-run-0001", "predecessor_run_id": predecessor_run},
        _forwarded(**SUCCESSOR, **overrides),
    )
    unknown = _refused(
        PREDECESSOR_NOT_ELIGIBLE, specs, "register_run",
        {**_MANIFEST, "idempotency_key": "succ-run-0002", "predecessor_run_id": "run_" + "0" * 26},
        _forwarded(**SUCCESSOR),
    )
    assert failure.message == unknown.message
    assert store._runs == runs_before


def test_a_successor_without_work_read_is_refused_before_the_registry() -> None:
    store = ReferenceRecordStore()
    specs, predecessor_run = _predecessor_with_history(store)
    runs_before = dict(store._runs)
    evidence_only = _forwarded(**SUCCESSOR, authorities=frozenset({"work:evidence"}))

    _refused(
        "authority-required", specs, "register_run",
        {**_MANIFEST, "idempotency_key": "succ-run-0001", "predecessor_run_id": predecessor_run},
        evidence_only,
    )
    assert store._runs == runs_before
    # A run with no predecessor still needs only work:evidence.
    _register(specs, evidence_only, key="solo-run-0001")


def test_the_successor_cannot_act_on_the_predecessors_run() -> None:
    """Continuation transfers context, not authority: the successor's
    writes and reads go through its own run, and the predecessor's run_id
    stays someone else's run."""

    store = ReferenceRecordStore()
    specs, predecessor_run = _predecessor_with_history(store)
    successor = _forwarded(**SUCCESSOR)
    _register(specs, successor, key="succ-run-0001", predecessor=predecessor_run)

    _refused(
        "run-not-found", specs, "write_session_note",
        {"run_id": predecessor_run, "note": "hijack", "idempotency_key": "note-key-0002"},
        successor,
    )
    _refused(
        "run-not-found", specs, "append_evidence",
        {
            "run_id": predecessor_run, "kind": "k", "ref": "r", "digest": "d",
            "collector": "c",
            "validity": {"basis": "indefinite", "valid_from": "2026-09-27T00:00:00Z"},
            "idempotency_key": "evidence-key-0002",
        },
        successor,
    )
    # Reading through the predecessor's handle is not a continuation either.
    _refused(
        "run-not-found", specs, "read_predecessor_context", {"run_id": predecessor_run}, successor
    )
    assert [note["note"] for note in store.notes[predecessor_run]] == ["stopped after step 3"]
    assert len(store.evidence[predecessor_run]) == 1


# -- over /mcp: the successor holds only its own grant ------------------------------


def _edge(keys: Any, store: ReferenceRecordStore) -> Any:
    context = ToolsetContext(
        env={}, work_source=ShellWorkSource(base_url="http://127.0.0.1:8080"), runs=store
    )
    record = build_toolset(context)
    effects = effect_tools._build(
        intent_store=InMemoryIntentStore(),
        runs=store,
        repository_policies={
            REPO_ID: RepositoryEffectPolicy(path_allowlist=frozenset({"docs/*"}))
        },
    )
    return edge_client(keys[0], FakeShell(), toolsets=(record, effects))


def _headers(keys: Any, *, subject: str, authorities: list[str], grant_id: str) -> dict[str, str]:
    return identity_headers(
        assertion(
            keys[1], subject=subject, authorities=authorities,
            client_id="claude-connector", grant_id=grant_id,
        )
    )


def _propose(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "item_id": 1,
        "repository": REPO_ID,
        "base_commit": "a" * 40,
        "title": "Fix the typo",
        "rationale": "A short rationale for the change.",
        "unified_diff": (
            "diff --git a/docs/readme.md b/docs/readme.md\n"
            "index 1111111..2222222 100644\n"
            "--- a/docs/readme.md\n"
            "+++ b/docs/readme.md\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
        "idempotency_key": "propose-key-0001",
    }


def _result(client: Any, headers: dict[str, str], tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return client.post(MCP_PATH, headers=headers, json=call(tool, arguments)).json()["result"]


def _error_code(result: dict[str, Any]) -> str | None:
    return result["structuredContent"]["error"]["code"] if result["isError"] else None


def test_over_mcp_the_successor_inherits_no_authority_from_the_predecessor(keys) -> None:
    store = ReferenceRecordStore()
    client = _edge(keys, store)
    predecessor_grant = ["work:read", "work:evidence", "effect:propose"]
    successor_grant = ["work:read", "work:evidence"]

    def predecessor() -> dict[str, str]:
        return _headers(keys, subject=SUBJECT, authorities=predecessor_grant, grant_id="grant-1")

    def successor(authorities: list[str] = successor_grant) -> dict[str, str]:
        return _headers(
            keys, subject=SUCCESSOR_SUBJECT, authorities=authorities, grant_id="grant-2"
        )

    registered = _result(
        client, predecessor(), "register_run", {**_MANIFEST, "idempotency_key": "pred-run-0001"}
    )
    predecessor_run = registered["structuredContent"]["run_id"]
    noted = _result(
        client, predecessor(), "write_session_note",
        {"run_id": predecessor_run, "note": "checkpoint", "idempotency_key": "note-key-0001"},
    )
    assert noted["isError"] is False
    proposed = _result(client, predecessor(), "propose_effect", _propose(predecessor_run))
    assert proposed["isError"] is False, proposed

    registered = _result(
        client, successor(), "register_run",
        {**_MANIFEST, "idempotency_key": "succ-run-0001", "predecessor_run_id": predecessor_run},
    )
    assert registered["isError"] is False, registered
    successor_run = registered["structuredContent"]["run_id"]

    context = _result(client, successor(), "read_predecessor_context", {"run_id": successor_run})
    assert context["isError"] is False, context
    assert context["structuredContent"]["predecessor_run_id"] == predecessor_run
    assert [n["note"] for n in context["structuredContent"]["session_notes"]] == ["checkpoint"]

    # The predecessor could propose effects; its successor cannot, on either run.
    for run_id in (successor_run, predecessor_run):
        assert _error_code(
            _result(client, successor(), "propose_effect", _propose(run_id))
        ) == "authority-required"
    # Even with its own effect:propose grant, the successor cannot borrow the
    # predecessor's run.
    own_grant = successor(["work:read", "work:evidence", "effect:propose"])
    assert _error_code(
        _result(client, own_grant, "propose_effect", _propose(predecessor_run))
    ) == "run-not-found"
    # Reading the context is itself work:read.
    assert _error_code(
        _result(
            client, successor(["work:evidence"]), "read_predecessor_context",
            {"run_id": successor_run},
        )
    ) == "authority-required"


# -- the durable store: served only when the owner advertises it -------------------


CONTEXT_OPERATION = "work.run.predecessor-context-v1"
#: What a sprintctl before 0.11.0 advertises for the record bucket.
OLD_OWNER_OPERATIONS = (
    "work.public.list-v1", "work.public.item-v1", "work.run.register-v1",
    "work.run.resolve-v1", "work.evidence.tail-v1", "work.evidence.append-v1",
    "work.session-note.write-v1",
)
NEW_OWNER_OPERATIONS = (*OLD_OWNER_OPERATIONS, CONTEXT_OPERATION)
PREDECESSOR_RUN = "run_" + "P" * 26
SUCCESSOR_RUN = "run_" + "S" * 26


_MESSAGES = {"predecessor-not-eligible": "that run cannot be continued by the caller"}


class FakeOwner:
    """The runtime shell in front of a sprintctl work adapter, for
    `SprintctlRecordStore`: a catalog (optionally failing first) and scripted
    answers per operation.  Records every invoke envelope."""

    def __init__(
        self,
        operations: tuple[str, ...],
        *,
        catalog_failures: int = 0,
        resolve_binding: dict[str, Any] | None = None,
        hang_catalog: bool = False,
        context_result: dict[str, Any] | None = None,
        register_refusal: str | None = None,
    ) -> None:
        self.operations = operations
        self.catalog_failures = catalog_failures
        self.hang_catalog = hang_catalog
        self.context_result = context_result
        self.register_refusal = register_refusal
        self.catalog_reads = 0
        self.invoked: list[dict[str, Any]] = []
        self.resolve_binding = resolve_binding

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/catalog/v1":
            self.catalog_reads += 1
            if self.hang_catalog:
                return self._hang()
            if self.catalog_failures:
                self.catalog_failures -= 1
                return httpx.Response(503, json={"error": "not ready"})
            return httpx.Response(
                200,
                json={"revision": "rev-1", "operations": [{"name": n} for n in self.operations]},
            )
        envelope = json.loads(request.content)
        self.invoked.append(envelope)
        operation = envelope["operation"]
        if operation not in self.operations:
            return self._error(operation, "unknown-operation", 404)
        if operation == "work.run.register-v1":
            if self.register_refusal is not None and "predecessor_run_id" in envelope["arguments"]:
                # sprintctl#114: one code and one message for unknown,
                # malformed and other-workspace predecessors alike.
                return self._error(operation, self.register_refusal, 422)
            run = {
                "run_id": SUCCESSOR_RUN, "harness_id": "h", "harness_build": "1",
                "model_id": "m", "recipe_id": "r", "observed_profile": OBSERVED_PROFILE,
                "grant_ids": [], "claim_ids": [],
            }
            return self._ok(operation, {"repo_id": REPO_ID, "run": run})
        if operation == "work.run.resolve-v1":
            if self.resolve_binding is None:
                return self._error(operation, "run-not-found", 404)
            return self._ok(
                operation, {"repo_id": REPO_ID, "run_id": envelope["arguments"]["run_id"],
                            **self.resolve_binding},
            )
        if operation == CONTEXT_OPERATION:
            if self.context_result is not None:
                return self._ok(operation, self.context_result)
            return self._ok(operation, {
                "repo_id": REPO_ID,
                "run_id": envelope["arguments"]["run_id"],
                "predecessor_run_id": PREDECESSOR_RUN,
                "session_notes": [
                    {"note_id": 1, "note": "stopped after step 3", "created_at": "2026-09-30T10:00:00Z"},
                ],
                "evidence": [{"item_id": "evi_1", "kind": "test-report", "chain_seq": 0}],
            })
        raise AssertionError(f"unexpected operation {operation}")

    @staticmethod
    async def _hang() -> httpx.Response:
        await asyncio.sleep(30)
        raise AssertionError("the catalog read was not bounded")

    @staticmethod
    def _ok(operation: str, result: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "accepted", "operation": operation, "result": result}
        )

    @staticmethod
    def _error(operation: str, code: str, status: int) -> httpx.Response:
        return httpx.Response(
            status,
            json={"status": "rejected", "operation": operation,
                  "error": {"code": code, "message": _MESSAGES.get(code, code)}},
        )


def _sprintctl_store(owner: FakeOwner) -> SprintctlRecordStore:
    return SprintctlRecordStore(
        base_url="http://127.0.0.1:8080", timeout=5.0, transport=httpx.MockTransport(owner)
    )


def _sprintctl_edge(keys: Any, owner: FakeOwner, store: SprintctlRecordStore | None = None) -> Any:
    context = ToolsetContext(
        env={}, work_source=ShellWorkSource(base_url="http://127.0.0.1:8080"),
        runs=store or _sprintctl_store(owner),
    )
    return edge_client(keys[0], FakeShell(), toolsets=(build_toolset(context),))


def _successor_headers(keys: Any, authorities: list[str] | None = None) -> Any:
    """A factory: every request carries a fresh assertion (replays are refused)."""
    return lambda: _headers(
        keys, subject=SUCCESSOR_SUBJECT,
        authorities=authorities or ["work:read", "work:evidence"], grant_id="grant-2",
    )


def _listed(client: Any, headers: Any) -> dict[str, Any]:
    tools = client.post(MCP_PATH, headers=headers(), json=rpc("tools/list")).json()["result"]["tools"]
    return {tool["name"]: tool for tool in tools}


def _successor_binding() -> dict[str, Any]:
    return {
        "principal_id": SUCCESSOR_PRINCIPAL, "workspace_id": WORKSPACE_ID,
        "client_id": "claude-connector", "grant_id": "grant-2",
    }


def test_against_an_older_work_adapter_continuation_is_not_listed(keys) -> None:
    """No regression: a sprintctl without the capability gets exactly the
    record tools it had, and a predecessor is refused before the owner."""
    owner = FakeOwner(OLD_OWNER_OPERATIONS)
    client = _sprintctl_edge(keys, owner)
    headers = _successor_headers(keys)
    listed = _listed(client, headers)
    assert "read_predecessor_context" not in listed
    assert {"register_run", "append_evidence", "write_session_note"} <= set(listed)
    assert "predecessor_run_id" not in listed["register_run"]["inputSchema"]["properties"]
    assert _error_code(
        _result(client, headers(), "read_predecessor_context", {"run_id": SUCCESSOR_RUN})
    ) == "unknown-tool"
    assert _error_code(
        _result(
            client, headers(), "register_run",
            {**_MANIFEST, "idempotency_key": "succ-run-0001", "predecessor_run_id": PREDECESSOR_RUN},
        )
    ) == "invalid-arguments"
    assert owner.invoked == []
    # Without a predecessor register_run works as before, and sends no
    # predecessor_run_id at all (the request digest sprintctl stores is unchanged).
    registered = _result(client, headers(), "register_run", {**_MANIFEST, "idempotency_key": "succ-run-0002"})
    assert registered["isError"] is False, registered
    assert "predecessor_run_id" not in owner.invoked[-1]["arguments"]
    # The catalog answer is kept: one read for all of the above.
    assert owner.catalog_reads == 1


def test_the_sprintctl_store_never_drops_a_predecessor_silently() -> None:
    owner = FakeOwner(OLD_OWNER_OPERATIONS)
    store = _sprintctl_store(owner)
    binding = RunBinding(principal_id=PREDECESSOR_PRINCIPAL, workspace_id=WORKSPACE_ID, repo_id=REPO_ID)

    async def attempts() -> list[str]:
        codes = []
        for attempt in (
            lambda: store.register(
                binding, idempotency_key="key-0001", forwarded=_forwarded(), manifest=_MANIFEST,
                predecessor_run_id=PREDECESSOR_RUN,
            ),
            lambda: store.read_predecessor_context(SUCCESSOR_RUN, forwarded=_forwarded()),
        ):
            try:
                await attempt()
            except ToolFailure as failure:
                codes.append(failure.code)
        return codes

    assert asyncio.run(attempts()) == [RECORD_OWNER_INCOMPATIBLE, RECORD_OWNER_INCOMPATIBLE]
    assert owner.invoked == []


def test_with_the_capability_the_sprintctl_store_serves_continuation(keys) -> None:
    owner = FakeOwner(NEW_OWNER_OPERATIONS, resolve_binding=_successor_binding())
    client = _sprintctl_edge(keys, owner)
    headers = _successor_headers(keys)
    listed = _listed(client, headers)
    assert listed["read_predecessor_context"]["annotations"]["readOnlyHint"] is True
    assert "predecessor_run_id" in listed["register_run"]["inputSchema"]["properties"]

    registered = _result(
        client, headers(), "register_run",
        {**_MANIFEST, "idempotency_key": "succ-run-0001", "predecessor_run_id": PREDECESSOR_RUN},
    )
    assert registered["isError"] is False, registered
    assert owner.invoked[-1]["operation"] == "work.run.register-v1"
    assert owner.invoked[-1]["arguments"]["predecessor_run_id"] == PREDECESSOR_RUN

    context = _result(client, headers(), "read_predecessor_context", {"run_id": SUCCESSOR_RUN})
    assert context["isError"] is False, context
    assert context["structuredContent"] == {
        "run_id": SUCCESSOR_RUN,
        "predecessor_run_id": PREDECESSOR_RUN,
        "session_notes": [
            {"note_id": 1, "note": "stopped after step 3", "created_at": "2026-09-30T10:00:00Z"},
        ],
        "evidence": [{"item_id": "evi_1", "kind": "test-report", "chain_seq": 0}],
        "next_after_note_id": None,
        "next_after_chain_seq": None,
    }
    # The caller's own run is resolved first, then read through that run only.
    assert [e["operation"] for e in owner.invoked[-2:]] == ["work.run.resolve-v1", CONTEXT_OPERATION]
    assert owner.invoked[-1]["arguments"] == {"run_id": SUCCESSOR_RUN}


@pytest.mark.parametrize("binding", ["unknown", "someone-else"])
def test_over_the_sprintctl_store_only_the_caller_s_own_run_is_read(keys, binding) -> None:
    other = {**_successor_binding(), "principal_id": PREDECESSOR_PRINCIPAL, "grant_id": "grant-1"}
    owner = FakeOwner(
        NEW_OWNER_OPERATIONS, resolve_binding=None if binding == "unknown" else other
    )
    client = _sprintctl_edge(keys, owner)
    assert _error_code(
        _result(client, _successor_headers(keys)(), "read_predecessor_context", {"run_id": SUCCESSOR_RUN})
    ) == "run-not-found"
    assert CONTEXT_OPERATION not in [e["operation"] for e in owner.invoked]


def test_reading_the_context_needs_work_read(keys) -> None:
    owner = FakeOwner(NEW_OWNER_OPERATIONS, resolve_binding=_successor_binding())
    client = _sprintctl_edge(keys, owner)
    headers = _successor_headers(keys, ["work:evidence"])
    assert "read_predecessor_context" not in _listed(client, headers)
    assert _error_code(
        _result(client, headers(), "read_predecessor_context", {"run_id": SUCCESSOR_RUN})
    ) == "authority-required"
    assert owner.invoked == []


def test_an_unreadable_catalog_hides_continuation_and_backs_off(keys) -> None:
    owner = FakeOwner(NEW_OWNER_OPERATIONS, catalog_failures=1)
    store = _sprintctl_store(owner)
    now = [100.0]
    store.clock = lambda: now[0]
    client = _sprintctl_edge(keys, owner, store)
    headers = _successor_headers(keys)
    first = _listed(client, headers)
    assert "read_predecessor_context" not in first
    assert "predecessor_run_id" not in first["register_run"]["inputSchema"]["properties"]
    # One read for the whole list (both describes), none during the backoff.
    assert owner.catalog_reads == 1
    assert "read_predecessor_context" not in _listed(client, headers)
    assert owner.catalog_reads == 1
    now[0] += store.catalog_retry + 0.1
    assert "read_predecessor_context" in _listed(client, headers)
    assert "read_predecessor_context" in _listed(client, headers)
    # Kept once read.
    assert owner.catalog_reads == 2


def test_a_hung_catalog_does_not_stall_tools_list(keys) -> None:
    import time

    owner = FakeOwner(NEW_OWNER_OPERATIONS, hang_catalog=True)
    store = _sprintctl_store(owner)
    store.catalog_timeout = 0.2
    client = _sprintctl_edge(keys, owner, store)
    headers = _successor_headers(keys)
    started = time.monotonic()
    listed = _listed(client, headers)
    elapsed = time.monotonic() - started
    assert "read_predecessor_context" not in listed
    # One bounded read, not one upstream timeout per describe.
    assert elapsed < 1.5, elapsed
    assert owner.catalog_reads == 1
    # register_run's own check shares the backoff: no second read.
    started = time.monotonic()
    assert _error_code(
        _result(
            client, headers(), "register_run",
            {**_MANIFEST, "idempotency_key": "succ-run-0001", "predecessor_run_id": PREDECESSOR_RUN},
        )
    ) == "invalid-arguments"
    assert time.monotonic() - started < 1.5
    assert owner.catalog_reads == 1
    assert owner.invoked == []


def test_concurrent_callers_share_one_catalog_read() -> None:
    owner = FakeOwner(NEW_OWNER_OPERATIONS)
    store = _sprintctl_store(owner)

    async def many() -> list[bool]:
        return list(await asyncio.gather(*(store.continuation_available() for _ in range(5))))

    assert asyncio.run(many()) == [True] * 5
    assert owner.catalog_reads == 1


class _GatedOwner(FakeOwner):
    """A catalog that answers only once the test opens the gate."""

    def __init__(self) -> None:
        super().__init__(NEW_OWNER_OPERATIONS)
        self.gate: asyncio.Event | None = None

    def __call__(self, request: httpx.Request) -> Any:
        if request.url.path != "/api/catalog/v1":
            return super().__call__(request)
        self.catalog_reads += 1

        async def answer() -> httpx.Response:
            assert self.gate is not None
            await self.gate.wait()
            return httpx.Response(
                200,
                json={"revision": "rev-1", "operations": [{"name": n} for n in self.operations]},
            )

        return answer()


def test_cancelling_a_waiter_leaves_the_read_and_the_others_intact() -> None:
    owner = _GatedOwner()
    store = _sprintctl_store(owner)

    async def scenario() -> list[Any]:
        owner.gate = asyncio.Event()
        prober = asyncio.create_task(store.continuation_available())
        await asyncio.sleep(0)
        waiters = [asyncio.create_task(store.continuation_available()) for _ in range(3)]
        await asyncio.sleep(0.01)
        waiters[1].cancel()
        await asyncio.sleep(0)
        owner.gate.set()
        return await asyncio.gather(prober, *waiters, return_exceptions=True)

    results = asyncio.run(scenario())
    assert results[0] is True and results[1] is True and results[3] is True
    assert isinstance(results[2], asyncio.CancelledError)
    assert owner.catalog_reads == 1


def test_cancelling_the_prober_neither_fails_the_read_nor_starts_a_backoff() -> None:
    owner = _GatedOwner()
    store = _sprintctl_store(owner)

    async def scenario() -> tuple[Any, Any, bool]:
        owner.gate = asyncio.Event()
        prober = asyncio.create_task(store.continuation_available())
        await asyncio.sleep(0.01)
        waiter = asyncio.create_task(store.continuation_available())
        await asyncio.sleep(0)
        prober.cancel()  # the request that started the read disconnects
        await asyncio.sleep(0)
        owner.gate.set()
        results = await asyncio.gather(prober, waiter, return_exceptions=True)
        fresh = await store.continuation_available()
        return results[0], results[1], fresh

    cancelled, waiter, fresh = asyncio.run(scenario())
    assert isinstance(cancelled, asyncio.CancelledError)
    assert waiter is True
    assert fresh is True
    assert store._retry_at == 0.0  # no backoff was started
    assert owner.catalog_reads == 1


@pytest.mark.parametrize(
    "malformed",
    [
        {"run_id": PREDECESSOR_RUN, "predecessor_run_id": None, "session_notes": [], "evidence": []},
        {"run_id": SUCCESSOR_RUN, "predecessor_run_id": "run_bad", "session_notes": [], "evidence": []},
        {"run_id": SUCCESSOR_RUN, "predecessor_run_id": None, "session_notes": {}, "evidence": []},
        {"run_id": SUCCESSOR_RUN, "predecessor_run_id": None, "session_notes": [], "evidence": ["x"]},
        {"run_id": SUCCESSOR_RUN, "predecessor_run_id": None, "session_notes": [], "evidence": [],
         "next_after_note_id": "7"},
        {"run_id": SUCCESSOR_RUN, "predecessor_run_id": None, "session_notes": [], "evidence": [],
         "next_after_chain_seq": -1},
    ],
    ids=[
        "other-run", "bad-predecessor", "notes-not-a-list", "evidence-not-objects",
        "cursor-not-an-integer", "negative-cursor",
    ],
)
def test_a_malformed_context_result_is_refused(keys, malformed) -> None:
    owner = FakeOwner(
        NEW_OWNER_OPERATIONS, resolve_binding=_successor_binding(), context_result=malformed
    )
    client = _sprintctl_edge(keys, owner)
    assert _error_code(
        _result(client, _successor_headers(keys)(), "read_predecessor_context", {"run_id": SUCCESSOR_RUN})
    ) == "record-shell-unavailable"


def test_paging_arguments_reach_the_owner_and_its_cursors_come_back(keys) -> None:
    page = {
        "run_id": SUCCESSOR_RUN, "predecessor_run_id": PREDECESSOR_RUN,
        "session_notes": [{"note_id": 8, "note": "n", "created_at": "2026-09-30T10:00:00Z"}],
        "evidence": [{"item_id": "evi_4", "chain_seq": 4}],
        "next_after_note_id": 8, "next_after_chain_seq": None,
    }
    owner = FakeOwner(
        NEW_OWNER_OPERATIONS, resolve_binding=_successor_binding(), context_result=page
    )
    client = _sprintctl_edge(keys, owner)
    listed = _listed(client, _successor_headers(keys))
    properties = listed["read_predecessor_context"]["inputSchema"]["properties"]
    assert properties["limit"]["maximum"] == 500
    result = _result(
        client, _successor_headers(keys)(), "read_predecessor_context",
        {"run_id": SUCCESSOR_RUN, "limit": 1, "after_note_id": 7, "after_chain_seq": 3},
    )
    assert result["isError"] is False, result
    assert owner.invoked[-1]["arguments"] == {
        "run_id": SUCCESSOR_RUN, "limit": 1, "after_note_id": 7, "after_chain_seq": 3,
    }
    assert result["structuredContent"]["next_after_note_id"] == 8
    assert result["structuredContent"]["next_after_chain_seq"] is None
    # Without paging arguments only run_id is sent.
    _result(client, _successor_headers(keys)(), "read_predecessor_context", {"run_id": SUCCESSOR_RUN})
    assert owner.invoked[-1]["arguments"] == {"run_id": SUCCESSOR_RUN}


@pytest.mark.parametrize("arguments", [
    {"limit": 0}, {"limit": 501}, {"limit": True}, {"after_note_id": -1},
    {"after_chain_seq": "3"}, {"cursor": 1},
])
def test_out_of_range_paging_is_refused_before_the_owner(keys, arguments) -> None:
    owner = FakeOwner(NEW_OWNER_OPERATIONS, resolve_binding=_successor_binding())
    client = _sprintctl_edge(keys, owner)
    assert _error_code(
        _result(
            client, _successor_headers(keys)(), "read_predecessor_context",
            {"run_id": SUCCESSOR_RUN, **arguments},
        )
    ) == "invalid-arguments"
    assert owner.invoked == []


def _context_page(notes: list[int], seqs: list[int], **cursors: Any) -> dict[str, Any]:
    return {
        "run_id": SUCCESSOR_RUN, "predecessor_run_id": PREDECESSOR_RUN,
        "session_notes": [{"note_id": n, "note": "n", "created_at": "t"} for n in notes],
        "evidence": [{"item_id": f"evi_{q}", "chain_seq": q} for q in seqs],
        "next_after_note_id": None, "next_after_chain_seq": None, **cursors,
    }


@pytest.mark.parametrize(
    ("arguments", "page"),
    [
        # Over-long: more entries than the limit sent, or than the default 100.
        ({"limit": 2}, _context_page([1, 2, 3], [])),
        ({}, _context_page(list(range(1, 102)), [])),
        # A non-advancing cursor: equal to the after_* sent.
        ({"limit": 1, "after_note_id": 5}, _context_page([], [], next_after_note_id=5)),
        # A regressing cursor: below the last entry returned.
        ({"limit": 2}, _context_page([], [0, 1], next_after_chain_seq=0)),
        # Entries not past the cursor sent, or out of order.
        ({"after_chain_seq": 3}, _context_page([], [3])),
        ({}, _context_page([2, 1], [])),
        # A cursor on a page that is not full.
        ({"limit": 5}, _context_page([1, 2], [], next_after_note_id=2)),
    ],
    ids=[
        "over-limit", "over-default", "non-advancing", "regressing",
        "not-past-cursor", "out-of-order", "cursor-on-short-page",
    ],
)
def test_an_inconsistent_page_fails_closed(keys, arguments, page) -> None:
    owner = FakeOwner(NEW_OWNER_OPERATIONS, resolve_binding=_successor_binding(), context_result=page)
    client = _sprintctl_edge(keys, owner)
    assert _error_code(
        _result(
            client, _successor_headers(keys)(), "read_predecessor_context",
            {"run_id": SUCCESSOR_RUN, **arguments},
        )
    ) == "record-shell-unavailable"


def test_a_consistent_full_page_with_cursors_passes(keys) -> None:
    page = _context_page([3, 4], [0, 1], next_after_note_id=4, next_after_chain_seq=1)
    owner = FakeOwner(NEW_OWNER_OPERATIONS, resolve_binding=_successor_binding(), context_result=page)
    client = _sprintctl_edge(keys, owner)
    result = _result(
        client, _successor_headers(keys)(), "read_predecessor_context",
        {"run_id": SUCCESSOR_RUN, "limit": 2, "after_note_id": 2},
    )
    assert result["isError"] is False, result
    assert result["structuredContent"]["next_after_note_id"] == 4


def test_an_unpaged_store_refuses_paging_rather_than_ignoring_it() -> None:
    store = ReferenceRecordStore()
    specs = _toolset(store)
    run_id = _register(specs, _forwarded(), key="solo-run-0002")
    _refused(
        "invalid-arguments", specs, "read_predecessor_context",
        {"run_id": run_id, "after_note_id": 1}, _forwarded(),
    )


def test_naming_a_predecessor_needs_work_read_with_the_capability_on(keys) -> None:
    owner = FakeOwner(NEW_OWNER_OPERATIONS)
    client = _sprintctl_edge(keys, owner)
    headers = _successor_headers(keys, ["work:evidence"])
    assert _error_code(
        _result(
            client, headers(), "register_run",
            {**_MANIFEST, "idempotency_key": "succ-run-0001", "predecessor_run_id": PREDECESSOR_RUN},
        )
    ) == "authority-required"
    assert owner.invoked == []


def test_the_owner_s_predecessor_refusal_reaches_the_caller_uniformly(keys) -> None:
    """Unknown and ineligible (another workspace's) predecessors are one
    answer from sprintctl (#114), passed through unchanged."""
    owner = FakeOwner(NEW_OWNER_OPERATIONS, register_refusal="predecessor-not-eligible")
    client = _sprintctl_edge(keys, owner)
    headers = _successor_headers(keys)
    answers = []
    for index, predecessor in enumerate(("run_" + "0" * 26, PREDECESSOR_RUN)):
        result = _result(
            client, headers(), "register_run",
            {**_MANIFEST, "idempotency_key": f"succ-run-000{index}", "predecessor_run_id": predecessor},
        )
        assert result["isError"] is True
        answers.append(result["structuredContent"]["error"])
    assert answers[0] == answers[1] == {
        "code": "predecessor-not-eligible",
        "message": "that run cannot be continued by the caller",
    }


def test_the_advertised_schema_carries_the_predecessor_only_with_continuation() -> None:
    specs = _toolset(ReferenceRecordStore())
    schema = specs["register_run"].definition["inputSchema"]
    assert schema["properties"]["predecessor_run_id"]["pattern"]
    assert "predecessor_run_id" not in schema["required"]
    assert specs["read_predecessor_context"].bucket == "read"
    assert specs["read_predecessor_context"].definition["annotations"]["readOnlyHint"] is True
    with pytest.raises(ToolFailure) as refused:
        specs["register_run"].parse(
            {**_MANIFEST, "idempotency_key": "key-0001", "predecessor_run_id": "run_bad"}
        )
    assert refused.value.code == "invalid-arguments"

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
from dataclasses import replace
from typing import Any

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

    async def read_predecessor_context(self, run_id: str, *, forwarded: Any) -> dict[str, Any]:
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


# -- the durable store: not served until the owner records predecessors ------------


def test_the_sprintctl_store_does_not_advertise_continuation() -> None:
    store = SprintctlRecordStore(base_url="http://127.0.0.1:8080", timeout=5.0)
    specs = _toolset(store)
    assert set(specs) == {"register_run", "append_evidence", "write_session_note"}
    schema = specs["register_run"].definition["inputSchema"]
    assert "predecessor_run_id" not in schema["properties"]
    with pytest.raises(ToolFailure) as refused:
        specs["register_run"].parse(
            {**_MANIFEST, "idempotency_key": "key-0001", "predecessor_run_id": "run_" + "0" * 26}
        )
    assert refused.value.code == "invalid-arguments"


def test_the_sprintctl_store_never_drops_a_predecessor_silently() -> None:
    def no_call(request: Any) -> Any:
        raise AssertionError("the owner must not be called")

    import httpx

    store = SprintctlRecordStore(
        base_url="http://127.0.0.1:8080", timeout=5.0, transport=httpx.MockTransport(no_call)
    )
    binding = RunBinding(principal_id=PREDECESSOR_PRINCIPAL, workspace_id=WORKSPACE_ID, repo_id=REPO_ID)
    for attempt in (
        store.register(
            binding, idempotency_key="key-0001", forwarded=_forwarded(), manifest=_MANIFEST,
            predecessor_run_id="run_" + "0" * 26,
        ),
        store.read_predecessor_context("run_" + "0" * 26, forwarded=_forwarded()),
    ):
        with pytest.raises(ToolFailure) as refused:
            asyncio.run(attempt)
        assert refused.value.code == RECORD_OWNER_INCOMPATIBLE


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

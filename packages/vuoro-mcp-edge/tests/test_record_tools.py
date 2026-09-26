"""agentops#2466 (E2): register_run, append_evidence, write_session_note --
the record bucket's tools, and the durable SprintctlRecordStore behind them.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from vuoro_service.identity import Identity
from vuoro_mcp_edge.record_tools import (
    CHAIN_ATTEMPTS,
    RECORD_OWNER_INCOMPATIBLE,
    RecordShellClient,
    SprintctlRecordStore,
    build_run_registry,
    build_toolset,
)
from vuoro_mcp_edge.runs import RunBinding, UnavailableRunRegistry
from vuoro_mcp_edge.toolsets import ToolFailure, ToolsetContext
from vuoro_mcp_edge.work_source import ForwardedIdentity, ShellWorkSource

REQUEST_ID = "01K33333333333333333333333"
REPO_ID = "repo-a"
PRINCIPAL_ID = "vuoro-cloud-control:github:123:0"
WORKSPACE_ID = "01K11111111111111111111111"
RUN_ID = "run_" + "0" * 24 + "AA"
assert len(RUN_ID) == 30  # "run_" + 26 crockford chars


def _forwarded(
    *,
    principal_id: str = PRINCIPAL_ID,
    workspace_id: str = WORKSPACE_ID,
    client_id: str | None = None,
    grant_id: str | None = None,
) -> ForwardedIdentity:
    identity = Identity(
        actor="github:123",
        environment="vuoro-dev",
        authorities=frozenset({"work:evidence"}),
        repo_ids=frozenset({REPO_ID}),
        workspace_id=workspace_id,
        principal_id=principal_id,
        client_id=client_id,
        grant_id=grant_id,
    )
    return ForwardedIdentity(
        assertion="a.b.c", request_id=REQUEST_ID, repo_id=REPO_ID, identity=identity
    )


def _context(runs: Any) -> ToolsetContext:
    return ToolsetContext(
        env={}, work_source=ShellWorkSource(base_url="http://127.0.0.1:8080"), runs=runs
    )


def _accepted(operation: str, result: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "schema_version": "invocation-result/v1",
            "request_id": REQUEST_ID,
            "operation": operation,
            "catalog_revision": "rev-1",
            "status": "accepted",
            "result": result,
            "error": None,
        },
    )


def _rejected(operation: str, code: str, message: str = "rejected", status: int = 409) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "schema_version": "invocation-result/v1",
            "request_id": REQUEST_ID,
            "operation": operation,
            "catalog_revision": "rev-1",
            "status": "rejected",
            "result": None,
            "error": {"code": code, "message": message},
        },
    )


class _FakeShell:
    """A minimal ``/api/invoke/v1`` handler: canned responses, in order."""

    def __init__(self, *responses: httpx.Response) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/invoke/v1"
        self.requests.append(json.loads(request.content))
        if not self._responses:
            raise AssertionError("unexpected extra request")
        return self._responses.pop(0)


def _store(shell: _FakeShell) -> SprintctlRecordStore:
    return SprintctlRecordStore(
        base_url="http://127.0.0.1:8080", timeout=5.0, transport=httpx.MockTransport(shell)
    )


def _run(coro):
    return asyncio.run(coro)


OBSERVED_PROFILE = {"instruction_digest": "sha256:" + "a" * 64, "skill_digests": []}


def _run_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "run_id": RUN_ID,
        "principal_id": PRINCIPAL_ID,
        "workspace_id": WORKSPACE_ID,
        "harness_id": "claude-code",
        "harness_build": "1.0.0",
        "model_id": "claude-sonnet-5",
        "recipe_id": "recipe-1",
        "observed_profile": OBSERVED_PROFILE,
        "grant_ids": [],
        "claim_ids": [],
        "created_at": "2026-09-26T00:00:00Z",
    }
    row.update(overrides)
    return row


class TestBuildToolsetGate:
    def test_returns_none_for_the_unavailable_placeholder(self) -> None:
        assert build_toolset(_context(UnavailableRunRegistry())) is None

    def test_returns_tools_for_a_durable_store(self) -> None:
        toolset = build_toolset(_context(_store(_FakeShell())))
        assert toolset is not None
        assert [spec.name for spec in toolset.tools] == [
            "register_run", "append_evidence", "write_session_note",
        ]
        assert all(spec.bucket == "record" for spec in toolset.tools)


class TestBuildRunRegistry:
    def test_default_upstream(self) -> None:
        registry = build_run_registry({})
        assert isinstance(registry, SprintctlRecordStore)

    def test_malformed_timeout_falls_back(self) -> None:
        # Does not raise: composition.py already validated this before
        # constructing ShellWorkSource; this function has no one to raise to.
        build_run_registry({"VUORO_MCP_UPSTREAM_TIMEOUT_SECONDS": "not-a-number"})
        build_run_registry({"VUORO_MCP_UPSTREAM_TIMEOUT_SECONDS": "999"})


class TestRegisterRun:
    def test_mints_a_run_bound_to_the_caller(self) -> None:
        shell = _FakeShell(_accepted("work.run.register-v1", {"repo_id": REPO_ID, "run": _run_row()}))
        store = _store(shell)
        toolset = build_toolset(_context(store))
        spec = next(t for t in toolset.tools if t.name == "register_run")
        parsed = spec.parse(
            {
                "harness_id": "claude-code", "harness_build": "1.0.0", "model_id": "claude-sonnet-5",
                "recipe_id": "recipe-1", "observed_profile": OBSERVED_PROFILE,
                "idempotency_key": "register-key-0001",
            }
        )
        result = _run(spec.run(parsed, _forwarded()))
        assert result == {"run_id": RUN_ID}
        sent = shell.requests[0]
        assert sent["operation"] == "work.run.register-v1"
        assert sent["arguments"]["idempotency_key"] == "register-key-0001"
        assert sent["arguments"]["observed_profile"] == OBSERVED_PROFILE

    def test_an_invalid_run_id_from_sprintctl_is_a_tool_failure(self) -> None:
        shell = _FakeShell(
            _accepted("work.run.register-v1", {"repo_id": REPO_ID, "run": _run_row(run_id="not-a-run-id")})
        )
        store = _store(shell)
        with pytest.raises(ToolFailure) as excinfo:
            _run(
                store.register(
                    RunBinding(principal_id=PRINCIPAL_ID, workspace_id=WORKSPACE_ID, repo_id=REPO_ID),
                    idempotency_key="register-key-0002",
                    forwarded=_forwarded(),
                    manifest={
                        "harness_id": "claude-code", "harness_build": "1.0.0",
                        "model_id": "claude-sonnet-5", "recipe_id": "recipe-1",
                        "observed_profile": OBSERVED_PROFILE,
                    },
                )
            )
        assert excinfo.value.code == "record-shell-unavailable"

    def test_sprintctl_s_idempotency_conflict_surfaces_as_a_tool_failure(self) -> None:
        shell = _FakeShell(_rejected("work.run.register-v1", "idempotency-conflict"))
        toolset = build_toolset(_context(_store(shell)))
        spec = next(t for t in toolset.tools if t.name == "register_run")
        parsed = spec.parse(
            {
                "harness_id": "claude-code", "harness_build": "1.0.0", "model_id": "claude-sonnet-5",
                "recipe_id": "recipe-1", "observed_profile": OBSERVED_PROFILE,
                "idempotency_key": "register-key-0003",
            }
        )
        with pytest.raises(ToolFailure) as excinfo:
            _run(spec.run(parsed, _forwarded()))
        assert excinfo.value.code == "idempotency-conflict"

    @pytest.mark.parametrize(
        "bad_arguments",
        [
            {},
            {"harness_id": ""},
            {"harness_id": "x", "harness_build": "1", "model_id": "m", "recipe_id": "r",
             "observed_profile": {}, "idempotency_key": "short"},
            {"harness_id": "x", "harness_build": "1", "model_id": "m", "recipe_id": "r",
             "observed_profile": {"skill_digests": []}, "idempotency_key": "register-key-0004"},
            {"harness_id": "x", "harness_build": "1", "model_id": "m", "recipe_id": "r",
             "observed_profile": {"instruction_digest": "d", "skill_digests": [{"skill_id": "a"}]},
             "idempotency_key": "register-key-0004"},
            {"harness_id": "x", "harness_build": "1", "model_id": "m", "recipe_id": "r",
             "observed_profile": {"instruction_digest": "d", "skill_digests": []},
             "idempotency_key": "register-key-0004", "unexpected": "x"},
        ],
    )
    def test_bad_arguments_are_refused_before_any_call(self, bad_arguments: dict[str, Any]) -> None:
        shell = _FakeShell()  # any use would raise: no responses queued
        toolset = build_toolset(_context(_store(shell)))
        spec = next(t for t in toolset.tools if t.name == "register_run")
        with pytest.raises(ToolFailure) as excinfo:
            spec.parse(bad_arguments)
        assert excinfo.value.code == "invalid-arguments"
        assert shell.requests == []


class TestResolveRun:
    def test_resolves_the_matching_binding(self) -> None:
        shell = _FakeShell(
            _accepted(
                "work.run.resolve-v1",
                {
                    "repo_id": REPO_ID, "run_id": RUN_ID,
                    "principal_id": PRINCIPAL_ID, "workspace_id": WORKSPACE_ID,
                },
            )
        )
        store = _store(shell)
        caller = RunBinding(principal_id=PRINCIPAL_ID, workspace_id=WORKSPACE_ID, repo_id=REPO_ID)
        resolved = _run(store.resolve(RUN_ID, caller, forwarded=_forwarded()))
        assert resolved == caller

    def test_a_malformed_run_id_is_run_not_found_without_a_call(self) -> None:
        shell = _FakeShell()
        store = _store(shell)
        caller = RunBinding(principal_id=PRINCIPAL_ID, workspace_id=WORKSPACE_ID, repo_id=REPO_ID)
        with pytest.raises(ToolFailure) as excinfo:
            _run(store.resolve("not-a-run-id", caller, forwarded=_forwarded()))
        assert excinfo.value.code == "run-not-found"
        assert shell.requests == []

    def test_sprintctl_s_run_not_found_propagates(self) -> None:
        shell = _FakeShell(_rejected("work.run.resolve-v1", "run-not-found", status=404))
        store = _store(shell)
        caller = RunBinding(principal_id=PRINCIPAL_ID, workspace_id=WORKSPACE_ID, repo_id=REPO_ID)
        with pytest.raises(ToolFailure) as excinfo:
            _run(store.resolve(RUN_ID, caller, forwarded=_forwarded()))
        assert excinfo.value.code == "run-not-found"

    def test_a_mismatched_binding_in_the_response_is_refused(self) -> None:
        """Defense in depth: even if the shell answered with someone else's
        binding, this store never trusts it over the caller's own."""
        shell = _FakeShell(
            _accepted(
                "work.run.resolve-v1",
                {
                    "repo_id": REPO_ID, "run_id": RUN_ID,
                    "principal_id": "someone-else:9:0", "workspace_id": WORKSPACE_ID,
                },
            )
        )
        store = _store(shell)
        caller = RunBinding(principal_id=PRINCIPAL_ID, workspace_id=WORKSPACE_ID, repo_id=REPO_ID)
        with pytest.raises(ToolFailure) as excinfo:
            _run(store.resolve(RUN_ID, caller, forwarded=_forwarded()))
        assert excinfo.value.code == "run-not-found"

    def test_resolves_a_run_bound_to_the_same_client_and_grant(self) -> None:
        shell = _FakeShell(_accepted("work.run.resolve-v1", _resolved(client_id="c1", grant_id="g1")))
        caller = RunBinding(
            principal_id=PRINCIPAL_ID, workspace_id=WORKSPACE_ID, repo_id=REPO_ID,
            client_id="c1", grant_id="g1",
        )
        resolved = _run(_store(shell).resolve(RUN_ID, caller, forwarded=_forwarded()))
        assert resolved == caller

    @pytest.mark.parametrize(
        ("run_owner", "caller"),
        [
            ({"client_id": "c1", "grant_id": "g1"}, {"client_id": "c2", "grant_id": "g1"}),
            ({"client_id": "c1", "grant_id": "g1"}, {"client_id": "c1", "grant_id": "g2"}),
            ({"client_id": "c1", "grant_id": "g1"}, {}),
            ({}, {"client_id": "c1", "grant_id": "g1"}),
        ],
    )
    def test_a_different_client_or_grant_is_run_not_found(
        self, run_owner: dict[str, str], caller: dict[str, str]
    ) -> None:
        """Same principal, workspace and repository, but the run was minted
        under another OAuth client or grant (or none): not the caller's."""
        shell = _FakeShell(_accepted("work.run.resolve-v1", _resolved(**run_owner)))
        binding = RunBinding(
            principal_id=PRINCIPAL_ID, workspace_id=WORKSPACE_ID, repo_id=REPO_ID, **caller
        )
        with pytest.raises(ToolFailure) as excinfo:
            _run(_store(shell).resolve(RUN_ID, binding, forwarded=_forwarded()))
        assert (excinfo.value.code, excinfo.value.message) == (
            "run-not-found", "no run with that id belongs to the caller",
        )

    def test_the_tools_bind_the_asserted_client_and_grant(self) -> None:
        """binding_for carries the assertion's client and grant into resolve:
        a run the owner reports under another grant is refused."""
        shell = _FakeShell(_accepted("work.run.resolve-v1", _resolved(client_id="c1", grant_id="g1")))
        toolset = build_toolset(_context(_store(shell)))
        spec = next(t for t in toolset.tools if t.name == "write_session_note")
        parsed = spec.parse({"run_id": RUN_ID, "note": "hi", "idempotency_key": "note-key-0009"})
        with pytest.raises(ToolFailure) as excinfo:
            _run(spec.run(parsed, _forwarded(client_id="c1", grant_id="g2")))
        assert excinfo.value.code == "run-not-found"
        assert len(shell.requests) == 1  # never reached the note write


def _resolved(**binding: str) -> dict[str, Any]:
    return {
        "repo_id": REPO_ID, "run_id": RUN_ID,
        "principal_id": PRINCIPAL_ID, "workspace_id": WORKSPACE_ID, **binding,
    }


def _tail(seq: int) -> dict[str, Any]:
    return {
        "repo_id": REPO_ID, "run_id": RUN_ID,
        "item": {
            "item_id": f"evi_{seq}", "kind": "test", "ref": f"ref-{seq}",
            "digest": "sha256:" + "c" * 64, "collector": "tester", "validity": VALIDITY,
            "claims": [], "provenance": {}, "chain_seq": seq, "chain_prev_digest": None,
        },
    }


def _appended(seq: int) -> dict[str, Any]:
    return {
        "repo_id": REPO_ID, "run_id": RUN_ID,
        "item": {**_tail(seq)["item"], "item_id": "evi_new"},
    }


APPEND_ARGUMENTS = {
    "run_id": RUN_ID, "kind": "test", "ref": "ref-x", "digest": "sha256:" + "d" * 64,
    "collector": "tester", "idempotency_key": "evidence-key-race",
}


VALIDITY = {"basis": "indefinite", "valid_from": "2026-09-26T00:00:00Z", "valid_until": None, "component_digests": {}}


class TestAppendEvidence:
    def test_first_append_starts_a_fresh_chain(self) -> None:
        shell = _FakeShell(
            _accepted(
                "work.run.resolve-v1",
                {"repo_id": REPO_ID, "run_id": RUN_ID, "principal_id": PRINCIPAL_ID, "workspace_id": WORKSPACE_ID},
            ),
            _accepted("work.evidence.tail-v1", {"repo_id": REPO_ID, "run_id": RUN_ID, "item": None}),
            _accepted(
                "work.evidence.append-v1",
                {
                    "repo_id": REPO_ID, "run_id": RUN_ID,
                    "item": {
                        "item_id": "evi_1", "kind": "test", "ref": "ref-1", "digest": "sha256:" + "b" * 64,
                        "collector": "tester", "validity": VALIDITY, "claims": [], "provenance": {},
                        "chain_seq": 0, "chain_prev_digest": None,
                    },
                },
            ),
        )
        toolset = build_toolset(_context(_store(shell)))
        spec = next(t for t in toolset.tools if t.name == "append_evidence")
        parsed = spec.parse(
            {
                "run_id": RUN_ID, "kind": "test", "ref": "ref-1", "digest": "sha256:" + "b" * 64,
                "collector": "tester", "validity": VALIDITY, "idempotency_key": "evidence-key-0001",
            }
        )
        result = _run(spec.run(parsed, _forwarded()))
        assert result["chain_seq"] == 0
        assert result["chain_prev_digest"] is None
        append_request = shell.requests[2]
        assert append_request["arguments"]["chain_seq"] == 0
        assert append_request["arguments"]["chain_prev_digest"] is None
        # item_id is derived here, not taken from the caller.
        assert append_request["arguments"]["item_id"]

    def test_a_second_append_extends_the_observed_tail(self) -> None:
        tail_digest = "sha256:" + "c" * 64
        shell = _FakeShell(
            _accepted(
                "work.run.resolve-v1",
                {"repo_id": REPO_ID, "run_id": RUN_ID, "principal_id": PRINCIPAL_ID, "workspace_id": WORKSPACE_ID},
            ),
            _accepted(
                "work.evidence.tail-v1",
                {
                    "repo_id": REPO_ID, "run_id": RUN_ID,
                    "item": {
                        "item_id": "evi_1", "kind": "test", "ref": "ref-1", "digest": tail_digest,
                        "collector": "tester", "validity": VALIDITY, "claims": [], "provenance": {},
                        "chain_seq": 0, "chain_prev_digest": None,
                    },
                },
            ),
            _accepted(
                "work.evidence.append-v1",
                {
                    "repo_id": REPO_ID, "run_id": RUN_ID,
                    "item": {
                        "item_id": "evi_2", "kind": "test", "ref": "ref-2", "digest": "sha256:" + "d" * 64,
                        "collector": "tester", "validity": VALIDITY, "claims": [], "provenance": {},
                        "chain_seq": 1, "chain_prev_digest": "will-be-overwritten-by-assertion-below",
                    },
                },
            ),
        )
        toolset = build_toolset(_context(_store(shell)))
        spec = next(t for t in toolset.tools if t.name == "append_evidence")
        parsed = spec.parse(
            {
                "run_id": RUN_ID, "kind": "test", "ref": "ref-2", "digest": "sha256:" + "d" * 64,
                "collector": "tester", "validity": VALIDITY, "idempotency_key": "evidence-key-0002",
            }
        )
        _run(spec.run(parsed, _forwarded()))
        append_request = shell.requests[2]
        assert append_request["arguments"]["chain_seq"] == 1
        # The computed chain_prev_digest is core.chain.entry_digest(tail),
        # not an arbitrary string: a run bound to it must not be able to
        # forge a link with a hand-picked value.
        from vuoro_evidence.core.chain import entry_digest
        from vuoro_evidence.core.model import EvidenceItem, ValidityBasis, ValidityWindow

        placeholder_validity = ValidityWindow(
            basis=ValidityBasis.INDEFINITE,
            valid_from=datetime(1970, 1, 1, tzinfo=timezone.utc),
        )
        tail_item = EvidenceItem(
            item_id="evi_1", kind="_", ref="_", digest=tail_digest, collector="_",
            validity=placeholder_validity, chain_seq=0, chain_prev_digest=None,
        )
        assert append_request["arguments"]["chain_prev_digest"] == entry_digest(tail_item)

    def test_retrying_the_same_idempotency_key_sends_the_same_item_id(self) -> None:
        """A retry (same run_id, same idempotency_key) must resend the same
        item_id, or sprintctl's idempotency ledger -- which digests item_id
        along with the rest of the arguments -- would see a "different"
        request and refuse the retry as idempotency-conflict instead of
        replaying cleanly (agentops#2466 E2 final report, gap 1)."""

        def _one_round(shell: _FakeShell) -> str:
            toolset = build_toolset(_context(_store(shell)))
            spec = next(t for t in toolset.tools if t.name == "append_evidence")
            parsed = spec.parse(
                {
                    "run_id": RUN_ID, "kind": "test", "ref": "ref-1", "digest": "sha256:" + "b" * 64,
                    "collector": "tester", "validity": VALIDITY, "idempotency_key": "evidence-key-retry",
                }
            )
            _run(spec.run(parsed, _forwarded()))
            return shell.requests[2]["arguments"]["item_id"]

        # Two independent rounds (as an original call and a client-side retry
        # each would be), each observing a *different* tail -- item_id must
        # not depend on it.
        first = _one_round(
            _FakeShell(
                _accepted(
                    "work.run.resolve-v1",
                    {"repo_id": REPO_ID, "run_id": RUN_ID, "principal_id": PRINCIPAL_ID, "workspace_id": WORKSPACE_ID},
                ),
                _accepted("work.evidence.tail-v1", {"repo_id": REPO_ID, "run_id": RUN_ID, "item": None}),
                _accepted(
                    "work.evidence.append-v1",
                    {
                        "repo_id": REPO_ID, "run_id": RUN_ID,
                        "item": {
                            "item_id": "evi_1", "kind": "test", "ref": "ref-1", "digest": "sha256:" + "b" * 64,
                            "collector": "tester", "validity": VALIDITY, "claims": [], "provenance": {},
                            "chain_seq": 0, "chain_prev_digest": None,
                        },
                    },
                ),
            )
        )
        second = _one_round(
            _FakeShell(
                _accepted(
                    "work.run.resolve-v1",
                    {"repo_id": REPO_ID, "run_id": RUN_ID, "principal_id": PRINCIPAL_ID, "workspace_id": WORKSPACE_ID},
                ),
                _accepted(
                    "work.evidence.tail-v1",
                    {
                        "repo_id": REPO_ID, "run_id": RUN_ID,
                        "item": {
                            "item_id": "evi_other", "kind": "test", "ref": "ref-other", "digest": "sha256:" + "e" * 64,
                            "collector": "tester", "validity": VALIDITY, "claims": [], "provenance": {},
                            "chain_seq": 1, "chain_prev_digest": "sha256:" + "f" * 64,
                        },
                    },
                ),
                _accepted(
                    "work.evidence.append-v1",
                    {
                        "repo_id": REPO_ID, "run_id": RUN_ID,
                        "item": {
                            "item_id": "evi_1", "kind": "test", "ref": "ref-1", "digest": "sha256:" + "b" * 64,
                            "collector": "tester", "validity": VALIDITY, "claims": [], "provenance": {},
                            "chain_seq": 0, "chain_prev_digest": None,
                        },
                    },
                ),
            )
        )
        assert first == second

    def test_a_different_idempotency_key_sends_a_different_item_id(self) -> None:
        shell_a = _FakeShell(
            _accepted(
                "work.run.resolve-v1",
                {"repo_id": REPO_ID, "run_id": RUN_ID, "principal_id": PRINCIPAL_ID, "workspace_id": WORKSPACE_ID},
            ),
            _accepted("work.evidence.tail-v1", {"repo_id": REPO_ID, "run_id": RUN_ID, "item": None}),
            _accepted(
                "work.evidence.append-v1",
                {
                    "repo_id": REPO_ID, "run_id": RUN_ID,
                    "item": {
                        "item_id": "evi_1", "kind": "test", "ref": "ref-1", "digest": "sha256:" + "b" * 64,
                        "collector": "tester", "validity": VALIDITY, "claims": [], "provenance": {},
                        "chain_seq": 0, "chain_prev_digest": None,
                    },
                },
            ),
        )
        toolset = build_toolset(_context(_store(shell_a)))
        spec = next(t for t in toolset.tools if t.name == "append_evidence")
        parsed = spec.parse(
            {
                "run_id": RUN_ID, "kind": "test", "ref": "ref-1", "digest": "sha256:" + "b" * 64,
                "collector": "tester", "validity": VALIDITY, "idempotency_key": "evidence-key-a",
            }
        )
        _run(spec.run(parsed, _forwarded()))
        item_id_a = shell_a.requests[2]["arguments"]["item_id"]

        shell_b = _FakeShell(
            _accepted(
                "work.run.resolve-v1",
                {"repo_id": REPO_ID, "run_id": RUN_ID, "principal_id": PRINCIPAL_ID, "workspace_id": WORKSPACE_ID},
            ),
            _accepted("work.evidence.tail-v1", {"repo_id": REPO_ID, "run_id": RUN_ID, "item": None}),
            _accepted(
                "work.evidence.append-v1",
                {
                    "repo_id": REPO_ID, "run_id": RUN_ID,
                    "item": {
                        "item_id": "evi_2", "kind": "test", "ref": "ref-1", "digest": "sha256:" + "b" * 64,
                        "collector": "tester", "validity": VALIDITY, "claims": [], "provenance": {},
                        "chain_seq": 0, "chain_prev_digest": None,
                    },
                },
            ),
        )
        toolset_b = build_toolset(_context(_store(shell_b)))
        spec_b = next(t for t in toolset_b.tools if t.name == "append_evidence")
        parsed_b = spec_b.parse(
            {
                "run_id": RUN_ID, "kind": "test", "ref": "ref-1", "digest": "sha256:" + "b" * 64,
                "collector": "tester", "validity": VALIDITY, "idempotency_key": "evidence-key-b",
            }
        )
        _run(spec_b.run(parsed_b, _forwarded()))
        item_id_b = shell_b.requests[2]["arguments"]["item_id"]
        assert item_id_a != item_id_b

    def test_appending_to_someone_elses_run_is_run_not_found_before_any_append(self) -> None:
        shell = _FakeShell(_rejected("work.run.resolve-v1", "run-not-found", status=404))
        toolset = build_toolset(_context(_store(shell)))
        spec = next(t for t in toolset.tools if t.name == "append_evidence")
        parsed = spec.parse(
            {
                "run_id": RUN_ID, "kind": "test", "ref": "ref-1", "digest": "sha256:" + "b" * 64,
                "collector": "tester", "validity": VALIDITY, "idempotency_key": "evidence-key-0003",
            }
        )
        with pytest.raises(ToolFailure) as excinfo:
            _run(spec.run(parsed, _forwarded()))
        assert excinfo.value.code == "run-not-found"
        assert len(shell.requests) == 1  # resolve only; never reached tail or append

    def test_claims_and_provenance_pass_through_untouched(self) -> None:
        claim = {
            "claim_type": "observation", "subject": "effect-1", "grant_id": None,
            "freshness": {"scope": "repo", "position": 3}, "confirms": True, "detail": {"note": "ok"},
        }
        shell = _FakeShell(
            _accepted(
                "work.run.resolve-v1",
                {"repo_id": REPO_ID, "run_id": RUN_ID, "principal_id": PRINCIPAL_ID, "workspace_id": WORKSPACE_ID},
            ),
            _accepted("work.evidence.tail-v1", {"repo_id": REPO_ID, "run_id": RUN_ID, "item": None}),
            _accepted(
                "work.evidence.append-v1",
                {
                    "repo_id": REPO_ID, "run_id": RUN_ID,
                    "item": {
                        "item_id": "evi_1", "kind": "test", "ref": "ref-1", "digest": "sha256:" + "e" * 64,
                        "collector": "tester", "validity": VALIDITY, "claims": [claim],
                        "provenance": {"session": "abc"}, "chain_seq": 0, "chain_prev_digest": None,
                    },
                },
            ),
        )
        toolset = build_toolset(_context(_store(shell)))
        spec = next(t for t in toolset.tools if t.name == "append_evidence")
        parsed = spec.parse(
            {
                "run_id": RUN_ID, "kind": "test", "ref": "ref-1", "digest": "sha256:" + "e" * 64,
                "collector": "tester", "validity": VALIDITY, "claims": [claim],
                "provenance": {"session": "abc"}, "idempotency_key": "evidence-key-0004",
            }
        )
        _run(spec.run(parsed, _forwarded()))
        sent = shell.requests[2]["arguments"]
        assert sent["claims"] == [claim]
        assert sent["provenance"] == {"session": "abc"}


class TestWriteSessionNote:
    def test_writes_a_note(self) -> None:
        shell = _FakeShell(
            _accepted(
                "work.run.resolve-v1",
                {"repo_id": REPO_ID, "run_id": RUN_ID, "principal_id": PRINCIPAL_ID, "workspace_id": WORKSPACE_ID},
            ),
            _accepted(
                "work.session-note.write-v1",
                {"repo_id": REPO_ID, "run_id": RUN_ID, "note_id": 1, "note": "hi", "created_at": "2026-09-26T00:00:00Z"},
            ),
        )
        toolset = build_toolset(_context(_store(shell)))
        spec = next(t for t in toolset.tools if t.name == "write_session_note")
        parsed = spec.parse({"run_id": RUN_ID, "note": "hi", "idempotency_key": "note-key-0001"})
        result = _run(spec.run(parsed, _forwarded()))
        assert result["note_id"] == 1
        assert result["note"] == "hi"

    def test_writing_to_someone_elses_run_is_run_not_found(self) -> None:
        shell = _FakeShell(_rejected("work.run.resolve-v1", "run-not-found", status=404))
        toolset = build_toolset(_context(_store(shell)))
        spec = next(t for t in toolset.tools if t.name == "write_session_note")
        parsed = spec.parse({"run_id": RUN_ID, "note": "hi", "idempotency_key": "note-key-0002"})
        with pytest.raises(ToolFailure) as excinfo:
            _run(spec.run(parsed, _forwarded()))
        assert excinfo.value.code == "run-not-found"


class TestRecordShellClientTransport:
    def test_a_transport_error_is_a_tool_failure(self) -> None:
        def _raise(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        client = RecordShellClient(
            base_url="http://127.0.0.1:8080", timeout=1.0, transport=httpx.MockTransport(_raise)
        )
        with pytest.raises(ToolFailure) as excinfo:
            _run(client.invoke("work.run.register-v1", {}, _forwarded()))
        assert excinfo.value.code == "record-shell-unavailable"

    def test_a_non_json_body_is_a_tool_failure(self) -> None:
        def _text(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not json")

        client = RecordShellClient(
            base_url="http://127.0.0.1:8080", timeout=1.0, transport=httpx.MockTransport(_text)
        )
        with pytest.raises(ToolFailure) as excinfo:
            _run(client.invoke("work.run.register-v1", {}, _forwarded()))
        assert excinfo.value.code == "record-shell-unavailable"


class TestAppendEvidenceChainRace:
    """The tail read and the append are separate calls: a concurrent append
    can take the computed slot, and sprintctl refuses the stale link with
    evidence-chain-conflict.  append_evidence re-reads the tail and relinks,
    at most CHAIN_ATTEMPTS times."""

    def _spec(self, shell: _FakeShell):
        toolset = build_toolset(_context(_store(shell)))
        spec = next(t for t in toolset.tools if t.name == "append_evidence")
        return spec, spec.parse({**APPEND_ARGUMENTS, "validity": VALIDITY})

    def test_a_lost_race_relinks_against_the_new_tail(self) -> None:
        shell = _FakeShell(
            _accepted("work.run.resolve-v1", _resolved()),
            _accepted("work.evidence.tail-v1", _tail(0)),
            _rejected("work.evidence.append-v1", "evidence-chain-conflict"),
            _accepted("work.evidence.tail-v1", _tail(1)),
            _accepted("work.evidence.append-v1", _appended(2)),
        )
        spec, parsed = self._spec(shell)
        result = _run(spec.run(parsed, _forwarded()))
        assert result["chain_seq"] == 2
        appends = [r["arguments"] for r in shell.requests if r["operation"] == "work.evidence.append-v1"]
        assert [a["chain_seq"] for a in appends] == [1, 2]
        # Same item and key on every attempt: only the link changes.
        assert len({(a["item_id"], a["idempotency_key"]) for a in appends}) == 1

    def test_it_gives_up_after_a_bounded_number_of_attempts(self) -> None:
        responses = [_accepted("work.run.resolve-v1", _resolved())]
        for seq in range(CHAIN_ATTEMPTS):
            responses.append(_accepted("work.evidence.tail-v1", _tail(seq)))
            responses.append(_rejected("work.evidence.append-v1", "evidence-chain-conflict"))
        shell = _FakeShell(*responses)  # a further call would raise: none queued
        spec, parsed = self._spec(shell)
        with pytest.raises(ToolFailure) as excinfo:
            _run(spec.run(parsed, _forwarded()))
        assert excinfo.value.code == "evidence-chain-conflict"
        assert len(shell.requests) == 1 + 2 * CHAIN_ATTEMPTS

    def test_other_append_failures_are_not_retried(self) -> None:
        shell = _FakeShell(
            _accepted("work.run.resolve-v1", _resolved()),
            _accepted("work.evidence.tail-v1", _tail(0)),
            _rejected("work.evidence.append-v1", "idempotency-conflict"),
        )
        spec, parsed = self._spec(shell)
        with pytest.raises(ToolFailure) as excinfo:
            _run(spec.run(parsed, _forwarded()))
        assert excinfo.value.code == "idempotency-conflict"
        assert len(shell.requests) == 3

    def test_the_description_does_not_promise_an_unconditional_extension(self) -> None:
        toolset = build_toolset(_context(_store(_FakeShell())))
        spec = next(t for t in toolset.tools if t.name == "append_evidence")
        description = spec.definition["description"]
        assert "automatically" not in description
        assert "evidence-chain-conflict" in description


class TestOlderSprintctl:
    """Against a work adapter without the record operations (sprintctl
    before 0.8.0) the shell answers unknown-operation; each tool reports a
    typed record-owner-incompatible naming the release it needs."""

    @pytest.mark.parametrize(
        ("tool", "arguments", "operation"),
        [
            (
                "register_run",
                {
                    "harness_id": "claude-code", "harness_build": "1.0.0", "model_id": "m",
                    "recipe_id": "r", "observed_profile": OBSERVED_PROFILE,
                    "idempotency_key": "register-key-old",
                },
                "work.run.register-v1",
            ),
            (
                "append_evidence",
                {**APPEND_ARGUMENTS, "validity": VALIDITY},
                "work.run.resolve-v1",
            ),
            (
                "write_session_note",
                {"run_id": RUN_ID, "note": "hi", "idempotency_key": "note-key-old"},
                "work.run.resolve-v1",
            ),
        ],
    )
    def test_an_older_sprintctl_is_a_typed_error(
        self, tool: str, arguments: dict[str, Any], operation: str
    ) -> None:
        shell = _FakeShell(
            _rejected(
                operation, "unknown-operation",
                "operation is not present in the active catalog", status=404,
            )
        )
        toolset = build_toolset(_context(_store(shell)))
        spec = next(t for t in toolset.tools if t.name == tool)
        with pytest.raises(ToolFailure) as excinfo:
            _run(spec.run(spec.parse(arguments), _forwarded()))
        assert excinfo.value.code == RECORD_OWNER_INCOMPATIBLE
        assert operation in excinfo.value.message
        assert "sprintctl 0.8.0" in excinfo.value.message

    def test_other_upstream_codes_pass_through(self) -> None:
        shell = _FakeShell(_rejected("work.run.resolve-v1", "run-not-found", status=404))
        caller = RunBinding(principal_id=PRINCIPAL_ID, workspace_id=WORKSPACE_ID, repo_id=REPO_ID)
        with pytest.raises(ToolFailure) as excinfo:
            _run(_store(shell).resolve(RUN_ID, caller, forwarded=_forwarded()))
        assert excinfo.value.code == "run-not-found"

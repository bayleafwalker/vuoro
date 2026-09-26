"""E3 (agentops#2467): propose_effect / get_effect -- schema, refusals,
idempotency, run binding and state reporting. No path here ever applies a
diff or reaches an executor; see effect_tools.py's module docstring."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from edge_support import FakeShell, ISSUER, REPO_ID, SUBJECT, WORKSPACE_ID, edge_client, identity_headers, assertion, rpc
from vuoro_mcp_edge import effect_tools
from vuoro_mcp_edge.effect_tools import (
    ENV_REPOSITORY_POLICY,
    INTENT_ID,
    EffectIntent,
    InMemoryIntentStore,
    RepositoryEffectPolicy,
    UnavailableIntentStore,
    _load_repository_policies,
    validate_diff,
)
from vuoro_mcp_edge.runs import InMemoryRunRegistry, RunBinding, UnavailableRunRegistry
from vuoro_mcp_edge.server import CURRENT_PROTOCOL_VERSION, MCP_PATH, TOOL_ORDER
from vuoro_mcp_edge.toolsets import ToolFailure, ToolsetContext
from vuoro_mcp_edge.work_source import ShellWorkSource

PRINCIPAL_ID = f"{ISSUER}:{SUBJECT}:0"
BINDING = RunBinding(principal_id=PRINCIPAL_ID, workspace_id=WORKSPACE_ID, repo_id=REPO_ID)
EFFECT_AUTHORITY = "effect:propose"


def _auth(keys: Any) -> dict[str, str]:
    return identity_headers(assertion(keys[1], authorities=[EFFECT_AUTHORITY]))


def _client(
    keys: Any,
    shell: Any = None,
    *,
    intent_store: Any = None,
    runs: Any = None,
    policies: dict[str, RepositoryEffectPolicy] | None = None,
):
    intent_store = InMemoryIntentStore() if intent_store is None else intent_store
    runs = InMemoryRunRegistry() if runs is None else runs
    toolset = effect_tools._build(
        intent_store=intent_store, runs=runs, repository_policies=policies or {}
    )
    client = edge_client(keys[0], shell or FakeShell(), toolsets=(toolset,))
    return client, intent_store, runs


def _register_run(runs: InMemoryRunRegistry, *, key: str = "run-key-0001") -> str:
    return asyncio.run(runs.register(BINDING, idempotency_key=key))


def _call_with(client, keys, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        MCP_PATH, headers=_auth(keys), json=rpc("tools/call", {"name": name, "arguments": arguments})
    )
    return response.json()["result"]


def _assert_error(result: dict[str, Any], code: str) -> None:
    assert result["isError"] is True, result
    assert result["structuredContent"]["error"]["code"] == code, result


# -- diff fixtures -------------------------------------------------------------

_OLD_SHA = "a" * 40
_NEW_SHA = "b" * 40


def modify_diff(path: str = "docs/readme.md") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"index 1111111..2222222 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )


def add_diff(path: str = "docs/new.md") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1 @@\n"
        "+hello\n"
    )


def delete_diff(path: str = "docs/old.md") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "deleted file mode 100644\n"
        "index 1111111..0000000\n"
        f"--- a/{path}\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-bye\n"
    )


def rename_diff(old_path: str = "docs/old.md", new_path: str = "docs/new.md") -> str:
    return (
        f"diff --git a/{old_path} b/{new_path}\n"
        "similarity index 100%\n"
        f"rename from {old_path}\n"
        f"rename to {new_path}\n"
    )


def binary_diff(path: str = "img.png") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "index 1111111..2222222 100644\n"
        f"Binary files a/{path} and b/{path} differ\n"
    )


def mode_change_diff(path: str = "script.sh") -> str:
    return f"diff --git a/{path} b/{path}\nold mode 100644\nnew mode 100755\n"


def symlink_diff(path: str = "link") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 120000\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        "@@ -0,0 +1 @@\n"
        "+target\n"
    )


def submodule_diff(path: str = "sub") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"index {_OLD_SHA[:7]}..{_NEW_SHA[:7]} 160000\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        f"-Subproject commit {_OLD_SHA}\n"
        f"+Subproject commit {_NEW_SHA}\n"
    )


def traversal_diff(path: str = "../../etc/passwd") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "index 1111111..2222222 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
    )


def ci_workflow_diff(path: str = ".github/workflows/ci.yml") -> str:
    return modify_diff(path)


def _args(**overrides: Any) -> dict[str, Any]:
    base = {
        "run_id": "run_placeholder",
        "repository": REPO_ID,
        "base_commit": _OLD_SHA,
        "title": "Fix the typo",
        "rationale": "A short rationale for the change.",
        "unified_diff": modify_diff(),
        "idempotency_key": "propose-key-0001",
    }
    base.update(overrides)
    return base


# -- schema validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": "x" * 201},
        {"rationale": "x" * 4001},
        {"base_commit": "not-hex"},
        {"base_commit": "a" * 39},
        {"idempotency_key": "short"},
        {"unified_diff": ""},
        {"title": ""},
    ],
)
def test_propose_effect_rejects_out_of_shape_arguments(keys, overrides) -> None:
    client, _, runs = _client(keys)
    run_id = _register_run(runs)
    result = _call_with(client, keys, "propose_effect", _args(run_id=run_id, **overrides))
    _assert_error(result, "invalid-arguments")


def test_propose_effect_rejects_unexpected_argument(keys) -> None:
    client, _, runs = _client(keys)
    run_id = _register_run(runs)
    result = _call_with(client, keys, "propose_effect", {**_args(run_id=run_id), "extra": 1})
    _assert_error(result, "invalid-arguments")


def test_propose_effect_rejects_a_missing_argument(keys) -> None:
    client, _, runs = _client(keys)
    run_id = _register_run(runs)
    arguments = _args(run_id=run_id)
    del arguments["rationale"]
    result = _call_with(client, keys, "propose_effect", arguments)
    _assert_error(result, "invalid-arguments")


def test_unified_diff_over_256_kib_is_rejected() -> None:
    # At the unit level, not over HTTP: server.py's transport body cap
    # (MAX_BODY_BYTES, 64 KiB) is smaller than section 7's own 256 KiB
    # unified_diff cap, so a request this large never reaches tool dispatch
    # in the current composition -- see the PR body for this discrepancy.
    oversized = modify_diff() + ("+" + "x" * 300_000 + "\n")
    with pytest.raises(ToolFailure) as failure:
        effect_tools._parse_propose(_args(unified_diff=oversized))
    assert failure.value.code == "invalid-arguments"


# -- run binding ------------------------------------------------------------------


def test_propose_effect_succeeds_with_a_bound_run(keys) -> None:
    client, store, runs = _client(keys)
    run_id = _register_run(runs)
    result = _call_with(client, keys, "propose_effect", _args(run_id=run_id))
    assert result["isError"] is False
    body = result["structuredContent"]
    assert INTENT_ID.fullmatch(body["intent_id"])
    assert body["state"] == "proposed"
    assert set(body) == {"intent_id", "state"}


def test_unregistered_run_id_is_run_not_found(keys) -> None:
    client, _, _runs = _client(keys)
    result = _call_with(client, keys, "propose_effect", _args(run_id="run_" + "0" * 26))
    _assert_error(result, "run-not-found")


def test_a_run_bound_to_another_caller_is_run_not_found(keys) -> None:
    client, _, runs = _client(keys)
    other = RunBinding(principal_id="other:0", workspace_id=WORKSPACE_ID, repo_id=REPO_ID)
    run_id = asyncio.run(runs.register(other, idempotency_key="k"))
    result = _call_with(client, keys, "propose_effect", _args(run_id=run_id))
    _assert_error(result, "run-not-found")


def test_unavailable_run_registry_fails_closed(keys) -> None:
    client, _, _runs = _client(keys, runs=UnavailableRunRegistry())
    result = _call_with(client, keys, "propose_effect", _args(run_id="run_" + "0" * 26))
    _assert_error(result, "runs-unavailable")


def test_repository_argument_must_match_the_callers_bound_repository(keys) -> None:
    client, _, runs = _client(keys)
    run_id = _register_run(runs)
    result = _call_with(client, keys, "propose_effect", _args(run_id=run_id, repository="some-other-repo"))
    _assert_error(result, "repository-mismatch")


# -- diff structural refusals ---------------------------------------------------


@pytest.mark.parametrize(
    ("diff", "code"),
    [
        (binary_diff(), "binary-patch-refused"),
        (mode_change_diff(), "mode-change-refused"),
        (symlink_diff(), "symlink-refused"),
        (submodule_diff(), "submodule-refused"),
        (traversal_diff(), "path-outside-repository"),
        (delete_diff(), "path-not-allowlisted"),
        (rename_diff(), "path-not-allowlisted"),
        (ci_workflow_diff(), "protected-path-refused"),
        ("please just fix the typo in the readme", "diff-not-supported"),
    ],
)
def test_propose_effect_refuses_each_structural_case(keys, diff, code) -> None:
    client, _, runs = _client(keys)
    run_id = _register_run(runs)
    result = _call_with(client, keys, "propose_effect", _args(run_id=run_id, unified_diff=diff))
    _assert_error(result, code)


def test_a_plain_modify_or_add_needs_no_allowlist_entry(keys) -> None:
    client, _, runs = _client(keys)
    for diff in (modify_diff("docs/a.md"), add_diff("docs/b.md")):
        run_id = _register_run(runs, key=f"key-{diff[:5]}")
        result = _call_with(
            client, keys, "propose_effect", _args(run_id=run_id, unified_diff=diff, idempotency_key=f"k-{hash(diff)}")
        )
        assert result["isError"] is False, result


def test_delete_and_rename_succeed_once_allowlisted(keys) -> None:
    policy = {REPO_ID: RepositoryEffectPolicy(path_allowlist=frozenset({"docs/*"}))}
    client, _, runs = _client(keys, policies=policy)
    run_id = _register_run(runs)
    result = _call_with(client, keys, "propose_effect", _args(run_id=run_id, unified_diff=delete_diff()))
    assert result["isError"] is False, result
    run_id2 = _register_run(runs, key="k2")
    result2 = _call_with(
        client, keys, "propose_effect",
        _args(run_id=run_id2, unified_diff=rename_diff(), idempotency_key="k-rename"),
    )
    assert result2["isError"] is False, result2


def test_ci_workflow_path_succeeds_once_allowlisted(keys) -> None:
    policy = {REPO_ID: RepositoryEffectPolicy(path_allowlist=frozenset({".github/workflows/*"}))}
    client, _, runs = _client(keys, policies=policy)
    run_id = _register_run(runs)
    result = _call_with(client, keys, "propose_effect", _args(run_id=run_id, unified_diff=ci_workflow_diff()))
    assert result["isError"] is False, result


def test_sops_like_protected_pattern_is_configurable_per_repository(keys) -> None:
    policy = {REPO_ID: RepositoryEffectPolicy(protected_path_patterns=frozenset({"secrets/*"}))}
    client, _, runs = _client(keys, policies=policy)
    run_id = _register_run(runs)
    result = _call_with(
        client, keys, "propose_effect", _args(run_id=run_id, unified_diff=modify_diff("secrets/config.yaml"))
    )
    _assert_error(result, "protected-path-refused")


# -- idempotency -----------------------------------------------------------------


def test_replaying_the_same_key_and_arguments_returns_the_same_intent_no_second_create(keys) -> None:
    client, store, runs = _client(keys)
    run_id = _register_run(runs)
    first = _call_with(client, keys, "propose_effect", _args(run_id=run_id))
    assert first["isError"] is False
    second = _call_with(client, keys, "propose_effect", _args(run_id=run_id))
    assert second["isError"] is False
    assert first["structuredContent"] == second["structuredContent"]
    assert len(store._intents) == 1


def test_same_key_different_arguments_is_idempotency_conflict(keys) -> None:
    client, _, runs = _client(keys)
    run_id = _register_run(runs)
    first = _call_with(client, keys, "propose_effect", _args(run_id=run_id))
    assert first["isError"] is False
    second = _call_with(
        client, keys, "propose_effect", _args(run_id=run_id, title="A completely different title")
    )
    _assert_error(second, "idempotency-conflict")


def test_unavailable_intent_store_fails_closed_for_both_tools(keys) -> None:
    client, _, runs = _client(keys, intent_store=UnavailableIntentStore())
    run_id = _register_run(runs)
    proposed = _call_with(client, keys, "propose_effect", _args(run_id=run_id))
    _assert_error(proposed, "effects-unavailable")
    fetched = _call_with(client, keys, "get_effect", {"intent_id": "effect_" + "0" * 26})
    _assert_error(fetched, "effects-unavailable")


def test_production_default_composition_fails_closed(keys) -> None:
    context = ToolsetContext(
        env={}, work_source=ShellWorkSource(base_url="http://127.0.0.1:8080"), runs=UnavailableRunRegistry()
    )
    toolset = effect_tools.build_toolset(context)
    assert toolset is not None
    assert {tool.name for tool in toolset.tools} == {"propose_effect", "get_effect"}
    client = edge_client(keys[0], FakeShell(), toolsets=(toolset,))
    result = _call_with(client, keys, "propose_effect", _args(run_id="run_" + "0" * 26))
    _assert_error(result, "effects-unavailable")


# -- get_effect --------------------------------------------------------------------


def test_get_effect_reports_the_proposed_state(keys) -> None:
    client, _, runs = _client(keys)
    run_id = _register_run(runs)
    proposed = _call_with(client, keys, "propose_effect", _args(run_id=run_id))
    intent_id = proposed["structuredContent"]["intent_id"]
    fetched = _call_with(client, keys, "get_effect", {"intent_id": intent_id})
    assert fetched["structuredContent"] == {"intent_id": intent_id, "state": "proposed"}


def test_get_effect_unknown_id_is_effect_not_found(keys) -> None:
    client, _, _runs = _client(keys)
    result = _call_with(client, keys, "get_effect", {"intent_id": "effect_" + "0" * 26})
    _assert_error(result, "effect-not-found")


def test_get_effect_refuses_an_intent_bound_to_another_caller(keys) -> None:
    store = InMemoryIntentStore()
    other_binding = RunBinding(principal_id="other:0", workspace_id=WORKSPACE_ID, repo_id=REPO_ID)
    intent = EffectIntent(
        intent_id="effect_" + "1" * 26,
        run_id="run_" + "0" * 26,
        binding=other_binding,
        repository=REPO_ID,
        base_commit=_OLD_SHA,
        title="t",
        rationale="r",
        unified_diff=modify_diff(),
    )
    store.seed(intent)
    client, _, _runs = _client(keys, intent_store=store)
    result = _call_with(client, keys, "get_effect", {"intent_id": intent.intent_id})
    _assert_error(result, "effect-not-found")


@pytest.mark.parametrize("state", ["accepted", "rejected", "applied", "failed"])
def test_get_effect_surfaces_every_reconciler_reported_state(keys, state) -> None:
    client, store, runs = _client(keys)
    run_id = _register_run(runs)
    proposed = _call_with(client, keys, "propose_effect", _args(run_id=run_id))
    intent_id = proposed["structuredContent"]["intent_id"]
    store.set_state(intent_id, state)
    fetched = _call_with(client, keys, "get_effect", {"intent_id": intent_id})
    assert fetched["structuredContent"] == {"intent_id": intent_id, "state": state}


def test_get_effect_rejects_unknown_argument(keys) -> None:
    client, _, _runs = _client(keys)
    result = _call_with(client, keys, "get_effect", {"intent_id": "x", "extra": 1})
    _assert_error(result, "invalid-arguments")


# -- authority and protocol conformance --------------------------------------------


def test_effect_tools_need_the_propose_authority(keys) -> None:
    client, _, runs = _client(keys)
    run_id = _register_run(runs)
    response = client.post(
        MCP_PATH,
        headers=identity_headers(assertion(keys[1], authorities=["work:read"])),
        json=rpc("tools/call", {"name": "propose_effect", "arguments": _args(run_id=run_id)}),
    )
    result = response.json()["result"]
    _assert_error(result, "authority-required")


def test_effect_tools_follow_the_builtins_in_order(keys) -> None:
    client, _, _runs = _client(keys)
    listed = client.post(MCP_PATH, headers=_auth(keys), json=rpc("tools/list")).json()["result"]
    names = [tool["name"] for tool in listed["tools"]]
    assert names == [*TOOL_ORDER, "propose_effect", "get_effect"]
    for tool in listed["tools"]:
        if tool["name"] == "propose_effect":
            assert tool["annotations"]["idempotentHint"] is True
            assert tool["annotations"]["readOnlyHint"] is False
        if tool["name"] == "get_effect":
            assert tool["annotations"]["readOnlyHint"] is True


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("tools/list", None),
        ("server/discover", None),
        ("tools/call", {"name": "get_effect", "arguments": {"intent_id": "effect_" + "0" * 26}}),
        ("tools/call", {"name": "propose_effect", "arguments": {}}),
    ],
)
def test_effect_tools_satisfy_the_2026_07_28_client_contract(keys, method, params) -> None:
    client, _, _runs = _client(keys)
    response = client.post(
        MCP_PATH,
        headers={**_auth(keys), "MCP-Protocol-Version": CURRENT_PROTOCOL_VERSION},
        json=rpc(method, params),
    )
    result = response.json()["result"]
    assert result["resultType"] in {"complete", "input_required", "task"}
    assert result["resultType"] == "complete"
    if method in ("tools/list", "server/discover"):
        assert isinstance(result["ttlMs"], int) and result["ttlMs"] >= 0
        assert result["cacheScope"] in {"public", "private"}


# -- validate_diff unit-level (no HTTP) ---------------------------------------------


def test_validate_diff_accepts_a_plain_modify() -> None:
    validate_diff(modify_diff(), policy=RepositoryEffectPolicy())


def test_validate_diff_refuses_binary_directly() -> None:
    with pytest.raises(ToolFailure) as failure:
        validate_diff(binary_diff(), policy=RepositoryEffectPolicy())
    assert failure.value.code == "binary-patch-refused"


# -- repository policy configuration parsing ---------------------------------------


def test_load_repository_policies_empty_env_means_no_configured_repository() -> None:
    assert _load_repository_policies({}) == {}


def test_load_repository_policies_parses_json() -> None:
    env = {
        ENV_REPOSITORY_POLICY: json.dumps(
            {"repo-a": {"path_allowlist": ["docs/*"], "protected_path_patterns": ["secrets/*"]}}
        )
    }
    policies = _load_repository_policies(env)
    assert policies["repo-a"].path_allowlist == frozenset({"docs/*"})
    assert policies["repo-a"].protected_path_patterns == frozenset({"secrets/*"})


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        json.dumps({"repo-a": "not-an-object"}),
        json.dumps({"repo-a": {"path_allowlist": "not-a-list"}}),
        json.dumps({"repo-a": {"protected_path_patterns": [1]}}),
    ],
)
def test_load_repository_policies_rejects_malformed_configuration(raw) -> None:
    with pytest.raises(ValueError):
        _load_repository_policies({ENV_REPOSITORY_POLICY: raw})


# -- TS-16: the edge proposes only; acceptance is never reachable from here ---------

_TRANSITION_WORDS = ("accept", "approve", "reject", "transition", "apply", "execute", "settle")


def test_the_edge_tool_list_has_no_accept_or_transition_tool(keys) -> None:
    from vuoro_mcp_edge.composition import build_toolsets

    context = ToolsetContext(
        env={"VUORO_MCP_EFFECT_AUTO_ACCEPT": "1"},
        work_source=ShellWorkSource(base_url="http://127.0.0.1:8080"),
        runs=UnavailableRunRegistry(),
    )
    toolsets = build_toolsets(context)
    client = edge_client(keys[0], FakeShell(), toolsets=toolsets)
    listed = client.post(MCP_PATH, headers=_auth(keys), json=rpc("tools/list")).json()["result"]
    names = [tool["name"] for tool in listed["tools"]]
    assert "propose_effect" in names
    for name in names:
        assert not any(word in name for word in _TRANSITION_WORDS), name
    # Nor is there a way to reach one through the effect toolset's store.
    assert not any(hasattr(effect_tools.IntentStore, name) for name in ("accept", "reject", "transition"))


@pytest.mark.parametrize(
    "extra",
    [
        {"state": "accepted"},
        {"accepted": True},
        {"acceptor": {"kind": "operator", "subject": "me"}},
        {"auto_accept": True},
    ],
)
def test_propose_effect_with_a_state_or_acceptance_argument_is_refused(keys, extra) -> None:
    client, store, runs = _client(keys)
    run_id = _register_run(runs)
    result = _call_with(client, keys, "propose_effect", {**_args(run_id=run_id), **extra})
    _assert_error(result, "invalid-arguments")
    assert store._intents == {}


def test_auto_accept_env_has_no_effect_on_the_edge(keys, monkeypatch) -> None:
    """Auto-accept is a trusted-side config file only. Setting the env
    changes neither the production toolset nor what propose_effect records
    through it."""

    on = {"VUORO_MCP_EFFECT_AUTO_ACCEPT": json.dumps({"version": 1, "policies": [{"id": "all", "enabled": True}]})}
    for env in (on, {"VUORO_MCP_EFFECT_AUTO_ACCEPT": "true"}):
        monkeypatch.setenv("VUORO_MCP_EFFECT_AUTO_ACCEPT", env["VUORO_MCP_EFFECT_AUTO_ACCEPT"])
        # Production composition, with only the store and registry swapped
        # for the reference ones so a proposal can actually be recorded.
        store = InMemoryIntentStore()
        monkeypatch.setattr(effect_tools, "UnavailableIntentStore", lambda: store)
        runs = InMemoryRunRegistry()
        context = ToolsetContext(env=env, work_source=ShellWorkSource(base_url="http://127.0.0.1:8080"), runs=runs)
        toolset = effect_tools.build_toolset(context)
        baseline = effect_tools.build_toolset(
            ToolsetContext(env={}, work_source=ShellWorkSource(base_url="http://127.0.0.1:8080"), runs=runs)
        )
        assert [(t.name, t.definition) for t in toolset.tools] == [(t.name, t.definition) for t in baseline.tools]

        client = edge_client(keys[0], FakeShell(), toolsets=(toolset,))
        run_id = _register_run(runs)
        proposed = _call_with(client, keys, "propose_effect", _args(run_id=run_id))
        assert proposed["structuredContent"]["state"] == "proposed", proposed
        intent_id = proposed["structuredContent"]["intent_id"]
        assert store._intents[intent_id].state == "proposed"
        assert store._intents[intent_id].acceptor is None
        fetched = _call_with(client, keys, "get_effect", {"intent_id": intent_id})
        assert fetched["structuredContent"] == {"intent_id": intent_id, "state": "proposed"}


def test_get_effect_reports_the_trusted_side_acceptor(keys) -> None:
    client, store, runs = _client(keys)
    run_id = _register_run(runs)
    intent_id = _call_with(client, keys, "propose_effect", _args(run_id=run_id))["structuredContent"]["intent_id"]
    acceptor = {"kind": "policy", "policy_id": "docs-only", "version": 7, "scope": {}, "config_digest": "0" * 64}
    store.set_state(intent_id, "accepted", acceptor=acceptor)
    fetched = _call_with(client, keys, "get_effect", {"intent_id": intent_id})
    assert fetched["structuredContent"] == {"intent_id": intent_id, "state": "accepted", "acceptor": acceptor}


# -- diff grammar: nothing outside the recognised grammar reaches git apply ----------

#: A valid block, then an old-style hunk git apply also applies.
TRAILING_OLD_STYLE_HUNK = modify_diff() + (
    "--- a/.github/workflows/ci.yml\n"
    "+++ b/.github/workflows/ci.yml\n"
    "@@ -0,0 +1 @@\n"
    "+on: push\n"
)

#: An old-style patch before the first `diff --git` header.
PREAMBLE_HUNK = (
    "--- a/.github/workflows/ci.yml\n"
    "+++ b/.github/workflows/ci.yml\n"
    "@@ -0,0 +1 @@\n"
    "+on: push\n"
) + modify_diff()

EXECUTABLE_NEW_FILE = (
    "diff --git a/docs/run.sh b/docs/run.sh\n"
    "new file mode 100755\n"
    "index 0000000..1111111\n"
    "--- /dev/null\n"
    "+++ b/docs/run.sh\n"
    "@@ -0,0 +1 @@\n"
    "+echo hi\n"
)


@pytest.mark.parametrize(
    ("diff", "code"),
    [
        (TRAILING_OLD_STYLE_HUNK, "diff-not-supported"),
        (PREAMBLE_HUNK, "diff-not-supported"),
        ("free text preamble\n" + modify_diff(), "diff-not-supported"),
        (modify_diff() + "trailing garbage\n", "diff-not-supported"),
        (EXECUTABLE_NEW_FILE, "mode-change-refused"),
        # A hunk longer than its header says: the extra line is not context.
        (modify_diff() + "+one more\n", "diff-not-supported"),
        # Header disagreeing with the ---/+++ paths.
        (modify_diff().replace("+++ b/docs/readme.md", "+++ b/.github/workflows/ci.yml"), "diff-not-supported"),
        (modify_diff("docs/.git/config"), "path-outside-repository"),
    ],
)
def test_diffs_outside_the_grammar_are_refused(diff, code) -> None:
    with pytest.raises(ToolFailure) as failure:
        validate_diff(diff, policy=RepositoryEffectPolicy())
    assert failure.value.code == code


@pytest.mark.parametrize("diff", [TRAILING_OLD_STYLE_HUNK, PREAMBLE_HUNK, EXECUTABLE_NEW_FILE])
def test_the_bypass_shapes_are_refused_over_the_wire(keys, diff) -> None:
    client, store, runs = _client(keys)
    run_id = _register_run(runs)
    result = _call_with(client, keys, "propose_effect", _args(run_id=run_id, unified_diff=diff))
    assert result["isError"] is True
    assert store._intents == {}


@pytest.mark.parametrize(
    "diff",
    [
        modify_diff() + add_diff("docs/b.md"),
        "diff --git a/docs/readme.md b/docs/readme.md\n--- a/docs/readme.md\n+++ b/docs/readme.md\n"
        "@@ -1,3 +1,3 @@\n a\n-b\n+c\n\n\\ No newline at end of file\n",
        "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@ section\n-x\n+y\n@@ -9,2 +9,2 @@\n-p\n+q\n r\n",
    ],
)
def test_well_formed_multi_section_diffs_still_pass(diff) -> None:
    validate_diff(diff, policy=RepositoryEffectPolicy())


# -- idempotency under a race ----------------------------------------------------------


class _YieldingStore(InMemoryIntentStore):
    """Suspends in lookup so two proposals interleave exactly where a real
    store's round trip would."""

    async def lookup(self, workspace_id, tool, key):
        found = await super().lookup(workspace_id, tool, key)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return found


def test_a_concurrent_duplicate_propose_yields_exactly_one_intent() -> None:
    from types import SimpleNamespace

    store = _YieldingStore()
    runs = InMemoryRunRegistry()
    run_id = _register_run(runs)
    toolset = effect_tools._build(intent_store=store, runs=runs, repository_policies={})
    propose = next(spec for spec in toolset.tools if spec.name == "propose_effect")
    forwarded = SimpleNamespace(
        identity=SimpleNamespace(principal_id=PRINCIPAL_ID, workspace_id=WORKSPACE_ID), repo_id=REPO_ID
    )
    parsed = effect_tools._parse_propose(_args(run_id=run_id))

    async def race():
        return await asyncio.gather(propose.run(parsed, forwarded), propose.run(dict(parsed), forwarded))

    first, second = asyncio.run(race())
    assert first == second
    assert list(store._intents) == [first["intent_id"]]


def test_a_concurrent_conflicting_propose_is_refused_and_leaves_no_orphan() -> None:
    from types import SimpleNamespace

    store = _YieldingStore()
    runs = InMemoryRunRegistry()
    run_id = _register_run(runs)
    toolset = effect_tools._build(intent_store=store, runs=runs, repository_policies={})
    propose = next(spec for spec in toolset.tools if spec.name == "propose_effect")
    forwarded = SimpleNamespace(
        identity=SimpleNamespace(principal_id=PRINCIPAL_ID, workspace_id=WORKSPACE_ID), repo_id=REPO_ID
    )
    one = effect_tools._parse_propose(_args(run_id=run_id))
    two = effect_tools._parse_propose(_args(run_id=run_id, title="Another title"))

    async def race():
        return await asyncio.gather(propose.run(one, forwarded), propose.run(two, forwarded), return_exceptions=True)

    first, second = asyncio.run(race())
    assert isinstance(second, ToolFailure) and second.code == "idempotency-conflict"
    assert list(store._intents) == [first["intent_id"]]


# -- review of 6020c69: git control files, NUL bytes, control characters ------------

GITATTRIBUTES_BLOB_DIFF = (
    "diff --git a/.gitattributes b/.gitattributes\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/.gitattributes\n"
    "@@ -0,0 +1 @@\n"
    "+blob diff\n"
)
NUL_HUNK_DIFF = (
    "diff --git a/blob b/blob\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/blob\n"
    "@@ -0,0 +1 @@\n"
    "+a\x00b\n"
)


@pytest.mark.parametrize(
    ("diff", "code"),
    [
        (GITATTRIBUTES_BLOB_DIFF, "git-control-file-refused"),
        (NUL_HUNK_DIFF, "binary-patch-refused"),
        (GITATTRIBUTES_BLOB_DIFF + NUL_HUNK_DIFF, "binary-patch-refused"),
        (modify_diff(".gitmodules"), "git-control-file-refused"),
        (modify_diff(".mailmap"), "git-control-file-refused"),
        (modify_diff(".gitignore"), "git-control-file-refused"),
        (modify_diff("docs/.GitAttributes"), "git-control-file-refused"),
        (modify_diff("info/attributes"), "git-control-file-refused"),
        (modify_diff(".gitea/workflows/ci.yml"), "protected-path-refused"),
        (modify_diff("docs/./readme.md"), "path-outside-repository"),
        # Non-ASCII digits in a hunk header are not part of the grammar.
        (modify_diff().replace("@@ -1 +1 @@", "@@ -١ +١ @@"), "diff-not-supported"),
    ],
)
def test_git_control_files_nul_bytes_and_gitea_are_refused(diff, code) -> None:
    with pytest.raises(ToolFailure) as failure:
        validate_diff(diff, policy=RepositoryEffectPolicy())
    assert failure.value.code == code


def test_git_control_files_are_refused_even_when_allowlisted() -> None:
    policy = RepositoryEffectPolicy(path_allowlist=frozenset({"*", ".gitattributes"}))
    with pytest.raises(ToolFailure) as failure:
        validate_diff(GITATTRIBUTES_BLOB_DIFF, policy=policy)
    assert failure.value.code == "git-control-file-refused"


def test_a_gitkeep_is_not_a_control_file() -> None:
    validate_diff(add_diff("docs/.gitkeep"), policy=RepositoryEffectPolicy())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "Fix the typo\x1b[2K"),
        ("title", "Fix\rthe typo"),
        ("title", "Fix\nthe typo"),
        ("title", "Fix the ‮typo"),
        ("title", "Fix the ⁦typo⁩"),
        ("title", "Fix\x85"),
        ("rationale", "Looks fine\x1b[1A\x1b[2K"),
        ("rationale", "line\rover"),
        ("rationale", "bidi ‪ override"),
    ],
)
def test_control_and_bidi_characters_in_title_and_rationale_are_refused(keys, field, value) -> None:
    client, store, runs = _client(keys)
    run_id = _register_run(runs)
    result = _call_with(client, keys, "propose_effect", _args(run_id=run_id, **{field: value}))
    _assert_error(result, "invalid-arguments")
    assert store._intents == {}


def test_a_multi_line_rationale_is_still_accepted(keys) -> None:
    client, _, runs = _client(keys)
    run_id = _register_run(runs)
    result = _call_with(
        client, keys, "propose_effect", _args(run_id=run_id, rationale="First line.\n\n\tIndented detail.")
    )
    assert result["isError"] is False, result

"""Inbound auth is the gateway assertion and nothing else; upstream forwards it."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from edge_support import (
    REPO_ID,
    REQUEST_ID,
    FakeShell,
    accepted,
    assertion,
    call,
    edge_client,
    identity_headers,
    list_record,
    list_result,
    rpc,
)
from vuoro_mcp_edge.server import MCP_PATH


def _assert_unauthorized(response) -> None:
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == -32001
    assert "result" not in body
    # The gateway owns the OAuth challenge; this server issues none.
    assert "www-authenticate" not in response.headers


def test_no_assertion_is_401(keys) -> None:
    shell = FakeShell()
    response = edge_client(keys[0], shell).post(MCP_PATH, json=rpc("tools/list"))
    _assert_unauthorized(response)
    assert shell.requests == []


def test_bearer_token_alone_is_not_accepted(keys) -> None:
    response = edge_client(keys[0], FakeShell()).post(
        MCP_PATH,
        headers={"Authorization": "Bearer vuo_pat_something"},
        json=rpc("tools/list"),
    )
    _assert_unauthorized(response)


def test_missing_request_id_is_401(keys) -> None:
    response = edge_client(keys[0], FakeShell()).post(
        MCP_PATH,
        headers={"X-Vuoro-Identity": assertion(keys[1])},
        json=rpc("tools/list"),
    )
    _assert_unauthorized(response)


def _expired() -> dict[str, float]:
    now = datetime.now(UTC).replace(microsecond=0).timestamp()
    return {"iat": now - 60, "nbf": now - 61, "exp": now - 30}


@pytest.mark.parametrize(
    ("label", "token_kwargs"),
    [
        ("wrong aud", {"aud": "vuoro-control"}),
        ("wrong iss", {"iss": "someone-else"}),
        ("wrong kid", {"kid": "gateway-2025-12"}),
        ("expired", _expired()),
        ("wrong workspace", {"workspace_id": "01K99999999999999999999999"}),
        ("request id mismatch", {"request_id": "01K55555555555555555555555"}),
        ("unbound repo", {"repo_ids": ["repo-z"]}),
    ],
)
def test_invalid_assertion_is_401_and_nothing_goes_upstream(keys, label, token_kwargs) -> None:
    shell = FakeShell()
    token = assertion(keys[1], **token_kwargs)
    response = edge_client(keys[0], shell).post(
        MCP_PATH, headers=identity_headers(token), json=call("list_ready_work")
    )
    _assert_unauthorized(response)
    assert shell.requests == [], label


def test_assertion_signed_by_another_key_is_401(keys) -> None:
    token = assertion(Ed25519PrivateKey.generate())
    response = edge_client(keys[0], FakeShell()).post(
        MCP_PATH, headers=identity_headers(token), json=rpc("tools/list")
    )
    _assert_unauthorized(response)


def test_duplicate_identity_header_is_401(keys) -> None:
    token = assertion(keys[1])
    client = edge_client(keys[0], FakeShell())
    response = client.post(
        MCP_PATH,
        headers=[
            ("X-Vuoro-Identity", token),
            ("X-Vuoro-Identity", token),
            ("X-Request-Id", REQUEST_ID),
        ],
        json=rpc("tools/list"),
    )
    _assert_unauthorized(response)


def test_the_assertion_is_forwarded_verbatim_and_nothing_else_authenticates(keys) -> None:
    shell = FakeShell(accepted(list_result(list_record(1))))
    token = assertion(keys[1])
    response = edge_client(keys[0], shell).post(
        MCP_PATH, headers=identity_headers(token), json=call("list_ready_work")
    )
    assert response.json()["result"]["isError"] is False
    (invoke,) = shell.invoke_requests
    assert invoke.url == "http://127.0.0.1:8080/api/invoke/v1"
    assert invoke.headers["x-vuoro-identity"] == token
    assert invoke.headers["x-request-id"] == REQUEST_ID
    assert invoke.headers["x-vuoro-client-protocol"] == "1"
    assert "authorization" not in invoke.headers
    (catalog,) = shell.catalog_requests
    # The catalog is public; the assertion is not sprayed at it.
    assert "x-vuoro-identity" not in catalog.headers
    assert "authorization" not in catalog.headers


def test_invocation_envelope_binds_request_id_and_single_repo(keys, auth) -> None:
    shell = FakeShell(accepted(list_result()))
    edge_client(keys[0], shell).post(MCP_PATH, headers=auth, json=call("list_ready_work"))
    (envelope,) = shell.invocations
    assert envelope == {
        "schema_version": "invocation/v1",
        "request_id": REQUEST_ID,
        "operation": "work.public.list-v1",
        "arguments": {},
        "catalog_revision": "rev-1",
        "basis_revision": None,
        "idempotency_key": None,
        "repo_id": REPO_ID,
    }


def test_two_repositories_in_the_assertion_is_workspace_ambiguous(keys) -> None:
    shell = FakeShell()
    token = assertion(keys[1], repo_ids=["repo-a", "repo-b"])
    response = edge_client(
        keys[0], shell, allowed_repo_ids=frozenset({"repo-a", "repo-b"})
    ).post(MCP_PATH, headers=identity_headers(token), json=call("list_ready_work"))
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "workspace-ambiguous"
    assert "items" not in result["structuredContent"]
    assert shell.invoke_requests == []


@pytest.mark.parametrize(
    "authorities",
    [["audit:read"], ["work:read", "work:write"], ["work:read", "audit:read"]],
)
def test_an_assertion_broader_or_other_than_work_read_is_401(keys, authorities) -> None:
    shell = FakeShell()
    token = assertion(keys[1], authorities=authorities)
    response = edge_client(keys[0], shell).post(
        MCP_PATH, headers=identity_headers(token), json=call("describe_work", {"work_id": 1})
    )
    _assert_unauthorized(response)
    assert shell.requests == []

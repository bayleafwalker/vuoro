"""The edge against a real runtime shell (`vuoro_service.app.create_app`).

The shell is the unmodified protocol-v1 app with the gateway assertion
verifier, served in-process over ASGI.  This proves the forwarded assertion
passes the shell's own checks -- including request-id correlation against
the invocation envelope -- and that repo scope comes from the assertion.
"""

from __future__ import annotations

import httpx
import pytest
from edge_support import (
    ENVIRONMENT,
    REPO_ID,
    assertion,
    call,
    identity_headers,
    item_record,
    item_result,
    list_record,
    list_result,
    resolver,
)
from fastapi.testclient import TestClient
from vuoro_mcp_edge.server import MCP_PATH, create_edge_app
from vuoro_mcp_edge.work_source import ShellWorkSource
from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry, OperationRejectedError
from vuoro_service.contracts import DomainCompatibility, OperationDefinition

_SCHEMA = "https://json-schema.org/draft/2020-12/schema"


def _definition(name: str, input_schema: dict) -> OperationDefinition:
    return OperationDefinition(
        name=name,
        owning_domain="work",
        input_schema={"$schema": _SCHEMA, **input_schema},
        result_schema={"$schema": _SCHEMA, "type": "object"},
        required_authority="work:read",
        execution_semantics="read",
        idempotency="not-allowed",
        repo_scoped=True,
    )


class _Sprintctl:
    def __init__(self) -> None:
        self.contexts: list = []
        self.list_records = [list_record(1), list_record(2, blocked=True)]
        self.unavailable = False

    def list(self, arguments, context):
        self.contexts.append(context)
        if self.unavailable:
            raise OperationRejectedError(
                "postgres-runtime-unavailable", "work runtime unavailable", http_status=503
            )
        return list_result(*self.list_records)

    def item(self, arguments, context):
        self.contexts.append(context)
        if arguments["work_id"] != 2:
            raise OperationRejectedError("item-not-found", "not found", http_status=404)
        return item_result(item_record(2, blocked_by=[1]))


@pytest.fixture
def stack(keys):
    sprintctl = _Sprintctl()
    registry = CatalogRegistry()
    registry.register(
        _definition(
            "work.public.list-v1",
            {"type": "object", "properties": {}, "additionalProperties": False},
        ),
        sprintctl.list,
    )
    registry.register(
        _definition(
            "work.public.item-v1",
            {
                "type": "object",
                "required": ["work_id"],
                "properties": {"work_id": {"type": "integer", "minimum": 1}},
                "additionalProperties": False,
            },
        ),
        sprintctl.item,
    )
    shell = create_app(
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
        identity_resolver=resolver(keys[0]),
    )
    source = ShellWorkSource(
        base_url="http://127.0.0.1:8080", transport=httpx.ASGITransport(app=shell)
    )
    edge = create_edge_app(identity_resolver=resolver(keys[0]), work_source=source)
    return TestClient(edge), sprintctl


def test_forwarded_assertion_is_accepted_by_the_shell(stack, keys) -> None:
    client, sprintctl = stack
    token = assertion(keys[1])
    result = client.post(
        MCP_PATH, headers=identity_headers(token), json=call("list_ready_work")
    ).json()["result"]
    assert result["isError"] is False, result
    assert [item["work_id"] for item in result["structuredContent"]["items"]] == [1]
    (context,) = sprintctl.contexts
    assert context.repo_id == REPO_ID
    assert context.identity.actor == "github:123"


def test_describe_blocked_item_through_the_shell(stack, auth) -> None:
    client, _ = stack
    result = client.post(
        MCP_PATH, headers=auth, json=call("describe_work", {"work_id": 2})
    ).json()["result"]
    assert result["isError"] is False, result
    assert result["structuredContent"]["item"]["blocked"] is True


def test_shell_503_rejection_reaches_the_client_as_a_tool_error(stack, auth) -> None:
    client, sprintctl = stack
    sprintctl.unavailable = True
    response = client.post(MCP_PATH, headers=auth, json=call("list_ready_work"))
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "postgres-runtime-unavailable"
    assert "items" not in result["structuredContent"]


def test_shell_not_found_reaches_the_client_as_a_tool_error(stack, auth) -> None:
    client, _ = stack
    result = client.post(
        MCP_PATH, headers=auth, json=call("describe_work", {"work_id": 9})
    ).json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "item-not-found"


def test_a_leaking_adapter_behind_a_permissive_schema_is_stopped_at_the_edge(
    stack, auth
) -> None:
    client, sprintctl = stack
    sprintctl.list_records = [list_record(1, description="private body text")]
    response = client.post(MCP_PATH, headers=auth, json=call("list_ready_work"))
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "contract-violation"
    assert "private body text" not in response.text


def test_request_id_flows_from_the_assertion_to_the_invocation_context(stack, keys) -> None:
    """The shell's own correlation check runs on the forwarded headers."""

    client, sprintctl = stack
    other = "01K66666666666666666666666"
    token = assertion(keys[1], request_id=other, jti=other)
    # Inbound, the edge verifies it against the matching header and accepts;
    # the shell then sees the same pair and the envelope carrying it.
    result = client.post(
        MCP_PATH, headers=identity_headers(token, other), json=call("list_ready_work")
    ).json()["result"]
    assert result["isError"] is False
    assert sprintctl.contexts[0].request_id == other

"""Unit tests for the strict MCP 2026-07-28 client (scripts/mcp_strict_client.py).

The client itself runs against the service image in the `mcp-strict-client`
CI job; these tests pin what it refuses, so a loosened check fails here
rather than silently passing a regression there.
"""

from __future__ import annotations

import importlib.util
import json
import re
import stat
import sys
from pathlib import Path

import httpx
import jwt
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "mcp_strict_client.py"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _load():
    spec = importlib.util.spec_from_file_location("mcp_strict_client", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


client = _load()


@pytest.fixture(scope="module")
def schema():
    return client.Schema()


def _list_response(**result_overrides):
    result = {
        "tools": [{"name": "list_ready_work", "inputSchema": {"type": "object"}}],
        "resultType": "complete",
        "ttlMs": 0,
        "cacheScope": "private",
    }
    result.update(result_overrides)
    return {"jsonrpc": "2.0", "id": 1, "result": result}


def test_the_vendored_schema_is_pinned_by_digest(tmp_path) -> None:
    tampered = tmp_path / "schema.json"
    tampered.write_bytes(client.SCHEMA_PATH.read_bytes().replace(b'"private"', b'"shared"', 1))
    with pytest.raises(SystemExit, match="not the pinned"):
        client.Schema(tampered)
    assert client.SCHEMA_SOURCE.endswith("/schema/2026-07-28/schema.json")
    assert re.search(r"/blob/[0-9a-f]{40}/", client.SCHEMA_SOURCE), "the source must name a commit"


def test_a_conforming_tools_list_passes(schema) -> None:
    body = _list_response()
    assert schema.errors("ListToolsResultResponse", body) == []
    assert client.result_value_errors(body["result"]) == []


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"resultType": "tools-list-result"}, "not a completion kind"),
        ({"resultType": "task"}, "expected 'complete'"),
        ({"cacheScope": "shared"}, "cacheScope 'shared'"),
        ({"ttlMs": -1}, "ttlMs -1"),
        ({"ttlMs": True}, "ttlMs True"),
    ],
)
def test_value_rules_refuse_what_strict_clients_refuse(override, fragment) -> None:
    problems = client.result_value_errors(_list_response(**override)["result"])
    assert any(fragment in problem for problem in problems), problems


@pytest.mark.parametrize("missing", ["resultType", "cacheScope", "ttlMs"])
def test_the_schema_requires_the_list_envelope_fields(schema, missing) -> None:
    body = _list_response()
    del body["result"][missing]
    assert any(missing in problem for problem in schema.errors("ListToolsResultResponse", body))


def test_the_schema_enumerates_cache_scope_on_list_results(schema) -> None:
    errors = schema.errors("ListToolsResultResponse", _list_response(cacheScope="shared"))
    assert any("cacheScope" in error for error in errors)


def test_error_objects_must_have_an_integer_code_and_a_message(schema) -> None:
    good = {"jsonrpc": "2.0", "id": 3, "error": {"code": -32601, "message": "method not found"}}
    assert schema.errors("JSONRPCErrorResponse", good) == []
    assert schema.errors("MethodNotFoundError", good["error"]) == []
    for bad in ({"code": "-32601", "message": "x"}, {"code": -32601}, {"message": "x"}):
        assert schema.errors("JSONRPCErrorResponse", {**good, "error": bad})


def test_a_null_error_id_is_outside_the_2026_07_28_schema(schema) -> None:
    body = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "invalid JSON"}}
    assert schema.errors("JSONRPCErrorResponse", body)
    del body["id"]
    assert schema.errors("JSONRPCErrorResponse", body) == []


def test_tool_errors_need_is_error_code_and_message() -> None:
    good = {
        "content": [{"type": "text", "text": "item-not-found: no such work item"}],
        "structuredContent": {"error": {"code": "item-not-found", "message": "no such work item"}},
        "isError": True,
        "resultType": "complete",
    }
    assert client.tool_error_errors(good, "item-not-found") == []
    assert client.tool_error_errors({**good, "isError": False}, "item-not-found")
    assert client.tool_error_errors(good, "invalid-params")
    assert client.tool_error_errors({**good, "structuredContent": {}}, "item-not-found")


def _observe_all_known(report) -> None:
    for check_id, known in client.KNOWN_DEVIATIONS.items():
        report.record(check_id, "known", sorted(known.messages))


def test_report_fails_on_new_deviations_and_on_stale_known_ones(capsys) -> None:
    report = client.Report()
    _observe_all_known(report)
    report.record("tools-list", "tools/list", [])
    assert report.finish() == 0

    report = client.Report()
    _observe_all_known(report)
    report.record("tools-list", "tools/list", ["cacheScope 'shared' is not one of"])
    assert report.finish() == 1

    report = client.Report()  # a known deviation fixed but still listed
    assert report.finish() == 1
    assert "no longer observed" in capsys.readouterr().out


def test_a_known_check_id_excuses_only_its_exact_messages() -> None:
    report = client.Report()
    _observe_all_known(report)
    report.record("ping.envelope", "ping", ["JSONRPCResultResponse: id: None is not of type 'string'"])
    assert report.finish() == 1
    assert [check_id for check_id, _ in report.failures] == ["ping.envelope"]


def test_every_known_deviation_names_a_check_the_client_runs() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    body = source.split("KNOWN_DEVIATIONS: dict[str, KnownDeviation] = {", 1)[1].split("\n}\n", 1)[1]
    for check_id in client.KNOWN_DEVIATIONS:
        assert f'"{check_id}"' in body or check_id == "error.id-null", f"{check_id} is never recorded"
    assert '"error.id-null"' in body


def _response(status: int, body) -> httpx.Response:
    return httpx.Response(status, json=body)


def _checks(schema):
    report = client.Report()
    return client.Checks(schema, report), report


def _failed(report) -> list[str]:
    return sorted({check_id for check_id, _ in report.failures})


def test_a_null_id_is_excused_only_where_the_server_could_not_read_it(schema) -> None:
    error = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "invalid JSON"}}
    checks, report = _checks(schema)
    checks.error("error.parse", "parse", _response(200, error), http_status=200,
                 code=-32700, definition="ParseError")
    assert _failed(report) == [] and "error.id-null" in report.observed_known

    # The server read id 7 (method not found) but answered null: a failure.
    error = {"jsonrpc": "2.0", "id": None, "error": {"code": -32601, "message": "nope"}}
    checks, report = _checks(schema)
    checks.error("error.method-not-found", "unknown method", _response(200, error),
                 http_status=200, code=-32601, definition="MethodNotFoundError", rpc_id=7)
    assert _failed(report) == ["error.method-not-found"]
    assert "error.id-null" not in report.observed_known


def test_a_wrong_non_null_id_fails_even_on_a_pre_parse_refusal(schema) -> None:
    error = {"jsonrpc": "2.0", "id": 99, "error": {"code": -32001, "message": "no"}}
    checks, report = _checks(schema)
    checks.error("error.unauthenticated", "401", _response(401, error), http_status=401,
                 code=-32001, definition="Error", rpc_id=3, pre_parse=True)
    assert _failed(report) == ["error.unauthenticated"]


def test_ping_failing_transport_is_not_masked_by_the_known_deviation(schema) -> None:
    checks, report = _checks(schema)
    checks.ping(5, _response(200, {"jsonrpc": "2.0", "id": 5, "result": {}}))
    assert _failed(report) == [] and "ping.envelope" in report.observed_known

    checks, report = _checks(schema)
    checks.ping(5, _response(500, {"jsonrpc": "2.0", "id": 5, "result": {}}))
    assert _failed(report) == ["ping.transport"]

    checks, report = _checks(schema)
    checks.ping(5, _response(200, {"jsonrpc": "2.0", "id": 6, "result": {}}))
    assert _failed(report) == ["ping.transport"]

    checks, report = _checks(schema)
    checks.ping(5, _response(200, {"jsonrpc": "2.0", "id": 5, "result": {"resultType": "pong"}}))
    assert _failed(report) == ["ping.envelope"]


def test_unsupported_version_body_problems_are_not_masked(schema) -> None:
    def run(body):
        checks, report = _checks(schema)
        response = _response(400, body)
        seen = checks.error(
            "unsupported-version.error", "v", response, http_status=400, code=-32022,
            definition=None, rpc_id=42, pre_parse=True,
            status_check_id="unsupported-version.http-status",
            wrapper="UnsupportedProtocolVersionError",
        )
        return seen, report

    # Today's answer: only the tracked messages.
    _, report = run({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "unsupported"}})
    assert _failed(report) == []
    # A missing message is not one of them.
    _, report = run({"jsonrpc": "2.0", "id": None, "error": {"code": -32600}})
    assert _failed(report) == ["unsupported-version.error"]


def test_setup_writes_an_ephemeral_signer_and_readable_mounts(tmp_path) -> None:
    client.setup(tmp_path)
    private = tmp_path / "signer" / "private.pem"
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    for key, ws in client.WORKSPACES.items():
        root = tmp_path / f"etc-vuoro-{key}"
        public = root / "identity" / "gateway-public.pem"
        assert stat.S_IMODE(public.stat().st_mode) == 0o644
        assert "PRIVATE" not in public.read_text()
        bindings = json.loads((root / "bindings" / "bindings.json").read_text())
        assert bindings["environment"] == ws.environment
        env = dict(
            line.split("=", 1) for line in (tmp_path / f"edge-{key}.env").read_text().splitlines()
        )
        assert env["VUORO_WORKSPACE_ID"] == ws.workspace_id
        assert env["VUORO_MCP_UPSTREAM_URL"] == f"http://stub-{key}:8080"
        assert not any(name.endswith("_DSN") for name in env)

    signer = client.Signer(private)
    ws = client.WORKSPACES["a"]
    first, first_id = signer.mint(ws)
    second, second_id = signer.mint(ws)
    assert first_id != second_id
    public_pem = (tmp_path / "etc-vuoro-a" / "identity" / "gateway-public.pem").read_bytes()
    claims = jwt.decode(first, public_pem, algorithms=["EdDSA"], audience=client.AUDIENCE)
    assert claims["jti"] == claims["request_id"] == first_id
    assert claims["workspace_id"] == ws.workspace_id and claims["repo_ids"] == [ws.repo_id]
    assert claims["client_id"] and claims["grant_id"]
    assert claims["exp"] - claims["iat"] <= 30
    assert jwt.get_unverified_header(first)["kid"] == client.KEY_ID


def test_the_workspaces_cannot_be_confused() -> None:
    a, b = client.WORKSPACES["a"], client.WORKSPACES["b"]
    assert a.workspace_id != b.workspace_id and a.repo_id != b.repo_id
    assert a.title(1) != b.title(1)
    assert 3 in [i["work_id"] for i in a.items] and 3 not in [i["work_id"] for i in b.items]


def test_ci_runs_the_strict_client_on_the_service_image() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    job = workflow.split("\n  mcp-strict-client:\n", 1)[1]
    assert "uses: docker/build-push-action@v7" in job
    assert "tags: vuoro-service:ci" in job
    assert "scripts/mcp_strict_client.sh vuoro-service:ci" in job
    assert "pull_request:" in workflow.split("jobs:", 1)[0]

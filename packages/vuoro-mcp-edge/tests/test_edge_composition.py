"""Environment composition: same trust configuration as the shell, no credentials."""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import vuoro_mcp_edge
import vuoro_service.composition as service_composition
from edge_support import (
    ENVIRONMENT,
    ISSUER,
    KEY_ID,
    REPO_ID,
    REQUEST_ID,
    SUBJECT,
    WORKSPACE_ID,
    assertion,
    call,
    identity_headers,
)
from fastapi.testclient import TestClient
from vuoro_mcp_edge import composition
from vuoro_mcp_edge.composition import (
    EdgeConfigurationError,
    create_app_from_environment,
    refuse_credentials,
)
from vuoro_mcp_edge.record_tools import SprintctlRecordStore
from vuoro_mcp_edge.runs import RunRegistry, binding_for
from vuoro_mcp_edge.toolsets import ToolSet, ToolsetContext, ToolSpec
from vuoro_mcp_edge.work_source import ForwardedIdentity

SRC = Path(vuoro_mcp_edge.__file__).parent


@pytest.fixture
def cloud_mounts(tmp_path: Path, keys, monkeypatch) -> dict[str, str]:
    """Stand the approved Cloud mounts up under tmp_path, as the shell sees them."""

    root = tmp_path / "etc-vuoro"
    (root / "identity").mkdir(parents=True)
    (root / "bindings").mkdir()
    key_path = root / "identity" / "gateway-public.pem"
    key_path.write_bytes(keys[0].read_bytes())
    bindings_path = root / "bindings" / "bindings.json"
    bindings_path.write_text(
        json.dumps(
            {
                "schema_version": "vuoro-project-bindings/v1",
                "environment": ENVIRONMENT,
                "projects": [
                    {
                        "project_id": "01K22222222222222222222222",
                        "descriptor_digest": "sha256:" + "a" * 64,
                        "repositories": [
                            {"repo_id": REPO_ID, "git_remote": None, "commit_sha": None}
                        ],
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(service_composition, "_CLOUD_BINDINGS_TRUST_ROOT", root)
    # The shell's embedded bindings default, read at call time.
    monkeypatch.setattr(service_composition, "_DEFAULT_PROJECT_BINDINGS_PATH", bindings_path)
    monkeypatch.setattr(service_composition, "_CLOUD_GATEWAY_PUBLIC_KEY_PATH", key_path)
    # The pod-local emptyDir both containers mount for the edge proof key.
    proof_dir = tmp_path / "run-vuoro" / "edge-proof"
    proof_dir.mkdir(parents=True)
    proof_key_path = proof_dir / "key"
    monkeypatch.setattr(service_composition, "_EDGE_PROOF_KEY_PATH", proof_key_path)
    return {
        "VUORO_ENVIRONMENT_NAME": ENVIRONMENT,
        "VUORO_WORKSPACE_ID": WORKSPACE_ID,
        "VUORO_GATEWAY_PUBLIC_KEY_FILE": str(key_path),
        "VUORO_GATEWAY_ASSERTION_ISSUER": ISSUER,
        "VUORO_GATEWAY_ASSERTION_KEY_ID": KEY_ID,
        "VUORO_EDGE_PROOF_KEY_FILE": str(proof_key_path),
    }


def test_environment_builds_a_server_that_verifies_assertions(cloud_mounts, auth) -> None:
    client = TestClient(create_app_from_environment(cloud_mounts))
    assert client.post("/mcp", json=call("list_ready_work")).status_code == 401
    listed = client.post(
        "/mcp", headers=auth, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert listed.status_code == 200


def test_environment_composition_wires_a_durable_record_bucket(cloud_mounts, auth) -> None:
    """composition.py's one line (``runs=record_tools.build_run_registry(env)``)
    must actually reach ``create_edge_app`` -- not just type-check -- so the
    record bucket's tools are discoverable, not silently absent the way they
    are with the shared ``UnavailableRunRegistry`` placeholder
    (test_toolsets.py's ``test_default_composition_ships_no_write_tools``)."""

    client = TestClient(create_app_from_environment(cloud_mounts))
    listed = client.post(
        "/mcp", headers=auth, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert listed.status_code == 200
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert {"register_run", "append_evidence", "write_session_note"} <= names


RUN_ID = "run_" + "0" * 24 + "AA"


def _probe_toolset(runs: RunRegistry) -> ToolSet:
    """A propose-bucket tool that uses ``context.runs`` exactly as E3's
    ``propose_effect`` does: typed as the shared ``RunRegistry`` protocol,
    never as E2's concrete store."""

    async def run(parsed: dict[str, Any], forwarded: ForwardedIdentity) -> dict[str, Any]:
        binding = binding_for(forwarded)
        resolved = await runs.resolve(parsed["run_id"], binding, forwarded=forwarded)
        return {"principal_id": resolved.principal_id, "grant_id": resolved.grant_id}

    definition = {
        "name": "probe_run",
        "title": "Probe a run",
        "description": "Test tool.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}},
    }
    return ToolSet(name="probe", tools=(ToolSpec("probe_run", "propose", definition, dict, run),))


def test_a_protocol_caller_resolves_through_the_production_registry(
    cloud_mounts, keys, monkeypatch
) -> None:
    """The registry composition.py wires in as ``context.runs`` must accept
    the calls the ``RunRegistry`` protocol promises.  Everything here is the
    production path except the shell's HTTP transport: a toolset that calls
    ``runs.resolve`` through the protocol and gets a TypeError would surface
    as ``internal-error`` and fail this test."""

    seen: list[dict[str, Any]] = []

    def shell(request: httpx.Request) -> httpx.Response:
        envelope = json.loads(request.content)
        seen.append(envelope)
        assert envelope["operation"] == "work.run.resolve-v1"
        return httpx.Response(
            200,
            json={
                "status": "accepted",
                "operation": "work.run.resolve-v1",
                "result": {
                    "repo_id": REPO_ID,
                    "run_id": RUN_ID,
                    "principal_id": f"{ISSUER}:{SUBJECT}:0",
                    "workspace_id": WORKSPACE_ID,
                    "client_id": "claude-connector",
                    "grant_id": "grant-1",
                },
            },
        )

    original_init = httpx.AsyncClient.__init__

    def mocked_init(self, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("transport") is None:
            kwargs["transport"] = httpx.MockTransport(shell)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", mocked_init)

    composed: list[ToolsetContext] = []
    original_build = composition.build_toolsets

    def build_with_probe(context: ToolsetContext) -> tuple[ToolSet, ...]:
        composed.append(context)
        return (*original_build(context), _probe_toolset(context.runs))

    monkeypatch.setattr(composition, "build_toolsets", build_with_probe)

    client = TestClient(create_app_from_environment(cloud_mounts))
    # The registry under test is the durable one, not a test reference.
    assert type(composed[0].runs) is SprintctlRecordStore
    token = assertion(
        keys[1],
        authorities=["work:read", "effect:propose"],
        client_id="claude-connector",
        grant_id="grant-1",
    )
    result = client.post(
        "/mcp", headers=identity_headers(token), json=call("probe_run", {"run_id": RUN_ID})
    ).json()["result"]
    assert result["isError"] is False, result
    assert result["structuredContent"] == {
        "principal_id": f"{ISSUER}:{SUBJECT}:0",
        "grant_id": "grant-1",
    }
    assert [envelope["arguments"] for envelope in seen] == [{"run_id": RUN_ID}]


@pytest.mark.parametrize("method", ["register", "resolve"])
def test_the_production_registry_matches_the_protocol_signature(cloud_mounts, method) -> None:
    """A second, cheaper guard: the composed registry's methods take exactly
    the parameters ``RunRegistry`` declares, no more (a widened required
    keyword breaks protocol callers) and no fewer."""

    composed: list[ToolsetContext] = []
    original_build = composition.build_toolsets

    def capture(context: ToolsetContext) -> tuple[ToolSet, ...]:
        composed.append(context)
        return original_build(context)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(composition, "build_toolsets", capture)
        create_app_from_environment(cloud_mounts)
    store_method = getattr(type(composed[0].runs), method)
    protocol_method = getattr(RunRegistry, method)
    assert inspect.signature(store_method).parameters.keys() == (
        inspect.signature(protocol_method).parameters.keys()
    )
    for name, parameter in inspect.signature(protocol_method).parameters.items():
        assert inspect.signature(store_method).parameters[name].kind == parameter.kind


def test_wrong_audience_configuration_rejects_the_gateway_assertion(cloud_mounts, auth) -> None:
    env = {**cloud_mounts, "VUORO_GATEWAY_ASSERTION_AUDIENCE": "vuoro-other"}
    client = TestClient(create_app_from_environment(env))
    response = client.post(
        "/mcp", headers=auth, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert response.status_code == 401


def test_the_edge_audience_can_be_split_from_the_shell_audience(cloud_mounts, keys) -> None:
    """agentops#2519: once the gateway mints a separate MCP audience, the
    edge expects it and the shell-audience assertion is refused here."""

    env = {**cloud_mounts, "VUORO_EDGE_GATEWAY_ASSERTION_AUDIENCE": "vuoro-mcp"}
    client = TestClient(create_app_from_environment(env))
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    shared = client.post("/mcp", headers=identity_headers(assertion(keys[1])), json=body)
    assert shared.status_code == 401
    split = client.post(
        "/mcp", headers=identity_headers(assertion(keys[1], aud="vuoro-mcp")), json=body
    )
    assert split.status_code == 200, split.text


def test_the_edge_consumes_each_assertion_once(cloud_mounts, auth) -> None:
    client = TestClient(create_app_from_environment(cloud_mounts))
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post("/mcp", headers=auth, json=body).status_code == 200
    again = client.post("/mcp", headers=auth, json=body)
    assert again.status_code == 401
    assert again.json()["error"]["code"] == -32003


def test_missing_edge_proof_key_refuses_to_start(cloud_mounts) -> None:
    """Without proofs the shell would refuse a tool call's second upstream
    call as a replay; the edge does not start half-working."""

    env = dict(cloud_mounts)
    del env["VUORO_EDGE_PROOF_KEY_FILE"]
    with pytest.raises(EdgeConfigurationError, match="VUORO_EDGE_PROOF_KEY_FILE"):
        create_app_from_environment(env)


def test_edge_proof_key_outside_its_mount_refuses_to_start(cloud_mounts, tmp_path) -> None:
    env = {**cloud_mounts, "VUORO_EDGE_PROOF_KEY_FILE": str(tmp_path / "elsewhere")}
    with pytest.raises(EdgeConfigurationError, match="edge proof key mount"):
        create_app_from_environment(env)


def test_edge_and_shell_share_one_created_proof_key(cloud_mounts) -> None:
    key_path = Path(cloud_mounts["VUORO_EDGE_PROOF_KEY_FILE"])
    assert not key_path.exists()
    create_app_from_environment(cloud_mounts)
    first = key_path.read_bytes()
    assert service_composition.load_edge_proof_key(cloud_mounts) == first
    create_app_from_environment(cloud_mounts)
    assert key_path.read_bytes() == first


def test_the_shell_verifier_from_the_same_environment_accepts_edge_proofs(
    cloud_mounts, keys
) -> None:
    """The shell side of one pod's environment: its verifier accepts a proof
    minted with the key the edge created, several times for one assertion,
    and the MCP-minted assertion itself only through the edge."""

    from starlette.requests import Request
    from vuoro_service.edge_proof import PROOF_HEADER, mint_edge_proof
    from vuoro_service.identity import IdentityResolutionError

    create_app_from_environment(cloud_mounts)
    key = Path(cloud_mounts["VUORO_EDGE_PROOF_KEY_FILE"]).read_bytes()
    shell = service_composition.load_gateway_assertion_resolver(cloud_mounts)
    token = assertion(keys[1], client_id="claude-connector", grant_id="grant-1")
    body = b'{"request_id": "x"}'

    def request(proof: str | None) -> Request:
        headers = [(b"x-vuoro-identity", token.encode()), (b"x-request-id", REQUEST_ID.encode())]
        if proof is not None:
            headers.append((PROOF_HEADER.encode(), proof.encode()))
        built = Request({"type": "http", "method": "POST", "path": "/api/invoke/v1", "headers": headers})
        built.state.vuoro_invocation_request_id = REQUEST_ID
        built.state.vuoro_invocation_body = body
        return built

    for _ in range(3):
        proof = mint_edge_proof(
            key, method="POST", path="/api/invoke/v1", assertion=token, body=body
        )
        assert shell(request(proof)).actor == "github:123"
    with pytest.raises(IdentityResolutionError, match="only through the MCP edge"):
        shell(request(None))


def test_missing_gateway_key_refuses_to_start(cloud_mounts) -> None:
    env = dict(cloud_mounts)
    del env["VUORO_GATEWAY_PUBLIC_KEY_FILE"]
    with pytest.raises(EdgeConfigurationError, match="VUORO_GATEWAY_PUBLIC_KEY_FILE"):
        create_app_from_environment(env)


def test_missing_issuer_refuses_to_start(cloud_mounts) -> None:
    env = dict(cloud_mounts)
    del env["VUORO_GATEWAY_ASSERTION_ISSUER"]
    with pytest.raises(EdgeConfigurationError, match="VUORO_GATEWAY_ASSERTION_ISSUER"):
        create_app_from_environment(env)


def test_key_outside_the_approved_mount_refuses_to_start(cloud_mounts, tmp_path) -> None:
    env = {**cloud_mounts, "VUORO_GATEWAY_PUBLIC_KEY_FILE": str(tmp_path / "other.pem")}
    with pytest.raises(EdgeConfigurationError):
        create_app_from_environment(env)


@pytest.mark.parametrize(
    "extra",
    [
        {"VUORO_WORK_RUNTIME_DSN": "postgresql://x"},
        {"VUORO_WORK_DSN": "postgresql://x"},
        {"VUORO_MCP_EDGE_TOKEN": "vuo_pat_abc"},
        {"ANYTHING": "vuo_pat_abc"},
    ],
)
def test_an_environment_carrying_a_credential_refuses_to_start(cloud_mounts, extra) -> None:
    with pytest.raises(EdgeConfigurationError, match="holds no credential"):
        create_app_from_environment({**cloud_mounts, **extra})


def test_refuse_credentials_accepts_a_clean_environment(cloud_mounts) -> None:
    refuse_credentials(cloud_mounts)


def test_upstream_url_must_be_http(cloud_mounts) -> None:
    with pytest.raises(EdgeConfigurationError, match="VUORO_MCP_UPSTREAM_URL"):
        create_app_from_environment({**cloud_mounts, "VUORO_MCP_UPSTREAM_URL": "127.0.0.1:8080"})


def test_upstream_default_is_the_local_shell() -> None:
    from vuoro_mcp_edge.work_source import DEFAULT_UPSTREAM_URL

    assert DEFAULT_UPSTREAM_URL == "http://127.0.0.1:8080"


#: A credential mechanism in either quote style, or written as a bare header.
_CREDENTIAL_PATTERNS = (
    r"[\"']authorization",
    r"\bauthorization\s*:",
    r"[\"']bearer",
    r"\bbearer\s+[a-z0-9{_$]",
    r"vuo_pat_",
    r"_dsn\b",
    r"psycopg",
)


@pytest.mark.parametrize(
    "module", ["server.py", "work_source.py", "contract.py", "errors.py", "__init__.py"]
)
def test_request_path_modules_carry_no_credential_mechanism(module) -> None:
    source = (SRC / module).read_text(encoding="utf-8")
    hits = [
        pattern
        for pattern in _CREDENTIAL_PATTERNS
        if re.search(pattern, source, re.IGNORECASE)
    ]
    assert hits == [], (module, hits)


@pytest.mark.parametrize(
    "snippet",
    [
        'headers={"Authorization": token}',
        "headers={'Authorization': token}",
        "Authorization: Bearer abc",
        'f"Bearer {token}"',
        "'bearer ' + token",
        "VUORO_WORK_DSN",
    ],
)
def test_the_credential_search_catches_each_form(snippet) -> None:
    assert any(re.search(p, snippet, re.IGNORECASE) for p in _CREDENTIAL_PATTERNS)

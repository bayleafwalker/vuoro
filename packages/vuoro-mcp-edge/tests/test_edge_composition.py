"""Environment composition: same trust configuration as the shell, no credentials."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import vuoro_mcp_edge
import vuoro_service.composition as service_composition
from edge_support import ENVIRONMENT, ISSUER, KEY_ID, REPO_ID, WORKSPACE_ID, call
from fastapi.testclient import TestClient
from vuoro_mcp_edge.composition import (
    EdgeConfigurationError,
    create_app_from_environment,
    refuse_credentials,
)

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
    return {
        "VUORO_ENVIRONMENT_NAME": ENVIRONMENT,
        "VUORO_WORKSPACE_ID": WORKSPACE_ID,
        "VUORO_GATEWAY_PUBLIC_KEY_FILE": str(key_path),
        "VUORO_GATEWAY_ASSERTION_ISSUER": ISSUER,
        "VUORO_GATEWAY_ASSERTION_KEY_ID": KEY_ID,
    }


def test_environment_builds_a_server_that_verifies_assertions(cloud_mounts, auth) -> None:
    client = TestClient(create_app_from_environment(cloud_mounts))
    assert client.post("/mcp", json=call("list_ready_work")).status_code == 401
    listed = client.post(
        "/mcp", headers=auth, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert listed.status_code == 200


def test_wrong_audience_configuration_rejects_the_gateway_assertion(cloud_mounts, auth) -> None:
    env = {**cloud_mounts, "VUORO_GATEWAY_ASSERTION_AUDIENCE": "vuoro-other"}
    client = TestClient(create_app_from_environment(env))
    response = client.post(
        "/mcp", headers=auth, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    assert response.status_code == 401


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


@pytest.mark.parametrize("module", ["server.py", "work_source.py", "contract.py"])
def test_request_path_modules_carry_no_credential_mechanism(module) -> None:
    source = (SRC / module).read_text(encoding="utf-8").lower()
    for needle in ("\"authorization", "\"bearer", "vuo_pat_", "_dsn", "psycopg"):
        assert needle not in source, (module, needle)

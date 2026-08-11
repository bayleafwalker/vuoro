from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from vuoro_bootstrap.api import BootstrapApi, BootstrapError
from vuoro_bootstrap import cli
from vuoro_bootstrap.files import (
    BootstrapFilesError,
    apply_plan,
    plan_files,
    read_private_file,
)
from vuoro_client.discovery import parse_bootstrap_manifest, parse_discovery
from vuoro_client.profile import load_profile


DISCOVERY = {
    "schema_version": "vuoro-discovery/v1",
    "environment_id": "vuoro-cloud",
    "api_endpoint": "https://api.vuoro.cloud",
    "bootstrap_endpoint": "https://api.vuoro.cloud/api/control/v1/bootstrap/sessions",
    "activation_endpoint": "https://vuoro.cloud/activate",
    "client_protocol": {"minimum": 1, "maximum": 1},
    "bootstrap_manifest": "https://api.vuoro.cloud/api/control/v1/bootstrap/manifest",
}
MANIFEST = {
    "schema_version": "vuoro-bootstrap-manifest/v1",
    "environment_id": "vuoro-cloud",
    "minimum_python": "3.12",
    "packages": {"vuoro-bootstrap": "0.1.0", "vuoro-client": "0.1.0", "sprintctl": "0.2.22"},
    "service": {"client_protocol_minimum": 1, "client_protocol_maximum": 1, "vuoro_cloud": "0.1.0"},
    "release_ready": True,
}
EXCHANGE = {
    "workspace_id": "workspace-1",
    "project_id": "project-1",
    "repo_id": "repo-1",
    "token_id": "token-1",
    "token": "secret-token",
    "profile": {"schema_version": "vuoro-client-profile/v1", "id": "vuoro-cloud", "target": {"environment_id": "vuoro-cloud", "endpoint": "https://api.vuoro.cloud"}},
}


def test_api_validates_cloud_shaped_discovery_manifest_and_session() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/vuoro":
            return httpx.Response(200, json=DISCOVERY)
        if request.url.path.endswith("/bootstrap/manifest"):
            return httpx.Response(200, json=MANIFEST)
        if request.method == "POST" and request.url.path.endswith("/bootstrap/sessions"):
            return httpx.Response(201, json={"session_id": "session-1", "device_code": "device-1", "user_code": "KITE-MOON", "verification_uri": "https://vuoro.cloud/activate", "expires_in": 600, "interval": 3})
        if request.method == "POST" and request.url.path.endswith("/exchange"):
            return httpx.Response(200, json=EXCHANGE)
        return httpx.Response(404)

    with BootstrapApi(
        "https://api.vuoro.cloud/",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) as api:
        discovery = api.discovery()
        manifest = api.manifest(discovery)
        session = api.create_session(discovery, repository_hint={"repo_id": "repo-1"})
        exchange = api.exchange(discovery, session_id=session["session_id"], device_code=session["device_code"])

    assert manifest.packages.sprintctl == "0.2.22"
    assert exchange["token_id"] == "token-1"


def test_discovery_requires_the_exact_normalized_requested_api_endpoint() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DISCOVERY)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with BootstrapApi("https://api.vuoro.cloud/tenant/", client=client) as api:
        with pytest.raises(BootstrapError, match="exactly match"):
            api.discovery()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verification_uri", "https://vuoro.cloud/other-activation"),
        ("expires_in", 0),
        ("expires_in", True),
        ("interval", -1),
        ("interval", 1.5),
    ],
)
def test_session_creation_rejects_uncorrelated_or_nonpositive_activation_contract(
    field: str, value: object
) -> None:
    response = {
        "session_id": "session-1",
        "device_code": "device-1",
        "user_code": "KITE-MOON",
        "verification_uri": "https://vuoro.cloud/activate",
        "expires_in": 600,
        "interval": 3,
        field: value,
    }

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=response)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with BootstrapApi("https://api.vuoro.cloud", client=client) as api:
        with pytest.raises(BootstrapError):
            api.create_session(
                parse_discovery(DISCOVERY), repository_hint={"repo_id": "repo-1"}
            )


@pytest.mark.parametrize(
    ("session_id", "encoded_id"),
    [
        ("session/part?query#fragment", "session%2Fpart%3Fquery%23fragment"),
        ("..", "%2E%2E"),
    ],
)
def test_exchange_uses_declared_session_endpoint_and_encodes_opaque_id(
    session_id: str, encoded_id: str
) -> None:
    discovery = parse_discovery(
        {
            **DISCOVERY,
            "bootstrap_endpoint": "https://api.vuoro.cloud/custom/bootstrap-sessions",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.decode() == (
            f"/custom/bootstrap-sessions/{encoded_id}/exchange"
        )
        return httpx.Response(200, json=EXCHANGE)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with BootstrapApi("https://api.vuoro.cloud", client=client) as api:
        exchange = api.exchange(
            discovery,
            session_id=session_id,
            device_code="device-1",
        )
    assert exchange["token"] == "secret-token"


def test_file_plan_is_pure_and_conflicts_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    credential_dir = tmp_path / "config" / "credentials"
    plan = plan_files(
        EXCHANGE,
        root=root,
        credential_dir=credential_dir,
        repo_id="repo-1",
        expected_environment_id="vuoro-cloud",
        sprintctl_version="0.2.22",
        install_mode="print",
    )
    assert not root.exists()
    assert plan.files[root / ".sprintctl" / "backend.json"]
    assert b"secret-token" in plan.files[credential_dir / "vuoro-cloud.token"]
    assert plan.commands == ("uv tool install --python 3.12 'sprintctl[served]==0.2.22'",)

    apply_plan(plan)
    assert (credential_dir / "vuoro-cloud.token").stat().st_mode & 0o777 == 0o600
    assert load_profile(root / ".vuoro" / "profile.json").endpoint == "https://api.vuoro.cloud"
    (root / ".sprintctl" / "backend.json").write_text("{}")
    try:
        apply_plan(plan)
    except BootstrapFilesError:
        pass
    else:
        raise AssertionError("conflicting bootstrap output must not be overwritten")


def test_file_plan_rejects_authority_or_symlink_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".vuoro").symlink_to(outside, target_is_directory=True)
    plan = plan_files(
        EXCHANGE,
        root=root,
        credential_dir=tmp_path / "credentials",
        repo_id="repo-1",
        sprintctl_version="0.2.22",
    )
    try:
        apply_plan(plan)
    except BootstrapFilesError:
        pass
    else:
        raise AssertionError("bootstrap must not follow a repository symlink")

    mismatch = {**EXCHANGE, "repo_id": "other-repo"}
    try:
        plan_files(
            mismatch,
            root=tmp_path / "new",
            credential_dir=tmp_path / "credentials",
            repo_id="repo-1",
            sprintctl_version="0.2.22",
        )
    except BootstrapFilesError:
        pass
    else:
        raise AssertionError("bootstrap must bind output to Cloud's approved repository")


def test_file_plan_requires_concrete_sprintctl_release(tmp_path: Path) -> None:
    with pytest.raises(BootstrapFilesError, match="concrete immutable"):
        plan_files(
            EXCHANGE,
            root=tmp_path / "repo",
            credential_dir=tmp_path / "credentials",
            repo_id="repo-1",
            sprintctl_version=None,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("mode", [0o400, 0o644, 0o700, 0o1600])
def test_private_session_read_requires_exact_permissions(
    tmp_path: Path, mode: int
) -> None:
    session_file = tmp_path / "bootstrap-session.json"
    session_file.write_text("{}")
    session_file.chmod(mode)
    with pytest.raises(BootstrapFilesError, match="mode 0600"):
        read_private_file(session_file)


@pytest.mark.parametrize("mode", [0o400, 0o700, 0o1600])
def test_idempotent_private_write_requires_existing_mode_0600(
    tmp_path: Path, mode: int
) -> None:
    plan = plan_files(
        EXCHANGE,
        root=tmp_path / "repo",
        credential_dir=tmp_path / "credentials",
        repo_id="repo-1",
        sprintctl_version="0.2.22",
    )
    apply_plan(plan)
    credential = tmp_path / "credentials" / "vuoro-cloud.token"
    credential.chmod(mode)
    with pytest.raises(BootstrapFilesError, match="mode 0600"):
        apply_plan(plan)


def test_private_session_read_rejects_symlinked_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    session_file = outside / "bootstrap-session.json"
    session_file.write_text("{}")
    session_file.chmod(0o600)
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(BootstrapFilesError, match="cannot safely read"):
        read_private_file(linked / "bootstrap-session.json")


def test_cli_reuses_saved_session_for_exchange(monkeypatch, tmp_path: Path, capsys) -> None:
    class FakeApi:
        creates = 0
        exchanges = 0

        def __init__(self, _endpoint):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def discovery(self):
            return parse_discovery(DISCOVERY)

        def manifest(self, _discovery):
            return parse_bootstrap_manifest(MANIFEST)

        def create_session(self, _discovery, *, repository_hint):
            FakeApi.creates += 1
            return {
                "session_id": "session-1",
                "device_code": "device-1",
                "user_code": "KITE-MOON",
                "verification_uri": "https://vuoro.cloud/activate",
                "expires_in": 600,
                "interval": 3,
            }

        def exchange(self, _discovery, *, session_id, device_code):
            assert (session_id, device_code) == ("session-1", "device-1")
            FakeApi.exchanges += 1
            return EXCHANGE

    monkeypatch.setattr(cli, "BootstrapApi", FakeApi)
    root = tmp_path / "repo"
    assert cli.main(["bootstrap", "https://api.vuoro.cloud", "--repo-id", "repo-1", "--root", str(root)]) == 0
    capsys.readouterr()
    session_file = root / ".vuoro" / "bootstrap-session.json"
    assert session_file.is_file()

    assert cli.main(["bootstrap", "https://api.vuoro.cloud", "--repo-id", "repo-1", "--root", str(root)]) == 0
    capsys.readouterr()
    assert FakeApi.creates == 1
    assert FakeApi.exchanges == 1
    assert not session_file.exists()


def test_cli_print_mode_is_local_side_effect_free(monkeypatch, tmp_path: Path, capsys) -> None:
    class PrintApi:
        def __init__(self, _endpoint):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def discovery(self):
            return parse_discovery(DISCOVERY)

        def manifest(self, _discovery):
            return parse_bootstrap_manifest(MANIFEST)

        def create_session(self, *_args, **_kwargs):
            raise AssertionError("print mode must not create a device session")

    monkeypatch.setattr(cli, "BootstrapApi", PrintApi)
    root = tmp_path / "repo"
    assert cli.main(
        [
            "bootstrap",
            "https://api.vuoro.cloud",
            "--repo-id",
            "repo-1",
            "--root",
            str(root),
            "--install-mode",
            "print",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "print-only"
    assert not root.exists()

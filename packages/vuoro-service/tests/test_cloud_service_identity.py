from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Request
from vuoro_service.cloud_service_identity import (
    SERVICE_AUDIENCE,
    SERVICE_PATHS,
    SERVICE_SUBJECT,
    CloudServiceAssertionVerifier,
)
from vuoro_service.composition import (
    CompositionError,
    _mount_private_recipient_delivery,
)
from vuoro_service.replay import ReplayCache

ISSUER = "vuoro-cloud-test"
WORKSPACE = "01JTESTWORKSPACE000000000000"
KID = "cloud-service-test"
PATH = "/internal/operator-request-delivery/v1/resolve"


def _pem_public(key):
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _token(key, *, body: bytes, path=PATH, **overrides):
    now = int(time.time())
    claims = {
        "aud": SERVICE_AUDIENCE,
        "body_sha256": __import__("hashlib").sha256(body).hexdigest(),
        "exp": now + 20,
        "iat": now,
        "iss": ISSUER,
        "jti": "jti-" + str(now) + "-" + str(id(body)),
        "method": "POST",
        "nbf": now,
        "path": path,
        "sub": SERVICE_SUBJECT,
        "workspace_id": WORKSPACE,
    }
    claims.update(overrides)
    return jwt.encode(
        claims,
        key,
        algorithm="EdDSA",
        headers={"kid": KID, "typ": "JWT"},
    )


def _request(
    token, *, body: bytes, path=PATH, client=("127.0.0.1", 12345)
):
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"x-vuoro-cloud-service-assertion", token.encode())],
            "server": ("tenant.local", 8080),
            "client": client,
            "state": {"vuoro_bounded_body": body},
        }
    )


def _verifier(tmp_path, key, *, replay_not_before=None):
    path = tmp_path / "cloud-service-public.pem"
    path.write_bytes(_pem_public(key))
    return CloudServiceAssertionVerifier(
        path,
        issuer=ISSUER,
        workspace_id=WORKSPACE,
        key_id=KID,
        replay_not_before=replay_not_before,
    )


def test_service_identity_is_separate_exact_body_route_and_per_process_replay(tmp_path):
    key = Ed25519PrivateKey.generate()
    body = b'{"schema":"operator-request-delivery-read/v1","request_id":"req-a"}'
    token = _token(key, body=body)
    verifier_a = _verifier(tmp_path, key)

    assert verifier_a(_request(token, body=body)) is True
    assert verifier_a(_request(token, body=body)) is False
    # Replay memory is process-local; do not claim cluster-wide one-use.
    assert _verifier(tmp_path, key)(_request(token, body=body)) is True


def test_restart_watermark_rejects_assertions_issued_before_startup(tmp_path):
    key = Ed25519PrivateKey.generate()
    body = b"{}"
    now = int(time.time())
    token = _token(
        key,
        body=body,
        iat=now - 5,
        nbf=now - 5,
        exp=now + 15,
        jti="before-startup",
    )
    verifier = _verifier(tmp_path, key)
    assert verifier._replay_not_before >= now - 2
    assert verifier(_request(token, body=body)) is False

    assert _verifier(tmp_path, key)(_request(token, body=body + b" ")) is False
    assert (
        _verifier(tmp_path, key)(_request(token, body=body, path=PATH + "/other"))
        is False
    )


def test_service_clock_skew_is_bounded_and_expiry_is_rechecked(tmp_path):
    key = Ed25519PrivateKey.generate()
    body = b"{}"
    now = int(time.time())
    verifier = _verifier(tmp_path, key, replay_not_before=now - 5)
    slight_future = _token(
        key,
        body=body,
        iat=now + 1,
        nbf=now + 1,
        exp=now + 20,
        jti="slight-future",
    )
    assert verifier(_request(slight_future, body=body)) is True

    expired = _token(
        key,
        body=body,
        iat=now - 1,
        nbf=now - 1,
        exp=now,
        jti="expired-now",
    )
    assert verifier(_request(expired, body=body)) is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": [SERVICE_AUDIENCE, "vuoro-service"]},
        {"sub": "human-user"},
        {"workspace_id": "another-workspace"},
        {"method": "GET"},
        {"path": "/internal/other"},
        {"actor": "caller-controlled-user"},
    ],
)
def test_service_identity_rejects_mixed_audience_user_claims_and_mismatches(
    tmp_path, overrides
):
    key = Ed25519PrivateKey.generate()
    body = b"{}"
    verifier = _verifier(tmp_path, key)
    token = _token(key, body=body, **overrides)
    assert verifier(_request(token, body=body)) is False


def test_user_gateway_assertion_cannot_satisfy_cloud_service_guard(tmp_path):
    service_key = Ed25519PrivateKey.generate()
    user_key = Ed25519PrivateKey.generate()
    verifier = _verifier(tmp_path, service_key)
    body = b"{}"
    token = _token(user_key, body=body)
    assert verifier(_request(token, body=body)) is False


@pytest.mark.parametrize(
    "client", [("10.42.0.4", 12345), ("not-an-ip", 12345), None]
)
def test_service_guard_rejects_non_loopback_proxy_peer(tmp_path, client):
    key = Ed25519PrivateKey.generate()
    body = b"{}"
    verifier = _verifier(tmp_path, key)
    token = _token(key, body=body)
    request = _request(token, body=body, client=client)
    assert verifier(request) is False


def test_service_guard_rejects_duplicate_header_and_bad_key_material(tmp_path):
    key = Ed25519PrivateKey.generate()
    body = b"{}"
    token = _token(key, body=body)
    verifier = _verifier(tmp_path, key)
    request = _request(token, body=body)
    request.scope["headers"].append(
        (b"x-vuoro-cloud-service-assertion", token.encode())
    )
    assert verifier(request) is False
    assert SERVICE_PATHS == {
        "/internal/operator-request-delivery/v1",
        "/internal/operator-request-delivery/v1/resolve",
        "/internal/operator-request-delivery/v1/receipts",
    }


def test_runtime_owner_routes_are_absent_by_default_and_missing_identity_fails_closed():
    app = FastAPI()
    _mount_private_recipient_delivery(
        app,
        environ={},
        work_pg=object(),
        work_dsn="unused",
        workspace_id=WORKSPACE,
        repository_ids=frozenset({"repo"}),
        environment_name="test",
        gateway_public_key_file=None,
        private_ingress_guard=None,
    )
    assert not any("operator-request-delivery" in route.path for route in app.routes)

    with pytest.raises(CompositionError, match="enable flag"):
        _mount_private_recipient_delivery(
            app,
            environ={"VUORO_OPERATOR_REQUEST_DELIVERY_ENABLED": "yes"},
            work_pg=object(),
            work_dsn="unused",
            workspace_id=WORKSPACE,
            repository_ids=frozenset({"repo"}),
            environment_name="test",
            gateway_public_key_file=None,
            private_ingress_guard=None,
        )
    with pytest.raises(CompositionError, match="gateway identity trust"):
        _mount_private_recipient_delivery(
            app,
            environ={"VUORO_OPERATOR_REQUEST_DELIVERY_ENABLED": "true"},
            work_pg=object(),
            work_dsn="unused",
            workspace_id=WORKSPACE,
            repository_ids=frozenset({"repo"}),
            environment_name="test",
            gateway_public_key_file=None,
            private_ingress_guard=None,
        )


def test_service_key_must_differ_from_gateway_and_admission_keys(tmp_path):
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "cloud-service-public.pem"
    path.write_bytes(_pem_public(key))
    with pytest.raises(ValueError, match="must be distinct"):
        CloudServiceAssertionVerifier(
            path,
            issuer=ISSUER,
            workspace_id=WORKSPACE,
            key_id=KID,
            forbidden_public_keys=(key.public_key(),),
        )


def test_empty_injected_replay_cache_is_preserved(tmp_path):
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "cloud-service-public.pem"
    path.write_bytes(_pem_public(key))
    cache = ReplayCache(name="injected-cloud-service-test")
    verifier = CloudServiceAssertionVerifier(
        path,
        issuer=ISSUER,
        workspace_id=WORKSPACE,
        key_id=KID,
        replay_cache=cache,
    )
    assert verifier._replay_cache is cache


def test_enabled_runtime_requires_injected_private_ingress_guard():
    app = FastAPI()
    with pytest.raises(CompositionError, match="trusted private ingress guard"):
        _mount_private_recipient_delivery(
            app,
            environ={"VUORO_OPERATOR_REQUEST_DELIVERY_ENABLED": "true"},
            work_pg=object(),
            work_dsn="unused",
            workspace_id=WORKSPACE,
            repository_ids=frozenset({"repo"}),
            environment_name="test",
            gateway_public_key_file="/test/gateway-public.pem",
            private_ingress_guard=None,
        )


def test_private_delivery_app_closes_bootstrap_store_on_disabled_and_enabled_paths(
    monkeypatch,
):
    from vuoro_service import composition

    failed_close = []
    bootstrap_store = SimpleNamespace(
        conn=SimpleNamespace(close=lambda: failed_close.append(True))
    )
    disabled = SimpleNamespace(state=SimpleNamespace())
    composition._attach_private_delivery_state(
        disabled,
        private_app=FastAPI(openapi_url=None, docs_url=None, redoc_url=None),
        work_store=bootstrap_store,
        mounted=False,
    )
    assert disabled.state.private_delivery_app is None
    assert disabled.state.private_delivery_bootstrap_store is bootstrap_store
    monkeypatch.setattr(composition, "create_composed_app", lambda **_kwargs: disabled)
    with pytest.raises(CompositionError, match="listener is disabled"):
        composition.create_private_delivery_app()
    assert failed_close == [True]

    private_app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    closed = []
    host_app = SimpleNamespace(
        state=SimpleNamespace(
            private_delivery_app=private_app,
            private_delivery_bootstrap_store=SimpleNamespace(
                conn=SimpleNamespace(close=lambda: closed.append(True))
            ),
        )
    )
    guard = object()
    observed = {}

    def create_composed_app(*, private_delivery_ingress_guard):
        observed["guard"] = private_delivery_ingress_guard
        return host_app

    monkeypatch.setattr(composition, "create_composed_app", create_composed_app)
    assert (
        composition.create_private_delivery_app(
            private_delivery_ingress_guard=guard
        )
        is private_app
    )
    assert observed["guard"] is guard
    assert closed == [True]


def test_private_delivery_factory_closes_store_when_real_composition_mount_is_disabled(
    monkeypatch,
):
    import sys
    from types import ModuleType

    from vuoro_service import composition

    closed = []
    work_store = SimpleNamespace(conn=SimpleNamespace(close=lambda: closed.append(True)))
    work_application = SimpleNamespace(
        served_runtime_ready=lambda: None,
        resolve_run_delegation_digest=lambda _digest: None,
    )

    class FakeWorkApplication:
        @staticmethod
        def postgres(_store):
            return work_application

    class FakeAuditAdapter:
        def __init__(self, **_kwargs):
            pass

        def compatibility(self):
            return {"compatible": True}

        def register(self, _registry):
            pass

    class FakeManifest:
        adapters = tuple(SimpleNamespace(domain=name) for name in ("work", "audit", "knowledge"))

        @staticmethod
        def pin(domain):
            return SimpleNamespace(
                adapter_module="auditctl.vuoro_adapter",
                register="VuoroAuditAdapter.register",
                domain=domain,
            )

    sprintctl = ModuleType("sprintctl")
    work_pg = ModuleType("sprintctl.pg")
    work_pg.get_connection = lambda _dsn: work_store
    work_migrations = ModuleType("sprintctl.pg_migrations")
    work_migrations.compatibility_handshake = lambda _store: {
        "remote_schema": {"actual": 1}
    }
    sprintctl.pg = work_pg
    sprintctl.pg_migrations = work_migrations
    sprintctl_application = ModuleType("sprintctl.application")
    sprintctl_application.ApplicationRejection = RuntimeError
    sprintctl_application.ProjectMemberApplication = object
    sprintctl_application.ProjectWorkApplication = object
    sprintctl_application.WorkApplication = FakeWorkApplication
    auditctl = ModuleType("auditctl")
    audit_adapter_module = ModuleType("auditctl.vuoro_adapter")
    audit_adapter_module.VuoroAuditAdapter = FakeAuditAdapter
    auditctl.vuoro_adapter = audit_adapter_module
    monkeypatch.setitem(sys.modules, "sprintctl", sprintctl)
    monkeypatch.setitem(sys.modules, "sprintctl.pg", work_pg)
    monkeypatch.setitem(sys.modules, "sprintctl.pg_migrations", work_migrations)
    monkeypatch.setitem(sys.modules, "sprintctl.application", sprintctl_application)
    monkeypatch.setitem(sys.modules, "auditctl", auditctl)
    monkeypatch.setitem(sys.modules, "auditctl.vuoro_adapter", audit_adapter_module)

    manifest = FakeManifest()
    monkeypatch.setattr(
        composition.CompositionManifest,
        "load",
        classmethod(lambda _cls, _path: manifest),
    )
    monkeypatch.setattr(composition, "verify_adapter_artifacts", lambda *_args: None)
    monkeypatch.setattr(composition, "verify_installed_composition", lambda *_args: None)
    monkeypatch.setattr(
        composition, "_load_work_resource_observation_authorizer", lambda _path: lambda *_: True
    )
    monkeypatch.setattr(composition, "_compatibility", lambda *_args: SimpleNamespace(state="compatible"))
    monkeypatch.setattr(composition, "_load_function", lambda _pin: lambda *_args, **_kwargs: None)
    monkeypatch.setattr(composition, "_bind_work_resource_visibility", lambda *_args: None)
    monkeypatch.setattr(composition, "_runtime_path", lambda *_args, **_kwargs: Path("/tmp/bindings.json"))
    monkeypatch.setattr(
        composition,
        "_load_project_binding_for_composition",
        lambda *_args, **_kwargs: SimpleNamespace(
            environment=None,
            repo_ids=("repo",),
            project_id="project",
            home_repo="repo",
            source_repository="https://example.test/repo",
            source_revision="a" * 40,
            source_path="project.toml",
            source_sha256="b" * 64,
        ),
    )
    monkeypatch.setattr(composition, "_validate_identity_mode", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(composition, "load_identities", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        composition,
        "compose_authorized_project_application",
        lambda *_args, **_kwargs: SimpleNamespace(invoke=lambda *_args: None),
    )
    monkeypatch.setattr(composition, "_pg_connection_factory", lambda _dsn: object())
    monkeypatch.setattr(composition, "_compose_knowledge", lambda *_args: SimpleNamespace(state="compatible"))
    monkeypatch.setattr(
        composition,
        "create_app",
        lambda **_kwargs: SimpleNamespace(state=SimpleNamespace()),
    )
    mounted = []
    monkeypatch.setattr(
        composition,
        "_mount_private_recipient_delivery",
        lambda *_args, **_kwargs: mounted.append(False) or False,
    )
    env = {
        "VUORO_ENVIRONMENT_NAME": "test",
        "VUORO_ENVIRONMENT_CLASS": "development",
        "VUORO_COMPOSITION_MANIFEST": "/tmp/manifest.json",
        "VUORO_WORK_RUNTIME_DSN": "postgresql://test",
        "VUORO_WORK_REPOSITORY_ID": "repo",
        "VUORO_WORK_RESOURCE_OBSERVERS_FILE": "/tmp/observers.json",
        "VUORO_PROJECT_BINDINGS_FILE": "/tmp/bindings.json",
        "VUORO_IDENTITIES_FILE": "/tmp/identities.json",
        "VUORO_AUDIT_RUNTIME_DSN": "postgresql://test",
        "VUORO_AUDIT_SCHEMA": "audit",
        "VUORO_ADAPTER_WHEEL_DIR": "/tmp/wheels",
        "VUORO_INSTALLED_COMPOSITION_PATH": "/tmp/installed.json",
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(CompositionError, match="listener is disabled"):
        composition.create_private_delivery_app()
    assert mounted == [False]
    assert closed == [True]

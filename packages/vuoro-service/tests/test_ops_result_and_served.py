"""vuoro-ops envelope, composition stamp, role-probe and catalog-probe."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

from vuoro_service.ops.result import DiagnosticResult, composition_stamp, parse_work_ref
from vuoro_service.ops.served import ProbeRefused, ServedReader, catalog_probe, load_served_profile, role_probe

PINS = Path(__file__).resolve().parents[1] / "composition" / "adapter-pins.json"
PINS_SHA = hashlib.sha256(PINS.read_bytes()).hexdigest()
REVISION = "a" * 64


def _profile(tmp_path: Path, **overrides) -> Path:
    document = {
        "schema_version": "vuoro-client-profile/v1",
        "id": "probe-test",
        "revision": 1,
        "source_environment_id": "workstation",
        "target": {"environment_id": "vuoro-test", "environment_class": "development",
                   "endpoint": "https://vuoro.invalid"},
        "credential_ref": "file:/nonexistent",
        "required_authorities": ["work:read", "work:evidence"],
        "production_endpoint_denied": False,
    }
    document.update(overrides)
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _handshake(**overrides):
    document = {
        "schema_version": "handshake/v1",
        "environment": {"name": "vuoro-test", "environment_class": "development", "constraints": [], "runbook_refs": []},
        "service_version": "9.9.9",
        "api_versions": {"meta": "v1", "catalog": "v1", "invoke": "v1"},
        "schema_versions": {},
        "client_protocol": {"minimum": 1, "maximum": 1},
        "catalog_revision": REVISION,
        "compatibility": {"state": "compatible", "domains": {
            "work": {"api_version": "work-api/v1", "schema_version": "sprintctl-work-compatibility/v1", "state": "compatible", "reason": None},
            "audit": {"api_version": "audit/v1", "schema_version": "audit-schema/v1", "state": "compatible", "reason": None},
        }},
        "service_release": {"distribution": "vuoro-service", "version": "9.9.9"},
    }
    document.update(overrides)
    return document


OPERATIONS = [
    {"name": "work.identity.current", "owning_domain": "work", "input_schema": {"type": "object"},
     "result_schema": {}, "required_authority": "work:read", "execution_semantics": "read", "idempotency": "not-allowed"},
    {"name": "work.evidence.list", "owning_domain": "work", "input_schema": {"type": "object"},
     "result_schema": {}, "required_authority": "work:evidence", "execution_semantics": "read", "idempotency": "not-allowed"},
    {"name": "work.item.done", "owning_domain": "work", "input_schema": {"type": "object", "required": ["id"]},
     "result_schema": {}, "required_authority": "work:write", "execution_semantics": "write", "idempotency": "required"},
    {"name": "audit.read.events", "owning_domain": "audit", "input_schema": {"type": "object"},
     "result_schema": {}, "required_authority": "audit:read", "execution_semantics": "read", "idempotency": "not-allowed"},
]


class FakeServer:
    def __init__(self, handshake=None, operations=None, denied=(), catalog_revision=REVISION):
        self.handshake = handshake or _handshake()
        self.catalog = {"schema_version": "operation-catalog/v1", "revision": catalog_revision,
                        "operations": operations if operations is not None else OPERATIONS}
        self.denied = set(denied)
        self.requests: list[tuple[str, str, dict]] = []

    def __call__(self, method, path, headers, body):
        self.requests.append((method, path, body))
        assert headers["Authorization"] == "Bearer test-token"
        if path == "/api/meta/v1/handshake":
            return 200, self.handshake
        if path == "/api/catalog/v1":
            return 200, self.catalog
        assert method == "POST" and path == "/api/invoke/v1"
        assert body["idempotency_key"] is None
        if body["operation"] in self.denied:
            return 403, {"status": "rejected", "error": {"code": "forbidden", "message": "no"}}
        if body["operation"] == "work.identity.current":
            return 200, {"status": "accepted", "result": {"repo_id": body["repo_id"], "actor": "tester"}}
        return 200, {"status": "accepted", "result": {"items": []}}


def _attestation(tmp_path: Path) -> Path:
    path = tmp_path / "installed-composition.json"
    path.write_text(json.dumps({"schema_version": "vuoro-installed-composition/v1", "verified": True,
                                "manifest_sha256": PINS_SHA, "distributions": []}))
    return path


def _document(result: DiagnosticResult, tmp_path: Path, attestation: Path | None = None) -> dict:
    return result.document(manifest_path=PINS, attestation_path=attestation or _attestation(tmp_path))


def test_envelope_carries_pins_digest_and_validates(tmp_path):
    document = _document(DiagnosticResult("role-probe", "1", "ok", {"x": 1},
                                          work_refs=[{"kind": "work-item", "id": "1"}] * 2), tmp_path,
                         tmp_path / "absent.json")
    assert document["schema_version"] == "vuoro-diagnostic-result/v1"
    assert document["composition"]["pins_sha256"] == PINS_SHA
    assert document["composition"]["manifest_valid"] is True
    assert {lock["distribution"] for lock in document["composition"]["locks"]} >= {"sprintctl", "auditctl"}
    assert document["work_refs"] == [{"kind": "work-item", "id": "1"}]
    assert "installed-composition-attestation-absent" in document["unknowns"]
    assert document["status"] == "attention"  # an unknown never leaves a result ok
    with pytest.raises(ValueError):
        DiagnosticResult("role-probe", "1", "maybe", {}).document(manifest_path=PINS)
    with pytest.raises(jsonschema.ValidationError):
        DiagnosticResult("not-an-operation", "1", "ok", {}).document(manifest_path=PINS, attestation_path=tmp_path / "x")


def test_installed_attestation_is_compared_with_the_pins(tmp_path):
    attestation = tmp_path / "installed.json"
    attestation.write_text(json.dumps({"schema_version": "vuoro-installed-composition/v1", "verified": True,
                                       "manifest_sha256": PINS_SHA, "distributions": []}))
    stamp, unknowns = composition_stamp(PINS, attestation)
    assert stamp["installed_attestation"]["matches_pins"] is True and unknowns == []
    attestation.write_text(json.dumps({"verified": True, "manifest_sha256": "0" * 64}))
    stamp, _ = composition_stamp(PINS, attestation)
    assert stamp["installed_attestation"]["matches_pins"] is False


def test_work_refs_are_kind_and_id():
    assert parse_work_ref("work-item:2600") == {"kind": "work-item", "id": "2600"}
    with pytest.raises(ValueError):
        parse_work_ref("2600")


def test_role_probe_matches_pins_and_observes_authorities_read_only(tmp_path):
    server = FakeServer()
    result = role_probe(_profile(tmp_path), manifest_path=PINS, repo_id="agentops",
                        transport=server, token="test-token", local_version="9.9.9")
    document = _document(result, tmp_path)
    assert document["status"] == "ok", document["result"]["mismatches"]
    assert document["result"]["identity"] == {"status": 200, "repo_id": "agentops", "actor": "tester"}
    authorities = document["result"]["required_authorities"]
    assert authorities["work:read"]["state"] == "granted"
    assert authorities["work:evidence"] == {"probe_operation": "work.evidence.list", "state": "granted", "status": 200}
    assert document["unknowns"] == []
    invoked = [body["operation"] for method, _, body in server.requests if method == "POST"]
    assert "work.item.done" not in invoked


def test_role_probe_reports_structured_mismatches(tmp_path):
    handshake = _handshake(environment={"name": "vuoro-other", "environment_class": "development"},
                           service_version="0.0.1")
    handshake["compatibility"]["domains"]["audit"] = {"api_version": "audit/v2", "state": "incompatible"}
    server = FakeServer(handshake=handshake, denied={"work.evidence.list"})
    document = _document(role_probe(_profile(tmp_path), manifest_path=PINS, repo_id="agentops",
                                    transport=server, token="test-token", local_version="9.9.9"), tmp_path)
    kinds = {item["kind"]: item for item in document["result"]["mismatches"]}
    assert kinds["environment"]["severity"] == "error"
    assert kinds["service-version"]["expected"] == "9.9.9" and kinds["service-version"]["observed"] == "0.0.1"
    assert kinds["api-version:audit"]["observed"] == "audit/v2"
    assert "domain-compatibility:audit" in kinds
    assert kinds["authority:work:evidence"]["observed"] == "denied"
    assert document["status"] == "error"


def test_reader_refuses_non_read_operations(tmp_path):
    reader = ServedReader(load_served_profile(_profile(tmp_path)), FakeServer(), "test-token")
    with pytest.raises(ProbeRefused):
        reader.read("work.item.done", {"id": 1}, repo_id="agentops")


@pytest.mark.parametrize("endpoint", [
    "http://localhost.evil.example", "http://127.0.0.1.nip.io", "http://user:pw@localhost",
    "https://token@vuoro.invalid", "ftp://localhost", "http://10.0.0.1", "http://localhost/?x=1",
])
def test_endpoint_must_be_https_or_exact_loopback_without_userinfo(tmp_path, endpoint):
    path = _profile(tmp_path, target={"environment_id": "t", "environment_class": "development", "endpoint": endpoint})
    with pytest.raises(ProbeRefused):
        load_served_profile(path)


@pytest.mark.parametrize("endpoint", ["http://localhost:8080", "http://127.0.0.1", "http://[::1]:9", "https://v.invalid/x"])
def test_loopback_and_https_endpoints_are_accepted(tmp_path, endpoint):
    path = _profile(tmp_path, target={"environment_id": "t", "environment_class": "development", "endpoint": endpoint})
    assert load_served_profile(path).endpoint == endpoint.rstrip("/")


def test_redirects_are_never_followed_with_the_credential():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from vuoro_service.ops.served import urllib_transport

    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append((self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    status, _ = urllib_transport(f"http://127.0.0.1:{server.server_port}")("GET", "/api/meta/v1/handshake",
                                                                          {"Authorization": "Bearer t"}, None)
    thread.join(5)
    server.server_close()
    assert status == 302 and hits == [("/api/meta/v1/handshake", "Bearer t")]


def test_profile_that_denies_production_is_refused(tmp_path):
    path = _profile(tmp_path, target={"environment_id": "p", "environment_class": "production",
                                      "endpoint": "https://p.invalid"}, production_endpoint_denied=True)
    with pytest.raises(ProbeRefused):
        load_served_profile(path)


def test_catalog_probe_compares_expected_identity(tmp_path):
    server = FakeServer()
    ok = _document(catalog_probe(_profile(tmp_path), manifest_path=PINS, transport=server, token="test-token",
                                 expected={"revision": REVISION, "total": 4}, local_version="9.9.9"), tmp_path)
    assert ok["status"] == "ok"
    assert ok["result"]["catalog"]["domain_counts"] == {"audit": 1, "work": 3}
    bad = _document(catalog_probe(_profile(tmp_path), manifest_path=PINS, transport=FakeServer(catalog_revision="b" * 64),
                                  token="test-token", expected={"revision": REVISION}, local_version="9.9.9"), tmp_path)
    kinds = {item["kind"] for item in bad["result"]["mismatches"]}
    assert {"catalog-revision-vs-handshake", "catalog-revision"} <= kinds and bad["status"] == "error"


def test_catalog_probe_derives_expected_operations_from_an_image(tmp_path):
    derived = {"revision": REVISION, "total": 5, "domain_counts": {"work": 4, "audit": 1},
               "operations": sorted([op["name"] for op in OPERATIONS] + ["work.missing.op"])}
    calls = []

    def runner(image, script, env):
        calls.append((image, env))
        assert "def observe" in script
        return {"catalog": derived, "manifest_sha256": "f" * 64}

    document = _document(catalog_probe(_profile(tmp_path), manifest_path=PINS, transport=FakeServer(),
                                       token="test-token", expected_from_image="img@sha256:" + "1" * 64,
                                       image_runner=runner, local_version="9.9.9"), tmp_path)
    assert calls == [("img@sha256:" + "1" * 64, {"VUORO_OPS_DERIVE_CATALOG": "1"})]
    kinds = {item["kind"]: item for item in document["result"]["mismatches"]}
    assert kinds["catalog-operations"]["expected"] == {"missing_from_served": ["work.missing.op"]}
    assert kinds["image-manifest-vs-pins"]["expected"] == PINS_SHA
    assert document["result"]["expected_source"].startswith("derived:")

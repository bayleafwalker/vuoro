"""The knowledge.* operations served end to end through the real Vuoro service shell.

Catalog tests run without a database. Invocation tests use the disposable
PostgreSQL fixture (``VUORO_WORKSPACE_TEST_PG_BIN`` or
``VUORO_WORKSPACE_TEST_DSN``) and go through ``POST /api/invoke/v1``: identity
resolution, the shell's ``required_authority`` gate, idempotency envelope
rules, schema validation and then the store's membership intersection.
"""

from __future__ import annotations

import base64
import hashlib
import itertools
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("vuoro_service", reason="served-adapter tests need vuoro-service (test extra)")
pytest.importorskip("httpx", reason="served-adapter tests need httpx (test extra)")

from fastapi.testclient import TestClient  # noqa: E402

from vuoro_service.app import ServiceSettings, create_app  # noqa: E402
from vuoro_service.catalog import CatalogRegistry  # noqa: E402
from vuoro_service.identity import Identity, StaticBearerIdentityResolver  # noqa: E402
from vuoro_workspace import FsObjectStore  # noqa: E402
from vuoro_workspace.vuoro_adapter import (  # noqa: E402
    MAX_UPLOAD_BYTES,
    KnowledgeApplication,
    catalog_operation_specs,
    principal_from_identity,
    register_knowledge_catalog,
)
from workspace_fixtures import pg_conn, pg_dsn, schema  # noqa: E402,F401  (fixtures)

ENV = "knowledge-test"
EVERY = ("knowledge:read", "knowledge:contribute", "knowledge:review", "knowledge:admin")

#: Pinned so any change to a knowledge operation's served contract is a
#: deliberate, reviewed catalog revision change.
KNOWLEDGE_CATALOG_REVISION = "133e24ad0490a7e3302d510f22c59b7f3dd37456d7ae2892c4bd8d4d2fd21767"
EXPECTED_OPERATIONS = {
    "knowledge.workspace.create-v1": ("knowledge:admin", "write", "required"),
    "knowledge.member.set-v1": ("knowledge:admin", "write", "required"),
    "knowledge.item.create-v1": ("knowledge:contribute", "write", "required"),
    "knowledge.revision.draft-v1": ("knowledge:contribute", "write", "required"),
    "knowledge.proposal.submit-v1": ("knowledge:contribute", "write", "required"),
    "knowledge.proposal.review-v1": ("knowledge:review", "write", "required"),
    "knowledge.proposal.withdraw-v1": ("knowledge:contribute", "write", "required"),
    "knowledge.artifact.begin-v1": ("knowledge:contribute", "write", "required"),
    "knowledge.artifact.upload-v1": ("knowledge:contribute", "write", "not-allowed"),
    "knowledge.artifact.finalize-v1": ("knowledge:contribute", "write", "required"),
    "knowledge.artifact.review-v1": ("knowledge:review", "write", "required"),
    "knowledge.item.read-v1": ("knowledge:read", "read", "not-allowed"),
    "knowledge.artifact.download-v1": ("knowledge:read", "read", "not-allowed"),
    "knowledge.workspace.search-v1": ("knowledge:read", "read", "not-allowed"),
    "knowledge.workspace.changes-v1": ("knowledge:read", "read", "not-allowed"),
    "knowledge.item.delete-v1": ("knowledge:admin", "write", "required"),
    "knowledge.artifact.delete-v1": ("knowledge:admin", "write", "required"),
    "knowledge.workspace.reconcile-v1": ("knowledge:admin", "write", "not-allowed"),
}


def _registry(tmp_path: Path, connect=lambda: None, schema_name: str = "knowledge") -> CatalogRegistry:
    registry = CatalogRegistry()
    application = KnowledgeApplication(connect, FsObjectStore(tmp_path / "objects"), schema=schema_name)
    register_knowledge_catalog(registry, application)
    return registry


# ------------------------------------------------------------------ catalog


def test_catalog_registers_every_knowledge_operation_with_its_authority(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    catalog = registry.catalog().model_dump(mode="json")
    served = {
        op["name"]: (op["required_authority"], op["execution_semantics"], op["idempotency"])
        for op in catalog["operations"]
    }
    assert served == EXPECTED_OPERATIONS
    assert all(op["owning_domain"] == "knowledge" and op["repo_scoped"] is False for op in catalog["operations"])
    # Retryable writes carry idempotency; the two convergent writes refuse a key.
    for name, (_, semantics, idempotency) in EXPECTED_OPERATIONS.items():
        if semantics == "read":
            assert idempotency == "not-allowed"
        elif name not in {"knowledge.artifact.upload-v1", "knowledge.workspace.reconcile-v1"}:
            assert idempotency == "required", name


def test_catalog_revision_is_stable_and_pinned(tmp_path: Path) -> None:
    first = _registry(tmp_path).revision
    second = _registry(tmp_path / "again").revision
    assert first == second == KNOWLEDGE_CATALOG_REVISION
    # Specs are isolated copies: mutating one cannot change the next registration.
    specs = catalog_operation_specs()
    specs[0]["input_schema"]["properties"].clear()
    assert _registry(tmp_path / "third").revision == KNOWLEDGE_CATALOG_REVISION


def test_principal_mapping_uses_principal_id_and_the_agent_rule() -> None:
    human = principal_from_identity(Identity("alice", ENV, frozenset({*EVERY, "work:read"}), principal_id="iss:alice:0"))
    assert human.principal_id == "iss:alice:0" and not human.is_agent
    assert human.authorities == frozenset(EVERY)
    oauth = principal_from_identity(Identity("bot", ENV, frozenset(EVERY), principal_id="iss:bot:0", client_id="c1"))
    marked = principal_from_identity(
        Identity("rt", ENV, frozenset({*EVERY, "knowledge:agent"}), principal_id="iss:rt:0"))
    assert oauth.is_agent and marked.is_agent
    assert "knowledge:agent" not in marked.authorities
    from vuoro_workspace import WorkspaceError

    with pytest.raises(WorkspaceError):
        principal_from_identity(Identity("anon", ENV, frozenset(EVERY)))


# ----------------------------------------------------------------- served


class Served:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.ids = itertools.count()

    def call(self, token: str, operation: str, arguments: dict[str, Any], *, key: str | None = None,
             auto_key: bool = True) -> tuple[int, dict[str, Any]]:
        if key is None and auto_key and EXPECTED_OPERATIONS[operation][2] == "required":
            key = f"auto-key-{next(self.ids):06d}"
        body: dict[str, Any] = {"request_id": f"r{next(self.ids)}", "operation": operation, "arguments": arguments}
        if key is not None:
            body["idempotency_key"] = key
        response = self.client.post("/api/invoke/v1", json=body,
                                    headers={"Authorization": f"Bearer {token}", "X-Vuoro-Client-Protocol": "1"})
        return response.status_code, response.json()

    def ok(self, token: str, operation: str, arguments: dict[str, Any], **kw: Any) -> dict[str, Any]:
        status, body = self.call(token, operation, arguments, **kw)
        assert status == 200 and body["status"] == "accepted", body
        return body["result"]

    def code(self, token: str, operation: str, arguments: dict[str, Any], **kw: Any) -> tuple[int, str]:
        status, body = self.call(token, operation, arguments, **kw)
        assert body["status"] == "rejected", body
        return status, body["error"]["code"]


def _identity(name: str, authorities: tuple[str, ...] = EVERY, **extra: Any) -> Identity:
    return Identity(name, ENV, frozenset(authorities), principal_id=f"test:{name}:0", **extra)


TOKENS = {
    "t-admin": _identity("admin"),
    "t-reader": _identity("reader"),
    "t-contrib": _identity("contrib"),
    "t-reviewer": _identity("reviewer"),
    "t-outsider": _identity("outsider"),
    "t-agent": _identity("agent", client_id="mcp-client"),
    "t-noknow": _identity("noknow", ("work:read",)),
    "t-readonly-cred": _identity("readonly", ("knowledge:read",)),
}


@pytest.fixture
def served(pg_dsn: str, pg_conn: Any, schema: str, tmp_path: Path) -> Any:
    import psycopg
    from vuoro_workspace import migrate_schema

    migrate_schema(pg_conn, schema)
    registry = _registry(tmp_path, lambda: psycopg.connect(pg_dsn, autocommit=True), schema)
    app = create_app(
        settings=ServiceSettings(environment_name=ENV, compatibility_state="compatible"),
        registry=registry,
        identity_resolver=StaticBearerIdentityResolver(TOKENS),
    )
    s = Served(TestClient(app))
    s.ws = s.ok("t-admin", "knowledge.workspace.create-v1", {"name": "Research"})["workspace_id"]
    for name, role in (("reader", "reader"), ("contrib", "contributor"), ("reviewer", "reviewer"),
                       ("agent", "reviewer"), ("readonly", "administrator")):
        s.ok("t-admin", "knowledge.member.set-v1",
             {"workspace_id": s.ws, "principal_id": f"test:{name}:0", "role": role})
    return s


def test_missing_knowledge_authority_is_refused_by_the_shell(served) -> None:
    s = served
    for op, args in (("knowledge.workspace.search-v1", {"workspace_id": s.ws, "query": "x"}),
                     ("knowledge.item.create-v1", {"workspace_id": s.ws, "title": "t", "body_md": "b"})):
        assert s.code("t-noknow", op, args) == (403, "authority-required")
    # A read-only credential cannot reach a contribute operation even with an
    # administrator membership: the credential bounds the role.
    assert s.code("t-readonly-cred", "knowledge.item.create-v1",
                  {"workspace_id": s.ws, "title": "t", "body_md": "b"}) == (403, "authority-required")


def test_membership_is_intersected_on_every_call(served) -> None:
    s = served
    search = {"workspace_id": s.ws, "query": "anything"}
    assert s.code("t-outsider", "knowledge.workspace.search-v1", search) == (403, "knowledge-forbidden")
    # A reader member holding every credential authority is still only a reader.
    assert s.code("t-reader", "knowledge.item.create-v1",
                  {"workspace_id": s.ws, "title": "t", "body_md": "b"}) == (403, "knowledge-forbidden")
    item = s.ok("t-contrib", "knowledge.item.create-v1", {"workspace_id": s.ws, "title": "Draft", "body_md": "b"})
    prop = s.ok("t-contrib", "knowledge.proposal.submit-v1",
                {"workspace_id": s.ws, "revision_id": item["revision_id"], "expected_base_revision_id": None})
    # An OAuth-client (agent) identity is capped at contributor despite a reviewer membership.
    assert s.code("t-agent", "knowledge.proposal.review-v1",
                  {"workspace_id": s.ws, "proposal_id": prop["proposal_id"], "decision": "approve"}) \
        == (403, "knowledge-forbidden")
    # Removing a member refuses their very next call.
    s.ok("t-reader", "knowledge.workspace.changes-v1", {"workspace_id": s.ws})
    s.ok("t-admin", "knowledge.member.set-v1",
         {"workspace_id": s.ws, "principal_id": "test:reader:0", "role": None})
    assert s.code("t-reader", "knowledge.workspace.changes-v1", {"workspace_id": s.ws}) == (403, "knowledge-forbidden")


def test_idempotency_replay_and_conflict(served) -> None:
    s = served
    args = {"workspace_id": s.ws, "title": "Once", "body_md": "body"}
    assert s.code("t-contrib", "knowledge.item.create-v1", args, auto_key=False) == (400, "idempotency-key-required")
    first = s.ok("t-contrib", "knowledge.item.create-v1", args, key="item-create-0001")
    replay = s.ok("t-contrib", "knowledge.item.create-v1", args, key="item-create-0001")
    assert replay == first
    assert s.code("t-contrib", "knowledge.item.create-v1", {**args, "body_md": "other"},
                  key="item-create-0001") == (409, "knowledge-idempotency-conflict")
    assert s.code("t-contrib", "knowledge.item.create-v1", args, key="short") == (422, "knowledge-invalid-argument")
    assert s.code("t-contrib", "knowledge.workspace.search-v1", {"workspace_id": s.ws, "query": "x"},
                  key="not-allowed-key") == (400, "idempotency-key-not-allowed")


def test_upload_finalize_review_download_round_trip(served) -> None:
    s = served
    data = b"# Findings\n\nRetention improved after onboarding changes.\n"
    begun = s.ok("t-contrib", "knowledge.artifact.begin-v1", {
        "workspace_id": s.ws, "expected_sha256": hashlib.sha256(data).hexdigest(), "expected_size": len(data),
        "media_type": "text/markdown", "name": "findings.md"})
    assert begun["processing_state"] == "reserved" and "object_key" not in begun
    uploaded = s.ok("t-contrib", "knowledge.artifact.upload-v1", {
        "workspace_id": s.ws, "version_id": begun["version_id"],
        "content_base64": base64.b64encode(data).decode()})
    assert uploaded == {"version_id": begun["version_id"], "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()}
    fin_args = {"workspace_id": s.ws, "version_id": begun["version_id"]}
    final = s.ok("t-contrib", "knowledge.artifact.finalize-v1", fin_args, key="finalize-0001")
    assert final["processing_state"] == "ready" and final["review_state"] == "unreviewed"
    assert s.ok("t-contrib", "knowledge.artifact.finalize-v1", fin_args, key="finalize-0001") == final
    download = {"workspace_id": s.ws, "artifact_id": begun["artifact_id"]}
    # Unreviewed: invisible to a reader, visible to its contributor by exact version.
    assert s.code("t-reader", "knowledge.artifact.download-v1", download) == (404, "knowledge-not-found")
    working = s.ok("t-contrib", "knowledge.artifact.download-v1", {**download, "version": begun["version_id"]})
    assert base64.b64decode(working["content_base64"]) == data
    assert s.code("t-contrib", "knowledge.artifact.review-v1",
                  {**fin_args, "decision": "approve"}) == (403, "knowledge-forbidden")  # own upload
    s.ok("t-reviewer", "knowledge.artifact.review-v1", {**fin_args, "decision": "approve"})
    got = s.ok("t-reader", "knowledge.artifact.download-v1", download)
    assert base64.b64decode(got["content_base64"]) == data
    assert got["media_type"] == "text/markdown" and got["sha256"] == hashlib.sha256(data).hexdigest()
    assert got["review_state"] == "approved" and got["extraction_state"] == "ready"
    assert got["content_disposition"] == "attachment" and final["content_disposition"] == "attachment"
    assert final["extraction_truncated"] is False


def test_active_content_is_never_served_as_its_declared_type(served) -> None:
    s = served
    data = b"<html><script>alert(1)</script></html>"
    begun = s.ok("t-contrib", "knowledge.artifact.begin-v1", {
        "workspace_id": s.ws, "expected_sha256": hashlib.sha256(data).hexdigest(), "expected_size": len(data),
        "media_type": "text/html", "name": "page.html"})
    s.ok("t-contrib", "knowledge.artifact.upload-v1", {
        "workspace_id": s.ws, "version_id": begun["version_id"], "content_base64": base64.b64encode(data).decode()})
    s.ok("t-contrib", "knowledge.artifact.finalize-v1", {"workspace_id": s.ws, "version_id": begun["version_id"]})
    got = s.ok("t-contrib", "knowledge.artifact.download-v1",
               {"workspace_id": s.ws, "artifact_id": begun["artifact_id"], "version": begun["version_id"]})
    assert got["media_type"] != "text/html"
    assert got["content_disposition"] == "attachment"
    assert base64.b64decode(got["content_base64"]) == data


def test_cursor_and_body_limits_map_to_stable_codes(served) -> None:
    s = served
    import json as _json

    ahead = base64.urlsafe_b64encode(_json.dumps({"v": 1, "w": s.ws, "s": 10_000},
                                                 separators=(",", ":")).encode()).decode().rstrip("=")
    assert s.code("t-reader", "knowledge.workspace.changes-v1", {"workspace_id": s.ws, "cursor": ahead}) \
        == (400, "knowledge-invalid-cursor")
    assert s.code("t-reader", "knowledge.workspace.changes-v1", {"workspace_id": s.ws, "cursor": "garbage"}) \
        == (400, "knowledge-invalid-cursor")
    assert s.code("t-contrib", "knowledge.item.create-v1",
                  {"workspace_id": s.ws, "title": "Big", "body_md": "x" * 1_000_001}) \
        == (422, "schema-validation-failed")


def test_upload_cap_and_bad_base64_are_refused(served) -> None:
    s = served
    too_big = {"workspace_id": s.ws, "expected_sha256": "0" * 64, "expected_size": MAX_UPLOAD_BYTES + 1,
               "media_type": "text/plain", "name": "big.txt"}
    assert s.code("t-contrib", "knowledge.artifact.begin-v1", too_big) == (422, "schema-validation-failed")
    begun = s.ok("t-contrib", "knowledge.artifact.begin-v1", {**too_big, "expected_size": 3})
    assert s.code("t-contrib", "knowledge.artifact.upload-v1", {
        "workspace_id": s.ws, "version_id": begun["version_id"], "content_base64": "%%%"}) \
        == (422, "knowledge-invalid-argument")


def _publish(s: Served, title: str, body: str) -> dict[str, Any]:
    item = s.ok("t-contrib", "knowledge.item.create-v1", {"workspace_id": s.ws, "title": title, "body_md": body})
    prop = s.ok("t-contrib", "knowledge.proposal.submit-v1",
                {"workspace_id": s.ws, "revision_id": item["revision_id"], "expected_base_revision_id": None})
    s.ok("t-reviewer", "knowledge.proposal.review-v1",
         {"workspace_id": s.ws, "proposal_id": prop["proposal_id"], "decision": "approve"})
    return item


def test_search_scope_filtering(served) -> None:
    s = served
    published = _publish(s, "Churn summary", "Retention improved after onboarding changes.")
    s.ok("t-contrib", "knowledge.item.create-v1",
         {"workspace_id": s.ws, "title": "Churn draft", "body_md": "Unreviewed churn notes."})
    query = {"workspace_id": s.ws, "query": "churn"}
    approved = s.ok("t-reader", "knowledge.workspace.search-v1", query)
    assert approved["total"] == 1 and approved["results"][0]["id"] == published["item_id"]
    assert approved["scope"] == "approved" and approved["index_watermark"] > 0
    assert s.code("t-reader", "knowledge.workspace.search-v1", {**query, "scope": "working"}) \
        == (403, "knowledge-forbidden")
    assert s.ok("t-reader", "knowledge.workspace.search-v1", {**query, "scope": "history"})["total"] == 1
    assert s.ok("t-contrib", "knowledge.workspace.search-v1", {**query, "scope": "working"})["total"] == 2
    read = s.ok("t-reader", "knowledge.item.read-v1", {"workspace_id": s.ws, "item_id": published["item_id"]})
    assert read["publication_state"] == "approved" and read["revision_id"] == published["revision_id"]


def test_stale_base_is_refused_with_the_current_revision(served) -> None:
    s = served
    item = _publish(s, "Policy", "Version one.")
    proposals = []
    for body in ("Version two A.", "Version two B."):
        rev = s.ok("t-contrib", "knowledge.revision.draft-v1",
                   {"workspace_id": s.ws, "item_id": item["item_id"], "title": "Policy", "body_md": body})
        proposals.append(s.ok("t-contrib", "knowledge.proposal.submit-v1", {
            "workspace_id": s.ws, "revision_id": rev["revision_id"],
            "expected_base_revision_id": item["revision_id"]}))
    s.ok("t-reviewer", "knowledge.proposal.review-v1",
         {"workspace_id": s.ws, "proposal_id": proposals[0]["proposal_id"], "decision": "approve"})
    status, body = s.call("t-reviewer", "knowledge.proposal.review-v1",
                          {"workspace_id": s.ws, "proposal_id": proposals[1]["proposal_id"], "decision": "approve"})
    assert status == 409 and body["error"]["code"] == "knowledge-stale-base"
    details = body["error"]["details"]
    assert details["current_revision_id"] == proposals[0]["revision_id"]
    assert details["current_revision"]["body_md"] == "Version two A."


def test_delete_tombstones_and_changes_feed(served) -> None:
    s = served
    item = _publish(s, "Gone soon", "Temporary knowledge.")
    feed = s.ok("t-reader", "knowledge.workspace.changes-v1", {"workspace_id": s.ws})
    s.ok("t-admin", "knowledge.item.delete-v1", {"workspace_id": s.ws, "item_id": item["item_id"]})
    after = s.ok("t-reader", "knowledge.workspace.changes-v1", {"workspace_id": s.ws, "cursor": feed["cursor"]})
    assert [c["tombstone"] for c in after["changes"]] == [True]
    assert s.code("t-reader", "knowledge.item.read-v1", {"workspace_id": s.ws, "item_id": item["item_id"]}) \
        == (404, "knowledge-not-found")
    report = s.ok("t-admin", "knowledge.workspace.reconcile-v1", {"workspace_id": s.ws})
    assert report["workspace_id"] == s.ws and report["reset_verifications"] == []


def test_compatibility_gate_is_read_only(pg_dsn: str, pg_conn: Any, schema: str, tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg
    from vuoro_workspace import migrate_schema

    monkeypatch.setenv("VUORO_WORKSPACE_SCHEMA_MODE", "operator-migrate")
    application = KnowledgeApplication(lambda: psycopg.connect(pg_dsn, autocommit=True),
                                       FsObjectStore(tmp_path / "objects"), schema=schema)
    before = application.compatibility()
    assert before["compatible"] is False and before["reason"]
    # The gate never migrates, whatever the store's startup-mode variable says.
    assert pg_conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname = %s", (schema,)).fetchone()[0] == 0
    migrate_schema(pg_conn, schema)
    after = application.compatibility()
    assert after["compatible"] is True
    assert after["domain_api_version"] == "vuoro-workspace/v1"
    assert after["schema_version"] == "knowledge-schema/v1"

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
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
from typing import Any

import pytest

from workspace_fixtures import require_or_skip

try:
    import httpx  # noqa: F401
    import vuoro_service  # noqa: F401
except ImportError:  # pragma: no cover - depends on the test extra
    require_or_skip("served-adapter tests need the vuoro-workspace test extra (vuoro-service, httpx)")

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
    store_idempotency_key,
)
from workspace_fixtures import pg_conn, pg_dsn, schema  # noqa: E402,F401  (fixtures)

ENV = "knowledge-test"
HUMAN = "knowledge:human"
EVERY = ("knowledge:read", "knowledge:contribute", "knowledge:review", "knowledge:admin")

#: Pinned so any change to a knowledge operation's served contract is a
#: deliberate, reviewed catalog revision change.
KNOWLEDGE_CATALOG_REVISION = "e651dd978f73772e520f28f5ab9b6f46b77578ecc459d171d01eb4c204e75e49"
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


class LiveClock:
    """Real time plus a test-controlled offset (object mtimes stay real)."""

    def __init__(self) -> None:
        self.offset = timedelta(0)

    def __call__(self) -> datetime:
        return datetime.now(timezone.utc) + self.offset


def _registry(tmp_path: Path, connect=lambda: None, schema_name: str = "knowledge",
              runtime_role: str = "vuoro_knowledge_runtime", **app_kwargs: Any) -> CatalogRegistry:
    registry = CatalogRegistry()
    application = KnowledgeApplication(connect, FsObjectStore(tmp_path / "objects"), schema=schema_name,
                                       runtime_role=runtime_role, **app_kwargs)
    register_knowledge_catalog(registry, application)
    registry.application = application  # type: ignore[attr-defined]  (test handle)
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


def test_principal_mapping_fails_closed_to_agent() -> None:
    human = principal_from_identity(
        Identity("alice", ENV, frozenset({*EVERY, HUMAN, "work:read"}), principal_id="iss:alice:0"))
    assert human.principal_id == "iss:alice:0" and not human.is_agent
    assert human.authorities == frozenset(EVERY)  # markers and other domains are not passed on
    unmarked = principal_from_identity(Identity("tok", ENV, frozenset(EVERY), principal_id="iss:tok:0"))
    oauth_with_marker = principal_from_identity(
        Identity("bot", ENV, frozenset({*EVERY, HUMAN}), principal_id="iss:bot:0", client_id="c1"))
    grant_with_marker = principal_from_identity(
        Identity("bot", ENV, frozenset({*EVERY, HUMAN}), principal_id="iss:bot:0", grant_id="g1"))
    assert unmarked.is_agent and oauth_with_marker.is_agent and grant_with_marker.is_agent
    from vuoro_workspace import WorkspaceError

    with pytest.raises(WorkspaceError):
        principal_from_identity(Identity("anon", ENV, frozenset({*EVERY, HUMAN})))


def test_envelope_idempotency_keys_map_deterministically_onto_the_store_grammar() -> None:
    from types import SimpleNamespace

    from vuoro_workspace.store import IDEMPOTENCY_KEY_RE

    assert store_idempotency_key(SimpleNamespace(idempotency_key=None)) is None
    assert store_idempotency_key(SimpleNamespace(idempotency_key="plain-key-0001")) == "plain-key-0001"
    for raw in ("short", "x" * 200, "spaces are fine here", "ünïcode-key"):
        derived = store_idempotency_key(SimpleNamespace(idempotency_key=raw))
        assert IDEMPOTENCY_KEY_RE.fullmatch(derived) and derived.startswith("vk1:")
        assert derived == store_idempotency_key(SimpleNamespace(idempotency_key=raw))
    assert store_idempotency_key(SimpleNamespace(idempotency_key="short")) != \
        store_idempotency_key(SimpleNamespace(idempotency_key="short2"))


def test_runtime_role_is_required(tmp_path: Path) -> None:
    for role in (None, "", "  "):
        with pytest.raises((ValueError, TypeError)):
            KnowledgeApplication(lambda: None, FsObjectStore(tmp_path), runtime_role=role)  # type: ignore[arg-type]


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
    # Operator-minted static identities for humans carry the explicit marker.
    "t-admin": _identity("admin", (*EVERY, HUMAN)),
    "t-reader": _identity("reader", (*EVERY, HUMAN)),
    "t-contrib": _identity("contrib", (*EVERY, HUMAN)),
    "t-reviewer": _identity("reviewer", (*EVERY, HUMAN)),
    "t-outsider": _identity("outsider", (*EVERY, HUMAN)),
    # An OAuth client stays an agent even if a human marker leaks onto it.
    "t-agent": _identity("agent", (*EVERY, HUMAN), client_id="mcp-client"),
    # A workspace-token style identity without the marker is an agent.
    "t-token": _identity("token"),
    "t-noknow": _identity("noknow", ("work:read",)),
    "t-readonly-cred": _identity("readonly", ("knowledge:read", HUMAN)),
}
RUNTIME_PASSWORD = "kw-test-only"


def _runtime_role(pg_conn: Any, schema: str) -> str:
    """A non-owner, non-superuser login role with only runtime DML on the schema."""
    role = f"{schema}_rt"
    pg_conn.execute(f'DROP ROLE IF EXISTS "{role}"')
    pg_conn.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{RUNTIME_PASSWORD}'")
    pg_conn.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
    pg_conn.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
    return role


def _connect_as(pg_dsn: str, role: str):
    import psycopg
    from psycopg.conninfo import make_conninfo

    dsn = make_conninfo(pg_dsn, user=role, password=RUNTIME_PASSWORD)
    return lambda: psycopg.connect(dsn, autocommit=True)


@pytest.fixture
def served(pg_dsn: str, pg_conn: Any, schema: str, tmp_path: Path) -> Any:  # noqa: C901
    import psycopg
    from vuoro_workspace import migrate_schema

    migrate_schema(pg_conn, schema)
    role = _runtime_role(pg_conn, schema)
    clock = LiveClock()
    registry = _registry(tmp_path, _connect_as(pg_dsn, role), schema, runtime_role=role,
                         store_kwargs={"clock": clock})
    app = create_app(
        settings=ServiceSettings(environment_name=ENV, compatibility_state="compatible"),
        registry=registry,
        identity_resolver=StaticBearerIdentityResolver(TOKENS),
    )
    s = Served(TestClient(app))
    s.dsn, s.schema, s.clock, s.role = pg_dsn, schema, clock, role
    s.application, s.objects_root = registry.application, tmp_path / "objects"
    s.ws = s.ok("t-admin", "knowledge.workspace.create-v1", {"name": "Research"})["workspace_id"]
    for name, member_role in (("reader", "reader"), ("contrib", "contributor"), ("reviewer", "reviewer"),
                              ("agent", "reviewer"), ("token", "reviewer"), ("readonly", "administrator")):
        s.ok("t-admin", "knowledge.member.set-v1",
             {"workspace_id": s.ws, "principal_id": f"test:{name}:0", "role": member_role})
    yield s
    pg_conn.execute(f'DROP OWNED BY "{role}"')
    pg_conn.execute(f'DROP ROLE IF EXISTS "{role}"')


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
    review = {"workspace_id": s.ws, "proposal_id": prop["proposal_id"], "decision": "approve"}
    # Agents are capped at contributor despite a reviewer membership: an OAuth
    # client (even carrying the human marker) and an unmarked token identity.
    assert s.code("t-agent", "knowledge.proposal.review-v1", review) == (403, "knowledge-forbidden")
    assert s.code("t-token", "knowledge.proposal.review-v1", review) == (403, "knowledge-forbidden")
    # A static identity with the explicit human marker and a reviewer role can review.
    assert s.ok("t-reviewer", "knowledge.proposal.review-v1", review)["state"] == "approved"
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
    # Envelope keys outside the store grammar are hashed deterministically, so
    # they replay and conflict exactly like grammar-conforming keys.
    for raw in ("short", "k" * 200):
        once = s.ok("t-contrib", "knowledge.item.create-v1", {**args, "title": raw[:20]}, key=raw)
        assert s.ok("t-contrib", "knowledge.item.create-v1", {**args, "title": raw[:20]}, key=raw) == once
        assert s.code("t-contrib", "knowledge.item.create-v1", {**args, "title": "changed"}, key=raw) \
            == (409, "knowledge-idempotency-conflict")
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
    for scope in ("working", "history"):
        assert s.code("t-reader", "knowledge.workspace.search-v1", {**query, "scope": scope}) \
            == (403, "knowledge-forbidden")
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


def test_compatibility_gate_is_read_only_and_refuses_owner_dsns(pg_dsn: str, pg_conn: Any, schema: str,
                                                               tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg
    from vuoro_workspace import migrate_schema

    monkeypatch.setenv("VUORO_WORKSPACE_SCHEMA_MODE", "operator-migrate")
    objects = FsObjectStore(tmp_path / "objects")
    owner = KnowledgeApplication(lambda: psycopg.connect(pg_dsn, autocommit=True), objects, schema=schema,
                                 runtime_role="vuoro_knowledge_runtime")
    before = owner.compatibility()
    assert before["compatible"] is False and before["reason"]
    # The gate never migrates, whatever the store's startup-mode variable says.
    assert pg_conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname = %s", (schema,)).fetchone()[0] == 0
    migrate_schema(pg_conn, schema)
    # The owner/migration DSN is refused even once the schema exists: it is not
    # the configured runtime role, and even when configured as one, it is a
    # superuser (owner) connection.
    assert "role_kind_mismatch" in owner.compatibility()["reasons"]
    as_owner = KnowledgeApplication(lambda: psycopg.connect(pg_dsn, autocommit=True), objects, schema=schema,
                                    runtime_role="postgres")
    refused = as_owner.compatibility()
    assert refused["compatible"] is False
    assert "runtime_role_is_superuser" in refused["reasons"]
    role = _runtime_role(pg_conn, schema)
    try:
        runtime = KnowledgeApplication(_connect_as(pg_dsn, role), objects, schema=schema, runtime_role=role)
        after = runtime.compatibility()
        assert after["compatible"] is True, after
        assert after["domain_api_version"] == "vuoro-workspace/v1"
        assert after["schema_version"] == "knowledge-schema/v1"
    finally:
        pg_conn.execute(f'DROP OWNED BY "{role}"')
        pg_conn.execute(f'DROP ROLE IF EXISTS "{role}"')


def test_errors_do_not_disclose_storage_internals(served) -> None:
    s = served
    data = b"bytes never uploaded"
    begun = s.ok("t-contrib", "knowledge.artifact.begin-v1", {
        "workspace_id": s.ws, "expected_sha256": hashlib.sha256(data).hexdigest(), "expected_size": len(data),
        "media_type": "text/plain", "name": "missing.txt"})
    status, body = s.call("t-contrib", "knowledge.artifact.finalize-v1",
                          {"workspace_id": s.ws, "version_id": begun["version_id"]})
    assert status == 409 and body["error"]["code"] == "knowledge-object-missing"
    assert "object_key" not in (body["error"].get("details") or {})
    assert "ws/" not in body["error"]["message"]


def test_reconcile_never_deletes_on_back_to_back_passes(served) -> None:
    s = served
    orphan_key = f"ws/{s.ws}/art/01JUNKARTIFACT000000000000/01JUNKVERSION0000000000000"
    s.application.objects.put(orphan_key, b"stray bytes")
    s.clock.offset = timedelta(days=2)  # the object is now well past grace
    args = {"workspace_id": s.ws, "delete_orphans": True, "grace_seconds": 3600}
    first = s.ok("t-admin", "knowledge.workspace.reconcile-v1", args)
    second = s.ok("t-admin", "knowledge.workspace.reconcile-v1", args)
    for report in (first, second):
        assert [(o["object_key"], o["status"]) for o in report["orphans"]] == [(orphan_key, "pending-second-pass")]
    assert s.application.objects.head(orphan_key) is not None
    s.clock.offset += timedelta(seconds=3601)  # grace has now passed since the first sighting
    third = s.ok("t-admin", "knowledge.workspace.reconcile-v1", args)
    assert [(o["object_key"], o["status"]) for o in third["orphans"]] == [(orphan_key, "deleted")]
    assert s.application.objects.head(orphan_key) is None
    assert s.code("t-admin", "knowledge.workspace.reconcile-v1", {**args, "grace_seconds": 0}) \
        == (422, "schema-validation-failed")


def test_download_size_is_checked_before_fetching(served, tmp_path: Path) -> None:
    s = served
    data = b"# small\n"
    begun = s.ok("t-contrib", "knowledge.artifact.begin-v1", {
        "workspace_id": s.ws, "expected_sha256": hashlib.sha256(data).hexdigest(), "expected_size": len(data),
        "media_type": "text/markdown", "name": "small.md"})
    s.ok("t-contrib", "knowledge.artifact.upload-v1", {
        "workspace_id": s.ws, "version_id": begun["version_id"], "content_base64": base64.b64encode(data).decode()})
    s.ok("t-contrib", "knowledge.artifact.finalize-v1", {"workspace_id": s.ws, "version_id": begun["version_id"]})
    # The store refuses on the recorded size before touching the object store.
    from vuoro_workspace import WorkspaceError
    from vuoro_workspace.store import Principal

    class NoFetch(FsObjectStore):
        def get(self, key):  # pragma: no cover - must not be reached
            raise AssertionError("bytes fetched before the size check")

    import psycopg
    from vuoro_workspace import WorkspaceStore

    store = WorkspaceStore(psycopg.connect(s.dsn, autocommit=True), NoFetch(tmp_path / "nofetch"), schema=s.schema)
    try:
        with pytest.raises(WorkspaceError) as info:
            store.download(Principal("test:contrib:0", frozenset(EVERY)), s.ws, begun["artifact_id"],
                           begun["version_id"], max_bytes=len(data) - 1)
        assert info.value.code == "payload-too-large"
    finally:
        store.close()


# ------------------------------------------------------------------- pool


def _ctx(name: str = "reader") -> SimpleNamespace:
    return SimpleNamespace(identity=TOKENS[f"t-{name}"], idempotency_key=None)


def test_pool_reuses_a_connection_after_a_domain_refusal(served) -> None:
    s = served
    app = s.application
    s.ok("t-reader", "knowledge.workspace.changes-v1", {"workspace_id": s.ws})
    assert len(app._idle) == 1
    conn = app._idle[0].conn
    assert s.code("t-outsider", "knowledge.workspace.changes-v1", {"workspace_id": s.ws}) \
        == (403, "knowledge-forbidden")
    assert [store.conn for store in app._idle] == [conn]  # same connection, back in the pool


def test_pool_replaces_a_dead_idle_connection(served, pg_conn: Any) -> None:
    s = served
    app = s.application
    s.ok("t-reader", "knowledge.workspace.changes-v1", {"workspace_id": s.ws})
    dead = app._idle[0].conn
    pid = dead.info.backend_pid
    pg_conn.execute("SELECT pg_terminate_backend(%s, 5000)", (pid,))  # waits for exit
    s.ok("t-reader", "knowledge.workspace.changes-v1", {"workspace_id": s.ws})
    assert len(app._idle) == 1 and app._idle[0].conn is not dead
    assert dead.closed


def test_pool_discards_a_connection_broken_mid_call(served) -> None:
    s = served
    app = s.application
    s.ok("t-reader", "knowledge.workspace.changes-v1", {"workspace_id": s.ws})
    first = app._idle[0].conn

    def explode(store, principal):
        store.conn.close()
        raise RuntimeError("connection lost mid-call")

    with pytest.raises(RuntimeError):
        app._call(_ctx(), explode)
    assert app._idle == []
    s.ok("t-reader", "knowledge.workspace.changes-v1", {"workspace_id": s.ws})
    assert app._idle[0].conn is not first


def test_more_concurrent_calls_than_pool_slots_all_complete(served) -> None:
    s = served
    app = KnowledgeApplication(_connect_as(s.dsn, s.role), FsObjectStore(s.objects_root), schema=s.schema,
                               runtime_role=s.role, max_connections=3)
    results: list[Any] = []
    errors: list[BaseException] = []

    def call() -> None:
        try:
            results.append(app.workspace_changes({"workspace_id": s.ws}, _ctx()))
        except BaseException as error:  # pragma: no cover - reported below
            errors.append(error)

    threads = [threading.Thread(target=call) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    try:
        assert errors == [] and len(results) == 12
        assert 1 <= len(app._idle) <= 3
    finally:
        app.close()


def test_pool_wait_is_bounded_and_answers_unavailable(served) -> None:
    from vuoro_service.catalog import OperationRejectedError

    s = served
    app = KnowledgeApplication(_connect_as(s.dsn, s.role), FsObjectStore(s.objects_root), schema=s.schema,
                               runtime_role=s.role, max_connections=1, acquire_timeout=0.2)
    assert app._slots.acquire(timeout=1)  # hold the only slot
    try:
        with pytest.raises(OperationRejectedError) as info:
            app.workspace_changes({"workspace_id": s.ws}, _ctx())
        assert (info.value.code, info.value.http_status) == ("knowledge-unavailable", 503)
    finally:
        app._slots.release()
        app.close()


def test_from_dsn_bounds_connect_and_statement_time(served) -> None:
    from psycopg.conninfo import make_conninfo

    s = served
    app = KnowledgeApplication.from_dsn(make_conninfo(s.dsn, user=s.role, password=RUNTIME_PASSWORD),
                                        FsObjectStore(s.objects_root), schema=s.schema, runtime_role=s.role,
                                        statement_timeout_ms=1234)
    conn = app._connect()
    try:
        assert conn.execute("SHOW statement_timeout").fetchone()[0] == "1234ms"
        assert conn.info.get_parameters().get("connect_timeout") == "5"
    finally:
        conn.close()


def test_connection_parameters_keep_dsn_options_and_add_bounds() -> None:
    from vuoro_workspace.vuoro_adapter import connection_parameters

    merged = connection_parameters("host=db dbname=k options='-c search_path=public' connect_timeout=2 keepalives_idle=7",
                                   statement_timeout_ms=1000)
    assert merged["options"] == "-c search_path=public -c statement_timeout=1000"
    assert merged["connect_timeout"] == "2" and merged["keepalives_idle"] == "7"
    plain = connection_parameters("host=db dbname=k")
    assert plain["options"] == "-c statement_timeout=30000" and plain["connect_timeout"] == 5
    assert plain["keepalives"] == 1 and plain["keepalives_count"] == 3


def test_from_dsn_preserves_dsn_options_on_a_real_connection(served) -> None:
    from psycopg.conninfo import make_conninfo

    s = served
    dsn = make_conninfo(s.dsn, user=s.role, password=RUNTIME_PASSWORD, options="-c lock_timeout=4321")
    app = KnowledgeApplication.from_dsn(dsn, FsObjectStore(s.objects_root), schema=s.schema, runtime_role=s.role)
    conn = app._connect()
    try:
        assert conn.execute("SHOW lock_timeout").fetchone()[0] == "4321ms"
        assert conn.execute("SHOW statement_timeout").fetchone()[0] == "30s"
    finally:
        conn.close()


def test_a_non_inheriting_member_of_the_owner_role_is_refused(served, pg_conn: Any) -> None:
    s = served
    owner, member = f"{s.schema}_owner", f"{s.schema}_noinherit"
    pg_conn.execute(f'CREATE ROLE "{owner}" NOLOGIN')
    pg_conn.execute(f'ALTER SCHEMA "{s.schema}" OWNER TO "{owner}"')
    pg_conn.execute(f"CREATE ROLE \"{member}\" LOGIN NOINHERIT PASSWORD '{RUNTIME_PASSWORD}'")
    pg_conn.execute(f'GRANT "{owner}" TO "{member}"')
    # Ordinary runtime grants, so only the owner membership can fail the gate.
    pg_conn.execute(f'GRANT USAGE ON SCHEMA "{s.schema}" TO "{member}"')
    pg_conn.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{s.schema}" TO "{member}"')
    try:
        app = KnowledgeApplication(_connect_as(s.dsn, member), FsObjectStore(s.objects_root), schema=s.schema,
                                   runtime_role=member)
        report = app.compatibility()
        assert report["compatible"] is False and "runtime_role_owns_schema" in report["reasons"]
    finally:
        pg_conn.execute(f'ALTER SCHEMA "{s.schema}" OWNER TO CURRENT_USER')
        pg_conn.execute(f'DROP OWNED BY "{member}"')
        pg_conn.execute(f'DROP ROLE IF EXISTS "{member}"')
        pg_conn.execute(f'DROP ROLE IF EXISTS "{owner}"')


def test_a_store_released_after_close_is_closed_not_pooled(served) -> None:
    s = served
    app = s.application
    with app._store() as store:
        app.close()
        conn = store.conn
    assert app._idle == [] and conn.closed

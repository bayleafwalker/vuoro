"""The eleven Phase 1 failure scenarios (phase0 contract §4), one test each or more."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta

import pytest

from workspace_fixtures import Clock, clock, env, pg_conn, pg_dsn, schema  # noqa: F401  (fixtures)
from vuoro_workspace import Principal, WorkspaceError

MD = b"# Findings\n\nThe quarterly churn analysis shows retention improving.\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def upload(e, who, data=MD, media="text/markdown", name="report.md", artifact_id=None, finalize=True, **kw):
    begun = e.store.artifact_begin(e.p[who], e.ws, expected_sha256=kw.get("expected_sha", sha(data)),
                                   expected_size=kw.get("expected_size", len(data)), media_type=media,
                                   name=None if artifact_id else name, artifact_id=artifact_id)
    e.store.artifact_upload(e.p[who], e.ws, begun["version_id"], data)
    if not finalize:
        return begun, None
    return begun, e.store.artifact_finalize(e.p[who], e.ws, begun["version_id"])


def publish_item(e, title="Churn summary", body="Retention improved after onboarding changes."):
    item = e.store.item_create(e.p["contrib"], e.ws, title, body)
    prop = e.store.proposal_submit(e.p["contrib"], e.ws, item["revision_id"], None)
    e.store.proposal_review(e.p["reviewer"], e.ws, prop["proposal_id"], "approve")
    return item


def age(e, key):
    """Stamp an object's mtime with the store's (fake) clock."""
    ts = e.clock.now.timestamp()
    os.utime(e.objects.root / key, (ts, ts))


def codes(fn):
    with pytest.raises(WorkspaceError) as info:
        fn()
    return info.value.code


# 1 -------------------------------------------------------------------------
def test_s1_reserved_version_is_invisible_then_expires(env):
    e = env
    # The name contains the search term, so only the ready filter keeps it out of results.
    begun, _ = upload(e, "contrib", name="churn report", finalize=False)
    art, ver = begun["artifact_id"], begun["version_id"]
    for who in ("reader", "contrib"):
        assert e.store.search(e.p[who], e.ws, "churn", scope="history")["total"] == 0
        assert codes(lambda: e.store.download(e.p[who], e.ws, art, ver)) == "not-found"
    assert codes(lambda: e.store.download(e.p["reader"], e.ws, art)) == "not-found"
    feed = e.store.changes_since(e.p["reader"], e.ws)
    assert all(c["version_id"] != ver for c in feed["changes"])

    age(e, begun["object_key"])
    e.clock.advance(hours=2)
    report = e.store.reconcile(e.admin, e.ws, grace=timedelta(hours=1))
    assert report["expired_versions"] == [ver]
    assert codes(lambda: e.store.artifact_finalize(e.p["contrib"], e.ws, ver)) == "reservation-expired"
    assert codes(lambda: e.store.artifact_upload(e.p["contrib"], e.ws, ver, MD)) == "state-conflict"
    # Its leftover bytes are now an orphan candidate (reported, not deleted on first pass).
    assert [o["reason"] for o in report["orphans"]] == ["expired-version"]


def test_s1_finalize_after_ttl_expires_instead_of_verifying(env):
    e = env
    begun, _ = upload(e, "contrib", finalize=False)
    e.clock.advance(hours=2)
    assert codes(lambda: e.store.artifact_finalize(e.p["contrib"], e.ws, begun["version_id"])) == "reservation-expired"
    feed = e.store.changes_since(e.p["contrib"], e.ws)
    assert feed["changes"][-1]["kind"] == "artifact.expired"


# 2 -------------------------------------------------------------------------
@pytest.mark.parametrize("variant", ["checksum", "size", "type"])
def test_s2_wrong_checksum_size_or_type_is_rejected(env, variant):
    e = env
    if variant == "checksum":
        begun, result = upload(e, "contrib", expected_sha="0" * 64, name="churn report")
        reason = "checksum-mismatch"
    elif variant == "size":
        begun, result = upload(e, "contrib", expected_size=len(MD) + 1, name="churn report")
        reason = "size-mismatch"
    else:
        begun, result = upload(e, "contrib", data=MD, media="application/pdf", name="churn report pdf")
        reason = "media-type-mismatch"
    assert result["processing_state"] == "rejected"
    assert reason in result["reject_reasons"]
    ver = begun["version_id"]
    assert codes(lambda: e.store.artifact_review(e.p["reviewer"], e.ws, ver, "approve")) == "state-conflict"
    assert codes(lambda: e.store.download(e.p["contrib"], e.ws, begun["artifact_id"], ver)) == "not-found"
    assert e.store.search(e.p["contrib"], e.ws, "churn", scope="history")["total"] == 0
    # Retried finalize keeps the terminal answer; it never becomes ready.
    assert e.store.artifact_finalize(e.p["contrib"], e.ws, ver) == result


# 3 -------------------------------------------------------------------------
def test_s3_finalize_retry_and_key_reuse_give_one_version_and_same_response(env):
    e = env
    c = e.p["contrib"]
    req = dict(expected_sha256=sha(MD), expected_size=len(MD), media_type="text/markdown", name="report.md")
    first = e.store.artifact_begin(c, e.ws, idempotency_key="begin-0001", **req)
    again = e.store.artifact_begin(c, e.ws, idempotency_key="begin-0001", **req)
    assert again == first
    e.store.artifact_upload(c, e.ws, first["version_id"], MD)
    f1 = e.store.artifact_finalize(c, e.ws, first["version_id"], idempotency_key="final-0001")
    f2 = e.store.artifact_finalize(c, e.ws, first["version_id"], idempotency_key="final-0001")
    f3 = e.store.artifact_finalize(c, e.ws, first["version_id"])
    assert f1 == f2 == f3 and f1["processing_state"] == "ready"
    with e.store.conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM artifact_version WHERE workspace_id = %s", (e.ws,))
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT count(*) AS n FROM workspace_event WHERE workspace_id = %s AND kind = 'artifact.ready'",
                    (e.ws,))
        assert cur.fetchone()["n"] == 1
    # Same key, different input: refused.
    other = dict(req, expected_size=len(MD) + 5)
    assert codes(lambda: e.store.artifact_begin(c, e.ws, idempotency_key="begin-0001", **other)) == "idempotency-conflict"
    assert codes(lambda: e.store.artifact_begin(c, e.ws, idempotency_key="bad key", **req)) == "invalid-argument"
    # Key scope is per principal: another principal's identical key is independent.
    other_principal = e.store.artifact_begin(e.p["contrib2"], e.ws, idempotency_key="begin-0001", **req)
    assert other_principal["version_id"] != first["version_id"]


# 4 -------------------------------------------------------------------------
def test_s4_two_proposals_on_one_base_first_wins_second_stale(env):
    e = env
    item = publish_item(e)
    base = item["revision_id"]
    d1 = e.store.revision_draft(e.p["contrib"], e.ws, item["item_id"], "Churn summary", "Version A")
    d2 = e.store.revision_draft(e.p["contrib2"], e.ws, item["item_id"], "Churn summary", "Version B")
    p1 = e.store.proposal_submit(e.p["contrib"], e.ws, d1["revision_id"], base)
    p2 = e.store.proposal_submit(e.p["contrib2"], e.ws, d2["revision_id"], base)
    ok = e.store.proposal_review(e.p["reviewer"], e.ws, p1["proposal_id"], "approve")
    assert ok["superseded_revision_id"] == base
    with pytest.raises(WorkspaceError) as info:
        e.store.proposal_review(e.p["reviewer"], e.ws, p2["proposal_id"], "approve")
    assert info.value.code == "stale-base"
    assert info.value.details["current_revision_id"] == d1["revision_id"]
    assert info.value.details["current_revision"]["body_md"] == "Version A"
    read = e.store.read(e.p["reader"], e.ws, item["item_id"])
    assert read["revision_id"] == d1["revision_id"] and read["body_md"] == "Version A"
    assert e.store.read(e.p["reader"], e.ws, item["item_id"], base)["state"] == "superseded"
    # The loser is still open and can be withdrawn by its author.
    assert e.store.proposal_withdraw(e.p["contrib2"], e.ws, p2["proposal_id"])["state"] == "withdrawn"


# 5 -------------------------------------------------------------------------
def test_s5_contributor_agent_and_author_cannot_approve(env):
    e = env
    item = e.store.item_create(e.p["reviewer"], e.ws, "Reviewer's own note", "Self-authored content")
    prop = e.store.proposal_submit(e.p["reviewer"], e.ws, item["revision_id"], None)
    pid = prop["proposal_id"]
    assert codes(lambda: e.store.proposal_review(e.p["contrib"], e.ws, pid, "approve")) == "forbidden"
    assert codes(lambda: e.store.proposal_review(e.p["agent"], e.ws, pid, "approve")) == "forbidden"
    assert codes(lambda: e.store.proposal_review(e.p["reviewer"], e.ws, pid, "approve")) == "forbidden"
    # An agent cannot be granted reviewer, and an agent-marked credential is capped even on a reviewer seat.
    assert codes(lambda: e.store.member_set(e.admin, e.ws, "agent", "reviewer", is_agent=True)) == "invalid-argument"
    capped = Principal("reviewer2", e.p["reviewer2"].authorities, is_agent=True)
    assert codes(lambda: e.store.proposal_review(capped, e.ws, pid, "approve")) == "forbidden"
    # A reviewer seat with a credential lacking knowledge:review is refused (intersection).
    narrow = Principal("reviewer2", {"knowledge:read", "knowledge:contribute"})
    assert codes(lambda: e.store.proposal_review(narrow, e.ws, pid, "approve")) == "forbidden"
    # Artifact versions: the uploader cannot approve their own, an agent cannot approve any.
    begun, _ = upload(e, "reviewer")
    assert codes(lambda: e.store.artifact_review(e.p["reviewer"], e.ws, begun["version_id"], "approve")) == "forbidden"
    assert codes(lambda: e.store.artifact_review(e.p["agent"], e.ws, begun["version_id"], "approve")) == "forbidden"
    # Another, human reviewer can.
    assert e.store.proposal_review(e.p["reviewer2"], e.ws, pid, "approve")["state"] == "approved"


# 6 -------------------------------------------------------------------------
def test_s6_reader_never_sees_drafts_or_unreviewed_artifacts(env):
    e = env
    publish_item(e, "Approved churn", "Approved churn conclusion")
    e.store.item_create(e.p["contrib"], e.ws, "Draft churn", "Draft churn speculation")
    proposed = e.store.item_create(e.p["contrib"], e.ws, "Proposed churn", "Proposed churn idea")
    e.store.proposal_submit(e.p["contrib"], e.ws, proposed["revision_id"], None)
    upload(e, "contrib", data=b"churn table unreviewed\n", media="text/plain", name="churn.txt")
    approved_art, _ = upload(e, "contrib", data=b"churn table approved\n", media="text/plain", name="churn-final.txt")
    e.store.artifact_review(e.p["reviewer"], e.ws, approved_art["version_id"], "approve")

    for scope in ("approved", "history"):
        res = e.store.search(e.p["reader"], e.ws, "churn", scope=scope)
        titles = sorted(r["title"] for r in res["results"])
        assert titles == ["Approved churn", "churn-final.txt"], scope
        assert res["total"] == 2
        assert all(r["publication_state"] == "approved" for r in res["results"])
    assert codes(lambda: e.store.search(e.p["reader"], e.ws, "churn", scope="working")) == "forbidden"
    working = e.store.search(e.p["contrib"], e.ws, "churn", scope="working")
    assert working["total"] == 5
    assert working["index_watermark"] == e.store.changes_since(e.admin, e.ws)["watermark"]
    # Readers cannot open the draft by exact id either.
    draft = e.store.search(e.p["contrib"], e.ws, "speculation", scope="working")["results"][0]
    assert codes(lambda: e.store.read(e.p["reader"], e.ws, draft["id"], draft["version_id"])) == "not-found"
    # And the reader's change feed carries only published events.
    kinds = {c["kind"] for c in e.store.changes_since(e.p["reader"], e.ws)["changes"]}
    assert kinds <= {"workspace.created", "proposal.approved", "artifact.approved"}


# 7 -------------------------------------------------------------------------
def test_s7_removed_member_is_refused_on_next_call_even_with_a_cursor(env):
    e = env
    item = publish_item(e)
    feed = e.store.changes_since(e.p["reader"], e.ws)
    e.store.member_set(e.admin, e.ws, "reader", None)
    r = e.p["reader"]
    assert codes(lambda: e.store.read(r, e.ws, item["item_id"])) == "forbidden"
    assert codes(lambda: e.store.search(r, e.ws, "retention")) == "forbidden"
    assert codes(lambda: e.store.changes_since(r, e.ws, feed["cursor"])) == "forbidden"
    # Demotion is equally immediate.
    e.store.member_set(e.admin, e.ws, "contrib2", "reader")
    assert codes(lambda: e.store.item_create(e.p["contrib2"], e.ws, "t", "b")) == "forbidden"
    # The last administrator cannot be removed.
    assert codes(lambda: e.store.member_set(e.admin, e.ws, "admin", None)) == "state-conflict"


# 8 -------------------------------------------------------------------------
def test_s8_deleted_item_vanishes_and_feed_carries_tombstone(env):
    e = env
    item = publish_item(e, "Retention memo", "Retention memo body")
    begun, _ = upload(e, "contrib", data=b"retention data\n", media="text/plain", name="retention.txt")
    e.store.artifact_review(e.p["reviewer"], e.ws, begun["version_id"], "approve")
    feed = e.store.changes_since(e.p["reader"], e.ws)
    assert e.store.search(e.p["reader"], e.ws, "retention")["total"] == 2

    e.store.delete(e.admin, e.ws, "item", item["item_id"])
    e.store.delete(e.admin, e.ws, "artifact", begun["artifact_id"])
    for who in ("reader", "contrib"):
        assert codes(lambda: e.store.read(e.p[who], e.ws, item["item_id"])) == "not-found"
        assert codes(lambda: e.store.read(e.p[who], e.ws, item["item_id"], item["revision_id"])) == "not-found"
        assert codes(lambda: e.store.download(e.p[who], e.ws, begun["artifact_id"])) == "not-found"
        for scope in ("approved", "history"):
            assert e.store.search(e.p[who], e.ws, "retention", scope=scope)["total"] == 0
    after = e.store.changes_since(e.p["reader"], e.ws, feed["cursor"])
    tombs = [(c["entity_kind"], c["entity_id"]) for c in after["changes"] if c["tombstone"]]
    assert tombs == [("item", item["item_id"]), ("artifact", begun["artifact_id"])]
    assert codes(lambda: e.store.delete(e.admin, e.ws, "item", item["item_id"])) == "not-found"
    # New drafts and relations cannot target the deleted item.
    assert codes(lambda: e.store.revision_draft(e.p["contrib"], e.ws, item["item_id"], "x", "y")) == "not-found"


# 9 -------------------------------------------------------------------------
def test_s9_reconcile_reports_missing_objects_and_orphans_two_pass(env):
    e = env
    begun, _ = upload(e, "contrib")
    key = begun["object_key"]
    orphan_key = f"ws/{e.ws}/art/01JUNKARTIFACT000000000000/01JUNKVERSION0000000000000"
    e.objects.put(orphan_key, b"stray bytes")
    age(e, orphan_key)
    os.unlink(e.objects.root / key)

    # Within the grace period the orphan is not reported yet.
    first = e.store.reconcile(e.admin, e.ws, grace=timedelta(days=1))
    assert first["missing_objects"] == [{"version_id": begun["version_id"], "object_key": key}]
    assert first["orphans"] == []

    e.clock.advance(days=2)
    second = e.store.reconcile(e.admin, e.ws, grace=timedelta(days=1), delete_orphans=True)
    assert second["orphans"] == [{"object_key": orphan_key, "size": 11, "reason": "no-row",
                                  "status": "pending-second-pass"}]
    assert e.objects.head(orphan_key) is not None  # never deleted on the first sighting
    third = e.store.reconcile(e.admin, e.ws, grace=timedelta(days=1), delete_orphans=True)
    assert third["orphans"][0]["status"] == "deleted"
    assert e.objects.head(orphan_key) is None
    assert codes(lambda: e.store.download(e.p["contrib"], e.ws, begun["artifact_id"], begun["version_id"])) == "object-missing"
    assert codes(lambda: e.store.reconcile(e.p["reviewer"], e.ws)) == "forbidden"


# 10 ------------------------------------------------------------------------
def test_s10_export_import_round_trip_preserves_checksums_ids_relations_history(env):
    e = env
    data = b"%PDF-1.4\nnot a real pdf but magic matches\n"
    sheet, _ = upload(e, "contrib", data=data, media="application/pdf", name="evidence.pdf")
    e.store.artifact_review(e.p["reviewer"], e.ws, sheet["version_id"], "approve")
    v2, _ = upload(e, "contrib", data=b"second version text\n", media="text/plain",
                   artifact_id=sheet["artifact_id"])
    rel = [{"kind": "supports", "target_kind": "artifact_version", "target_id": sheet["version_id"]},
           {"kind": "cites", "target_kind": "repository_document",
            "target_ref": {"repository": "vuoro", "commit": "4786ca6", "path": "docs/plans/x.md"}}]
    item = e.store.item_create(e.p["contrib"], e.ws, "Summary", "Summary v1", relations=rel)
    p1 = e.store.proposal_submit(e.p["contrib"], e.ws, item["revision_id"], None, rationale="first")
    e.store.proposal_review(e.p["reviewer"], e.ws, p1["proposal_id"], "approve", note="ok")
    d2 = e.store.revision_draft(e.p["contrib"], e.ws, item["item_id"], "Summary", "Summary v2")
    p2 = e.store.proposal_submit(e.p["contrib"], e.ws, d2["revision_id"], item["revision_id"])
    e.store.proposal_review(e.p["reviewer"], e.ws, p2["proposal_id"], "reject")
    gone = publish_item(e, "Deleted", "to be deleted")
    e.store.delete(e.admin, e.ws, "item", gone["item_id"])
    # One missing object is exported as an explicit missing entry.
    lost, _ = upload(e, "contrib", data=b"lost bytes\n", media="text/plain", name="lost.txt")
    os.unlink(e.objects.root / lost["object_key"])

    out = e.tmp / "bundle"
    exported = e.store.export(e.admin, e.ws, str(out))
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["format"] == "vuoro-workspace-export/v1"
    assert manifest["snapshot_seq"] == exported["snapshot_seq"]
    assert manifest["missing"] == [{"kind": "artifact_version", "version_id": lost["version_id"],
                                    "reason": "object-missing"}]
    assert [t["id"] for t in manifest["tombstones"]] == [gone["item_id"]]
    assert codes(lambda: e.store.export(e.p["reviewer"], e.ws, str(e.tmp / "x"))) == "forbidden"

    imported = e.store.import_bundle(e.admin, str(out), name="Copy")
    new_ws, m = imported["workspace_id"], imported["origin_map"]
    assert new_ws != e.ws and m["workspace"] == {e.ws: new_ws}
    # Every id is remapped and recorded.
    assert set(m["revision"]) == {item["revision_id"], d2["revision_id"]}
    assert set(m["artifact_version"]) == {sheet["version_id"], v2["version_id"], lost["version_id"]}
    # Content and checksums survive.
    read = e.store.read(e.admin, new_ws, m["item"][item["item_id"]])
    assert read["revision_id"] == m["revision"][item["revision_id"]] and read["body_md"] == "Summary v1"
    dl = e.store.download(e.admin, new_ws, m["artifact"][sheet["artifact_id"]])
    assert dl["version_id"] == m["artifact_version"][sheet["version_id"]]
    assert dl["sha256"] == sha(data) and dl["data"] == data and dl["review_state"] == "approved"
    # Relations are remapped, version-pinned, and the repository citation is kept.
    targets = sorted((r["kind"], r["target_id"], json.dumps(r["target_ref"])) for r in read["relations"])
    assert targets[1][:2] == ("supports", m["artifact_version"][sheet["version_id"]])
    assert json.loads(targets[0][2])["commit"] == "4786ca6"
    # Publication history survives the round trip modulo the id mapping.
    again = e.tmp / "bundle2"
    e.store.export(e.admin, new_ws, str(again))
    m2 = json.loads((again / "manifest.json").read_text())
    inverse = {v: k for kind in m.values() for k, v in kind.items()}
    remapped = [dict(h, target_id=inverse[h["target_id"]], proposal_id=inverse.get(h["proposal_id"]))
                for h in m2["publication_history"]]
    canon = lambda hs: sorted(json.dumps(h, sort_keys=True) for h in hs)  # noqa: E731
    assert canon(remapped) == canon(manifest["publication_history"])
    assert len(manifest["publication_history"]) == 3
    assert m2["workspace"]["origin"]["source_workspace_id"] == e.ws
    # The lost object is not resurrected as ready.
    assert codes(lambda: e.store.download(e.admin, new_ws, m["artifact"][lost["artifact_id"]],
                                          m["artifact_version"][lost["version_id"]])) == "not-found"


def test_s10_import_rejects_tampered_bundle_before_writing(env):
    e = env
    upload(e, "contrib")
    publish_item(e)
    out = e.tmp / "bundle"
    e.store.export(e.admin, e.ws, str(out))
    manifest = json.loads((out / "manifest.json").read_text())
    path = out / manifest["artifacts"][0]["versions"][0]["path"]
    path.write_bytes(path.read_bytes() + b"tamper")
    with e.store.conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM workspace")
        before = cur.fetchone()["n"]
    with pytest.raises(WorkspaceError) as info:
        e.store.import_bundle(e.admin, str(out))
    assert info.value.code == "import-invalid"
    assert any("checksum" in p for p in info.value.details["problems"])
    with e.store.conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM workspace")
        assert cur.fetchone()["n"] == before


# 11 ------------------------------------------------------------------------
def test_s11_workspace_isolation(env):
    e = env
    other = e.store.workspace_create(Principal("other-admin", e.admin.authorities), "Other")["workspace_id"]
    oa = Principal("other-admin", e.admin.authorities)
    e.store.member_set(oa, other, "ocontrib", "contributor")
    e.store.member_set(oa, other, "orev", "reviewer")
    oc = Principal("ocontrib", e.admin.authorities)
    b_item = e.store.item_create(oc, other, "Secret churn", "Workspace B churn secret")
    b_prop = e.store.proposal_submit(oc, other, b_item["revision_id"], None)
    e.store.proposal_review(Principal("orev", e.admin.authorities), other, b_prop["proposal_id"], "approve")
    data = b"B only churn\n"
    b_art = e.store.artifact_begin(oc, other, expected_sha256=sha(data), expected_size=len(data),
                                   media_type="text/plain", name="b.txt")
    e.store.artifact_upload(oc, other, b_art["version_id"], data)
    e.store.artifact_finalize(oc, other, b_art["version_id"])

    # A-members cannot enter B.
    for who in ("reader", "contrib", "reviewer"):
        assert codes(lambda: e.store.search(e.p[who], other, "churn")) == "forbidden"
        assert codes(lambda: e.store.changes_since(e.p[who], other)) == "forbidden"
    assert codes(lambda: e.store.read(e.admin, other, b_item["item_id"])) == "forbidden"
    # B's ids are unknown inside A.
    assert codes(lambda: e.store.read(e.admin, e.ws, b_item["item_id"])) == "not-found"
    assert codes(lambda: e.store.download(e.admin, e.ws, b_art["artifact_id"], b_art["version_id"])) == "not-found"
    assert codes(lambda: e.store.proposal_review(e.p["reviewer"], e.ws, b_prop["proposal_id"], "reject")) == "not-found"
    assert codes(lambda: e.store.artifact_finalize(e.p["contrib"], e.ws, b_art["version_id"])) == "not-found"
    assert codes(lambda: e.store.artifact_upload(e.p["contrib"], e.ws, b_art["version_id"], data)) == "not-found"
    assert codes(lambda: e.store.revision_draft(e.p["contrib"], e.ws, b_item["item_id"], "x", "y")) == "not-found"
    assert codes(lambda: e.store.delete(e.admin, e.ws, "item", b_item["item_id"])) == "not-found"
    # A cannot reference B's versions.
    for target_kind, target_id in (("revision", b_item["revision_id"]), ("artifact_version", b_art["version_id"])):
        rel = [{"kind": "cites", "target_kind": target_kind, "target_id": target_id}]
        assert codes(lambda: e.store.item_create(e.p["contrib"], e.ws, "x", "y", relations=rel)) == "not-found"
    # A cursor from B is not valid in A.
    b_cursor = e.store.changes_since(oa, other)["cursor"]
    assert codes(lambda: e.store.changes_since(e.admin, e.ws, b_cursor)) == "invalid-cursor"
    # Search in A never returns B content, at any scope.
    for scope in ("approved", "working", "history"):
        assert e.store.search(e.admin, e.ws, "churn secret", scope=scope)["total"] == 0
    # Reconcile of A does not see B's objects.
    assert e.store.reconcile(e.admin, e.ws, grace=timedelta(0))["orphans"] == []

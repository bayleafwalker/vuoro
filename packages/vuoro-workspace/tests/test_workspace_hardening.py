"""Regression, concurrency and crafted-input tests from the PR #225 review."""

from __future__ import annotations

import json
import threading

import pytest
from workspace_fixtures import Clock, clock, env, pg_conn, pg_dsn, schema  # noqa: F401  (fixtures)
from test_workspace_failure_scenarios import MD, codes, publish_item, sha, upload

from vuoro_workspace import Principal, WorkspaceError
from vuoro_workspace.extraction import Extraction

EVERY = {"knowledge:read", "knowledge:contribute", "knowledge:review", "knowledge:admin"}


def run_concurrently(*fns):
    """Run callables on threads released together; return (results, errors) in order."""
    barrier = threading.Barrier(len(fns))
    results: list = [None] * len(fns)
    errors: list = [None] * len(fns)

    def wrap(i, fn):
        barrier.wait()
        try:
            results[i] = fn()
        except Exception as exc:  # noqa: BLE001
            errors[i] = exc

    threads = [threading.Thread(target=wrap, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return results, errors


def scalar(e, sql, *args):
    with e.store.conn.cursor() as cur:
        cur.execute(sql, args)
        return next(iter(cur.fetchone().values()))


# 1: tsvector limit and body cap ---------------------------------------------
def test_large_bodies_index_a_prefix_and_oversize_is_a_domain_error(env):
    e = env
    body = " ".join(f"w{i}x" for i in range(120_000))  # ~0.9M chars of distinct lexemes: >1MB tsvector unbounded
    item = e.store.item_create(e.p["contrib"], e.ws, "Big", body)
    hits = e.store.search(e.p["contrib"], e.ws, "w5x", scope="working")
    assert [h["id"] for h in hits["results"]] == [item["item_id"]]
    assert codes(lambda: e.store.item_create(e.p["contrib"], e.ws, "Too big", "x" * 1_000_001)) == "invalid-argument"


def test_huge_extraction_is_truncated_not_stuck(env):
    e = env
    data = " ".join(f"t{i}q" for i in range(500_000)).encode()  # ~4MB of distinct words
    begun, result = upload(e, "contrib", data=data, media="text/plain", name="big.txt")
    assert result["processing_state"] == "ready"
    assert result["extraction_state"] == "ready" and result["extraction_truncated"] is True
    assert e.store.search(e.p["contrib"], e.ws, "t7q", scope="working")["total"] == 1


def test_extraction_insert_failure_marks_extraction_failed_not_finalize(env, monkeypatch):
    e = env
    import vuoro_workspace.store as store_mod

    monkeypatch.setattr(store_mod, "extract", lambda media, data: Extraction("ready", "x" * 1_000_001))
    begun, result = upload(e, "contrib", media="text/plain")
    assert result["processing_state"] == "ready" and result["extraction_state"] == "failed"
    assert scalar(e, "SELECT processing_state FROM artifact_version WHERE version_id = %s",
                  begun["version_id"]) == "ready"


# 2: stranded verifying --------------------------------------------------------
def _strand(e, version_id):
    with e.store.conn.cursor() as cur:
        cur.execute("UPDATE artifact_version SET processing_state = 'verifying', verifying_since = %s "
                    "WHERE version_id = %s", (e.clock.now, version_id))


def test_stranded_verifying_is_reset_by_reconcile_or_retried_by_finalize(env):
    e = env
    begun, _ = upload(e, "contrib", finalize=False)
    _strand(e, begun["version_id"])
    assert e.store.reconcile(e.admin, e.ws)["reset_verifications"] == []  # within verify_timeout
    e.clock.advance(minutes=20)
    report = e.store.reconcile(e.admin, e.ws)
    assert report["reset_verifications"] == [begun["version_id"]]
    assert e.store.artifact_finalize(e.p["contrib"], e.ws, begun["version_id"])["processing_state"] == "ready"
    # A finalize retry alone also recovers a stranded verification.
    again, _ = upload(e, "contrib", finalize=False)
    _strand(e, again["version_id"])
    assert e.store.artifact_finalize(e.p["contrib"], e.ws, again["version_id"])["processing_state"] == "ready"


def test_stranded_verifying_past_reservation_expires(env):
    e = env
    begun, _ = upload(e, "contrib", finalize=False)
    _strand(e, begun["version_id"])
    e.clock.advance(hours=2)
    assert e.store.reconcile(e.admin, e.ws)["expired_versions"] == [begun["version_id"]]
    assert codes(lambda: e.store.artifact_finalize(e.p["contrib"], e.ws, begun["version_id"])) == "reservation-expired"


# 3: last-admin race -----------------------------------------------------------
def test_concurrent_admin_demotions_keep_one_administrator(env):
    e = env
    e.store.member_set(e.admin, e.ws, "admin2", "administrator")
    admin2 = Principal("admin2", EVERY)
    s1, s2 = e.new_store(), e.new_store()
    for _ in range(3):
        results, errors = run_concurrently(
            lambda: s1.member_set(e.admin, e.ws, "admin2", "reader"),
            lambda: s2.member_set(admin2, e.ws, "admin", "reader"),
        )
        assert sum(r is not None for r in results) == 1
        assert {getattr(x, "code", None) for x in errors if x} <= {"state-conflict", "forbidden"}
        assert scalar(e, "SELECT count(*) FROM workspace_member WHERE workspace_id = %s AND role = 'administrator'",
                      e.ws) == 1
        survivor = e.admin if results[0] is not None else admin2
        other = "admin2" if survivor is e.admin else "admin"
        e.store.member_set(survivor, e.ws, other, "administrator")


# 6: agent marker preserved ----------------------------------------------------
def test_member_set_preserves_agent_marker_unless_explicitly_cleared(env):
    e = env
    assert e.store.member_set(e.admin, e.ws, "agent", "reader")["is_agent"] is True
    assert codes(lambda: e.store.member_set(e.admin, e.ws, "agent", "reviewer")) == "invalid-argument"
    assert codes(lambda: e.store.proposal_review(e.p["agent"], e.ws, "x", "approve")) == "forbidden"
    cleared = e.store.member_set(e.admin, e.ws, "agent", "reviewer", is_agent=False)
    assert cleared["is_agent"] is False
    assert e.store.member_set(e.admin, e.ws, "newbie", "reader")["is_agent"] is False


# 8: reader leaks --------------------------------------------------------------
def test_s6_reader_feed_excludes_unpublished_work_and_watermark_moves_only_on_visible_events(env):
    e = env
    r = e.p["reader"]
    before = e.store.changes_since(r, e.ws)
    search_before = e.store.search(r, e.ws, "anything")["index_watermark"]
    draft = e.store.item_create(e.p["contrib"], e.ws, "Secret draft", "hidden")
    upload(e, "contrib", name="unreviewed thing")
    e.store.delete(e.admin, e.ws, "item", draft["item_id"])
    after = e.store.changes_since(r, e.ws, before["cursor"])
    assert after["changes"] == [] and after["watermark"] == before["watermark"]
    assert after["cursor"] == before["cursor"]
    assert e.store.search(r, e.ws, "anything")["index_watermark"] == search_before
    contrib_feed = e.store.changes_since(e.p["contrib"], e.ws)
    assert any(c["tombstone"] and c["entity_id"] == draft["item_id"] for c in contrib_feed["changes"])
    # A published item's deletion is still a published tombstone.
    published = publish_item(e)
    mark = e.store.changes_since(r, e.ws)
    e.store.delete(e.admin, e.ws, "item", published["item_id"])
    tombs = e.store.changes_since(r, e.ws, mark["cursor"])["changes"]
    assert [(c["entity_id"], c["tombstone"]) for c in tombs] == [(published["item_id"], True)]


def test_read_filters_relations_by_caller_visibility(env):
    e = env
    unreviewed, _ = upload(e, "contrib", name="raw data")
    draft = e.store.item_create(e.p["contrib2"], e.ws, "Draft note", "draft")
    rels = [
        {"kind": "supports", "target_kind": "artifact_version", "target_id": unreviewed["version_id"]},
        {"kind": "cites", "target_kind": "revision", "target_id": draft["revision_id"]},
        {"kind": "cites", "target_kind": "repository_document",
         "target_ref": {"repository": "vuoro", "commit": "abc1234", "path": "README.md"}},
    ]
    item = e.store.item_create(e.p["contrib"], e.ws, "Summary", "Summary body", relations=rels)
    prop = e.store.proposal_submit(e.p["contrib"], e.ws, item["revision_id"], None)
    e.store.proposal_review(e.p["reviewer"], e.ws, prop["proposal_id"], "approve")
    reader_view = e.store.read(e.p["reader"], e.ws, item["item_id"])
    assert [r["target_kind"] for r in reader_view["relations"]] == ["repository_document"]
    contrib_view = e.store.read(e.p["contrib"], e.ws, item["item_id"])
    assert len(contrib_view["relations"]) == 3
    e.store.artifact_review(e.p["reviewer"], e.ws, unreviewed["version_id"], "approve")
    assert len(e.store.read(e.p["reader"], e.ws, item["item_id"])["relations"]) == 2


# 9: pointers never regress ----------------------------------------------------
def test_latest_and_approved_pointers_only_move_forward(env):
    e = env
    c = e.p["contrib"]
    v1, _ = upload(e, "contrib", finalize=False)
    v2, _ = upload(e, "contrib", artifact_id=v1["artifact_id"], finalize=False)
    e.store.artifact_finalize(c, e.ws, v2["version_id"])
    e.store.artifact_finalize(c, e.ws, v1["version_id"])
    art = v1["artifact_id"]
    assert scalar(e, "SELECT latest_version_id FROM artifact WHERE artifact_id = %s", art) == v2["version_id"]
    e.store.artifact_review(e.p["reviewer"], e.ws, v2["version_id"], "approve")
    late = e.store.artifact_review(e.p["reviewer"], e.ws, v1["version_id"], "approve")
    assert late["approved_version_id"] == v2["version_id"]
    assert e.store.download(e.p["reader"], e.ws, art)["version_id"] == v2["version_id"]


# 10: finalize idempotency keys ------------------------------------------------
def test_finalize_key_reused_for_another_version_conflicts(env):
    e = env
    c = e.p["contrib"]
    v1, _ = upload(e, "contrib", finalize=False)
    v2, _ = upload(e, "contrib", finalize=False)
    e.store.artifact_finalize(c, e.ws, v1["version_id"], idempotency_key="final-key-1")
    assert codes(lambda: e.store.artifact_finalize(c, e.ws, v2["version_id"],
                                                   idempotency_key="final-key-1")) == "idempotency-conflict"


def test_concurrent_finalize_with_one_key_conflicts_or_replays(env):
    e = env
    c = e.p["contrib"]
    v1, _ = upload(e, "contrib", finalize=False)
    v2, _ = upload(e, "contrib", finalize=False)
    s1, s2 = e.new_store(), e.new_store()
    results, errors = run_concurrently(
        lambda: s1.artifact_finalize(c, e.ws, v1["version_id"], idempotency_key="race-key-01"),
        lambda: s2.artifact_finalize(c, e.ws, v2["version_id"], idempotency_key="race-key-01"),
    )
    assert sum(r is not None for r in results) == 1
    assert [x.code for x in errors if x] == ["idempotency-conflict"]
    same, errs = run_concurrently(
        lambda: s1.artifact_finalize(c, e.ws, v1["version_id"], idempotency_key="race-key-02"),
        lambda: s2.artifact_finalize(c, e.ws, v1["version_id"], idempotency_key="race-key-02"),
    )
    assert errs == [None, None] and same[0] == same[1]
    assert scalar(e, "SELECT count(*) FROM idempotency_ledger WHERE idempotency_key = 'race-key-02'") == 1


# concurrency ------------------------------------------------------------------
def test_concurrent_approvals_on_one_base(env):
    e = env
    item = publish_item(e)
    base = item["revision_id"]
    d1 = e.store.revision_draft(e.p["contrib"], e.ws, item["item_id"], "T", "A")
    d2 = e.store.revision_draft(e.p["contrib2"], e.ws, item["item_id"], "T", "B")
    p1 = e.store.proposal_submit(e.p["contrib"], e.ws, d1["revision_id"], base)
    p2 = e.store.proposal_submit(e.p["contrib2"], e.ws, d2["revision_id"], base)
    s1, s2 = e.new_store(), e.new_store()
    results, errors = run_concurrently(
        lambda: s1.proposal_review(e.p["reviewer"], e.ws, p1["proposal_id"], "approve"),
        lambda: s2.proposal_review(e.p["reviewer2"], e.ws, p2["proposal_id"], "approve"),
    )
    assert sum(r is not None for r in results) == 1
    assert [x.code for x in errors if x] == ["stale-base"]
    assert scalar(e, "SELECT count(*) FROM knowledge_revision WHERE item_id = %s AND state = 'approved'",
                  item["item_id"]) == 1


def test_concurrent_first_use_of_an_idempotency_key_executes_once(env):
    e = env
    stores = [e.new_store() for _ in range(6)]
    results, errors = run_concurrently(*[
        (lambda s=s: s.item_create(e.p["contrib"], e.ws, "Once", "body", idempotency_key="create-once-1"))
        for s in stores
    ])
    assert errors == [None] * 6
    assert all(r == results[0] for r in results)
    assert scalar(e, "SELECT count(*) FROM knowledge_item WHERE workspace_id = %s", e.ws) == 1


def test_event_seq_is_monotonic_and_gap_free_under_concurrent_writers(env):
    e = env
    stores = [e.new_store() for _ in range(4)]

    def writer(s, n):
        return [s.item_create(e.p["contrib"], e.ws, f"w{n}-{i}", "b") for i in range(8)]

    run_concurrently(*[(lambda s=s, n=n: writer(s, n)) for n, s in enumerate(stores)])
    with e.store.conn.cursor() as cur:
        cur.execute("SELECT seq FROM workspace_event WHERE workspace_id = %s ORDER BY seq", (e.ws,))
        seqs = [r["seq"] for r in cur.fetchall()]
        cur.execute("SELECT last_seq FROM workspace_seq WHERE workspace_id = %s", (e.ws,))
        last = cur.fetchone()["last_seq"]
    assert seqs == list(range(1, last + 1))
    assert scalar(e, "SELECT count(*) FROM outbox WHERE workspace_id = %s", e.ws) == len(seqs)
    assert scalar(e, "SELECT count(*) FROM knowledge_item WHERE workspace_id = %s", e.ws) == 32


# s11 extras -------------------------------------------------------------------
def test_cross_workspace_references_are_refused(env):
    e = env
    oa = Principal("other-admin", EVERY)
    other = e.store.workspace_create(oa, "Other")["workspace_id"]
    b_item = e.store.item_create(oa, other, "B", "b")
    data = b"b bytes\n"
    b_art = e.store.artifact_begin(oa, other, expected_sha256=sha(data), expected_size=len(data),
                                   media_type="text/plain", name="b.txt")
    c = e.p["contrib"]
    assert codes(lambda: e.store.artifact_begin(c, e.ws, expected_sha256=sha(data), expected_size=len(data),
                                                media_type="text/plain", artifact_id=b_art["artifact_id"])) == "not-found"
    a_item = e.store.item_create(c, e.ws, "A", "a")
    assert codes(lambda: e.store.proposal_submit(c, e.ws, a_item["revision_id"], b_item["revision_id"])) == "not-found"
    assert codes(lambda: e.store.revision_draft(c, e.ws, a_item["item_id"], "A", "a2",
                                                parent_revision_id=b_item["revision_id"])) == "not-found"
    rel = [{"kind": "derived_from", "target_kind": "artifact_version", "target_id": b_art["version_id"]}]
    assert codes(lambda: e.store.artifact_begin(c, e.ws, expected_sha256=sha(data), expected_size=len(data),
                                                media_type="text/plain", name="x", relations=rel)) == "not-found"


# crafted bundles --------------------------------------------------------------
def _bundle(e, tmp_name="bundle"):
    upload(e, "contrib", name="first.md")
    upload(e, "contrib", data=b"second artifact\n", media="text/plain", name="second.txt")
    a = publish_item(e, "Item A", "body A")
    publish_item(e, "Item B", "body B")
    e.store.item_create(e.p["contrib"], e.ws, "Cites", "x", relations=[
        {"kind": "cites", "target_kind": "revision", "target_id": a["revision_id"]}])
    out = e.tmp / tmp_name
    e.store.export(e.admin, e.ws, str(out))
    return out, json.loads((out / "manifest.json").read_text())


def _rewrite(out, manifest):
    (out / "manifest.json").write_text(json.dumps(manifest))


def _workspace_count(e):
    return scalar(e, "SELECT count(*) FROM workspace")


def _import_problems(e, out):
    before = _workspace_count(e)
    with pytest.raises(WorkspaceError) as info:
        e.store.import_bundle(e.admin, str(out))
    assert info.value.code == "import-invalid"
    assert _workspace_count(e) == before
    return " | ".join(info.value.details["problems"])


@pytest.mark.parametrize("bad", ["../../etc/passwd", "/etc/passwd", "items/../../x", "manifest.json"])
def test_import_refuses_path_traversal(env, bad):
    e = env
    out, m = _bundle(e)
    m["items"][0]["revisions"][0]["path"] = bad
    _rewrite(out, m)
    assert "unsafe path" in _import_problems(e, out)


def test_import_cannot_publish_a_draft_through_the_approved_pointer(env):
    out, manifest = _bundle(env)
    item = next(item for item in manifest["items"] if item["approved_revision_id"] is None)
    item["approved_revision_id"] = item["latest_revision_id"]
    _rewrite(out, manifest)
    assert "approved_revision_id" in _import_problems(env, out)


def test_import_cannot_approve_an_unreviewed_artifact_through_its_pointer(env):
    out, manifest = _bundle(env)
    artifact = manifest["artifacts"][0]
    artifact["approved_version_id"] = artifact["latest_version_id"]
    _rewrite(out, manifest)
    assert "approved_version_id" in _import_problems(env, out)


def test_import_rejects_checksum_valid_non_utf8_revision_before_writing(env):
    out, manifest = _bundle(env)
    revision = manifest["items"][0]["revisions"][0]
    body = b"\xff\xfeinvalid markdown"
    (out / revision["path"]).write_bytes(body)
    revision["content_sha256"] = sha(body)
    _rewrite(out, manifest)
    assert "UTF-8" in _import_problems(env, out)


def test_approved_search_does_not_trust_a_legacy_inconsistent_pointer(env):
    e = env
    draft = e.store.item_create(e.p["contrib"], e.ws, "UnpublishedDraft", "privatephrase")
    # Represent a legacy inconsistent import; ordinary owner operations cannot
    # create this pointer. A reader must still never receive the draft hit.
    with e.store.conn.cursor() as cur:
        cur.execute("UPDATE knowledge_item SET approved_revision_id = %s WHERE item_id = %s",
                    (draft["revision_id"], draft["item_id"]))
    assert e.store.search(e.p["reader"], e.ws, "privatephrase")["total"] == 0
    assert e.store.search(e.p["contrib"], e.ws, "privatephrase", scope="working")["total"] == 1


def test_import_refuses_cross_item_and_unknown_proposals(env):
    e = env
    out, m = _bundle(e)
    other_item = next(i["item_id"] for i in m["items"] if i["item_id"] != m["proposals"][0]["item_id"])
    m["proposals"][0]["item_id"] = other_item
    _rewrite(out, m)
    assert "outside its item" in _import_problems(e, out)
    m["proposals"][0]["item_id"] = "01J00000000000000000000000"
    _rewrite(out, m)
    assert "outside the bundle" in _import_problems(e, out)


def test_import_refuses_relation_kind_mismatch(env):
    e = env
    out, m = _bundle(e)
    version_id = m["artifacts"][0]["versions"][0]["version_id"]
    m["relations"][0]["target_id"] = version_id  # still claims target_kind "revision"
    _rewrite(out, m)
    assert "is not a revision" in _import_problems(e, out)


def _all_keys(e):
    return {o.key.split("/")[1] for o in e.objects.list("ws/")}


def test_import_failure_after_object_puts_leaves_no_orphans(env, monkeypatch):
    e = env
    out, _ = _bundle(e)
    workspaces_with_objects = _all_keys(e)
    original = e.store._emit

    def failing_emit(cur, ws, actor, kind, *a, **kw):
        if kind == "workspace.imported":
            raise RuntimeError("injected failure after puts")
        return original(cur, ws, actor, kind, *a, **kw)

    monkeypatch.setattr(e.store, "_emit", failing_emit)
    before = _workspace_count(e)
    with pytest.raises(RuntimeError):
        e.store.import_bundle(e.admin, str(out))
    assert _workspace_count(e) == before
    assert _all_keys(e) == workspaces_with_objects


def test_import_failing_put_cleans_up_earlier_puts(env):
    e = env
    out, _ = _bundle(e)
    workspaces_with_objects = _all_keys(e)
    real = e.store.objects

    class FailSecondPut:
        def __init__(self):
            self.puts = 0

        def __getattr__(self, name):
            return getattr(real, name)

        def put(self, key, data):
            self.puts += 1
            if self.puts == 2:
                raise OSError("disk full")
            real.put(key, data)

    e.store.objects = FailSecondPut()
    try:
        with pytest.raises(OSError):
            e.store.import_bundle(e.admin, str(out))
    finally:
        e.store.objects = real
    assert _all_keys(e) == workspaces_with_objects


def test_upload_refused_once_verifying(env):
    e = env
    begun, _ = upload(e, "contrib", finalize=False)
    _strand(e, begun["version_id"])
    assert codes(lambda: e.store.artifact_upload(e.p["contrib"], e.ws, begun["version_id"], MD)) == "state-conflict"



# re-review N2/N3/N4 ---------------------------------------------------------------
def test_import_rederives_media_type_from_bytes(env):
    e = env
    out, m = _bundle(e)
    version = m["artifacts"][0]["versions"][0]
    html = b"<html><body><script>alert(1)</script></body></html>\n"
    (out / version["path"]).write_bytes(html)
    version.update(media_type="text/html", sha256=sha(html), size=len(html))
    _rewrite(out, m)
    imported = e.store.import_bundle(e.admin, str(out))
    new_ws, vmap = imported["workspace_id"], imported["origin_map"]["artifact_version"]
    amap = imported["origin_map"]["artifact"]
    dl = e.store.download(e.admin, new_ws, amap[m["artifacts"][0]["artifact_id"]], vmap[version["version_id"]])
    assert dl["media_type"] == "application/octet-stream" and dl["content_disposition"] == "attachment"
    assert dl["extraction_state"] == "unsupported"
    # Bytes that contradict the declared type are refused outright.
    version.update(media_type="application/pdf")
    _rewrite(out, m)
    assert "contradicts its media type" in _import_problems(e, out)


def test_export_import_carries_extraction_truncation(env):
    e = env
    data = " ".join(f"t{i}q" for i in range(250_000)).encode()
    upload(e, "contrib", data=data, media="text/plain", name="big.txt")
    out = e.tmp / "trunc"
    e.store.export(e.admin, e.ws, str(out))
    m = json.loads((out / "manifest.json").read_text())
    assert m["artifacts"][0]["versions"][0]["extraction_truncated"] is True
    new_ws = e.store.import_bundle(e.admin, str(out))["workspace_id"]
    assert scalar(e, "SELECT bool_and(truncated) FROM extraction WHERE workspace_id = %s", new_ws) is True
    assert scalar(e, "SELECT finalize_result->>'content_disposition' FROM artifact_version WHERE workspace_id = %s",
                  new_ws) == "attachment"


def test_finalize_key_race_loser_is_not_stranded(env):
    e = env
    c = e.p["contrib"]
    v1, _ = upload(e, "contrib", finalize=False)
    v2, _ = upload(e, "contrib", finalize=False)
    s1, s2 = e.new_store(), e.new_store()
    results, errors = run_concurrently(
        lambda: s1.artifact_finalize(c, e.ws, v1["version_id"], idempotency_key="race-key-03"),
        lambda: s2.artifact_finalize(c, e.ws, v2["version_id"], idempotency_key="race-key-03"),
    )
    loser = v2 if results[0] is not None else v1
    assert scalar(e, "SELECT processing_state FROM artifact_version WHERE version_id = %s",
                  loser["version_id"]) == "reserved"
    assert e.store.artifact_finalize(c, e.ws, loser["version_id"])["processing_state"] == "ready"


# contract scenarios 12 and 13 (phase0 §4, 896b502) ------------------------------
def test_s12_review_refused_unless_processing_ready(env):
    e = env
    reviewer = e.p["reviewer"]
    reserved, _ = upload(e, "contrib", finalize=False)
    assert codes(lambda: e.store.artifact_review(reviewer, e.ws, reserved["version_id"], "approve")) == "state-conflict"
    _strand(e, reserved["version_id"])  # verifying
    assert codes(lambda: e.store.artifact_review(reviewer, e.ws, reserved["version_id"], "approve")) == "state-conflict"
    rejected, result = upload(e, "contrib", expected_sha="0" * 64)
    assert result["processing_state"] == "rejected"
    assert codes(lambda: e.store.artifact_review(reviewer, e.ws, rejected["version_id"], "approve")) == "state-conflict"
    expired, _ = upload(e, "contrib", finalize=False)
    e.clock.advance(hours=2)
    e.store.reconcile(e.admin, e.ws)
    assert codes(lambda: e.store.artifact_review(reviewer, e.ws, expired["version_id"], "approve")) == "state-conflict"
    assert scalar(e, "SELECT count(*) FROM artifact_version WHERE workspace_id = %s AND review_state <> 'unreviewed'",
                  e.ws) == 0


def test_s13_non_administrators_cannot_delete_export_or_import(env):
    e = env
    item = publish_item(e)
    out = e.tmp / "s13"
    e.store.export(e.admin, e.ws, str(out))
    for who in ("reader", "contrib", "reviewer", "agent"):
        p = e.p[who]
        assert codes(lambda: e.store.delete(p, e.ws, "item", item["item_id"])) == "forbidden"
        assert codes(lambda: e.store.export(p, e.ws, str(e.tmp / f"x-{who}"))) == "forbidden"
    # Import creates a new workspace, so there is no membership yet: it needs knowledge:admin on a
    # non-agent credential, which is what makes the caller that workspace's first administrator.
    no_admin = Principal("reviewer", {"knowledge:read", "knowledge:contribute", "knowledge:review"})
    assert codes(lambda: e.store.import_bundle(no_admin, str(out))) == "forbidden"
    assert codes(lambda: e.store.import_bundle(e.p["agent"], str(out))) == "forbidden"
    assert e.store.read(e.p["reader"], e.ws, item["item_id"])["state"] == "approved"


def test_workspace_create_needs_admin_credential_and_creator_is_first_admin(env):
    e = env
    assert codes(lambda: e.store.workspace_create(Principal("u", {"knowledge:read", "knowledge:review"}), "W")) \
        == "forbidden"
    assert codes(lambda: e.store.workspace_create(Principal("bot", EVERY, is_agent=True), "W")) == "forbidden"
    creator = Principal("founder", EVERY)
    ws = e.store.workspace_create(creator, "W")["workspace_id"]
    assert scalar(e, "SELECT role FROM workspace_member WHERE workspace_id = %s AND principal_id = 'founder'",
                  ws) == "administrator"
    assert codes(lambda: e.store.member_set(creator, ws, "founder", "reviewer")) == "state-conflict"

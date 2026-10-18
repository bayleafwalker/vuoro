import tempfile
import threading
import unittest
from pathlib import Path
import sqlite3
import json

from owner_oracle import Conflict, OwnerOracle, ResponseLost, material_digest
from artifact_semantics import make_signed_artifact


class OwnerOracleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ec00-owner-")
        self.path = Path(self.tmp.name) / "owner.sqlite"
        default_body = {"purpose": "test-only", "procedure_revision": "source-r1"}
        digest = material_digest(default_body)
        OwnerOracle.initialize(self.path, [
            {"principal_id": "requester-a", "workspace_id": "workspace-a", "role": "requester"},
            {"principal_id": "requester-a2", "workspace_id": "workspace-a", "role": "requester"},
            {"principal_id": "verifier-a", "workspace_id": "workspace-a", "role": "verifier"},
            {"principal_id": "operator-a", "workspace_id": "workspace-a", "role": "operator"},
            {"principal_id": "coordinator-a", "workspace_id": "workspace-a", "role": "coordinator"},
            {"principal_id": "requester-b", "workspace_id": "workspace-b", "role": "requester"},
        ], evidence=[
            {"evidence_ref": "evidence:source:123", "workspace_id": "workspace-a", "request_id": "request-1", "material_digest": digest, "outcome": "performed", "status": "eligible"},
            {"evidence_ref": "evidence:source:revoked", "workspace_id": "workspace-a", "request_id": "request-1", "material_digest": digest, "outcome": "performed", "status": "revoked"},
            {"evidence_ref": "evidence:source:other-request", "workspace_id": "workspace-a", "request_id": "request-other", "material_digest": digest, "outcome": "performed", "status": "eligible"},
            {"evidence_ref": "evidence:source:other-digest", "workspace_id": "workspace-a", "request_id": "request-1", "material_digest": material_digest({**default_body, "procedure_revision": "different"}), "outcome": "performed", "status": "eligible"},
        ], links=[
            {"link_id": "work-a", "workspace_id": "workspace-a", "owner_principal": "requester-a", "repo_id": "repo-a", "run_ref": "run-a"},
            {"link_id": "work-b", "workspace_id": "workspace-a", "owner_principal": "requester-a", "repo_id": "repo-a", "run_ref": "run-b"},
            {"link_id": "work-c", "workspace_id": "workspace-a", "owner_principal": "requester-a2", "repo_id": "repo-a", "run_ref": "run-c"},
            {"link_id": "work-z", "workspace_id": "workspace-b", "owner_principal": "requester-b", "repo_id": "repo-b", "run_ref": "run-z"},
        ])
        self.owner = OwnerOracle.connect_as(self.path, "requester-a")
        self.verifier = OwnerOracle.connect_as(self.path, "verifier-a")
        self.operator = OwnerOracle.connect_as(self.path, "operator-a")
        self.coordinator = OwnerOracle.connect_as(self.path, "coordinator-a")

    def tearDown(self):
        self.owner.close()
        self.verifier.close()
        self.operator.close()
        self.coordinator.close()
        self.tmp.cleanup()

    def publish(self, request_id="request-1", *, dependants=None, principal=None, body=None):
        owner = principal or self.owner
        return owner.publish(
            request_id,
            body or {"purpose": "test-only", "procedure_revision": "source-r1"},
            dependants or ["work-a", "work-b"],
            f"publish-{request_id}",
        )

    def test_foreign_keys_are_enforced_and_database_is_file_backed(self):
        self.assertNotEqual(":memory:", self.owner.path)
        self.assertEqual(1, self.owner.db.execute("PRAGMA foreign_keys").fetchone()[0])
        with self.assertRaises(sqlite3.IntegrityError):
            self.owner.db.execute("INSERT INTO dependent VALUES('missing','work-x',1,0)")

    def test_same_key_replay_survives_close_reopen_and_conflicts_on_body_or_operation(self):
        first = self.publish()
        self.owner.close()
        self.owner = OwnerOracle.connect_as(self.path, "requester-a")
        self.assertEqual(first, self.publish())
        self.assertEqual(1, self.owner.request_count())
        with self.assertRaises(Conflict):
            self.owner.publish("request-1", {"different": True}, ["work-a"], "publish-request-1")
        with self.assertRaises(Conflict):
            self.owner.report_performed("request-1", 1, "publish-request-1")
        self.assertEqual(1, self.owner.request_count())

    def test_post_commit_response_loss_reconciles_after_database_reopen(self):
        with self.assertRaises(ResponseLost):
            self.owner.publish("request-1", {"procedure": "r1"}, ["work-a"], "publish-key", drop_after_commit=True)
        self.assertEqual(1, self.owner.request_count())
        self.owner.close()
        self.owner = OwnerOracle.connect_as(self.path, "requester-a")
        replay = self.owner.publish("request-1", {"procedure": "r1"}, ["work-a"], "publish-key")
        self.assertEqual({"request_id": "request-1", "revision": 1}, replay)
        self.assertEqual(1, self.owner.request_count())

    def test_two_independent_connections_compete_on_same_revision_one_wins(self):
        self.publish()
        barrier = threading.Barrier(2)
        outcomes = []
        def update(body, key):
            peer = OwnerOracle.connect_as(self.path, "requester-a")
            barrier.wait()
            try:
                outcomes.append(("ok", peer.revise("request-1", 1, body, key)))
            except Conflict:
                outcomes.append(("conflict", None))
            finally:
                peer.close()
        threads = [
            threading.Thread(target=update, args=({"procedure": "left"}, "cas-left")),
            threading.Thread(target=update, args=({"procedure": "right"}, "cas-right")),
        ]
        for t in threads: t.start()
        for t in threads: t.join(timeout=5)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertEqual(["conflict", "ok"], sorted(x[0] for x in outcomes))
        self.assertEqual(2, self.owner.request("request-1")["revision"])

    def test_stale_revision_refuses_without_partial_write(self):
        self.publish()
        self.owner.revise("request-1", 1, {"procedure": "r2"}, "revise-r2")
        before = self.owner.request("request-1")
        with self.assertRaises(Conflict):
            self.owner.revise("request-1", 1, {"procedure": "r3"}, "revise-stale")
        self.assertEqual(before, self.owner.request("request-1"))

    def test_each_material_transition_advances_revision_and_revision_invalidates_verification(self):
        self.publish()
        self.owner.report_performed("request-1", 1, "report")
        self.assertEqual(2, self.owner.request("request-1")["revision"])
        with self.assertRaises(Conflict):
            self.verifier.verify("request-1", 1, "evidence:source:123", "verify-stale")
        self.verifier.verify("request-1", 2, "evidence:source:123", "verify")
        self.assertEqual(3, self.owner.request("request-1")["revision"])
        revised = self.owner.revise("request-1", 3, {"procedure": "materially changed"}, "revise-after-report")
        self.assertEqual(4, revised["revision"])
        current = self.owner.request("request-1")
        self.assertEqual((0, 0, None), (current["reported"], current["verified"], current["evidence_ref"]))
        with self.assertRaises(Conflict):
            self.verifier.resolve("request-1", 4, "resolve-unverified")
        self.owner.report_performed("request-1", 4, "report-after-revision")
        with self.assertRaises(Conflict):
            self.verifier.verify("request-1", 5, "evidence:source:123", "verify-stale-evidence")

    def test_report_is_not_verification_and_evidence_reference_must_be_typed_nonempty(self):
        self.publish()
        report = self.owner.report_performed("request-1", 1, "report")
        self.assertEqual(2, report["revision"])
        current = self.owner.request("request-1")
        self.assertEqual((1, 0, "open"), (current["reported"], current["verified"], current["state"]))
        with self.assertRaises(Conflict):
            self.verifier.verify("request-1", 2, "", "verify-empty")
        with self.assertRaises(Conflict):
            self.verifier.verify("request-1", 2, "untyped free text", "verify-untyped")
        with self.assertRaises(Conflict):
            self.verifier.verify("request-1", 2, "evidence:source:forged", "verify-forged")
        with self.assertRaises(Conflict):
            self.verifier.verify("request-1", 2, "evidence:source:revoked", "verify-revoked")
        with self.assertRaises(Conflict):
            self.verifier.verify("request-1", 2, "evidence:source:other-request", "verify-wrong-request")
        with self.assertRaises(Conflict):
            self.verifier.verify("request-1", 2, "evidence:source:other-digest", "verify-wrong-material")
        with self.assertRaises(Conflict):
            self.owner.verify("request-1", 2, "evidence:source:123", "requester-forges-verification")
        self.assertEqual(2, self.owner.request("request-1")["revision"])

    def test_role_and_workspace_claims_in_request_content_are_refused_and_other_workspace_is_isolated(self):
        with self.assertRaises(Conflict):
            self.owner.publish("forged", {"role": "verifier", "workspace_id": "workspace-b"}, ["x"], "forged")
        self.publish()
        foreign = OwnerOracle.connect_as(self.path, "requester-b")
        self.assertEqual([], foreign.list_requests()["items"])
        with self.assertRaises(Conflict):
            foreign.request("request-1")
        foreign.close()

    def test_shared_withdrawal_preserves_other_dependent(self):
        self.publish()
        result = self.owner.withdraw_dependent("request-1", "work-a", 1, "withdraw-a")
        self.assertEqual(2, result["revision"])
        self.assertEqual({"work-a": {"active": 0, "continuation_ack": 0}, "work-b": {"active": 1, "continuation_ack": 0}}, self.owner.dependants("request-1"))

    def test_exact_artifact_decision_is_digest_bound_and_material_revision_invalidates_it(self):
        body = {"purpose": "test-only", "procedure_revision": "source-r1"}
        artifact = make_signed_artifact(workspace_id="workspace-a", request_id="request-1", material_revision=1,
                                        material_digest=material_digest(body),
                                        original_bytes=b"exact original artifact\x00with bytes")
        self.owner.publish("request-1", body, ["work-a", "work-b"], "publish-request-1",
                           signed_artifact=artifact)
        stored_artifact = self.owner.signed_artifact("request-1", 1)
        self.assertEqual(b"exact original artifact\x00with bytes", stored_artifact["original_bytes"])
        self.assertEqual(artifact["envelope_bytes"], stored_artifact["envelope_bytes"])
        self.assertEqual(artifact["signature"], stored_artifact["signature"])
        digest_a = artifact["artifact_digest"]
        digest_b = "sha256:" + "b" * 64
        with self.assertRaises(Conflict):
            self.owner.decide_artifact("request-1", 1, digest_b, "decision-wrong-digest")
        with self.assertRaises(Conflict):
            self.owner.decide_artifact("request-1", 1, digest_a, "requester-decision")
        result = self.operator.decide_artifact("request-1", 1, digest_a, "decision-a")
        self.assertEqual(2, result["revision"])
        self.assertEqual(digest_a, self.owner.request("request-1")["artifact_decision_digest"])
        with self.assertRaises(Conflict):
            self.owner.decide_artifact("request-1", 2, "not-a-digest", "decision-invalid")
        self.owner.revise("request-1", 2, {"procedure": "new artifact"}, "revise-artifact")
        current = self.owner.request("request-1")
        self.assertEqual(3, current["revision"])
        self.assertEqual(2, current["material_revision"])
        self.assertIsNone(current["artifact_decision_digest"])
        self.assertEqual(b"exact original artifact\x00with bytes",
                         self.owner.signed_artifact("request-1", 1)["original_bytes"])
        with self.assertRaises(Conflict):
            self.operator.decide_artifact("request-1", 3, digest_a, "decision-old-revision")

    def test_same_body_revise_advances_material_epoch_and_rejects_old_signature(self):
        body = {"purpose": "same material bytes", "procedure_revision": "source-r1"}
        artifact = make_signed_artifact(workspace_id="workspace-a", request_id="request-1", material_revision=1,
                                        material_digest=material_digest(body), original_bytes=b"same-body artifact")
        self.owner.publish("request-1", body, ["work-a"], "same-body-publish", signed_artifact=artifact)
        changed = self.owner.revise("request-1", 1, body, "same-body-revise")
        current = self.owner.request("request-1")
        self.assertEqual(2, changed["revision"])
        self.assertEqual(2, current["material_revision"])
        self.assertEqual(material_digest(body), material_digest(json.loads(current["body_json"])))
        self.assertEqual(b"same-body artifact", self.owner.signed_artifact("request-1", 1)["original_bytes"])
        with self.assertRaises(Conflict):
            self.operator.decide_artifact("request-1", 2, artifact["artifact_digest"], "same-body-old-signature")

    def test_signed_artifact_refuses_byte_signature_context_and_domain_tampering(self):
        body = {"purpose": "test-only", "procedure_revision": "source-r1"}
        valid = make_signed_artifact(workspace_id="workspace-a", request_id="signed-request", material_revision=1,
                                     material_digest=material_digest(body), original_bytes=b"signed bytes")
        for field, value in (
            ("original_bytes", b"changed bytes"),
            ("artifact_digest", "sha256:" + "0" * 64),
            ("envelope_bytes", valid["envelope_bytes"].replace(b"/v1", b"/v2")),
            ("signature", bytes([valid["signature"][0] ^ 1]) + valid["signature"][1:]),
        ):
            broken = dict(valid, **{field: value})
            with self.subTest(field=field), self.assertRaises(Conflict):
                self.owner.publish("signed-request", body, ["work-a"], f"bad-{field}",
                                   signed_artifact=broken)
            self.assertEqual(0, self.owner.request_count())

        wrong_context = make_signed_artifact(workspace_id="workspace-b", request_id="signed-request", material_revision=1,
                                             material_digest=material_digest(body), original_bytes=b"signed bytes")
        with self.assertRaises(Conflict):
            self.owner.publish("signed-request", body, ["work-a"], "bad-tenant", signed_artifact=wrong_context)
        for label, context in (
            ("request", dict(workspace_id="workspace-a", request_id="different-request", material_revision=1)),
            ("revision", dict(workspace_id="workspace-a", request_id="signed-request", material_revision=2)),
            ("domain-version", dict(workspace_id="workspace-a", request_id="signed-request", material_revision=1,
                                     domain="vuoro-ec00-artifact-decision/v2")),
        ):
            context.setdefault("material_digest", material_digest(body))
            context.setdefault("original_bytes", b"signed bytes")
            wrong = make_signed_artifact(**context)
            with self.subTest(context=label), self.assertRaises(Conflict):
                self.owner.publish("signed-request", body, ["work-a"], f"bad-{label}", signed_artifact=wrong)

        self.owner.publish("signed-request", body, ["work-a"], "good-signed-publish", signed_artifact=valid)
        self.owner.publish("other-request", body, ["work-a"], "publish-other-request")
        for column, value in (
            ("original_bytes", sqlite3.Binary(b"changed bytes")),
            ("envelope_bytes", sqlite3.Binary(valid["envelope_bytes"] + b"x")),
            ("signature", sqlite3.Binary(valid["signature"][:-1] + bytes([valid["signature"][-1] ^ 1]))),
            ("material_digest", "sha256:" + "f" * 64),
            ("workspace_id", "workspace-b"),
            ("request_id", "other-request"),
            ("material_revision", 2),
            ("key_id", "unknown-test-key"),
        ):
            with self.subTest(column=column):
                saved = self.owner.db.execute(f"SELECT {column} FROM signed_artifact WHERE request_id=?", ("signed-request",)).fetchone()[0]
                self.owner.db.execute(f"UPDATE signed_artifact SET {column}=? WHERE request_id=?", (value, "signed-request"))
                with self.assertRaises(Conflict):
                    self.operator.decide_artifact("signed-request", 1, valid["artifact_digest"], f"tampered-{column}")
                restore_key = value if column == "request_id" else "signed-request"
                self.owner.db.execute(f"UPDATE signed_artifact SET {column}=? WHERE request_id=?", (saved, restore_key))

    def test_resolution_and_each_dependent_ack_have_distinct_revisioned_events(self):
        self.publish()
        report = self.owner.report_performed("request-1", 1, "report")
        verified = self.verifier.verify("request-1", report["revision"], "evidence:source:123", "verify")
        resolved = self.verifier.resolve("request-1", verified["revision"], "resolve")
        ack_a = self.owner.acknowledge_continuation("request-1", "work-a", resolved["revision"], "ack-a")
        self.assertEqual(5, ack_a["revision"])
        ack_b = self.owner.acknowledge_continuation("request-1", "work-b", ack_a["revision"], "ack-b")
        self.assertEqual(6, ack_b["revision"])
        state = self.owner.dependants("request-1")
        self.assertEqual(1, state["work-a"]["continuation_ack"])
        self.assertEqual(1, state["work-b"]["continuation_ack"])

    def test_scoped_query_paginates_and_cursor_cannot_cross_principal_or_workspace(self):
        for n in range(3): self.publish(f"request-{n}", dependants=["work-a"])
        first = self.owner.list_requests(limit=2)
        self.assertEqual(2, len(first["items"]))
        self.assertIsNotNone(first["next_cursor"])
        second = self.owner.list_requests(first["next_cursor"], limit=2)
        self.assertEqual(1, len(second["items"]))
        foreign = OwnerOracle.connect_as(self.path, "requester-b")
        with self.assertRaises(Conflict): foreign.list_requests(first["next_cursor"])
        foreign.close()
        peer = OwnerOracle.connect_as(self.path, "requester-a2")
        with self.assertRaises(Conflict): peer.list_requests(first["next_cursor"])
        peer.close()
        foreign = OwnerOracle.connect_as(self.path, "requester-b")
        self.assertEqual(0, len(foreign.list_requests()["items"]))
        foreign.close()

    def test_event_watch_replays_persisted_events_is_bounded_and_refuses_expired_cursor(self):
        for n in range(4): self.publish(f"request-{n}", dependants=["work-a"])
        first = self.owner.watch(limit=1)
        self.assertEqual(1, len(first["events"]))
        self.assertTrue(first["more"])
        reopened = OwnerOracle.connect_as(self.path, "requester-a", max_read=1)
        second = reopened.watch(first["next_cursor"], limit=1)
        self.assertEqual(1, len(second["events"]))
        self.assertNotEqual(first["events"][0], second["events"][0])
        reopened.compact_events_through(2)
        with self.assertRaises(Conflict): reopened.watch(first["next_cursor"], limit=1)
        with self.assertRaises(Conflict): reopened.watch(limit=2)
        reopened.close()

    def test_revoked_identity_rejects_real_issued_page_and_watch_cursors(self):
        self.publish("request-0", dependants=["work-a"])
        self.publish("request-1", dependants=["work-b"])
        page = self.owner.list_requests(limit=1)
        cursor = page["next_cursor"]
        self.assertIsNotNone(cursor)
        watch = self.owner.watch(limit=1)
        self.assertIsNotNone(watch["next_cursor"])
        self.owner.db.execute("UPDATE identity SET active=0 WHERE principal_id='requester-a'")
        with self.assertRaises(Conflict): self.owner.list_requests(cursor)
        with self.assertRaises(Conflict): self.owner.watch(watch["next_cursor"])

    def test_coordinator_uses_only_prevalidated_shared_links(self):
        result = self.coordinator.publish("shared-request", {"purpose": "shared fixture"}, ["work-a", "work-c"], "coordinator-publish")
        self.assertEqual(1, result["revision"])
        self.assertEqual({"work-a", "work-c"}, set(self.owner.dependants("shared-request")))
        with self.assertRaises(Conflict):
            self.coordinator.publish("forged-shared", {"purpose": "unknown link"}, ["invented-link"], "coordinator-forge")

    def test_only_validated_link_owner_can_publish_withdraw_or_ack_dependency(self):
        self.publish()
        peer = OwnerOracle.connect_as(self.path, "requester-a2")
        with self.assertRaises(Conflict):
            peer.publish("peer-request", {"purpose": "cannot claim another owner's link"}, ["work-a"], "peer-publish")
        with self.assertRaises(Conflict):
            peer.withdraw_dependent("request-1", "work-a", 1, "peer-withdraw")
        report = self.owner.report_performed("request-1", 1, "report")
        verified = self.verifier.verify("request-1", report["revision"], "evidence:source:123", "verify")
        resolved = self.verifier.resolve("request-1", verified["revision"], "resolve")
        with self.assertRaises(Conflict):
            peer.acknowledge_continuation("request-1", "work-a", resolved["revision"], "peer-ack")
        self.assertEqual(0, self.owner.dependants("request-1")["work-a"]["continuation_ack"])
        peer.close()


if __name__ == "__main__":
    unittest.main()

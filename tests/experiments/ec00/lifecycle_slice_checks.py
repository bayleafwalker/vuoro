"""Generated Python clients exercise the predeclared owner lifecycle on one SQLite file."""
from __future__ import annotations

import base64
import json
import os
import hashlib
import sqlite3
import sys
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
import select

import httpx
import grpc
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tests/experiments/ec00/generated/protobuf/python"))
sys.path.insert(0, str(ROOT / "tests/experiments/ec00/generated/typespec/clients/python"))

from oa import v1_pb2, v1_pb2_grpc
from ec00_oa_python_client.api.default import (
    acknowledge_continuation, decide_artifact, list_requests, publish, read_artifact,
    read_dependants, read_request, report_performed, resolve, revise, verify, watch,
    withdraw_dependent,
)
from ec00_oa_python_client.client import Client
from ec00_oa_python_client.models import (
    AcknowledgeRequest, DecideArtifactRequest, PublishRequest, RevisionRequest,
    ReviseRequest, VerifyRequest, WithdrawRequest,
)
from ec00_oa_python_client.types import UNSET
from artifact_semantics import make_signed_artifact, transport_fields
from candidate_owner_http import (CandidateOwnerServer, initialize_fixture, persisted_artifact,
                                  persisted_request, seed_evidence, seed_typed_read_fixture)
from owner_oracle import OwnerOracle, material_digest


class LifecycleSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ec00-lifecycle-")
        self.db_path = Path(self.temp.name) / "owner.sqlite"
        initialize_fixture(self.db_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def body_artifact(self, request_id: str) -> tuple[dict[str, object], dict[str, object]]:
        body: dict[str, object] = {"objective": "complete generated-client lifecycle", "note": "source material"}
        signed = make_signed_artifact(workspace_id="workspace-a", request_id=request_id, material_revision=1,
                                      material_digest=material_digest(body),
                                      original_bytes=b"\x00signed artifact\xffwith exact bytes\n")
        seed_evidence(self.db_path, evidence_ref=f"evidence:source:{request_id}",
                      request_id=request_id, body=body)
        return body, signed

    def events(self, request_id: str) -> list[tuple[int, str, int]]:
        db = sqlite3.connect(self.db_path)
        try:
            return db.execute("SELECT seq,action,revision FROM event WHERE request_id=? ORDER BY seq",
                              (request_id,)).fetchall()
        finally:
            db.close()

    @staticmethod
    def http_client(base_url: str, key: str = "") -> Client:
        return Client(base_url=base_url, headers={"Idempotency-Key": key},
                      raise_on_unexpected_status=False)

    def http_mutation(self, server: CandidateOwnerServer, key: str, function, **kwargs):
        try:
            return function(client=self.http_client(server.base_url, key), **kwargs)
        except httpx.TransportError:
            # The harness fault schedule closes the response only after the SQLite commit.
            return function(client=self.http_client(server.base_url, key), **kwargs)

    @staticmethod
    def parsed(result):
        return result.parsed.to_dict()

    def publish_extras_openapi(self, base: str) -> None:
        for suffix in ("41", "43"):
            reqid = "900719925474" + suffix
            with CandidateOwnerServer(self.db_path, principal_id="coordinator-a") as server:
                response = publish.sync_detailed(client=self.http_client(server.base_url, f"extra-{suffix}"),
                                                 body=PublishRequest(request_id=reqid, objective="page fixture",
                                                                     dependants=["work-a"]))
                self.assertEqual(200, response.status_code)

    def test_typespec_openapi_python_full_lifecycle_pages_revocation_and_exact_artifact(self) -> None:
        request_id = "9007199254741101"
        body, signed = self.body_artifact(request_id)
        publish_key = "openapi-publish-lifecycle"
        with CandidateOwnerServer(self.db_path, principal_id="coordinator-a",
                                  fault_plan={("publish", publish_key)}) as server:
            fields = transport_fields(signed)
            data = PublishRequest(
                request_id=request_id, objective=body["objective"], note=body["note"],
                dependants=["work-a", "work-b"], artifact_digest=fields["artifactDigest"],
                artifact_key_id=fields["artifactKeyId"], artifact_bytes=fields["artifactBytes"],
                artifact_envelope=fields["artifactEnvelope"], artifact_signature=fields["artifactSignature"],
            )
            created = self.http_mutation(server, publish_key, publish.sync_detailed, body=data)
            self.assertEqual((200, 1), (created.status_code, created.parsed.revision))
            self.assertIn(("publish", publish_key), server.fault_consumed)
            again = publish.sync_detailed(client=self.http_client(server.base_url, publish_key), body=data)
            self.assertEqual(1, again.parsed.revision)
            request_doc = self.parsed(read_request.sync_detailed(request_id, client=self.http_client(server.base_url)))
            deps = self.parsed(read_dependants.sync_detailed(request_id, client=self.http_client(server.base_url)))
            saved_artifact = self.parsed(read_artifact.sync_detailed(request_id, "1", client=self.http_client(server.base_url)))
            self.assertEqual("1", request_doc["revision"])
            self.assertEqual("1", request_doc["materialRevision"])
            self.assertEqual("open", request_doc["state"])
            self.assertEqual({"objective": body["objective"], "note": body["note"]}, request_doc["body"])
            self.assertEqual([{"dependentId": "work-a", "active": True, "continuationAck": False},
                              {"dependentId": "work-b", "active": True, "continuationAck": False}], deps["items"])
            self.assertEqual(base64.b64encode(signed["original_bytes"]).decode(), saved_artifact["originalBytes"])
            self.assertEqual("1", saved_artifact["materialRevision"])
            self.assertEqual(material_digest(body), saved_artifact["materialDigest"])
            self.assertEqual(signed["key_id"], saved_artifact["keyId"])
            self.assertEqual((signed["original_bytes"], signed["envelope_bytes"], signed["signature"], signed["artifact_digest"]),
                             persisted_artifact(self.db_path, request_id, 1))

        # Wrong role and wrong digest refuse without changing the owner revision.
        with CandidateOwnerServer(self.db_path, principal_id="requester-a") as server:
            refused = decide_artifact.sync_detailed(request_id, client=self.http_client(server.base_url, "wrong-role"),
                                                    body=DecideArtifactRequest(expected_revision=1,
                                                                               artifact_digest=signed["artifact_digest"]))
            self.assertEqual(409, refused.status_code)
        with CandidateOwnerServer(self.db_path, principal_id="operator-a") as server:
            refused = decide_artifact.sync_detailed(request_id, client=self.http_client(server.base_url, "openapi-wrong-digest"),
                                                    body=DecideArtifactRequest(expected_revision=1,
                                                                               artifact_digest="sha256:" + "0" * 64))
            self.assertEqual(409, refused.status_code)

        # A stale write and unauthorized proof fail; each real mutation is dropped once after commit.
        with CandidateOwnerServer(self.db_path, principal_id="requester-a",
                                  fault_plan={("report-performed", "openapi-report")}) as server:
            stale = report_performed.sync_detailed(request_id, client=self.http_client(server.base_url, "stale-report"),
                                                   body=RevisionRequest(expected_revision=0))
            self.assertEqual(409, stale.status_code)
            report_body = RevisionRequest(expected_revision=1)
            report_result = self.http_mutation(server, "openapi-report", report_performed.sync_detailed,
                                               request_id=request_id, body=report_body)
            self.assertEqual((200, 2), (report_result.status_code, report_result.parsed.revision))
            self.assertIn(("report-performed", "openapi-report"), server.fault_consumed)
            self.assertEqual(2, report_performed.sync_detailed(request_id, client=self.http_client(server.base_url, "openapi-report"),
                                                               body=report_body).parsed.revision)

        with CandidateOwnerServer(self.db_path, principal_id="requester-a") as server:
            unauthorized = verify.sync_detailed(request_id, client=self.http_client(server.base_url, "requester-cannot-verify"),
                                                body=VerifyRequest(expected_revision=2, evidence_ref=f"evidence:source:{request_id}"))
            self.assertEqual(409, unauthorized.status_code)
        with CandidateOwnerServer(self.db_path, principal_id="verifier-a",
                                  fault_plan={("verify", "openapi-verify")}) as server:
            verify_body = VerifyRequest(expected_revision=2, evidence_ref=f"evidence:source:{request_id}")
            verified = self.http_mutation(server, "openapi-verify", verify.sync_detailed,
                                          request_id=request_id, body=verify_body)
            self.assertEqual((200, 3), (verified.status_code, verified.parsed.revision))
            self.assertEqual(3, verify.sync_detailed(request_id, client=self.http_client(server.base_url, "openapi-verify"),
                                                     body=verify_body).parsed.revision)

        decision_key = "openapi-decide-artifact"
        with CandidateOwnerServer(self.db_path, principal_id="operator-a",
                                  fault_plan={("decide-artifact", decision_key)}) as server:
            decision_body = DecideArtifactRequest(expected_revision=3, artifact_digest=signed["artifact_digest"])
            decided = self.http_mutation(server, decision_key, decide_artifact.sync_detailed,
                                         request_id=request_id, body=decision_body)
            self.assertEqual((200, 4), (decided.status_code, decided.parsed.revision))
            self.assertIn(("decide-artifact", decision_key), server.fault_consumed)
            self.assertEqual(4, decide_artifact.sync_detailed(request_id, client=self.http_client(server.base_url, decision_key),
                                                              body=decision_body).parsed.revision)

        with CandidateOwnerServer(self.db_path, principal_id="verifier-a",
                                  fault_plan={("resolve", "openapi-resolve")}) as server:
            resolve_key = "openapi-resolve"
            resolve_body = RevisionRequest(expected_revision=4)
            resolved = self.http_mutation(server, resolve_key, resolve.sync_detailed,
                                          request_id=request_id, body=resolve_body)
            self.assertEqual((200, 5), (resolved.status_code, resolved.parsed.revision))
            self.assertIn(("resolve", resolve_key), server.fault_consumed)

        for principal, dependent, expected, key in (
            ("requester-a", "work-a", 5, "openapi-ack-a"),
            ("requester-a2", "work-b", 6, "openapi-ack-b"),
        ):
            with CandidateOwnerServer(self.db_path, principal_id=principal,
                                      fault_plan={("ack-continuation", key)}) as server:
                ack_body = AcknowledgeRequest(dependent_id=dependent, expected_revision=expected)
                result = self.http_mutation(server, key, acknowledge_continuation.sync_detailed,
                                            request_id=request_id, body=ack_body)
                self.assertEqual((200, expected + 1), (result.status_code, result.parsed.revision))
                self.assertEqual(expected + 1, acknowledge_continuation.sync_detailed(
                    request_id, client=self.http_client(server.base_url, key), body=ack_body).parsed.revision)

        expected_actions = ["published", "performed-reported", "outcome-verified", "artifact-decided",
                            "resolved", "continuation-acknowledged", "continuation-acknowledged"]
        events = self.events(request_id)
        self.assertEqual(list(zip(expected_actions, range(1, 8))), [(row[1], row[2]) for row in events])

        # A separate open request proves withdrawal preserves its sibling and stale CAS has no partial effect.
        withdraw_id = "9007199254741103"
        with CandidateOwnerServer(self.db_path, principal_id="coordinator-a") as server:
            created = publish.sync_detailed(client=self.http_client(server.base_url, "openapi-withdraw-publish"),
                                            body=PublishRequest(request_id=withdraw_id, objective="withdraw case",
                                                                dependants=["work-a", "work-b"]))
            self.assertEqual(1, created.parsed.revision)
        with CandidateOwnerServer(self.db_path, principal_id="requester-a",
                                  fault_plan={("withdraw-dependent", "openapi-withdraw-a")}) as server:
            withdrawal = WithdrawRequest(dependent_id="work-a", expected_revision=1)
            result = self.http_mutation(server, "openapi-withdraw-a", withdraw_dependent.sync_detailed,
                                        request_id=withdraw_id, body=withdrawal)
            self.assertEqual((200, 2), (result.status_code, result.parsed.revision))
            stale = withdraw_dependent.sync_detailed(withdraw_id, client=self.http_client(server.base_url, "openapi-withdraw-stale"),
                                                     body=withdrawal)
            self.assertEqual(409, stale.status_code)
        with CandidateOwnerServer(self.db_path, principal_id="requester-a") as server:
            deps = self.parsed(read_dependants.sync_detailed(withdraw_id, client=self.http_client(server.base_url)))
            self.assertEqual([{"dependentId": "work-a", "active": False, "continuationAck": False},
                              {"dependentId": "work-b", "active": True, "continuationAck": False}], deps["items"])

        # Material revise clears the derived state and the previous revision's signed decision.
        with CandidateOwnerServer(self.db_path, principal_id="coordinator-a") as server:
            invalidation_id = "9007199254741105"
            invalidation_body = {"objective": "revision invalidation"}
            invalidation_signed = make_signed_artifact(workspace_id="workspace-a", request_id=invalidation_id,
                                                       material_revision=1, material_digest=material_digest(invalidation_body),
                                                       original_bytes=b"revision invalidation artifact")
            from artifact_semantics import transport_fields as artifact_fields
            f = artifact_fields(invalidation_signed)
            created = publish.sync_detailed(client=self.http_client(server.base_url, "openapi-invalidation-publish"),
                body=PublishRequest(request_id=invalidation_id, objective=invalidation_body["objective"], dependants=["work-a"],
                    artifact_digest=f["artifactDigest"], artifact_key_id=f["artifactKeyId"],
                    artifact_bytes=f["artifactBytes"], artifact_envelope=f["artifactEnvelope"], artifact_signature=f["artifactSignature"]))
            self.assertEqual(1, created.parsed.revision)
            seed_evidence(self.db_path, evidence_ref=f"evidence:source:{invalidation_id}", request_id=invalidation_id,
                          body=invalidation_body)
        with CandidateOwnerServer(self.db_path, principal_id="operator-a") as server:
            decision = decide_artifact.sync_detailed(invalidation_id, client=self.http_client(server.base_url, "invalidation-decision"),
                body=DecideArtifactRequest(expected_revision=1, artifact_digest=invalidation_signed["artifact_digest"]))
            self.assertEqual(2, decision.parsed.revision)
        with CandidateOwnerServer(self.db_path, principal_id="requester-a") as server:
            report = report_performed.sync_detailed(invalidation_id, client=self.http_client(server.base_url, "invalidation-report"),
                                                    body=RevisionRequest(expected_revision=2))
            self.assertEqual(3, report.parsed.revision)
        with CandidateOwnerServer(self.db_path, principal_id="verifier-a") as server:
            verified = verify.sync_detailed(invalidation_id, client=self.http_client(server.base_url, "invalidation-verify"),
                body=VerifyRequest(expected_revision=3, evidence_ref=f"evidence:source:{invalidation_id}"))
            self.assertEqual(4, verified.parsed.revision)
        with CandidateOwnerServer(self.db_path, principal_id="requester-a",
                                  fault_plan={("revise", "invalidation-revise")}) as server:
            revise_body = ReviseRequest(request_id=invalidation_id, expected_revision=4, objective="changed material")
            revised = self.http_mutation(server, "invalidation-revise", revise.sync_detailed,
                request_id=invalidation_id, body=revise_body)
            self.assertEqual(5, revised.parsed.revision)
            self.assertIn(("revise", "invalidation-revise"), server.fault_consumed)
            after = self.parsed(read_request.sync_detailed(invalidation_id, client=self.http_client(server.base_url)))
            self.assertEqual((False, False, None, None), (after["reported"], after["verified"], after["evidenceRef"],
                                                          after["artifactDecisionDigest"]))
            stale_decision = decide_artifact.sync_detailed(invalidation_id, client=self.http_client(server.base_url, "old-artifact-after-revise"),
                body=DecideArtifactRequest(expected_revision=5, artifact_digest=invalidation_signed["artifact_digest"]))
            self.assertEqual(409, stale_decision.status_code)
            self.assertEqual(b"revision invalidation artifact", persisted_artifact(self.db_path, invalidation_id, 1)[0])

        # Bounded page/watch cursors survive reconnect; slow polling stays bounded and is identity-bound.
        self.publish_extras_openapi("")
        with CandidateOwnerServer(self.db_path, principal_id="requester-a") as server:
            first = self.parsed(list_requests.sync_detailed(client=self.http_client(server.base_url), limit=2))
            self.assertEqual(2, len(first["items"]))
            self.assertIsNotNone(first["nextCursor"])
            oversized = list_requests.sync_detailed(client=self.http_client(server.base_url), limit=3)
            self.assertEqual(409, oversized.status_code)
            page_cursor = first["nextCursor"]
            watch_first = self.parsed(watch.sync_detailed(client=self.http_client(server.base_url), limit=1))
            self.assertEqual(1, len(watch_first["events"]))
            self.assertTrue(watch_first["more"])
            watch_cursor = watch_first["nextCursor"]
        with CandidateOwnerServer(self.db_path, principal_id="requester-a2") as peer:
            self.assertEqual(409, list_requests.sync_detailed(client=self.http_client(peer.base_url), cursor=page_cursor, limit=2).status_code)
            self.assertEqual(409, watch.sync_detailed(client=self.http_client(peer.base_url), cursor=watch_cursor, limit=1).status_code)
        with CandidateOwnerServer(self.db_path, principal_id="requester-b") as foreign:
            self.assertEqual(409, list_requests.sync_detailed(client=self.http_client(foreign.base_url), cursor=page_cursor, limit=2).status_code)
        with CandidateOwnerServer(self.db_path, principal_id="requester-a") as reopened:
            second = self.parsed(list_requests.sync_detailed(client=self.http_client(reopened.base_url), cursor=page_cursor, limit=2))
            self.assertGreaterEqual(len(second["items"]), 1)
            # New events arrive while the reader is between bounded polling calls.
            with CandidateOwnerServer(self.db_path, principal_id="coordinator-a") as writer:
                published = publish.sync_detailed(client=self.http_client(writer.base_url, "slow-poll-event"),
                    body=PublishRequest(request_id="9007199254741107", objective="arrived during slow poll", dependants=["work-a"]))
                self.assertEqual(1, published.parsed.revision)
            time.sleep(0.02)
            resumed = self.parsed(watch.sync_detailed(client=self.http_client(reopened.base_url), cursor=watch_cursor, limit=1))
            self.assertEqual(1, len(resumed["events"]))
            self.assertNotEqual((watch_first["events"][0]["action"], watch_first["events"][0]["revision"]),
                                (resumed["events"][0]["action"], resumed["events"][0]["revision"]))
            tampered = watch_cursor[:-2] + ("AA" if not watch_cursor.endswith("AA") else "BB")
            self.assertEqual(409, watch.sync_detailed(client=self.http_client(reopened.base_url), cursor=tampered, limit=1).status_code)
            db = OwnerOracle.connect_as(self.db_path, "coordinator-a")
            try:
                seq = db.db.execute("SELECT MAX(seq) FROM event").fetchone()[0]
                db.compact_events_through(seq)
            finally:
                db.close()
            self.assertEqual(409, watch.sync_detailed(client=self.http_client(reopened.base_url), cursor=watch_cursor, limit=1).status_code)

        # An already-bound owner client revalidates principal status on reads and writes.
        revoked_id = "9007199254741109"
        with CandidateOwnerServer(self.db_path, principal_id="coordinator-a") as server:
            publish.sync_detailed(client=self.http_client(server.base_url, "revocation-fixture"),
                body=PublishRequest(request_id=revoked_id, objective="revocation fixture", dependants=["work-a"]))
        with CandidateOwnerServer(self.db_path, principal_id="requester-a") as revoked:
            db = sqlite3.connect(self.db_path)
            try:
                db.execute("UPDATE identity SET active=0 WHERE principal_id='requester-a'")
                db.commit()
            finally:
                db.close()
            self.assertEqual(409, read_request.sync_detailed(revoked_id, client=self.http_client(revoked.base_url)).status_code)
            self.assertEqual(409, report_performed.sync_detailed(revoked_id, client=self.http_client(revoked.base_url, "revoked-write"),
                body=RevisionRequest(expected_revision=1)).status_code)

    def test_protobuf_native_grpc_lifecycle_uses_same_owner_and_signed_artifact(self) -> None:
        request_id = "9007199254741121"
        body, signed = self.body_artifact(request_id)

        def stub_for(principal: str):
            server = CandidateOwnerServer(self.db_path, principal_id=principal)
            server.__enter__()
            channel = grpc.insecure_channel(server.grpc_target)
            return server, channel, v1_pb2_grpc.OwnerOperationsStub(channel)

        coordinator, channel, stub = stub_for("coordinator-a")
        fields = transport_fields(signed)
        try:
            result = stub.Publish(v1_pb2.PublishRequest(
                request_id=request_id, objective=body["objective"], note=body["note"],
                dependent_ids=["work-a", "work-b", "work-c"],
                artifact_digest=fields["artifactDigest"], artifact_key_id=fields["artifactKeyId"],
                artifact_bytes=base64.b64decode(fields["artifactBytes"]),
                artifact_envelope=base64.b64decode(fields["artifactEnvelope"]),
                artifact_signature=base64.b64decode(fields["artifactSignature"]),
            ), metadata=(("idempotency-key", "grpc-lifecycle-publish"),), timeout=5)
            self.assertEqual((request_id, 1), (result.request_id, result.revision))
            request_read = stub.ReadRequest(v1_pb2.RequestById(request_id=request_id), timeout=5)
            self.assertEqual((request_id, 1, 1, v1_pb2.REQUEST_STATE_OPEN),
                             (request_read.request_id, request_read.revision,
                              request_read.material_revision, request_read.state))
            self.assertEqual(body["objective"], request_read.body.objective)
            self.assertEqual("note", request_read.body.WhichOneof("note_value"))
            self.assertEqual(body["note"], request_read.body.note)
            self.assertEqual("evidence_ref_null", request_read.WhichOneof("evidence_ref_value"))
            dependants = stub.ReadDependants(v1_pb2.RequestById(request_id=request_id), timeout=5)
            self.assertEqual([("work-a", True, False), ("work-b", True, False), ("work-c", True, False)],
                             [(item.dependent_id, item.active, item.continuation_ack) for item in dependants.items])
            stored = stub.ReadArtifact(v1_pb2.ReadArtifactRequest(request_id=request_id, material_revision=1), timeout=5)
            self.assertEqual(1, stored.material_revision)
            self.assertEqual(material_digest(body), stored.material_digest)
            self.assertEqual(signed["artifact_digest"], stored.artifact_digest)
            self.assertEqual(signed["key_id"], stored.key_id)
            self.assertEqual(signed["original_bytes"], stored.original_bytes)
            self.assertEqual(signed["envelope_bytes"], stored.envelope_bytes)
            self.assertEqual(signed["signature"], stored.signature)
        finally:
            channel.close()
            coordinator.__exit__(None, None, None)

        owner, channel, stub = stub_for("requester-a2")
        try:
            result = stub.WithdrawDependent(v1_pb2.WithdrawDependentRequest(
                request_id=request_id, dependent_id="work-c", expected_revision=1),
                metadata=(("idempotency-key", "grpc-lifecycle-withdraw"),), timeout=5)
            self.assertEqual(2, result.revision)
            with self.assertRaises(grpc.RpcError) as stale:
                stub.WithdrawDependent(v1_pb2.WithdrawDependentRequest(
                    request_id=request_id, dependent_id="work-b", expected_revision=1),
                    metadata=(("idempotency-key", "grpc-lifecycle-stale-withdraw"),), timeout=5)
            self.assertEqual(grpc.StatusCode.ABORTED, stale.exception.code())
        finally:
            channel.close()
            owner.__exit__(None, None, None)

        reporter, channel, stub = stub_for("coordinator-a")
        try:
            result = stub.ReportPerformed(v1_pb2.ReportPerformedRequest(
                request_id=request_id, expected_revision=2),
                metadata=(("idempotency-key", "grpc-lifecycle-report"),), timeout=5)
            self.assertEqual(3, result.revision)
        finally:
            channel.close()
            reporter.__exit__(None, None, None)

        verifier, channel, stub = stub_for("verifier-a")
        try:
            result = stub.Verify(v1_pb2.VerifyRequest(
                request_id=request_id, expected_revision=3, evidence_ref=f"evidence:source:{request_id}"),
                metadata=(("idempotency-key", "grpc-lifecycle-verify"),), timeout=5)
            self.assertEqual(4, result.revision)
        finally:
            channel.close()
            verifier.__exit__(None, None, None)

        operator, channel, stub = stub_for("operator-a")
        try:
            result = stub.DecideArtifact(v1_pb2.DecideArtifactRequest(
                request_id=request_id, expected_revision=4, artifact_digest=signed["artifact_digest"]),
                metadata=(("idempotency-key", "grpc-lifecycle-decision"),), timeout=5)
            self.assertEqual(5, result.revision)
        finally:
            channel.close()
            operator.__exit__(None, None, None)

        resolver, channel, stub = stub_for("verifier-a")
        try:
            result = stub.Resolve(v1_pb2.ResolveRequest(request_id=request_id, expected_revision=5),
                metadata=(("idempotency-key", "grpc-lifecycle-resolve"),), timeout=5)
            self.assertEqual(6, result.revision)
        finally:
            channel.close()
            resolver.__exit__(None, None, None)

        for principal, dependent, revision in (("requester-a", "work-a", 6), ("requester-a2", "work-b", 7)):
            owner, channel, stub = stub_for(principal)
            try:
                result = stub.AcknowledgeContinuation(v1_pb2.AcknowledgeContinuationRequest(
                    request_id=request_id, dependent_id=dependent, expected_revision=revision),
                    metadata=(("idempotency-key", f"grpc-lifecycle-ack-{dependent}"),), timeout=5)
                self.assertEqual(revision + 1, result.revision)
            finally:
                channel.close()
                owner.__exit__(None, None, None)

        self.assertEqual(["published", "dependent-withdrawn", "performed-reported", "outcome-verified",
                          "artifact-decided", "resolved", "continuation-acknowledged",
                          "continuation-acknowledged"],
                         [event[1] for event in self.events(request_id)])
        self.assertEqual(8, self.events(request_id)[-1][2])
        reader, channel, stub = stub_for("requester-a")
        try:
            request_read = stub.ReadRequest(v1_pb2.RequestById(request_id=request_id), timeout=5)
            self.assertEqual((8, v1_pb2.REQUEST_STATE_RESOLVED), (request_read.revision, request_read.state))
            self.assertTrue(request_read.verified)
            self.assertEqual(f"evidence:source:{request_id}", request_read.evidence_ref)
            page = stub.ListRequests(v1_pb2.PageRequest(limit=1), timeout=5)
            self.assertEqual(request_id, page.items[0].request_id)
            self.assertEqual(8, page.items[0].revision)
            events = stub.Watch(v1_pb2.PageRequest(limit=2), timeout=5)
            self.assertEqual(2, len(events.events))
            self.assertEqual(1, events.events[0].seq)
            self.assertTrue(events.more)
        finally:
            channel.close()
            reader.__exit__(None, None, None)

    def test_typed_reads_preserve_large_int64_and_empty_artifact_bytes(self) -> None:
        fixture = seed_typed_read_fixture(self.db_path)
        request_id = fixture["request_id"]
        revision = fixture["revision"]
        material_revision = fixture["material_revision"]
        sequence = fixture["sequence"]
        signed = fixture["signed"]
        with CandidateOwnerServer(self.db_path, principal_id="requester-a") as server:
            request_doc = self.parsed(read_request.sync_detailed(
                request_id, client=self.http_client(server.base_url)))
            self.assertEqual((str(revision), str(material_revision), str(sequence)),
                             (request_doc["revision"], request_doc["materialRevision"],
                              request_doc["createdSeq"]))
            self.assertEqual((request_id, "open", fixture["body"]["objective"]),
                             (request_doc["requestId"], request_doc["state"],
                              request_doc["body"]["objective"]))
            self.assertIsNone(request_doc["evidenceRef"])
            deps = self.parsed(read_dependants.sync_detailed(
                request_id, client=self.http_client(server.base_url)))
            self.assertEqual([{"dependentId": "work-a", "active": True, "continuationAck": False}],
                             deps["items"])
            artifact = self.parsed(read_artifact.sync_detailed(
                request_id, str(material_revision), client=self.http_client(server.base_url)))
            self.assertEqual(str(material_revision), artifact["materialRevision"])
            self.assertEqual("", artifact["originalBytes"])
            self.assertEqual(base64.b64encode(signed["envelope_bytes"]).decode(), artifact["envelopeBytes"])
            self.assertEqual(base64.b64encode(signed["signature"]).decode(), artifact["signature"])
            page = self.parsed(list_requests.sync_detailed(client=self.http_client(server.base_url), limit=2))
            self.assertEqual(str(revision), next(item["revision"] for item in page["items"]
                                                  if item["requestId"] == request_id))
            watch_page = self.parsed(watch.sync_detailed(client=self.http_client(server.base_url), limit=2))
            event = next(item for item in watch_page["events"] if item["requestId"] == request_id)
            self.assertEqual((str(sequence), str(revision)), (event["seq"], event["revision"]))

            channel = grpc.insecure_channel(server.grpc_target)
            stub = v1_pb2_grpc.OwnerOperationsStub(channel)
            try:
                proto_request = stub.ReadRequest(v1_pb2.RequestById(request_id=request_id), timeout=5)
                self.assertEqual((revision, material_revision, sequence),
                                 (proto_request.revision, proto_request.material_revision,
                                  proto_request.created_seq))
                self.assertEqual(v1_pb2.REQUEST_STATE_OPEN, proto_request.state)
                self.assertEqual("evidence_ref_null", proto_request.WhichOneof("evidence_ref_value"))
                proto_deps = stub.ReadDependants(v1_pb2.RequestById(request_id=request_id), timeout=5)
                self.assertEqual([("work-a", True, False)],
                                 [(row.dependent_id, row.active, row.continuation_ack) for row in proto_deps.items])
                proto_artifact = stub.ReadArtifact(v1_pb2.ReadArtifactRequest(
                    request_id=request_id, material_revision=material_revision), timeout=5)
                self.assertEqual(b"", proto_artifact.original_bytes)
                self.assertEqual(signed["envelope_bytes"], proto_artifact.envelope_bytes)
                self.assertEqual(signed["signature"], proto_artifact.signature)
                proto_page = stub.ListRequests(v1_pb2.PageRequest(limit=2), timeout=5)
                self.assertEqual(revision, next(item.revision for item in proto_page.items
                                                 if item.request_id == request_id))
                proto_watch = stub.Watch(v1_pb2.PageRequest(limit=2), timeout=5)
                proto_event = next(item for item in proto_watch.events if item.request_id == request_id)
                self.assertEqual((sequence, revision), (proto_event.seq, proto_event.revision))
            finally:
                channel.close()

    def test_typed_read_shape_is_known_break_from_a94_jsonresult(self) -> None:
        """The candidate replaces the old v1 JsonResult shape without negotiation."""
        fixture = seed_typed_read_fixture(self.db_path)
        request_id = fixture["request_id"]
        with CandidateOwnerServer(self.db_path, principal_id="requester-a") as server:
            with urllib.request.urlopen(f"{server.base_url}/v1/requests/{request_id}", timeout=5) as response:
                http_body = json.load(response)
            self.assertNotIn("json", http_body)
            self.assertEqual(request_id, http_body["requestId"])
            channel = grpc.insecure_channel(server.grpc_target)
            try:
                current = v1_pb2_grpc.OwnerOperationsStub(channel).ReadRequest(
                    v1_pb2.RequestById(request_id=request_id), timeout=5)
            finally:
                channel.close()

        # In a94cf67, JsonResult's only field was string json = 1. The new
        # RequestRead uses field 1 for request_id; an old decoder sees a
        # syntactically valid protobuf value with different semantics.
        legacy_file = descriptor_pb2.FileDescriptorProto(
            name="frozen-a94-json-result.proto", package="ec00.oa.v1", syntax="proto3")
        legacy = legacy_file.message_type.add(name="JsonResult")
        legacy_field = legacy.field.add(name="json", number=1,
            label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
            type=descriptor_pb2.FieldDescriptorProto.TYPE_STRING)
        pool = descriptor_pool.DescriptorPool()
        pool.Add(legacy_file)
        legacy_type = message_factory.GetMessageClass(pool.FindMessageTypeByName("ec00.oa.v1.JsonResult"))
        old_client_view = legacy_type.FromString(current.SerializeToString())
        self.assertEqual(request_id, old_client_view.json)
        # This particular string is valid JSON as a number, which makes the
        # incompatibility a silent semantic change rather than a parse error.
        decoded_old_payload = json.loads(old_client_view.json)
        self.assertEqual(int(request_id), decoded_old_payload)
        self.assertIsInstance(decoded_old_payload, int)

    def test_generated_typescript_openapi_and_protobuf_lifecycle_clients(self) -> None:
        node = os.environ.get("EC00_NODE")
        self.assertTrue(node, "EC00_NODE must point to the pinned Node 24.21.0 toolchain")
        big_read = seed_typed_read_fixture(self.db_path)
        http_id, proto_id = "9007199254741123", "9007199254741125"
        http_artifact = make_signed_artifact(workspace_id="workspace-a", request_id=http_id, material_revision=1,
            material_digest=material_digest({"objective": "generated TypeSpec lifecycle"}),
            original_bytes=b"TypeSpec artifact bytes\x00with exact retention")
        proto_artifact = make_signed_artifact(workspace_id="workspace-a", request_id=proto_id, material_revision=1,
            material_digest=material_digest({"objective": "generated protobuf lifecycle"}),
            original_bytes=b"protobuf artifact bytes\x00with exact retention")
        for request_id, body in ((http_id, {"objective": "generated TypeSpec lifecycle"}),
                                 (proto_id, {"objective": "generated protobuf lifecycle"})):
            seed_evidence(self.db_path, evidence_ref=f"evidence:source:{request_id}",
                          request_id=request_id, body=body)
        bundle = {}
        for name, artifact in (("http", http_artifact), ("proto", proto_artifact)):
            bundle[name] = {key: (base64.b64encode(value).decode("ascii") if isinstance(value, bytes) else value)
                            for key, value in artifact.items()}
        candidates = HERE / "candidates"
        big_signed = big_read["signed"]
        big_artifact = {key: base64.b64encode(big_signed[key]).decode("ascii")
                        for key in ("original_bytes", "envelope_bytes", "signature")}
        big_artifact.update({"artifact_digest": big_signed["artifact_digest"], "key_id": big_signed["key_id"]})
        env = dict(os.environ, EC00_BASE_URL="", EC00_SIGNED_ARTIFACT=json.dumps(bundle),
                   EC00_BIG_READ_ID=big_read["request_id"], EC00_BIG_READ_ARTIFACT=json.dumps(big_artifact),
                   NODE_PATH=str(candidates / "node_modules"),
                   PATH=f"{Path(node).resolve().parent}:{candidates / 'node_modules/.bin'}:{os.environ['PATH']}")
        with CandidateOwnerServer(self.db_path, principal_id="requester-a") as server:
            env["EC00_BASE_URL"] = server.base_url
            result = subprocess.run([str(candidates / "node_modules/.bin/tsx"),
                                     str(candidates / "run_typescript_clients.ts")],
                cwd=candidates, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stdout + "\n" + result.stderr)
        output = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual((http_id, proto_id), (output["httpLifecycleId"], output["protoLifecycleId"]))
        for request_id, artifact in ((http_id, http_artifact), (proto_id, proto_artifact)):
            self.assertEqual((3, "open"), self.request_state(request_id))
            self.assertEqual((artifact["original_bytes"], artifact["envelope_bytes"], artifact["signature"],
                              artifact["artifact_digest"]), persisted_artifact(self.db_path, request_id, 1))
            self.assertEqual(["published", "dependent-withdrawn", "performed-reported"],
                             [event[1] for event in self.events(request_id)])

    def test_unknown_json_and_protobuf_fields_are_ignored_without_owner_projection(self) -> None:
        json_id, proto_id = "9007199254741131", "9007199254741133"
        with CandidateOwnerServer(self.db_path, principal_id="requester-a") as server:
            json_response = httpx.post(server.base_url + "/v1/requests",
                headers={"Idempotency-Key": "unknown-json-field"},
                json={"requestId": json_id, "objective": "unknown JSON field",
                      "futureField": {"nested": "value"}})
            self.assertEqual(200, json_response.status_code)

            def varint(value: int) -> bytes:
                output = bytearray()
                while value > 0x7F:
                    output.append((value & 0x7F) | 0x80)
                    value >>= 7
                output.append(value)
                return bytes(output)

            known = v1_pb2.PublishRequest(request_id=proto_id, objective="unknown protobuf field",
                                          dependent_ids=["work-a"]).SerializeToString()
            unknown = (known + varint((100 << 3) | 2) + varint(len(b"future")) + b"future"
                       + varint((101 << 3) | 0) + varint(123)
                       + varint((102 << 3) | 1) + bytes.fromhex("0102030405060708")
                       + varint((103 << 3) | 5) + bytes.fromhex("01020304"))
            proto_response = httpx.post(server.base_url + "/proto/v1/requests", content=unknown,
                headers={"Content-Type": "application/protobuf", "Idempotency-Key": "unknown-proto-field"})
            self.assertEqual(200, proto_response.status_code)

            # NullValue is known, but an unrecognized enum number is not a new null spelling.
            # The parser keeps the enum field present; the adapter must reject rather than erase it.
            unknown_enum = (v1_pb2.PublishRequest(request_id="9007199254741135", objective="unknown enum value")
                            .SerializeToString() + varint((4 << 3) | 0) + varint(1))
            enum_response = httpx.post(server.base_url + "/proto/v1/requests", content=unknown_enum,
                headers={"Content-Type": "application/protobuf", "Idempotency-Key": "unknown-proto-enum"})
            self.assertEqual(422, enum_response.status_code)

            channel = grpc.insecure_channel(server.grpc_target)
            publish_rpc = channel.unary_unary(
                "/ec00.oa.v1.OwnerOperations/Publish",
                request_serializer=lambda value: value,
                response_deserializer=v1_pb2.MutationResult.FromString,
            )
            try:
                grpc_known = v1_pb2.PublishRequest(request_id="9007199254741137",
                    objective="gRPC unknown fields", dependent_ids=["work-a"]).SerializeToString()
                grpc_unknown = (grpc_known + varint((100 << 3) | 2) + varint(3) + b"new"
                                + varint((101 << 3) | 0) + varint(456))
                accepted = publish_rpc(grpc_unknown, metadata=(("idempotency-key", "grpc-unknown-fields"),), timeout=5)
                self.assertEqual(1, accepted.revision)
                with self.assertRaises(grpc.RpcError) as unknown_value:
                    publish_rpc(unknown_enum.replace(b"9007199254741135", b"9007199254741139"),
                                metadata=(("idempotency-key", "grpc-unknown-enum"),), timeout=5)
                self.assertEqual(grpc.StatusCode.INVALID_ARGUMENT, unknown_value.exception.code())
            finally:
                channel.close()
            self.assertIsNone(persisted_request(self.db_path, "9007199254741135"))
            self.assertIsNone(persisted_request(self.db_path, "9007199254741139"))

        for request_id, expected in ((json_id, {"objective": "unknown JSON field"}),
                                     (proto_id, {"objective": "unknown protobuf field"}),
                                     ("9007199254741137", {"objective": "gRPC unknown fields"})):
            self.assertEqual(expected, json.loads(persisted_request(self.db_path, request_id)[1]))

    def test_frozen_old_and_current_clients_interoperate_for_common_publish_revise(self) -> None:
        frozen = HERE / "compat/merge-base-5ff5"
        manifest = json.loads((frozen / "manifest.json").read_text())
        self.assertEqual("5ff5d82345d2b668ef4495656547a04877f1c275", manifest["source_commit"])
        for group in ("source_files", "test_harness_files"):
            for item in manifest[group]:
                relative = item["path"]
                expected = base64.b64decode(item["sha256_base64"]).hex()
                self.assertEqual(expected, hashlib.sha256((frozen / relative).read_bytes()).hexdigest(), relative)

        candidates = HERE / "candidates"
        node = os.environ.get("EC00_NODE")
        self.assertTrue(node, "EC00_NODE must point to the pinned Node 24.21.0 toolchain")
        with CandidateOwnerServer(self.db_path, principal_id="requester-a") as current_server:
            # Frozen Python and TypeScript generated clients call the expanded server.
            old_py = subprocess.run([sys.executable, str(frozen / "run_old_python_clients.py"),
                                     current_server.base_url], cwd=frozen, capture_output=True, text=True, timeout=20)
            self.assertEqual(0, old_py.returncode, old_py.stdout + "\n" + old_py.stderr)
            node_modules_link = frozen / "node_modules"
            self.assertFalse(node_modules_link.exists())
            node_modules_link.symlink_to(candidates / "node_modules", target_is_directory=True)
            try:
                old_ts = subprocess.run([str(candidates / "node_modules/.bin/tsx"),
                                         str(frozen / "run_old_typescript_clients.ts"), current_server.base_url],
                    cwd=frozen, env=dict(os.environ,
                        PATH=f"{Path(node).resolve().parent}:{candidates / 'node_modules/.bin'}:{os.environ['PATH']}"),
                    capture_output=True, text=True, timeout=20)
            finally:
                node_modules_link.unlink()
            self.assertEqual(0, old_ts.returncode, old_ts.stdout + "\n" + old_ts.stderr)
            old_outputs = [json.loads(old_py.stdout.strip().splitlines()[-1]),
                           json.loads(old_ts.stdout.strip().splitlines()[-1])]
            self.assertEqual({"9007199254741201", "9007199254741203", "9007199254741211", "9007199254741213"},
                             {row[key] for row in old_outputs for key in ("protoId", "httpId")})
            for row in old_outputs:
                for key in ("protoId", "httpId"):
                    self.assertEqual((2, "open"), self.request_state(row[key]))

        # Current generated clients call the frozen 5ff5 HTTP adapter and owner.
        old_db = Path(self.temp.name) / "frozen-owner.sqlite"
        old_server = subprocess.Popen([sys.executable, str(frozen / "serve_old.py"), str(old_db)],
            cwd=frozen, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready, _, _ = select.select([old_server.stdout], [], [], 10)
            self.assertTrue(ready, "frozen server did not become ready")
            old_url = old_server.stdout.readline().strip()
            self.assertTrue(old_url.startswith("http://127.0.0.1:"), old_url)

            py_proto_id = "9007199254741221"
            response = httpx.post(old_url + "/proto/v1/requests",
                content=v1_pb2.PublishRequest(request_id=py_proto_id, objective="new protobuf Python publish").SerializeToString(),
                headers={"Content-Type": "application/protobuf", "Idempotency-Key": "new-py-proto-publish"})
            self.assertEqual(200, response.status_code)
            self.assertEqual(1, v1_pb2.MutationResult.FromString(response.content).revision)
            revision = httpx.patch(old_url + f"/proto/v1/requests/{py_proto_id}",
                content=v1_pb2.ReviseRequest(request_id=py_proto_id, expected_revision=1,
                    objective="new protobuf Python revise").SerializeToString(),
                headers={"Content-Type": "application/protobuf", "Idempotency-Key": "new-py-proto-revise"})
            self.assertEqual(200, revision.status_code)
            self.assertEqual(2, v1_pb2.MutationResult.FromString(revision.content).revision)

            new_http_id = "9007199254741223"
            created = publish.sync_detailed(client=self.http_client(old_url, "new-py-http-publish"),
                body=PublishRequest(request_id=new_http_id, objective="new OpenAPI Python publish"))
            self.assertEqual((200, 1), (created.status_code, created.parsed.revision))
            changed = revise.sync_detailed(new_http_id, client=self.http_client(old_url, "new-py-http-revise"),
                body=ReviseRequest(request_id=new_http_id, expected_revision=1, objective="new OpenAPI Python revise"))
            self.assertEqual((200, 2), (changed.status_code, changed.parsed.revision))

            new_ts = subprocess.run([str(candidates / "node_modules/.bin/tsx"),
                                     str(candidates / "run_typescript_clients.ts")],
                cwd=candidates, env=dict(os.environ, EC00_BASE_URL=old_url,
                    NODE_PATH=str(candidates / "node_modules"),
                    PATH=f"{Path(node).resolve().parent}:{candidates / 'node_modules/.bin'}:{os.environ['PATH']}"),
                capture_output=True, text=True, timeout=20)
            self.assertEqual(0, new_ts.returncode, new_ts.stdout + "\n" + new_ts.stderr)
            new_ts_output = json.loads(new_ts.stdout.strip().splitlines()[-1])
            # The old server has the same public persistence column for common operations.
            db = sqlite3.connect(old_db)
            try:
                rows = db.execute("SELECT request_id,revision FROM request WHERE request_id IN (?,?)",
                                  (py_proto_id, new_http_id)).fetchall()
                self.assertEqual({(py_proto_id, 2), (new_http_id, 2)}, set(rows))
                ts_rows = db.execute("SELECT request_id,revision FROM request WHERE request_id IN (?,?)",
                    (new_ts_output["protoId"], new_ts_output["httpId"])).fetchall()
                self.assertEqual({(new_ts_output["protoId"], 2), (new_ts_output["httpId"], 2)}, set(ts_rows))
            finally:
                db.close()
        finally:
            if old_server.stdin:
                old_server.stdin.close()
            old_server.wait(timeout=10)
            if old_server.returncode not in (0, None):
                error = old_server.stderr.read() if old_server.stderr else ""
                self.fail(f"frozen server exited {old_server.returncode}: {error}")
            if old_server.stdout:
                old_server.stdout.close()
            if old_server.stderr:
                old_server.stderr.close()

    def request_state(self, request_id: str) -> tuple[int, str]:
        db = sqlite3.connect(self.db_path)
        try:
            row = db.execute("SELECT revision,state FROM request WHERE request_id=?", (request_id,)).fetchone()
            return row
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()

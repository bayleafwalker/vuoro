"""Generated unary watch clients share persisted cursor/reconnect semantics."""
from contextlib import closing
from pathlib import Path
import asyncio
import base64
import httpx
import sqlite3
import tempfile
import unittest

from lifecycle_slice_checks import (
    CandidateOwnerServer, initialize_fixture, OwnerOracle, Client, watch,
    grpc, v1_pb2, v1_pb2_grpc, UNSET,
)
from owner_oracle import ResponseLost
from transport_failure_checks import OwnerBoundaryGate


class WatchReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ec00-watch-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "owner.sqlite"
        initialize_fixture(self.path)
        for i in range(5):
            self.append(i)

    def append(self, i):
        owner = OwnerOracle.connect_as(self.path, "requester-a")
        try:
            owner.publish(str(9007199254741000 + i), {"objective": f"event {i}"}, ["work-a"], f"seed-{i}")
        finally:
            owner.close()

    def snapshot(self):
        with closing(sqlite3.connect(self.path)) as db:
            return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                    for table in ("request", "event", "idem")}

    def page(self, server, arm, cursor=None, limit=2):
        if arm == "grpc":
            with grpc.insecure_channel(server.grpc_target) as channel:
                page = v1_pb2_grpc.OwnerOperationsStub(channel).Watch(
                    v1_pb2.PageRequest(cursor=cursor or "", limit=limit), timeout=5)
            return {"events": [{"seq": str(e.seq), "requestId": e.request_id,
                                "action": e.action, "revision": str(e.revision)} for e in page.events],
                    "nextCursor": page.next_cursor if page.HasField("next_cursor") else None,
                    "more": page.more}
        with Client(base_url=server.base_url) as client:
            response = watch.sync_detailed(client=client, cursor=cursor if cursor is not None else UNSET, limit=limit)
        self.assertEqual(200, response.status_code)
        return response.parsed.to_dict()

    def refuse(self, server, cursor, limit=2):
        before = self.snapshot()
        with Client(base_url=server.base_url) as client:
            self.assertEqual(409, watch.sync_detailed(client=client, cursor=cursor, limit=limit).status_code)
        with grpc.insecure_channel(server.grpc_target) as channel:
            with self.assertRaises(grpc.RpcError) as caught:
                v1_pb2_grpc.OwnerOperationsStub(channel).Watch(
                    v1_pb2.PageRequest(cursor=cursor, limit=limit), timeout=5)
            self.assertEqual(grpc.StatusCode.ABORTED, caught.exception.code())
        self.assertEqual(before, self.snapshot())

    def test_reconnect_identical_cursor_slow_poll_and_exact_event_order(self):
        before = self.snapshot()
        with CandidateOwnerServer(self.path) as server:
            first = self.page(server, "openapi")
            self.assertEqual(first, self.page(server, "grpc"))
            self.assertEqual((2, True), (len(first["events"]), first["more"]))
        # This is delayed bounded polling, not a live subscriber or stream queue.
        self.append(5)
        expected = self.snapshot()
        self.assertEqual(6, len(expected["event"]))
        self.assertEqual(before["event"], expected["event"][:5])
        with CandidateOwnerServer(self.path) as reopened:
            self.assertEqual(first, self.page(reopened, "grpc"))
            cursor, events = first["nextCursor"], list(first["events"])
            for _ in range(4):
                left = self.page(reopened, "openapi", cursor)
                self.assertEqual(left, self.page(reopened, "grpc", cursor))
                self.assertEqual(left, self.page(reopened, "openapi", cursor))
                self.assertLessEqual(len(left["events"]), 2)
                events.extend(left["events"])
                if not left["events"]:
                    self.assertFalse(left["more"])
                    self.assertEqual(cursor, left["nextCursor"])
                    break
                cursor = left["nextCursor"]
            self.assertEqual([
                {"seq": str(row[0]), "requestId": row[2], "action": row[3], "revision": str(row[4])}
                for row in expected["event"]], events)
        self.assertEqual(expected, self.snapshot())

    def test_zero_limit_is_an_explicit_candidate_semantic_difference(self):
        before = self.snapshot()
        with CandidateOwnerServer(self.path) as server:
            default = self.page(server, "openapi")
            self.assertEqual(default, self.page(server, "grpc", limit=0))
            with Client(base_url=server.base_url) as client:
                self.assertEqual(default, watch.sync_detailed(client=client).parsed.to_dict())
                self.assertEqual(409, watch.sync_detailed(client=client, limit=0).status_code)
            with grpc.insecure_channel(server.grpc_target) as channel:
                result = v1_pb2_grpc.OwnerOperationsStub(channel).Watch(v1_pb2.PageRequest(), timeout=5)
                self.assertEqual(2, len(result.events))
                self.assertEqual(default["nextCursor"], result.next_cursor)
        self.assertEqual(before, self.snapshot())

    def test_response_loss_before_and_after_read_replays_same_cursor(self):
        with CandidateOwnerServer(self.path) as server:
            cursor = self.page(server, "grpc")["nextCursor"]
            expected = self.page(server, "openapi", cursor)
            original = server.httpd.owner_call
            before = self.snapshot()
            for arm in ("grpc", "openapi"):
                for phase in ("before", "after"):
                    with self.subTest(arm=arm, phase=phase):
                        calls = []
                        def lost(operation, *args, **kwargs):
                            calls.append(operation)
                            if phase == "after":
                                original(operation, *args, **kwargs)
                            raise ResponseLost("injected unary read response loss")
                        server.httpd.owner_call = lost
                        try:
                            if arm == "grpc":
                                with self.assertRaises(grpc.RpcError) as caught:
                                    self.page(server, arm, cursor)
                                self.assertEqual(grpc.StatusCode.UNAVAILABLE, caught.exception.code())
                            else:
                                with self.assertRaises(httpx.RemoteProtocolError):
                                    self.page(server, arm, cursor)
                        finally:
                            server.httpd.owner_call = original
                        self.assertEqual(["watch"], calls)
                        self.assertEqual(expected, self.page(server, arm, cursor))
                        self.assertEqual(before, self.snapshot())

    def test_deadline_and_cancel_preserve_read_cursor_and_owner_state(self):
        for arm in ("grpc", "openapi"):
            for phase in ("before", "after"):
                for failure in ("deadline", "cancel"):
                    with self.subTest(arm=arm, phase=phase, failure=failure):
                        with CandidateOwnerServer(self.path) as server:
                            cursor = self.page(server, arm)["nextCursor"]
                            expected = self.page(server, arm, cursor)
                            before = self.snapshot()
                            gate = OwnerBoundaryGate(server.httpd.owner_call, phase, failure)
                            server.httpd.owner_call = gate
                            try:
                                if arm == "grpc":
                                    with grpc.insecure_channel(server.grpc_target) as channel:
                                        grpc.channel_ready_future(channel).result(timeout=5)
                                        call = v1_pb2_grpc.OwnerOperationsStub(channel).Watch.future(
                                            v1_pb2.PageRequest(cursor=cursor, limit=2),
                                            timeout=1 if failure == "deadline" else 5)
                                        self.assertTrue(gate.entered.wait(5))
                                        if failure == "cancel":
                                            self.assertTrue(call.cancel())
                                            with self.assertRaises(grpc.FutureCancelledError):
                                                call.result(timeout=3)
                                        else:
                                            with self.assertRaises(grpc.RpcError) as caught:
                                                call.result(timeout=3)
                                            self.assertEqual(grpc.StatusCode.DEADLINE_EXCEEDED, caught.exception.code())
                                else:
                                    asyncio.run(self.http_cancel(server, cursor, gate, failure))
                                self.assertEqual(before, self.snapshot())
                            finally:
                                gate.release.set()
                                self.assertTrue(gate.finished.wait(5))
                                server.httpd.owner_call = gate.original
                            self.assertIsNone(gate.error)
                            self.assertEqual(expected, self.page(server, arm, cursor))
                            self.assertEqual(before, self.snapshot())

    async def http_cancel(self, server, cursor, gate, failure):
        async with Client(base_url=server.base_url,
                          timeout=httpx.Timeout(1 if failure == "deadline" else 5)) as client:
            task = asyncio.create_task(watch.asyncio_detailed(client=client, cursor=cursor, limit=2))
            try:
                self.assertTrue(await asyncio.to_thread(gate.entered.wait, 5))
                if failure == "cancel":
                    self.assertTrue(task.cancel())
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    with self.assertRaises(httpx.ReadTimeout):
                        await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    def test_cursor_refusal_parity_kind_identity_tamper_bounds_expiry_revocation(self):
        with CandidateOwnerServer(self.path) as server:
            cursor = self.page(server, "grpc")["nextCursor"]
            self.refuse(server, cursor, limit=3)
            tampered = bytearray(base64.urlsafe_b64decode(cursor))
            tampered[-1] ^= 1  # Valid Base64 and original payload; only the MAC changes.
            self.refuse(server, base64.urlsafe_b64encode(tampered).decode())
            owner = OwnerOracle.connect_as(self.path, "requester-a")
            try:
                page_cursor = owner.list_requests(limit=1)["next_cursor"]
            finally:
                owner.close()
            self.refuse(server, page_cursor)
        for principal in ("requester-a2", "requester-b"):
            with CandidateOwnerServer(self.path, principal_id=principal) as server:
                self.refuse(server, cursor)
        owner = OwnerOracle.connect_as(self.path, "coordinator-a")
        try:
            owner.compact_events_through(5)
        finally:
            owner.close()
        with CandidateOwnerServer(self.path) as server:
            self.refuse(server, cursor)
            with closing(sqlite3.connect(self.path)) as db:
                db.execute("UPDATE identity SET active=0 WHERE principal_id='requester-a'")
                db.commit()
            self.refuse(server, cursor)


if __name__ == "__main__":
    unittest.main()

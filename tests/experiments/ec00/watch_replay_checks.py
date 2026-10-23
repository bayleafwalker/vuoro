"""Generated unary watch clients share persisted cursor/reconnect semantics."""
from contextlib import closing
from pathlib import Path
import asyncio
import base64
import httpx
import sqlite3
import socket
import threading
import time
import json
import tempfile
import unittest

from lifecycle_slice_checks import (
    CandidateOwnerServer, initialize_fixture, OwnerOracle, Client, watch,
    grpc, v1_pb2, v1_pb2_grpc, UNSET,
)
from owner_oracle import ResponseLost
from transport_failure_checks import OwnerBoundaryGate


class WatchFixture(unittest.TestCase):
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


class WatchReplayTests(WatchFixture):
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



class NativeWatchReplayTests(WatchFixture):

    def native(self, server, arm, cursor=None):
        from versioned_native_checks import NativeRunner, write_native_state
        state = Path(self.temp.name) / (arm + "-restart.json")
        write_native_state(state, cursor=cursor)
        return NativeRunner(arm, server.base_url, state)

    def test_native_cursor_disconnect_restart_order_and_refusals(self):
        from versioned_counter_http import VersionedCounterServer
        from versioned_native_checks import native_command, assert_native_failure
        cursors = {}
        for arm in ("typescript", "go", "rust"):
            native_command(arm)
            with self.subTest(arm=arm):
                before = self.snapshot()
                with VersionedCounterServer(self.path) as server:
                    first = self.native(server, arm).run("watch")
                    self.assertEqual(200, first["status"])
                    self.assertEqual(self.page(server, "openapi"), first["page"])
                    cursor = first["page"]["nextCursor"]
                    expected = self.page(server, "openapi", cursor)
                    for phase in ("before", "after"):
                        for failure in ("deadline", "cancel", "transport"):
                            with self.subTest(phase=phase, failure=failure):
                                gate = OwnerBoundaryGate(server.httpd.owner_call, phase, failure)
                                server.httpd.owner_call = gate
                                runner = self.native(server, arm, cursor)
                                process = runner.start("watch", failure)
                                try:
                                    self.assertTrue(gate.entered.wait(5))
                                    if failure == "transport":
                                        gate.release.set()
                                    result = runner.finish(process, cancel=failure == "cancel")
                                    assert_native_failure(self, arm, failure, result)
                                    self.assertEqual(before, self.snapshot())
                                finally:
                                    gate.release.set()
                                    self.assertTrue(gate.finished.wait(5))
                                    server.httpd.owner_call = gate.original
                                    if process.poll() is None:
                                        process.kill()
                                        process.communicate()
                                self.assertEqual(expected, runner.run("watch")["page"])
                # Same file, new client process and new server process shell.
                with VersionedCounterServer(self.path) as restarted:
                    self.assertEqual(first["page"], self.native(restarted, arm).run("watch")["page"])
                    events, next_cursor = list(first["page"]["events"]), cursor
                    for _ in range(4):
                        runner = self.native(restarted, arm, next_cursor)
                        page = runner.run("watch")["page"]
                        self.assertEqual(page, runner.run("watch")["page"])
                        self.assertLessEqual(len(page["events"]), 2)
                        events.extend(page["events"])
                        if not page["events"]:
                            self.assertEqual(next_cursor, page["nextCursor"])
                            self.assertFalse(page["more"])
                            break
                        next_cursor = page["nextCursor"]
                    self.assertEqual([{"seq": str(r[0]), "requestId": r[2], "action": r[3],
                                       "revision": str(r[4])} for r in before["event"]], events)
                    cursors[arm] = {"expired": cursor, "terminal": next_cursor}
                    self.assertEqual(2, json.loads(base64.urlsafe_b64decode(cursor)[:-32])["position"])
                    self.assertEqual(5, json.loads(base64.urlsafe_b64decode(next_cursor)[:-32])["position"])
                for principal in ("requester-a2", "requester-b"):
                    with VersionedCounterServer(self.path, principal_id=principal) as foreign:
                        self.assertEqual(409, self.native(foreign, arm, cursor).run("watch")["status"])
                self.assertEqual(before, self.snapshot())
        owner = OwnerOracle.connect_as(self.path, "coordinator-a")
        try:
            owner.compact_events_through(5)
        finally:
            owner.close()
        compacted = self.snapshot()
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual("6", db.execute(
                "SELECT value FROM metadata WHERE key='retained_from_seq:workspace-a'").fetchone()[0])
        revocation_observations = []
        with VersionedCounterServer(self.path) as server:
            for arm in ("typescript", "go", "rust"):
                with self.subTest(arm=arm, case="expired-cursor"):
                    self.assertEqual(409, self.native(
                        server, arm, cursors[arm]["expired"]).run("watch")["status"])
                    self.assertEqual(compacted, self.snapshot())
                with self.subTest(arm=arm, case="valid-cursor-before-revocation"):
                    result = self.native(server, arm, cursors[arm]["terminal"]).run("watch")
                    self.assertEqual(200, result["status"], result)
                    self.assertEqual({"events": [], "nextCursor": cursors[arm]["terminal"],
                                      "more": False}, result["page"])
                    self.assertEqual(compacted, self.snapshot())
                    revocation_observations.append({"arm": arm, "cursorPosition": 5,
                        "retainedFromSeq": 6, "beforeRevocationStatus": result["status"]})
            with closing(sqlite3.connect(self.path)) as db:
                db.execute("UPDATE identity SET active=0 WHERE principal_id='requester-a'")
                db.commit()
                self.assertEqual(0, db.execute(
                    "SELECT active FROM identity WHERE principal_id='requester-a'").fetchone()[0])
            for arm in ("typescript", "go", "rust"):
                with self.subTest(arm=arm, case="revoked-valid-cursor"):
                    result = self.native(server, arm, cursors[arm]["terminal"]).run("watch")
                    observation = next(row for row in revocation_observations if row["arm"] == arm)
                    observation["afterRevocationStatus"] = result["status"]
                    self.assertEqual(409, result["status"], result)
                    self.assertEqual(compacted, self.snapshot())
        print("EC00_NATIVE_CURSOR_REVOCATION=" + json.dumps(revocation_observations, sort_keys=True), flush=True)


class StalledConsumerTests(WatchFixture):
    @staticmethod
    def kernel_connections(port):
        """Linux loopback observation, including orphaned FIN/TIME_WAIT sockets."""
        rows = Path("/proc/net/tcp").read_text().splitlines()[1:]
        return [row.split()[3] for row in rows
                if row.split()[1].split(":")[1] == f"{port:04X}" and row.split()[3] != "0A"]

    def test_unread_response_cohorts_bound_body_connections_threads_and_kernel_states(self):
        before = self.snapshot()
        observations = []
        with CandidateOwnerServer(self.path) as server:
            calls = []
            original = server.httpd.owner_call
            def observed(operation, *args, **kwargs):
                calls.append(operation)
                return original(operation, *args, **kwargs)
            server.httpd.owner_call = observed
            for cohort in range(3):
                sockets, admitted = [], 0
                started = time.monotonic()
                try:
                    for _ in range(40):
                        connection = socket.create_connection(server.httpd.server_address, timeout=5)
                        sockets.append(connection)
                        connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1152)
                        connection.sendall(b"GET /v1/events?limit=2 HTTP/1.0\r\nHost: fixture\r\n\r\n")
                        head = b""
                        try:
                            while not head.endswith(b"\r\n\r\n"):
                                byte = connection.recv(1)
                                if not byte:
                                    break
                                head += byte
                        except ConnectionResetError:
                            continue
                        if head:
                            self.assertIn(b"200", head.split(b"\r\n")[0])
                            body_bytes = int(head.split(b"Content-Length: ")[1].split(b"\r\n")[0])
                            self.assertLessEqual(body_bytes, 1024)
                            admitted += 1
                    with server.httpd.transport_condition:
                        active = len(server.httpd.transport_active)
                    states = self.kernel_connections(server.httpd.server_address[1])
                    owned_threads = sum(t.name.startswith("ec00-http-handler") or t.name == "ec00-http-deadline"
                                        for t in threading.enumerate())
                    observations.append({"cohort":cohort, "attempts":40, "admitted":admitted,
                        "active":active, "ownedThreads":owned_threads, "bodyBytes":body_bytes,
                        "kernelStates":states})
                    self.assertEqual(16, admitted, observations)
                    self.assertEqual(16, active, observations)
                    self.assertLessEqual(owned_threads, 32, observations)
                    self.assertLessEqual(len(states), 16, observations)
                    self.assertEqual(16, len(calls), "rejected connection reached owner")
                    self.assertEqual(24, server.httpd.transport_rejected)
                    self.assertEqual(before, self.snapshot())
                    with server.httpd.transport_condition:
                        self.assertTrue(server.httpd.transport_condition.wait_for(
                            lambda: not server.httpd.transport_active, timeout=5), observations)
                    observations[-1]["settledSeconds"] = round(time.monotonic() - started, 6)
                    self.assertLessEqual(observations[-1]["settledSeconds"], 3.0, observations)
                    self.assertEqual([], self.kernel_connections(server.httpd.server_address[1]), observations)
                    # Valid consumers make progress after each stalled cohort settles.
                    page = self.page(server, "openapi")
                    self.assertEqual((2, True), (len(page["events"]), page["more"]))
                    self.assertEqual(page, self.page(server, "openapi"))
                    with server.httpd.transport_condition:
                        self.assertTrue(server.httpd.transport_condition.wait_for(
                            lambda: not server.httpd.transport_active, timeout=5))
                    self.assertEqual([], self.kernel_connections(server.httpd.server_address[1]))
                    calls.clear()
                    # Count only this next cohort, not the two progress controls.
                    server.httpd.transport_rejected = 0
                finally:
                    for connection in sockets:
                        connection.close()
            self.assertEqual(16, server.httpd.transport_peak_active)
        print("EC00_STALLED_CONSUMERS=" + json.dumps(observations, sort_keys=True), flush=True)
        self.assertEqual(before, self.snapshot())



if __name__ == "__main__":
    unittest.main()

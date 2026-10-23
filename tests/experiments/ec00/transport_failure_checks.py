"""Loopback generated-client failures; client cancellation is not owner rollback."""
from __future__ import annotations

import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from lifecycle_slice_checks import (
    Client, PublishRequest, publish, v1_pb2, v1_pb2_grpc, grpc, httpx,
    CandidateOwnerServer, initialize_fixture,
)
from owner_oracle import ResponseLost


class OwnerBoundaryGate:
    """Pause one real call before invocation or after its committed return.

    Installed only by these tests; no wire flags, owner or runtime changes.
    The server handler remains alive when the client deadline/cancellation fires.
    """

    def __init__(self, original, phase: str, failure: str):
        self.original, self.phase, self.failure = original, phase, failure
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.calls = 0
        self.error = None

    def __call__(self, operation, *args, **kwargs):
        self.calls += 1
        if self.calls != 1:
            return self.original(operation, *args, **kwargs)
        try:
            result = None
            if self.phase == "after":
                result = self.original(operation, *args, **kwargs)
            self.entered.set()
            if not self.release.wait(10):
                raise AssertionError("test failed to release owner boundary")
            if self.failure == "transport":
                raise ResponseLost("injected response unavailability at owner boundary")
            if self.phase == "before":
                result = self.original(operation, *args, **kwargs)
            return result
        except BaseException as exc:
            self.error = exc
            raise
        finally:
            self.finished.set()


class TransportFailureTests(unittest.TestCase):
    def test_python_predispatch_cancellation_and_expired_await_budget_have_no_effect(self):
        for failure in ("deadline", "cancel"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "owner.sqlite"
                initialize_fixture(path)
                with CandidateOwnerServer(path) as server:
                    calls = []
                    original = server.httpd.owner_call
                    def observed(*args, **kwargs):
                        calls.append(args[0])
                        return original(*args, **kwargs)
                    server.httpd.owner_call = observed
                    async def exercise():
                        entered, never_send = asyncio.Event(), asyncio.Event()
                        async def before_send(request):
                            entered.set()
                            await never_send.wait()
                        async with Client(base_url=server.base_url,
                                httpx_args={"event_hooks": {"request": [before_send]}}) as client:
                            task = asyncio.create_task(publish.asyncio_detailed(client=client,
                                body=PublishRequest(request_id="9007199254740993", objective="predispatch")))
                            await asyncio.wait_for(entered.wait(), 5)
                            if failure == "cancel":
                                task.cancel()
                                with self.assertRaises(asyncio.CancelledError):
                                    await task
                            else:
                                # Expire the native asyncio await budget while the
                                # real httpx request is paused before socket send.
                                with self.assertRaises(TimeoutError):
                                    await asyncio.wait_for(task, timeout=0)
                    asyncio.run(exercise())
                    self.assertEqual([], calls)
                    self.assertEqual((0, 0, 0), self.snapshot(path))

    def snapshot(self, path):
        with closing(sqlite3.connect(path)) as db:
            return tuple(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                         for table in ("request", "event", "idem"))

    def run_case(self, arm, phase, failure):
        with tempfile.TemporaryDirectory(prefix="ec00-transport-") as directory:
            path = Path(directory) / "owner.sqlite"
            initialize_fixture(path)
            with CandidateOwnerServer(path) as server:
                gate = OwnerBoundaryGate(server.httpd.owner_call, phase, failure)
                server.httpd.owner_call = gate
                try:
                    if arm == "grpc":
                        self.grpc_failure(server, gate, path, phase, failure)
                    else:
                        asyncio.run(self.http_failure(server, gate, path, phase, failure))
                    self.assertTrue(gate.finished.wait(5), "owner call did not terminate")
                    if failure != "transport":
                        self.assertIsNone(gate.error)
                    # A dispatched handler may commit after its client has gone away.
                    expected = 0 if phase == "before" and failure == "transport" else 1
                    self.assertEqual((expected,) * 3, self.snapshot(path))
                    server.httpd.owner_call = gate.original
                    self.replay_and_refuse(server, arm, path)
                finally:
                    gate.release.set()
                    gate.finished.wait(5)

    def grpc_failure(self, server, gate, path, phase, failure):
        with grpc.insecure_channel(server.grpc_target) as channel:
            grpc.channel_ready_future(channel).result(timeout=5)
            stub = v1_pb2_grpc.OwnerOperationsStub(channel)
            call = stub.Publish.future(
                v1_pb2.PublishRequest(request_id="9007199254740993", objective="bounded effect"),
                metadata=(("idempotency-key", "failure-key"),),
                timeout=1 if failure == "deadline" else 5,
            )
            self.assertTrue(gate.entered.wait(5), "RPC never reached owner boundary")
            self.assertEqual((int(phase == "after"),) * 3, self.snapshot(path))
            if failure == "cancel":
                self.assertTrue(call.cancel())
                with self.assertRaises(grpc.FutureCancelledError):
                    call.result(timeout=3)
            elif failure == "deadline":
                with self.assertRaises(grpc.RpcError) as caught:
                    call.result(timeout=3)
                self.assertEqual(grpc.StatusCode.DEADLINE_EXCEEDED, caught.exception.code())
            else:
                gate.release.set()
                with self.assertRaises(grpc.RpcError) as caught:
                    call.result(timeout=3)
                self.assertEqual(grpc.StatusCode.UNAVAILABLE, caught.exception.code())
            gate.release.set()

    async def http_failure(self, server, gate, path, phase, failure):
        client = Client(base_url=server.base_url, headers={"Idempotency-Key": "failure-key"},
                        timeout=httpx.Timeout(1 if failure == "deadline" else 5))
        async with client:
            task = asyncio.create_task(publish.asyncio_detailed(
                client=client, body=PublishRequest(request_id="9007199254740993",
                                                  objective="bounded effect")))
            try:
                self.assertTrue(await asyncio.to_thread(gate.entered.wait, 5))
                self.assertEqual((int(phase == "after"),) * 3, self.snapshot(path))
                if failure == "cancel":
                    self.assertTrue(task.cancel())
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                elif failure == "deadline":
                    with self.assertRaises(httpx.ReadTimeout):
                        await task
                else:
                    gate.release.set()
                    with self.assertRaises(httpx.RemoteProtocolError):
                        await task
            finally:
                gate.release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    def replay_and_refuse(self, server, arm, path):
        for objective, success in (("bounded effect", True), ("bounded effect", True),
                                   ("changed effect", False)):
            if arm == "grpc":
                with grpc.insecure_channel(server.grpc_target) as channel:
                    stub = v1_pb2_grpc.OwnerOperationsStub(channel)
                    request = v1_pb2.PublishRequest(request_id="9007199254740993", objective=objective)
                    if success:
                        result = stub.Publish(request, metadata=(("idempotency-key", "failure-key"),), timeout=5)
                        self.assertEqual(("9007199254740993", 1), (result.request_id, result.revision))
                    else:
                        with self.assertRaises(grpc.RpcError) as caught:
                            stub.Publish(request, metadata=(("idempotency-key", "failure-key"),), timeout=5)
                        self.assertEqual(grpc.StatusCode.ABORTED, caught.exception.code())
            else:
                with Client(base_url=server.base_url, headers={"Idempotency-Key": "failure-key"}) as client:
                    result = publish.sync_detailed(client=client, body=PublishRequest(
                        request_id="9007199254740993", objective=objective))
                    self.assertEqual(200 if success else 409, result.status_code)
                    if success:
                        self.assertEqual(("9007199254740993", 1),
                                         (result.parsed.request_id, result.parsed.revision))
            self.assertEqual((1, 1, 1), self.snapshot(path))
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual('{"objective":"bounded effect"}',
                             db.execute("SELECT body_json FROM request").fetchone()[0])

    def test_generated_clients_before_and_after_commit_failures(self):
        for arm in ("grpc", "openapi"):
            for phase in ("before", "after"):
                for failure in ("deadline", "cancel", "transport"):
                    with self.subTest(arm=arm, phase=phase, failure=failure):
                        self.run_case(arm, phase, failure)



class NativeTransportFailureTests(unittest.TestCase):
    """Actual generated HTTP clients; process exit never implies owner rollback."""

    def test_native_predispatch_expired_deadline_and_cancel_have_no_call_or_effect(self):
        from versioned_native_checks import NativeRunner, native_command, write_native_state, assert_native_failure
        from versioned_counter_checks import initialize_counter, snapshot
        from versioned_counter_http import VersionedCounterServer
        for arm in ("typescript", "go", "rust"):
            native_command(arm)  # Compilation is outside the boundary/deadline observation.
            for failure in ("deadline", "cancel"):
                with self.subTest(arm=arm, failure=failure), tempfile.TemporaryDirectory() as directory:
                    path, state = Path(directory) / "owner.sqlite", Path(directory) / "restart.json"
                    initialize_counter(path, 1)
                    write_native_state(state)
                    before = snapshot(path)
                    with VersionedCounterServer(path) as server:
                        calls = []
                        original = server.httpd.owner_call
                        def observed(*args, **kwargs):
                            calls.append(args[0])
                            return original(*args, **kwargs)
                        server.httpd.owner_call = observed
                        result = NativeRunner(arm, server.base_url, state).run("attempt", failure, True)
                        assert_native_failure(self, arm, failure, result)
                        self.assertEqual([], calls, "predispatch failure reached the owner")
                    self.assertEqual(before, snapshot(path))

    def test_native_dispatched_cancel_deadline_loss_and_restarted_reconciliation(self):
        from versioned_native_checks import NativeRunner, native_command, write_native_state, assert_native_failure
        from versioned_counter_checks import initialize_counter, snapshot
        from versioned_counter_http import VersionedCounterServer
        for arm in ("typescript", "go", "rust"):
            native_command(arm)
            for phase in ("before", "after"):
                for failure in ("deadline", "cancel", "transport"):
                    with self.subTest(arm=arm, phase=phase, failure=failure), tempfile.TemporaryDirectory() as directory:
                        path, state = Path(directory) / "owner.sqlite", Path(directory) / "restart.json"
                        initialize_counter(path, 1)
                        write_native_state(state)
                        before = snapshot(path)
                        with VersionedCounterServer(path) as server:
                            runner = NativeRunner(arm, server.base_url, state)
                            gate = OwnerBoundaryGate(server.httpd.owner_call, phase, failure)
                            server.httpd.owner_call = gate
                            process = runner.start("attempt", failure)
                            try:
                                self.assertTrue(gate.entered.wait(5), "native call never reached owner boundary")
                                at_boundary = snapshot(path)
                                self.assertEqual(1 + int(phase == "after"), len(at_boundary["event"]))
                                if failure == "transport":
                                    gate.release.set()
                                result = runner.finish(process, cancel=failure == "cancel")
                                assert_native_failure(self, arm, failure, result)
                                self.assertEqual(at_boundary, snapshot(path))
                            finally:
                                gate.release.set()
                                self.assertTrue(gate.finished.wait(5))
                                server.httpd.owner_call = gate.original
                                if process.poll() is None:
                                    process.kill()
                                    process.communicate()
                            if failure != "transport":
                                self.assertIsNone(gate.error)
                            expected = 1 if phase == "before" and failure == "transport" else 2
                            self.assertEqual(expected, len(snapshot(path)["event"]))
                        # A fresh server and fresh native processes restore the same key.
                        with VersionedCounterServer(path) as restarted:
                            runner = NativeRunner(arm, restarted.base_url, state)
                            for _ in range(2):
                                result = runner.run("reconcile")
                                self.assertEqual((200, 200, 409),
                                    (result["status"], result["replayStatus"], result["changedStatus"]))
                                self.assertEqual({"requestId": "9007199254741199", "revision": "2"}, result["first"])
                                self.assertEqual(result["first"], result["replay"])
                                after = snapshot(path)
                                self.assertEqual(before["event"], after["event"][:-1])
                                self.assertEqual((2, 2, 2), (after["request"][0][2], len(after["event"]), len(after["idem"])))
                                self.assertEqual('{"objective":"native bounded effect"}', after["request"][0][5])

if __name__ == "__main__":
    unittest.main()

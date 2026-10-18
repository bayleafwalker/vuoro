"""Generated-stub-only client exercising the shared 59fa/current gRPC surface."""
from __future__ import annotations

import json
import sys

import grpc
from oa import v1_pb2, v1_pb2_grpc


def expect_code(call, expected: grpc.StatusCode) -> None:
    try:
        call()
    except grpc.RpcError as exc:
        if exc.code() != expected:
            raise AssertionError(f"expected {expected}, got {exc.code()}: {exc.details()}") from exc
    else:
        raise AssertionError(f"expected gRPC status {expected}")


def main() -> None:
    target, request_id = sys.argv[1:]
    channel = grpc.insecure_channel(target)
    stub = v1_pb2_grpc.OwnerOperationsStub(channel)
    try:
        common = dict(timeout=5)
        publish = v1_pb2.PublishRequest(request_id=request_id, objective="first objective")
        publish.note = ""  # present empty string remains distinct from absent.
        key = (("idempotency-key", "native-compat-publish"),)
        first = stub.Publish(publish, metadata=key, **common)
        replay = stub.Publish(publish, metadata=key, **common)
        assert first.revision == replay.revision == 1

        changed_publish = v1_pb2.PublishRequest(request_id=request_id, objective="different")
        expect_code(lambda: stub.Publish(changed_publish, metadata=key, **common), grpc.StatusCode.ABORTED)

        missing_revision = v1_pb2.ReviseRequest(request_id=request_id, objective="missing revision")
        expect_code(lambda: stub.Revise(missing_revision,
                    metadata=(("idempotency-key", "native-compat-absent"),), **common),
                    grpc.StatusCode.INVALID_ARGUMENT)
        zero_revision = v1_pb2.ReviseRequest(request_id=request_id, expected_revision=0,
                                               objective="zero revision")
        expect_code(lambda: stub.Revise(zero_revision,
                    metadata=(("idempotency-key", "native-compat-zero"),), **common),
                    grpc.StatusCode.ABORTED)

        revise = v1_pb2.ReviseRequest(request_id=request_id, expected_revision=1,
                                      objective="second objective")
        revise.note_null = 0
        revise_key = (("idempotency-key", "native-compat-revise"),)
        second = stub.Revise(revise, metadata=revise_key, **common)
        replayed_second = stub.Revise(revise, metadata=revise_key, **common)
        assert second.revision == replayed_second.revision == 2
        stale = v1_pb2.ReviseRequest(request_id=request_id, expected_revision=1,
                                     objective="stale objective")
        expect_code(lambda: stub.Revise(stale,
                    metadata=(("idempotency-key", "native-compat-stale"),), **common),
                    grpc.StatusCode.ABORTED)
        absent_id = str(int(request_id) + 2)
        absent_note = v1_pb2.PublishRequest(request_id=absent_id, objective="absent note")
        absent_created = stub.Publish(absent_note,
            metadata=(("idempotency-key", "native-compat-absent-note"),), **common)
        empty_id = str(int(request_id) + 4)
        empty_note = v1_pb2.PublishRequest(request_id=empty_id, objective="present empty note")
        empty_note.note = ""
        empty_created = stub.Publish(empty_note,
            metadata=(("idempotency-key", "native-compat-empty-note"),), **common)
        assert absent_created.revision == empty_created.revision == 1
        print(json.dumps({"request_id": request_id, "absent_note_id": absent_id,
                          "empty_note_id": empty_id,
                          "revisions": [first.revision, second.revision],
                          "client_schema": "selected-by-pinned-PYTHONPATH"}))
    finally:
        channel.close()


if __name__ == "__main__":
    main()

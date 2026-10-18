"""Exercise frozen Python clients from merge base 5ff5d823 against a new server."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "generated/protobuf/python"))
sys.path.insert(0, str(HERE / "generated/typespec/clients/python"))

from oa import v1_pb2
from ec00_oa_python_client.api.default import publish, revise
from ec00_oa_python_client.client import Client
from ec00_oa_python_client.models import PublishRequest, ReviseRequest


def proto_call(base: str, method: str, path: str, message: object, key: str) -> tuple[int, bytes]:
    response = httpx.request(method, base + path, content=message.SerializeToString(), timeout=5,
        headers={"Content-Type": "application/protobuf", "Idempotency-Key": key})
    return response.status_code, response.content


def main() -> None:
    base = sys.argv[1]
    proto_id = "9007199254741201"
    status, raw = proto_call(base, "POST", "/proto/v1/requests",
        v1_pb2.PublishRequest(request_id=proto_id, objective="frozen protobuf Python publish"), "old-py-proto-publish")
    assert status == 200
    result = v1_pb2.MutationResult.FromString(raw)
    assert (result.request_id, result.revision) == (proto_id, 1)
    status, raw = proto_call(base, "PATCH", f"/proto/v1/requests/{proto_id}",
        v1_pb2.ReviseRequest(request_id=proto_id, expected_revision=1,
                             objective="frozen protobuf Python revise"), "old-py-proto-revise")
    assert status == 200
    assert v1_pb2.MutationResult.FromString(raw).revision == 2

    http_id = "9007199254741203"
    client = Client(base_url=base, headers={"Idempotency-Key": "old-py-http-publish"},
                    raise_on_unexpected_status=False)
    result = publish.sync_detailed(client=client,
        body=PublishRequest(request_id=http_id, objective="frozen OpenAPI Python publish"))
    assert result.status_code == 200 and result.parsed.revision == 1
    result = revise.sync_detailed(http_id,
        client=Client(base_url=base, headers={"Idempotency-Key": "old-py-http-revise"},
                      raise_on_unexpected_status=False),
        body=ReviseRequest(request_id=http_id, expected_revision=1, objective="frozen OpenAPI Python revise"))
    assert result.status_code == 200 and result.parsed.revision == 2
    print(json.dumps({"protoId": proto_id, "httpId": http_id, "revisions": [2, 2]}))


if __name__ == "__main__":
    main()

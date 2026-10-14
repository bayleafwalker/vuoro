"""Database-free units: ULIDs, object stores (fs and stubbed s3), sniffing, extraction, cursors."""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest

from vuoro_workspace import FsObjectStore, S3ObjectStore, WorkspaceError, new_ulid, sniff_media_type
from vuoro_workspace.extraction import extract
from vuoro_workspace.ids import is_ulid, request_digest
from vuoro_workspace.objectstore import ObjectStore, artifact_key, resolve_media_type
from vuoro_workspace.store import _decode_cursor, _encode_cursor


def test_ulids_are_valid_unique_and_monotonic():
    ids = [new_ulid() for _ in range(2000)]
    assert all(is_ulid(i) for i in ids)
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)
    assert new_ulid(now_ms=0) < new_ulid()


def test_request_digest_is_canonical():
    assert request_digest("op", {"a": 1, "b": [1, 2]}) == request_digest("op", {"b": [1, 2], "a": 1})
    assert request_digest("op", {"a": 1}) != request_digest("op2", {"a": 1})


def _exercise(store: ObjectStore) -> None:
    key = artifact_key("W", "A", "V")
    assert store.head(key) is None and store.get(key) is None
    store.put(key, b"hello")
    info = store.head(key)
    assert info is not None and info.size == 5
    assert store.get(key) == b"hello"
    store.put("ws/W/art/A/V2", b"x")
    store.put("ws/OTHER/art/A/V", b"y")
    assert sorted(o.key for o in store.list("ws/W/")) == ["ws/W/art/A/V", "ws/W/art/A/V2"]
    store.delete(key)
    assert store.head(key) is None
    store.delete(key)  # idempotent
    for bad in ("../etc/passwd", "/abs", "ws/../x", ""):
        with pytest.raises(ValueError):
            store.put(bad, b"")


def test_fs_object_store(tmp_path):
    store = FsObjectStore(tmp_path)
    assert isinstance(store, ObjectStore)
    _exercise(store)


class _NotFound(Exception):
    def __init__(self) -> None:
        self.response = {"Error": {"Code": "404"}}


class StubS3:
    """Just enough of the boto3 S3 client surface, with pagination."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.calls: list[str] = []

    def put_object(self, Bucket, Key, Body):
        self.calls.append("put")
        self.objects[(Bucket, Key)] = bytes(Body)

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise _NotFound()
        return {"ContentLength": len(self.objects[(Bucket, Key)]), "LastModified": datetime.now(timezone.utc)}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise _NotFound()
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for b, k in self.objects if b == Bucket and k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + 1]
        more = start + 1 < len(keys)
        return {"Contents": [{"Key": k, "Size": len(self.objects[(Bucket, k)]),
                              "LastModified": datetime.now(timezone.utc)} for k in page],
                "IsTruncated": more, "NextContinuationToken": str(start + 1) if more else None}


def test_s3_object_store_with_stub_client():
    client = StubS3()
    store = S3ObjectStore("bucket", prefix="vuoro/", client=client)
    _exercise(store)
    assert all(k.startswith("vuoro/ws/") for _, k in client.objects)


def test_s3_errors_other_than_not_found_propagate():
    class Broken(StubS3):
        def head_object(self, Bucket, Key):
            raise RuntimeError("access denied")

    with pytest.raises(RuntimeError):
        S3ObjectStore("b", client=Broken()).head("ws/a")


@pytest.mark.parametrize(("data", "expected"), [
    (b"%PDF-1.7\n", "application/pdf"),
    (b"\x89PNG\r\n\x1a\nxxxx", "image/png"),
    (b"\xff\xd8\xff\xe0", "image/jpeg"),
    (b"PK\x03\x04rest", "application/zip"),
    (b"# Title\n", "text/plain"),
    (b"\x00\x01\x02\xff", "application/octet-stream"),
])
def test_sniff(data, expected):
    assert sniff_media_type(data) == expected


def test_resolve_media_type_rules():
    assert resolve_media_type("text/markdown", b"# x")[0] == "text/markdown"
    assert resolve_media_type(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"PK\x03\x04")[0] is not None
    assert resolve_media_type("application/pdf", b"# not a pdf")[0] is None
    assert resolve_media_type("image/png", b"%PDF-")[0] is None
    # Anything outside the text allowlist and the magic-verified types is stored as octet-stream.
    assert resolve_media_type("application/x-custom", b"\x00\x01\xff")[0] == "application/octet-stream"
    assert resolve_media_type("text/html", b"<html><script>x</script>")[0] == "application/octet-stream"
    assert resolve_media_type("image/svg+xml", b"<svg onload=alert(1)>")[0] == "application/octet-stream"
    assert resolve_media_type("text/csv", b"a,b\n1,2\n")[0] == "text/csv"
    assert resolve_media_type("text/plain", b"%PDF-1.4")[0] is None
    assert resolve_media_type("application/x-custom", b"%PDF-1.4")[0] is None


def test_extraction_states():
    md = extract("text/markdown", b"# Head\nbody\n## Sub\n")
    assert md.state == "ready" and [a["text"] for a in md.anchors] == ["Head", "Sub"]
    assert extract("text/plain", b"plain").text == "plain"
    assert extract("image/png", b"\x89PNG").state == "unsupported"
    assert extract("application/octet-stream", b"<html>").state == "unsupported"
    big = extract("text/plain", b"word " * 300_000)
    assert big.state == "ready" and big.truncated and len(big.text) == 1_000_000
    assert extract("text/plain", b"\xff\xfe").state == "failed"
    pdf = extract("application/pdf", b"%PDF-1.4 broken")
    try:
        import pypdf  # noqa: F401
    except ImportError:
        assert pdf.state == "unsupported"
    else:
        assert pdf.state == "failed"


def test_cursor_is_opaque_and_workspace_bound():
    c = _encode_cursor("W1", 7)
    assert _decode_cursor(c, "W1") == 7
    for bad, ws in ((c, "W2"), ("not-a-cursor!!", "W1"), ("", "W1")):
        with pytest.raises(WorkspaceError) as info:
            _decode_cursor(bad, ws)
        assert info.value.code == "invalid-cursor"

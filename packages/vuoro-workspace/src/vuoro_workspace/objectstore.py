"""Pluggable object store for artifact bytes.

Keys have the form ``ws/<workspace_id>/art/<artifact_id>/<version_id>``.
``fs`` serves tests and single-node use; ``s3`` serves any S3-compatible
bucket under a per-deployment prefix (boto3 is the optional ``s3`` extra).
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$")


def artifact_key(workspace_id: str, artifact_id: str, version_id: str) -> str:
    return f"ws/{workspace_id}/art/{artifact_id}/{version_id}"


def workspace_prefix(workspace_id: str) -> str:
    return f"ws/{workspace_id}/"


def _check_key(key: str) -> str:
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key) or ".." in key.split("/"):
        raise ValueError(f"invalid object key: {key!r}")
    return key


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    key: str
    size: int
    modified_at: datetime


@runtime_checkable
class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def head(self, key: str) -> ObjectInfo | None: ...
    def get(self, key: str) -> bytes | None: ...
    def delete(self, key: str) -> None: ...
    def list(self, prefix: str) -> Iterator[ObjectInfo]: ...


class FsObjectStore:
    """Filesystem backend; writes are atomic (temp file + rename)."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / _check_key(key)

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def head(self, key: str) -> ObjectInfo | None:
        path = self._path(key)
        try:
            st = path.stat()
        except FileNotFoundError:
            return None
        return ObjectInfo(key, st.st_size, datetime.fromtimestamp(st.st_mtime, timezone.utc))

    def get(self, key: str) -> bytes | None:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            return None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list(self, prefix: str) -> Iterator[ObjectInfo]:
        base = self.root / prefix.rstrip("/")
        if not base.is_dir():
            return
        for path in sorted(base.rglob("*")):
            if path.is_file() and not path.name.startswith(".tmp-"):
                key = path.relative_to(self.root).as_posix()
                st = path.stat()
                yield ObjectInfo(key, st.st_size, datetime.fromtimestamp(st.st_mtime, timezone.utc))


def _is_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None) or {}
    code = str((response.get("Error") or {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


class S3ObjectStore:
    """S3-compatible backend. ``client`` is a boto3 S3 client (or a compatible stub)."""

    def __init__(self, bucket: str, prefix: str = "", client: Any | None = None, **client_kwargs: Any) -> None:
        if client is None:
            try:
                import boto3  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - depends on extras
                raise RuntimeError("the s3 backend needs the 'vuoro-workspace[s3]' extra (boto3)") from exc
            client = boto3.client("s3", **client_kwargs)
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""

    def _k(self, key: str) -> str:
        return self.prefix + _check_key(key)

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._k(key), Body=data)

    def head(self, key: str) -> ObjectInfo | None:
        try:
            resp = self.client.head_object(Bucket=self.bucket, Key=self._k(key))
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise
        return ObjectInfo(key, int(resp["ContentLength"]), resp["LastModified"])

    def get(self, key: str) -> bytes | None:
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=self._k(key))
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise
        return resp["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._k(key))

    def list(self, prefix: str) -> Iterator[ObjectInfo]:
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": self.prefix + prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self.client.list_objects_v2(**kwargs)
            for item in resp.get("Contents", []):
                yield ObjectInfo(item["Key"][len(self.prefix):], int(item["Size"]), item["LastModified"])
            if not resp.get("IsTruncated"):
                return
            token = resp.get("NextContinuationToken")


# --- media type sniffing ---------------------------------------------------

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"PK\x03\x04", "application/zip"),
    (b"\x1f\x8b", "application/gzip"),
)
_ZIP_CONTAINERS = {
    "application/zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/epub+zip",
}
OCTET_STREAM = "application/octet-stream"
# Text subtypes stored, extracted and indexed as text. Others (text/html,
# image/svg+xml, application/xml, ...) are stored as octet-stream.
TEXT_TYPES = frozenset({
    "text/plain", "text/markdown", "text/x-markdown", "text/csv", "text/tab-separated-values",
    "application/json", "application/yaml", "application/x-yaml", "text/yaml",
})
_MAGIC_TYPES = {t for _, t in _MAGIC} | _ZIP_CONTAINERS


def sniff_media_type(data: bytes) -> str:
    """Best-effort type from content: a magic number, UTF-8 text, or octet-stream."""
    for magic, media_type in _MAGIC:
        if data.startswith(magic):
            return media_type
    try:
        sample = data[:8192]
        sample.decode("utf-8")
        if b"\x00" not in sample:
            return "text/plain"
    except UnicodeDecodeError:
        # A multi-byte sequence cut at the sample boundary is still text.
        try:
            data[:8188].decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError:
            pass
    return "application/octet-stream"


def resolve_media_type(declared: str, data: bytes) -> tuple[str | None, str]:
    """Return (stored media type or None on mismatch, sniffed type).

    Only allow-listed text types and magic-verified binary types keep their
    declared type. Anything else that does not contradict its content (HTML,
    SVG, XML, unknown binaries) is stored as ``application/octet-stream``:
    the workspace never serves active content, and every download is
    delivered as an attachment.
    """
    declared = declared.split(";", 1)[0].strip().lower()
    sniffed = sniff_media_type(data)
    if declared in TEXT_TYPES:
        return (declared if sniffed == "text/plain" else None), sniffed
    if declared in _MAGIC_TYPES:
        if sniffed == declared or (sniffed == "application/zip" and declared in _ZIP_CONTAINERS):
            return declared, sniffed
        return None, sniffed
    if sniffed in _MAGIC_TYPES:
        # A known binary format declared as something else is a mismatch.
        return None, sniffed
    return OCTET_STREAM, sniffed

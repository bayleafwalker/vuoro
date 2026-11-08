"""Lookup the published tenant work-schema contract for a service release."""

from __future__ import annotations

import json
from importlib.resources import files
import re
from typing import Any


_PINNED_RELEASE = re.compile(r"^vuoro-service-v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)@sha256:[0-9a-f]{64}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class UnknownServiceVersionError(ValueError):
    """Raised when the schema map has no entry for a service version."""


def lookup_tenant_schema(version: str) -> dict[str, Any]:
    """Return a release mapping for a semantic version or pinned image identity.

    The pinned form's SHA-256 suffix is shape-checked only; it is not bound to
    an image or wheel digest. Raises ``ValueError`` for malformed inputs and
    ``UnknownServiceVersionError`` for well-formed versions absent from the map.
    """
    if not isinstance(version, str):
        raise ValueError("service version must be a string")
    match = _PINNED_RELEASE.fullmatch(version)
    if match:
        version = match.group("version")
    elif not _VERSION.fullmatch(version):
        raise ValueError("expected a service version like 0.1.105 or a pinned vuoro-service-v<version>@sha256:<digest>")

    mapping = json.loads(files("vuoro_service").joinpath("tenant-runtime-schema-map.json").read_text(encoding="utf-8"))
    for row in mapping["releases"]:
        if row["service_version"] == version:
            return row
    raise UnknownServiceVersionError(f"no tenant work-schema mapping for vuoro-service {version}")

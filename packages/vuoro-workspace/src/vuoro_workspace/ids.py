"""ULID identifiers and canonical request digests (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import date, datetime
from typing import Any

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_lock = threading.Lock()
_last_ms = -1
_last_rand = 0


def new_ulid(now_ms: int | None = None) -> str:
    """Return a 26-character Crockford ULID, monotonic within one process."""
    global _last_ms, _last_rand
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    with _lock:
        if ms <= _last_ms:
            ms = _last_ms
            rand = (_last_rand + 1) & ((1 << 80) - 1)
        else:
            rand = int.from_bytes(os.urandom(10), "big")
        _last_ms, _last_rand = ms, rand
    value = (ms << 80) | rand
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def is_ulid(value: Any) -> bool:
    return isinstance(value, str) and bool(ULID_RE.fullmatch(value))


def _default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return sorted(value) if isinstance(value, (set, frozenset)) else list(value)
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default)


def normalize(value: Any) -> Any:
    """Round-trip through canonical JSON so fresh and replayed results compare equal."""
    return json.loads(canonical_json(value))


def request_digest(operation: str, request: Any) -> str:
    """SHA-256 of the canonical JSON form of an operation's input."""
    return hashlib.sha256(canonical_json({"op": operation, "req": request}).encode()).hexdigest()

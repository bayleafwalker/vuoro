"""One-use, body-bound edge proofs for MCP edge -> runtime shell calls.

The MCP edge (`vuoro-mcp-edge`) and the runtime shell run as two processes in
one pod.  The edge is the first verifier of every gateway assertion on the
MCP path and consumes its ``jti`` there; one MCP tool call can then make
several shell calls with that same assertion (``append_evidence`` resolves
the run, reads the chain tail and appends, and re-reads and re-appends on a
chain conflict).  The shell therefore cannot accept the forwarded assertion
"once", and it must not accept it as a bearer credential either.

Instead the edge attaches an edge proof to each shell call::

    X-Vuoro-Edge-Proof: v1.<nonce>.<iat_ms>.<exp_ms>.<mac>

``mac`` is HMAC-SHA256 under a per-pod key over the nonce, the times, the
HTTP method and path, SHA-256 of the forwarded assertion and SHA-256 of the
exact request body.  The shell verifies the assertion's gateway signature as
before (the key cannot mint an identity) and the proof, and accepts each
nonce once.  So a captured proof cannot be replayed, cannot carry a
different body, and cannot vouch for a different assertion.

The key is 32 random bytes in a file both containers can read, normally a
memory-backed ``emptyDir``: whichever process starts first creates it
atomically, the other reads it.  It never leaves the pod and a new pod has a
new key.
"""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
import binascii
from collections.abc import Callable
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import time

from vuoro_service.replay import ReplayCache


PROOF_HEADER = "x-vuoro-edge-proof"
PROOF_VERSION = "v1"
#: How long a minted proof is valid.  Shell calls are on localhost; the
#: proof is checked on arrival, not after the upstream timeout.
PROOF_TTL_MS = 10_000
#: Edge and shell share one node clock; this only absorbs rounding.
_PROOF_CLOCK_SKEW_MS = 1_000
KEY_BYTES = 32
_NONCE_BYTES = 16
_MAC_BYTES = 32
_DOMAIN = b"vuoro-edge-proof/v1"


class EdgeProofConfigurationError(ValueError):
    """The edge proof key cannot be loaded or created."""


class EdgeProofError(ValueError):
    """The edge proof is missing, malformed, expired or does not verify."""


class EdgeProofReplayed(EdgeProofError):
    """The edge proof's nonce was already accepted."""


def load_or_create_proof_key(path: Path) -> bytes:
    """Read the pod's edge proof key, creating it if no process has yet.

    Creation writes a private temporary file and hard-links it into place,
    so two processes starting together agree on one key: the loser's link
    fails with `FileExistsError` and it reads the winner's file.
    """

    if not path.is_absolute():
        raise EdgeProofConfigurationError("edge proof key path must be absolute")
    try:
        if not path.exists():
            temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(secrets.token_bytes(KEY_BYTES))
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        key = path.read_bytes()
    except OSError as error:
        raise EdgeProofConfigurationError("cannot load or create the edge proof key") from error
    if len(key) != KEY_BYTES:
        raise EdgeProofConfigurationError(
            f"edge proof key must be exactly {KEY_BYTES} bytes"
        )
    return key


def _b64(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str, length: int) -> bytes:
    try:
        data = urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, binascii.Error) as error:
        raise EdgeProofError("edge proof is malformed") from error
    # Only the canonical spelling: no alternative encodings of one nonce.
    if len(data) != length or _b64(data) != text:
        raise EdgeProofError("edge proof is malformed")
    return data


def _mac(
    key: bytes,
    *,
    nonce: str,
    iat_ms: int,
    exp_ms: int,
    method: str,
    path: str,
    assertion: str,
    body: bytes,
) -> bytes:
    message = b"\n".join(
        (
            _DOMAIN,
            nonce.encode("ascii"),
            str(iat_ms).encode("ascii"),
            str(exp_ms).encode("ascii"),
            method.upper().encode("ascii"),
            path.encode("utf-8"),
            hashlib.sha256(assertion.encode("utf-8")).hexdigest().encode("ascii"),
            hashlib.sha256(body).hexdigest().encode("ascii"),
        )
    )
    return hmac.new(key, message, hashlib.sha256).digest()


def mint_edge_proof(
    key: bytes,
    *,
    method: str,
    path: str,
    assertion: str,
    body: bytes,
    now: float | None = None,
) -> str:
    """A fresh proof for exactly this request."""

    iat_ms = int((time.time() if now is None else now) * 1000)
    exp_ms = iat_ms + PROOF_TTL_MS
    nonce = _b64(secrets.token_bytes(_NONCE_BYTES))
    mac = _mac(
        key,
        nonce=nonce,
        iat_ms=iat_ms,
        exp_ms=exp_ms,
        method=method,
        path=path,
        assertion=assertion,
        body=body,
    )
    return f"{PROOF_VERSION}.{nonce}.{iat_ms}.{exp_ms}.{_b64(mac)}"


def _millis(text: str) -> int:
    if not text.isascii() or not text.isdigit() or len(text) > 16 or text != str(int(text)):
        raise EdgeProofError("edge proof is malformed")
    return int(text)


class EdgeProofVerifier:
    """Verifies edge proofs and accepts each nonce once.

    `not_before` is this process's start time: a proof minted before it may
    have been accepted by a previous instance whose nonce cache is gone, so
    it is refused rather than guessed about.
    """

    def __init__(
        self,
        key: bytes,
        *,
        cache: ReplayCache | None = None,
        not_before: float | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(key) != KEY_BYTES:
            raise EdgeProofConfigurationError(
                f"edge proof key must be exactly {KEY_BYTES} bytes"
            )
        self._key = key
        self._cache = cache if cache is not None else ReplayCache(name="edge-proof")
        self._not_before_ms = None if not_before is None else int(not_before * 1000)
        self._clock = clock

    def verify(
        self, proof: str, *, method: str, path: str, assertion: str, body: bytes
    ) -> None:
        """Accept `proof` for this request, once.

        Raises `EdgeProofReplayed` for a nonce already accepted,
        `EdgeProofError` for anything else wrong, and
        `vuoro_service.replay.ReplayCacheFull` at capacity.
        """

        parts = proof.split(".")
        if len(parts) != 5 or parts[0] != PROOF_VERSION:
            raise EdgeProofError("edge proof is malformed")
        _version, nonce_text, iat_text, exp_text, mac_text = parts
        nonce = _unb64(nonce_text, _NONCE_BYTES)
        mac = _unb64(mac_text, _MAC_BYTES)
        iat_ms = _millis(iat_text)
        exp_ms = _millis(exp_text)
        expected = _mac(
            self._key,
            nonce=nonce_text,
            iat_ms=iat_ms,
            exp_ms=exp_ms,
            method=method,
            path=path,
            assertion=assertion,
            body=body,
        )
        if not hmac.compare_digest(mac, expected):
            raise EdgeProofError("edge proof does not verify for this request")
        now_ms = int(self._clock() * 1000)
        if not 0 < exp_ms - iat_ms <= PROOF_TTL_MS:
            raise EdgeProofError("edge proof lifetime is invalid")
        if iat_ms > now_ms + _PROOF_CLOCK_SKEW_MS:
            raise EdgeProofError("edge proof is not yet valid")
        if exp_ms <= now_ms:
            raise EdgeProofError("edge proof has expired")
        if self._not_before_ms is not None and iat_ms < self._not_before_ms:
            raise EdgeProofReplayed("edge proof predates this verifier's replay window")
        if not self._cache.consume(nonce, (exp_ms + _PROOF_CLOCK_SKEW_MS) / 1000):
            raise EdgeProofReplayed("edge proof was already used")


__all__ = [
    "KEY_BYTES",
    "PROOF_HEADER",
    "PROOF_TTL_MS",
    "EdgeProofConfigurationError",
    "EdgeProofError",
    "EdgeProofReplayed",
    "EdgeProofVerifier",
    "load_or_create_proof_key",
    "mint_edge_proof",
]

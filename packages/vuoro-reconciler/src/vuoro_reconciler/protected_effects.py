"""Immutable validated DATA, never an authenticated approval or execution grant.

An executor must independently obtain a fresh authenticated private broker read
each cycle. Neither construction of these types nor public/file bytes authorize
an effect. This module has no execution or transport dependencies.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, repr=False)
class AcceptedMetadata:
    acceptance_id: str
    preparation_id: str
    canonical_hash: str
    receipt_id: str
    accepted_at: str


@dataclass(frozen=True, slots=True, repr=False)
class AcceptedPageData:
    namespace_utf8: bytes
    items: tuple[AcceptedMetadata, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True, repr=False)
class EnvelopeData:
    kind: str
    document_digest: str
    document_utf8: bytes


@dataclass(frozen=True, slots=True, repr=False)
class AcceptanceData:
    acceptance_id: str
    preparation_id: str
    accepted_at: str
    signature_sha256: str
    challenge_sha256: str
    requester_utf8: bytes
    operator_utf8: bytes
    challenge_utf8: bytes


@dataclass(frozen=True, slots=True, repr=False)
class ReceiptData:
    receipt_id: str
    acceptance_id: str
    preparation_id: str
    canonical_hash: str
    signature_sha256: str
    challenge_sha256: str
    accepted_at: str
    receipt_utf8: bytes


@dataclass(frozen=True, slots=True, repr=False)
class ProtectedEffectData:
    namespace_utf8: bytes
    locator_utf8: bytes
    canonical_utf8: bytes
    canonical_hash: str
    legacy_content_digest: str
    repository: str
    source_run: str
    parameters_utf8: bytes
    envelopes: tuple[EnvelopeData, ...]
    run_document_utf8: bytes
    work_document_utf8: bytes
    release_document_utf8: bytes
    acceptance_revision: int
    acceptance: AcceptanceData
    receipt: ReceiptData


@dataclass(frozen=True, slots=True, repr=False)
class CurrentEffectBasisData:
    """Canonicalized unsigned native RESULT data; excludes wrappers and live evidence."""

    canonical_utf8: bytes
    intent_utf8: bytes
    run_utf8: bytes
    work_utf8: bytes
    release_utf8: bytes | None
    repo_id: str
    workspace_id: str
    status: str


@dataclass(frozen=True, slots=True, repr=False)
class CurrentnessResult:
    status: str
    reason: str

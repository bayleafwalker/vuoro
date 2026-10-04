"""Pure values/protocol for owner admission; these types confer no authority.

Only the shell's authenticated path may construct an authoritative context.
Structural validation is not proof of identity, ingress eligibility or database
write authority. Owners independently check those conditions at admission.
"""
from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
import re
from typing import Literal, Protocol

from .catalog import _NAME as _OPERATION_NAME

CATALOG_REQUIRED_PROFILE = "catalog-required/v2"
TRUSTED_REVIEW_INGRESS_PROFILE = "resource-review-ingress/v1"
ARGUMENT_BYTES_LIMIT = 65_536
_AUTHORITY = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:[.:][a-z][a-z0-9]*(?:-[a-z0-9]+)*)*$")
_REVISION = re.compile(r"^[0-9a-f]{64}$")
_SUBJECT = re.compile(r"^[A-Za-z0-9._-]+$")


def _text(value: str, label: str) -> None:
    if type(value) is not str:
        raise TypeError(f"{label} must be str")
    if not value or value != value.strip():
        raise ValueError(f"{label} must be nonempty text without outer whitespace")


def _bounded_envelope_text(value: str, label: str) -> None:
    # Match the frozen invocation framing, not domain-key semantics.
    if type(value) is not str:
        raise TypeError(f"{label} must be str")
    if not 1 <= len(value) <= 256:
        raise ValueError(f"{label} must be 1..256 characters")


def _frozen_texts(value: frozenset[str], label: str, *, authority: bool = False) -> None:
    if type(value) is not frozenset:
        raise TypeError(f"{label} must be frozenset")
    for item in value:
        _text(item, label)
        if item == "*" or (authority and not _AUTHORITY.fullmatch(item)):
            raise ValueError(f"{label} contains an invalid member")


@dataclass(frozen=True, slots=True)
class PrincipalIdentity:
    """Already verified issuer/subject/epoch data, never a mint or parser fallback."""
    issuer: str
    subject: str
    epoch: int

    def __post_init__(self) -> None:
        _text(self.issuer, "issuer")
        _text(self.subject, "subject")
        if not _SUBJECT.fullmatch(self.subject):
            raise ValueError("subject must be an opaque colon-free identifier")
        if type(self.epoch) is not int:
            raise TypeError("epoch must be an integer")
        if self.epoch < 0:
            raise ValueError("epoch must be nonnegative")

    @property
    def principal_id(self) -> str:
        return f"{self.issuer}:{self.subject}:{self.epoch}"


@dataclass(frozen=True, slots=True)
class AdmissionIdentity:
    principal: PrincipalIdentity
    environment: str
    authorities: frozenset[str]
    repository_ids: frozenset[str]

    def __post_init__(self) -> None:
        if type(self.principal) is not PrincipalIdentity:
            raise TypeError("principal must be PrincipalIdentity")
        _text(self.environment, "environment")
        _frozen_texts(self.authorities, "authorities", authority=True)
        _frozen_texts(self.repository_ids, "repository_ids")


@dataclass(frozen=True, slots=True)
class TrustedIngressProvenance:
    """Closed review provenance shape, not evidence that a transport is trusted.

    The shell supplies this only after its configured trusted ingress checks;
    the owner rechecks the exact policy and end principal. No wire coercion,
    service principal substitution or caller-provided reviewer is supported.
    """
    principal: PrincipalIdentity
    assertion_issuer: str
    assertion_audience: str
    profile: Literal["resource-review-ingress/v1"] = TRUSTED_REVIEW_INGRESS_PROFILE

    def __post_init__(self) -> None:
        if type(self.principal) is not PrincipalIdentity:
            raise TypeError("principal must be PrincipalIdentity")
        _text(self.assertion_issuer, "assertion_issuer")
        _text(self.assertion_audience, "assertion_audience")
        if type(self.profile) is not str or self.profile != TRUSTED_REVIEW_INGRESS_PROFILE:
            raise ValueError("unsupported trusted ingress profile")


@dataclass(frozen=True, slots=True)
class OwnerAdmissionContext:
    identity: AdmissionIdentity
    request_id: str
    repo_id: str
    catalog_revision: str
    idempotency_key: str
    trusted_ingress: TrustedIngressProvenance | None = None
    admission_profile: Literal["catalog-required/v2"] = CATALOG_REQUIRED_PROFILE

    def __post_init__(self) -> None:
        if type(self.identity) is not AdmissionIdentity:
            raise TypeError("identity must be AdmissionIdentity")
        _bounded_envelope_text(self.request_id, "request_id")
        _bounded_envelope_text(self.repo_id, "repo_id")
        _bounded_envelope_text(self.idempotency_key, "idempotency_key")
        if type(self.catalog_revision) is not str or not _REVISION.fullmatch(self.catalog_revision):
            raise ValueError("catalog_revision must be exact lowercase SHA256 hex")
        if type(self.admission_profile) is not str or self.admission_profile != CATALOG_REQUIRED_PROFILE:
            raise ValueError("unsupported admission profile")
        if self.trusted_ingress is not None:
            if type(self.trusted_ingress) is not TrustedIngressProvenance:
                raise TypeError("trusted_ingress must be TrustedIngressProvenance")
            if self.trusted_ingress.principal != self.identity.principal:
                raise ValueError("trusted ingress must name the same end principal")


@dataclass(frozen=True, slots=True)
class OwnerAdmissionRequest:
    operation: str
    argument_bytes: bytes
    context: OwnerAdmissionContext

    def __post_init__(self) -> None:
        if type(self.operation) is not str or not _OPERATION_NAME.fullmatch(self.operation):
            raise ValueError("operation must be an exact operation name")
        if type(self.argument_bytes) is not bytes:
            raise TypeError("argument_bytes must be immutable bytes")
        if not 0 < len(self.argument_bytes) <= ARGUMENT_BYTES_LIMIT:
            raise ValueError("argument_bytes must contain 1..65536 immutable bytes")
        if type(self.context) is not OwnerAdmissionContext:
            raise TypeError("context must be OwnerAdmissionContext")


@dataclass(frozen=True, slots=True)
class AdmissionResponse:
    """Exact public wire result; formatting/digestion remains owner responsibility."""
    http_status: int
    body_bytes: bytes

    def __post_init__(self) -> None:
        if type(self.http_status) is not int:
            raise TypeError("http_status must be an integer")
        if not 200 <= self.http_status <= 599:
            raise ValueError("http_status must be a final HTTP result")
        if type(self.body_bytes) is not bytes:
            raise TypeError("body_bytes must be immutable bytes")


class OwnerAdmission(Protocol):
    def admit(self, request: OwnerAdmissionRequest) -> AdmissionResponse | Awaitable[AdmissionResponse]: ...

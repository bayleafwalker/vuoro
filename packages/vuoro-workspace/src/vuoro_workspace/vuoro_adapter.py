"""Vuoro protocol-v1 adapter: serve the workspace store as ``knowledge.*`` operations.

Registration follows the shared function-registration protocol used by
Sprintctl (``register_work_catalog(registry, application)``): the composition
builds a :class:`KnowledgeApplication` from its runtime DSN and object store
and calls :func:`register_knowledge_catalog`. Vuoro service types are imported
lazily, so the store stays usable without the service installed.

Authority is two-layered (phase0 contract §3.2):

* each operation declares the coarse credential authority
  (``knowledge:read|contribute|review|admin``) that the service shell checks
  before dispatch (``authority-required``, HTTP 403);
* the store then intersects the credential's ``knowledge:*`` authorities with
  the principal's current membership role on every call.

Principal mapping (see :func:`principal_from_identity`): the store principal is
the identity's minted ``principal_id`` (never the display ``actor``). The
caller is treated as an agent, and so capped at contributor, when the identity
was minted under a delegated OAuth client grant (``client_id`` or
``grant_id`` present: a connected client acting for a user) or when the
credential carries the ``knowledge:agent`` marker authority. Membership rows
can additionally mark a principal as an agent; either marker caps it.

Artifact bytes travel as base64 in JSON. One upload call carries at most
:data:`MAX_UPLOAD_BYTES` (8 MiB) in Phase 1, so a served artifact version is
reserved with ``expected_size`` at most that cap. Larger artifacts wait for
presigned uploads. Downloads return base64 plus the recorded media type in
JSON only; this adapter never serves an HTTP body with an active content type.

``workspace export``/``import`` stay store-API/operator only in Phase 1: they
read and write server-side bundle directories, and serving them needs a
bundle-transfer design rather than a filesystem path argument.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any, NoReturn

from vuoro_adapter_kit import SCHEMA_FEATURES as _ADAPTER_SCHEMA_FEATURES
from vuoro_adapter_kit import object_schema

from . import errors as E
from .errors import WorkspaceError
from .ids import normalize
from .migrations import DEFAULT_SCHEMA, DOMAIN_API_VERSION, SchemaCompatibilityError, compatibility
from .objectstore import FsObjectStore, ObjectStore, S3ObjectStore
from .store import ADMIN, CONTRIBUTE, READ, REVIEW, ROLES, SCOPES, Principal, WorkspaceStore

DOMAIN_NAME = "knowledge"
#: Served API identity; equals the store's own ``domain_api_version``.
API_VERSION = DOMAIN_API_VERSION
SCHEMA_VERSION = "knowledge-schema/v1"
AGENT_MARKER_AUTHORITY = "knowledge:agent"
KNOWLEDGE_AUTHORITIES = frozenset({READ, CONTRIBUTE, REVIEW, ADMIN, AGENT_MARKER_AUTHORITY})
#: Phase 1 per-call upload cap (bytes, before base64).
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_BASE64 = 4 * ((MAX_UPLOAD_BYTES + 2) // 3)
SCHEMA_FEATURES = list(_ADAPTER_SCHEMA_FEATURES)

OBJECT_STORE_ENV = "VUORO_KNOWLEDGE_OBJECT_STORE"
S3_ENDPOINT_ENV = "VUORO_KNOWLEDGE_S3_ENDPOINT_URL"
S3_REGION_ENV = "VUORO_KNOWLEDGE_S3_REGION"

ConnectionFactory = Callable[[], Any]
OperationDefinitionFactory = Callable[..., Any]
RejectionFactory = Callable[..., BaseException]

# --------------------------------------------------------------- schemas

_ULID = {"type": "string", "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$"}
_NULLABLE_ULID = {"oneOf": [_ULID, {"type": "null"}]}
_TEXT = {"type": "string", "minLength": 1, "maxLength": 1000}
_NULLABLE_TEXT = {"type": ["string", "null"]}
_BODY = {"type": "string", "maxLength": 1_000_000}
_SHA256 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_TIMESTAMP = {"type": "string", "minLength": 1}
_PRINCIPAL = {"type": "string", "minLength": 1, "maxLength": 1000}
_VERSION_SELECTOR = {"oneOf": [{"const": "latest-approved"}, _ULID]}
_DECISION = {"enum": ["approve", "reject"]}
_SOURCES = {"type": "array", "maxItems": 100}
_RELATION = {
    "type": "object",
    "properties": {
        "kind": {"enum": ["derived_from", "supports", "supersedes", "cites"]},
        "target_kind": {"enum": ["revision", "artifact_version", "repository_document"]},
        "target_id": _ULID,
        "target_ref": {
            "type": "object",
            "properties": {
                "repository": {"type": "string", "minLength": 1},
                "commit": {"type": "string", "minLength": 1},
                "path": {"type": "string", "minLength": 1},
            },
            "required": ["repository", "commit", "path"],
            "additionalProperties": False,
        },
    },
    "required": ["kind", "target_kind"],
    "additionalProperties": False,
}
_RELATIONS = {"type": "array", "items": _RELATION, "maxItems": 100}
_WS = {"workspace_id": _ULID}
_PROCESSING = {"enum": ["reserved", "verifying", "ready", "rejected", "expired"]}
_REVIEW = {"enum": ["unreviewed", "approved", "rejected"]}
_EXTRACTION = {"enum": ["pending", "ready", "unsupported", "failed"]}
_REVISION_STATE = {"enum": ["draft", "proposed", "approved", "rejected", "withdrawn", "superseded"]}


def _obj(properties: Mapping[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return object_schema(properties, required=required)


def _result(properties: Mapping[str, Any], required: tuple[str, ...] | None = None) -> dict[str, Any]:
    return object_schema(properties, required=tuple(properties) if required is None else required)


_STORED_RELATION = {
    "type": "object",
    "properties": {
        "relation_id": _ULID,
        "kind": {"type": "string"},
        "target_kind": {"type": "string"},
        "target_id": _NULLABLE_ULID,
        "target_ref": {"type": ["object", "null"]},
    },
    "required": ["relation_id", "kind", "target_kind", "target_id", "target_ref"],
    "additionalProperties": False,
}
_FINALIZE_RESULT = {
    "artifact_id": _ULID,
    "version_id": _ULID,
    "processing_state": _PROCESSING,
    "review_state": _REVIEW,
    "extraction_state": _EXTRACTION,
    "sha256": _SHA256,
    "size": {"type": "integer", "minimum": 0},
    "media_type": {"type": "string"},
    "reject_reasons": {"type": "array", "items": {"type": "string"}},
}
_READ_RESULT = {
    "workspace_id": _ULID,
    "item_id": _ULID,
    "revision_id": _ULID,
    "parent_revision_id": _NULLABLE_ULID,
    "title": {"type": "string"},
    "body_md": {"type": "string"},
    "content_sha256": _SHA256,
    "author_id": {"type": "string"},
    "sources": {"type": "array"},
    "state": _REVISION_STATE,
    "publication_state": _REVISION_STATE,
    "reviewed_by": _NULLABLE_TEXT,
    "reviewed_at": {"type": ["string", "null"]},
    "created_at": _TIMESTAMP,
    "approved_revision_id": _NULLABLE_ULID,
    "sources_supplied": {"type": "boolean"},
    "relations": {"type": "array", "items": _STORED_RELATION},
    "watermark": {"type": "integer", "minimum": 0},
}
_DOWNLOAD_RESULT = {
    "artifact_id": _ULID,
    "version_id": _ULID,
    "name": {"type": "string"},
    "media_type": {"type": "string"},
    "size": {"type": "integer", "minimum": 0},
    "sha256": _SHA256,
    "review_state": _REVIEW,
    "processing_state": _PROCESSING,
    "extraction_state": _EXTRACTION,
    "content_base64": {"type": "string", "contentEncoding": "base64"},
}
_SEARCH_HIT = {
    "type": "object",
    "properties": {
        "kind": {"enum": ["item", "artifact"]},
        "id": _ULID,
        "version_id": _ULID,
        "title": {"type": "string"},
        "publication_state": {"type": "string"},
        "extraction_state": {"type": ["string", "null"]},
        "rank": {"type": "number"},
        "snippet": {"type": ["string", "null"]},
    },
    "required": ["kind", "id", "version_id", "title", "publication_state", "extraction_state", "rank", "snippet"],
    "additionalProperties": False,
}
_CHANGE = {
    "type": "object",
    "properties": {
        "seq": {"type": "integer", "minimum": 1},
        "kind": {"type": "string"},
        "entity_kind": {"type": "string"},
        "entity_id": {"type": "string"},
        "version_id": {"type": ["string", "null"]},
        "tombstone": {"type": "boolean"},
        "payload": {"type": "object"},
        "created_at": _TIMESTAMP,
    },
    "required": ["seq", "kind", "entity_kind", "entity_id", "version_id", "tombstone", "payload", "created_at"],
    "additionalProperties": False,
}


def _spec(
    name: str,
    *,
    authority: str,
    semantics: str,
    idempotency: str,
    input_schema: dict[str, Any],
    result_schema: dict[str, Any],
    handler: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "owning_domain": DOMAIN_NAME,
        "input_schema": input_schema,
        "result_schema": result_schema,
        "required_authority": authority,
        "execution_semantics": semantics,
        "idempotency": idempotency,
        "repo_scoped": False,
        "deprecation": {"deprecated": False, "replacement": None, "sunset_at": None},
        "required_client_schema_features": SCHEMA_FEATURES,
        "_handler_name": handler,
    }


_W, _R = "write", "read"
_OPERATIONS: tuple[dict[str, Any], ...] = (
    _spec(
        "knowledge.workspace.create-v1", authority=ADMIN, semantics=_W, idempotency="required",
        input_schema=_obj({"name": _TEXT}, ("name",)),
        result_schema=_result({"workspace_id": _ULID, "name": {"type": "string"},
                               "tenant_id": {"type": "string"}, "seq": {"type": "integer", "minimum": 1}}),
        handler="workspace_create",
    ),
    _spec(
        "knowledge.member.set-v1", authority=ADMIN, semantics=_W, idempotency="required",
        input_schema=_obj({**_WS, "principal_id": _PRINCIPAL,
                           "role": {"oneOf": [{"enum": list(ROLES)}, {"type": "null"}]},
                           "is_agent": {"type": "boolean"}},
                          ("workspace_id", "principal_id", "role")),
        result_schema=_result({"principal_id": {"type": "string"},
                               "role": {"oneOf": [{"enum": list(ROLES)}, {"type": "null"}]},
                               "is_agent": {"type": ["boolean", "null"]}}),
        handler="member_set",
    ),
    _spec(
        "knowledge.item.create-v1", authority=CONTRIBUTE, semantics=_W, idempotency="required",
        input_schema=_obj({**_WS, "title": _TEXT, "body_md": _BODY, "sources": _SOURCES,
                           "relations": _RELATIONS}, ("workspace_id", "title", "body_md")),
        result_schema=_result({"item_id": _ULID, "revision_id": _ULID, "state": {"const": "draft"}}),
        handler="item_create",
    ),
    _spec(
        "knowledge.revision.draft-v1", authority=CONTRIBUTE, semantics=_W, idempotency="required",
        input_schema=_obj({**_WS, "item_id": _ULID, "title": _TEXT, "body_md": _BODY,
                           "parent_revision_id": _NULLABLE_ULID, "sources": _SOURCES, "relations": _RELATIONS},
                          ("workspace_id", "item_id", "title", "body_md")),
        result_schema=_result({"item_id": _ULID, "revision_id": _ULID, "parent_revision_id": _NULLABLE_ULID,
                               "state": {"const": "draft"}}),
        handler="revision_draft",
    ),
    _spec(
        "knowledge.proposal.submit-v1", authority=CONTRIBUTE, semantics=_W, idempotency="required",
        input_schema=_obj({**_WS, "revision_id": _ULID, "expected_base_revision_id": _NULLABLE_ULID,
                           "rationale": {"type": "string", "maxLength": 10000}, "evidence": _SOURCES},
                          ("workspace_id", "revision_id", "expected_base_revision_id")),
        result_schema=_result({"proposal_id": _ULID, "item_id": _ULID, "revision_id": _ULID,
                               "state": {"const": "open"}}),
        handler="proposal_submit",
    ),
    _spec(
        "knowledge.proposal.review-v1", authority=REVIEW, semantics=_W, idempotency="required",
        input_schema=_obj({**_WS, "proposal_id": _ULID, "decision": _DECISION, "note": _NULLABLE_TEXT},
                          ("workspace_id", "proposal_id", "decision")),
        result_schema=_result({"proposal_id": _ULID, "state": {"enum": ["approved", "rejected"]},
                               "item_id": _ULID, "revision_id": _ULID, "approved_revision_id": _ULID,
                               "superseded_revision_id": _NULLABLE_ULID},
                              ("proposal_id", "state", "item_id")),
        handler="proposal_review",
    ),
    _spec(
        "knowledge.proposal.withdraw-v1", authority=CONTRIBUTE, semantics=_W, idempotency="required",
        input_schema=_obj({**_WS, "proposal_id": _ULID}, ("workspace_id", "proposal_id")),
        result_schema=_result({"proposal_id": _ULID, "state": {"const": "withdrawn"}, "revision_id": _ULID}),
        handler="proposal_withdraw",
    ),
    _spec(
        "knowledge.artifact.begin-v1", authority=CONTRIBUTE, semantics=_W, idempotency="required",
        input_schema=_obj({**_WS, "expected_sha256": _SHA256,
                           "expected_size": {"type": "integer", "minimum": 0, "maximum": MAX_UPLOAD_BYTES},
                           "media_type": {"type": "string", "minLength": 3, "maxLength": 255},
                           "name": _TEXT, "artifact_id": _ULID, "relations": _RELATIONS},
                          ("workspace_id", "expected_sha256", "expected_size", "media_type")),
        result_schema=_result({"artifact_id": _ULID, "version_id": _ULID, "processing_state": {"const": "reserved"},
                               "expires_at": _TIMESTAMP}),
        handler="artifact_begin",
    ),
    # A convergent put of a reserved version's bytes: retrying is safe without
    # a key (the reservation fixes the expected digest, and finalize verifies
    # it), and the store keeps no ledger for the transfer step.
    _spec(
        "knowledge.artifact.upload-v1", authority=CONTRIBUTE, semantics=_W, idempotency="not-allowed",
        input_schema=_obj({**_WS, "version_id": _ULID,
                           "content_base64": {"type": "string", "maxLength": MAX_UPLOAD_BASE64,
                                              "contentEncoding": "base64"}},
                          ("workspace_id", "version_id", "content_base64")),
        result_schema=_result({"version_id": _ULID, "size": {"type": "integer", "minimum": 0},
                               "sha256": _SHA256}),
        handler="artifact_upload",
    ),
    _spec(
        "knowledge.artifact.finalize-v1", authority=CONTRIBUTE, semantics=_W, idempotency="required",
        input_schema=_obj({**_WS, "version_id": _ULID}, ("workspace_id", "version_id")),
        result_schema=_result(_FINALIZE_RESULT),
        handler="artifact_finalize",
    ),
    _spec(
        "knowledge.artifact.review-v1", authority=REVIEW, semantics=_W, idempotency="required",
        input_schema=_obj({**_WS, "version_id": _ULID, "decision": _DECISION, "note": _NULLABLE_TEXT},
                          ("workspace_id", "version_id", "decision")),
        result_schema=_result({"artifact_id": _ULID, "version_id": _ULID,
                               "review_state": {"enum": ["approved", "rejected"]}}),
        handler="artifact_review",
    ),
    _spec(
        "knowledge.item.read-v1", authority=READ, semantics=_R, idempotency="not-allowed",
        input_schema=_obj({**_WS, "item_id": _ULID, "version": _VERSION_SELECTOR}, ("workspace_id", "item_id")),
        result_schema=_result(_READ_RESULT),
        handler="item_read",
    ),
    _spec(
        "knowledge.artifact.download-v1", authority=READ, semantics=_R, idempotency="not-allowed",
        input_schema=_obj({**_WS, "artifact_id": _ULID, "version": _VERSION_SELECTOR},
                          ("workspace_id", "artifact_id")),
        result_schema=_result(_DOWNLOAD_RESULT),
        handler="artifact_download",
    ),
    _spec(
        "knowledge.workspace.search-v1", authority=READ, semantics=_R, idempotency="not-allowed",
        input_schema=_obj({**_WS, "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                           "scope": {"enum": list(SCOPES)},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                           "offset": {"type": "integer", "minimum": 0, "maximum": 10000}},
                          ("workspace_id", "query")),
        result_schema=_result({"workspace_id": _ULID, "scope": {"enum": list(SCOPES)},
                               "results": {"type": "array", "items": _SEARCH_HIT, "maxItems": 100},
                               "total": {"type": "integer", "minimum": 0},
                               "index_watermark": {"type": "integer", "minimum": 0},
                               "index_complete": {"type": "boolean"}}),
        handler="workspace_search",
    ),
    _spec(
        "knowledge.workspace.changes-v1", authority=READ, semantics=_R, idempotency="not-allowed",
        input_schema=_obj({**_WS, "cursor": {"type": ["string", "null"], "maxLength": 512},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 1000}},
                          ("workspace_id",)),
        result_schema=_result({"workspace_id": _ULID, "changes": {"type": "array", "items": _CHANGE},
                               "cursor": {"type": "string"}, "has_more": {"type": "boolean"},
                               "watermark": {"type": "integer", "minimum": 0}}),
        handler="workspace_changes",
    ),
    _spec(
        "knowledge.item.delete-v1", authority=ADMIN, semantics=_W, idempotency="required",
        input_schema=_obj({**_WS, "item_id": _ULID}, ("workspace_id", "item_id")),
        result_schema=_result({"kind": {"const": "item"}, "id": _ULID, "deleted_at": _TIMESTAMP}),
        handler="item_delete",
    ),
    _spec(
        "knowledge.artifact.delete-v1", authority=ADMIN, semantics=_W, idempotency="required",
        input_schema=_obj({**_WS, "artifact_id": _ULID}, ("workspace_id", "artifact_id")),
        result_schema=_result({"kind": {"const": "artifact"}, "id": _ULID, "deleted_at": _TIMESTAMP}),
        handler="artifact_delete",
    ),
    # Convergent maintenance: expiring stale reservations and the two-pass
    # orphan sweep are safe to re-run, and the store keeps no ledger for it.
    _spec(
        "knowledge.workspace.reconcile-v1", authority=ADMIN, semantics=_W, idempotency="not-allowed",
        input_schema=_obj({**_WS, "grace_seconds": {"type": "integer", "minimum": 0, "maximum": 30 * 86400},
                           "delete_orphans": {"type": "boolean"}}, ("workspace_id",)),
        result_schema=_result({"workspace_id": _ULID, "expired_versions": {"type": "array", "items": _ULID},
                               "missing_objects": {"type": "array", "items": {"type": "object"}},
                               "orphans": {"type": "array", "items": {"type": "object"}}}),
        handler="workspace_reconcile",
    ),
)


def catalog_operation_specs() -> tuple[dict[str, Any], ...]:
    """Catalog data (with private ``_handler_name``) without importing Vuoro."""
    return tuple(deepcopy(spec) for spec in _OPERATIONS)


# --------------------------------------------------------- identity mapping


def principal_from_identity(identity: Any) -> Principal:
    """Map a Vuoro invocation identity to a store principal.

    * ``principal_id`` is the identity's minted, stable principal; an identity
      without one cannot hold workspace membership and is refused.
    * Only ``knowledge:*`` authorities are passed on.
    * Agent rule: an identity minted under a delegated OAuth client grant
      (``client_id`` or ``grant_id`` set) is a connected client acting for a
      user, and a credential carrying ``knowledge:agent`` is an autonomous
      runtime; both are agents, capped at contributor by the store.
    """
    principal_id = getattr(identity, "principal_id", None)
    if not isinstance(principal_id, str) or not principal_id:
        raise WorkspaceError(E.FORBIDDEN, "knowledge operations require a minted principal_id")
    authorities = frozenset(a for a in getattr(identity, "authorities", ()) if a in KNOWLEDGE_AUTHORITIES)
    is_agent = (
        getattr(identity, "client_id", None) is not None
        or getattr(identity, "grant_id", None) is not None
        or AGENT_MARKER_AUTHORITY in authorities
    )
    return Principal(principal_id, authorities - {AGENT_MARKER_AUTHORITY}, is_agent=is_agent)


# -------------------------------------------------------------- application

_HTTP_STATUS = {
    E.FORBIDDEN: 403,
    E.NOT_FOUND: 404,
    E.INVALID: 422,
    E.STALE_BASE: 409,
    E.IDEMPOTENCY_CONFLICT: 409,
    E.STATE_CONFLICT: 409,
    E.OBJECT_MISSING: 409,
    E.RESERVATION_EXPIRED: 410,
    E.INVALID_CURSOR: 400,
    E.IMPORT_INVALID: 422,
    "payload-too-large": 413,
}


def _load_operation_definition(**values: Any) -> Any:
    from vuoro_service.contracts import OperationDefinition

    return OperationDefinition(**values)


def _load_rejection(code: str, message: str, http_status: int, details: Mapping[str, Any] | None = None) -> BaseException:
    from vuoro_service.catalog import OperationRejectedError

    return OperationRejectedError(code, message, http_status=http_status, details=details)


def object_store_from_uri(uri: str, environ: Mapping[str, str] | None = None) -> ObjectStore:
    """``fs:/absolute/path`` or ``s3://bucket[/prefix]`` (boto3 default credential chain).

    For S3-compatible endpoints set ``VUORO_KNOWLEDGE_S3_ENDPOINT_URL`` and
    optionally ``VUORO_KNOWLEDGE_S3_REGION``.
    """
    environ = os.environ if environ is None else environ
    if not isinstance(uri, str) or not uri or uri != uri.strip():
        raise ValueError(f"{OBJECT_STORE_ENV} must be fs:/absolute/path or s3://bucket[/prefix]")
    if uri.startswith("fs:"):
        root = Path(uri[3:])
        if not root.is_absolute():
            raise ValueError(f"{OBJECT_STORE_ENV} fs root must be absolute")
        return FsObjectStore(root)
    if uri.startswith("s3://"):
        bucket, _, prefix = uri[5:].partition("/")
        if not bucket:
            raise ValueError(f"{OBJECT_STORE_ENV} s3 URI needs a bucket")
        kwargs: dict[str, Any] = {}
        if environ.get(S3_ENDPOINT_ENV):
            kwargs["endpoint_url"] = environ[S3_ENDPOINT_ENV]
        if environ.get(S3_REGION_ENV):
            kwargs["region_name"] = environ[S3_REGION_ENV]
        return S3ObjectStore(bucket, prefix, **kwargs)
    raise ValueError(f"{OBJECT_STORE_ENV} must be fs:/absolute/path or s3://bucket[/prefix]")


class KnowledgeApplication:
    """Serve store calls; one autocommit connection per invocation.

    Construction opens nothing. :meth:`compatibility` is the read-only startup
    gate; it never migrates (``VUORO_WORKSPACE_SCHEMA_MODE`` is deliberately
    not consulted here — only ``vuoro-workspace migrate`` runs DDL).
    """

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        object_store: ObjectStore,
        *,
        schema: str = DEFAULT_SCHEMA,
        runtime_role: str | None = None,
        rejection_factory: RejectionFactory = _load_rejection,
        store_kwargs: Mapping[str, Any] | None = None,
    ) -> None:
        self._connect = connection_factory
        self.objects = object_store
        self.schema = schema
        self.runtime_role = runtime_role
        self._reject_factory = rejection_factory
        self._store_kwargs = dict(store_kwargs or {})

    @classmethod
    def from_dsn(cls, dsn: str, object_store: ObjectStore, **kwargs: Any) -> "KnowledgeApplication":
        def connect() -> Any:
            import psycopg

            return psycopg.connect(dsn, autocommit=True)

        return cls(connect, object_store, **kwargs)

    # -- gate

    def compatibility(self) -> dict[str, Any]:
        """Read-only schema/role check for the service handshake."""
        conn = self._connect()
        try:
            report = compatibility(conn, self.schema, self.runtime_role).to_dict()
        finally:
            conn.close()
        report["schema_version"] = (
            f"knowledge-schema/v{report['installed_schema_version']}"
            if report.get("installed_schema_version") is not None else "uninstalled"
        )
        if not report["compatible"]:
            report["reason"] = "; ".join(report.get("reasons") or ()) or "knowledge schema is incompatible"
        return report

    # -- plumbing

    def _reject(self, code: str, message: str, status: int, details: Mapping[str, Any] | None = None) -> NoReturn:
        raise self._reject_factory(code, message, status, details)

    @contextmanager
    def _store(self) -> Iterator[WorkspaceStore]:
        conn = self._connect()
        try:
            store = WorkspaceStore(conn, self.objects, schema=self.schema, runtime_role=self.runtime_role,
                                   **self._store_kwargs)
            yield store
        finally:
            conn.close()

    def _call(self, context: Any, fn: Callable[[WorkspaceStore, Principal], dict[str, Any]]) -> dict[str, Any]:
        try:
            principal = principal_from_identity(context.identity)
            with self._store() as store:
                return normalize(fn(store, principal))
        except WorkspaceError as error:
            details = normalize(error.details) if error.details else None
            self._reject(f"knowledge-{error.code}", error.message, _HTTP_STATUS.get(error.code, 409), details)
        except SchemaCompatibilityError as error:
            self._reject("knowledge-schema-incompatible", str(error), 503)

    # -- handlers (arguments are already schema-validated by the registry)

    def workspace_create(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return self._call(ctx, lambda s, p: s.workspace_create(p, a["name"], idempotency_key=ctx.idempotency_key))

    def member_set(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        extra = {"is_agent": a["is_agent"]} if "is_agent" in a else {}
        return self._call(ctx, lambda s, p: s.member_set(p, a["workspace_id"], a["principal_id"], a["role"],
                                                         idempotency_key=ctx.idempotency_key, **extra))

    def item_create(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return self._call(ctx, lambda s, p: s.item_create(
            p, a["workspace_id"], a["title"], a["body_md"], sources=a.get("sources", []),
            relations=a.get("relations", []), idempotency_key=ctx.idempotency_key))

    def revision_draft(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return self._call(ctx, lambda s, p: s.revision_draft(
            p, a["workspace_id"], a["item_id"], a["title"], a["body_md"],
            parent_revision_id=a.get("parent_revision_id"), sources=a.get("sources", []),
            relations=a.get("relations", []), idempotency_key=ctx.idempotency_key))

    def proposal_submit(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return self._call(ctx, lambda s, p: s.proposal_submit(
            p, a["workspace_id"], a["revision_id"], a["expected_base_revision_id"],
            rationale=a.get("rationale", ""), evidence=a.get("evidence", []), idempotency_key=ctx.idempotency_key))

    def proposal_review(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return self._call(ctx, lambda s, p: s.proposal_review(
            p, a["workspace_id"], a["proposal_id"], a["decision"], note=a.get("note"),
            idempotency_key=ctx.idempotency_key))

    def proposal_withdraw(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return self._call(ctx, lambda s, p: s.proposal_withdraw(
            p, a["workspace_id"], a["proposal_id"], idempotency_key=ctx.idempotency_key))

    def artifact_begin(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        def run(s: WorkspaceStore, p: Principal) -> dict[str, Any]:
            result = s.artifact_begin(
                p, a["workspace_id"], expected_sha256=a["expected_sha256"], expected_size=a["expected_size"],
                media_type=a["media_type"], name=a.get("name"), artifact_id=a.get("artifact_id"),
                relations=a.get("relations", []), idempotency_key=ctx.idempotency_key)
            return {k: result[k] for k in ("artifact_id", "version_id", "processing_state", "expires_at")}

        return self._call(ctx, run)

    def artifact_upload(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        try:
            data = base64.b64decode(a["content_base64"], validate=True)
        except (binascii.Error, ValueError):
            self._reject("knowledge-invalid-argument", "content_base64 is not valid base64", 422)
        if len(data) > MAX_UPLOAD_BYTES:
            self._reject("knowledge-payload-too-large",
                         f"one upload call carries at most {MAX_UPLOAD_BYTES} bytes in Phase 1", 413)

        def run(s: WorkspaceStore, p: Principal) -> dict[str, Any]:
            result = s.artifact_upload(p, a["workspace_id"], a["version_id"], data)
            return {"version_id": result["version_id"], "size": result["size"],
                    "sha256": hashlib.sha256(data).hexdigest()}

        return self._call(ctx, run)

    def artifact_finalize(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        def run(s: WorkspaceStore, p: Principal) -> dict[str, Any]:
            result = s.artifact_finalize(p, a["workspace_id"], a["version_id"], idempotency_key=ctx.idempotency_key)
            return {k: result[k] for k in _FINALIZE_RESULT}

        return self._call(ctx, run)

    def artifact_review(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return self._call(ctx, lambda s, p: s.artifact_review(
            p, a["workspace_id"], a["version_id"], a["decision"], note=a.get("note"),
            idempotency_key=ctx.idempotency_key))

    def item_read(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        def run(s: WorkspaceStore, p: Principal) -> dict[str, Any]:
            result = s.read(p, a["workspace_id"], a["item_id"], a.get("version", "latest-approved"))
            return {k: result[k] for k in _READ_RESULT}

        return self._call(ctx, run)

    def artifact_download(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        def run(s: WorkspaceStore, p: Principal) -> dict[str, Any]:
            result = s.download(p, a["workspace_id"], a["artifact_id"], a.get("version", "latest-approved"))
            data = result.pop("data")
            if len(data) > MAX_UPLOAD_BYTES:
                raise WorkspaceError("payload-too-large", "artifact exceeds the Phase 1 served download cap")
            # Bytes travel only as base64 inside the JSON envelope with the
            # recorded media type; nothing is ever served as an HTTP body.
            served = {k: result[k] for k in _DOWNLOAD_RESULT if k != "content_base64"}
            served["content_base64"] = base64.b64encode(data).decode("ascii")
            return served

        return self._call(ctx, run)

    def workspace_search(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return self._call(ctx, lambda s, p: s.search(
            p, a["workspace_id"], a["query"], scope=a.get("scope", "approved"), limit=a.get("limit", 20),
            offset=a.get("offset", 0)))

    def workspace_changes(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return self._call(ctx, lambda s, p: s.changes_since(
            p, a["workspace_id"], a.get("cursor"), limit=a.get("limit", 100)))

    def item_delete(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return self._call(ctx, lambda s, p: s.delete(p, a["workspace_id"], "item", a["item_id"],
                                                     idempotency_key=ctx.idempotency_key))

    def artifact_delete(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return self._call(ctx, lambda s, p: s.delete(p, a["workspace_id"], "artifact", a["artifact_id"],
                                                     idempotency_key=ctx.idempotency_key))

    def workspace_reconcile(self, a: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return self._call(ctx, lambda s, p: s.reconcile(
            p, a["workspace_id"], grace=timedelta(seconds=a.get("grace_seconds", 3600)),
            delete_orphans=a.get("delete_orphans", False)))


def register_knowledge_catalog(
    registry: Any,
    application: KnowledgeApplication,
    *,
    operation_definition_factory: OperationDefinitionFactory = _load_operation_definition,
) -> None:
    """Register every ``knowledge.*`` operation in a protocol-v1 catalog registry."""
    for raw_spec in catalog_operation_specs():
        handler_name = raw_spec.pop("_handler_name")
        registry.register(operation_definition_factory(**raw_spec), getattr(application, handler_name))


__all__ = [
    "AGENT_MARKER_AUTHORITY",
    "API_VERSION",
    "DOMAIN_NAME",
    "KnowledgeApplication",
    "MAX_UPLOAD_BYTES",
    "SCHEMA_VERSION",
    "catalog_operation_specs",
    "object_store_from_uri",
    "principal_from_identity",
    "register_knowledge_catalog",
]

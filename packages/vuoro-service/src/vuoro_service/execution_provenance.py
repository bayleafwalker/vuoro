"""Narrow, fail-closed provenance contract for dispatch enqueue composition.

This module deliberately does not import ActionQ.  The released ActionQ
0.1.6 wheel has no ``execution.dispatch.enqueue`` v2 API and therefore cannot
consume this contract.  Once a pinned ActionQ release exposes the matching
``InvocationProvenance`` interface, composition may adapt this value at that
single boundary; until then it is only a tested contract fixture.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from vuoro_service.identity import Identity, InvocationContext


DISPATCH_ENQUEUE_OPERATION = "execution.dispatch.enqueue"
DISPATCH_ENQUEUE_AUTHORITY = "execution.enqueue"


class DispatchEnqueueProvenanceError(ValueError):
    """The authenticated Protocol v1 invocation cannot enqueue a dispatch."""


@dataclass(frozen=True)
class DispatchEnqueueProvenance:
    """The only identity-derived fields a future ActionQ adapter may receive.

    ``authorized_repositories`` is the identity's declared scope, not a
    caller-provided actor or authority.  The target repository is checked
    before this value is constructed and remains in the owner-domain request.
    """

    actor: str
    environment: str
    request_id: str
    catalog_revision: str
    idempotency_key: str
    basis_revision: str | None
    authorized_repositories: tuple[str, ...]


def _has_raw_actor_header(headers: Mapping[str, str]) -> bool:
    """Reject any caller header whose hyphen-delimited name names an actor."""

    for name in headers:
        parts = name.casefold().split("-")
        if "actor" in parts:
            return True
    return False


def resolve_dispatch_enqueue_provenance(
    context: InvocationContext | object,
    *,
    expected_environment: str,
    headers: Mapping[str, str] | None = None,
) -> DispatchEnqueueProvenance:
    """Resolve one authenticated identity into ActionQ-shaped narrow provenance.

    This is intentionally stricter than generic catalog invocation: a v2
    dispatch enqueue needs the named ``execution.enqueue`` authority, a single
    concrete repository target authorized by the resolved identity, and an
    idempotency key.  It accepts no caller actor header or fallback identity.
    """

    if headers is not None and _has_raw_actor_header(headers):
        raise DispatchEnqueueProvenanceError("caller actor headers are forbidden")
    if not isinstance(context, InvocationContext) or not isinstance(context.identity, Identity):
        raise DispatchEnqueueProvenanceError("one authenticated Protocol v1 identity is required")

    identity = context.identity
    if not identity.actor:
        raise DispatchEnqueueProvenanceError("authenticated identity actor is required")
    if identity.environment != expected_environment:
        raise DispatchEnqueueProvenanceError(
            "authenticated identity is bound to a different environment"
        )
    if DISPATCH_ENQUEUE_AUTHORITY not in identity.authorities:
        raise DispatchEnqueueProvenanceError(
            f"authenticated identity lacks {DISPATCH_ENQUEUE_AUTHORITY} authority"
        )
    if not context.repo_id:
        raise DispatchEnqueueProvenanceError("dispatch enqueue requires one repository scope")
    if not identity.authorizes_repo(context.repo_id):
        raise DispatchEnqueueProvenanceError(
            "authenticated identity is not authorized for the repository scope"
        )
    if context.idempotency_requirement != "required" or not context.idempotency_key:
        raise DispatchEnqueueProvenanceError("dispatch enqueue requires an idempotency key")

    return DispatchEnqueueProvenance(
        actor=identity.actor,
        environment=identity.environment,
        request_id=context.request_id,
        catalog_revision=context.catalog_revision,
        idempotency_key=context.idempotency_key,
        basis_revision=context.basis_revision,
        authorized_repositories=tuple(sorted(identity.repo_ids)),
    )

"""Identity resolution interfaces for the reusable service shell."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import re
from typing import Literal

from fastapi import Request


#: A minted principal identifier: ``<issuer>:<subject>:<epoch>``. The epoch is a
#: per-subject issuance counter that increments on any reissue -- a rename, a
#: credential re-mint, a decommission-and-reuse -- so a reissued actor is a
#: *different* principal by construction and inherits nothing. Ownership in the
#: federation authority turns on this string forever and there is no ownership
#: transfer operation, which is why it cannot be the display actor: an actor is
#: chosen for humans and is expected to change.
#:
#: The issuer may itself contain colons (it is often a URL), so the subject and
#: epoch are read from the right.
PRINCIPAL_ID = re.compile(r"^.+:[^:\s]+:(?:0|[1-9][0-9]*)$")

#: A reserved system principal, e.g. ActionQ's pinned ``federation-backfill/v1``.
#: Exempt from the minting rule by name: nothing issues it, and its whole
#: purpose is to be a fixed literal that makes machine-written changes
#: distinguishable from a person's.
SYSTEM_PRINCIPAL_ID = re.compile(r"^[a-z][a-z0-9-]*/v[1-9][0-9]*$")

#: An identity's ``repo_ids`` containing this sentinel is authorized for
#: every repository, not an enumerated set. Matches how the two production
#: identities today are bound to a host, not a single repository.
ALL_REPOS = "*"


class IdentityResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class Identity:
    actor: str
    environment: str
    authorities: frozenset[str] = frozenset()
    repo_ids: frozenset[str] = frozenset()
    workspace_id: str | None = None
    #: ``None`` means no stable principal was asserted, and it is not a default
    #: to fall back through: an identity without one cannot be bound to
    #: federation ownership at all. Both production resolvers require it; the
    #: field is optional here so that a caller with no federation involvement
    #: does not have to invent one.
    principal_id: str | None = None

    def __post_init__(self) -> None:
        if self.principal_id is not None and not (
            PRINCIPAL_ID.fullmatch(self.principal_id)
            or SYSTEM_PRINCIPAL_ID.fullmatch(self.principal_id)
        ):
            raise IdentityResolutionError(
                "principal_id must be <issuer>:<subject>:<epoch> or a reserved system principal"
            )

    @property
    def authorized_repositories(self) -> tuple[str, ...]:
        """Expose the stable owner-adapter provenance spelling for repo scope."""

        return tuple(sorted(self.repo_ids))

    def authorizes_repo(self, repo_id: str) -> bool:
        return ALL_REPOS in self.repo_ids or repo_id in self.repo_ids


@dataclass(frozen=True)
class InvocationContext:
    identity: Identity
    request_id: str
    basis_revision: str | None
    catalog_revision: str
    idempotency_requirement: Literal["not-allowed", "optional", "required"]
    idempotency_key: str | None
    # Client-supplied, not identity-bound: the repository this invocation
    # targets. The identity only authorizes it (Identity.authorizes_repo);
    # it does not dictate it. None for operations outside the work domain.
    repo_id: str | None = None


IdentityResolver = Callable[[Request], Identity | Awaitable[Identity]]


class StaticBearerIdentityResolver:
    """Test/evaluation resolver that maps opaque bearer tokens to identities."""

    def __init__(self, identities: Mapping[str, Identity]) -> None:
        self._identities = dict(identities)

    def __call__(self, request: Request) -> Identity:
        authorization = request.headers.get("authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or token not in self._identities:
            raise IdentityResolutionError("a valid bearer identity is required")
        return self._identities[token]


def deny_all_identities(_request: Request) -> Identity:
    raise IdentityResolutionError("no identity resolver is configured")

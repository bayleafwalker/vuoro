"""Identity resolution interfaces for the reusable service shell."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from fastapi import Request


@dataclass(frozen=True)
class Identity:
    actor: str
    environment: str
    authorities: frozenset[str] = frozenset()


@dataclass(frozen=True)
class InvocationContext:
    identity: Identity
    request_id: str
    basis_revision: str | None
    catalog_revision: str
    idempotency_requirement: Literal["not-allowed", "optional", "required"]
    idempotency_key: str | None


class IdentityResolutionError(ValueError):
    pass


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

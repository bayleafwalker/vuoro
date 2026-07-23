"""Identity resolution interfaces for the reusable service shell."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, KeysView, Mapping
from dataclasses import dataclass, field
from typing import Literal

from fastapi import Request


@dataclass(frozen=True)
class Identity:
    actor: str
    environment: str
    authorities: frozenset[str] = frozenset()


class TransientCredentials:
    """Redacted, non-serializable carrier for out-of-band invocation proofs.

    Bindings are keyed by a non-secret ``sha256:<64-lowercase-hex>`` reference
    and are only ever readable through :meth:`reveal`. Instances exist only on
    the per-request :class:`InvocationContext` — they must never be cached,
    logged, or persisted.
    """

    __slots__ = ("_bindings",)

    def __init__(self, bindings: Mapping[str, str] | None = None) -> None:
        self._bindings: dict[str, str] = dict(bindings) if bindings else {}

    @classmethod
    def empty(cls) -> "TransientCredentials":
        return cls()

    def __bool__(self) -> bool:
        return bool(self._bindings)

    def __len__(self) -> int:
        return len(self._bindings)

    def keys(self) -> KeysView[str]:
        return self._bindings.keys()

    def reveal(self, key: str) -> str | None:
        return self._bindings.get(key)

    def __repr__(self) -> str:
        return f"<TransientCredentials {len(self._bindings)} binding(s) redacted>"

    def __str__(self) -> str:
        return self.__repr__()

    def __reduce__(self) -> tuple:
        raise TypeError("TransientCredentials is not serializable")


@dataclass(frozen=True)
class InvocationContext:
    identity: Identity
    request_id: str
    basis_revision: str | None
    catalog_revision: str
    idempotency_requirement: Literal["not-allowed", "optional", "required"]
    idempotency_key: str | None
    transient_credentials: TransientCredentials = field(
        default_factory=TransientCredentials.empty
    )


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

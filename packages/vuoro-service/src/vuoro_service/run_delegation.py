"""Compose the work owner's run credential lookup with the primary resolver.

The service owns no token store, mint, revocation, migration or acceptance state.
The released work owner must revalidate its typed context within every action.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import inspect
import re
from typing import Any, Callable

from fastapi import Request

from .identity import Identity, IdentityResolutionError, IdentityResolver

_ALLOWED = frozenset({"work.lease.currentness-v1", "work.run.resolve-v1", "work.run.predecessor-context-v1",
                      "work.session-note.write-v1", "work.evidence.append-v1"})
_AUTHORITIES = frozenset({"work:read", "work:evidence"})
_TOKEN = re.compile(r"vrd_[A-Z2-7]{52}")


class DelegatedCredentialInvalid(IdentityResolutionError):
    code = "credential-invalid"


@dataclass(frozen=True)
class DelegatedIdentity(Identity):
    # This is the actual immutable owner context, not a reconstructed mapping.
    delegation: Any = None

    def permits(self, operation: str, arguments: object) -> bool:
        return (isinstance(arguments, Mapping) and self.delegation is not None and operation in _ALLOWED
                and operation in self.delegation.operations
                and arguments.get("run_id") == self.delegation.run_id
                and self.expires_at is not None
                and self.expires_at > datetime.now(timezone.utc))


class DelegatedBearerIdentityResolver:
    """Handle only reserved vrd_ credentials; every other mode stays primary.

    lookup accepts a SHA256 hex digest only and returns the released owner's
    ResolvedRunDelegation. It must resolve within the configured owner/database
    environment independently of the request's caller-supplied repo_id. No
    lookup means unsupported credentials refuse, never fall back to static.
    """

    def __init__(self, primary: IdentityResolver, *, environment: str,
                 lookup: Callable[[str], Any] | None,
                 expected_workspace_id: str | None = None,
                 allowed_repo_ids: frozenset[str] | None = None) -> None:
        self.primary = primary
        self.environment = environment
        self.lookup = lookup
        self.expected_workspace_id = expected_workspace_id
        self.allowed_repo_ids = allowed_repo_ids

    async def __call__(self, request: Request) -> Identity:
        scheme, separator, token = request.headers.get("authorization", "").partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.startswith("vrd_"):
            identity = self.primary(request)
            return await identity if inspect.isawaitable(identity) else identity
        if not _TOKEN.fullmatch(token) or self.lookup is None:
            raise DelegatedCredentialInvalid("run credential is unavailable or invalid")
        try:
            resolved = self.lookup(hashlib.sha256(token.encode("ascii")).hexdigest())
            if inspect.isawaitable(resolved):
                resolved = await resolved
            scope = resolved.delegation
            if (not scope.operations or len(set(scope.operations)) != len(scope.operations)
                    or not set(scope.operations) <= _ALLOWED
                    or not isinstance(scope.run_id, str) or not scope.run_id
                    or not isinstance(scope.delegation_id, str) or not scope.delegation_id
                    or not isinstance(resolved.repo_id, str) or not resolved.repo_id
                    or resolved.repo_id == "*"
                    or not isinstance(resolved.workspace_id, str) or not resolved.workspace_id):
                raise ValueError("invalid owner scope")
            if (self.expected_workspace_id is not None
                    and resolved.workspace_id != self.expected_workspace_id):
                raise ValueError("delegation belongs to another hosted workspace")
            if self.allowed_repo_ids is not None and resolved.repo_id not in self.allowed_repo_ids:
                raise ValueError("delegation belongs to another hosted repository")
            expiry = resolved.expires_at_datetime
            if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                raise ValueError("expired owner credential")
            return DelegatedIdentity(
                actor="delegation:" + scope.delegation_id, environment=self.environment,
                authorities=frozenset(resolved.authorities) & _AUTHORITIES,
                repo_ids=frozenset({resolved.repo_id}),
                workspace_id=resolved.workspace_id, principal_id=resolved.principal_id,
                client_id=resolved.client_id, grant_id=resolved.grant_id,
                delegation=scope, expires_at=expiry,
            )
        except Exception:
            # Never echo the bearer, owner exception or internal lookup details.
            # A missing owner, expired/revoked record or schema failure is closed.
            raise DelegatedCredentialInvalid("run credential is unavailable or invalid") from None

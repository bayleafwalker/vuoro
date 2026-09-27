"""Signs every edge -> shell call that forwards an assertion (agentops#2519).

The edge consumes each gateway assertion's jti when it verifies the inbound
MCP request, and one tool call can then reach the shell several times with
that assertion (resolve, tail, append, the chain-conflict retries).  The
shell accepts those calls only with a one-use edge proof bound to the exact
method, path, assertion and body (`vuoro_service.edge_proof`); this httpx
auth hook mints one per request, so no call site can forget it.

The per-pod key is a MAC key over requests the shell verifies the gateway's
signature on anyway: it cannot mint an identity or widen one.
"""

from __future__ import annotations

from collections.abc import Generator

import httpx
from vuoro_service.edge_proof import PROOF_HEADER, mint_edge_proof

__all__ = ["EdgeProofAuth"]

_IDENTITY_HEADER = "x-vuoro-identity"


class EdgeProofAuth(httpx.Auth):
    """Attach a fresh edge proof to each request carrying an assertion."""

    requires_request_body = True

    def __init__(self, key: bytes) -> None:
        self._key = key

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        assertion = request.headers.get(_IDENTITY_HEADER)
        if assertion is not None:
            request.headers[PROOF_HEADER] = mint_edge_proof(
                self._key,
                method=request.method,
                path=request.url.path,
                assertion=assertion,
                body=request.content,
            )
        yield request

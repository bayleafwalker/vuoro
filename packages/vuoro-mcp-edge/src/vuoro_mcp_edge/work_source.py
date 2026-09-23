"""`ShellWorkSource`: the sprintctl public-work contract over the runtime shell.

The edge calls the vuoro runtime shell on localhost (`POST /api/invoke/v1`)
with the caller's own gateway assertion, forwarded verbatim.  Design
constraints, each load-bearing:

* **No credential of its own.** No workspace token, no DSN, no signing key.
  Every upstream invocation carries the `X-Vuoro-Identity` assertion the
  gateway minted for the inbound MCP request, plus its `X-Request-Id`, and
  the shell verifies both again.  The invocation's `request_id` *is* that
  request id, because the shell binds the signed `request_id` claim to the
  invocation envelope.
* **No response cache.** Authorization is per caller: a cached answer would
  hand one caller's result to another without the shell ever seeing the
  second caller's assertion.  Only the catalog check is cached, and the
  catalog is public and caller-independent.
* **Catalog check with one stale-catalog retry.** A wrong or missing
  operation name fails loudly on the first call with the advertised names,
  never as an empty list.
* **Strict emission.** Every result goes through `contract.validate_*`, which
  refuses any record whose key set is not exactly the contract's.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .contract import (
    OPERATION_ITEM,
    OPERATION_LIST,
    REQUIRED_OPERATIONS,
    validate_item_result,
    validate_list_result,
)
from .errors import WorkSourceUnavailable

__all__ = [
    "CLIENT_PROTOCOL",
    "DEFAULT_UPSTREAM_URL",
    "ForwardedIdentity",
    "ShellWorkSource",
]

#: The runtime shell's client protocol revision.
CLIENT_PROTOCOL = "1"
DEFAULT_UPSTREAM_URL = "http://127.0.0.1:8080"

_PROTOCOL_HEADER = "X-Vuoro-Client-Protocol"
_IDENTITY_HEADER = "X-Vuoro-Identity"
_REQUEST_ID_HEADER = "X-Request-Id"


class ForwardedIdentity:
    """The inbound request's assertion and correlation id, passed through."""

    __slots__ = ("assertion", "repo_id", "request_id")

    def __init__(self, *, assertion: str, request_id: str, repo_id: str) -> None:
        self.assertion = assertion
        self.request_id = request_id
        self.repo_id = repo_id

    def headers(self) -> dict[str, str]:
        return {
            _IDENTITY_HEADER: self.assertion,
            _REQUEST_ID_HEADER: self.request_id,
        }


class ShellWorkSource:
    """Reads the public-work contract through the runtime shell's invoke API."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_UPSTREAM_URL,
        request_timeout: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=request_timeout,
            transport=transport,
            headers={_PROTOCOL_HEADER: CLIENT_PROTOCOL},
        )
        self._catalog_revision: str | None = None
        self._catalog_checked = False
        self._catalog_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- contract operations -------------------------------------------------

    async def list_work(self, identity: ForwardedIdentity) -> dict[str, Any]:
        """`work.public.list-v1`: every open item, validated, owner order."""

        result = await self._invoke(OPERATION_LIST, {}, identity)
        return validate_list_result(result)

    async def describe_work(
        self, identity: ForwardedIdentity, work_id: int
    ) -> dict[str, Any]:
        """`work.public.item-v1`: one item, validated.  Not-found is an error."""

        result = await self._invoke(OPERATION_ITEM, {"work_id": work_id}, identity)
        return validate_item_result(result)

    # -- transport -----------------------------------------------------------

    async def _ensure_catalog(self, *, force_refresh: bool = False) -> None:
        async with self._catalog_lock:
            if self._catalog_checked and not force_refresh:
                return
            self._catalog_checked = False
            try:
                response = await self._client.get("/api/catalog/v1")
            except httpx.HTTPError as exc:
                raise WorkSourceUnavailable(
                    "transport-error", f"catalog fetch failed: {type(exc).__name__}"
                ) from exc
            if response.status_code >= 400:
                raise WorkSourceUnavailable(
                    "catalog-unavailable",
                    f"catalog fetch returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            try:
                catalog = response.json()
            except ValueError as exc:
                raise WorkSourceUnavailable(
                    "invalid-response", "catalog body is not JSON"
                ) from exc
            if not isinstance(catalog, dict) or not isinstance(
                catalog.get("revision"), str
            ):
                raise WorkSourceUnavailable(
                    "invalid-response", "catalog body has no revision"
                )
            advertised = {
                operation.get("name")
                for operation in catalog.get("operations") or ()
                if isinstance(operation, dict)
            }
            missing = [name for name in REQUIRED_OPERATIONS if name not in advertised]
            if missing:
                raise WorkSourceUnavailable(
                    "catalog-mismatch",
                    "the runtime catalog does not advertise "
                    f"{', '.join(missing)}; advertised work.public operations: "
                    f"{', '.join(sorted(n for n in advertised if isinstance(n, str) and n.startswith('work.public.'))) or 'none'}",
                )
            self._catalog_revision = catalog["revision"]
            self._catalog_checked = True

    async def _invoke(
        self, operation: str, arguments: dict[str, Any], identity: ForwardedIdentity
    ) -> Any:
        await self._ensure_catalog()
        response = await self._post(operation, arguments, identity)
        if response.status_code == 409 and _error_code(response) == "stale-catalog":
            # Retry exactly once against the refreshed catalog, then surface
            # whatever comes back.  The assertion's 30 s lifetime covers it.
            await self._ensure_catalog(force_refresh=True)
            response = await self._post(operation, arguments, identity)
        return _unwrap(response, operation)

    async def _post(
        self, operation: str, arguments: dict[str, Any], identity: ForwardedIdentity
    ) -> httpx.Response:
        envelope = {
            "schema_version": "invocation/v1",
            # The shell requires the signed request_id claim, the
            # X-Request-Id header and this field to agree.
            "request_id": identity.request_id,
            "operation": operation,
            "arguments": arguments,
            "catalog_revision": self._catalog_revision,
            "basis_revision": None,
            "idempotency_key": None,
            "repo_id": identity.repo_id,
        }
        try:
            return await self._client.post(
                "/api/invoke/v1", json=envelope, headers=identity.headers()
            )
        except httpx.HTTPError as exc:
            raise WorkSourceUnavailable(
                "transport-error", f"{operation} failed: {type(exc).__name__}"
            ) from exc


def _error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    return error.get("code") if isinstance(error, dict) else None


def _unwrap(response: httpx.Response, operation: str) -> Any:
    try:
        body = response.json()
    except ValueError as exc:
        raise WorkSourceUnavailable(
            "invalid-response",
            f"{operation} returned a non-JSON body (HTTP {response.status_code})",
            status_code=response.status_code,
        ) from exc
    if not isinstance(body, dict):
        raise WorkSourceUnavailable(
            "invalid-response",
            f"{operation} returned {type(body).__name__}, expected an envelope",
            status_code=response.status_code,
        )
    if response.status_code >= 400 or body.get("status") != "accepted":
        error = body.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        raise WorkSourceUnavailable(
            code if isinstance(code, str) and code else "invocation-rejected",
            message
            if isinstance(message, str) and message
            else f"{operation} was not accepted (HTTP {response.status_code})",
            status_code=response.status_code,
        )
    if body.get("operation") not in (None, operation):
        raise WorkSourceUnavailable(
            "invalid-response", f"{operation} response names a different operation"
        )
    return body.get("result")

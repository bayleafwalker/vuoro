"""Schema-driven asynchronous transport client for protocol v1."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from jsonschema import Draft202012Validator

from vuoro_client.errors import (
    ClientIncompatibleError,
    InvocationRejectedError,
    OperationNotFoundError,
)


PROTOCOL_VERSION = 1
SUPPORTED_SCHEMA_FEATURES = frozenset(
    {
        "json-schema-draft-2020-12",
        "local-defs-ref",
    }
)


@dataclass(frozen=True)
class Profile:
    name: str
    endpoint: str
    credential_ref: str
    expected_environment: str | None = None


CredentialResolver = Callable[[str], str]


class AsyncVuoroClient:
    def __init__(
        self,
        profile: Profile,
        credential_resolver: CredentialResolver,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        supported_schema_features: frozenset[str] = SUPPORTED_SCHEMA_FEATURES,
    ) -> None:
        self.profile = profile
        self._credential_resolver = credential_resolver
        self.supported_schema_features = supported_schema_features
        self._http = httpx.AsyncClient(base_url=profile.endpoint, transport=transport)
        self._catalog: dict[str, Any] | None = None
        self._catalog_etag: str | None = None
        self.active_environment: str | None = None

    async def __aenter__(self) -> AsyncVuoroClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _headers(self, *, authenticated: bool) -> dict[str, str]:
        headers = {"X-Vuoro-Client-Protocol": str(PROTOCOL_VERSION)}
        if authenticated:
            token = self._credential_resolver(self.profile.credential_ref)
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def handshake(self) -> dict[str, Any]:
        response = await self._http.get("/api/meta/v1/handshake")
        response.raise_for_status()
        handshake = response.json()
        protocol_range = handshake["client_protocol"]
        if (
            not protocol_range["minimum"]
            <= PROTOCOL_VERSION
            <= protocol_range["maximum"]
        ):
            raise ClientIncompatibleError(
                f"protocol {PROTOCOL_VERSION} is outside service range "
                f"{protocol_range['minimum']}..{protocol_range['maximum']}"
            )
        environment = handshake["environment"]["name"]
        if (
            self.profile.expected_environment
            and environment != self.profile.expected_environment
        ):
            raise ClientIncompatibleError(
                f"profile expects environment {self.profile.expected_environment!r}, got {environment!r}"
            )
        self.active_environment = environment
        return handshake

    async def catalog(self, *, force_refresh: bool = False) -> dict[str, Any]:
        headers = self._headers(authenticated=False)
        if self._catalog_etag and not force_refresh:
            headers["If-None-Match"] = self._catalog_etag
        response = await self._http.get("/api/catalog/v1", headers=headers)
        if response.status_code == 304 and self._catalog is not None:
            return self._catalog
        if response.status_code == 426:
            raise ClientIncompatibleError(
                "service rejected client protocol during catalog discovery"
            )
        response.raise_for_status()
        catalog = response.json()
        self._catalog = catalog
        self._catalog_etag = response.headers.get("etag")
        return catalog

    async def _operation(self, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        catalog = await self.catalog()
        operation = next(
            (
                candidate
                for candidate in catalog["operations"]
                if candidate["name"] == name
            ),
            None,
        )
        if operation is None:
            catalog = await self.catalog(force_refresh=True)
            operation = next(
                (
                    candidate
                    for candidate in catalog["operations"]
                    if candidate["name"] == name
                ),
                None,
            )
        if operation is None:
            raise OperationNotFoundError(name)
        missing = sorted(
            set(operation.get("required_client_schema_features", []))
            - self.supported_schema_features
        )
        if missing:
            raise ClientIncompatibleError(
                f"operation {name} requires unsupported schema features: {', '.join(missing)}"
            )
        return catalog, operation

    async def invoke(
        self,
        operation_name: str,
        arguments: Any,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        catalog, operation = await self._operation(operation_name)
        Draft202012Validator(operation["input_schema"]).validate(arguments)
        response = await self._http.post(
            "/api/invoke/v1",
            headers=self._headers(authenticated=True),
            json={
                "schema_version": "invocation/v1",
                "operation": operation_name,
                "arguments": arguments,
                "catalog_revision": catalog["revision"],
                "idempotency_key": idempotency_key,
            },
        )
        envelope = response.json()
        if (
            response.status_code == 409
            and envelope.get("error", {}).get("code") == "stale-catalog"
        ):
            self._catalog = None
            self._catalog_etag = None
        if response.status_code == 426:
            raise ClientIncompatibleError(envelope["error"]["message"])
        if response.is_error or envelope.get("status") != "accepted":
            error = envelope.get("error") or {
                "code": "transport-error",
                "message": response.text,
            }
            raise InvocationRejectedError(
                error["code"],
                error["message"],
                status_code=response.status_code,
            )
        Draft202012Validator(operation["result_schema"]).validate(envelope["result"])
        return envelope["result"]

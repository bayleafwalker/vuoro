"""Schema-driven asynchronous transport client for protocol v1."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import asyncio
import math
import time
from typing import Any, Literal
from uuid import uuid4

import httpx
from jsonschema import Draft202012Validator

from vuoro_client.errors import (
    ClientIncompatibleError,
    InvocationRejectedError,
    OperationNotFoundError,
)
from vuoro_client.profile import Profile
from vuoro_client.resources import ResourceChanges, ResourceReference, ResourceSnapshot


PROTOCOL_VERSION = 1
SUPPORTED_SCHEMA_FEATURES = frozenset(
    {
        "json-schema-draft-2020-12",
        "local-defs-ref",
    }
)


CredentialResolver = Callable[[str], str]
ObservationHook = Callable[[Mapping[str, Any]], None]


class AsyncVuoroClient:
    def __init__(
        self,
        profile: Profile,
        credential_resolver: CredentialResolver,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        supported_schema_features: frozenset[str] = SUPPORTED_SCHEMA_FEATURES,
        observation_hook: ObservationHook | None = None,
        observation_state: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
    ) -> None:
        self.profile = profile
        self._credential_resolver = credential_resolver
        self.supported_schema_features = supported_schema_features
        self._http = httpx.AsyncClient(base_url=profile.endpoint, transport=transport)
        self._catalog: dict[str, Any] | None = None
        self._catalog_etag: str | None = None
        self.active_environment: str | None = None
        self.active_environment_class: str | None = None
        self.active_environment_constraints: tuple[str, ...] = ()
        self.active_environment_runbook_refs: tuple[str, ...] = ()
        self.invocation_schema_versions: list[str] = ["invocation/v1"]
        self._observation_hook = observation_hook
        self._observation_state = {
            key: {"event_ids": set(value.get("event_ids", ())), "cursors": list(value.get("cursors", ()))}
            for key, value in (observation_state or {}).items()
        }

    async def __aenter__(self) -> AsyncVuoroClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def export_observation_state(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Transfer reconnect-only dedup/cursor state; contains no authority."""
        return {key: {"event_ids": tuple(sorted(value["event_ids"])), "cursors": tuple(value["cursors"])}
                for key, value in self._observation_state.items()}

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
        service_release = handshake.get("service_release")
        if service_release is not None and (
            not isinstance(service_release, dict)
            or service_release.get("distribution") != "vuoro-service"
            or not isinstance(service_release.get("version"), str)
            or not service_release["version"]
        ):
            raise ClientIncompatibleError("service release identity is invalid")
        environment = handshake["environment"]["name"]
        if (
            self.profile.expected_environment
            and environment != self.profile.expected_environment
        ):
            raise ClientIncompatibleError(
                f"profile expects environment {self.profile.expected_environment!r}, got {environment!r}"
            )
        self.active_environment = environment
        self.active_environment_class = handshake["environment"]["environment_class"]
        self.active_environment_constraints = tuple(
            handshake["environment"].get("constraints", [])
        )
        self.active_environment_runbook_refs = tuple(
            handshake["environment"].get("runbook_refs", [])
        )
        self.invocation_schema_versions = handshake.get(
            "invocation_schema_versions", ["invocation/v1"]
        )
        return handshake

    def describe_active_environment(self) -> str:
        """Render the currently active environment for display in a session.

        Must be called after `handshake()`. Only surfaces the bounded,
        non-secret fields the service chooses to serve.
        """
        if self.active_environment is None:
            raise RuntimeError("describe_active_environment() called before handshake()")
        lines = [f"environment: {self.active_environment} ({self.active_environment_class})"]
        if self.active_environment_constraints:
            lines.append("constraints: " + ", ".join(self.active_environment_constraints))
        if self.active_environment_runbook_refs:
            lines.append("runbook refs:")
            lines.extend(f"  - {ref}" for ref in self.active_environment_runbook_refs)
        return "\n".join(lines)

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
        request_id: str | None = None,
        basis_revision: str | None = None,
        idempotency_key: str | None = None,
        repo_id: str | None = None,
        transient_credentials: Mapping[str, str] | None = None,
    ) -> Any:
        use_v2 = bool(transient_credentials)
        if use_v2:
            if self.active_environment is None:
                await self.handshake()
            if "invocation/v2" not in self.invocation_schema_versions:
                raise ClientIncompatibleError(
                    "server does not advertise invocation/v2; transient credentials "
                    "cannot be transported"
                )
        return await self._invoke_version(
            2 if use_v2 else 1,
            operation_name,
            arguments,
            request_id=request_id,
            basis_revision=basis_revision,
            idempotency_key=idempotency_key,
            repo_id=repo_id,
            transient_credentials=transient_credentials,
        )

    async def _resource_descriptor(self, resource_kind: str) -> tuple[dict[str, Any], dict[str, Any]]:
        catalog = await self.catalog()
        descriptor = next(
            (item for item in catalog.get("resource_kinds", ()) if item["resource_kind"] == resource_kind),
            None,
        )
        if descriptor is None:
            catalog = await self.catalog(force_refresh=True)
            descriptor = next(
                (item for item in catalog.get("resource_kinds", ()) if item["resource_kind"] == resource_kind),
                None,
            )
        if descriptor is None:
            raise ClientIncompatibleError(f"resource kind is not advertised: {resource_kind}")
        return catalog, descriptor

    async def get(
        self, resource_kind: str, resource_ref: str, *, repo_id: str | None = None
    ) -> dict[str, Any]:
        """Fetch an owner snapshot without interpreting its domain state."""
        _catalog, descriptor = await self._resource_descriptor(resource_kind)
        result = await self.invoke(
            descriptor["observation"]["snapshot_operation"],
            {"resource_ref": resource_ref},
            repo_id=repo_id,
        )
        result = ResourceSnapshot.model_validate(result).model_dump(mode="json")
        if not isinstance(result, dict) or result.get("reference") != resource_ref:
            raise ClientIncompatibleError("snapshot response does not bind the requested resource")
        if not isinstance(result.get("cursor"), str) or not isinstance(result.get("terminal"), bool) or not isinstance(result.get("state"), dict):
            raise ClientIncompatibleError("snapshot response is not a neutral resource envelope")
        return result

    async def changes(
        self,
        resource_kind: str,
        resource_ref: str,
        cursor: str,
        *,
        wait_seconds: int = 0,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Poll changes, preserving opaque cursors and deduplicating revisions."""
        if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, int) or wait_seconds < 0:
            raise ValueError("wait_seconds must be a non-negative integer")
        catalog, descriptor = await self._resource_descriptor(resource_kind)
        maximum = 0
        for capability in catalog.get("observation_transports", ()):
            if capability.get("transport") == "bounded-long-poll":
                maximum = int(capability["maximum_wait_seconds"])
                break
        if wait_seconds and (not maximum or wait_seconds > maximum):
            raise ClientIncompatibleError(
                f"bounded-long-poll does not support wait_seconds={wait_seconds}"
            )
        selected = "bounded-long-poll" if wait_seconds else "poll"
        if self._observation_hook:
            self._observation_hook({"event": "observation.transport-selected", "transport": selected,
                                    "resource_kind": resource_kind, "wait_seconds": wait_seconds})
        result = await self.invoke(
            descriptor["observation"]["changes_operation"],
            {"resource_ref": resource_ref, "cursor": cursor, "wait_seconds": wait_seconds},
            repo_id=repo_id,
        )
        result = ResourceChanges.model_validate(result).model_dump(mode="json")
        if not isinstance(result, dict) or result.get("reference") != resource_ref:
            raise ClientIncompatibleError("changes response does not bind the requested resource")
        changes = result.get("events")
        next_cursor = result.get("next_cursor")
        if not isinstance(changes, list) or not isinstance(next_cursor, str) or not next_cursor:
            raise ClientIncompatibleError("changes response must contain events and an opaque next_cursor")
        state = self._observation_state.setdefault((resource_kind, resource_ref), {"event_ids": set(), "cursors": []})
        cursors = state["cursors"]
        if next_cursor in cursors and (not cursors or next_cursor != cursors[-1]):
            raise ClientIncompatibleError("owner cursor chain regressed")
        if not cursors or cursors[-1] != cursor:
            cursors.append(cursor)
        if next_cursor != cursors[-1]:
            cursors.append(next_cursor)
        seen = state["event_ids"]
        deduplicated = []
        for change in changes:
            event_id = change.get("event_id") if isinstance(change, dict) else None
            if not isinstance(event_id, str) or not event_id:
                raise ClientIncompatibleError("each event must contain an opaque event_id")
            if event_id not in seen:
                seen.add(event_id)
                deduplicated.append(change)
        result = dict(result)
        result["events"] = deduplicated
        return result

    async def wait(
        self,
        resource_kind: str,
        resource_ref: str,
        *,
        until: Literal["terminal"] = "terminal",
        wait_seconds: int = 30,
        timeout: float = 900,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Observe until terminal; timeout/disconnect never issues owner commands."""
        if until != "terminal":
            raise ValueError("until must be 'terminal'")
        _catalog, descriptor = await self._resource_descriptor(resource_kind)
        if not descriptor["observation"].get("supports_terminality", False):
            raise ClientIncompatibleError(f"resource kind does not advertise terminality: {resource_kind}")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = time.monotonic() + timeout
        saved = self._observation_state.get((resource_kind, resource_ref))
        saved_cursors = list(saved.get("cursors", ())) if saved else []
        if saved_cursors:
            cursor = saved_cursors[-1]
        else:
            snapshot = await self.get(resource_kind, resource_ref, repo_id=repo_id)
            if snapshot["terminal"]:
                return snapshot
            cursor = snapshot["cursor"]
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("resource wait exceeded its overall timeout")
            try:
                delta = await asyncio.wait_for(
                    self.changes(
                        resource_kind, resource_ref, cursor,
                        wait_seconds=min(wait_seconds, max(1, math.ceil(remaining))),
                        repo_id=repo_id,
                    ),
                    timeout=remaining,
                )
            except InvocationRejectedError as error:
                if error.code != "cursor_expired":
                    raise
                snapshot = await self.get(resource_kind, resource_ref, repo_id=repo_id)
                if snapshot["terminal"]:
                    return snapshot
                cursor = snapshot["cursor"]
                continue
            except (httpx.TimeoutException, httpx.TransportError):
                if self._observation_hook:
                    self._observation_hook({"event": "observation.reconnecting", "resource_kind": resource_kind})
                continue
            cursor = delta["next_cursor"]
            if any(bool(event.get("terminal")) for event in delta["events"]):
                return await self.get(resource_kind, resource_ref, repo_id=repo_id)

    async def _invoke_version(
        self,
        version: int,
        operation_name: str,
        arguments: Any,
        *,
        request_id: str | None,
        basis_revision: str | None,
        idempotency_key: str | None,
        repo_id: str | None,
        transient_credentials: Mapping[str, str] | None,
    ) -> Any:
        catalog, operation = await self._operation(operation_name)
        Draft202012Validator(operation["input_schema"]).validate(arguments)
        payload = {
            "schema_version": f"invocation/v{version}",
            "request_id": request_id or str(uuid4()),
            "operation": operation_name,
            "arguments": arguments,
            "catalog_revision": catalog["revision"],
            "basis_revision": basis_revision,
            "idempotency_key": idempotency_key,
            "repo_id": repo_id,
        }
        if version == 2:
            payload["transient_credentials"] = dict(transient_credentials or {})
        response = await self._http.post(
            f"/api/invoke/v{version}",
            headers=self._headers(authenticated=True),
            json=payload,
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
        result = envelope["result"]
        result_contract = operation.get("result_contract")
        if result_contract and result_contract.get("mode") == "resource-reference":
            result = ResourceReference.model_validate(result).model_dump(mode="json")
        return result

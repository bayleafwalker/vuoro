"""Schema-driven asynchronous transport client for protocol v1."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib.metadata import PackageNotFoundError, version
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


def _client_version() -> str:
    try:
        return version("vuoro-client")
    except PackageNotFoundError:
        return "unknown"


USER_AGENT = f"vuoro-client/{_client_version()}"


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
        self._http = httpx.AsyncClient(
            base_url=profile.endpoint,
            transport=transport,
            headers={"User-Agent": USER_AGENT},
        )
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
        headers = self._headers(authenticated=bool(self.profile.credential_ref))
        response = await self._http.get("/api/meta/v1/handshake", headers=headers)
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
        headers = self._headers(authenticated=bool(self.profile.credential_ref))
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
    ) -> Any:
        catalog, operation = await self._operation(operation_name)
        Draft202012Validator(operation["input_schema"]).validate(arguments)
        payload = {
            "schema_version": "invocation/v1",
            "request_id": request_id or str(uuid4()),
            "operation": operation_name,
            "arguments": arguments,
            "catalog_revision": catalog["revision"],
            "basis_revision": basis_revision,
            "idempotency_key": idempotency_key,
            "repo_id": repo_id,
        }
        response = await self._http.post(
            "/api/invoke/v1",
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

    async def publish_operator_request(
        self,
        *,
        request_id: str,
        packet: Mapping[str, Any],
        dependants: list[Mapping[str, Any]],
        idempotency_key: str,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Publish one stable request through the advertised owner operation."""
        return await self.invoke(
            "work.request.publish-v1",
            {
                "request_id": request_id,
                "packet": dict(packet),
                "dependants": [dict(item) for item in dependants],
                "idempotency_key": idempotency_key,
            },
            repo_id=repo_id,
        )

    async def get_operator_request(
        self, request_id: str, *, repo_id: str | None = None
    ) -> dict[str, Any]:
        """Read the owner snapshot for one request without interpreting it."""
        return await self.invoke(
            "work.request.get-v1", {"request_id": request_id}, repo_id=repo_id
        )

    async def get_operator_request_continuation(
        self, request_id: str, *, repo_id: str | None = None
    ) -> dict[str, Any]:
        """Read one owner continuation snapshot without waking or acknowledging."""
        return await self.invoke(
            "work.request.continuation-v1", {"request_id": request_id}, repo_id=repo_id
        )

    async def record_operator_request_continuation_receipt(
        self,
        *,
        request_id: str,
        resolution_event_revision: int,
        work_item_id: int,
        expected_work_revision: str,
        idempotency_key: str,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Record the publisher's packet receipt without claiming runtime delivery."""
        return await self.invoke(
            "work.request.continuation-receipt-v1",
            {
                "request_id": request_id,
                "resolution_event_revision": resolution_event_revision,
                "work_item_id": work_item_id,
                "expected_work_revision": expected_work_revision,
                "idempotency_key": idempotency_key,
            },
            repo_id=repo_id,
        )

    async def list_operator_requests(
        self,
        *,
        limit: int = 50,
        after_request_id: str | None = None,
        state: str | None = None,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Page owner request records using the advertised request contract."""
        arguments: dict[str, Any] = {"limit": limit}
        if after_request_id is not None:
            arguments["after_request_id"] = after_request_id
        if state is not None:
            arguments["state"] = state
        return await self.invoke("work.request.list-v1", arguments, repo_id=repo_id)

    async def list_operator_request_events(
        self,
        request_id: str,
        *,
        after_revision: int = 0,
        limit: int = 50,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Read the append-only lifecycle events after a request revision."""
        return await self.invoke(
            "work.request.events-v1",
            {"request_id": request_id, "after_revision": after_revision, "limit": limit},
            repo_id=repo_id,
        )

    async def revise_operator_request(
        self,
        *,
        request_id: str,
        expected_revision: int,
        packet: Mapping[str, Any],
        idempotency_key: str,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Replace the packet only at the exact revision read by the producer."""
        return await self.invoke(
            "work.request.revise-v1",
            {
                "request_id": request_id,
                "expected_revision": expected_revision,
                "packet": dict(packet),
                "idempotency_key": idempotency_key,
            },
            repo_id=repo_id,
        )

    async def add_operator_request_dependant(
        self,
        *,
        request_id: str,
        expected_revision: int,
        dependant: Mapping[str, Any],
        idempotency_key: str,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Attach another source session/work item at an exact revision."""
        return await self.invoke(
            "work.request.dependent-add-v1",
            {
                "request_id": request_id,
                "expected_revision": expected_revision,
                "dependant": dict(dependant),
                "idempotency_key": idempotency_key,
            },
            repo_id=repo_id,
        )

    async def withdraw_operator_request_dependant(
        self,
        *,
        request_id: str,
        expected_revision: int,
        work_item_id: int,
        session_id: str,
        idempotency_key: str,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Withdraw only this work/session dependant at an exact revision."""
        return await self.invoke(
            "work.request.dependent-withdraw-v1",
            {
                "request_id": request_id,
                "expected_revision": expected_revision,
                "work_item_id": work_item_id,
                "session_id": session_id,
                "idempotency_key": idempotency_key,
            },
            repo_id=repo_id,
        )

    async def record_operator_request_progress(
        self,
        *,
        request_id: str,
        expected_revision: int,
        work_item_id: int,
        session_id: str,
        step_id: str,
        status: str,
        note: str,
        idempotency_key: str,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one dependent's procedure progress without closing its request."""
        return await self.invoke(
            "work.request.progress-v1",
            {"request_id": request_id, "expected_revision": expected_revision,
             "work_item_id": work_item_id, "session_id": session_id, "step_id": step_id,
             "status": status, "note": note, "idempotency_key": idempotency_key},
            repo_id=repo_id,
        )

    async def report_operator_request_performed(
        self,
        *,
        request_id: str,
        expected_revision: int,
        outcome: str,
        note: str,
        idempotency_key: str,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Report a performed, partial or uncertain result; this is unverified."""
        return await self.invoke(
            "work.request.report-performed-v1",
            {"request_id": request_id, "expected_revision": expected_revision,
             "outcome": outcome, "note": note, "idempotency_key": idempotency_key},
            repo_id=repo_id,
        )

    async def attest_operator_request(
        self,
        *,
        request_id: str,
        expected_revision: int,
        statement: str,
        performed_at: str,
        idempotency_key: str,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Record an explicitly authorized human completion attestation."""
        return await self.invoke(
            "work.request.attest-v1",
            {"request_id": request_id, "expected_revision": expected_revision,
             "statement": statement, "performed_at": performed_at,
             "idempotency_key": idempotency_key},
            repo_id=repo_id,
        )

    async def decline_operator_request(
        self,
        *,
        request_id: str,
        expected_revision: int,
        reason: str,
        idempotency_key: str,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Decline a live request as a separate lifecycle outcome."""
        return await self.invoke(
            "work.request.decline-v1",
            {"request_id": request_id, "expected_revision": expected_revision,
             "reason": reason, "idempotency_key": idempotency_key},
            repo_id=repo_id,
        )

    async def supersede_operator_request(
        self,
        *,
        request_id: str,
        expected_revision: int,
        replacement_request_id: str,
        packet: Mapping[str, Any],
        idempotency_key: str,
        repo_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a replacement revision and carry all active dependants forward."""
        return await self.invoke(
            "work.request.supersede-v1",
            {"request_id": request_id, "expected_revision": expected_revision,
             "replacement_request_id": replacement_request_id, "packet": dict(packet),
             "idempotency_key": idempotency_key},
            repo_id=repo_id,
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


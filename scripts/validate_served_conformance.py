"""Credential-free served HTTP conformance gate for the built distributions.

Run this script from an isolated environment containing the built
``vuoro-client`` and ``vuoro-service`` wheels. It intentionally uses a small
in-memory adapter and ASGI HTTP transport: no domain package, database,
migration, deployment, or editable checkout is involved.
"""

from __future__ import annotations

import asyncio
from importlib.metadata import version as installed_version
import sys

import httpx

from vuoro_client import (
    AsyncVuoroClient,
    ClientIncompatibleError,
    InvocationRejectedError,
    Profile,
    __version__ as client_version,
)
from vuoro_bootstrap import BootstrapApi, __version__ as bootstrap_version
from vuoro_service import __version__ as service_version
from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry, DEFAULT_SCHEMA_FEATURES
from vuoro_service.contracts import DomainCompatibility, OperationDefinition
from vuoro_service.identity import Identity, StaticBearerIdentityResolver


def _schema(*, required: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": list(required),
        "properties": {name: {"type": "string"} for name in required},
        "additionalProperties": False,
    }


def _app() -> tuple[object, CatalogRegistry]:
    registry = CatalogRegistry(
        schema_features=DEFAULT_SCHEMA_FEATURES | {"future-schema-feature"}
    )
    registry.register(
        OperationDefinition(
            name="work.conformance.read",
            owning_domain="work",
            input_schema=_schema(required=("value",)),
            result_schema=_schema(required=("value", "repo_id")),
            required_authority="work:read",
            execution_semantics="read",
            idempotency="not-allowed",
            repo_scoped=True,
        ),
        lambda arguments, context: {"value": arguments["value"], "repo_id": context.repo_id},
    )
    registry.register(
        OperationDefinition(
            name="work.conformance.future",
            owning_domain="work",
            input_schema=_schema(),
            result_schema={"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"},
            required_authority="work:read",
            execution_semantics="read",
            idempotency="not-allowed",
            required_client_schema_features=[
                "json-schema-draft-2020-12",
                "future-schema-feature",
            ],
        ),
        lambda _arguments, _context: {},
    )
    resolver = StaticBearerIdentityResolver(
        {
            "read-token": Identity(
                actor="human:reader",
                environment="served-test",
                authorities=frozenset({"work:read"}),
                repo_ids=frozenset({"repo-a"}),
            ),
            "no-authority": Identity(
                actor="human:limited",
                environment="served-test",
                authorities=frozenset(),
                repo_ids=frozenset({"repo-a"}),
            ),
            "wrong-repo": Identity(
                actor="human:other-repo",
                environment="served-test",
                authorities=frozenset({"work:read"}),
                repo_ids=frozenset({"repo-a"}),
            ),
        }
    )
    return (
        create_app(
            settings=ServiceSettings(
                environment_name="served-test",
                environment_class="development",
                compatibility_state="compatible",
                domains={
                    "work": DomainCompatibility(
                        api_version="work/v1",
                        schema_version="work-schema/v1",
                        state="compatible",
                    )
                },
            ),
            registry=registry,
            identity_resolver=resolver,
        ),
        registry,
    )


async def run() -> None:
    assert installed_version("vuoro-client") == client_version
    assert installed_version("vuoro-service") == service_version
    assert installed_version("vuoro-bootstrap") == bootstrap_version
    assert BootstrapApi is not None
    app, registry = _app()
    profile = Profile("served-test", "http://served.test", "token:read", "served-test")
    asgi_transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=asgi_transport, base_url="http://served.test"
    ) as direct:
        async with AsyncVuoroClient(profile, lambda _ref: "read-token", transport=asgi_transport) as client:
            handshake = await client.handshake()
            assert handshake["service_release"]["distribution"] == "vuoro-service"
            assert handshake["service_release"]["version"] == service_version
            catalog = await client.catalog()
            assert catalog["revision"] == registry.revision
            result = await client.invoke(
                "work.conformance.read",
                {"value": "accepted"},
                repo_id="repo-a",
            )
            assert result == {"value": "accepted", "repo_id": "repo-a"}

            incompatible = False
            try:
                await client.invoke("work.conformance.future", {})
            except ClientIncompatibleError:
                incompatible = True
            assert incompatible, "unsupported schema features must fail before invocation"

            stale = await direct.post(
                "/api/invoke/v1",
                headers={"X-Vuoro-Client-Protocol": "1", "Authorization": "Bearer read-token"},
                json={
                    "schema_version": "invocation/v1",
                    "request_id": "stale-request",
                    "operation": "work.conformance.read",
                    "arguments": {"value": "stale"},
                    "catalog_revision": "not-the-current-revision",
                },
            )
            assert stale.status_code == 409
            assert stale.json()["error"]["code"] == "stale-catalog"

            malformed = await direct.post(
                "/api/invoke/v1",
                headers={"X-Vuoro-Client-Protocol": "1", "Authorization": "Bearer read-token"},
                json={"schema_version": "invocation/v1", "request_id": "malformed"},
            )
            assert malformed.status_code == 422
            assert malformed.json()["error"]["code"] == "invalid-invocation-envelope"

        async with AsyncVuoroClient(
            Profile("limited", "http://served.test", "token:limited", "served-test"),
            lambda _ref: "no-authority",
            transport=asgi_transport,
        ) as limited:
            try:
                await limited.invoke(
                    "work.conformance.read", {"value": "rejected"}, repo_id="repo-a"
                )
            except InvocationRejectedError as error:
                assert error.code == "authority-required"
            else:
                raise AssertionError("missing authority must be rejected")

        async with AsyncVuoroClient(
            Profile("wrong-repo", "http://served.test", "token:wrong", "served-test"),
            lambda _ref: "wrong-repo",
            transport=asgi_transport,
        ) as wrong_repo_client:
            try:
                await wrong_repo_client.invoke(
                    "work.conformance.read", {"value": "rejected"}, repo_id="repo-b"
                )
            except InvocationRejectedError as error:
                assert error.code == "repo-unauthorized"
            else:
                raise AssertionError("wrong repository must be rejected")

    print("served conformance: PASS")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except (AssertionError, httpx.HTTPError) as error:
        print(f"served conformance: FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)

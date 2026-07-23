from __future__ import annotations

import logging
import pickle

import httpx
import pytest

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry, OperationRejectedError
from vuoro_service.contracts import DomainCompatibility, OperationDefinition
from vuoro_service.identity import (
    Identity,
    InvocationContext,
    StaticBearerIdentityResolver,
    TransientCredentials,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


VALID_KEY_A = "sha256:" + "a" * 64
VALID_KEY_B = "sha256:" + "b" * 64

# Adversarial sentinel: obviously wrong to see anywhere in logs, response
# bodies, or serialized output. Every leak-proof test in this file uses this
# exact string so a failure is unambiguous.
SENTINEL = "SPROOF-DO-NOT-LEAK-9f8e7d6c"


def _record_text_blob(record: logging.LogRecord) -> str:
    """Flatten everything a LogRecord could plausibly expose into one string."""

    pieces = [record.getMessage(), repr(record.args)]
    if record.exc_info:
        pieces.append(logging.Formatter().formatException(record.exc_info))
    if record.exc_text:
        pieces.append(record.exc_text)
    # Any custom `extra={...}` keys land as arbitrary attributes on the
    # LogRecord instance itself, not in a dedicated dict, so scan __dict__.
    for key, value in record.__dict__.items():
        if key in (
            "msg",
            "args",
            "exc_info",
            "exc_text",
            "stack_info",
        ):
            continue
        pieces.append(f"{key}={value!r}")
    return "\n".join(pieces)


def configured_service(handler=None) -> tuple[CatalogRegistry, object]:
    registry = CatalogRegistry()
    registry.register(
        OperationDefinition(
            name="work.pilot.transition",
            owning_domain="work",
            input_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": "integer"}},
                "additionalProperties": False,
            },
            result_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["accepted"],
                "properties": {"accepted": {"type": "integer"}},
                "additionalProperties": False,
            },
            required_authority="work.transition",
            execution_semantics="write",
            idempotency="required",
        ),
        handler or (lambda arguments, context: {"accepted": arguments["value"]}),
    )
    settings = ServiceSettings(
        environment_name="vuoro-dev",
        environment_class="development",
        compatibility_state="compatible",
        domains={
            "work": DomainCompatibility(
                api_version="work/v1",
                schema_version="work-schema/1",
                state="compatible",
            )
        },
    )
    resolver = StaticBearerIdentityResolver(
        {
            "dev-token": Identity(
                actor="human:developer",
                environment="vuoro-dev",
                authorities=frozenset({"work.transition"}),
            ),
        }
    )
    return registry, create_app(
        settings=settings, registry=registry, identity_resolver=resolver
    )


def base_request(revision: str, **overrides) -> dict:
    request = {
        "schema_version": "invocation/v2",
        "request_id": "request-v2",
        "operation": "work.pilot.transition",
        "arguments": {"value": 7},
        "catalog_revision": revision,
        "idempotency_key": "transition-7",
        "transient_credentials": {},
    }
    request.update(overrides)
    return request


@pytest.mark.anyio
async def test_handshake_advertises_invocation_schema_versions_with_v2() -> None:
    _, app = configured_service()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        handshake = (await client.get("/api/meta/v1/handshake")).json()
        assert handshake["invocation_schema_versions"] == [
            "invocation/v1",
            "invocation/v2",
        ]
        assert handshake["schema_versions"]["invocation"] == "invocation/v1"


@pytest.mark.anyio
async def test_handshake_advertises_v1_only_when_v2_not_wired() -> None:
    registry = CatalogRegistry()
    settings = ServiceSettings(
        environment_name="vuoro-dev",
        environment_class="development",
        compatibility_state="compatible",
        invocation_schema_versions=("invocation/v1",),
    )
    app = create_app(settings=settings, registry=registry)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        handshake = (await client.get("/api/meta/v1/handshake")).json()
        assert handshake["invocation_schema_versions"] == ["invocation/v1"]


@pytest.mark.anyio
async def test_v2_multi_binding_reaches_handler_via_reveal() -> None:
    observed = None

    def handler(arguments, context):
        nonlocal observed
        observed = context
        return {"accepted": arguments["value"]}

    registry, app = configured_service(handler)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v2",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer dev-token",
            },
            json=base_request(
                registry.revision,
                transient_credentials={
                    VALID_KEY_A: "proof-a",
                    VALID_KEY_B: "proof-b",
                },
            ),
        )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert observed is not None
    assert observed.transient_credentials.reveal(VALID_KEY_A) == "proof-a"
    assert observed.transient_credentials.reveal(VALID_KEY_B) == "proof-b"
    assert observed.transient_credentials.reveal("sha256:" + "c" * 64) is None
    assert sorted(observed.transient_credentials.keys()) == sorted(
        [VALID_KEY_A, VALID_KEY_B]
    )
    assert "proof-a" not in repr(observed.transient_credentials)
    assert "proof-b" not in repr(observed.transient_credentials)


@pytest.mark.anyio
async def test_v1_route_defaults_to_empty_transient_credentials() -> None:
    observed = None

    def handler(arguments, context):
        nonlocal observed
        observed = context
        return {"accepted": arguments["value"]}

    registry, app = configured_service(handler)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v1",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer dev-token",
            },
            json={
                "schema_version": "invocation/v1",
                "request_id": "request-v1",
                "operation": "work.pilot.transition",
                "arguments": {"value": 7},
                "catalog_revision": registry.revision,
                "idempotency_key": "transition-7",
            },
        )
    assert response.status_code == 200
    assert observed is not None
    assert bool(observed.transient_credentials) is False
    assert observed.transient_credentials.reveal(VALID_KEY_A) is None


@pytest.mark.parametrize(
    "bindings",
    [
        {"not-a-sha256": "value"},
        {"sha256:" + "g" * 64: "value"},
        {"sha256:" + "A" * 64: "value"},
        {VALID_KEY_A: ""},
        {f"sha256:{n:064x}": "value" for n in range(9)},
    ],
    ids=[
        "malformed-prefix",
        "invalid-hex-char",
        "uppercase-hex",
        "empty-value",
        "over-cap-bindings",
    ],
)
@pytest.mark.anyio
async def test_v2_structural_violations_fail_closed(bindings: dict) -> None:
    called = False

    def handler(arguments, context):
        nonlocal called
        called = True
        return {"accepted": arguments["value"]}

    registry, app = configured_service(handler)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v2",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer dev-token",
            },
            json=base_request(registry.revision, transient_credentials=bindings),
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-transient-binding"
    assert called is False


@pytest.mark.anyio
async def test_v2_route_is_exposed() -> None:
    _, app = configured_service()
    paths = {route.path for route in app.routes}
    assert "/api/invoke/v2" in paths
    assert "/api/invoke/v1" in paths


# --- Adversarial redaction proofs (sub-build 1c) -----------------------------
#
# These tests exist to prove the transient-credential redaction guarantee
# holds, not merely to assert intended behavior. Each one drives a distinct
# surface (logs on the happy path, logs on an unexpected handler crash, the
# handler-authored error-message boundary, HTTP response bodies for
# structural rejections, repr/str, and pickling) with the sentinel value
# `SENTINEL` and checks it never appears where it must not.


@pytest.mark.anyio
async def test_happy_path_never_logs_the_sentinel(caplog: pytest.LogCaptureFixture) -> None:
    def handler(arguments, context):
        # Handler legitimately reveals the credential to do its job; this
        # must never be observable via logging.
        assert context.transient_credentials.reveal(VALID_KEY_A) == SENTINEL
        return {"accepted": arguments["value"]}

    registry, app = configured_service(handler)
    caplog.set_level(logging.DEBUG, logger="vuoro_service")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v2",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer dev-token",
            },
            json=base_request(
                registry.revision,
                transient_credentials={VALID_KEY_A: SENTINEL},
            ),
        )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert SENTINEL not in response.text
    for record in caplog.records:
        assert SENTINEL not in _record_text_blob(record)


@pytest.mark.anyio
async def test_unexpected_handler_exception_never_logs_the_sentinel(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drives the ``LOGGER.exception(...)`` path in ``app.py``'s ``_dispatch``.

    ``logging.Logger.exception`` captures a full traceback by default, so
    this is the highest-value surface: it proves that neither the traceback
    text nor a naive handler that stringifies the whole context into its
    exception message can smuggle the sentinel into logs.
    """

    def handler(arguments, context):
        # Adversarial: a poorly-written handler that accidentally embeds the
        # whole invocation context (not just its own domain data) while
        # crashing. TransientCredentials' redacted __repr__ must protect the
        # value even here.
        raise Exception(f"handler exploded while holding context={context!r}")

    registry, app = configured_service(handler)
    caplog.set_level(logging.DEBUG, logger="vuoro_service")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v2",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer dev-token",
            },
            json=base_request(
                registry.revision,
                transient_credentials={VALID_KEY_A: SENTINEL},
            ),
        )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "operation-handler-failed"
    assert SENTINEL not in response.text
    assert any(record.exc_info for record in caplog.records), (
        "expected LOGGER.exception to have fired with exc_info attached"
    )
    for record in caplog.records:
        assert SENTINEL not in _record_text_blob(record)


@pytest.mark.anyio
async def test_operation_rejected_error_surfaces_handler_authored_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Documents a deliberate boundary, not a transport leak.

    The redaction guarantee covers the *transport's* ``transient_credentials``
    binding — it does not, and cannot, stop a handler from choosing to put
    arbitrary text (including a value it obtained via ``.reveal()``) into an
    ``OperationRejectedError`` message it authors itself. This test proves
    that today's ``_dispatch`` code passes such a handler-authored message
    straight through to ``error.message`` in the response body: that is
    expected/acceptable because the handler chose to put it there, but it is
    exactly the kind of thing that must be documented rather than assumed.
    """

    def handler(arguments, context):
        proof = context.transient_credentials.reveal(VALID_KEY_A)
        raise OperationRejectedError(
            "proof-rejected", f"handler rejected using proof {proof}"
        )

    registry, app = configured_service(handler)
    caplog.set_level(logging.DEBUG, logger="vuoro_service")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v2",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer dev-token",
            },
            json=base_request(
                registry.revision,
                transient_credentials={VALID_KEY_A: SENTINEL},
            ),
        )
    assert response.status_code == 409
    body = response.json()
    assert body["status"] == "rejected"
    # Documented boundary: handler-authored text is not sanitized by the
    # transport, so the sentinel the handler deliberately embedded shows up
    # here. This is the handler's own choice, not a transport-level leak.
    assert SENTINEL in body["error"]["message"]
    # The transport itself still never logs it, regardless of what the
    # handler put in its own error message.
    for record in caplog.records:
        assert SENTINEL not in _record_text_blob(record)


@pytest.mark.parametrize(
    "bindings",
    [
        {"not-a-sha256": SENTINEL},
        {"sha256:" + "g" * 64: SENTINEL},
        {"sha256:" + "A" * 64: SENTINEL},
        {VALID_KEY_A: ""},
        {f"sha256:{n:064x}": SENTINEL for n in range(9)},
    ],
    ids=[
        "malformed-prefix",
        "invalid-hex-char",
        "uppercase-hex",
        "empty-value",
        "over-cap-bindings",
    ],
)
@pytest.mark.anyio
async def test_v2_structural_violations_never_echo_supplied_values(
    bindings: dict,
) -> None:
    """Extends the structural-violation fail-closed tests: a 422 caused by a
    malformed key, empty value, or over-cap binding count must never echo any
    *value* the caller supplied back in the response body. Only non-secret
    material (the key, or nothing at all) may appear."""

    called = False

    def handler(arguments, context):
        nonlocal called
        called = True
        return {"accepted": arguments["value"]}

    registry, app = configured_service(handler)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/invoke/v2",
            headers={
                "X-Vuoro-Client-Protocol": "1",
                "Authorization": "Bearer dev-token",
            },
            json=base_request(registry.revision, transient_credentials=bindings),
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid-transient-binding"
    assert called is False
    assert SENTINEL not in response.text


class TestTransientCredentialsRedaction:
    """`repr`/`str`/serialization proofs for ``TransientCredentials`` itself."""

    def test_repr_and_str_never_contain_the_sentinel(self) -> None:
        credentials = TransientCredentials({VALID_KEY_A: SENTINEL, VALID_KEY_B: SENTINEL})
        assert SENTINEL not in repr(credentials)
        assert SENTINEL not in str(credentials)
        assert "2 binding(s) redacted" in repr(credentials)

    def test_reveal_still_returns_the_real_value(self) -> None:
        credentials = TransientCredentials({VALID_KEY_A: SENTINEL})
        assert credentials.reveal(VALID_KEY_A) == SENTINEL

    def test_invocation_context_repr_never_contains_the_sentinel(self) -> None:
        context = InvocationContext(
            identity=Identity(actor="human:developer", environment="vuoro-dev"),
            request_id="request-v2",
            basis_revision=None,
            catalog_revision="revision",
            idempotency_requirement="optional",
            idempotency_key=None,
            transient_credentials=TransientCredentials({VALID_KEY_A: SENTINEL}),
        )
        assert SENTINEL not in repr(context)
        assert SENTINEL not in str(context)

    def test_pickling_transient_credentials_is_blocked(self) -> None:
        credentials = TransientCredentials({VALID_KEY_A: SENTINEL})
        with pytest.raises(TypeError):
            pickle.dumps(credentials)

    def test_pickling_invocation_context_is_blocked_by_the_credentials_field(
        self,
    ) -> None:
        # InvocationContext is a plain frozen dataclass; nothing about it
        # blocks pickling except that one of its fields refuses to be
        # reduced. Prove the whole object inherits that refusal.
        context = InvocationContext(
            identity=Identity(actor="human:developer", environment="vuoro-dev"),
            request_id="request-v2",
            basis_revision=None,
            catalog_revision="revision",
            idempotency_requirement="optional",
            idempotency_key=None,
            transient_credentials=TransientCredentials({VALID_KEY_A: SENTINEL}),
        )
        with pytest.raises(TypeError):
            pickle.dumps(context)

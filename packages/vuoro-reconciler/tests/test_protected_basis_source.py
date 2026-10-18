"""Actual private native wrapper reads; four public intent states remain DATA."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
from uuid import UUID

import pytest

from vuoro_reconciler import protected_read_transport as wire_module
from vuoro_reconciler.protected_basis_source import NativeBasisSource
from vuoro_reconciler.protected_currentness import evaluate_currentness
from vuoro_reconciler.protected_read_transport import ProtectedReadRefused
from test_protected_copy_codec import canonical, decode, large_canonical_copy, sha
from test_protected_currentness import basis
from test_protected_read_transport import (
    Credentials,
    Loopback,
    NAMESPACE,
    TOKEN,
    certificates as certificate_fixture,
    client_context,
    profile,
    response,
)


@pytest.fixture(scope="session")
def certificates(tmp_path_factory):
    return certificate_fixture.__wrapped__(tmp_path_factory)


def invocation(request, value=None):
    return {
        "schema_version": "invocation-result/v1",
        "request_id": request["request_id"],
        "operation": "work.effect.basis-v1",
        "catalog_revision": "sha256:" + "c" * 64,
        "status": "accepted",
        "result": basis() if value is None else value,
        "error": None,
    }


def native_response(body, *, length=None):
    return response(
        body,
        headers=[
            b"Content-Length: " + str(len(body) if length is None else length).encode(),
            b"Content-Type: application/json",
        ],
    )


@pytest.mark.parametrize("state", ["proposed", "accepted", "rejected", "applied"])
@pytest.mark.parametrize(
    "status,expected",
    [
        ("pending", "READY"),
        ("active", "READY"),
        ("blocked", "DEFERRED"),
        ("done", "REFUSED"),
    ],
)
def test_actual_tls_all_native_states_and_work_status_are_data(
    certificates, state, status, expected
):
    async def run():
        async def emit(reader, writer):
            request = json.loads(server.requests[-1][1])
            raw = json.dumps(
                invocation(request, basis(state=state, status=status)), indent=2
            ).encode()
            # Native /api/invoke/v1 actually lacks D1's Cache-Control:no-store.
            writer.write(native_response(raw))
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            chosen = profile(server.port, certificates=certificates)
            credentials = Credentials(client_context(certificates))
            source = NativeBasisSource(chosen, credentials)
            effect = decode()
            returned = await source.read_basis(effect)
            assert json.loads(returned.intent_utf8)["state"] == state
            assert returned.canonical_utf8 == canonical(
                basis(state=state, status=status)
            )
            assert evaluate_currentness(effect, returned).status == expected
            assert len(credentials.calls) == 1

    asyncio.run(run())


def test_fresh_uuid_exact_invocation_schema_fixed_read_operation_and_profile(
    certificates,
):
    async def run():
        async def emit(reader, writer):
            writer.write(
                native_response(
                    canonical(invocation(json.loads(server.requests[-1][1])))
                )
            )
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            chosen = profile(server.port, certificates=certificates)
            credentials = Credentials(client_context(certificates))
            source = NativeBasisSource(chosen, credentials)
            effect = decode()
            await source.read_basis(effect)
            await source.read_basis(effect)
            first, second = [json.loads(row[1]) for row in server.requests]
            assert set(first) == {
                "schema_version",
                "request_id",
                "operation",
                "arguments",
                "repo_id",
                "catalog_revision",
                "basis_revision",
                "idempotency_key",
            }
            assert (
                UUID(first["request_id"]).version == 4
                and UUID(second["request_id"]).version == 4
            )
            assert first["request_id"] != second["request_id"]
            locator = json.loads(effect.locator_utf8)
            assert first["schema_version"] == "invocation/v1"
            assert first["operation"] == "work.effect.basis-v1"
            assert first["arguments"] == {
                "intent_id": locator["intent_id"],
                "intent_revision": locator["intent_revision"],
            }
            assert first["repo_id"] == NAMESPACE["repo_id"]
            assert (
                first["catalog_revision"]
                is first["basis_revision"]
                is first["idempotency_key"]
                is None
            )
            assert all(
                row[0].startswith(b"POST /api/invoke/v1 HTTP/1.1\r\n")
                for row in server.requests
            )
            assert all(
                b"X-Vuoro-Client-Protocol: 1\r\n" in row[0]
                for row in server.requests
            )
            assert credentials.calls == [chosen, chosen]
            with pytest.raises(AttributeError):
                source.profile = chosen

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["namespace", "repository", "forged-effect"])
def test_profile_effect_pin_before_credentials_or_network(certificates, kind):
    async def run():
        chosen = profile(
            certificates=certificates,
            namespace_utf8=canonical({**NAMESPACE, "repo_id": "foreign"})
            if kind == "namespace"
            else canonical(NAMESPACE),
            repository="foreign" if kind == "repository" else decode().repository,
        )
        credentials = Credentials(client_context(certificates))
        source = NativeBasisSource(chosen, credentials)
        effect = decode()
        if kind == "forged-effect":
            effect = replace(effect, canonical_hash="sha256:" + "0" * 64)
        with pytest.raises(ProtectedReadRefused):
            await source.read_basis(effect)
        assert credentials.calls == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "invocation/v2"),
        ("request_id", "different"),
        ("operation", "work.effect.accept-v1"),
        ("status", "rejected"),
        ("status", "error"),
        ("error", {"message": "secret-looking error"}),
        ("catalog_revision", None),
        ("catalog_revision", True),
        ("catalog_revision", ""),
        ("catalog_revision", "x" * 257),
        ("result", None),
        ("result", []),
    ],
)
def test_wrapper_closed_correlation_status_type_and_catalog_refusals(
    certificates, field, value
):
    async def run():
        async def emit(reader, writer):
            request = json.loads(server.requests[-1][1])
            body = invocation(request)
            body[field] = value
            writer.write(native_response(canonical(body)))
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            source = NativeBasisSource(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            with pytest.raises(
                ProtectedReadRefused, match="^protected-read-refused$"
            ) as error:
                await source.read_basis(decode())
            assert error.value.__cause__ is None and error.value.__context__ is None
            assert TOKEN not in str(error.value) and "secret-looking" not in str(
                error.value
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind",
    [
        "extra",
        "missing",
        "duplicate",
        "float",
        "nonfinite",
        "unsafe-int",
        "surrogate",
        "rpc-wrap",
    ],
)
def test_native_raw_wrapper_strict_json_and_exact_seven_keys(certificates, kind):
    async def run():
        async def emit(reader, writer):
            body = invocation(json.loads(server.requests[-1][1]))
            raw = canonical(body)
            if kind == "extra":
                raw = canonical({**body, "extra": None})
            elif kind == "missing":
                del body["error"]
                raw = canonical(body)
            elif kind == "duplicate":
                raw = b'{"status":"accepted",' + raw[1:]
            elif kind == "float":
                raw = raw.replace(b'"revision":1', b'"revision":1.0')
            elif kind == "nonfinite":
                raw = raw.replace(
                    b'"catalog_revision":"sha256:' + b"c" * 64 + b'"',
                    b'"catalog_revision":NaN',
                )
            elif kind == "unsafe-int":
                raw = raw.replace(b'"revision":1', b'"revision":9007199254740992')
            elif kind == "surrogate":
                raw = raw.replace(
                    b'"catalog_revision":"sha256:' + b"c" * 64 + b'"',
                    b'"catalog_revision":"\\ud800"',
                )
            else:
                raw = canonical({"result": body})
            writer.write(native_response(raw))
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            source = NativeBasisSource(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            with pytest.raises(ProtectedReadRefused):
                await source.read_basis(decode())

    asyncio.run(run())


@pytest.mark.parametrize(
    "part,field,value",
    [
        (None, "repo_id", "foreign"),
        (None, "workspace_id", "foreign"),
        ("intent", "intent_id", "different"),
        ("intent", "revision", 2),
        ("intent", "repository", "foreign"),
        ("intent", "run_id", "different"),
        ("run", "principal_id", "different"),
        ("intent", "unified_diff", "other patch"),
    ],
)
def test_basis_scope_original_proposal_and_run_joins(certificates, part, field, value):
    async def run():
        async def emit(reader, writer):
            body = basis()
            (body if part is None else body[part])[field] = value
            if part == "intent" and field in ("repository", "unified_diff"):
                params = {
                    k: body["intent"][k]
                    for k in (
                        "item_id",
                        "repository",
                        "base_commit",
                        "title",
                        "rationale",
                        "unified_diff",
                    )
                }
                body["intent"]["canonical_intent_digest"] = sha(
                    canonical({"schema": "sprintctl-effect-intent/v1", **params}), False
                )
            if part == "intent" and field == "run_id":
                body["run"]["run_id"] = value
            writer.write(
                native_response(
                    canonical(invocation(json.loads(server.requests[-1][1]), body))
                )
            )
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            source = NativeBasisSource(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            with pytest.raises(ProtectedReadRefused):
                await source.read_basis(decode())

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind",
    [
        "work-revise",
        "description",
        "selected-release",
        "missing-release",
        "status-token",
    ],
)
def test_current_work_and_selected_release_own_revisions_remain_independent_data(
    certificates, kind
):
    async def run():
        value = basis()
        if kind == "work-revise":
            value["work"]["item_revision"] = (
                value["work"]["edit_revision"] + "@revise:999"
            )
        elif kind == "description":
            old = value["work"]["edit_revision"]
            value["work"]["edit_revision"] = (
                old.rsplit("@sha256:", 1)[0] + "@sha256:" + "0" * 64
            )
            value["work"]["item_revision"] = (
                value["work"]["edit_revision"] + "@revise:0"
            )
        elif kind == "selected-release":
            release = value["release"]
            release["item_revision"] = value["work"]["edit_revision"] + "@revise:999"
            release["release_digest"] = sha(
                canonical(
                    {
                        "aggregate_uuid": value["work"]["aggregate_uuid"],
                        "item_revision": release["item_revision"],
                        "acceptance_contract": release["acceptance_contract"],
                        "context_refs": release["context_refs"],
                    }
                ),
                False,
            )
        elif kind == "missing-release":
            value["release"] = None
        else:
            value["work"]["status_revision"] = "new observation token"

        async def emit(reader, writer):
            writer.write(
                native_response(
                    json.dumps(
                        invocation(json.loads(server.requests[-1][1]), value)
                    ).encode()
                )
            )
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            source = NativeBasisSource(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            returned = await source.read_basis(decode())
            assert json.loads(returned.work_utf8) == value["work"]
            assert (
                json.loads(returned.release_utf8) if returned.release_utf8 else None
            ) == value["release"]
            assert evaluate_currentness(decode(), returned).status == (
                "READY" if kind == "status-token" else "REFUSED"
            )

    asyncio.run(run())


def test_native_four_mib_raw_wrapper_boundary_and_two_mib_canonical_result(
    certificates,
):
    async def run():
        which = 0

        async def emit(reader, writer):
            nonlocal which
            body = invocation(json.loads(server.requests[-1][1]))
            if which in (0, 1):
                raw = canonical(body)
                raw += b" " * (4 * 1024 * 1024 - len(raw) + (1 if which == 1 else 0))
            else:
                value = body["result"]
                value["intent"]["unified_diff"] = ""
                needed = (
                    2 * 1024 * 1024 - len(canonical(value)) + (1 if which == 3 else 0)
                )
                value["intent"]["unified_diff"] = "☃" * (needed // 3) + "x" * (
                    needed % 3
                )
                params = {
                    k: value["intent"][k]
                    for k in (
                        "item_id",
                        "repository",
                        "base_commit",
                        "title",
                        "rationale",
                        "unified_diff",
                    )
                }
                value["intent"]["canonical_intent_digest"] = sha(
                    canonical({"schema": "sprintctl-effect-intent/v1", **params}), False
                )
                raw = canonical(body)
            which += 1
            writer.write(native_response(raw))
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            source = NativeBasisSource(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            assert (await source.read_basis(decode())).canonical_utf8 == canonical(
                basis()
            )
            with pytest.raises(ProtectedReadRefused):
                await source.read_basis(decode())
            # A valid changed proposal under the canonical cap still cannot replace
            # the frozen original proposal. The pure decoder's exact2MiB positive
            # is separately qualified by test_protected_currentness.
            for _ in range(2):
                with pytest.raises(ProtectedReadRefused):
                    await source.read_basis(decode())

    asyncio.run(run())


@pytest.mark.parametrize("state", ["proposed", "accepted", "rejected", "applied"])
def test_actual_native_owner_fixture_other_proposal_cannot_replace_copy(
    certificates, state
):
    root = os.environ.get("Q4_NATIVE_BASIS_FIXTURE_DIR")
    if root is None:
        pytest.skip(
            "requires explicit owner-generated native PostgreSQL basis fixtures"
        )
    actual = json.loads((Path(root) / (state + ".json")).read_bytes())
    assert actual["intent"]["state"] == state

    async def run():
        async def emit(reader, writer):
            writer.write(
                native_response(
                    json.dumps(
                        invocation(json.loads(server.requests[-1][1]), actual)
                    ).encode()
                )
            )
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            source = NativeBasisSource(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            with pytest.raises(ProtectedReadRefused):
                await source.read_basis(decode())

    asyncio.run(run())


def test_native_total_deadline_uses_shared_fixed_wire_and_aborts_real_peer(
    certificates, monkeypatch
):
    monkeypatch.setattr(wire_module, "_TOTAL_TIMEOUT_SECONDS", 0.20)

    async def run():
        closed = asyncio.Event()

        async def stalled(reader, writer):
            await reader.read()
            closed.set()

        async with Loopback(certificates, stalled) as server:
            source = NativeBasisSource(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            start = asyncio.get_running_loop().time()
            with pytest.raises(ProtectedReadRefused):
                await source.read_basis(decode())
            assert asyncio.get_running_loop().time() - start < 0.65
            await asyncio.wait_for(closed.wait(), 0.5)

    asyncio.run(run())


def test_exact_two_mib_matching_canonical_result_is_valid_native_data(certificates):
    initial = decode()
    delta = len(canonical(basis(initial))) - len(initial.canonical_utf8)
    effect = decode(large_canonical_copy(2 * 1024 * 1024 - delta))
    value = basis(effect)
    assert len(canonical(value)) == 2 * 1024 * 1024

    async def run():
        async def emit(reader, writer):
            raw = canonical(invocation(json.loads(server.requests[-1][1]), value))
            assert len(raw) < 4 * 1024 * 1024
            writer.write(native_response(raw))
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            source = NativeBasisSource(
                profile(server.port, certificates=certificates),
                Credentials(client_context(certificates)),
            )
            result = await source.read_basis(effect)
            assert len(result.canonical_utf8) == 2 * 1024 * 1024
            assert evaluate_currentness(effect, result).status == "READY"

    asyncio.run(run())


@pytest.mark.parametrize("field", ["unknown_contract_key", "effect_check_revisions"])
def test_depth32_json_tree_is_not_valid_under_existing_closed_native_contract(field):
    from vuoro_reconciler.protected_copy_codec import ProtectedDataRefused, _tree
    from vuoro_reconciler.protected_currentness import decode_current_basis

    value = basis()
    nested = "leaf"
    for _ in range(29):
        nested = {"n": nested}
    value["release"]["acceptance_contract"][field] = nested
    release = value["release"]
    release["release_digest"] = sha(
        canonical(
            {
                "aggregate_uuid": value["work"]["aggregate_uuid"],
                "item_revision": release["item_revision"],
                "acceptance_contract": release["acceptance_contract"],
                "context_refs": release["context_refs"],
            }
        ),
        False,
    )
    # The general depth32 budget admits this JSON. The actual closed contract
    # rejects unknown keys and requires map revisions to be digest strings.
    _tree(value)
    with pytest.raises(ProtectedDataRefused):
        decode_current_basis(canonical(value), effect=decode())

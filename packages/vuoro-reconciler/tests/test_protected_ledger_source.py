"""Actual private-delivery golden DATA joins, no detached signature attestation."""

import asyncio
import base64
from dataclasses import FrozenInstanceError, replace
import importlib.util
import json

import pytest

from vuoro_reconciler.protected_ledger_source import (
    ProtectedCopyRead,
    ProtectedLedgerSource,
)
from vuoro_reconciler.protected_read_transport import (
    PrivateReadTransport,
    ProtectedReadRefused,
)
from test_protected_copy_codec import FIXTURE, canonical, decode
from test_protected_read_transport import (
    ACCEPTANCE_ID,
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


def original_page():
    # Actual D1 PG18 list metadata from the same qualified private-route golden.
    return {
        "schema": "cred-broker-protected-effect-list-accepted-response/v1",
        "namespace": NAMESPACE,
        "items": [
            {
                "acceptance_id": "iac_9075de8f8248438388580b58fb770af8",
                "preparation_id": "ipr_955e2c9d5ae44c07818e5d9375745377",
                "receipt_id": "iar_cf5b60fa3a52492db914a258f2830c7c",
                "accepted_at": "2026-10-13T19:41:57.997632Z",
                "canonical_hash": "sha256:b50c8848be449ea187a717cbb2b3787f058222f6273250d16d2d14ca4b3bca07",
            }
        ],
        "next_cursor": None,
    }


def test_actual_tls_list_copy_golden_fresh_credentials_and_exact_wire(certificates):
    async def run():
        bodies = [
            canonical(original_page()),
            FIXTURE.read_bytes(),
            FIXTURE.read_bytes(),
        ]

        async def emit(reader, writer):
            writer.write(response(bodies.pop(0)))
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            chosen = profile(server.port, certificates=certificates)
            credentials = Credentials(client_context(certificates))
            source = ProtectedLedgerSource(
                chosen, PrivateReadTransport(chosen, credentials)
            )
            page = await source.list_accepted()
            result = await source.read_copy(ACCEPTANCE_ID, page.items[0])
            repeated = await source.read_copy(ACCEPTANCE_ID, page.items[0])
            assert type(result) is ProtectedCopyRead and result == repeated
            assert (
                result.effect == decode() and result.wire_utf8 == FIXTURE.read_bytes()
            )
            assert result.effect.canonical_utf8 == base64.b64decode(
                json.loads(FIXTURE.read_bytes())["canonical_utf8_b64"]
            )
            assert result.effect.receipt.receipt_id == page.items[0].receipt_id
            assert credentials.calls == [chosen, chosen, chosen]
            assert len(server.requests) == 3
            assert TOKEN not in repr(result) and "intent_" not in repr(result)
            with pytest.raises(FrozenInstanceError):
                result.wire_utf8 = b"forged"
            with pytest.raises(AttributeError):
                source.profile = chosen
            assert importlib.util.find_spec("cred_broker") is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind",
    [
        "receipt",
        "namespace",
        "repository",
        "acceptance",
        "copy-noncanonical",
        "copy-multiple-json",
        "list-namespace",
        "list-noncanonical",
        "list-type",
    ],
)
def test_actual_delivered_wire_independent_closed_copy_and_list_refusals(
    certificates, kind
):
    async def run():
        copy = json.loads(FIXTURE.read_bytes())
        page = original_page()
        chosen_repository = decode().repository
        requested_id = ACCEPTANCE_ID
        listed = None
        if kind == "receipt":
            copy["receipt"]["receipt_id"] = "iar_" + "e" * 32
            from vuoro_reconciler.protected_copy_codec import decode_accepted_page

            listed = decode_accepted_page(
                canonical(page),
                namespace_utf8=canonical(NAMESPACE),
                requested_limit=64,
                previous_cursor=None,
            ).items[0]
        elif kind == "namespace":
            copy["locator"]["namespace"]["repo_id"] = "foreign"
        elif kind == "repository":
            chosen_repository = "foreign-repository"
        elif kind == "acceptance":
            requested_id = "iac_" + "e" * 32
        elif kind == "list-namespace":
            page["namespace"] = {**NAMESPACE, "repo_id": "foreign"}
        elif kind == "list-type":
            page["items"][0]["accepted_at"] = 1
        raw = canonical(page if kind.startswith("list-") else copy)
        if kind.endswith("noncanonical"):
            raw = json.dumps(
                page if kind.startswith("list-") else copy, indent=2
            ).encode()
        elif kind == "copy-multiple-json":
            raw += b"{}"

        async def emit(reader, writer):
            writer.write(response(raw))
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            chosen = profile(
                server.port, certificates=certificates, repository=chosen_repository
            )
            credentials = Credentials(client_context(certificates))
            source = ProtectedLedgerSource(
                chosen, PrivateReadTransport(chosen, credentials)
            )
            with pytest.raises(
                ProtectedReadRefused, match="^protected-read-refused$"
            ) as error:
                if kind.startswith("list-"):
                    await source.list_accepted()
                else:
                    await source.read_copy(requested_id, listed)
            assert error.value.__cause__ is None and error.value.__context__ is None
            assert TOKEN not in str(error.value) and "foreign" not in str(error.value)
            assert len(credentials.calls) == 1

    asyncio.run(run())


def test_ledger_transport_profile_must_match_exactly_before_requests(certificates):
    chosen = profile(certificates=certificates)
    credentials = Credentials(client_context(certificates))
    transport = PrivateReadTransport(chosen, credentials)
    for other in (
        replace(chosen, repository="other"),
        replace(chosen, https_origin="https://localhost:9443"),
        replace(chosen, namespace_utf8=canonical({**NAMESPACE, "repo_id": "other"})),
    ):
        with pytest.raises(ProtectedReadRefused):
            ProtectedLedgerSource(other, transport)
    assert credentials.calls == []


def test_valid_cursor_is_scoped_position_data_and_wrong_namespace_preflight(
    certificates,
):
    body = {
        "schema": "cred-broker-protected-effect-list-cursor/v1",
        "namespace": NAMESPACE,
        "acceptance_id": ACCEPTANCE_ID,
        "accepted_at": "2026-10-13T19:41:57.997632Z",
    }
    cursor = base64.urlsafe_b64encode(canonical(body)).decode().rstrip("=")

    async def run():
        # Empty page beyond an actual valid position conveys no completeness.
        page = {**original_page(), "items": []}

        async def emit(reader, writer):
            writer.write(response(canonical(page)))
            await writer.drain()

        async with Loopback(certificates, emit) as server:
            chosen = profile(server.port, certificates=certificates)
            credentials = Credentials(client_context(certificates))
            source = ProtectedLedgerSource(
                chosen, PrivateReadTransport(chosen, credentials)
            )
            assert (await source.list_accepted(cursor)).items == ()
            assert json.loads(server.requests[0][1])["cursor"] == cursor
            foreign = {**body, "namespace": {**NAMESPACE, "repo_id": "foreign"}}
            bad = base64.urlsafe_b64encode(canonical(foreign)).decode().rstrip("=")
            with pytest.raises(ProtectedReadRefused):
                await source.list_accepted(bad)
            assert len(credentials.calls) == 1 and len(server.requests) == 1

    asyncio.run(run())

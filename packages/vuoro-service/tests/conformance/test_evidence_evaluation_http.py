"""Actual released evaluator through ordinary HTTP; loopback disposable PG only."""

from copy import deepcopy
from dataclasses import replace
import json
import os
import uuid

from fastapi.testclient import TestClient
import jsonschema
import pytest

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.identity import Identity, StaticBearerIdentityResolver
from .sprintctl_binding import SprintctlProvider
from .test_bound_proposal_http import assert_installed_composition

OP = "work.evidence.evaluate-v1"
AT = "2026-10-09T12:00:00Z"
pytestmark = pytest.mark.essential_safety


@pytest.fixture
def evaluation_owner(request):
    url = request.config.getoption("--lease-pg-url")
    if url is None:
        pytest.skip("requires configured disposable PostgreSQL owner")
    if os.environ.get("VUORO_BOUND_COMPOSITION_PROOF") == "1":
        assert_installed_composition()
    from importlib.metadata import version

    assert version("sprintctl") == "0.18.0"
    from sprintctl.vuoro_adapter import register_work_catalog, catalog_operation_specs

    enabled = {
        o["name"]: o for o in catalog_operation_specs(resource_schema_available=True)
    }
    disabled = {
        o["name"]: o for o in catalog_operation_specs(resource_schema_available=False)
    }
    assert enabled[OP] == disabled[OP]
    owner = SprintctlProvider(url)
    reader = Identity(
        actor="fixture-reader",
        environment="lease-conformance",
        authorities=frozenset({"work:read", "work:evidence"}),
        principal_id="github:100:0",
        workspace_id="lease-conformance",
        client_id="client-A",
        grant_id="grant-A",
        repo_ids=frozenset({owner.store.repo_id}),
    )
    identities = {
        "reader": reader,
        "writer": replace(reader, authorities=reader.authorities | {"work:write"}),
        "read-only": replace(reader, authorities=frozenset({"work:read"})),
        "evidence-only": replace(reader, authorities=frozenset({"work:evidence"})),
        "neither": replace(reader, authorities=frozenset()),
    }
    for field, value in [
        ("principal_id", "github:200:0"),
        ("workspace_id", "other-workspace"),
        ("client_id", "client-B"),
        ("grant_id", "grant-B"),
    ]:
        identities[field] = replace(reader, **{field: value})
    registry = CatalogRegistry()
    register_work_catalog(registry, owner.app)
    try:
        with TestClient(
            create_app(
                settings=ServiceSettings(environment_name="lease-conformance"),
                registry=registry,
                identity_resolver=StaticBearerIdentityResolver(identities),
            )
        ) as client:

            def invoke(op, arguments, *, token="reader", key=None, repo_id=None):
                envelope = {
                    "schema_version": "invocation/v1",
                    "request_id": uuid.uuid4().hex,
                    "repo_id": repo_id or owner.store.repo_id,
                    "catalog_revision": registry.revision,
                    "operation": op,
                    "arguments": arguments,
                }
                if key is not None:
                    envelope["idempotency_key"] = key
                return client.post(
                    "/api/invoke/v1",
                    headers={
                        "Authorization": "Bearer " + token,
                        "X-Vuoro-Client-Protocol": "1",
                    },
                    json=envelope,
                )

            item = int(owner.new_subject())
            revision = owner.pg.item_release_revision(owner.store, item)
            response = invoke(
                "work.run.register-v1",
                {
                    "harness_id": "conformance",
                    "harness_build": "test",
                    "model_id": "scripted",
                    "recipe_id": "evaluation-http/v1",
                    "observed_profile": {
                        "instruction_digest": "sha256:" + "a" * 64,
                        "skill_digests": [],
                    },
                    "idempotency_key": "run-0001",
                },
                token="writer",
            )
            assert response.status_code == 200, response.json()
            run = response.json()["result"]["run"]["run_id"]
            response = invoke(
                "work.reservation.reserve-v1",
                {
                    "item_id": item,
                    "actor": "fixture-reader",
                    "session_id": "evaluation-http",
                    "expected_revision": revision,
                },
                token="writer",
                key="reserve-1",
            )
            assert response.status_code == 200, response.json()
            reserve = response.json()["result"]["reservation"]
            args = {
                "run_id": run,
                "subject": "effect",
                "basis": {
                    "item_id": item,
                    "expected_revision": revision,
                    "release_digest": reserve["release_digest"],
                },
                "as_of": AT,
                "current_input_digests": {},
                "expected_tail": None,
            }
            yield owner, args, invoke
    finally:
        owner.close()


def state(owner):
    """Compare complete authoritative rows, not merely counts."""
    rows = {}
    for table in (
        "run",
        "work_item",
        "evidence_item",
        "work_release",
        "reservation",
        "work_effect_intent",
        "work_decision",
        "work_idempotency_ledger",
    ):
        values = owner.store.conn.execute(
            f"SELECT to_jsonb(t) AS row FROM {table} t WHERE repo_id=%s",
            (owner.store.repo_id,),
        ).fetchall()
        rows[table] = sorted(
            (v["row"] for v in values), key=lambda v: json.dumps(v, sort_keys=True)
        )
    owner.store.conn.commit()
    return rows


def result(response):
    assert response.status_code == 200, response.json()
    value = response.json()["result"]
    from sprintctl.vuoro_adapter import WORK_OPERATION_CONTRACTS

    contract = next(c for c in WORK_OPERATION_CONTRACTS if c.name == OP)
    jsonschema.validate(value, contract.result_schema)
    assert value["authority_coverage"] == "unsupported"
    assert value["authenticated_execution_facts"] == []
    assert value["effect_state"] == "unknown" and value["recommendation"] == "reconcile"
    assert value["authorizes_execution"] is False
    return value


def append(fixture, **changes):
    owner, args, invoke = fixture
    request = {
        "run_id": args["run_id"],
        "item_id": "evaluation-item-1",
        "kind": "test",
        "ref": "local:fixture",
        "digest": "sha256:" + "d" * 64,
        "collector": "fixture",
        "validity": {
            "basis": "indefinite",
            "valid_from": "2026-10-09T00:00:00Z",
            "valid_until": None,
            "component_digests": {},
        },
        "claims": [],
        "provenance": {},
        "chain_seq": 0,
        "chain_prev_digest": None,
        "idempotency_key": "evidence-1",
    }
    request.update(changes)
    response = invoke("work.evidence.append-v1", request)
    assert response.status_code == 200, response.json()
    tail = response.json()["result"]["item"]
    args["expected_tail"] = {
        "item_id": tail["item_id"],
        "chain_seq": tail["chain_seq"],
        "entry_digest": owner.pg.evidence_entry_digest(tail),
    }


def test_empty_and_populated_reader_binding_schema_and_no_writes(evaluation_owner):
    owner, args, invoke = evaluation_owner
    before = state(owner)
    value = result(invoke(OP, args))
    resolved = invoke("work.run.resolve-v1", {"run_id": args["run_id"]})
    assert resolved.status_code == 200, resolved.json()
    assert value["run_binding"] == resolved.json()["result"]
    assert value["basis_status"] == "current" and value["evidence_validity"] == []
    assert state(owner) == before
    append(evaluation_owner)
    before = state(owner)
    first = result(invoke(OP, args))
    second = result(invoke(OP, args))
    first.pop("observed_at")
    second.pop("observed_at")
    assert first == second and first["evidence_validity"][0]["status"] == "valid"
    assert state(owner) == before


@pytest.mark.parametrize("token", ["read-only", "evidence-only", "neither"])
def test_two_scope_conjunction_required_without_write_authority(
    evaluation_owner, token
):
    owner, args, invoke = evaluation_owner
    before = state(owner)
    response = invoke(OP, args, token=token)
    assert response.status_code == 403, response.json()
    assert response.json()["error"]["code"] == "authority-required"
    assert state(owner) == before


@pytest.mark.parametrize(
    "token", ["principal_id", "workspace_id", "client_id", "grant_id"]
)
def test_full_binding_refused_without_disclosure(evaluation_owner, token):
    owner, args, invoke = evaluation_owner
    append(evaluation_owner)
    before = state(owner)
    response = invoke(OP, args, token=token)
    assert response.status_code == 404, response.json()
    assert (
        response.json()["error"]["code"] == "run-not-found"
        and response.json().get("result") is None
    )
    assert state(owner) == before


@pytest.mark.parametrize(
    "field", ["identity", "trusted", "authenticated_execution_facts"]
)
def test_extra_arguments_refused(evaluation_owner, field):
    owner, args, invoke = evaluation_owner
    before = state(owner)
    response = invoke(OP, {**args, field: {}})
    assert response.status_code == 422, response.json()
    assert response.json()["error"]["code"] == "schema-validation-failed"
    assert state(owner) == before


def test_envelope_key_and_foreign_repository_refused(evaluation_owner):
    owner, args, invoke = evaluation_owner
    before = state(owner)
    response = invoke(OP, args, key="read-key-1")
    assert (
        response.status_code == 400
        and response.json()["error"]["code"] == "idempotency-key-not-allowed"
    )
    response = invoke(OP, args, repo_id="foreign-repository")
    assert (
        response.status_code == 403
        and response.json()["error"]["code"] == "repo-unauthorized"
    )
    assert state(owner) == before


def test_exact_tail_conflict_then_stale_full_release_basis(evaluation_owner):
    owner, args, invoke = evaluation_owner
    append(evaluation_owner)
    before = state(owner)
    changed = deepcopy(args)
    changed["expected_tail"] = None
    response = invoke(OP, changed)
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "evidence-tail-mismatch"
    )
    assert state(owner) == before
    owner.pg.update_work_item_description(
        owner.store, args["basis"]["item_id"], "changed independently"
    )
    before = state(owner)
    value = result(invoke(OP, args))
    assert value["basis_status"] == "stale"
    args["basis"]["expected_revision"] = value["current_basis"]["expected_revision"]
    assert result(invoke(OP, args))["basis_status"] == "stale"
    assert state(owner) == before


@pytest.mark.parametrize(
    "case,status",
    [
        ("expired", "expired"),
        ("missing-input", "unknown"),
        ("invalid-legacy", "invalid"),
    ],
)
def test_distinct_validity_through_http(evaluation_owner, case, status):
    owner, args, invoke = evaluation_owner
    if case == "expired":
        append(
            evaluation_owner,
            validity={
                "basis": "bounded",
                "valid_from": "2026-10-08T00:00:00Z",
                "valid_until": "2026-10-08T01:00:00Z",
                "component_digests": {},
            },
        )
    elif case == "missing-input":
        append(
            evaluation_owner,
            validity={
                "basis": "until_inputs_change",
                "valid_from": "2026-10-08T00:00:00Z",
                "valid_until": None,
                "component_digests": {"tree": "sha256:" + "e" * 64},
            },
        )
    else:
        append(evaluation_owner)
        owner.store.conn.execute(
            "UPDATE evidence_item SET validity=%s::jsonb WHERE repo_id=%s AND run_id=%s",
            ('{"basis":"unsupported-legacy"}', owner.store.repo_id, args["run_id"]),
        )
        owner.store.conn.commit()
    before = state(owner)
    assert result(invoke(OP, args))["evidence_validity"][0]["status"] == status
    assert state(owner) == before


def test_forged_claims_remain_authored_and_readonly(evaluation_owner):
    owner, args, invoke = evaluation_owner
    append(
        evaluation_owner,
        claims=[
            {
                "claim_type": name,
                "subject": "effect",
                "grant_id": "claimed-grant",
                "freshness": None,
                "confirms": True,
                "detail": {
                    "trusted": True,
                    "grant_used": True,
                    "non_invocation_proven": True,
                },
            }
            for name in ("effect_completed", "observation", "effect_not_invoked")
        ],
        provenance={"authority": "owner"},
    )
    before = state(owner)
    value = result(invoke(OP, args))
    assert len(value["authored_assertions"]) == 3
    assert all(
        a["authority"] == "authored-assertion" for a in value["authored_assertions"]
    )
    assert state(owner) == before

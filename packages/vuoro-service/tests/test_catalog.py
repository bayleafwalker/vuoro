from __future__ import annotations

import asyncio
import hashlib

import pytest
from pydantic import ValidationError

from vuoro_service.catalog import CatalogRegistrationError, CatalogRegistry
from vuoro_service.contracts import (
    BoundedLongPollCapability,
    OperationDefinition,
    ResourceKindDefinition,
    ResourceObservationContract,
    ResourceResultContract,
)
from vuoro_service.identity import Identity, InvocationContext


OBJECT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
}


def operation(name: str) -> OperationDefinition:
    return OperationDefinition(
        name=name,
        owning_domain=name.split(".", 1)[0],
        input_schema=OBJECT_SCHEMA,
        result_schema=OBJECT_SCHEMA,
        execution_semantics="read",
        idempotency="not-allowed",
    )


def disclosed_operation(name: str) -> OperationDefinition:
    return OperationDefinition(
        **operation(name).model_dump(),
        failure_disclosure="resource-not-found/v1",
    )


def test_failure_disclosure_registration_is_immutable_and_observation_only() -> None:
    registry = CatalogRegistry()
    guard = lambda resource_ref, context: False
    for name in ("execution.session.get", "execution.session.changes"):
        registry.register(
            disclosed_operation(name), lambda arguments, context: arguments,
            visibility_guard=guard,
            visibility_reference_pattern=r"^exr1_[A-Za-z0-9_-]{43}$",
        )
    with pytest.raises(CatalogRegistrationError, match="only on registered"):
        _ = registry.revision
    registry.register_resource_kind(
        ResourceKindDefinition(
            resource_kind="execution.session",
            observation=ResourceObservationContract(
                snapshot_operation="execution.session.get",
                changes_operation="execution.session.changes",
                cursor_schema="execution-event-cursor/v1",
                supports_terminality=True,
            ),
        )
    )
    dumped = registry.catalog().model_dump(mode="json")
    assert [operation["failure_disclosure"] for operation in dumped["operations"]] == [
        "resource-not-found/v1", "resource-not-found/v1"
    ]


def test_failure_disclosure_requires_guard_read_semantics_and_grammar() -> None:
    registry = CatalogRegistry()
    with pytest.raises(CatalogRegistrationError, match="requires exactly one"):
        registry.register(disclosed_operation("execution.session.get"), lambda arguments, context: arguments)
    with pytest.raises(CatalogRegistrationError, match="requires exactly one"):
        registry.register(operation("execution.session.get"), lambda arguments, context: arguments, visibility_guard=lambda ref, context: False, visibility_reference_pattern="^x$")
    with pytest.raises(CatalogRegistrationError, match="requires exactly one"):
        registry.register(operation("execution.session.get"), lambda arguments, context: arguments, visibility_reference_pattern="^x$")
    write = disclosed_operation("execution.session.write").model_copy(update={"execution_semantics": "write"})
    with pytest.raises(CatalogRegistrationError, match="requires a read operation"):
        registry.register(write, lambda arguments, context: arguments, visibility_guard=lambda ref, context: False, visibility_reference_pattern="^x$")
    with pytest.raises(CatalogRegistrationError, match="invalid opaque-reference"):
        registry.register(disclosed_operation("execution.session.changes"), lambda arguments, context: arguments, visibility_guard=lambda ref, context: False, visibility_reference_pattern="[")


def test_resource_visibility_can_bind_an_older_owner_adapter_atomically() -> None:
    registry = CatalogRegistry()
    for name in ("work.maintenance.get", "work.maintenance.changes"):
        registry.register(operation(name), lambda arguments, context: arguments)
    registry.register_resource_kind(
        ResourceKindDefinition(
            resource_kind="work.maintenance-capability",
            observation=ResourceObservationContract(
                snapshot_operation="work.maintenance.get",
                changes_operation="work.maintenance.changes",
                cursor_schema="maintenance-cursor/v1",
                supports_terminality=True,
            ),
        )
    )

    assert registry.has_resource_kind("work.maintenance-capability")
    assert not registry.has_resource_kind("work.absent")
    registry.register_resource_visibility(
        "work.maintenance-capability",
        lambda resource_ref, context: resource_ref.endswith("A"),
        visibility_reference_pattern=r"^smr1_[A-Za-z0-9_-]{43}$",
    )

    catalog = registry.catalog().model_dump(mode="json")
    observed = {
        item["name"]: item["failure_disclosure"]
        for item in catalog["operations"]
    }
    assert observed == {
        "work.maintenance.changes": "resource-not-found/v1",
        "work.maintenance.get": "resource-not-found/v1",
    }
    with pytest.raises(CatalogRegistrationError, match="already bound"):
        registry.register_resource_visibility(
            "work.maintenance-capability",
            lambda resource_ref, context: True,
            visibility_reference_pattern=r"^smr1_",
        )
    with pytest.raises(CatalogRegistrationError, match="unregistered resource kind"):
        registry.register_resource_visibility(
            "work.absent", lambda resource_ref, context: True,
            visibility_reference_pattern=r"^smr1_",
        )


def test_revision_is_deterministic_and_catalog_is_sorted() -> None:
    first = CatalogRegistry()
    second = CatalogRegistry()
    for registry, names in (
        (first, ["work.pilot.zeta", "audit.observation.alpha"]),
        (second, ["audit.observation.alpha", "work.pilot.zeta"]),
    ):
        for name in names:
            registry.register(operation(name), lambda arguments, context: arguments)

    assert first.revision == second.revision
    assert [value.name for value in first.catalog().operations] == [
        "audit.observation.alpha",
        "work.pilot.zeta",
    ]


def test_owner_decoder_runs_at_composition_boundary_before_result_validation() -> None:
    registry = CatalogRegistry()
    values = operation("execution.action.get").model_dump()
    values["result_schema"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["schema_version", "reference", "cursor"],
        "properties": {
            "schema_version": {"const": "resource-snapshot/v1"},
            "reference": {"type": "string"},
            "cursor": {"type": "string"},
        },
    }
    definition = OperationDefinition(
        **values,
    )
    registry.register(
        definition,
        lambda _arguments, _context: {"owner_ref": "opaque", "sequence": 7},
        result_decoder=lambda value: {
            "schema_version": "resource-snapshot/v1",
            "reference": value["owner_ref"],
            "cursor": f"owner-cursor:{value['sequence']}",
        },
    )
    registered = registry.get(definition.name)
    assert registered is not None
    context = InvocationContext(
        identity=Identity(actor="test", environment="test"),
        request_id="request-1",
        basis_revision=None,
        catalog_revision=registry.revision,
        idempotency_requirement="not-allowed",
        idempotency_key=None,
    )
    assert asyncio.run(registry.invoke(registered, {}, context)) == {
        "schema_version": "resource-snapshot/v1",
        "reference": "opaque",
        "cursor": "owner-cursor:7",
    }


def test_two_registered_owner_decoders_remain_isolated_under_parallel_invocation() -> None:
    registry = CatalogRegistry()
    calls: dict[str, list[str]] = {"work": [], "execution": []}

    for domain in calls:
        definition = operation(f"{domain}.resource.get")

        def decode(value, *, owner=domain):
            calls[owner].append(value["owner"])
            return {}

        registry.register(
            definition,
            lambda arguments, context, owner=domain: {"owner": owner},
            result_decoder=decode,
        )

    context = InvocationContext(
        identity=Identity(actor="test", environment="test"),
        request_id="parallel",
        basis_revision=None,
        catalog_revision=registry.revision,
        idempotency_requirement="not-allowed",
        idempotency_key=None,
    )

    async def invoke_both():
        registered = [
            registry.get("work.resource.get"),
            registry.get("execution.resource.get"),
        ]
        assert all(item is not None for item in registered)
        return await asyncio.gather(*(
            registry.invoke(item, {}, context) for item in registered if item is not None
        ))

    assert asyncio.run(invoke_both()) == [{}, {}]
    assert calls == {"work": ["work"], "execution": ["execution"]}


def test_legacy_catalog_bytes_and_revision_are_unchanged_without_metadata() -> None:
    registry = CatalogRegistry()
    registry.register(
        operation("work.pilot.inspect"), lambda arguments, context: arguments
    )

    operation_bytes = (
        b'[{"deprecation":{"deprecated":false,"replacement":null,"sunset_at":null},'
        b'"execution_semantics":"read","idempotency":"not-allowed",'
        b'"input_schema":{"$schema":"https://json-schema.org/draft/2020-12/schema",'
        b'"additionalProperties":false,"type":"object"},"name":"work.pilot.inspect",'
        b'"owning_domain":"work","repo_scoped":false,"required_authority":null,'
        b'"required_client_schema_features":["json-schema-draft-2020-12"],'
        b'"result_schema":{"$schema":"https://json-schema.org/draft/2020-12/schema",'
        b'"additionalProperties":false,"type":"object"}}]'
    )
    assert registry.revision == hashlib.sha256(operation_bytes).hexdigest()
    dumped = registry.catalog().model_dump(mode="json")
    assert set(dumped) == {"schema_version", "revision", "operations"}
    assert "result_contract" not in dumped["operations"][0]


def test_resource_metadata_is_sorted_immutable_and_revision_bound() -> None:
    registry = CatalogRegistry()
    for name in ("execution.session.get", "execution.session.changes"):
        registry.register(operation(name), lambda arguments, context: arguments)
    resource_kind = ResourceKindDefinition(
        resource_kind="execution.session",
        observation=ResourceObservationContract(
            snapshot_operation="execution.session.get",
            changes_operation="execution.session.changes",
            cursor_schema="execution-event-cursor/v1",
            supports_terminality=True,
        ),
    )
    revision_without_metadata = registry.revision
    registry.register_resource_kind(resource_kind)
    registry.register_observation_transport(
        BoundedLongPollCapability(maximum_wait_seconds=30)
    )
    registry.register(
        OperationDefinition(
            **operation("execution.dispatch.enqueue").model_dump(),
            result_contract=ResourceResultContract(resource_kind="execution.session"),
        ),
        lambda arguments, context: arguments,
    )

    dumped = registry.catalog().model_dump(mode="json")
    assert registry.revision != revision_without_metadata
    assert dumped["resource_kinds"] == [resource_kind.model_dump(mode="json")]
    assert dumped["observation_transports"] == [
        {"transport": "bounded-long-poll", "maximum_wait_seconds": 30}
    ]
    assert dumped["operations"][0]["result_contract"] == {
        "mode": "resource-reference",
        "resource_kind": "execution.session",
    }
    with pytest.raises(ValidationError, match="frozen"):
        resource_kind.resource_kind = "execution.other"  # type: ignore[misc]


def test_resource_metadata_registration_invariants_fail_closed() -> None:
    registry = CatalogRegistry()
    registry.register(
        operation("execution.session.get"), lambda arguments, context: arguments
    )
    definition = ResourceKindDefinition(
        resource_kind="execution.session",
        observation=ResourceObservationContract(
            snapshot_operation="execution.session.get",
            changes_operation="execution.session.changes",
            cursor_schema="execution-event-cursor/v1",
            supports_terminality=True,
        ),
    )
    with pytest.raises(CatalogRegistrationError, match="unregistered operation"):
        registry.register_resource_kind(definition)

    with pytest.raises(CatalogRegistrationError, match="unregistered resource kind"):
        registry.register(
            OperationDefinition(
                **operation("execution.dispatch.enqueue").model_dump(),
                result_contract=ResourceResultContract(
                    resource_kind="execution.session"
                ),
            ),
            lambda arguments, context: arguments,
        )

    with pytest.raises(ValidationError, match="less than or equal to 300"):
        BoundedLongPollCapability(maximum_wait_seconds=301)


def test_registered_operation_is_isolated_from_caller_mutation() -> None:
    registry = CatalogRegistry()
    definition = operation("execution.session.get")
    registry.register(definition, lambda arguments, context: arguments)
    revision = registry.revision

    definition.owning_domain = "work"
    definition.execution_semantics = "admin"
    definition.input_schema["additionalProperties"] = True

    registered = registry.get("execution.session.get")
    assert registered is not None
    assert registered.definition.owning_domain == "execution"
    assert registered.definition.execution_semantics == "read"
    assert registered.definition.input_schema["additionalProperties"] is False
    assert registry.revision == revision

    registered.definition.owning_domain = "work"
    registry.catalog().operations[0].execution_semantics = "admin"
    reread = registry.get("execution.session.get")
    assert reread is not None
    assert reread.definition.owning_domain == "execution"
    assert registry.catalog().operations[0].execution_semantics == "read"
    assert registry.revision == revision


def test_result_contract_cannot_cross_owner_domains() -> None:
    registry = CatalogRegistry()
    for name in ("execution.session.get", "execution.session.changes"):
        registry.register(operation(name), lambda arguments, context: arguments)
    registry.register_resource_kind(
        ResourceKindDefinition(
            resource_kind="execution.session",
            observation=ResourceObservationContract(
                snapshot_operation="execution.session.get",
                changes_operation="execution.session.changes",
                cursor_schema="execution-event-cursor/v1",
                supports_terminality=True,
            ),
        )
    )

    with pytest.raises(CatalogRegistrationError, match="must be owned by work"):
        registry.register(
            OperationDefinition(
                **operation("work.dispatch.enqueue").model_dump(),
                result_contract=ResourceResultContract(
                    resource_kind="execution.session"
                ),
            ),
            lambda arguments, context: arguments,
        )


def test_duplicate_names_are_rejected() -> None:
    registry = CatalogRegistry()
    registry.register(
        operation("work.pilot.inspect"), lambda arguments, context: arguments
    )
    with pytest.raises(CatalogRegistrationError, match="duplicate operation name"):
        registry.register(
            operation("work.pilot.inspect"), lambda arguments, context: arguments
        )


def test_external_and_dynamic_schema_references_are_rejected() -> None:
    registry = CatalogRegistry()
    for reference_key, reference in (
        ("$ref", "https://attacker.invalid/schema.json"),
        ("$dynamicRef", "#node"),
    ):
        definition = operation(f"work.schema.{reference_key[1:].lower()}").model_copy(
            update={
                "input_schema": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    reference_key: reference,
                }
            }
        )
        with pytest.raises(
            CatalogRegistrationError, match="references are not supported|only local"
        ):
            registry.register(definition, lambda arguments, context: arguments)


def test_local_defs_reference_is_accepted() -> None:
    registry = CatalogRegistry()
    definition = operation("work.schema.local-ref").model_copy(
        update={
            "input_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$defs": {"identifier": {"type": "string"}},
                "$ref": "#/$defs/identifier",
            },
            "required_client_schema_features": [
                "json-schema-draft-2020-12",
                "local-defs-ref",
            ],
        }
    )
    registry.register(definition, lambda arguments, context: arguments)


def test_missing_local_reference_target_is_rejected_during_registration() -> None:
    registry = CatalogRegistry()
    definition = operation("work.schema.missing-ref").model_copy(
        update={
            "input_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$ref": "#/$defs/missing",
            }
        }
    )
    with pytest.raises(CatalogRegistrationError, match="target does not exist"):
        registry.register(definition, lambda arguments, context: arguments)


@pytest.mark.parametrize(
    "dialect",
    [None, "http://json-schema.org/draft-07/schema#"],
)
def test_schema_dialect_must_be_explicitly_2020_12(dialect: str | None) -> None:
    registry = CatalogRegistry()
    schema = dict(OBJECT_SCHEMA)
    if dialect is None:
        schema.pop("$schema")
    else:
        schema["$schema"] = dialect
    definition = operation("work.schema.wrong-dialect").model_copy(
        update={"input_schema": schema}
    )
    with pytest.raises(CatalogRegistrationError, match=r"\$schema must be"):
        registry.register(definition, lambda arguments, context: arguments)


def test_schema_features_cannot_be_omitted_from_catalog_metadata() -> None:
    registry = CatalogRegistry(
        schema_features=CatalogRegistry().schema_features
        | frozenset({"unevaluated-properties"})
    )
    definition = operation("work.schema.undeclared-feature").model_copy(
        update={
            "input_schema": {
                **OBJECT_SCHEMA,
                "unevaluatedProperties": False,
            }
        }
    )
    with pytest.raises(CatalogRegistrationError, match="undeclared client features"):
        registry.register(definition, lambda arguments, context: arguments)

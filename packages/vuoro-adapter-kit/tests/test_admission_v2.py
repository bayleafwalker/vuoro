from dataclasses import FrozenInstanceError, replace
import ast
import json
from pathlib import Path

import pytest

from vuoro_adapter_kit import (
    ARGUMENT_BYTES_LIMIT, CATALOG_REQUIRED_PROFILE, AdmissionIdentity,
    AdmissionResponse, OwnerAdmissionContext, OwnerAdmissionRequest,
    PrincipalIdentity, TrustedIngressProvenance, object_schema,
    operation_spec, operation_spec_v2,
)


def identity():
    return AdmissionIdentity(
        PrincipalIdentity("https://issuer.example:443", "opaque-subject", 7),
        "environment", frozenset({"work.resource.read"}), frozenset({"repo"}),
    )


def context(**overrides):
    return replace(OwnerAdmissionContext(identity(), "request", "repo", "a" * 64, "key"), **overrides)


def spec(**overrides):
    fields = dict(
        admission_profile=CATALOG_REQUIRED_PROFILE, legacy_availability="strict-only",
        required_authorities=["work.resource.relate", "work.resource.read"],
        input_schema=object_schema({"kind": {"enum": ["parent-of", "depends-on"]}}, required=("kind",)),
        result_schema=object_schema({}), execution_semantics="write", idempotency="required",
        repo_scoped=True, owner_admission_contract="resource-command-admission/v1",
    )
    fields.update(overrides)
    return operation_spec_v2("work.resource.relate-v1", **fields)


def test_v1_published_spec_serialization_is_unchanged():
    original = operation_spec(
        "work.read.items", input_schema=object_schema({}), result_schema=object_schema({}),
        required_authority="work:read", execution_semantics="read", idempotency="not-allowed",
    )
    # Literal legacy bytes, not derived from v2 or a second copy of its builder.
    assert json.dumps(original, sort_keys=True, separators=(",", ":")).encode() == (
        b'{"deprecation":{"deprecated":false,"replacement":null,"sunset_at":null},'
        b'"execution_semantics":"read","idempotency":"not-allowed",'
        b'"input_schema":{"$schema":"https://json-schema.org/draft/2020-12/schema",'
        b'"additionalProperties":false,"properties":{},"type":"object"},'
        b'"name":"work.read.items","owning_domain":"work","repo_scoped":false,'
        b'"required_authority":"work:read",'
        b'"required_client_schema_features":["json-schema-draft-2020-12"],'
        b'"result_schema":{"$schema":"https://json-schema.org/draft/2020-12/schema",'
        b'"additionalProperties":false,"properties":{},"type":"object"}}'
    )


def test_v2_authority_set_is_deterministic_and_inner_array_order_is_preserved():
    first = spec()
    second = spec(required_authorities=["work.resource.read", "work.resource.relate"])
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["required_authorities"] == ["work.resource.read", "work.resource.relate"]
    assert first["input_schema"]["properties"]["kind"]["enum"] == ["parent-of", "depends-on"]
    assert "required_authority" not in first
    assert first["schema_version"] == "operation-definition/v2"
    assert first["owner_admission_contract"] == "resource-command-admission/v1"


def test_v2_deep_copies_owner_inputs():
    schema = object_schema({"kind": {"enum": ["parent-of", "depends-on"]}})
    authorities = ["work.resource.read", "work.resource.relate"]
    value = spec(input_schema=schema, required_authorities=authorities)
    schema["properties"]["kind"]["enum"].reverse()
    authorities.clear()
    assert value["input_schema"]["properties"]["kind"]["enum"] == ["parent-of", "depends-on"]
    assert value["required_authorities"] == ["work.resource.read", "work.resource.relate"]


@pytest.mark.parametrize("overrides", [
    {"admission_profile": "catalog-required/v1"},
    {"legacy_availability": "both"},
    {"required_authorities": "work.resource.read"},
    {"required_authorities": ["work.resource.read", "work.resource.read"]},
    {"required_authorities": ["work.resource.*"]},
    {"required_authorities": ["work.resource.read "]},
    {"required_authorities": []},
    {"owner_admission_contract": "unversioned"},
    {"legacy_availability": "released-legacy"},
    {"repo_scoped": False},
    {"idempotency": "optional"},
    {"execution_semantics": "read"},
])
def test_invalid_recorder_metadata_is_rejected(overrides):
    with pytest.raises((ValueError, TypeError)):
        spec(**overrides)


def test_existing_read_can_be_declared_for_strict_view_without_recorder():
    value = spec(
        owner_admission_contract=None, legacy_availability="released-legacy",
        execution_semantics="read", idempotency="not-allowed", required_authorities=[],
    )
    assert "owner_admission_contract" not in value
    assert value["legacy_availability"] == "released-legacy"


@pytest.mark.parametrize("principal", [
    ("https://issuer", "actor:display", 0),
    (" https://issuer", "subject", 0),
    ("https://issuer", "subject", True),
    ("https://issuer", "subject", -1),
    ("https://issuer", "subject", "0"),
])
def test_principal_values_reject_substitution_shapes(principal):
    with pytest.raises((ValueError, TypeError)):
        PrincipalIdentity(*principal)


def test_principal_issuer_and_real_epoch_are_preserved_exactly():
    principal = identity().principal
    assert principal.principal_id == "https://issuer.example:443:opaque-subject:7"
    assert principal.issuer == "https://issuer.example:443"
    assert PrincipalIdentity(principal.issuer, principal.subject, 0).epoch == 0
    assert not hasattr(principal, "actor")


@pytest.mark.parametrize("overrides", [
    {"authorities": {"work.resource.read"}},
    {"repository_ids": ["repo"]},
    {"authorities": frozenset({"*"})},
    {"repository_ids": frozenset({"*"})},
    {"principal": "actor:subject:0"},
])
def test_mutable_or_ambiguous_identity_contents_rejected(overrides):
    with pytest.raises((ValueError, TypeError)):
        replace(identity(), **overrides)


def test_frozen_graph_of_values_and_exact_bytes():
    raw = b'{ "amount": 1.0, "duplicate":1,"duplicate":2 }'
    request = OwnerAdmissionRequest("work.resource.create-v1", raw, context())
    response = AdmissionResponse(409, b'{"original":"\\u00e9"}\n')
    assert request.argument_bytes is raw  # no JSON parsing/normalization before owner
    assert response.body_bytes == b'{"original":"\\u00e9"}\n'
    for instance, field, value in [(request, "argument_bytes", b"{}"), (request.context, "repo_id", "other"),
                                    (request.context.identity, "environment", "other"),
                                    (request.context.identity.principal, "epoch", 8),
                                    (response, "body_bytes", b"{}")]:
        with pytest.raises(FrozenInstanceError):
            setattr(instance, field, value)


@pytest.mark.parametrize("raw", [bytearray(b"{}"), memoryview(b"{}"), b"", b" " * (ARGUMENT_BYTES_LIMIT + 1)])
def test_request_rejects_mutable_or_overbound_argument_bytes(raw):
    with pytest.raises((ValueError, TypeError)):
        OwnerAdmissionRequest("work.resource.create-v1", raw, context())


def test_exact_argument_bound_is_permitted_without_parsing():
    OwnerAdmissionRequest("work.resource.create-v1", b" " * ARGUMENT_BYTES_LIMIT, context())


@pytest.mark.parametrize("overrides", [
    {"catalog_revision": "A" * 64}, {"catalog_revision": "a" * 63},
    {"admission_profile": "legacy/v1"}, {"trusted_ingress": True},
    {"trusted_ingress": "reviewer"}, {"trusted_ingress": {"trusted": True}},
])
def test_context_refuses_legacy_or_wire_trust_shapes(overrides):
    with pytest.raises((ValueError, TypeError)):
        context(**overrides)


def test_trusted_provenance_is_closed_and_binds_exact_end_principal():
    original = identity().principal
    provenance = TrustedIngressProvenance(original, "https://review-issuer", "review-audience")
    assert context(trusted_ingress=provenance).trusted_ingress is provenance
    with pytest.raises(ValueError):
        replace(provenance, profile="arbitrary-policy")
    with pytest.raises(ValueError):
        context(trusted_ingress=replace(provenance, principal=replace(original, epoch=8)))


@pytest.mark.parametrize("status, body", [(True, b"{}"), (199, b"{}"), (600, b"{}"), (200, bytearray(b"{}"))])
def test_response_rejects_nonfinal_status_and_mutable_bytes(status, body):
    with pytest.raises((TypeError, ValueError)):
        AdmissionResponse(status, body)


def test_new_pure_modules_have_no_service_or_domain_dependency():
    root = Path(__file__).parents[1] / "src" / "vuoro_adapter_kit"
    for name in ("admission.py", "catalog_v2.py"):
        tree = ast.parse((root / name).read_text())
        imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imports |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        assert not any(value.startswith(("vuoro_service", "sprintctl", "psycopg", "pydantic", "httpx")) for value in imports)


def test_envelope_text_is_not_normalized_or_domain_validated_by_kit():
    value = context(request_id=" request id ", idempotency_key=" key with spaces ")
    assert value.request_id == " request id "
    assert value.idempotency_key == " key with spaces "
    with pytest.raises(ValueError):
        context(idempotency_key="x" * 257)


def test_immutable_primitive_subclasses_cannot_smuggle_mutable_contents():
    class MutableSetWrapper(frozenset):
        pass
    class MutableBytesWrapper(bytes):
        pass
    wrapped = MutableSetWrapper({"work.resource.read"})
    wrapped.state = []
    with pytest.raises(TypeError):
        replace(identity(), authorities=wrapped)
    with pytest.raises(TypeError):
        AdmissionResponse(200, MutableBytesWrapper(b"{}"))


@pytest.mark.parametrize("semantics", ["write", "enqueue", "admin"])
def test_mutation_without_recorder_still_requires_an_explicit_authority(semantics):
    with pytest.raises(ValueError, match="strict mutations require explicit authorities"):
        spec(owner_admission_contract=None, execution_semantics=semantics, required_authorities=[])


@pytest.mark.parametrize("semantics", ["write", "enqueue", "admin"])
def test_recorder_allows_each_authorized_mutation_semantics(semantics):
    assert spec(execution_semantics=semantics)["execution_semantics"] == semantics


def test_string_subclasses_cannot_smuggle_mutable_dispatch_or_profile_metadata():
    class MutableString(str):
        pass
    for field, value in (("catalog_revision", "a" * 64), ("admission_profile", CATALOG_REQUIRED_PROFILE)):
        with pytest.raises(ValueError):
            context(**{field: MutableString(value)})
    with pytest.raises(ValueError, match="operation"):
        OwnerAdmissionRequest(MutableString("work.resource.create-v1"), b"{}", context())
    with pytest.raises(ValueError, match="ingress profile"):
        TrustedIngressProvenance(identity().principal, "issuer", "audience", MutableString("resource-review-ingress/v1"))
    for field, value in (("admission_profile", CATALOG_REQUIRED_PROFILE), ("legacy_availability", "strict-only"),
                         ("owner_admission_contract", "resource-command-admission/v1")):
        with pytest.raises(ValueError):
            spec(**{field: MutableString(value)})
    with pytest.raises(ValueError, match="authorities"):
        spec(required_authorities=[MutableString("work.resource.read")])


@pytest.mark.parametrize("authority", ["work..read", "work.read.", "work.read-", "*"])
def test_authority_names_have_unambiguous_nonempty_segments(authority):
    with pytest.raises(ValueError, match="authorities"):
        replace(identity(), authorities=frozenset({authority}))


@pytest.mark.parametrize("field", ["issuer", "subject"])
def test_trusted_ingress_refuses_other_issuer_or_subject(field):
    principal = replace(identity().principal, **{field: "other"})
    with pytest.raises(ValueError, match="same end principal"):
        context(trusted_ingress=TrustedIngressProvenance(principal, "issuer", "audience"))


@pytest.mark.parametrize("overrides", [{"request_id": ""}, {"repo_id": ""}, {"repo_id": "r" * 257}])
def test_context_preserves_frozen_character_bounds(overrides):
    with pytest.raises(ValueError, match="1..256"):
        context(**overrides)


def test_invalid_operation_and_nonstring_contract_refuse_for_specific_reason():
    with pytest.raises(ValueError, match="operation"):
        OwnerAdmissionRequest("work-only", b"{}", context())
    with pytest.raises(ValueError, match="versioned identifier"):
        spec(owner_admission_contract=1)


def test_published_audit_receipt_lookup_root_oneof_is_preserved_only_in_v2():
    # Exact released auditctl 0.1.9 audit.receipt.lookup input schema; independent
    # installed-owner gate compares the entire real catalog as well.
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
              "properties": {"receipt_id": {"type": "string", "format": "uuid"},
                             "event_id": {"type": "string", "pattern": "^ad:[0-9A-HJKMNP-TV-Z]{26}$"}},
              "additionalProperties": False,
              "oneOf": [{"required": ["receipt_id"]}, {"required": ["event_id"]}]}
    value = spec(input_schema=schema)
    assert value["input_schema"] == schema
    schema["oneOf"][0]["required"].append("later")
    schema["oneOf"].reverse()
    assert value["input_schema"]["oneOf"] == [{"required": ["receipt_id"]}, {"required": ["event_id"]}]
    with pytest.raises(ValueError, match="unsupported root fields"):
        operation_spec("audit.receipt.lookup", input_schema=value["input_schema"],
                       result_schema=object_schema({}), execution_semantics="read", idempotency="not-allowed")


@pytest.mark.parametrize("branches", [None, "required", {}, [], [None], [3], [[]], [{1: "bad"}],
                                     [{"required": "id"}], [{"required": [True]}],
                                     [{"required": ["id", "id"]}]])
def test_v2_oneof_refuses_invalid_branch_shapes(branches):
    schema = dict(object_schema({}), oneOf=branches)
    with pytest.raises((ValueError, TypeError)):
        spec(input_schema=schema)


def test_v2_oneof_root_extension_keeps_other_unknown_roots_closed():
    with pytest.raises(ValueError, match="unsupported root fields"):
        spec(input_schema=dict(object_schema({}), oneOf=[True, False], unknown=True))
    assert spec(result_schema=dict(object_schema({}), oneOf=[False, True]))["result_schema"]["oneOf"] == [False, True]

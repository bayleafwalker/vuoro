from __future__ import annotations

import pytest
from pydantic import ValidationError

from vuoro_service.contracts import _InvocationFields, InvocationRequest, InvocationRequestV2


COMMON_FIELDS = {
    "request_id": str,
    "operation": str,
    "arguments": object,
    "catalog_revision": (str, type(None)),
    "basis_revision": (str, type(None)),
    "idempotency_key": (str, type(None)),
    "repo_id": (str, type(None)),
}


class TestFieldOrigin:
    """Prove that the duplicated common fields now live in one shared base.

    These would fail against the pre-change duplication because each public
    model defined the fields inline, so there was no shared base to find them on.
    """

    def test_common_fields_reside_on_internal_base(self) -> None:
        for name in COMMON_FIELDS:
            resolved = _InvocationFields.model_fields[name]
            assert resolved.annotation is not None, f"{name} missing on _InvocationFields"

    def test_v1_derives_common_fields_from_internal_base(self) -> None:
        for name in COMMON_FIELDS:
            field = InvocationRequest.model_fields[name]
            source = (
                field.alias or name
                if field.alias
                else name
            )
            assert source in _InvocationFields.model_fields, (
                f"{name} on InvocationRequest does not originate from _InvocationFields"
            )

    def test_v2_derives_common_fields_from_internal_base(self) -> None:
        for name in COMMON_FIELDS:
            field = InvocationRequestV2.model_fields[name]
            assert field.annotation is not None, f"{name} missing on InvocationRequestV2"
            source = (
                field.alias or name
                if field.alias
                else name
            )
            assert source in _InvocationFields.model_fields, (
                f"{name} on InvocationRequestV2 does not originate from _InvocationFields"
            )

    def test_both_public_models_share_the_same_internal_base(self) -> None:
        assert InvocationRequest.__bases__[0] is InvocationRequestV2.__bases__[0]


class TestSchemaVersionStability:
    """Each public model keeps its own wire-visible schema version."""

    def test_v1_schema_version(self) -> None:
        assert InvocationRequest.model_fields["schema_version"].default == "invocation/v1"

    def test_v2_schema_version(self) -> None:
        assert InvocationRequestV2.model_fields["schema_version"].default == "invocation/v2"


class TestTransientCredentialsBoundary:
    """v1 rejects transient_credentials; v2 accepts and validates them."""

    def test_v1_rejects_transient_credentials(self) -> None:
        with pytest.raises(ValidationError):
            InvocationRequest(
                schema_version="invocation/v1",
                request_id="r1",
                operation="work.do",
                arguments={},
                transient_credentials={"sha256:" + "a" * 64: "proof"},
            )

    def test_v2_accepts_transient_credentials(self) -> None:
        model = InvocationRequestV2(
            schema_version="invocation/v2",
            request_id="r1",
            operation="work.do",
            arguments={},
            transient_credentials={"sha256:" + "a" * 64: "proof"},
        )
        assert "sha256:" + "a" * 64 in model.transient_credentials

    def test_v2_rejects_too_many_credentials(self) -> None:
        with pytest.raises(ValidationError):
            InvocationRequestV2(
                schema_version="invocation/v2",
                request_id="r1",
                operation="work.do",
                arguments={},
                transient_credentials={
                    f"sha256:{n:064x}": f"proof{n}" for n in range(9)
                },
            )

    def test_v2_rejects_malformed_key(self) -> None:
        with pytest.raises(ValidationError):
            InvocationRequestV2(
                schema_version="invocation/v2",
                request_id="r1",
                operation="work.do",
                arguments={},
                transient_credentials={"not-a-sha256": "value"},
            )

    def test_v2_rejects_empty_value(self) -> None:
        with pytest.raises(ValidationError):
            InvocationRequestV2(
                schema_version="invocation/v2",
                request_id="r1",
                operation="work.do",
                arguments={},
                transient_credentials={"sha256:" + "a" * 64: ""},
            )


class TestExtraFieldsRejection:
    """Both models inherit extra="forbid" from StrictModel."""

    def test_v1_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            InvocationRequest(
                schema_version="invocation/v1",
                request_id="r1",
                operation="work.do",
                arguments={},
                unknown_field="should-fail",
            )

    def test_v2_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            InvocationRequestV2(
                schema_version="invocation/v2",
                request_id="r1",
                operation="work.do",
                arguments={},
                unknown_field="should-fail",
            )


class TestSerializationParity:
    """Common fields serialize identically regardless of schema version."""

    COMMON_VALUES = {
        "request_id": "req-42",
        "operation": "work.pilot.transition",
        "arguments": {"value": 7},
        "catalog_revision": "rev1",
        "basis_revision": "basis-abc",
        "idempotency_key": "key-42",
        "repo_id": "sprintctl",
    }

    def test_serialization_matches(self) -> None:
        v1 = InvocationRequest(**self.COMMON_VALUES)
        v2 = InvocationRequestV2(**self.COMMON_VALUES)
        dumped_v1 = v1.model_dump(mode="json")
        dumped_v2 = v2.model_dump(mode="json")
        for name in COMMON_FIELDS:
            assert dumped_v1[name] == dumped_v2[name], (
                f"field {name} serialized differently for v1 and v2"
            )

    def test_schema_version_differs(self) -> None:
        v1 = InvocationRequest(**self.COMMON_VALUES)
        v2 = InvocationRequestV2(**self.COMMON_VALUES)
        assert v1.schema_version == "invocation/v1"
        assert v2.schema_version == "invocation/v2"


class TestModelDefaults:
    """Optional fields default to None."""

    def test_minimal_v1(self) -> None:
        v1 = InvocationRequest(
            schema_version="invocation/v1",
            request_id="r1",
            operation="work.do",
            arguments={},
        )
        assert v1.catalog_revision is None
        assert v1.basis_revision is None
        assert v1.idempotency_key is None
        assert v1.repo_id is None
        assert v1.model_dump(mode="json") == {
            "schema_version": "invocation/v1",
            "request_id": "r1",
            "operation": "work.do",
            "arguments": {},
            "catalog_revision": None,
            "basis_revision": None,
            "idempotency_key": None,
            "repo_id": None,
        }

    def test_minimal_v2(self) -> None:
        v2 = InvocationRequestV2(
            schema_version="invocation/v2",
            request_id="r1",
            operation="work.do",
            arguments={},
        )
        assert v2.catalog_revision is None
        assert v2.basis_revision is None
        assert v2.idempotency_key is None
        assert v2.repo_id is None
        assert v2.transient_credentials == {}
        dumped = v2.model_dump(mode="json")
        assert dumped["transient_credentials"] == {}
        for optional in ("catalog_revision", "basis_revision", "idempotency_key", "repo_id"):
            assert dumped[optional] is None

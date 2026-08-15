from __future__ import annotations

import pytest
from pydantic import ValidationError

from vuoro_service.contracts import InvocationRequest


COMMON_FIELDS = {
    "request_id": str,
    "operation": str,
    "arguments": object,
    "catalog_revision": (str, type(None)),
    "basis_revision": (str, type(None)),
    "idempotency_key": (str, type(None)),
    "repo_id": (str, type(None)),
}

MINIMAL = {
    "schema_version": "invocation/v1",
    "request_id": "r1",
    "operation": "work.do",
    "arguments": {},
}


class TestSchemaVersionStability:
    """The wire literal is a published contract; it must not drift."""

    def test_schema_version(self) -> None:
        assert InvocationRequest.model_fields["schema_version"].default == "invocation/v1"

    def test_every_common_field_is_declared(self) -> None:
        for name in COMMON_FIELDS:
            assert name in InvocationRequest.model_fields, f"{name} missing"


class TestExtraFieldsRejection:
    """The envelope inherits extra="forbid" from StrictModel."""

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            InvocationRequest(**MINIMAL, unknown_field="should-fail")

    def test_rejects_transient_credentials(self) -> None:
        """Regression guard: the invocation/v2 proof carrier stays retired.

        It existed only to transport sprintctl claim proofs. Re-adding the
        field to the envelope would silently re-open that transport, so the
        envelope must keep refusing it outright rather than ignoring it.
        """
        with pytest.raises(ValidationError):
            InvocationRequest(
                **MINIMAL,
                transient_credentials={"sha256:" + "a" * 64: "proof"},
            )


class TestModelDefaults:
    """Optional fields default to None and always serialize."""

    def test_minimal(self) -> None:
        request = InvocationRequest(**MINIMAL)
        assert request.model_dump(mode="json") == {
            "schema_version": "invocation/v1",
            "request_id": "r1",
            "operation": "work.do",
            "arguments": {},
            "catalog_revision": None,
            "basis_revision": None,
            "idempotency_key": None,
            "repo_id": None,
        }

    def test_full_round_trip(self) -> None:
        values = {
            "request_id": "req-42",
            "operation": "work.pilot.transition",
            "arguments": {"value": 7},
            "catalog_revision": "rev1",
            "basis_revision": "basis-abc",
            "idempotency_key": "key-42",
            "repo_id": "sprintctl",
        }
        dumped = InvocationRequest(**values).model_dump(mode="json")
        for name, value in values.items():
            assert dumped[name] == value
        assert dumped["schema_version"] == "invocation/v1"

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="PublishRequest")


@_attrs_define
class PublishRequest:
    """
    Attributes:
        request_id (str): Decimal string identifier; preserved above JavaScript's safe integer range.
        objective (str):
        note (None | str | Unset):
        dependants (list[str] | Unset):
        artifact_digest (str | Unset):
        artifact_key_id (str | Unset):
        artifact_bytes (str | Unset):
        artifact_envelope (str | Unset):
        artifact_signature (str | Unset):
    """

    request_id: str
    objective: str
    note: None | str | Unset = UNSET
    dependants: list[str] | Unset = UNSET
    artifact_digest: str | Unset = UNSET
    artifact_key_id: str | Unset = UNSET
    artifact_bytes: str | Unset = UNSET
    artifact_envelope: str | Unset = UNSET
    artifact_signature: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        request_id = self.request_id

        objective = self.objective

        note: None | str | Unset
        if isinstance(self.note, Unset):
            note = UNSET
        else:
            note = self.note

        dependants: list[str] | Unset = UNSET
        if not isinstance(self.dependants, Unset):
            dependants = self.dependants

        artifact_digest = self.artifact_digest

        artifact_key_id = self.artifact_key_id

        artifact_bytes = self.artifact_bytes

        artifact_envelope = self.artifact_envelope

        artifact_signature = self.artifact_signature

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "requestId": request_id,
                "objective": objective,
            }
        )
        if note is not UNSET:
            field_dict["note"] = note
        if dependants is not UNSET:
            field_dict["dependants"] = dependants
        if artifact_digest is not UNSET:
            field_dict["artifactDigest"] = artifact_digest
        if artifact_key_id is not UNSET:
            field_dict["artifactKeyId"] = artifact_key_id
        if artifact_bytes is not UNSET:
            field_dict["artifactBytes"] = artifact_bytes
        if artifact_envelope is not UNSET:
            field_dict["artifactEnvelope"] = artifact_envelope
        if artifact_signature is not UNSET:
            field_dict["artifactSignature"] = artifact_signature

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        request_id = d.pop("requestId")

        objective = d.pop("objective")

        def _parse_note(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        note = _parse_note(d.pop("note", UNSET))

        dependants = cast(list[str], d.pop("dependants", UNSET))

        artifact_digest = d.pop("artifactDigest", UNSET)

        artifact_key_id = d.pop("artifactKeyId", UNSET)

        artifact_bytes = d.pop("artifactBytes", UNSET)

        artifact_envelope = d.pop("artifactEnvelope", UNSET)

        artifact_signature = d.pop("artifactSignature", UNSET)

        publish_request = cls(
            request_id=request_id,
            objective=objective,
            note=note,
            dependants=dependants,
            artifact_digest=artifact_digest,
            artifact_key_id=artifact_key_id,
            artifact_bytes=artifact_bytes,
            artifact_envelope=artifact_envelope,
            artifact_signature=artifact_signature,
        )

        publish_request.additional_properties = d
        return publish_request

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties

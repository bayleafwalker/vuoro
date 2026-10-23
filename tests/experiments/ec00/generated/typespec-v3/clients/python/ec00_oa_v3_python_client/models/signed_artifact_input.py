from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="SignedArtifactInput")


@_attrs_define
class SignedArtifactInput:
    """
    Attributes:
        artifact_digest (str):
        artifact_key_id (str):
        artifact_bytes (str):
        artifact_envelope (str):
        artifact_signature (str):
    """

    artifact_digest: str
    artifact_key_id: str
    artifact_bytes: str
    artifact_envelope: str
    artifact_signature: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        artifact_digest = self.artifact_digest

        artifact_key_id = self.artifact_key_id

        artifact_bytes = self.artifact_bytes

        artifact_envelope = self.artifact_envelope

        artifact_signature = self.artifact_signature

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "artifactDigest": artifact_digest,
                "artifactKeyId": artifact_key_id,
                "artifactBytes": artifact_bytes,
                "artifactEnvelope": artifact_envelope,
                "artifactSignature": artifact_signature,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        artifact_digest = d.pop("artifactDigest")

        artifact_key_id = d.pop("artifactKeyId")

        artifact_bytes = d.pop("artifactBytes")

        artifact_envelope = d.pop("artifactEnvelope")

        artifact_signature = d.pop("artifactSignature")

        signed_artifact_input = cls(
            artifact_digest=artifact_digest,
            artifact_key_id=artifact_key_id,
            artifact_bytes=artifact_bytes,
            artifact_envelope=artifact_envelope,
            artifact_signature=artifact_signature,
        )

        signed_artifact_input.additional_properties = d
        return signed_artifact_input

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

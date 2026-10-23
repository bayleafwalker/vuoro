from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="ArtifactRead")


@_attrs_define
class ArtifactRead:
    """
    Attributes:
        material_revision (str):
        material_digest (str):
        artifact_digest (str):
        key_id (str):
        original_bytes (str):
        envelope_bytes (str):
        signature (str):
    """

    material_revision: str
    material_digest: str
    artifact_digest: str
    key_id: str
    original_bytes: str
    envelope_bytes: str
    signature: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        material_revision = self.material_revision

        material_digest = self.material_digest

        artifact_digest = self.artifact_digest

        key_id = self.key_id

        original_bytes = self.original_bytes

        envelope_bytes = self.envelope_bytes

        signature = self.signature

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "materialRevision": material_revision,
                "materialDigest": material_digest,
                "artifactDigest": artifact_digest,
                "keyId": key_id,
                "originalBytes": original_bytes,
                "envelopeBytes": envelope_bytes,
                "signature": signature,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        material_revision = d.pop("materialRevision")

        material_digest = d.pop("materialDigest")

        artifact_digest = d.pop("artifactDigest")

        key_id = d.pop("keyId")

        original_bytes = d.pop("originalBytes")

        envelope_bytes = d.pop("envelopeBytes")

        signature = d.pop("signature")

        artifact_read = cls(
            material_revision=material_revision,
            material_digest=material_digest,
            artifact_digest=artifact_digest,
            key_id=key_id,
            original_bytes=original_bytes,
            envelope_bytes=envelope_bytes,
            signature=signature,
        )

        artifact_read.additional_properties = d
        return artifact_read

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

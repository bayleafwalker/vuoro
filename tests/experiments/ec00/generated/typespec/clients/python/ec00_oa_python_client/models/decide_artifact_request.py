from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="DecideArtifactRequest")


@_attrs_define
class DecideArtifactRequest:
    """
    Attributes:
        expected_revision (int):
        artifact_digest (str):
    """

    expected_revision: int
    artifact_digest: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        expected_revision = self.expected_revision

        artifact_digest = self.artifact_digest

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "expectedRevision": expected_revision,
                "artifactDigest": artifact_digest,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        expected_revision = d.pop("expectedRevision")

        artifact_digest = d.pop("artifactDigest")

        decide_artifact_request = cls(
            expected_revision=expected_revision,
            artifact_digest=artifact_digest,
        )

        decide_artifact_request.additional_properties = d
        return decide_artifact_request

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

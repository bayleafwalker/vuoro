from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="AcknowledgeRequest")


@_attrs_define
class AcknowledgeRequest:
    """
    Attributes:
        dependent_id (str):
        expected_revision (str): Canonical nonnegative decimal text in signed-int64 range; 0 or no leading zeros.
    """

    dependent_id: str
    expected_revision: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        dependent_id = self.dependent_id

        expected_revision = self.expected_revision

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "dependentId": dependent_id,
                "expectedRevision": expected_revision,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        dependent_id = d.pop("dependentId")

        expected_revision = d.pop("expectedRevision")

        acknowledge_request = cls(
            dependent_id=dependent_id,
            expected_revision=expected_revision,
        )

        acknowledge_request.additional_properties = d
        return acknowledge_request

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

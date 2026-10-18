from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="Ec00OaV1DependentRead")


@_attrs_define
class Ec00OaV1DependentRead:
    """
    Attributes:
        dependent_id (str):
        active (bool):
        continuation_ack (bool):
    """

    dependent_id: str
    active: bool
    continuation_ack: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        dependent_id = self.dependent_id

        active = self.active

        continuation_ack = self.continuation_ack

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "dependentId": dependent_id,
                "active": active,
                "continuationAck": continuation_ack,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        dependent_id = d.pop("dependentId")

        active = d.pop("active")

        continuation_ack = d.pop("continuationAck")

        ec_00_oa_v1_dependent_read = cls(
            dependent_id=dependent_id,
            active=active,
            continuation_ack=continuation_ack,
        )

        ec_00_oa_v1_dependent_read.additional_properties = d
        return ec_00_oa_v1_dependent_read

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

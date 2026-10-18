from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="WatchEvent")


@_attrs_define
class WatchEvent:
    """
    Attributes:
        seq (str):
        request_id (str):
        action (str):
        revision (str):
    """

    seq: str
    request_id: str
    action: str
    revision: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        seq = self.seq

        request_id = self.request_id

        action = self.action

        revision = self.revision

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "seq": seq,
                "requestId": request_id,
                "action": action,
                "revision": revision,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        seq = d.pop("seq")

        request_id = d.pop("requestId")

        action = d.pop("action")

        revision = d.pop("revision")

        watch_event = cls(
            seq=seq,
            request_id=request_id,
            action=action,
            revision=revision,
        )

        watch_event.additional_properties = d
        return watch_event

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

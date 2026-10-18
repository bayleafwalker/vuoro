from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.ec_00_oa_v1_request_state import Ec00OaV1RequestState

T = TypeVar("T", bound="Ec00OaV1RequestSummary")


@_attrs_define
class Ec00OaV1RequestSummary:
    """
    Attributes:
        request_id (str):
        revision (str):
        state (Ec00OaV1RequestState):
        created_seq (str):
    """

    request_id: str
    revision: str
    state: Ec00OaV1RequestState
    created_seq: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        request_id = self.request_id

        revision = self.revision

        state = self.state.value

        created_seq = self.created_seq

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "requestId": request_id,
                "revision": revision,
                "state": state,
                "createdSeq": created_seq,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        request_id = d.pop("requestId")

        revision = d.pop("revision")

        state = Ec00OaV1RequestState(d.pop("state"))

        created_seq = d.pop("createdSeq")

        ec_00_oa_v1_request_summary = cls(
            request_id=request_id,
            revision=revision,
            state=state,
            created_seq=created_seq,
        )

        ec_00_oa_v1_request_summary.additional_properties = d
        return ec_00_oa_v1_request_summary

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

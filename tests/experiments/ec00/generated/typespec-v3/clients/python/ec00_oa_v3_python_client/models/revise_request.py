from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.command_body import CommandBody


T = TypeVar("T", bound="ReviseRequest")


@_attrs_define
class ReviseRequest:
    """
    Attributes:
        request_id (str):
        expected_revision (str):
        body (CommandBody):
    """

    request_id: str
    expected_revision: str
    body: CommandBody
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        request_id = self.request_id

        expected_revision = self.expected_revision

        body = self.body.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "requestId": request_id,
                "expectedRevision": expected_revision,
                "body": body,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.command_body import CommandBody

        d = dict(src_dict)
        request_id = d.pop("requestId")

        expected_revision = d.pop("expectedRevision")

        body = CommandBody.from_dict(d.pop("body"))

        revise_request = cls(
            request_id=request_id,
            expected_revision=expected_revision,
            body=body,
        )

        revise_request.additional_properties = d
        return revise_request

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

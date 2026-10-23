from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.command_body import CommandBody
    from ..models.signed_artifact_input import SignedArtifactInput


T = TypeVar("T", bound="PublishRequest")


@_attrs_define
class PublishRequest:
    """
    Attributes:
        request_id (str):
        body (CommandBody):
        dependants (list[str]):
        artifact (SignedArtifactInput | Unset):
    """

    request_id: str
    body: CommandBody
    dependants: list[str]
    artifact: SignedArtifactInput | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        request_id = self.request_id

        body = self.body.to_dict()

        dependants = self.dependants

        artifact: dict[str, Any] | Unset = UNSET
        if not isinstance(self.artifact, Unset):
            artifact = self.artifact.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "requestId": request_id,
                "body": body,
                "dependants": dependants,
            }
        )
        if artifact is not UNSET:
            field_dict["artifact"] = artifact

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.command_body import CommandBody
        from ..models.signed_artifact_input import SignedArtifactInput

        d = dict(src_dict)
        request_id = d.pop("requestId")

        body = CommandBody.from_dict(d.pop("body"))

        dependants = cast(list[str], d.pop("dependants"))

        _artifact = d.pop("artifact", UNSET)
        artifact: SignedArtifactInput | Unset
        if isinstance(_artifact, Unset):
            artifact = UNSET
        else:
            artifact = SignedArtifactInput.from_dict(_artifact)

        publish_request = cls(
            request_id=request_id,
            body=body,
            dependants=dependants,
            artifact=artifact,
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

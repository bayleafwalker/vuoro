from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.ec_00_oa_v1_request_state import Ec00OaV1RequestState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ec_00_oa_v1_request_body import Ec00OaV1RequestBody


T = TypeVar("T", bound="RequestRead")


@_attrs_define
class RequestRead:
    """
    Attributes:
        request_id (str):
        revision (str):
        material_revision (str):
        state (Ec00OaV1RequestState):
        body (Ec00OaV1RequestBody):
        reported (bool):
        verified (bool):
        created_seq (str):
        evidence_ref (None | str | Unset):
        artifact_decision_digest (None | str | Unset):
    """

    request_id: str
    revision: str
    material_revision: str
    state: Ec00OaV1RequestState
    body: Ec00OaV1RequestBody
    reported: bool
    verified: bool
    created_seq: str
    evidence_ref: None | str | Unset = UNSET
    artifact_decision_digest: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        request_id = self.request_id

        revision = self.revision

        material_revision = self.material_revision

        state = self.state.value

        body = self.body.to_dict()

        reported = self.reported

        verified = self.verified

        created_seq = self.created_seq

        evidence_ref: None | str | Unset
        if isinstance(self.evidence_ref, Unset):
            evidence_ref = UNSET
        else:
            evidence_ref = self.evidence_ref

        artifact_decision_digest: None | str | Unset
        if isinstance(self.artifact_decision_digest, Unset):
            artifact_decision_digest = UNSET
        else:
            artifact_decision_digest = self.artifact_decision_digest

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "requestId": request_id,
                "revision": revision,
                "materialRevision": material_revision,
                "state": state,
                "body": body,
                "reported": reported,
                "verified": verified,
                "createdSeq": created_seq,
            }
        )
        if evidence_ref is not UNSET:
            field_dict["evidenceRef"] = evidence_ref
        if artifact_decision_digest is not UNSET:
            field_dict["artifactDecisionDigest"] = artifact_decision_digest

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.ec_00_oa_v1_request_body import Ec00OaV1RequestBody

        d = dict(src_dict)
        request_id = d.pop("requestId")

        revision = d.pop("revision")

        material_revision = d.pop("materialRevision")

        state = Ec00OaV1RequestState(d.pop("state"))

        body = Ec00OaV1RequestBody.from_dict(d.pop("body"))

        reported = d.pop("reported")

        verified = d.pop("verified")

        created_seq = d.pop("createdSeq")

        def _parse_evidence_ref(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        evidence_ref = _parse_evidence_ref(d.pop("evidenceRef", UNSET))

        def _parse_artifact_decision_digest(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        artifact_decision_digest = _parse_artifact_decision_digest(
            d.pop("artifactDecisionDigest", UNSET)
        )

        request_read = cls(
            request_id=request_id,
            revision=revision,
            material_revision=material_revision,
            state=state,
            body=body,
            reported=reported,
            verified=verified,
            created_seq=created_seq,
            evidence_ref=evidence_ref,
            artifact_decision_digest=artifact_decision_digest,
        )

        request_read.additional_properties = d
        return request_read

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

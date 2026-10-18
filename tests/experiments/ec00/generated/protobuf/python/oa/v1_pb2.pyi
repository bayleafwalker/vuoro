from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class RequestState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    REQUEST_STATE_UNSPECIFIED: _ClassVar[RequestState]
    REQUEST_STATE_OPEN: _ClassVar[RequestState]
    REQUEST_STATE_RESOLVED: _ClassVar[RequestState]
REQUEST_STATE_UNSPECIFIED: RequestState
REQUEST_STATE_OPEN: RequestState
REQUEST_STATE_RESOLVED: RequestState

class PublishRequest(_message.Message):
    __slots__ = ("request_id", "objective", "note", "note_null", "dependent_ids", "artifact_digest", "artifact_key_id", "artifact_bytes", "artifact_envelope", "artifact_signature")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    OBJECTIVE_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    NOTE_NULL_FIELD_NUMBER: _ClassVar[int]
    DEPENDENT_IDS_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_DIGEST_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_KEY_ID_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_BYTES_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    objective: str
    note: str
    note_null: _struct_pb2.NullValue
    dependent_ids: _containers.RepeatedScalarFieldContainer[str]
    artifact_digest: str
    artifact_key_id: str
    artifact_bytes: bytes
    artifact_envelope: bytes
    artifact_signature: bytes
    def __init__(self, request_id: _Optional[str] = ..., objective: _Optional[str] = ..., note: _Optional[str] = ..., note_null: _Optional[_Union[_struct_pb2.NullValue, str]] = ..., dependent_ids: _Optional[_Iterable[str]] = ..., artifact_digest: _Optional[str] = ..., artifact_key_id: _Optional[str] = ..., artifact_bytes: _Optional[bytes] = ..., artifact_envelope: _Optional[bytes] = ..., artifact_signature: _Optional[bytes] = ...) -> None: ...

class ReviseRequest(_message.Message):
    __slots__ = ("request_id", "expected_revision", "objective", "note", "note_null")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_REVISION_FIELD_NUMBER: _ClassVar[int]
    OBJECTIVE_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    NOTE_NULL_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    expected_revision: int
    objective: str
    note: str
    note_null: _struct_pb2.NullValue
    def __init__(self, request_id: _Optional[str] = ..., expected_revision: _Optional[int] = ..., objective: _Optional[str] = ..., note: _Optional[str] = ..., note_null: _Optional[_Union[_struct_pb2.NullValue, str]] = ...) -> None: ...

class MutationResult(_message.Message):
    __slots__ = ("request_id", "revision")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    revision: int
    def __init__(self, request_id: _Optional[str] = ..., revision: _Optional[int] = ...) -> None: ...

class WithdrawDependentRequest(_message.Message):
    __slots__ = ("request_id", "dependent_id", "expected_revision")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    DEPENDENT_ID_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_REVISION_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    dependent_id: str
    expected_revision: int
    def __init__(self, request_id: _Optional[str] = ..., dependent_id: _Optional[str] = ..., expected_revision: _Optional[int] = ...) -> None: ...

class ReportPerformedRequest(_message.Message):
    __slots__ = ("request_id", "expected_revision")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_REVISION_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    expected_revision: int
    def __init__(self, request_id: _Optional[str] = ..., expected_revision: _Optional[int] = ...) -> None: ...

class VerifyRequest(_message.Message):
    __slots__ = ("request_id", "expected_revision", "evidence_ref")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_REVISION_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_REF_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    expected_revision: int
    evidence_ref: str
    def __init__(self, request_id: _Optional[str] = ..., expected_revision: _Optional[int] = ..., evidence_ref: _Optional[str] = ...) -> None: ...

class DecideArtifactRequest(_message.Message):
    __slots__ = ("request_id", "expected_revision", "artifact_digest")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_REVISION_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_DIGEST_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    expected_revision: int
    artifact_digest: str
    def __init__(self, request_id: _Optional[str] = ..., expected_revision: _Optional[int] = ..., artifact_digest: _Optional[str] = ...) -> None: ...

class ResolveRequest(_message.Message):
    __slots__ = ("request_id", "expected_revision")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_REVISION_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    expected_revision: int
    def __init__(self, request_id: _Optional[str] = ..., expected_revision: _Optional[int] = ...) -> None: ...

class AcknowledgeContinuationRequest(_message.Message):
    __slots__ = ("request_id", "dependent_id", "expected_revision")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    DEPENDENT_ID_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_REVISION_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    dependent_id: str
    expected_revision: int
    def __init__(self, request_id: _Optional[str] = ..., dependent_id: _Optional[str] = ..., expected_revision: _Optional[int] = ...) -> None: ...

class RequestBody(_message.Message):
    __slots__ = ("objective", "note", "note_null")
    OBJECTIVE_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    NOTE_NULL_FIELD_NUMBER: _ClassVar[int]
    objective: str
    note: str
    note_null: _struct_pb2.NullValue
    def __init__(self, objective: _Optional[str] = ..., note: _Optional[str] = ..., note_null: _Optional[_Union[_struct_pb2.NullValue, str]] = ...) -> None: ...

class RequestRead(_message.Message):
    __slots__ = ("request_id", "revision", "material_revision", "state", "body", "reported", "verified", "evidence_ref", "evidence_ref_null", "artifact_decision_digest", "artifact_decision_digest_null", "created_seq")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    MATERIAL_REVISION_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    BODY_FIELD_NUMBER: _ClassVar[int]
    REPORTED_FIELD_NUMBER: _ClassVar[int]
    VERIFIED_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_REF_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_REF_NULL_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_DECISION_DIGEST_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_DECISION_DIGEST_NULL_FIELD_NUMBER: _ClassVar[int]
    CREATED_SEQ_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    revision: int
    material_revision: int
    state: RequestState
    body: RequestBody
    reported: bool
    verified: bool
    evidence_ref: str
    evidence_ref_null: _struct_pb2.NullValue
    artifact_decision_digest: str
    artifact_decision_digest_null: _struct_pb2.NullValue
    created_seq: int
    def __init__(self, request_id: _Optional[str] = ..., revision: _Optional[int] = ..., material_revision: _Optional[int] = ..., state: _Optional[_Union[RequestState, str]] = ..., body: _Optional[_Union[RequestBody, _Mapping]] = ..., reported: _Optional[bool] = ..., verified: _Optional[bool] = ..., evidence_ref: _Optional[str] = ..., evidence_ref_null: _Optional[_Union[_struct_pb2.NullValue, str]] = ..., artifact_decision_digest: _Optional[str] = ..., artifact_decision_digest_null: _Optional[_Union[_struct_pb2.NullValue, str]] = ..., created_seq: _Optional[int] = ...) -> None: ...

class DependentRead(_message.Message):
    __slots__ = ("dependent_id", "active", "continuation_ack")
    DEPENDENT_ID_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_FIELD_NUMBER: _ClassVar[int]
    CONTINUATION_ACK_FIELD_NUMBER: _ClassVar[int]
    dependent_id: str
    active: bool
    continuation_ack: bool
    def __init__(self, dependent_id: _Optional[str] = ..., active: _Optional[bool] = ..., continuation_ack: _Optional[bool] = ...) -> None: ...

class DependantsRead(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[DependentRead]
    def __init__(self, items: _Optional[_Iterable[_Union[DependentRead, _Mapping]]] = ...) -> None: ...

class ArtifactRead(_message.Message):
    __slots__ = ("material_revision", "material_digest", "artifact_digest", "key_id", "original_bytes", "envelope_bytes", "signature")
    MATERIAL_REVISION_FIELD_NUMBER: _ClassVar[int]
    MATERIAL_DIGEST_FIELD_NUMBER: _ClassVar[int]
    ARTIFACT_DIGEST_FIELD_NUMBER: _ClassVar[int]
    KEY_ID_FIELD_NUMBER: _ClassVar[int]
    ORIGINAL_BYTES_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_BYTES_FIELD_NUMBER: _ClassVar[int]
    SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    material_revision: int
    material_digest: str
    artifact_digest: str
    key_id: str
    original_bytes: bytes
    envelope_bytes: bytes
    signature: bytes
    def __init__(self, material_revision: _Optional[int] = ..., material_digest: _Optional[str] = ..., artifact_digest: _Optional[str] = ..., key_id: _Optional[str] = ..., original_bytes: _Optional[bytes] = ..., envelope_bytes: _Optional[bytes] = ..., signature: _Optional[bytes] = ...) -> None: ...

class RequestSummary(_message.Message):
    __slots__ = ("request_id", "revision", "state", "created_seq")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    CREATED_SEQ_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    revision: int
    state: RequestState
    created_seq: int
    def __init__(self, request_id: _Optional[str] = ..., revision: _Optional[int] = ..., state: _Optional[_Union[RequestState, str]] = ..., created_seq: _Optional[int] = ...) -> None: ...

class RequestPage(_message.Message):
    __slots__ = ("items", "next_cursor")
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[RequestSummary]
    next_cursor: str
    def __init__(self, items: _Optional[_Iterable[_Union[RequestSummary, _Mapping]]] = ..., next_cursor: _Optional[str] = ...) -> None: ...

class WatchEvent(_message.Message):
    __slots__ = ("seq", "request_id", "action", "revision")
    SEQ_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    seq: int
    request_id: str
    action: str
    revision: int
    def __init__(self, seq: _Optional[int] = ..., request_id: _Optional[str] = ..., action: _Optional[str] = ..., revision: _Optional[int] = ...) -> None: ...

class WatchPage(_message.Message):
    __slots__ = ("events", "next_cursor", "more")
    EVENTS_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    MORE_FIELD_NUMBER: _ClassVar[int]
    events: _containers.RepeatedCompositeFieldContainer[WatchEvent]
    next_cursor: str
    more: bool
    def __init__(self, events: _Optional[_Iterable[_Union[WatchEvent, _Mapping]]] = ..., next_cursor: _Optional[str] = ..., more: _Optional[bool] = ...) -> None: ...

class RequestById(_message.Message):
    __slots__ = ("request_id",)
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    def __init__(self, request_id: _Optional[str] = ...) -> None: ...

class ReadArtifactRequest(_message.Message):
    __slots__ = ("request_id", "material_revision")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    MATERIAL_REVISION_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    material_revision: int
    def __init__(self, request_id: _Optional[str] = ..., material_revision: _Optional[int] = ...) -> None: ...

class PageRequest(_message.Message):
    __slots__ = ("cursor", "limit")
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    cursor: str
    limit: int
    def __init__(self, cursor: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class PageRequestV2(_message.Message):
    __slots__ = ("cursor", "limit")
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    cursor: str
    limit: int
    def __init__(self, cursor: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

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

class JsonResult(_message.Message):
    __slots__ = ("json",)
    JSON_FIELD_NUMBER: _ClassVar[int]
    json: str
    def __init__(self, json: _Optional[str] = ...) -> None: ...

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class PublishRequest(_message.Message):
    __slots__ = ("request_id", "objective", "note")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    OBJECTIVE_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    objective: str
    note: str
    def __init__(self, request_id: _Optional[str] = ..., objective: _Optional[str] = ..., note: _Optional[str] = ...) -> None: ...

class ReviseRequest(_message.Message):
    __slots__ = ("request_id", "expected_revision", "objective", "note")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_REVISION_FIELD_NUMBER: _ClassVar[int]
    OBJECTIVE_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    expected_revision: int
    objective: str
    note: str
    def __init__(self, request_id: _Optional[str] = ..., expected_revision: _Optional[int] = ..., objective: _Optional[str] = ..., note: _Optional[str] = ...) -> None: ...

class MutationResult(_message.Message):
    __slots__ = ("request_id", "revision")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    revision: int
    def __init__(self, request_id: _Optional[str] = ..., revision: _Optional[int] = ...) -> None: ...

"""Provisional client for the pinned Sovite remote-execution API.

This adapter is intentionally separate from AsyncVuoroClient and all live
dispatch paths. The route and response contract is pinned in the package docs.
"""
from __future__ import annotations

import json
import ipaddress
import math
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator


MAX_REQUEST_BYTES = 1 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 10_000


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non_finite_json_number")


def _preflight_json(raw: bytes) -> str:
    """Check lexical structure and resource limits before materializing JSON."""
    try:
        source = raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise ValueError("invalid_json_encoding") from None
    stack: list[str] = []
    nodes = 0
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char in " \t\r\n,:":
            index += 1
            continue
        if char in "[{":
            stack.append(char)
            nodes += 1
            if len(stack) - 1 > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
                raise ValueError("json_resource_limit")
            index += 1
            continue
        if char in "]}":
            if not stack or (char == "]") != (stack[-1] == "["):
                raise ValueError("invalid_json_structure")
            stack.pop()
            index += 1
            continue
        if char == '"':
            index += 1
            while index < length:
                current = source[index]
                if current == '"':
                    index += 1
                    break
                if ord(current) < 0x20:
                    raise ValueError("invalid_json_string")
                if current == "\\":
                    index += 1
                    if index >= length or source[index] not in '"\\/bfnrtu':
                        raise ValueError("invalid_json_escape")
                    if source[index] == "u":
                        digits = source[index + 1:index + 5]
                        if len(digits) != 4 or any(c not in "0123456789abcdefABCDEF" for c in digits):
                            raise ValueError("invalid_json_escape")
                        index += 4
                index += 1
            else:
                raise ValueError("unterminated_json_string")
            lookahead = index
            while lookahead < length and source[lookahead] in " \t\r\n":
                lookahead += 1
            # Object member names are not value nodes; JSON parsing below will
            # reject a string in any other invalid structural position.
            if lookahead >= length or source[lookahead] != ":":
                nodes += 1
                if len(stack) > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
                    raise ValueError("json_resource_limit")
            continue
        if char in "-0123456789tfn":
            nodes += 1
            if len(stack) > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
                raise ValueError("json_resource_limit")
            index += 1
            while index < length and source[index] not in " \t\r\n,:[]{}\"":
                index += 1
            continue
        raise ValueError("invalid_json_structure")
    if stack:
        raise ValueError("unterminated_json_structure")
    return source


def _validate_json_limits(value: Any) -> None:
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise ValueError("json_resource_limit")
        if item is None or type(item) in {bool, int}:
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("non_finite_json_number")
            continue
        if isinstance(item, str):
            if any(0xD800 <= ord(char) <= 0xDFFF for char in item):
                raise ValueError("non_scalar_json_string")
            continue
        if isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
            continue
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or any(0xD800 <= ord(char) <= 0xDFFF for char in key):
                    raise ValueError("non_scalar_json_string")
                pending.append((child, depth + 1))
            continue
        raise ValueError("invalid_json_value")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ExecutionIdentity(_StrictModel):
    instance_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    job_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    generation: str = Field(min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$")
    fence: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")


class ExecutionMutation(ExecutionIdentity):
    operation_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class PullRequest(_StrictModel):
    instance_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    operation_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class CompleteRequest(ExecutionMutation):
    outcome: Literal["completed", "failed", "retryable_failure", "cancelled"]
    result: dict[str, Any] | None = None
    error_code: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[a-z_]+$")


class PullJob(_StrictModel):
    job_id: str
    generation: str
    fence: str
    lease_until: FiniteFloat
    task: dict[str, Any]
    resolved: dict[str, Any]


class PullResponse(_StrictModel):
    state: Literal["idle", "reserved"]
    job: PullJob | None = None

    @model_validator(mode="after")
    def validate_state_shape(self) -> PullResponse:
        if (self.state == "idle") != (self.job is None):
            raise ValueError("pull_response_state_shape")
        return self


class StartResponse(_StrictModel):
    state: Literal["started", "already_started"]
    job_id: str
    generation: str
    fence: str
    lease_until: FiniteFloat | None = None
    deadline: FiniteFloat | None = None
    execution: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_replay_has_no_bearer(self) -> StartResponse:
        if self.state == "already_started" and self.execution is not None:
            raise ValueError("replayed_start_must_not_return_execution_bearer")
        return self


class RenewResponse(_StrictModel):
    state: Literal["renewed"]
    job_id: str
    generation: str
    fence: str
    lease_until: FiniteFloat
    deadline: FiniteFloat


class StatusResponse(_StrictModel):
    state: Literal["current", "superseded", "finished", "expired"]
    job_id: str
    generation: str
    fence: str
    phase: str | None
    job_status: str | None
    lease_until: FiniteFloat | None
    deadline: FiniteFloat | None


class HandbackResponse(_StrictModel):
    state: Literal["handed_back"]
    job_id: str
    generation: str
    fence: str
    disposition: Literal["cancelled", "blocked", "retry_wait"]


class CompleteResponse(_StrictModel):
    state: Literal["completed", "failed", "cancelled", "retry_wait", "blocked"]
    job_id: str
    generation: str
    fence: str
    error_code: str | None
    artifact_sha256: str | None = None


class LookupRequest(_StrictModel):
    instance_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    operation_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class LookupResponse(_StrictModel):
    state: Literal["found"]
    operation_id: str
    operation_kind: Literal["pull", "start", "renew", "handback", "complete"]
    job_id: str | None
    fence: str | None
    result_state: Literal[
        "idle", "reserved", "started", "already_started", "renewed", "handed_back",
        "completed", "failed", "cancelled", "retry_wait", "blocked",
    ]


class SoviteExecutionError(RuntimeError):
    """Sanitized HTTP or protocol refusal; never includes response contents."""

    def __init__(self, *, status_code: int | None, code: str = "remote_error") -> None:
        super().__init__(f"Sovite execution request failed ({code})")
        self.status_code = status_code
        self.code = code


class SoviteExecutionUncertain(SoviteExecutionError):
    """A mutation may have committed; use lookup before any further action."""

    def __init__(self, *, operation_id: str, status_code: int | None = None) -> None:
        super().__init__(status_code=status_code, code="outcome_uncertain")
        self.operation_id = operation_id
        self.recovery_action = "lookup(instance_id, operation_id) before retrying or advancing"


class SoviteExecutionClient:
    """Typed, bounded HTTP client for the provisional Sovite candidate only.

    No retries are configured. Mutations with an ambiguous transport outcome
    raise SoviteExecutionUncertain and carry the operation ID for lookup.
    """

    def __init__(
        self,
        endpoint: str,
        bearer_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        parsed = urlsplit(endpoint)
        try:
            loopback = parsed.hostname == "localhost" or (
                parsed.hostname is not None and ipaddress.ip_address(parsed.hostname).is_loopback
            )
        except ValueError:
            loopback = False
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment
                or (parsed.scheme != "https" and not loopback)):
            raise ValueError("Use HTTPS, or loopback HTTP, without embedded credentials")
        if (not isinstance(bearer_token, str) or not bearer_token or len(bearer_token) > 4096
                or any(ch in bearer_token for ch in "\r\n")):
            raise ValueError("A bounded execution-scope bearer token is required")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self._token = bearer_token
        self._http = httpx.AsyncClient(
            base_url=endpoint.rstrip("/"),
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            headers={"Authorization": f"Bearer {bearer_token}", "Accept": "application/json"},
        )

    async def __aenter__(self) -> SoviteExecutionClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _encode(model: BaseModel) -> bytes:
        try:
            dumped = model.model_dump(mode="json")
            _validate_json_limits(dumped)
            raw = json.dumps(dumped, ensure_ascii=False,
                             allow_nan=False, separators=(",", ":")).encode("utf-8", "strict")
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
            raise SoviteExecutionError(status_code=None, code="invalid_request") from None
        if len(raw) > MAX_REQUEST_BYTES:
            raise SoviteExecutionError(status_code=None, code="request_too_large")
        return raw

    async def _request(self, path: str, request: BaseModel, response_model: type[BaseModel],
                       *, mutation: bool, operation_id: str | None = None) -> Any:
        body = self._encode(request)
        try:
            async with self._http.stream("POST", path, content=body,
                                         headers={"Content-Type": "application/json"}) as response:
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        if mutation:
                            raise SoviteExecutionUncertain(operation_id=operation_id or "unknown",
                                                           status_code=response.status_code)
                        raise SoviteExecutionError(status_code=response.status_code,
                                                   code="response_too_large")
                    chunks.append(chunk)
                if response.status_code < 200 or response.status_code >= 300:
                    if mutation and response.status_code >= 500:
                        raise SoviteExecutionUncertain(operation_id=operation_id or "unknown",
                                                       status_code=response.status_code)
                    raise SoviteExecutionError(status_code=response.status_code)
        except SoviteExecutionError:
            raise
        except httpx.HTTPError:
            if mutation:
                raise SoviteExecutionUncertain(operation_id=operation_id or "unknown") from None
            raise SoviteExecutionError(status_code=None, code="transport_error") from None
        try:
            response_bytes = b"".join(chunks)
            source = _preflight_json(response_bytes)
            payload = json.loads(source, object_pairs_hook=_unique_object,
                                 parse_constant=_reject_constant)
            _validate_json_limits(payload)
            return response_model.model_validate(payload)
        except (ValueError, TypeError, RecursionError):
            if mutation:
                raise SoviteExecutionUncertain(operation_id=operation_id or "unknown",
                                               status_code=response.status_code) from None
            raise SoviteExecutionError(status_code=response.status_code, code="invalid_response") from None

    async def pull(self, request: PullRequest) -> PullResponse:
        return await self._request("/internal/execution/pull", request, PullResponse,
                                   mutation=True, operation_id=request.operation_id)

    async def start(self, request: ExecutionMutation) -> StartResponse:
        return await self._request("/internal/execution/start", request, StartResponse,
                                   mutation=True, operation_id=request.operation_id)

    async def renew(self, request: ExecutionMutation) -> RenewResponse:
        return await self._request("/internal/execution/renew", request, RenewResponse,
                                   mutation=True, operation_id=request.operation_id)

    async def status(self, request: ExecutionIdentity) -> StatusResponse:
        return await self._request("/internal/execution/status", request, StatusResponse,
                                   mutation=False)

    async def handback(self, request: ExecutionMutation) -> HandbackResponse:
        return await self._request("/internal/execution/handback", request, HandbackResponse,
                                   mutation=True, operation_id=request.operation_id)

    async def complete(self, request: CompleteRequest) -> CompleteResponse:
        return await self._request("/internal/execution/complete", request, CompleteResponse,
                                   mutation=True, operation_id=request.operation_id)

    async def lookup(self, request: LookupRequest) -> LookupResponse:
        return await self._request("/internal/execution/lookup", request, LookupResponse,
                                   mutation=False)

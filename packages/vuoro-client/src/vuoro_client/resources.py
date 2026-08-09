"""Strict neutral observation envelopes; every token remains owner supplied."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourceReference(_Strict):
    schema_version: Literal["resource-reference/v1"]
    owner: str = Field(min_length=1)
    resource_kind: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    revision: str = Field(min_length=1)


class ResourceSnapshot(_Strict):
    schema_version: Literal["resource-snapshot/v1"]
    reference: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    cursor: str = Field(min_length=1)
    terminal: bool
    state: dict[str, Any]


class ResourceEvent(_Strict):
    event_id: str = Field(min_length=1)
    terminal: bool
    data: dict[str, Any]


class ResourceChanges(_Strict):
    schema_version: Literal["resource-changes/v1"]
    reference: str = Field(min_length=1)
    next_cursor: str = Field(min_length=1)
    events: list[ResourceEvent]

"""Strict contracts for public Vuoro discovery and bootstrap metadata.

The transport client owns these wire contracts but does not perform bootstrap
filesystem or package-manager mutations.  Those responsibilities belong to
the separate ``vuoro-bootstrap`` distribution.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DiscoveryContractError(ValueError):
    """A discovery or bootstrap manifest response is not safe to consume."""


_IMMUTABLE_VERSION = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:[a-z]+[0-9]+)?$"
)
_PYTHON_VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")


def _immutable_version(value: str) -> str:
    if not _IMMUTABLE_VERSION.fullmatch(value):
        raise ValueError("compatibility values must be immutable release versions")
    return value


def _python_version(value: str) -> str:
    if not _PYTHON_VERSION.fullmatch(value):
        raise ValueError("minimum_python must be a concrete version")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _https_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("endpoint must be an HTTPS URL without credentials or query data")
    return value.rstrip("/")


class ClientProtocolRange(_StrictModel):
    minimum: int = Field(ge=1)
    maximum: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered(self) -> "ClientProtocolRange":
        if self.minimum > self.maximum:
            raise ValueError("client protocol minimum cannot exceed maximum")
        return self


class DiscoveryDocument(_StrictModel):
    """The unauthenticated ``/.well-known/vuoro`` response."""

    schema_version: str = Field(pattern=r"^vuoro-discovery/v1$")
    environment_id: str = Field(min_length=1)
    api_endpoint: str
    bootstrap_endpoint: str
    activation_endpoint: str
    client_protocol: ClientProtocolRange
    bootstrap_manifest: str

    _validate_api_endpoint = field_validator(
        "api_endpoint", "bootstrap_endpoint", "activation_endpoint", "bootstrap_manifest"
    )(_https_url)

    @model_validator(mode="after")
    def endpoints_are_bound(self) -> "DiscoveryDocument":
        api = urlsplit(self.api_endpoint)
        for field_name in ("bootstrap_endpoint", "bootstrap_manifest"):
            endpoint = urlsplit(getattr(self, field_name))
            if (endpoint.scheme, endpoint.netloc) != (api.scheme, api.netloc):
                raise ValueError(f"{field_name} must use the declared api_endpoint origin")
        if 1 < self.client_protocol.minimum or 1 > self.client_protocol.maximum:
            raise ValueError("discovery must advertise client protocol v1")
        return self


class BootstrapPackageVersions(_StrictModel):
    """The package portion of the Cloud compatibility set."""

    vuoro_bootstrap: str = Field(alias="vuoro-bootstrap", min_length=1)
    vuoro_client: str = Field(alias="vuoro-client", min_length=1)
    sprintctl: str = Field(min_length=1)

    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True
    )

    @field_validator("vuoro_bootstrap", "vuoro_client", "sprintctl")
    @classmethod
    def released_version(cls, value: str) -> str:
        return _immutable_version(value)


class BootstrapServiceCompatibility(_StrictModel):
    client_protocol_minimum: int = Field(ge=1)
    client_protocol_maximum: int = Field(ge=1)
    vuoro_cloud: str = Field(min_length=1)

    _validate_cloud_version = field_validator("vuoro_cloud")(_immutable_version)

    @model_validator(mode="after")
    def ordered_and_v1(self) -> "BootstrapServiceCompatibility":
        if self.client_protocol_minimum > self.client_protocol_maximum:
            raise ValueError("service protocol minimum cannot exceed maximum")
        if not (
            self.client_protocol_minimum <= 1 <= self.client_protocol_maximum
        ):
            raise ValueError("bootstrap service must support client protocol v1")
        return self


class BootstrapManifest(_StrictModel):
    """A machine-readable, release-gated compatibility set."""

    schema_version: str = Field(pattern=r"^vuoro-bootstrap-manifest/v1$")
    environment_id: str = Field(min_length=1)
    minimum_python: str = Field(min_length=1)
    packages: BootstrapPackageVersions
    service: BootstrapServiceCompatibility
    release_ready: bool

    _validate_python = field_validator("minimum_python")(_python_version)

    @model_validator(mode="after")
    def must_be_release_ready(self) -> "BootstrapManifest":
        if not self.release_ready:
            raise DiscoveryContractError(
                "bootstrap compatibility set is not release-ready"
            )
        return self


def parse_discovery(payload: Any) -> DiscoveryDocument:
    """Validate an untrusted discovery payload and normalize validation errors."""

    try:
        return DiscoveryDocument.model_validate(payload)
    except DiscoveryContractError:
        raise
    except Exception as error:
        raise DiscoveryContractError(f"invalid Vuoro discovery document: {error}") from error


def parse_bootstrap_manifest(payload: Any) -> BootstrapManifest:
    """Validate an untrusted bootstrap manifest and require release readiness."""

    try:
        return BootstrapManifest.model_validate(payload)
    except DiscoveryContractError:
        raise
    except Exception as error:
        raise DiscoveryContractError(f"invalid Vuoro bootstrap manifest: {error}") from error


__all__ = [
    "BootstrapManifest",
    "BootstrapPackageVersions",
    "BootstrapServiceCompatibility",
    "ClientProtocolRange",
    "DiscoveryContractError",
    "DiscoveryDocument",
    "parse_bootstrap_manifest",
    "parse_discovery",
]

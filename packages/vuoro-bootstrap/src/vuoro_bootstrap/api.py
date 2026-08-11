"""HTTP-only client for the Vuoro Cloud discovery/bootstrap control surface."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from vuoro_client import (
    BootstrapManifest,
    DiscoveryDocument,
    parse_bootstrap_manifest,
    parse_discovery,
)
from vuoro_bootstrap import __version__ as bootstrap_version


class BootstrapError(RuntimeError):
    """The public bootstrap contract could not be completed safely."""


class BootstrapApi:
    """Small, injectable API facade; it never writes local files."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        parsed = urlsplit(endpoint.rstrip("/"))
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise BootstrapError("bootstrap endpoint must be an HTTPS origin")
        self.endpoint = endpoint.rstrip("/")
        self._client = client or httpx.Client(base_url=self.endpoint, timeout=20.0)
        self._owns_client = client is None

    def __enter__(self) -> "BootstrapApi":
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._owns_client:
            self._client.close()

    def discovery(self) -> DiscoveryDocument:
        try:
            response = self._client.get(urljoin(self.endpoint + "/", ".well-known/vuoro"))
            response.raise_for_status()
            document = parse_discovery(response.json())
            configured = urlsplit(self.endpoint)
            advertised = urlsplit(document.api_endpoint)
            if (configured.scheme, configured.netloc) != (advertised.scheme, advertised.netloc):
                raise BootstrapError("discovery api_endpoint is not bound to the requested endpoint")
            return document
        except Exception as error:
            if isinstance(error, BootstrapError):
                raise
            raise BootstrapError(f"Vuoro discovery failed: {error}") from error

    def manifest(self, discovery: DiscoveryDocument) -> BootstrapManifest:
        try:
            response = self._client.get(discovery.bootstrap_manifest)
            response.raise_for_status()
            manifest = parse_bootstrap_manifest(response.json())
            if manifest.environment_id != discovery.environment_id:
                raise BootstrapError("bootstrap manifest environment does not match discovery")
            return manifest
        except Exception as error:
            if isinstance(error, BootstrapError):
                raise
            raise BootstrapError(f"bootstrap manifest failed: {error}") from error

    def create_session(
        self,
        discovery: DiscoveryDocument,
        *,
        repository_hint: dict[str, str],
    ) -> dict[str, Any]:
        payload = {
            "schema_version": "vuoro-bootstrap-request/v1",
            "client_name": "vuoro-bootstrap",
            "client_version": bootstrap_version,
            "requested_action": "commission-repository",
            "repository_hint": repository_hint,
        }
        try:
            response = self._client.post(discovery.bootstrap_endpoint, json=payload)
            response.raise_for_status()
            value = response.json()
        except Exception as error:
            raise BootstrapError(f"bootstrap session creation failed: {error}") from error
        required = {"session_id", "device_code", "user_code", "verification_uri", "expires_in", "interval"}
        if not isinstance(value, dict) or not required <= set(value):
            raise BootstrapError("bootstrap session response is incomplete")
        if not all(isinstance(value[key], str) and value[key] for key in required - {"expires_in", "interval"}):
            raise BootstrapError("bootstrap session response contains invalid identifiers")
        return value

    def exchange(self, discovery: DiscoveryDocument, *, session_id: str, device_code: str) -> dict[str, Any]:
        endpoint = urljoin(self.endpoint + "/", f"api/control/v1/bootstrap/sessions/{session_id}/exchange")
        if urlsplit(endpoint).netloc != urlsplit(discovery.bootstrap_endpoint).netloc:
            raise BootstrapError("bootstrap exchange origin is not discovery-bound")
        try:
            response = self._client.post(endpoint, json={"device_code": device_code})
            response.raise_for_status()
            value = response.json()
        except Exception as error:
            raise BootstrapError(f"bootstrap exchange failed: {error}") from error
        if not isinstance(value, dict) or not isinstance(value.get("token"), str) or not value["token"]:
            raise BootstrapError("bootstrap exchange did not return a token")
        return value

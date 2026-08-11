from __future__ import annotations

import pytest

from vuoro_client.discovery import (
    DiscoveryContractError,
    parse_bootstrap_manifest,
    parse_discovery,
)


DISCOVERY = {
    "schema_version": "vuoro-discovery/v1",
    "environment_id": "vuoro-cloud",
    "api_endpoint": "https://api.vuoro.cloud",
    "bootstrap_endpoint": "https://api.vuoro.cloud/api/control/v1/bootstrap/sessions",
    "activation_endpoint": "https://vuoro.cloud/activate",
    "client_protocol": {"minimum": 1, "maximum": 1},
    "bootstrap_manifest": "https://api.vuoro.cloud/api/control/v1/bootstrap/manifest",
}

MANIFEST = {
    "schema_version": "vuoro-bootstrap-manifest/v1",
    "environment_id": "vuoro-cloud",
    "minimum_python": "3.12",
    "packages": {
        "vuoro-bootstrap": "0.1.0",
        "vuoro-client": "0.1.0",
        "sprintctl": "0.2.22",
    },
    "service": {
        "client_protocol_minimum": 1,
        "client_protocol_maximum": 1,
        "vuoro_cloud": "0.1.0",
    },
    "release_ready": True,
}


def test_discovery_is_strict_and_binds_bootstrap_to_api_origin() -> None:
    document = parse_discovery(DISCOVERY)
    assert document.environment_id == "vuoro-cloud"
    assert document.client_protocol.minimum == 1

    invalid = {**DISCOVERY, "extra": True}
    with pytest.raises(DiscoveryContractError):
        parse_discovery(invalid)

    invalid = {**DISCOVERY, "bootstrap_manifest": "https://other.invalid/manifest"}
    with pytest.raises(DiscoveryContractError, match="origin"):
        parse_discovery(invalid)


def test_bootstrap_manifest_rejects_unreleased_or_incompatible_sets() -> None:
    manifest = parse_bootstrap_manifest(MANIFEST)
    assert manifest.packages.vuoro_client == "0.1.0"

    with pytest.raises(DiscoveryContractError, match="release-ready"):
        parse_bootstrap_manifest({**MANIFEST, "release_ready": False})

    unreleased = {**MANIFEST, "packages": {**MANIFEST["packages"], "sprintctl": "UNRELEASED"}}
    with pytest.raises(DiscoveryContractError, match="immutable"):
        parse_bootstrap_manifest(unreleased)

    development = {**MANIFEST, "packages": {**MANIFEST["packages"], "sprintctl": "0.1.0.dev0"}}
    with pytest.raises(DiscoveryContractError, match="immutable"):
        parse_bootstrap_manifest(development)

    moving_cloud = {**MANIFEST, "service": {**MANIFEST["service"], "vuoro_cloud": "main"}}
    with pytest.raises(DiscoveryContractError, match="immutable"):
        parse_bootstrap_manifest(moving_cloud)


@pytest.mark.parametrize(
    "payload",
    [
        {**DISCOVERY, "api_endpoint": "http://api.vuoro.cloud"},
        {**DISCOVERY, "client_protocol": {"minimum": 2, "maximum": 2}},
        {**DISCOVERY, "bootstrap_endpoint": "https://api.vuoro.cloud/sessions?token=bad"},
    ],
)
def test_discovery_fails_closed_on_unsafe_inputs(payload) -> None:
    with pytest.raises(DiscoveryContractError):
        parse_discovery(payload)

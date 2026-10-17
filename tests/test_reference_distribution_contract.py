"""Consistency checks for the candidate distribution contract, not qualification."""

import json
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _load(relative):
    return tomllib.loads((ROOT / relative).read_text())


def test_reference_distribution_tracks_every_pinned_runtime_descriptor():
    contract = _load("deploy/reference-distribution.toml")
    composition = json.loads(
        (ROOT / contract["composition_manifest"]).read_text()
    )
    pins = {item["lock_id"]: item for item in composition["release_locks"]}
    declared = {item["domain"]: item for item in contract["capability"]}
    descriptors = {item["domain"]: item for item in composition["runtime_descriptors"]}

    assert contract["schema_version"] == "vuoro-reference-distribution/v1"
    assert contract["supported_distribution"] is False
    assert contract["qualification_status"] == "pending"
    assert set(declared) == set(descriptors)

    for domain, descriptor in descriptors.items():
        capability = declared[domain]
        lock = pins[descriptor["lock_id"]]
        assert capability["lock_id"] == descriptor["lock_id"]
        assert capability["distribution"] == lock["distribution"]
        assert capability["pinned_version"] == lock["distribution_version"]
        assert capability["api_version"] == descriptor["api_version"]
        assert capability["schema_version"] == descriptor["schema_version"]
        assert capability["pin_maturity"] == "immutable-release-pinned"
        assert capability["composition_maturity"] == "source-composed"
        assert capability["deployment_maturity"] == "unknown"
        assert capability["user_qualification"] == "not-qualified"
        assert re.fullmatch(r"[0-9a-f]{40}", lock["source_revision"])
        assert re.fullmatch(r"[0-9a-f]{64}", lock["artifact_sha256"])
        assert lock["artifact_url"].startswith("https://")


def test_service_version_and_lifecycle_claims_remain_source_qualified():
    contract = _load("deploy/reference-distribution.toml")
    service = tomllib.loads((ROOT / contract["service_package"]).read_text())
    lifecycle = {item["operation"]: item for item in contract["lifecycle"]}

    assert service["project"]["name"] == contract["service_distribution"]
    assert service["project"]["version"] == contract["service_version"]
    assert contract["image_policy"] == "immutable-digest-required"
    assert set(lifecycle) == {
        "install",
        "upgrade-and-migrate",
        "rollback",
        "backup-and-restore",
        "export-and-import",
        "diagnostics",
        "removal",
    }
    assert all(item["boundary"] for item in lifecycle.values())
    assert all(
        (ROOT / reference.split("#", 1)[0]).is_file()
        for item in lifecycle.values()
        for reference in item["procedure"]
    )
    assert lifecycle["removal"]["source_status"] == "undefined"
    assert contract["support"]["status"] == "not-committed"
    assert contract["support"]["response_sla"] == "none"

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from types import SimpleNamespace

from vuoro_service.composition import (
    CompositionError,
    CompositionManifest,
    create_composed_app,
    load_identities,
    verify_adapter_artifacts,
    _execution_authorizer,
)
from vuoro_service.project_binding import load_project_bindings


ROOT = Path(__file__).parents[1]


def test_checked_in_manifest_pins_all_four_domains() -> None:
    manifest = CompositionManifest.load(ROOT / "composition" / "adapter-pins.json")
    assert {pin.domain for pin in manifest.adapters} == {"work", "execution", "knowledge", "audit"}
    assert all(len(pin.source_revision) == 40 for pin in manifest.adapters)
    assert all(len(pin.artifact_sha256) == 64 for pin in manifest.adapters)


def test_checked_in_adapter_artifact_urls_are_immutable_github_release_wheels() -> None:
    manifest = CompositionManifest.load(ROOT / "composition" / "adapter-pins.json")
    for pin in manifest.adapters:
        assert pin.artifact_url.startswith("https://github.com/")
        assert "/releases/download/" in pin.artifact_url
        assert pin.artifact_url.endswith(".whl")


def test_checked_in_adapter_artifact_urls_are_source_named_or_exact_semver_releases() -> None:
    manifest = CompositionManifest.load(ROOT / "composition" / "adapter-pins.json")
    for pin in manifest.adapters:
        tag = pin.artifact_url.split("/releases/download/", 1)[1].split("/", 1)[0]
        exact_owner_semver = (
            pin.artifact_url.startswith(pin.source_repository.rstrip("/") + "/releases/download/")
            and tag == f"v{pin.distribution_version}"
        )
        assert pin.source_revision[:7] in tag or exact_owner_semver


def test_checked_in_execution_pin_includes_released_contract_companion() -> None:
    pin = CompositionManifest.load(ROOT / "composition" / "adapter-pins.json").pin("execution")
    assert (pin.source_revision, pin.distribution_version, pin.schema_version) == (
        "dd41a9860cf9f07a4776f8279e048d70fd6dbb05", "0.1.14", "actionq-schema/v6")
    assert [(item.distribution, item.distribution_version) for item in pin.dependencies] == [
        ("actionq-contracts", "0.1.1")]


def test_checked_in_work_pin_is_the_reviewed_maintenance_adapter_release() -> None:
    pin = CompositionManifest.load(ROOT / "composition" / "adapter-pins.json").pin("work")
    assert (
        pin.source_revision,
        pin.distribution_version,
        pin.api_version,
        pin.schema_version,
    ) == (
        "b0123169a69e19c3ea3d7ef29b4cc6ed06409d29",
        "0.2.14",
        "work-api/v1",
        "work-schema/v1",
    )
    assert pin.artifact_url.endswith(
        "/vuoro-adapter-v1-b012316/sprintctl-0.2.14-py3-none-any.whl"
    )
    assert pin.artifact_sha256 == (
        "b142a24c20079637b9c5aabe7df2c0df8ded1c290c14ee20326b7ebce005b030"
    )
    assert (pin.adapter_module, pin.register) == (
        "sprintctl.vuoro_adapter",
        "register_work_catalog",
    )


def test_execution_authorizer_is_exact_and_repository_scoped() -> None:
    scoped = SimpleNamespace(authorized_repositories=("agentops", "vuoro"))
    wildcard = SimpleNamespace(authorized_repositories=("*",))
    for pair in (("execution.candidate-action.create", "create"),
                 ("execution.group.manage", "create"),
                 ("execution.group.manage", "update")):
        assert _execution_authorizer(scoped, *pair)
    assert _execution_authorizer(scoped, "execution.dispatch.repo:agentops", "enqueue")
    assert _execution_authorizer(scoped, "execution.dispatch.repo:vuoro", "read")
    assert _execution_authorizer(wildcard, "execution.dispatch.repo:any", "enqueue")
    assert not _execution_authorizer(scoped, "execution.dispatch.repo:kctl", "enqueue")
    assert not _execution_authorizer(scoped, "execution.group.manage", "delete")
    assert not _execution_authorizer(scoped, "execution.candidate-action.create", "update")
    assert not _execution_authorizer(scoped, "execution.anything", "create")


def test_checked_in_project_binding_is_immutable_and_canonical() -> None:
    raw = json.loads(
        (ROOT / "composition" / "project-bindings.json").read_text(encoding="utf-8")
    )
    bindings = load_project_bindings(raw)
    assert len(bindings) == 1
    binding = bindings[0]
    assert binding.project_id == "981b2073-d7af-4c28-bff3-3cf807495fba"
    assert binding.repo_ids == (
        "agentops", "vuoro", "sprintctl", "kctl", "actionq"
    )
    assert binding.source_revision == "8ea94af795862da34e718b1fcc08644d43756205"
    assert binding.source_sha256 == "7ff7c4022017d427ee1e6de648b72aaef2d71056e8abef8042cd22e517da1870"


def test_artifact_verification_fails_closed_on_mismatch(tmp_path: Path) -> None:
    source = ROOT / "composition" / "adapter-pins.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    artifact = tmp_path / "sprintctl-0.2.0-py3-none-any.whl"
    artifact.write_bytes(b"immutable-work-adapter")
    raw["adapters"] = [raw["adapters"][0]]
    raw["adapters"][0]["domain"] = "work"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CompositionError, match="exactly"):
        CompositionManifest.load(path)
    raw["adapters"] = json.loads(source.read_text(encoding="utf-8"))["adapters"]
    raw["adapters"][0]["artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    path.write_text(json.dumps(raw), encoding="utf-8")
    manifest = CompositionManifest.load(path)
    with pytest.raises(CompositionError, match="unavailable"):
        verify_adapter_artifacts(manifest, tmp_path)


def test_identity_registry_is_environment_bound_and_never_accepts_short_tokens(tmp_path: Path) -> None:
    path = tmp_path / "identities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "vuoro-identities/v1",
                "identities": {
                    "x" * 32: {
                        "actor": "test:developer",
                        "environment": "vuoro-dev",
                        "authorities": ["work.read"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    assert load_identities(path, environment="vuoro-dev")
    with pytest.raises(CompositionError, match="not bound"):
        load_identities(path, environment="production")


def test_identity_registry_supports_a_production_environment_binding(tmp_path: Path) -> None:
    path = tmp_path / "identities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "vuoro-identities/v1",
                "identities": {
                    "y" * 32: {
                        "actor": "test:operator",
                        "environment": "vuoro-shared",
                        "authorities": ["work.read"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    assert load_identities(path, environment="vuoro-shared")


def test_identity_registry_requires_repo_ids_for_work_authorities(tmp_path: Path) -> None:
    path = tmp_path / "identities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "vuoro-identities/v1",
                "identities": {
                    "z" * 32: {
                        "actor": "test:worker",
                        "environment": "vuoro-dev",
                        "authorities": ["work:read"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CompositionError, match="repo_ids"):
        load_identities(path, environment="vuoro-dev")


def test_identity_registry_authorizes_explicit_repos_and_the_wildcard(
    tmp_path: Path,
) -> None:
    path = tmp_path / "identities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "vuoro-identities/v1",
                "identities": {
                    "z" * 32: {
                        "actor": "test:worker",
                        "environment": "vuoro-dev",
                        "authorities": ["work:read"],
                        "repo_ids": ["sprintctl", "agentops"],
                    },
                    "y" * 32: {
                        "actor": "test:host",
                        "environment": "vuoro-dev",
                        "authorities": ["work:read"],
                        "repo_ids": ["*"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    resolver = load_identities(path, environment="vuoro-dev")
    scoped = resolver._identities["z" * 32]
    assert scoped.repo_ids == frozenset({"sprintctl", "agentops"})
    assert scoped.authorizes_repo("sprintctl") is True
    assert scoped.authorizes_repo("box") is False

    wildcard = resolver._identities["y" * 32]
    assert wildcard.authorizes_repo("sprintctl") is True
    assert wildcard.authorizes_repo("literally-anything") is True


def test_composition_allows_production_but_rejects_non_deployable_classes() -> None:
    production = {
        "VUORO_ENVIRONMENT_NAME": "vuoro-shared",
        "VUORO_ENVIRONMENT_CLASS": "production",
    }
    with pytest.raises(CompositionError, match="VUORO_COMPOSITION_MANIFEST"):
        create_composed_app(environ=production)
    with pytest.raises(CompositionError, match="deployable environment class"):
        create_composed_app(
            environ={
                "VUORO_ENVIRONMENT_NAME": "vuoro-recovery",
                "VUORO_ENVIRONMENT_CLASS": "recovery",
            }
        )


def test_composition_rejects_environment_record_class_mismatch(tmp_path: Path) -> None:
    record_path = tmp_path / "record.json"
    record_path.write_text(
        json.dumps(
            {
                "schema_version": "environment-record/v1",
                "id": "vuoro-shared",
                "environment_class": "development",
                "revision": 1,
                "roles": [],
                "constraints": [],
                "capabilities": [],
                "runbook_refs": [],
                "identity_bindings": [],
            }
        )
    )
    with pytest.raises(CompositionError, match="does not match"):
        create_composed_app(
            environ={
                "VUORO_ENVIRONMENT_NAME": "vuoro-shared",
                "VUORO_ENVIRONMENT_CLASS": "production",
                "VUORO_ENVIRONMENT_RECORD_PATH": str(record_path),
            }
        )

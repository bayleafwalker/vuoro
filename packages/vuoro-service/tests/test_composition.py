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
    verify_installed_composition,
    _execution_authorizer,
    _bind_work_resource_visibility,
    _load_work_resource_observation_authorizer,
)
from vuoro_service.identity import Identity, InvocationContext
from vuoro_service.project_binding import load_project_bindings


ROOT = Path(__file__).parents[1]


def test_checked_in_manifest_pins_all_four_domains() -> None:
    manifest = CompositionManifest.load(ROOT / "composition" / "adapter-pins.json")
    assert {pin.domain for pin in manifest.adapters} == {"work", "execution", "knowledge", "audit"}
    assert all(len(pin.source_revision) == 40 for pin in manifest.adapters)
    assert all(len(pin.artifact_sha256) == 64 for pin in manifest.adapters)
    assert all("migration_entrypoint" not in descriptor.__dict__ for descriptor in manifest.runtime_descriptors)


def test_v2_composition_fails_closed_on_duplicate_and_orphan_release_locks(
    tmp_path: Path,
) -> None:
    source = ROOT / "composition" / "adapter-pins.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    path = tmp_path / "manifest.json"

    duplicate = raw["release_locks"][0].copy()
    duplicate["lock_id"] = "duplicate-work"
    raw["release_locks"].append(duplicate)
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CompositionError, match="duplicate distributions"):
        CompositionManifest.load(path)

    raw = json.loads(source.read_text(encoding="utf-8"))
    raw["runtime_descriptors"][1]["dependency_lock_ids"] = []
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CompositionError, match="orphan"):
        CompositionManifest.load(path)


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
        "0e8b21325a7fd3d59a989110e61ce80476c51dea", "0.1.19", "actionq-schema/v10")
    assert [(item.distribution, item.distribution_version) for item in pin.dependencies] == [
        ("actionq-contracts", "0.1.1")]
    assert pin.artifact_sha256 == (
        "99449924645fb838ed202f572ccc6da3c96eee0e6b7442a246643fb74f55a4ec"
    )
    assert pin.dependencies[0].source_revision == "0e8b21325a7fd3d59a989110e61ce80476c51dea"


def test_checked_in_work_pin_is_the_maintenance_resource_owner_release() -> None:
    pin = CompositionManifest.load(ROOT / "composition" / "adapter-pins.json").pin("work")
    assert (
        pin.source_revision,
        pin.distribution_version,
        pin.api_version,
        pin.schema_version,
    ) == (
        "159647d80c91fb4d0f7ae2090c7dec413ec91a8f",
        "0.2.22",
        "work-api/v1",
        "work-schema/v1",
    )
    assert pin.artifact_url.endswith(
        "/v0.2.22/sprintctl-0.2.22-py3-none-any.whl"
    )
    assert pin.artifact_sha256 == (
        "bd508ff25f0a586cbcd5fee9a369188361b6f5c54f7a37160ba46e84756a72d8"
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


def test_work_resource_visibility_requires_a_separate_injected_policy() -> None:
    captured = {}

    class Registry:
        def has_resource_kind(self, resource_kind):
            return resource_kind == "work.maintenance-capability"

        def register_resource_visibility(self, resource_kind, guard, **options):
            captured.update(kind=resource_kind, guard=guard, options=options)

    class ScopedApplication:
        def maintenance_resource_visible(self, resource_ref, *, authorized):
            captured["owner_decision"] = (resource_ref, authorized)
            return authorized

    class WorkApplication:
        def _scoped_for(self, repo_id):
            captured["repo_id"] = repo_id
            return ScopedApplication()

    with pytest.raises(CompositionError, match="observation policy is required"):
        _bind_work_resource_visibility(Registry(), WorkApplication(), None)

    policy_calls = []
    _bind_work_resource_visibility(
        Registry(), WorkApplication(),
        lambda context, resource_ref: policy_calls.append(
            (context.identity.actor, context.repo_id, resource_ref)
        ) or context.identity.actor == "allowed",
    )
    identity = Identity(
        actor="denied", environment="vuoro-dev",
        authorities=frozenset({"work:maintenance"}),
        repo_ids=frozenset({"sprintctl"}),
    )
    context = InvocationContext(
        identity=identity, request_id="request", basis_revision=None,
        catalog_revision="revision", idempotency_requirement="not-allowed",
        idempotency_key=None, repo_id="sprintctl",
    )
    reference = "smr1_" + "A" * 43
    assert captured["guard"](reference, context) is False
    assert captured["owner_decision"] == (reference, False)
    assert policy_calls == [("denied", "sprintctl", reference)]


def test_production_observation_policy_is_strict_actor_repo_only(tmp_path) -> None:
    path = tmp_path / "observers.json"
    path.write_text(json.dumps({
        "schema_version": "vuoro-work-resource-observers/v1",
        "grants": [{"actor": "allowed", "repo_ids": ["sprintctl"]}],
    }), encoding="utf-8")
    policy = _load_work_resource_observation_authorizer(path)
    context = lambda actor, repo_id="sprintctl": InvocationContext(
        identity=Identity(
            actor=actor, environment="vuoro-dev",
            authorities=frozenset({"work:maintenance"}),
            repo_ids=frozenset({"sprintctl"}),
        ),
        request_id="request", basis_revision=None, catalog_revision="revision",
        idempotency_requirement="not-allowed", idempotency_key=None,
        repo_id=repo_id,
    )
    assert policy(context("allowed"), "smr1_" + "A" * 43)
    assert not policy(context("denied"), "smr1_" + "A" * 43)
    assert not policy(context("allowed", "foreign"), "smr1_" + "A" * 43)
    with pytest.raises(CompositionError, match="v1 shape"):
        path.write_text('{"schema_version":"vuoro-work-resource-observers/v1","grants":[],"extra":true}')
        _load_work_resource_observation_authorizer(path)


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
    raw["runtime_descriptors"] = [raw["runtime_descriptors"][0]]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CompositionError, match="exactly"):
        CompositionManifest.load(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    raw["release_locks"][0]["artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    path.write_text(json.dumps(raw), encoding="utf-8")
    manifest = CompositionManifest.load(path)
    with pytest.raises(CompositionError, match="unavailable"):
        verify_adapter_artifacts(manifest, tmp_path)


def test_installed_attestation_binds_each_runtime_import_to_the_release_lock(tmp_path: Path, monkeypatch) -> None:
    manifest_path = ROOT / "composition" / "adapter-pins.json"
    manifest = CompositionManifest.load(manifest_path)
    attestation_path = tmp_path / "installed.json"
    attestation_path.write_text(json.dumps({
        "schema_version": "vuoro-installed-composition/v1",
        "verified": True,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "distributions": [
            {
                "lock_id": lock.lock_id,
                "distribution": lock.distribution,
                "expected_version": lock.distribution_version,
                "artifact_sha256": lock.artifact_sha256,
                "installed_files_sha256": f"files:{lock.distribution}",
                "installed_files_count": 3,
            }
            for lock in manifest.release_locks
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(
        "vuoro_service.composition._installed_files_digest",
        lambda name: (f"files:{name}", 3),
    )
    verify_installed_composition(manifest, manifest_path, attestation_path)

    raw = json.loads(attestation_path.read_text(encoding="utf-8"))
    raw["distributions"][0]["installed_files_sha256"] = "tampered"
    attestation_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CompositionError, match="installed files"):
        verify_installed_composition(manifest, manifest_path, attestation_path)


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

from __future__ import annotations

import hashlib
import importlib.util
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
    _execution_completion_connection_factories,
    _bind_work_resource_visibility,
    _load_work_resource_observation_authorizer,
    _load_project_binding_for_composition,
    _load_project_bindings_file,
    _runtime_env,
    _runtime_path,
    _validate_identity_mode,
)
from vuoro_service.identity import Identity, InvocationContext
from vuoro_service.project_binding import load_project_bindings


ROOT = Path(__file__).parents[1]


def test_checked_in_manifest_pins_all_four_domains() -> None:
    manifest = CompositionManifest.load(ROOT / "composition" / "adapter-pins.json")
    assert {pin.domain for pin in manifest.adapters} == {"work", "execution", "knowledge", "audit"}
    assert all(len(pin.source_revision) == 40 for pin in manifest.adapters)
    assert all(len(pin.artifact_sha256) == 64 for pin in manifest.adapters)
    assert all(pin.lock_kind == "adapter" for pin in manifest.adapters)
    assert manifest.pin("execution").dependencies[0].lock_kind == "owner-dependency"
    assert all("migration_entrypoint" not in descriptor.__dict__ for descriptor in manifest.runtime_descriptors)


def _shared_dependency_manifest() -> dict:
    return json.loads(
        (ROOT / "composition" / "adapter-pins.json").read_text(encoding="utf-8")
    )


def test_shared_dependency_is_reusable_and_fetched_and_attested_once(tmp_path: Path) -> None:
    raw = _shared_dependency_manifest()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    manifest = CompositionManifest.load(path)
    assert [lock.lock_id for lock in manifest.release_locks].count("vuoro-schema-runtime") == 1
    assert sum(
        "vuoro-schema-runtime" in descriptor.dependency_lock_ids
        for descriptor in manifest.runtime_descriptors
    ) == 3

    fetch_spec = importlib.util.spec_from_file_location(
        "fetch_pins", ROOT.parents[1] / "scripts" / "fetch_pinned_adapters.py"
    )
    assert fetch_spec and fetch_spec.loader
    fetcher = importlib.util.module_from_spec(fetch_spec)
    fetch_spec.loader.exec_module(fetcher)
    fetched = fetcher.artifact_pins(raw)
    assert [lock_id for lock_id, _ in fetched].count("vuoro-schema-runtime") == 1

    attest_spec = importlib.util.spec_from_file_location(
        "attest_composition", ROOT.parents[1] / "scripts" / "attest_installed_composition.py"
    )
    assert attest_spec and attest_spec.loader
    attester = importlib.util.module_from_spec(attest_spec)
    attest_spec.loader.exec_module(attester)
    assert [entry["lock_id"] for entry in attester._pinned(raw)].count("vuoro-schema-runtime") == 1


def test_runtime_and_fetcher_share_v3_dependency_policy(tmp_path: Path) -> None:
    raw = _shared_dependency_manifest()
    shared_runtime = next(
        lock for lock in raw["release_locks"] if lock["lock_id"] == "vuoro-schema-runtime"
    )
    shared_runtime["source_repository"] = "https://github.com/example/vuoro"
    shared_runtime["artifact_url"] = "https://github.com/example/vuoro/releases/download/vuoro-schema-runtime-v0.1.0/vuoro_schema_runtime-0.1.0-py3-none-any.whl"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CompositionError, match="canonical Vuoro"):
        CompositionManifest.load(path)
    fetch_spec = importlib.util.spec_from_file_location(
        "fetch_pins", ROOT.parents[1] / "scripts" / "fetch_pinned_adapters.py"
    )
    assert fetch_spec and fetch_spec.loader
    fetcher = importlib.util.module_from_spec(fetch_spec)
    fetch_spec.loader.exec_module(fetcher)
    with pytest.raises(SystemExit, match="canonical Vuoro"):
        fetcher.artifact_pins(raw)


@pytest.mark.parametrize("mutation, message", [
    (lambda raw: raw["release_locks"][0].update(lock_kind="shared-dependency"), "primary"),
    (lambda raw: raw["release_locks"][2].update(lock_kind="adapter"), "adapter"),
    (lambda raw: raw["release_locks"][2].update(source_repository="https://github.com/example/actionq", artifact_url="https://github.com/example/actionq/releases/download/vuoro-adapter-v1-0e8b213/actionq_contracts-0.1.1-py3-none-any.whl"), "same owner"),
    (lambda raw: raw["release_locks"][0].update(artifact_sha256="x" * 64), "artifact"),
])
def test_attester_rejects_the_same_v3_policy_mutations_as_runtime(
    mutation, message: str,
) -> None:
    raw = json.loads(
        (ROOT / "composition" / "adapter-pins.json").read_text(encoding="utf-8")
    )
    mutation(raw)
    spec = importlib.util.spec_from_file_location(
        "attest_composition", ROOT.parents[1] / "scripts" / "attest_installed_composition.py"
    )
    assert spec and spec.loader
    attester = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(attester)
    with pytest.raises(SystemExit, match=message):
        attester._pinned(raw)


def test_v3_composition_fails_closed_on_duplicate_and_orphan_release_locks(
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
    with pytest.raises(CompositionError, match="owner dependency|orphan"):
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
            and tag in {
                f"v{pin.distribution_version}",
                f"{pin.distribution}-v{pin.distribution_version}",
            }
        )
        assert pin.source_revision[:7] in tag or exact_owner_semver


def test_checked_in_execution_pin_includes_released_contract_companion() -> None:
    pin = CompositionManifest.load(ROOT / "composition" / "adapter-pins.json").pin("execution")
    assert (pin.source_revision, pin.distribution_version, pin.schema_version) == (
        "183c0d79fe98e65e4d3d200563aaa7c903366b81", "0.1.22", "actionq-schema/v11")
    assert pin.artifact_url.endswith("/v0.1.22/actionq-0.1.22-py3-none-any.whl")
    assert pin.artifact_sha256 == (
        "5ffce20b2e9b53305a522b25f8504081442311392b025ea220fc8792e8e50bd2"
    )
    assert [(item.lock_id, item.lock_kind, item.distribution, item.distribution_version)
            for item in pin.dependencies] == [
        ("execution-contracts", "owner-dependency", "actionq-contracts", "0.1.1"),
        ("vuoro-adapter-kit", "shared-dependency", "vuoro-adapter-kit", "0.1.0"),
        ("vuoro-schema-runtime", "shared-dependency", "vuoro-schema-runtime", "0.1.0"),
    ]
    assert pin.dependencies[0].source_revision == "0e8b21325a7fd3d59a989110e61ce80476c51dea"
    assert pin.dependencies[1].source_revision == "a002e503dc1fa2f04858b04b581f5fcdfa0e7f3c"
    assert pin.dependencies[2].source_revision == "a002e503dc1fa2f04858b04b581f5fcdfa0e7f3c"


def test_checked_in_work_pin_is_the_maintenance_resource_owner_release() -> None:
    pin = CompositionManifest.load(ROOT / "composition" / "adapter-pins.json").pin("work")
    assert (
        pin.source_revision,
        pin.distribution_version,
        pin.api_version,
        pin.schema_version,
    ) == (
        "75fd4a7bc01472f941c923444cabe6451bb1afd0",
        "0.2.24",
        "work-api/v1",
        "work-schema/v1",
    )
    assert pin.artifact_url.endswith(
        "/v0.2.24/sprintctl-0.2.24-py3-none-any.whl"
    )
    assert pin.artifact_sha256 == (
        "30fe8d8e81b397f8f34c05b4f615d9cd7570e2a3acd0b26b94f2c4e35d38776c"
    )
    assert [
        (item.lock_id, item.lock_kind, item.distribution, item.distribution_version)
        for item in pin.dependencies
    ] == [("vuoro-adapter-kit", "shared-dependency", "vuoro-adapter-kit", "0.1.0")]
    assert (pin.adapter_module, pin.register) == (
        "sprintctl.vuoro_adapter",
        "register_work_catalog",
    )


def test_checked_in_knowledge_pin_is_kctl_013_with_released_shared_dependencies() -> None:
    manifest = CompositionManifest.load(ROOT / "composition" / "adapter-pins.json")
    pin = manifest.pin("knowledge")
    assert (
        pin.source_repository,
        pin.source_revision,
        pin.distribution,
        pin.distribution_version,
        pin.artifact_url,
        pin.artifact_sha256,
    ) == (
        "https://github.com/bayleafwalker/kctl",
        "f5c5483b0825219ad90488276b56d143b64f01ad",
        "kctl",
        "0.1.3",
        "https://github.com/bayleafwalker/kctl/releases/download/kctl-v0.1.3/kctl-0.1.3-py3-none-any.whl",
        "789b5aadfc4c31171d574c76b79af9999b08b5cf212969cefc8504eb2e99e43d",
    )
    assert (pin.adapter_module, pin.register, pin.api_version, pin.schema_version) == (
        "kctl.vuoro",
        "register_operations",
        "knowledge/v1",
        "knowledge-schema/v1",
    )
    assert [(dependency.lock_id, dependency.lock_kind, dependency.distribution,
             dependency.source_revision, dependency.artifact_url,
             dependency.artifact_sha256, dependency.distribution_version)
            for dependency in pin.dependencies] == [
        (
            "vuoro-adapter-kit",
            "shared-dependency",
            "vuoro-adapter-kit",
            "a002e503dc1fa2f04858b04b581f5fcdfa0e7f3c",
            "https://github.com/bayleafwalker/vuoro/releases/download/vuoro-adapter-kit-v0.1.0/vuoro_adapter_kit-0.1.0-py3-none-any.whl",
            "0037898a4c9f01720a42302365b0172ecd203732070326ea2abdf549a44bf0c2",
            "0.1.0",
        ),
        (
            "vuoro-schema-runtime",
            "shared-dependency",
            "vuoro-schema-runtime",
            "a002e503dc1fa2f04858b04b581f5fcdfa0e7f3c",
            "https://github.com/bayleafwalker/vuoro/releases/download/vuoro-schema-runtime-v0.1.0/vuoro_schema_runtime-0.1.0-py3-none-any.whl",
            "b66c9357c99aa9e1a7353991ce54105a8621958ecfac47f8c121d80b90b77912",
            "0.1.0",
        ),
    ]


def test_checked_in_audit_pin_is_auditctl_012_with_released_shared_dependencies() -> None:
    manifest = CompositionManifest.load(ROOT / "composition" / "adapter-pins.json")
    pin = manifest.pin("audit")
    assert (
        pin.source_repository,
        pin.source_revision,
        pin.distribution,
        pin.distribution_version,
        pin.artifact_url,
        pin.artifact_sha256,
    ) == (
        "https://github.com/bayleafwalker/auditctl",
        "613103644f2749a4d0f9ae5cb6913795fa7647a6",
        "auditctl",
        "0.1.2",
        "https://github.com/bayleafwalker/auditctl/releases/download/auditctl-v0.1.2/auditctl-0.1.2-py3-none-any.whl",
        "b76d9d7aab727c77a7dcfcdc4e5de423b61a07c8f89369101347a4dc6eaf33d1",
    )
    assert (pin.adapter_module, pin.register, pin.api_version, pin.schema_version) == (
        "auditctl.vuoro_adapter",
        "VuoroAuditAdapter.register",
        "audit/v1",
        "audit-schema/v1",
    )
    assert [(dependency.lock_id, dependency.lock_kind, dependency.distribution,
             dependency.source_revision, dependency.artifact_url,
             dependency.artifact_sha256, dependency.distribution_version)
            for dependency in pin.dependencies] == [
        (
            "vuoro-adapter-kit",
            "shared-dependency",
            "vuoro-adapter-kit",
            "a002e503dc1fa2f04858b04b581f5fcdfa0e7f3c",
            "https://github.com/bayleafwalker/vuoro/releases/download/vuoro-adapter-kit-v0.1.0/vuoro_adapter_kit-0.1.0-py3-none-any.whl",
            "0037898a4c9f01720a42302365b0172ecd203732070326ea2abdf549a44bf0c2",
            "0.1.0",
        ),
        (
            "vuoro-schema-runtime",
            "shared-dependency",
            "vuoro-schema-runtime",
            "a002e503dc1fa2f04858b04b581f5fcdfa0e7f3c",
            "https://github.com/bayleafwalker/vuoro/releases/download/vuoro-schema-runtime-v0.1.0/vuoro_schema_runtime-0.1.0-py3-none-any.whl",
            "b66c9357c99aa9e1a7353991ce54105a8621958ecfac47f8c121d80b90b77912",
            "0.1.0",
        ),
    ]


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


def test_schema_v11_requires_explicit_distinct_completion_dsns(monkeypatch) -> None:
    execution_pin = CompositionManifest.load(
        ROOT / "composition" / "adapter-pins.json"
    ).pin("execution")
    with pytest.raises(CompositionError, match="VUORO_EXECUTION_COMPLETION_INGEST_DSN"):
        _execution_completion_connection_factories(
            execution_pin=execution_pin,
            execution_runtime_dsn="postgresql://runtime/db",
            environ={},
        )
    with pytest.raises(CompositionError, match="distinct from the execution runtime"):
        _execution_completion_connection_factories(
            execution_pin=execution_pin,
            execution_runtime_dsn="postgresql://runtime/db",
            environ={
                "VUORO_EXECUTION_COMPLETION_INGEST_DSN": "postgresql://runtime/db",
                "VUORO_EXECUTION_COMPLETION_READ_DSN": "postgresql://read/db",
            },
        )
    with pytest.raises(CompositionError, match="ingest and read DSNs must be distinct"):
        _execution_completion_connection_factories(
            execution_pin=execution_pin,
            execution_runtime_dsn="postgresql://runtime/db",
            environ={
                "VUORO_EXECUTION_COMPLETION_INGEST_DSN": "postgresql://completion/db",
                "VUORO_EXECUTION_COMPLETION_READ_DSN": "postgresql://completion/db",
            },
        )
    seen: list[str] = []
    monkeypatch.setattr(
        "vuoro_service.composition._pg_connection_factory",
        lambda dsn: seen.append(dsn) or (lambda: None),
    )
    factories = _execution_completion_connection_factories(
        execution_pin=execution_pin,
        execution_runtime_dsn="postgresql://runtime/db",
        environ={
            "VUORO_EXECUTION_COMPLETION_INGEST_DSN": "postgresql://ingest/db",
            "VUORO_EXECUTION_COMPLETION_READ_DSN": "postgresql://read/db",
        },
    )
    assert factories is not None and len(factories) == 2
    assert seen == ["postgresql://ingest/db", "postgresql://read/db"]


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


def test_project_bindings_path_can_be_supplied_by_a_runtime_mount() -> None:
    mounted = "/etc/vuoro/bindings/bindings.json"
    assert _runtime_path(
        "VUORO_PROJECT_BINDINGS_FILE",
        {},
        default="/opt/vuoro/composition/project-bindings.json",
        mounted_path=mounted,
    ) == Path("/opt/vuoro/composition/project-bindings.json")
    assert _runtime_path(
        "VUORO_PROJECT_BINDINGS_FILE",
        {"VUORO_PROJECT_BINDINGS_FILE": mounted},
        default="/opt/vuoro/composition/project-bindings.json",
        mounted_path=mounted,
    ) == Path(mounted)
    with pytest.raises(CompositionError, match="approved Cloud binding mount"):
        _runtime_path(
            "VUORO_PROJECT_BINDINGS_FILE",
            {"VUORO_PROJECT_BINDINGS_FILE": ""},
            default="/opt/vuoro/composition/project-bindings.json",
            mounted_path=mounted,
        )
    with pytest.raises(CompositionError, match="approved Cloud binding mount"):
        _runtime_path(
            "VUORO_PROJECT_BINDINGS_FILE",
            {"VUORO_PROJECT_BINDINGS_FILE": "/tmp/arbitrary.json"},
            default="/opt/vuoro/composition/project-bindings.json",
            mounted_path=mounted,
        )
    gateway_key = "/etc/vuoro/identity/gateway-public.pem"
    assert _runtime_path(
        "VUORO_GATEWAY_PUBLIC_KEY_FILE",
        {"VUORO_GATEWAY_PUBLIC_KEY_FILE": gateway_key},
        default=gateway_key,
        mounted_path=gateway_key,
        mount_label="approved Cloud gateway key mount",
    ) == Path(gateway_key)
    with pytest.raises(CompositionError, match="approved Cloud gateway key mount"):
        _runtime_path(
            "VUORO_GATEWAY_PUBLIC_KEY_FILE",
            {"VUORO_GATEWAY_PUBLIC_KEY_FILE": "/tmp/gateway.pem"},
            default=gateway_key,
            mounted_path=gateway_key,
            mount_label="approved Cloud gateway key mount",
        )


def test_cloud_rendered_environment_and_dsn_aliases_are_compatible() -> None:
    assert _runtime_env(
        "VUORO_ENVIRONMENT_NAME",
        {"VUORO_ENVIRONMENT": "vuoro-cloud"},
        aliases=("VUORO_ENVIRONMENT",),
    ) == "vuoro-cloud"
    assert _runtime_env(
        "VUORO_WORK_RUNTIME_DSN",
        {"VUORO_WORK_DSN": "postgresql://work"},
        aliases=("VUORO_WORK_DSN",),
    ) == "postgresql://work"


def _hosted_binding(*, projects: list[dict] | None = None) -> dict:
    return {
        "schema_version": "vuoro-project-bindings/v1",
        "environment": "vuoro-cloud-ws-01k111111111",
        "projects": (
            projects
            if projects is not None
            else [
                {
                    "project_id": "01K22222222222222222222222",
                    "descriptor_digest": "sha256:" + "a" * 64,
                    "repositories": [
                        {
                            "repo_id": "repo-a",
                            "git_remote": "https://github.com/example/repo-a",
                            "commit_sha": "b" * 40,
                        }
                    ],
                }
            ]
        ),
    }


_HOSTED_ENVIRONMENT = "vuoro-cloud-ws-01k111111111"


def test_startup_loader_accepts_the_cloud_document_and_default_file(tmp_path: Path) -> None:
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps(_hosted_binding()), encoding="utf-8")
    loaded = _load_project_binding_for_composition(
        path, expected_environment=_HOSTED_ENVIRONMENT
    )
    assert loaded.project_id == "01K22222222222222222222222"
    assert loaded.repo_ids == ("repo-a",)
    assert loaded.members[0].commit_sha == "b" * 40
    assert loaded.environment == _HOSTED_ENVIRONMENT
    assert loaded.descriptor_digest == "sha256:" + "a" * 64


def test_startup_loader_rejects_a_hosted_environment_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps(_hosted_binding()), encoding="utf-8")
    with pytest.raises(CompositionError, match="does not match"):
        _load_project_binding_for_composition(
            path, expected_environment="vuoro-cloud-ws-other"
        )


def test_startup_loader_rejects_hosted_binding_without_expected_environment(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps(_hosted_binding()), encoding="utf-8")
    with pytest.raises(CompositionError, match="required for hosted"):
        _load_project_binding_for_composition(path)


@pytest.mark.parametrize("expected", ["", " leading", "trailing ", "bad\x00name"])
def test_startup_loader_rejects_invalid_expected_environment(
    tmp_path: Path, expected: str
) -> None:
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps(_hosted_binding()), encoding="utf-8")
    with pytest.raises(CompositionError, match="non-empty environment"):
        _load_project_binding_for_composition(
            path, expected_environment=expected
        )


def test_startup_loader_preserves_canonical_default_without_hosted_environment(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bindings.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "vuoro-project-bindings/v1",
                "bindings": [
                    {
                        "project_id": "981b2073-d7af-4c28-bff3-3cf807495fba",
                        "home_repo": "repo-a",
                        "members": [{"repo_id": "repo-a"}],
                        "source_repository": "https://github.com/example/repo-a",
                        "source_revision": "a" * 40,
                        "source_path": "project.toml",
                        "source_sha256": "b" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = _load_project_binding_for_composition(path)
    assert loaded.environment is None
    assert loaded.home_repo == "repo-a"


def test_hosted_binding_cannot_fall_back_to_static_identity_mode() -> None:
    hosted = SimpleNamespace(environment=_HOSTED_ENVIRONMENT)
    local = SimpleNamespace(environment=None)

    with pytest.raises(CompositionError, match="require gateway assertion"):
        _validate_identity_mode(hosted, gateway_key_configured=False)
    _validate_identity_mode(hosted, gateway_key_configured=True)
    _validate_identity_mode(local, gateway_key_configured=False)


def test_startup_loader_rejects_many_projects(tmp_path: Path) -> None:
    path = tmp_path / "bindings.json"
    documents = _hosted_binding()
    second = dict(documents["projects"][0])
    second["project_id"] = "01K33333333333333333333333"
    documents["projects"].append(second)
    path.write_text(json.dumps(documents), encoding="utf-8")
    with pytest.raises(CompositionError, match="exactly one"):
        _load_project_binding_for_composition(
            path, expected_environment=_HOSTED_ENVIRONMENT
        )


@pytest.mark.parametrize(
    "contents", ["", "{", b"{\xff"], ids=["empty", "bad-json", "bad-utf8"]
)
def test_startup_loader_normalizes_json_and_utf8_failures(tmp_path: Path, contents) -> None:
    path = tmp_path / "bindings.json"
    path.write_bytes(contents if isinstance(contents, bytes) else contents.encode())
    with pytest.raises(CompositionError, match="cannot load mounted"):
        _load_project_bindings_file(path)


def test_startup_loader_rejects_an_empty_cloud_project_list(tmp_path: Path) -> None:
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps(_hosted_binding(projects=[])), encoding="utf-8")
    with pytest.raises(CompositionError, match="exactly one"):
        _load_project_binding_for_composition(
            path, expected_environment=_HOSTED_ENVIRONMENT
        )


def test_startup_loader_rejects_directory_unreadable_and_broken_link(
    tmp_path: Path, monkeypatch
) -> None:
    directory = tmp_path / "bindings-dir"
    directory.mkdir()
    with pytest.raises(CompositionError, match="cannot load mounted"):
        _load_project_bindings_file(directory)

    broken = tmp_path / "broken.json"
    broken.symlink_to(tmp_path / "missing.json")
    with pytest.raises(CompositionError, match="cannot load mounted"):
        _load_project_bindings_file(broken)

    unreadable = tmp_path / "unreadable.json"
    unreadable.write_text(json.dumps(_hosted_binding()), encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def deny_read(path: Path) -> bytes:
        if path == unreadable:
            raise PermissionError("permission denied")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", deny_read)
    with pytest.raises(CompositionError, match="cannot load mounted"):
        _load_project_bindings_file(unreadable)


def test_startup_loader_accepts_a_configmap_style_symlink_inside_trusted_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "etc" / "vuoro"
    binding_dir = root / "bindings"
    data_dir = root / "..data"
    binding_dir.mkdir(parents=True)
    data_dir.mkdir()
    target = data_dir / "bindings.json"
    target.write_text(json.dumps(_hosted_binding()), encoding="utf-8")
    mounted = binding_dir / "bindings.json"
    mounted.symlink_to(Path("..") / "..data" / "bindings.json")
    loaded = _load_project_binding_for_composition(
        mounted, trusted_root=root, expected_environment=_HOSTED_ENVIRONMENT
    )
    assert loaded.project_id == "01K22222222222222222222222"

    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_hosted_binding()), encoding="utf-8")
    mounted.unlink()
    mounted.symlink_to(outside)
    with pytest.raises(CompositionError, match="trusted ConfigMap root"):
        _load_project_bindings_file(mounted, trusted_root=root)


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
                "lock_kind": lock.lock_kind,
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

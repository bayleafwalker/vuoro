"""The v4 record types load, and malformed ones do not.

Shape only. Everything that reasons across records -- at most one exclusive
binding per scope, closure completeness, conformance evidence, frozen operation
hashes, uniform construction -- is the validator's (freeze §4, rules 1-9) and
lands here later as the tests the freeze's falsifiers name. Nothing in this
module claims to falsify a marked claim yet; every one of them is still a
declared gap, and ``tests/test_falsifier_coverage.py`` is what says so.

At repository root rather than beside the package's own composition tests
because the freeze names ``tests/test_composition_v4.py`` in every
``planned_test``, and the ported gate resolves test references against that
path. Keeping the loader tests and the claim tests in one file is also the
honest arrangement: they are one deliverable landing in two increments.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from vuoro_service.composition_v4 import (
    Adapter,
    AuthorityBinding,
    CapabilityContract,
    CompositionProfile,
    CompositionV4Error,
    DeploymentClosure,
    Provider,
    SupportManifest,
)


def _contract(**overrides) -> dict:
    return {
        "capability_id": "execution/v1",
        "cardinality": "exclusive",
        "required": True,
        "scope_kind": "global",
        "frozen": True,
        "conformance": "operation-hashes",
        "owner": "actionq",
        **overrides,
    }


def _provider(**overrides) -> dict:
    return {
        "provider_id": "actionq-execution",
        "release_unit": "actionq-execution",
        "artifact_kind": "wheel",
        "role": "adapter",
        "source_repository": "https://github.com/bayleafwalker/actionq",
        "artifact": {
            "distribution": "actionq",
            "distribution_version": "0.1.26",
            "artifact_sha256": "a" * 64,
            "artifact_url": "https://github.com/bayleafwalker/actionq/releases/download/v0.1.26/actionq-0.1.26-py3-none-any.whl",
        },
        "capabilities": ["execution/v1"],
        **overrides,
    }


def _adapter(**overrides) -> dict:
    return {
        "adapter_id": "execution-adapter",
        "provider_id": "actionq-execution",
        "module": "actionq.vuoro",
        "build": "build",
        "register": "register_operations",
        **overrides,
    }


def _binding(**overrides) -> dict:
    return {
        "capability_id": "execution/v1",
        "scope_kind": "global",
        "provider_id": "actionq-execution",
        "adapter_id": "execution-adapter",
        **overrides,
    }


def _profile(**overrides) -> dict:
    return {
        "schema_version": "vuoro-composition/v4",
        "profile": "shared",
        "providers": [_provider()],
        "adapters": [_adapter()],
        "bindings": [_binding()],
        **overrides,
    }


def _support(**overrides) -> dict:
    return {
        "schema_version": "vuoro-support-manifest/v1",
        "contracts": [_contract()],
        **overrides,
    }


# --- capability contracts and the support manifest ---------------------------


def test_support_manifest_derives_the_required_set_from_its_records() -> None:
    manifest = SupportManifest.from_dict(_support(contracts=[
        _contract(),
        _contract(capability_id="secret.lease/v1", required=False, scope_kind="environment",
                  frozen=False, conformance="probe", owner="openbao"),
    ]))
    assert manifest.required_capabilities == {"execution/v1"}
    assert manifest.contract("secret.lease/v1").cardinality == "exclusive"


def test_optional_is_independent_of_cardinality() -> None:
    """The distinction an earlier draft's four mutually exclusive values could not express."""
    manifest = SupportManifest.from_dict(_support(contracts=[
        _contract(capability_id="telemetry.export/v1", cardinality="multi", required=False,
                  frozen=False, conformance="probe", owner="otel"),
    ]))
    contract = manifest.contract("telemetry.export/v1")
    assert (contract.cardinality, contract.required) == ("multi", False)


def test_a_contract_owner_may_be_unsettled() -> None:
    """`federation.principal/v1`'s owner is proposed, not pinned, and null says so."""
    contract = CapabilityContract.from_dict(
        _contract(capability_id="federation.principal/v1", owner=None)
    )
    assert contract.owner is None


@pytest.mark.parametrize("capability_id", ["execution", "execution/1", "Execution/v1", "execution/v0"])
def test_capability_ids_carry_the_v3_api_version_syntax(capability_id: str) -> None:
    with pytest.raises(CompositionV4Error, match="capability ids"):
        CapabilityContract.from_dict(_contract(capability_id=capability_id))


@pytest.mark.parametrize("field, value", [
    ("cardinality", "singleton"),
    ("scope_kind", "cluster"),
    ("conformance", "trust-me"),
])
def test_contract_enumerations_are_closed(field: str, value: str) -> None:
    with pytest.raises(CompositionV4Error, match=field):
        CapabilityContract.from_dict(_contract(**{field: value}))


def test_a_contract_cannot_carry_an_undeclared_field() -> None:
    """Including `plane`: the freeze's table is documentation, never a record."""
    with pytest.raises(CompositionV4Error, match="unknown fields"):
        CapabilityContract.from_dict(_contract(plane="coordination"))


def test_support_manifest_rejects_duplicate_and_foreign_schemas() -> None:
    with pytest.raises(CompositionV4Error, match="duplicate capability ids"):
        SupportManifest.from_dict(_support(contracts=[_contract(), _contract()]))
    with pytest.raises(CompositionV4Error, match="schema_version"):
        SupportManifest.from_dict(_support(schema_version="vuoro-composition/v3"))


# --- providers ---------------------------------------------------------------


@pytest.mark.parametrize("artifact_kind, artifact", [
    ("image", {"image_reference": "registry.example/openbao", "image_digest": "sha256:" + "b" * 64}),
    ("chart", {"chart_repository": "https://charts.example/", "chart": "openbao",
               "chart_version": "1.2.3", "chart_digest": "c" * 64,
               "values_digest": "d" * 64}),
    ("binary", {"artifact_url": "https://example.invalid/tool", "artifact_sha256": "e" * 64}),
])
def test_a_provider_is_identified_by_artifact_not_by_distribution(
    artifact_kind: str, artifact: dict
) -> None:
    provider = Provider.from_dict(_provider(
        provider_id="external-provider", artifact_kind=artifact_kind,
        role="external", artifact=artifact, capabilities=["secret.lease/v1"],
    ))
    assert provider.artifact == artifact


def test_a_chart_without_a_values_digest_is_not_an_identity() -> None:
    """Mutable values are not a freeze, so the digest is part of the identity."""
    with pytest.raises(CompositionV4Error, match="chart artifact is identified by"):
        Provider.from_dict(_provider(artifact_kind="chart", artifact={
            "chart_repository": "https://charts.example/", "chart": "openbao",
            "chart_version": "1.2.3", "chart_digest": "c" * 64,
        }))


def test_digest_fields_must_be_digests() -> None:
    with pytest.raises(CompositionV4Error, match="artifact_sha256"):
        Provider.from_dict(_provider(artifact={
            "distribution": "actionq", "distribution_version": "0.1.26",
            "artifact_sha256": "not-a-digest",
            "artifact_url": "https://github.com/bayleafwalker/actionq/releases/download/v0.1.26/actionq-0.1.26-py3-none-any.whl",
        }))


def test_one_repository_may_ship_two_providers() -> None:
    """The normal case: separate release units, same source repository."""
    profile = CompositionProfile.from_dict(_profile(
        providers=[
            _provider(),
            _provider(provider_id="actionq-federation", release_unit="actionq-federation",
                      capabilities=["federation.grant/v1"],
                      artifact={"distribution": "actionq", "distribution_version": "0.1.26",
                                "artifact_sha256": "f" * 64,
                                "artifact_url": "https://github.com/bayleafwalker/actionq/releases/download/v0.1.26/actionq_federation-0.1.26-py3-none-any.whl"}),
        ],
    ))
    units = {provider.release_unit for provider in profile.providers}
    assert len(units) == 2
    assert len({provider.source_repository for provider in profile.providers}) == 1


def test_a_provider_cannot_depend_on_itself() -> None:
    with pytest.raises(CompositionV4Error, match="depend on itself"):
        Provider.from_dict(_provider(dependencies=["actionq-execution"]))


def test_the_v3_dependency_edge_survives_as_a_declared_closure() -> None:
    """`dependency_lock_ids` is what bound the non-adapter locks into the composition."""
    profile = CompositionProfile.from_dict(_profile(providers=[
        _provider(dependencies=["vuoro-adapter-kit"]),
        _provider(provider_id="vuoro-adapter-kit", release_unit="vuoro-adapter-kit",
                  role="shared-dependency", capabilities=[],
                  source_repository="https://github.com/bayleafwalker/vuoro",
                  artifact={"distribution": "vuoro-adapter-kit", "distribution_version": "0.1.0",
                            "artifact_sha256": "1" * 64,
                            "artifact_url": "https://github.com/bayleafwalker/vuoro/releases/download/v0.1.0/vuoro_adapter_kit-0.1.0-py3-none-any.whl"}),
    ]))
    assert profile.provider("actionq-execution").dependencies == ("vuoro-adapter-kit",)


def test_a_dependency_must_resolve_to_a_declared_provider() -> None:
    with pytest.raises(CompositionV4Error, match="unknown dependency"):
        CompositionProfile.from_dict(_profile(providers=[_provider(dependencies=["ghost"])]))


# --- adapters ----------------------------------------------------------------


def test_an_adapter_has_nowhere_to_declare_canonical_state() -> None:
    """Structural, not a validator rule: an adapter with state is a provider.

    The same declaration on a provider record is accepted, which is the half
    that makes this a boundary rather than a blanket prohibition.
    """
    with pytest.raises(CompositionV4Error, match="unknown fields"):
        Adapter.from_dict(_adapter(schema_version="audit-schema/v1"))
    assert Provider.from_dict(
        _provider(closure={"schema_version": "actionq-schema/v12"})
    ).closure.schema_version == "actionq-schema/v12"


@pytest.mark.parametrize("declared", [
    {"module": "actionq.vuoro"},
    {"module": "actionq.vuoro", "register": "register_operations"},
    {"build": "build", "register": "register_operations"},
])
def test_the_three_entrypoints_are_declared_together(declared: dict) -> None:
    """A module with no build is the shape uniform construction abolishes."""
    with pytest.raises(CompositionV4Error, match="declared together or not at all"):
        Adapter.from_dict({"adapter_id": "execution-adapter",
                           "provider_id": "actionq-execution", **declared})


def test_an_external_adapter_needs_no_module() -> None:
    adapter = Adapter.from_dict({"adapter_id": "openbao-shim", "provider_id": "openbao"})
    assert (adapter.module, adapter.build, adapter.register) == (None, None, None)


def test_an_adapter_pins_its_runtime_settings_by_name() -> None:
    """What `build(RuntimeConfiguration)` receives is declared, not compiled in."""
    adapter = Adapter.from_dict(_adapter(runtime_settings={
        "dsn": "VUORO_EXECUTION_RUNTIME_DSN", "schema": "VUORO_EXECUTION_SCHEMA",
    }))
    assert adapter.runtime_settings["schema"] == "VUORO_EXECUTION_SCHEMA"


def test_an_adapter_references_exactly_one_provider() -> None:
    with pytest.raises(CompositionV4Error, match="unknown provider"):
        CompositionProfile.from_dict(_profile(adapters=[_adapter(provider_id="ghost")]))


# --- authority bindings ------------------------------------------------------


def test_a_binding_scope_is_a_kind_and_an_optional_instance() -> None:
    """Global scopes have no instance; environment scopes name the one they bind."""
    assert AuthorityBinding.from_dict(_binding()).scope == ("global", None)
    scoped = AuthorityBinding.from_dict(
        _binding(scope_kind="environment", scope_instance="production")
    )
    assert scoped.scope == ("environment", "production")


def test_coupling_is_declared_on_the_binding() -> None:
    """The explicit declaration that replaces v3's structural refusal to share."""
    binding = AuthorityBinding.from_dict(_binding(coupled_with=["federation.grant/v1"]))
    assert binding.coupled_with == ("federation.grant/v1",)


def test_a_binding_must_resolve_to_a_declared_provider_and_adapter() -> None:
    with pytest.raises(CompositionV4Error, match="unknown provider"):
        CompositionProfile.from_dict(_profile(bindings=[_binding(provider_id="ghost")]))
    with pytest.raises(CompositionV4Error, match="unknown adapter"):
        CompositionProfile.from_dict(_profile(bindings=[_binding(adapter_id="ghost")]))


def test_bindings_are_queryable_per_capability() -> None:
    profile = CompositionProfile.from_dict(_profile(bindings=[
        _binding(capability_id="telemetry.export/v1"),
        _binding(capability_id="telemetry.export/v1", scope_kind="environment",
                 scope_instance="production"),
    ]))
    assert len(profile.bindings_for("telemetry.export/v1")) == 2
    assert profile.bound_capabilities == {"telemetry.export/v1"}


# --- profiles ----------------------------------------------------------------


@pytest.mark.parametrize("name", ["local", "shared", "served", "cloud"])
def test_the_four_profiles_are_deployment_shapes(name: str) -> None:
    assert CompositionProfile.from_dict(_profile(profile=name)).profile == name


def test_an_unknown_profile_name_is_rejected() -> None:
    with pytest.raises(CompositionV4Error, match="is not one of"):
        CompositionProfile.from_dict(_profile(profile="development"))


def test_duplicate_record_ids_are_rejected() -> None:
    with pytest.raises(CompositionV4Error, match="duplicate provider ids"):
        CompositionProfile.from_dict(_profile(providers=[_provider(), _provider()]))
    with pytest.raises(CompositionV4Error, match="duplicate adapter ids"):
        CompositionProfile.from_dict(_profile(adapters=[_adapter(), _adapter()]))


def test_migrated_from_names_the_predecessor_by_manifest_bytes() -> None:
    """v3's only whole-profile identity, and the one its attestation already binds."""
    profile = CompositionProfile.from_dict(_profile(migrated_from={
        "schema_version": "vuoro-composition/v3", "manifest_sha256": "9" * 64,
    }))
    assert profile.migrated_from.manifest_sha256 == "9" * 64
    with pytest.raises(CompositionV4Error, match="manifest_sha256"):
        CompositionProfile.from_dict(_profile(migrated_from={
            "schema_version": "vuoro-composition/v3", "manifest_sha256": "short",
        }))


def test_a_loaded_profile_carries_the_digest_of_its_own_bytes(tmp_path: Path) -> None:
    """Computed over the file, never stored inside it: a digest recorded in the
    document it describes could not be the digest of that document."""
    path = tmp_path / "shared.json"
    path.write_text(json.dumps(_profile()), encoding="utf-8")
    loaded = CompositionProfile.load(path)
    assert loaded.profile_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_an_absent_profile_lock_fails_as_a_composition_error(tmp_path: Path) -> None:
    with pytest.raises(CompositionV4Error, match="cannot load profile lock"):
        CompositionProfile.load(tmp_path / "missing.json")


# --- the loader/validator boundary ------------------------------------------


def test_an_incomplete_closure_loads_so_that_rule_2_has_something_to_reject() -> None:
    """Deliberate, and the reason it is a test rather than a comment.

    "Every provider's deployment closure is complete and digest-bound" is a
    validator rule. If the loader required every closure field, the rule would
    hold vacuously and the falsifier the freeze plans for it could never be
    written -- an unchanged image with a changed configuration digest would be
    unrepresentable rather than rejected.
    """
    provider = Provider.from_dict(_provider(closure={"protocol_version": "v1"}))
    assert provider.closure == DeploymentClosure(protocol_version="v1")
    assert provider.closure.configuration_digest is None
    assert Provider.from_dict(_provider()).closure == DeploymentClosure()


def test_the_loader_does_not_enforce_at_most_one_exclusive_binding_per_scope() -> None:
    """Also deliberate: rule 1 is the validator's, and it lands with its falsifier.

    Pinning it here means the next increment moves this assertion rather than
    discovering that the property was already half-enforced in a place its own
    test does not look.
    """
    profile = CompositionProfile.from_dict(_profile(bindings=[
        _binding(),
        _binding(provider_id="actionq-execution", adapter_id="execution-adapter"),
    ]))
    assert len(profile.bindings_for("execution/v1")) == 2


# =============================================================================
# The freeze's falsifiers. Each test below is named by a `planned_test` entry in
# docs/plans/2026-08-22-composition-v4-design-freeze.md, and the gate in
# tests/test_falsifier_coverage.py binds each declared scope to the docstring of
# the test that claims it.
# =============================================================================

import subprocess
import sys
import textwrap
from types import ModuleType, SimpleNamespace

from vuoro_service.catalog import CatalogRegistry
from vuoro_service.composition_v4_runtime import (
    RuntimeConfiguration,
    compose,
    satisfies_uniform_construction,
)
from vuoro_service.composition_v4_validator import (
    CONFORMANCE_HARNESSES,
    V3_SOURCE_ALLOWLIST,
    closure_digest,
    scan_sources,
    violations,
)
from vuoro_service.contracts import OperationDefinition

ROOT = Path(__file__).parents[1]
COMPOSITION = ROOT / "packages/vuoro-service/composition"
SUPPORT_MANIFEST = COMPOSITION / "support-manifest.json"
SHARED_PROFILE = COMPOSITION / "profiles/shared.json"
V3_MANIFEST = COMPOSITION / "adapter-pins.json"

#: What the migrated profile still cannot carry: v3 pins a catalog-metadata
#: digest for work and execution and none for knowledge or audit.
KNOWN_SHARED_GAPS = (
    "rule 3: audit/v1 declares operation-hashes conformance and audit-adapter "
    "records no operation_hashes",
    "rule 3: knowledge/v1 declares operation-hashes conformance and "
    "knowledge-adapter records no operation_hashes",
)

OBJECT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def reference() -> tuple[SupportManifest, CompositionProfile]:
    return SupportManifest.load(SUPPORT_MANIFEST), CompositionProfile.load(SHARED_PROFILE)


def _operation(name: str) -> OperationDefinition:
    return OperationDefinition(
        name=name,
        owning_domain=name.split(".", 1)[0],
        input_schema=OBJECT_SCHEMA,
        result_schema=OBJECT_SCHEMA,
        execution_semantics="read",
        idempotency="not-allowed",
    )


def _stub_adapter(name: str, domain: str, *, build_arity: int = 1) -> ModuleType:
    """An adapter that satisfies the protocol, in the smallest form that can.

    Registers one operation named for its domain, so a registry can be checked
    for having composed it without any owner being installed.
    """
    module = ModuleType(name)

    def build(runtime):
        return SimpleNamespace(capability_id=runtime.capability_id, settings=dict(runtime.settings))

    def build_wrong(runtime, extra):  # pragma: no cover - never called
        return None

    def register(registry, application):
        registry.register(_operation(f"{domain}.thing.get"), lambda arguments, context: arguments)

    module.build = build if build_arity == 1 else build_wrong
    module.register = register
    sys.modules[name] = module
    return module


def _runtime(capability_id: str = "execution/v1") -> RuntimeConfiguration:
    return RuntimeConfiguration(
        capability_id=capability_id,
        environment_name="devbox",
        environment_class="development",
        settings={},
    )


def test_adding_a_contract_touches_no_python() -> None:
    """The rule the whole freeze exists for, checked in both directions.

    Scope: adding a contract to the support manifest changes no .py file, and no
    module under scripts/ or in the service package contains a contract-name
    literal set.
    """
    manifest, profile = reference()
    added = SupportManifest.from_dict({
        "schema_version": "vuoro-support-manifest/v1",
        "origin_allowlist": list(manifest.origin_allowlist),
        "contracts": [
            *(vars(contract) | {"capability_id": contract.capability_id}
              for contract in manifest.contracts),
            _contract(capability_id="federation.claim/v1", required=False, frozen=False,
                      scope_kind="project", owner=None),
        ],
    })
    # The required set moved, from records alone.
    assert added.contract("federation.claim/v1").scope_kind == "project"
    assert len(added.contracts) == len(manifest.contracts) + 1

    # And no Vuoro module names it, or names any other contract as a literal set.
    tracked = subprocess.run(
        ["git", "grep", "-l", "federation.claim/v1", "--",
         "packages/vuoro-service/src", "scripts"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert tracked.stdout == "", f"a .py file names the new contract: {tracked.stdout}"
    assert scan_sources(ROOT, profile, added) == []


def test_new_contract_composes_without_service_code_change() -> None:
    """What makes a fifth contract composable rather than merely declarable.

    Scope: a profile declaring a contract whose adapter does not satisfy
    build/register is rejected, and composing it registers that contract's
    operations without any owner-specific branch.
    """
    manifest = SupportManifest.from_dict(_support(contracts=[
        _contract(capability_id="federation.grant/v1", cardinality="exclusive",
                  required=False, scope_kind="global", frozen=False, owner="actionq"),
    ]))
    _stub_adapter("v4_stub_federation", "federation")
    profile = CompositionProfile.from_dict(_profile(
        providers=[_provider(provider_id="actionq-federation", capabilities=["federation.grant/v1"])],
        adapters=[_adapter(adapter_id="federation-catalog", provider_id="actionq-federation",
                           module="v4_stub_federation", build="build", register="register")],
        bindings=[_binding(capability_id="federation.grant/v1", provider_id="actionq-federation",
                           adapter_id="federation-catalog")],
    ))
    composed = compose(profile, manifest, environ={}, environment_name="devbox",
                       environment_class="development")
    assert [item.capability_id for item in composed.composed] == ["federation.grant/v1"]
    assert "federation.thing.get" in composed.registry.catalog().model_dump()["operations"][0]["name"]

    # An adapter that does not satisfy the protocol is rejected, not composed.
    _stub_adapter("v4_stub_broken", "federation", build_arity=2)
    broken = CompositionProfile.from_dict(_profile(
        providers=[_provider(provider_id="actionq-federation", capabilities=["federation.grant/v1"])],
        adapters=[_adapter(adapter_id="federation-catalog", provider_id="actionq-federation",
                           module="v4_stub_broken", build="build", register="register")],
        bindings=[_binding(capability_id="federation.grant/v1", provider_id="actionq-federation",
                           adapter_id="federation-catalog")],
    ))
    assert any("build must accept one" in item for item in violations(broken, manifest))
    with pytest.raises(CompositionV4Error, match="build must accept one"):
        compose(broken, manifest, environ={}, environment_name="devbox",
                environment_class="development")


def test_cardinality_is_rejected_outside_capability_records() -> None:
    """Cardinality belongs to the capability, never to a plane or a provider.

    Scope: a cardinality declared anywhere but on a capability contract is
    rejected, and required is accepted independently of cardinality.
    """
    for record, payload in (
        (Provider, _provider(cardinality="exclusive")),
        (Adapter, _adapter(cardinality="exclusive")),
        (AuthorityBinding, _binding(cardinality="exclusive")),
    ):
        with pytest.raises(CompositionV4Error, match="unknown fields"):
            record.from_dict(payload)
    for cardinality in ("exclusive", "multi", "projection"):
        for required in (True, False):
            contract = CapabilityContract.from_dict(
                _contract(cardinality=cardinality, required=required)
            )
            assert (contract.cardinality, contract.required) == (cardinality, required)


def test_frozen_and_iterative_capabilities_need_separate_release_units() -> None:
    """One repository, two providers -- the normal case, not a special one.

    Scope: two providers sharing source_repository but differing in release unit
    are accepted, and one provider record bound to two frozen-and-iterative
    capabilities is rejected.
    """
    manifest = SupportManifest.from_dict(_support(contracts=[
        _contract(capability_id="execution/v1", frozen=True,
                  operation_hashes="e" * 64),
        _contract(capability_id="federation.grant/v1", frozen=False, required=False),
    ]))
    shared_unit = _profile(
        providers=[_provider(capabilities=["execution/v1", "federation.grant/v1"])],
        bindings=[
            _binding(capability_id="execution/v1"),
            _binding(capability_id="federation.grant/v1"),
        ],
    )
    found = violations(CompositionProfile.from_dict(shared_unit), manifest,
                       check_entrypoints=False)
    assert any("frozen and an iterative" in item for item in found)

    split = _profile(
        providers=[
            _provider(),
            _provider(provider_id="actionq-federation", release_unit="actionq-federation",
                      capabilities=["federation.grant/v1"],
                      artifact={"distribution": "actionq-federation",
                                "distribution_version": "0.1.26",
                                "artifact_sha256": "f" * 64,
                                "artifact_url": "https://github.com/bayleafwalker/actionq/releases/download/v0.1.26/actionq_federation-0.1.26-py3-none-any.whl"}),
        ],
        adapters=[_adapter(), _adapter(adapter_id="federation-catalog",
                                       provider_id="actionq-federation")],
        bindings=[
            _binding(capability_id="execution/v1"),
            _binding(capability_id="federation.grant/v1", provider_id="actionq-federation",
                     adapter_id="federation-catalog"),
        ],
    )
    found = violations(CompositionProfile.from_dict(split), manifest, check_entrypoints=False)
    assert not any("frozen and an iterative" in item for item in found)


def test_adapter_records_cannot_declare_canonical_state() -> None:
    """An adapter with state of its own is a provider.

    Scope: an adapter record declaring a schema or store is rejected; the same
    declaration on a provider record is accepted.
    """
    for field_name in ("schema", "schema_version", "store", "dsn"):
        with pytest.raises(CompositionV4Error, match="unknown fields"):
            Adapter.from_dict(_adapter(**{field_name: "audit-schema/v1"}))
    provider = Provider.from_dict(_provider(closure={"schema_version": "audit-schema/v1"}))
    assert provider.closure.schema_version == "audit-schema/v1"
    assert violations(CompositionProfile.from_dict(_profile()), reference()[0],
                      check_entrypoints=False) is not None
    assert not any(
        item.startswith("rule 5")
        for item in violations(CompositionProfile.from_dict(_profile()), reference()[0],
                               check_entrypoints=False)
    )


def test_no_scope_has_two_authorities() -> None:
    """The v3 exclusive-primary guarantee, relocated to the binding.

    Scope: two authority bindings for one exclusive capability and one scope are
    rejected, across every profile.
    """
    manifest, _ = reference()
    for profile_name in ("local", "shared", "served", "cloud"):
        doubled = CompositionProfile.from_dict(_profile(
            profile=profile_name,
            providers=[_provider(), _provider(provider_id="second", release_unit="second",
                                              capabilities=["execution/v1"],
                                              artifact={"distribution": "actionq-alt",
                                                        "distribution_version": "0.1.26",
                                                        "artifact_sha256": "b" * 64,
                                                        "artifact_url": "https://github.com/bayleafwalker/actionq/releases/download/v0.1.26/actionq_alt-0.1.26-py3-none-any.whl"})],
            adapters=[_adapter(), _adapter(adapter_id="second-catalog", provider_id="second")],
            bindings=[_binding(), _binding(provider_id="second", adapter_id="second-catalog")],
        ))
        found = violations(doubled, manifest, check_entrypoints=False)
        assert any("exclusive and has two bindings" in item for item in found), profile_name

    # One binding per scope instance of the same capability is not a violation.
    manifest_env = SupportManifest.from_dict(_support(contracts=[
        _contract(capability_id="federation.resource/v1", scope_kind="environment",
                  required=False, frozen=False),
    ]))
    scoped = CompositionProfile.from_dict(_profile(bindings=[
        _binding(capability_id="federation.resource/v1", scope_kind="environment",
                 scope_instance="development"),
        _binding(capability_id="federation.resource/v1", scope_kind="environment",
                 scope_instance="production"),
    ]))
    assert not any(
        "two bindings" in item
        for item in violations(scoped, manifest_env, check_entrypoints=False)
    )


def test_federation_change_leaves_execution_v1_binding_identical() -> None:
    """Coupling is declared, not absent -- which is what v3 guaranteed structurally.

    Scope: a profile binding federation and execution/v1 to the same provider
    release unit, without an explicit coupling declaration, is rejected by the
    validator.
    """
    manifest = SupportManifest.from_dict(_support(contracts=[
        _contract(capability_id="execution/v1", frozen=False),
        _contract(capability_id="federation.grant/v1", frozen=False, required=False),
    ]))
    shared_unit = CompositionProfile.from_dict(_profile(
        providers=[_provider(capabilities=["execution/v1", "federation.grant/v1"])],
        bindings=[
            _binding(capability_id="execution/v1"),
            _binding(capability_id="federation.grant/v1"),
        ],
    ))
    assert any(
        "without declaring the coupling" in item
        for item in violations(shared_unit, manifest, check_entrypoints=False)
    )
    declared = CompositionProfile.from_dict(_profile(
        providers=[_provider(capabilities=["execution/v1", "federation.grant/v1"])],
        bindings=[
            _binding(capability_id="execution/v1", coupled_with=["federation.grant/v1"]),
            _binding(capability_id="federation.grant/v1", coupled_with=["execution/v1"]),
        ],
    ))
    assert not any(
        "coupling" in item
        for item in violations(declared, manifest, check_entrypoints=False)
    )


def test_multiple_telemetry_exporters_validate() -> None:
    """Multi cardinality means several simultaneous providers, not a fallback list.

    Scope: two or more bindings on a multi-cardinality telemetry.export
    capability validate.
    """
    manifest = SupportManifest.from_dict({
        "schema_version": "vuoro-support-manifest/v1",
        "origin_allowlist": ["https://registry.example/"],
        "contracts": [_contract(capability_id="telemetry.export/v1", cardinality="multi",
                                required=False, scope_kind="environment", frozen=False,
                                conformance="probe", owner="otel")],
    })
    def collector(index: int) -> dict:
        return _provider(
            provider_id=f"otel-collector-{index}", release_unit=f"otel-collector-{index}",
            artifact_kind="image", role="external",
            source_repository="https://registry.example/otel",
            artifact={"image_reference": f"https://registry.example/otel-{index}",
                      "image_digest": "sha256:" + str(index) * 64},
            capabilities=["telemetry.export/v1"],
            closure={"configuration_digest": str(index) * 64, "protocol_version": "1",
                     "probe_evidence": f"probe-{index}"},
        )
    providers = []
    for index in (1, 2):
        record = collector(index)
        record["closure"]["attestation"] = closure_digest(Provider.from_dict(record))
        providers.append(record)
    profile = CompositionProfile.from_dict(_profile(
        providers=providers,
        adapters=[{"adapter_id": f"otel-{index}", "provider_id": f"otel-collector-{index}"}
                  for index in (1, 2)],
        bindings=[_binding(capability_id="telemetry.export/v1", scope_kind="environment",
                           scope_instance="production",
                           provider_id=f"otel-collector-{index}", adapter_id=f"otel-{index}")
                  for index in (1, 2)],
    ))
    assert violations(profile, manifest, check_entrypoints=False) == ()


def test_unchanged_image_with_changed_closure_fails() -> None:
    """A digest over the image is not a digest over what was deployed.

    Scope: an unchanged image digest with a changed configuration digest or
    schema version fails validation until the closure is re-attested.
    """
    manifest = SupportManifest.from_dict({
        "schema_version": "vuoro-support-manifest/v1",
        "origin_allowlist": ["https://registry.example/"],
        "contracts": [_contract(capability_id="secret.lease/v1", scope_kind="environment",
                                required=False, frozen=False, conformance="probe",
                                owner="openbao")],
    })
    record = _provider(
        provider_id="openbao", release_unit="openbao", artifact_kind="image", role="external",
        source_repository="https://registry.example/openbao",
        artifact={"image_reference": "https://registry.example/openbao",
                  "image_digest": "sha256:" + "b" * 64},
        capabilities=["secret.lease/v1"],
        closure={"configuration_digest": "c" * 64, "protocol_version": "1",
                 "probe_evidence": "probe-2026-08-22"},
    )
    record["closure"]["attestation"] = closure_digest(Provider.from_dict(record))
    def profile_with(closure: dict) -> CompositionProfile:
        return CompositionProfile.from_dict(_profile(
            providers=[record | {"closure": closure}],
            adapters=[{"adapter_id": "openbao-shim", "provider_id": "openbao"}],
            bindings=[_binding(capability_id="secret.lease/v1", scope_kind="environment",
                               scope_instance="production", provider_id="openbao",
                               adapter_id="openbao-shim")],
        ))
    assert violations(profile_with(record["closure"]), manifest, check_entrypoints=False) == ()

    # Same image, moved configuration, stale attestation.
    moved = dict(record["closure"], configuration_digest="d" * 64)
    found = violations(profile_with(moved), manifest, check_entrypoints=False)
    assert any("was not re-attested" in item for item in found)

    # Re-attesting is what makes it pass again.
    reattested = dict(moved)
    reattested["attestation"] = closure_digest(
        Provider.from_dict(record | {"closure": moved})
    )
    assert violations(profile_with(reattested), manifest, check_entrypoints=False) == ()


def test_v3_reference_composition_migrates_losslessly() -> None:
    """The migration proof, and the reason a revision change is a fleet cutover.

    Scope: a CatalogRegistry built from the migrated v4 profile has a
    byte-identical revision to one built from the v3 manifest, and the migrated
    providers, sha256 values, execution/v1 operation hashes, bindings and
    dependency closure equal adapter-pins.json.
    """
    from vuoro_service.composition import CompositionManifest

    manifest, profile = reference()
    v3 = CompositionManifest.load(V3_MANIFEST)
    raw = json.loads(V3_MANIFEST.read_text(encoding="utf-8"))

    # Pins, digests and provenance, lock by lock. Compared against the wheel
    # half of the profile: the external providers deployed beside them have no
    # v3 records to be equal to, and come from the overlay.
    by_id = {provider.provider_id: provider for provider in profile.providers}
    wheels = {
        provider_id for provider_id, provider in by_id.items()
        if provider.artifact_kind == "wheel"
    }
    assert wheels == {lock.lock_id for lock in v3.release_locks}
    for lock in v3.release_locks:
        provider = by_id[lock.lock_id]
        assert provider.artifact["distribution"] == lock.distribution
        assert provider.artifact["distribution_version"] == lock.distribution_version
        assert provider.artifact["artifact_sha256"] == lock.artifact_sha256
        assert provider.artifact["artifact_url"] == lock.artifact_url
        assert provider.source_repository == lock.source_repository
        assert provider.source_revision == lock.source_revision

    # Bindings and the dependency closure, descriptor by descriptor.
    for descriptor in v3.runtime_descriptors:
        bindings = profile.bindings_for(descriptor.api_version)
        assert len(bindings) == 1, descriptor.api_version
        assert bindings[0].provider_id == descriptor.lock_id
        assert bindings[0].scope == ("global", None)
        assert by_id[descriptor.lock_id].dependencies == descriptor.dependency_lock_ids
        assert by_id[descriptor.lock_id].closure.schema_version == descriptor.schema_version

    # execution/v1's frozen operation hashes, against the digest the released
    # adapter validator already pins.
    assert manifest.contract("execution/v1").operation_hashes == (
        "8d434e8b347e804c90e48a6598304be84b12f2a61ebc2dbed00a26053239a778"
    )
    assert by_id["execution-adapter"].closure.operation_hashes == (
        manifest.contract("execution/v1").operation_hashes
    )
    assert profile.migrated_from.manifest_sha256 == hashlib.sha256(
        V3_MANIFEST.read_bytes()
    ).hexdigest()

    # The observable catalog. Both registries are built from the same adapters;
    # what differs is only how each path found them, which is exactly the thing
    # that must not move the revision. Owner wheels are not installed here, so
    # the adapters are stubs -- this proves the composition path is
    # revision-neutral, not what ActionQ's catalog contains, which
    # scripts/validate_v4_reference_profile.py proves where the wheels exist.
    domains = {descriptor.api_version: descriptor.domain for descriptor in v3.runtime_descriptors}
    for capability_id, domain in domains.items():
        _stub_adapter(f"vuoro_adapter_kit.adapters.{domain}", domain)

    v3_registry = CatalogRegistry()
    for descriptor in v3.runtime_descriptors:  # v3's own order: work, execution, knowledge, audit
        sys.modules[f"vuoro_adapter_kit.adapters.{descriptor.domain}"].register(
            v3_registry, object()
        )
    environ = {
        variable: "pinned-by-deployment"
        for adapter in profile.adapters
        for variable in adapter.runtime_settings.values()
    }
    composed = compose(profile, manifest, environ=environ, environment_name="devbox",
                       environment_class="development")
    assert composed.revision == v3_registry.revision
    # The external providers are bound and validated but compose nothing: they
    # are reached over the network, so they cannot move the served catalog --
    # which is why binding them did not have to wait for the equivalence proof.
    assert len(composed.composed) == len(raw["runtime_descriptors"])


def test_owner_capabilities_recover_without_vuoro() -> None:
    """Deliberately still a declared gap; this test asserts only that it is one.

    The claim needs sprintctl's and actionq's CLIs run against their own stores
    with vuoro-service absent, and neither owner distribution is installed in
    this workspace -- so a test here could only assert something weaker than the
    claim while appearing to discharge it. The freeze keeps the gap declared and
    this records why it cannot be closed from this repository.
    """
    assert "sprintctl" not in sys.modules
    with pytest.raises(ImportError):
        __import__("sprintctl")


def test_reissued_identity_owns_nothing_historical() -> None:
    """Identity is forever, so a reissued actor must not inherit ownership.

    Scope: the federation.principal/v1 contract declares ownership
    non-transferable and no bound provider passes conformance without evidence
    for it.
    """
    manifest, _ = reference()
    principal = manifest.contract("federation.principal/v1")
    assert principal.ownership == "non-transferable"
    assert principal.frozen is True

    unproven = _provider(provider_id="actionq-federation", release_unit="actionq-federation",
                         capabilities=["federation.principal/v1"],
                         closure={"configuration_digest": "a" * 64, "protocol_version": "1",
                                  "schema_version": "actionq-schema/v12",
                                  "operation_hashes": "9" * 64})
    unproven["closure"]["attestation"] = closure_digest(Provider.from_dict(unproven))
    profile = CompositionProfile.from_dict(_profile(
        providers=[unproven],
        adapters=[_adapter(adapter_id="principal-catalog", provider_id="actionq-federation")],
        bindings=[_binding(capability_id="federation.principal/v1",
                           provider_id="actionq-federation", adapter_id="principal-catalog")],
    ))
    found = violations(profile, manifest, check_entrypoints=False)
    assert any("ownership_evidence" in item for item in found)


def test_the_migrated_reference_profile_states_exactly_what_it_still_lacks() -> None:
    """Not a falsifier: a pin on the honest gaps, so closing one is a visible edit.

    v3 records a pinned catalog-metadata digest for work and execution and none
    for knowledge or audit, so the migrated profile cannot carry operation-hash
    conformance evidence for two of its four bindings. Asserting the exact
    violation set means the day an owner publishes one, this test fails and the
    profile gets updated -- rather than the gap quietly persisting behind a
    validator nobody runs.
    """
    manifest, profile = reference()
    found = violations(profile, manifest, root=ROOT, check_entrypoints=False)
    assert set(found) == set(KNOWN_SHARED_GAPS)


def test_the_rule_7_allowlists_are_the_v3_code_and_the_conformance_harnesses() -> None:
    """Both allowlists name files that exist, and the v3 one has an expiry.

    A scan whose allowlist silently accumulates entries is worth nothing, so the
    entries are asserted to exist and to be exactly the two categories the
    validator documents: the v3 composition code v4 replaces, and the
    per-provider conformance harnesses rule 3 names as evidence.
    """
    for relative in V3_SOURCE_ALLOWLIST + CONFORMANCE_HARNESSES:
        assert (ROOT / relative).is_file(), relative
    assert all("composition" in item or "scripts/" in item for item in V3_SOURCE_ALLOWLIST)
    assert all(item.startswith("scripts/") for item in CONFORMANCE_HARNESSES)


def test_the_shared_profile_is_the_migration_of_the_v3_manifest() -> None:
    """Drift between adapter-pins.json and the migrated profile fails here.

    The profile is generated, so the only way it can disagree with its source is
    an edit to one and not the other -- which is exactly what a checked-in
    derived artifact invites.
    """
    completed = subprocess.run(
        [sys.executable, "scripts/migrate_v3_composition.py", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


# --- the freeze's external proof cases, against real pins --------------------


def test_the_shared_profile_binds_external_providers_with_stated_origins() -> None:
    """Freeze §6's non-authoritative case, grounded rather than illustrated.

    The chart digests are the ones the upstream repository indexes publish for
    these versions and the values digests are computed from the cluster's own
    HelmReleases, because a proof case with invented pins would be the exact
    failure the closure rules exist to prevent. What is not here is the OpenBao
    `secret.lease/v1` case: OpenBao is not deployed, so its identity would have
    to be fabricated.
    """
    manifest, profile = reference()
    external = {
        provider.provider_id: provider
        for provider in profile.providers
        if provider.artifact_kind != "wheel"
    }
    assert set(external) == {"alloy", "kube-prometheus-stack"}
    for provider in external.values():
        assert provider.role == "external"
        assert provider.artifact["chart_repository"] in manifest.origin_allowlist
        assert provider.closure.probe_evidence
        assert provider.closure.attestation == closure_digest(provider)
        assert (ROOT / "packages/vuoro-service" / provider.closure.probe_evidence).is_file()


def test_a_multi_capability_and_an_exclusive_one_share_a_scope_instance() -> None:
    """Both halves of the case in one profile: several sinks, one store.

    telemetry.export/v1 is multi and metrics.storage/v1 is exclusive, bound at
    the same environment instance. The validator must accept the pair -- and the
    exclusive one must still be rejected if it gains a second binding there,
    which is what makes accepting the multi one meaningful rather than lax.
    """
    manifest, profile = reference()
    assert manifest.contract("telemetry.export/v1").cardinality == "multi"
    assert manifest.contract("metrics.storage/v1").cardinality == "exclusive"
    export = profile.bindings_for("telemetry.export/v1")
    storage = profile.bindings_for("metrics.storage/v1")
    assert [binding.scope for binding in export] == [("environment", "production")]
    assert [binding.scope for binding in storage] == [("environment", "production")]
    assert violations(profile, manifest, check_entrypoints=False) == KNOWN_SHARED_GAPS

    doubled = json.loads(SHARED_PROFILE.read_text(encoding="utf-8"))
    doubled["bindings"].append(dict(
        doubled["bindings"][[binding["capability_id"] for binding in doubled["bindings"]]
                            .index("metrics.storage/v1")],
        provider_id="alloy", adapter_id="alloy-export",
    ))
    found = violations(CompositionProfile.from_dict(doubled), manifest, check_entrypoints=False)
    assert any("exclusive and has two bindings" in item for item in found)

    # And a second exporter is legal at the same scope, which is the difference
    # cardinality is supposed to make.
    two_sinks = json.loads(SHARED_PROFILE.read_text(encoding="utf-8"))
    two_sinks["bindings"].append(dict(
        two_sinks["bindings"][[binding["capability_id"] for binding in two_sinks["bindings"]]
                              .index("telemetry.export/v1")],
        provider_id="kube-prometheus-stack", adapter_id="prometheus-storage",
    ))
    assert violations(CompositionProfile.from_dict(two_sinks), manifest,
                      check_entrypoints=False) == KNOWN_SHARED_GAPS


def test_the_overlay_is_the_only_source_of_the_external_records() -> None:
    """Two halves, two sources, and the generator is what keeps them apart.

    The wheels come from adapter-pins.json and the externals from the overlay.
    A record that appears in the generated profile but in neither source would
    mean someone hand-edited a generated file, which --check already fails on;
    this pins the division itself so the overlay cannot quietly acquire a wheel.
    """
    overlay = json.loads(
        (COMPOSITION / "profiles/shared-overlay.json").read_text(encoding="utf-8")
    )
    assert overlay["profile"] == "shared"
    assert all(provider["artifact_kind"] != "wheel" for provider in overlay["providers"])
    _, profile = reference()
    assert {provider["provider_id"] for provider in overlay["providers"]} == {
        provider.provider_id for provider in profile.providers
        if provider.artifact_kind != "wheel"
    }
def test_every_adapter_module_is_shipped_by_a_provider_the_profile_pins() -> None:
    """The gap that shipped a profile naming modules its own pins could not provide.

    For one commit the profile named `vuoro_adapter_kit.adapters.*` while pinning
    vuoro-adapter-kit 0.1.0, which does not contain them -- caught only by a
    comment in a CI step. The import half is now proven in CI against the
    fetched wheel; this is the half that can be proven from source: every
    adapter module's distribution is pinned by the profile, and the module file
    exists in that distribution's source tree.
    """
    _, profile = reference()
    distributions = {
        provider.artifact["distribution"].replace("-", "_"): provider
        for provider in profile.providers
        if provider.artifact_kind == "wheel"
    }
    packages = {path.name: path for path in (ROOT / "packages").iterdir() if path.is_dir()}
    for adapter in profile.adapters:
        if adapter.module is None:
            continue
        root_package = adapter.module.split(".", 1)[0]
        assert root_package in distributions, (
            f"{adapter.adapter_id} names {adapter.module}, whose distribution the "
            "profile does not pin"
        )
        owner = packages.get(root_package.replace("_", "-"))
        if owner is None:
            continue  # an owner's own wheel, not built from this repository
        source = owner / "src" / Path(*adapter.module.split("."))
        assert source.with_suffix(".py").is_file() or source.is_dir(), (
            f"{adapter.module} is not in {owner.name}'s source tree"
        )

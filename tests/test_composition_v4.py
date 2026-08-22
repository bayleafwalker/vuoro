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
        },
        "capabilities": ["execution/v1"],
        **overrides,
    }


def _adapter(**overrides) -> dict:
    return {
        "adapter_id": "execution-adapter",
        "provider_id": "actionq-execution",
        "module": "actionq.vuoro",
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
    ("chart", {"chart": "openbao", "chart_version": "1.2.3",
               "chart_digest": "c" * 64, "values_digest": "d" * 64}),
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
            "chart": "openbao", "chart_version": "1.2.3", "chart_digest": "c" * 64,
        }))


def test_digest_fields_must_be_digests() -> None:
    with pytest.raises(CompositionV4Error, match="artifact_sha256"):
        Provider.from_dict(_provider(artifact={
            "distribution": "actionq", "distribution_version": "0.1.26",
            "artifact_sha256": "not-a-digest",
        }))


def test_one_repository_may_ship_two_providers() -> None:
    """The normal case: separate release units, same source repository."""
    profile = CompositionProfile.from_dict(_profile(
        providers=[
            _provider(),
            _provider(provider_id="actionq-federation", release_unit="actionq-federation",
                      capabilities=["federation.grant/v1"],
                      artifact={"distribution": "actionq", "distribution_version": "0.1.26",
                                "artifact_sha256": "f" * 64}),
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
                            "artifact_sha256": "1" * 64}),
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


def test_module_and_register_are_declared_together() -> None:
    with pytest.raises(CompositionV4Error, match="declared together"):
        Adapter.from_dict({"adapter_id": "execution-adapter",
                           "provider_id": "actionq-execution", "module": "actionq.vuoro"})


def test_an_external_adapter_needs_no_module() -> None:
    adapter = Adapter.from_dict({"adapter_id": "openbao-shim", "provider_id": "openbao"})
    assert (adapter.module, adapter.register) == (None, None)


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

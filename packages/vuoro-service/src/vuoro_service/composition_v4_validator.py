"""One validator, one source of truth: freeze §4, rules 1-9.

The required-contract set is read from the support manifest and never from a
constant here -- that is the property the whole freeze exists for, and this
module is where it either holds or does not.

Every rule returns violations rather than raising at the first one, because a
profile with four problems should report four. :func:`validate` raises with the
whole list; :func:`violations` is what tests and the fetch/attest/startup
scripts read.

The rules, in the freeze's numbering, and where each came from:

1. bindings resolve, ``exclusive`` is at most one per scope, ``required`` is
   bound in each scope instance the profile declares (never "exactly one" per
   scope: the validator cannot enumerate the instances of a ``project`` or
   ``environment`` scope, so existence is only checkable where the profile
   declares the instance);
2. every bound provider's deployment closure is complete and digest-bound,
   which means attested: recomputing the closure digest must reproduce what the
   profile recorded;
3. conformance evidence exists per the contract's kind -- operation hashes for
   wheels, a probe record for external providers;
4. frozen contracts' operation hashes equal the baseline the support manifest
   declares;
5. no adapter declares canonical state -- structural, and asserted here against
   drift in the record type itself;
6. ``migrated_from`` resolves and carries an equivalence proof;
7. every adapter satisfies the uniform construction protocol, no module in the
   service package or under ``scripts/`` carries a contract-name literal set or
   an owner module path, and no single release unit backs both a frozen and an
   iterative exclusive capability;
8. the v3 invariants, generalised: artifact identity unique in the fetch
   namespace, owner dependencies from the primary's repository, shared
   dependencies from the canonical Vuoro repository and an allowlisted
   distribution, no orphan providers, canonical release-URL provenance for
   wheels and a stated origin for everything else;
9. ``environment``-scoped bindings name a deployable environment class.

Rule 7's source scan carries a v3 allowlist, and that is the one piece of this
module with an expiry date. ``composition.py`` and the three v3 scripts hold
exactly what the rule forbids -- ``_REQUIRED_DOMAINS``, ``from sprintctl import
pg`` -- because they are the code v4 replaces, and the freeze keeps v3 loadable
until the equivalence proof passes. The allowlist is empty the day v3 is
deleted, and the scan is worthless if it silently grows instead.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
from pathlib import Path
import re

from vuoro_service.composition import (
    _CANONICAL_VUORO_SOURCE_REPOSITORY,
    _DEPLOYABLE_ENVIRONMENT_CLASSES,
    _SHARED_DEPENDENCY_DISTRIBUTIONS,
    CompositionError,
    _release_wheel_identity,
)
from vuoro_service.composition_v4 import (
    Adapter,
    CompositionProfile,
    CompositionV4Error,
    DeploymentClosure,
    Provider,
    SupportManifest,
)
from vuoro_service.composition_v4_runtime import satisfies_uniform_construction


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")

# Fields that would mean an adapter owns canonical state. Named so rule 5 can
# assert their absence from the record type rather than trusting that nobody
# adds one later.
CANONICAL_STATE_FIELDS = frozenset(
    {"schema", "schema_version", "migration_version", "store", "dsn", "database"}
)

# Per-provider conformance harnesses, which are owner-specific by their nature:
# each one exercises one released wheel, and §4 rule 3 names them as the
# operation-hash evidence a wheel provider passes conformance with. Rule 7 and
# rule 3 would otherwise contradict each other -- the freeze forbids an owner
# module path under scripts/ in one clause and cites `validate_released_*` as
# the evidence mechanism in another. The reconciliation taken here is that rule
# 7 governs the *composition path*: what Vuoro composes must be declarative, and
# a harness that proves one provider conforms is provider code that happens to
# live in this repository. Adding a contract still touches no file in this
# tuple; adding a *provider* adds a harness, which is the intended cost.
CONFORMANCE_HARNESSES = (
    "scripts/validate_released_work_adapter.py",
    "scripts/validate_released_execution_adapter.py",
    "scripts/validate_released_knowledge_adapter.py",
    "scripts/validate_released_audit_adapter.py",
    "scripts/validate_released_catalog_composition.py",
    "scripts/capability_safety_probe.py",
)

# The v3 code the scan in rule 7 cannot see yet. Empty when v3 is deleted.
V3_SOURCE_ALLOWLIST = (
    "packages/vuoro-service/src/vuoro_service/composition.py",
    "scripts/fetch_pinned_adapters.py",
    "scripts/attest_installed_composition.py",
    "scripts/verify_pre_migration_startup.py",
)

SCANNED_SOURCE_ROOTS = (
    "packages/vuoro-service/src/vuoro_service",
    "scripts",
)


def closure_digest(provider: Provider) -> str:
    """The digest a provider's closure must carry to be attested.

    Over the artifact identity *and* the closure: an unchanged image whose
    configuration digest moved is a different closure, and the freeze is
    explicit that it must re-validate rather than ride on the image digest.
    """
    payload = {
        "artifact": dict(sorted(provider.artifact.items())),
        "closure": {
            name: value
            for name, value in sorted(vars(provider.closure).items())
            if name != "attestation" and value is not None
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _bound_providers(profile: CompositionProfile) -> dict[str, list[str]]:
    """provider id -> the capabilities bound to it."""
    bound: dict[str, list[str]] = {}
    for binding in profile.bindings:
        bound.setdefault(binding.provider_id, []).append(binding.capability_id)
    return bound


def _rule_1_bindings(profile: CompositionProfile, manifest: SupportManifest) -> list[str]:
    found: list[str] = []
    seen: dict[tuple[str, str, str | None], str] = {}
    for binding in profile.bindings:
        try:
            contract = manifest.contract(binding.capability_id)
        except CompositionV4Error:
            found.append(
                f"rule 1: binding for {binding.capability_id!r} names a capability the "
                "support manifest does not declare"
            )
            continue
        if binding.scope_kind != contract.scope_kind:
            found.append(
                f"rule 1: {binding.capability_id} is scoped {contract.scope_kind!r} but is "
                f"bound at {binding.scope_kind!r}"
            )
        key = (binding.capability_id, binding.scope_kind, binding.scope_instance)
        if contract.cardinality == "exclusive" and key in seen:
            found.append(
                f"rule 1: {binding.capability_id} is exclusive and has two bindings for "
                f"scope {binding.scope}"
            )
        seen[key] = binding.provider_id

    # Existence, only where the profile declares the instance.
    declared: dict[str, set[str | None]] = {}
    for binding in profile.bindings:
        declared.setdefault(binding.scope_kind, set()).add(binding.scope_instance)
    for contract in manifest.contracts:
        if not contract.required:
            continue
        instances = declared.get(contract.scope_kind, set())
        if contract.scope_kind == "global":
            instances = instances or {None}
        for instance in sorted(instances, key=lambda value: (value is not None, value or "")):
            if not any(
                binding.capability_id == contract.capability_id
                and binding.scope_instance == instance
                for binding in profile.bindings
            ):
                where = "" if instance is None else f" {instance!r}"
                found.append(
                    f"rule 1: required contract {contract.capability_id} is unbound in "
                    f"{contract.scope_kind}{where}"
                )
    return found


def _rule_2_closures(profile: CompositionProfile, manifest: SupportManifest) -> list[str]:
    found: list[str] = []
    for provider_id, capabilities in sorted(_bound_providers(profile).items()):
        try:
            provider = profile.provider(provider_id)
        except CompositionV4Error:
            continue  # rule 1 owns unresolvable references
        closure = provider.closure
        required = ["configuration_digest", "protocol_version"]
        # A schema version is required of providers that hold canonical state,
        # which is what operation-hash conformance identifies here; demanding
        # one of an OTel collector would be a field filled in with a
        # placeholder, which is worse than not asking.
        #
        # `migration_version` is *not* required, and that is a deliberate read
        # of the freeze rather than an omission: §3.6 lists "schema and
        # migration version", but v3 carries exactly one version per descriptor
        # (`actionq-schema/v12`), so requiring both would be satisfied today
        # only by writing the same string twice. The field stays in the record
        # for owners who separate them.
        if any(
            _conformance(manifest, capability) == "operation-hashes"
            for capability in capabilities
        ):
            required += ["schema_version"]
        for name in required:
            if getattr(closure, name) is None:
                found.append(f"rule 2: {provider_id} closure is missing {name}")
        if closure.configuration_digest and not _SHA256.fullmatch(closure.configuration_digest):
            found.append(f"rule 2: {provider_id} configuration_digest is not a digest")
        if closure.attestation is None:
            found.append(f"rule 2: {provider_id} closure is unattested")
        elif closure.attestation != closure_digest(provider):
            found.append(
                f"rule 2: {provider_id} closure attestation does not match its contents; "
                "the closure changed and was not re-attested"
            )
    return found


def _conformance(manifest: SupportManifest, capability_id: str) -> str | None:
    try:
        return manifest.contract(capability_id).conformance
    except CompositionV4Error:
        return None


def _rule_3_conformance(profile: CompositionProfile, manifest: SupportManifest) -> list[str]:
    found: list[str] = []
    for binding in profile.bindings:
        kind = _conformance(manifest, binding.capability_id)
        if kind is None:
            continue
        try:
            closure = profile.provider(binding.provider_id).closure
        except CompositionV4Error:
            continue
        evidence = "operation_hashes" if kind == "operation-hashes" else "probe_evidence"
        if getattr(closure, evidence) is None:
            found.append(
                f"rule 3: {binding.capability_id} declares {kind} conformance and "
                f"{binding.provider_id} records no {evidence}"
            )
        # Freeze §7, carried into conformance because that is where it bites: a
        # contract whose ownership is non-transferable is not conformed to by a
        # provider that has shown nothing about ownership. Identity is forever
        # and v1 has no ownership transfer, so a provider bound without this
        # evidence produces data that cannot be repaired later.
        contract = manifest.contract(binding.capability_id)
        if contract.ownership == "non-transferable" and closure.ownership_evidence is None:
            found.append(
                f"rule 3: {binding.capability_id} declares ownership "
                f"{contract.ownership!r} and {binding.provider_id} records no "
                "ownership_evidence"
            )
    return found


def _rule_4_frozen(profile: CompositionProfile, manifest: SupportManifest) -> list[str]:
    found: list[str] = []
    for contract in manifest.contracts:
        if not contract.frozen:
            continue
        if contract.operation_hashes is None:
            if profile.bindings_for(contract.capability_id):
                found.append(
                    f"rule 4: {contract.capability_id} is frozen and the support manifest "
                    "declares no baseline operation_hashes"
                )
            continue
        for binding in profile.bindings_for(contract.capability_id):
            try:
                closure = profile.provider(binding.provider_id).closure
            except CompositionV4Error:
                continue
            if closure.operation_hashes != contract.operation_hashes:
                found.append(
                    f"rule 4: {binding.provider_id} does not carry the frozen baseline for "
                    f"{contract.capability_id}"
                )
    return found


def _rule_5_adapter_state(profile: CompositionProfile) -> list[str]:
    """Structural: the record type has nowhere to put canonical state.

    Checked against the type rather than the instances, because an instance
    carrying a state field cannot be loaded at all -- so the only way this rule
    can start failing is by someone adding the field, and that is what this
    looks at.
    """
    declared = {item for item in Adapter.__dataclass_fields__}
    intruders = sorted(declared & CANONICAL_STATE_FIELDS)
    if intruders:
        return [
            f"rule 5: the adapter record declares {intruders}, which is canonical state; "
            "an adapter with state of its own is a provider"
        ]
    return []


def _rule_6_migration(
    profile: CompositionProfile, predecessor_bytes: bytes | None
) -> list[str]:
    migrated = profile.migrated_from
    if migrated is None:
        return []
    found: list[str] = []
    if not migrated.equivalence_proof:
        found.append("rule 6: migrated_from carries no equivalence proof")
    if predecessor_bytes is not None:
        digest = hashlib.sha256(predecessor_bytes).hexdigest()
        if digest != migrated.manifest_sha256:
            found.append(
                "rule 6: migrated_from.manifest_sha256 does not resolve to the "
                "predecessor supplied"
            )
    return found


def _rule_7_uniform_construction(
    profile: CompositionProfile,
    manifest: SupportManifest,
    *,
    root: Path | None,
    check_entrypoints: bool,
) -> list[str]:
    found: list[str] = []
    if check_entrypoints:
        for adapter in profile.adapters:
            if adapter.module is None:
                continue
            ok, complaint = satisfies_uniform_construction(adapter)
            if not ok:
                found.append(f"rule 7: {complaint}")

    # One release unit cannot back both a frozen and an iterative exclusive
    # capability: that is the coupling the freeze forbids, stated as rule 7
    # there because it is what forces two provider records out of one
    # repository.
    per_unit: dict[str, set[str]] = {}
    for binding in profile.bindings:
        try:
            provider = profile.provider(binding.provider_id)
        except CompositionV4Error:
            continue
        per_unit.setdefault(provider.release_unit, set()).add(binding.capability_id)
    for release_unit, capabilities in sorted(per_unit.items()):
        contracts = []
        for capability_id in capabilities:
            try:
                contracts.append(manifest.contract(capability_id))
            except CompositionV4Error:
                continue
        exclusive = [item for item in contracts if item.cardinality == "exclusive"]
        if any(item.frozen for item in exclusive) and any(not item.frozen for item in exclusive):
            found.append(
                f"rule 7: release unit {release_unit!r} backs both a frozen and an iterative "
                "exclusive capability; they are separate release units"
            )
        uncoupled = [item.capability_id for item in exclusive]
        if len(uncoupled) > 1:
            declared = {
                capability
                for binding in profile.bindings
                for capability in binding.coupled_with
            } | {
                binding.capability_id
                for binding in profile.bindings
                if binding.coupled_with
            }
            if not set(uncoupled) <= declared:
                found.append(
                    f"rule 7: release unit {release_unit!r} backs exclusive capabilities "
                    f"{sorted(uncoupled)} without declaring the coupling"
                )

    if root is not None:
        found.extend(scan_sources(root, profile, manifest))
    return found


def scan_sources(
    root: Path, profile: CompositionProfile, manifest: SupportManifest
) -> list[str]:
    """No contract-name literal set, no owner module path, in Vuoro's own code.

    The needles are derived from the profile and the manifest rather than
    listed: the owner names to look for are the distributions the profile pins,
    and the contract names are the ones the manifest declares. A check with a
    hardcoded list of owners would be the very thing it is checking for.
    """
    found: list[str] = []
    owners = {
        provider.artifact.get("distribution", "").replace("-", "_")
        for provider in profile.providers
        if provider.artifact_kind == "wheel"
    } - {"", "vuoro_adapter_kit", "vuoro_schema_runtime", "vuoro_client", "vuoro_service"}
    contract_names = {contract.capability_id for contract in manifest.contracts}
    contract_names |= {name.split("/", 1)[0] for name in contract_names}
    allowlisted = {
        (root / item).resolve()
        for item in V3_SOURCE_ALLOWLIST + CONFORMANCE_HARNESSES
    }

    for source_root in SCANNED_SOURCE_ROOTS:
        for path in sorted((root / source_root).rglob("*.py")):
            if path.resolve() in allowlisted:
                continue
            relative = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for name in _imported_roots(node):
                        if name in owners:
                            found.append(
                                f"rule 7: {relative} imports {name!r}, an owner module path"
                            )
                if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                    literals = [
                        element.value for element in node.elts
                        if isinstance(element, ast.Constant) and isinstance(element.value, str)
                    ]
                    if len(literals) == len(node.elts) and len(literals) > 1:
                        named = [item for item in literals if item in contract_names]
                        if len(named) > 1:
                            found.append(
                                f"rule 7: {relative} carries a contract-name literal set "
                                f"{sorted(named)}"
                            )
    return found


def _imported_roots(node: ast.Import | ast.ImportFrom) -> Iterable[str]:
    if isinstance(node, ast.ImportFrom):
        if node.module and node.level == 0:
            yield node.module.split(".")[0]
        return
    for alias in node.names:
        yield alias.name.split(".")[0]


def _rule_8_v3_invariants(
    profile: CompositionProfile, manifest: SupportManifest
) -> list[str]:
    found: list[str] = []
    by_id = {provider.provider_id: provider for provider in profile.providers}

    filenames: dict[str, str] = {}
    for provider in profile.providers:
        if provider.artifact_kind == "wheel":
            url = provider.artifact["artifact_url"]
            try:
                _, filename = _release_wheel_identity(provider.source_repository, url)
            except CompositionError as error:
                found.append(f"rule 8: {provider.provider_id}: {error}")
                continue
            if filename in filenames:
                found.append(
                    f"rule 8: {provider.provider_id} and {filenames[filename]} stage the "
                    f"same artifact filename {filename!r}"
                )
            filenames[filename] = provider.provider_id
            if provider.source_revision is None or not _GIT_SHA.fullmatch(provider.source_revision):
                found.append(
                    f"rule 8: {provider.provider_id} wheel has no full-Git-SHA source_revision"
                )
        else:
            origin = _origin(provider)
            if not any(origin.startswith(allowed) for allowed in manifest.origin_allowlist):
                found.append(
                    f"rule 8: {provider.provider_id} origin {origin!r} is not in the "
                    "manifest's origin allowlist; a digest binds content, not provenance"
                )

    for provider in profile.providers:
        for dependency_id in provider.dependencies:
            dependency = by_id.get(dependency_id)
            if dependency is None:
                continue  # the loader rejects this; nothing to add
            if dependency.role == "owner-dependency":
                if dependency.source_repository != provider.source_repository:
                    found.append(
                        f"rule 8: {dependency_id} is an owner dependency of "
                        f"{provider.provider_id} from a different repository"
                    )
            elif dependency.role == "shared-dependency":
                if dependency.source_repository != _CANONICAL_VUORO_SOURCE_REPOSITORY:
                    found.append(
                        f"rule 8: shared dependency {dependency_id} is not from the "
                        "canonical Vuoro repository"
                    )
                distribution = dependency.artifact.get("distribution")
                if distribution not in _SHARED_DEPENDENCY_DISTRIBUTIONS:
                    found.append(
                        f"rule 8: shared dependency distribution {distribution!r} is not "
                        "allowlisted"
                    )
            elif dependency.role == "adapter":
                found.append(
                    f"rule 8: {dependency_id} is an adapter primary and cannot be a dependency"
                )

    reachable: set[str] = set()
    frontier = [binding.provider_id for binding in profile.bindings]
    frontier += [adapter.provider_id for adapter in profile.adapters]
    while frontier:
        provider_id = frontier.pop()
        if provider_id in reachable or provider_id not in by_id:
            continue
        reachable.add(provider_id)
        frontier.extend(by_id[provider_id].dependencies)
    for orphan in sorted(set(by_id) - reachable):
        found.append(f"rule 8: {orphan} is reachable from no binding")
    return found


def _origin(provider: Provider) -> str:
    artifact = provider.artifact
    for name in ("image_reference", "artifact_url", "chart"):
        if name in artifact:
            return artifact[name]
    return provider.source_repository


def _rule_9_environment_classes(profile: CompositionProfile) -> list[str]:
    found: list[str] = []
    for binding in profile.bindings:
        if binding.scope_kind != "environment":
            continue
        if binding.scope_instance not in _DEPLOYABLE_ENVIRONMENT_CLASSES:
            found.append(
                f"rule 9: {binding.capability_id} binds environment "
                f"{binding.scope_instance!r}, which is not a deployable environment class"
            )
    return found


def violations(
    profile: CompositionProfile,
    manifest: SupportManifest,
    *,
    root: Path | None = None,
    predecessor_bytes: bytes | None = None,
    check_entrypoints: bool = True,
) -> tuple[str, ...]:
    """Every rule, every violation, in rule order.

    ``root`` enables rule 7's source scan (the repository root); ``check_entrypoints``
    imports the adapter modules and is off where they are not installed.
    """
    found: list[str] = []
    found += _rule_1_bindings(profile, manifest)
    found += _rule_2_closures(profile, manifest)
    found += _rule_3_conformance(profile, manifest)
    found += _rule_4_frozen(profile, manifest)
    found += _rule_5_adapter_state(profile)
    found += _rule_6_migration(profile, predecessor_bytes)
    found += _rule_7_uniform_construction(
        profile, manifest, root=root, check_entrypoints=check_entrypoints
    )
    found += _rule_8_v3_invariants(profile, manifest)
    found += _rule_9_environment_classes(profile)
    # A file importing one owner twice is one violation, not two.
    return tuple(dict.fromkeys(found))


def validate(
    profile: CompositionProfile,
    manifest: SupportManifest,
    **options,
) -> None:
    found = violations(profile, manifest, **options)
    if found:
        raise CompositionV4Error(
            f"profile {profile.profile!r} fails validation:\n  " + "\n  ".join(found)
        )


__all__ = [
    "CANONICAL_STATE_FIELDS",
    "CONFORMANCE_HARNESSES",
    "V3_SOURCE_ALLOWLIST",
    "closure_digest",
    "scan_sources",
    "validate",
    "violations",
]

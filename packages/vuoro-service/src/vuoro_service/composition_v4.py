"""Capability-based composition: the v4 record types and their loader.

Beside :mod:`vuoro_service.composition`, not replacing it. v3 stays loadable
and stays the composition the service actually serves until the equivalence
proof passes; nothing in this module is wired into ``create_composed_app``.

The ontology is frozen in ``docs/plans/2026-08-22-composition-v4-design-freeze.md``
and is five record types -- capability contract, provider, adapter, authority
binding, composition profile -- across two documents: a **support manifest**
(what Vuoro supports: contracts and their properties) and a **profile lock**
(what Vuoro tested: exact providers, adapters and bindings). Planes are not
records; they are a reading aid in the freeze and appear nowhere here.

**This module loads; it does not yet validate.** The split is deliberate and
is where the next increment attaches. What lives here is shape: field sets,
enumerations, identifier syntax, uniqueness of ids, and references that
resolve. What lives in the validator (freeze §4, rules 1-9) is everything that
needs to reason across records -- at most one exclusive binding per scope,
closure completeness, conformance evidence, frozen operation hashes, adapters
declaring no state, the uniform construction protocol, and the v3 invariants
carried forward as rules 8 and 9.

Two consequences of that split are load-bearing, because getting them backwards
would make a validator rule unfalsifiable by construction:

* deployment-closure fields are **optional** here. Rule 2 ("every provider's
  deployment closure is complete and digest-bound") has something to check only
  if an incomplete closure can be *loaded*. A loader that required every field
  would satisfy rule 2 vacuously and leave its falsifier untestable;
* an adapter record has **no** field in which canonical state could be
  declared, so rule 5's rejection is structural. That is the one place shape
  and rule coincide, and it is stated rather than left to be rediscovered.

Nothing here reads or asserts a coverage number; the falsifier gate
(``tests/test_falsifier_coverage.py``) owns that, and every claim the freeze
marks is still a declared gap until the tests it names exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from vuoro_service.composition import CompositionError


SUPPORT_MANIFEST_SCHEMA = "vuoro-support-manifest/v1"
PROFILE_LOCK_SCHEMA = "vuoro-composition/v4"
PREDECESSOR_SCHEMA = "vuoro-composition/v3"

CARDINALITIES = frozenset({"exclusive", "multi", "projection"})
SCOPE_KINDS = frozenset({"tenant", "project", "environment", "global"})
CONFORMANCE_KINDS = frozenset({"operation-hashes", "probe"})
# Freeze §7: an identity contract must forbid a reissued actor from acquiring a
# historical principal's ownership. Declared on the contract because it is a
# contract-level obligation, and a prerequisite of any provider binding to it.
OWNERSHIP_KINDS = frozenset({"non-transferable"})
ARTIFACT_KINDS = frozenset({"wheel", "image", "chart", "binary"})
PROVIDER_ROLES = frozenset({"adapter", "owner-dependency", "shared-dependency", "external"})
PROFILE_NAMES = frozenset({"local", "shared", "served", "cloud"})

# `<name>/v<N>`, the v3 api_version syntax carried forward as the contract id.
CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*/v[1-9][0-9]*$")
RECORD_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# Which identity fields each artifact kind must carry (freeze §3.3). `chart`
# carries a values digest because mutable values are not a freeze, and `image`
# is frozen only together with its closure -- which is why the closure is a
# separate record rather than more fields here.
ARTIFACT_IDENTITY_FIELDS: Mapping[str, frozenset[str]] = {
    "wheel": frozenset(
        {"distribution", "distribution_version", "artifact_sha256", "artifact_url"}
    ),
    "image": frozenset({"image_reference", "image_digest"}),
    "chart": frozenset({"chart", "chart_version", "chart_digest", "values_digest"}),
    "binary": frozenset({"artifact_url", "artifact_sha256"}),
}


class CompositionV4Error(CompositionError):
    """A v4 support manifest or profile lock is malformed.

    A subclass so that callers already catching :class:`CompositionError` keep
    working while the two loaders run side by side, and so a v4 failure is
    still distinguishable from a v3 one in a traceback.
    """


def _strict(raw: Mapping[str, Any], required: set[str], optional: set[str], label: str) -> dict:
    if not isinstance(raw, Mapping):
        raise CompositionV4Error(f"{label}: record must be an object")
    present = set(raw)
    missing = required - present
    unknown = present - required - optional
    if missing:
        raise CompositionV4Error(f"{label}: missing {sorted(missing)}")
    if unknown:
        raise CompositionV4Error(f"{label}: unknown fields {sorted(unknown)}")
    return dict(raw)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CompositionV4Error(f"{label}: must be a non-empty string")
    return value


def _flag(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise CompositionV4Error(f"{label}: must be true or false")
    return value


def _member(value: Any, allowed: frozenset[str], label: str) -> str:
    text = _text(value, label)
    if text not in allowed:
        raise CompositionV4Error(f"{label}: {text!r} is not one of {sorted(allowed)}")
    return text


@dataclass(frozen=True)
class CapabilityContract:
    """The smallest versioned semantic interface (freeze §3.2).

    ``cardinality`` lives here and nowhere else: it is a property of the
    capability, never of a provider or a plane. ``required`` is a separate flag
    rather than a fourth cardinality value, because absence-legality is
    orthogonal to it -- a capability can be optional and exclusive when present.
    """

    capability_id: str
    cardinality: str
    required: bool
    scope_kind: str
    frozen: bool
    conformance: str
    owner: str | None = None
    operation_hashes: str | None = None
    ownership: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CapabilityContract":
        label = f"capability {raw.get('capability_id', '?')!r}"
        data = _strict(
            raw,
            {"capability_id", "cardinality", "required", "scope_kind", "frozen", "conformance"},
            {"owner", "operation_hashes", "ownership"},
            label,
        )
        capability_id = _text(data["capability_id"], f"{label}: capability_id")
        if not CAPABILITY_ID.fullmatch(capability_id):
            raise CompositionV4Error(f"{label}: capability ids are `<name>/v<N>`")
        owner = data.get("owner")
        if owner is not None:
            # None is not an omission: the freeze records `federation.principal/v1`'s
            # owner as proposed and unsettled, and a loader that forced a name
            # would launder that open question into a pin.
            owner = _text(owner, f"{label}: owner")
        baseline = data.get("operation_hashes")
        if baseline is not None and not _SHA256.fullmatch(_text(baseline, f"{label}: operation_hashes")):
            raise CompositionV4Error(f"{label}: operation_hashes must be a SHA-256 digest")
        ownership = data.get("ownership")
        if ownership is not None:
            ownership = _member(ownership, OWNERSHIP_KINDS, f"{label}: ownership")
        return cls(
            capability_id=capability_id,
            cardinality=_member(data["cardinality"], CARDINALITIES, f"{label}: cardinality"),
            required=_flag(data["required"], f"{label}: required"),
            scope_kind=_member(data["scope_kind"], SCOPE_KINDS, f"{label}: scope_kind"),
            frozen=_flag(data["frozen"], f"{label}: frozen"),
            conformance=_member(data["conformance"], CONFORMANCE_KINDS, f"{label}: conformance"),
            owner=owner,
            operation_hashes=baseline,
            ownership=ownership,
        )


@dataclass(frozen=True)
class SupportManifest:
    """Compatible contracts and their properties: what Vuoro *supports*.

    The required-contract set is derived from the records here and is never a
    constant in code -- that is the whole reason v4 exists, so the derivation
    is a property rather than a literal anywhere.
    """

    schema_version: str
    contracts: tuple[CapabilityContract, ...]
    origin_allowlist: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: Path) -> "SupportManifest":
        return cls.from_dict(_read_json(path, "support manifest"))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SupportManifest":
        data = _strict(
            raw, {"schema_version", "contracts"}, {"origin_allowlist"}, "support manifest"
        )
        if data["schema_version"] != SUPPORT_MANIFEST_SCHEMA:
            raise CompositionV4Error(
                f"support manifest: unsupported schema_version {data['schema_version']!r}"
            )
        if not isinstance(data["contracts"], list) or not data["contracts"]:
            raise CompositionV4Error("support manifest: contracts must be a non-empty array")
        contracts = tuple(CapabilityContract.from_dict(item) for item in data["contracts"])
        ids = [contract.capability_id for contract in contracts]
        if len(ids) != len(set(ids)):
            raise CompositionV4Error("support manifest: duplicate capability ids")
        return cls(
            schema_version=data["schema_version"],
            contracts=contracts,
            # A digest binds content, not provenance, so image/chart/binary
            # artifacts need a stated origin (rule 8). It is declared here
            # rather than constant in code for the same reason the required
            # contract set is.
            origin_allowlist=_identifiers(
                data.get("origin_allowlist", []), "support manifest: origin_allowlist"
            ),
        )

    def contract(self, capability_id: str) -> CapabilityContract:
        for contract in self.contracts:
            if contract.capability_id == capability_id:
                return contract
        raise CompositionV4Error(f"support manifest declares no contract {capability_id!r}")

    @property
    def required_capabilities(self) -> frozenset[str]:
        return frozenset(
            contract.capability_id for contract in self.contracts if contract.required
        )


@dataclass(frozen=True)
class DeploymentClosure:
    """What must be re-validated when anything about a provider moves (§3.6).

    Every field is optional at load time. An unchanged image with a changed
    configuration digest is a changed closure and must fail -- but that is rule
    2's job, and a rule whose input cannot be malformed cannot be falsified.

    ``attestation`` is what "must re-validate" means mechanically: a digest over
    the artifact identity and every other field here. Change the configuration
    digest or the schema version without re-attesting and the recomputation no
    longer matches, which is the freeze's image-with-a-changed-closure case
    failing for the reason the freeze gives rather than by coincidence.
    """

    configuration_digest: str | None = None
    schema_version: str | None = None
    migration_version: str | None = None
    protocol_version: str | None = None
    operation_hashes: str | None = None
    probe_evidence: str | None = None
    ownership_evidence: str | None = None
    attestation: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], label: str) -> "DeploymentClosure":
        names = {item.name for item in fields(cls)}
        data = _strict(raw, set(), names, f"{label}: closure")
        for name, value in data.items():
            if value is not None:
                _text(value, f"{label}: closure.{name}")
        return cls(**{name: data.get(name) for name in names})


@dataclass(frozen=True)
class Provider:
    """An implementation, internal or external -- and one release unit (§3.3).

    Identified by source and artifact, not by Python distribution: a wheel is
    one ``artifact_kind`` among four. ``release_unit`` is what an authority
    binding actually references, which is how one repository ships two
    providers -- the normal case, not a special one.
    """

    provider_id: str
    release_unit: str
    artifact_kind: str
    role: str
    source_repository: str
    artifact: Mapping[str, str]
    capabilities: tuple[str, ...]
    source_revision: str | None = None
    dependencies: tuple[str, ...] = ()
    closure: DeploymentClosure = field(default_factory=DeploymentClosure)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Provider":
        label = f"provider {raw.get('provider_id', '?')!r}"
        data = _strict(
            raw,
            {"provider_id", "release_unit", "artifact_kind", "role",
             "source_repository", "artifact", "capabilities"},
            {"source_revision", "dependencies", "closure"},
            label,
        )
        provider_id = _text(data["provider_id"], f"{label}: provider_id")
        if not RECORD_ID.fullmatch(provider_id):
            raise CompositionV4Error(f"{label}: provider_id must be lowercase kebab-case")
        artifact_kind = _member(data["artifact_kind"], ARTIFACT_KINDS, f"{label}: artifact_kind")
        artifact = data["artifact"]
        expected = ARTIFACT_IDENTITY_FIELDS[artifact_kind]
        if not isinstance(artifact, Mapping) or set(artifact) != set(expected):
            raise CompositionV4Error(
                f"{label}: a {artifact_kind} artifact is identified by {sorted(expected)}"
            )
        for name, value in artifact.items():
            _text(value, f"{label}: artifact.{name}")
            if name.endswith(("sha256", "digest")) and not _SHA256.fullmatch(
                value.removeprefix("sha256:")
            ):
                raise CompositionV4Error(f"{label}: artifact.{name} must be a SHA-256 digest")
        capabilities = _identifiers(data["capabilities"], f"{label}: capabilities")
        for capability_id in capabilities:
            if not CAPABILITY_ID.fullmatch(capability_id):
                raise CompositionV4Error(f"{label}: capability ids are `<name>/v<N>`")
        dependencies = _identifiers(data.get("dependencies", []), f"{label}: dependencies")
        if provider_id in dependencies:
            raise CompositionV4Error(f"{label}: a provider cannot depend on itself")
        return cls(
            provider_id=provider_id,
            release_unit=_text(data["release_unit"], f"{label}: release_unit"),
            artifact_kind=artifact_kind,
            role=_member(data["role"], PROVIDER_ROLES, f"{label}: role"),
            source_repository=_text(data["source_repository"], f"{label}: source_repository"),
            artifact=dict(artifact),
            capabilities=capabilities,
            source_revision=(
                None if data.get("source_revision") is None
                else _text(data["source_revision"], f"{label}: source_revision")
            ),
            dependencies=dependencies,
            closure=DeploymentClosure.from_dict(data.get("closure", {}), label),
        )


@dataclass(frozen=True)
class Adapter:
    """A thin Vuoro translation layer that owns no canonical state (§3.4).

    There is no field here in which a schema, store or migration could be
    declared: an adapter with state of its own is a provider and is declared as
    one. ``module``/``register`` are the wheel case's half of the uniform
    construction protocol; enforcing that the named entrypoints actually satisfy
    ``build``/``register`` is rule 7 and is not done at load time.
    """

    adapter_id: str
    provider_id: str
    module: str | None = None
    build: str | None = None
    register: str | None = None
    runtime_settings: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Adapter":
        label = f"adapter {raw.get('adapter_id', '?')!r}"
        data = _strict(
            raw,
            {"adapter_id", "provider_id"},
            {"module", "build", "register", "runtime_settings"},
            label,
        )
        adapter_id = _text(data["adapter_id"], f"{label}: adapter_id")
        if not RECORD_ID.fullmatch(adapter_id):
            raise CompositionV4Error(f"{label}: adapter_id must be lowercase kebab-case")
        entrypoints = {name: data.get(name) for name in ("module", "build", "register")}
        declared = {name for name, value in entrypoints.items() if value is not None}
        if declared not in (set(), {"module", "build", "register"}):
            # All three or none: a module with no build is the shape the uniform
            # construction protocol exists to abolish, and naming two of the
            # three is how a half-migrated adapter would slip past rule 7.
            raise CompositionV4Error(
                f"{label}: module, build and register are declared together or not at all"
            )
        settings = data.get("runtime_settings", {})
        if not isinstance(settings, Mapping):
            raise CompositionV4Error(f"{label}: runtime_settings must be an object")
        for name, source in settings.items():
            _text(name, f"{label}: runtime_settings key")
            _text(source, f"{label}: runtime_settings[{name}]")
        return cls(
            adapter_id=adapter_id,
            provider_id=_text(data["provider_id"], f"{label}: provider_id"),
            **{
                name: None if value is None else _text(value, f"{label}: {name}")
                for name, value in entrypoints.items()
            },
            runtime_settings=dict(settings),
        )


@dataclass(frozen=True)
class AuthorityBinding:
    """`(capability, scope) -> provider`, plus the adapter used to reach it (§3.5).

    The v3 exclusive-primary guarantee lives here rather than in packaging.
    ``coupled_with`` is the explicit declaration that lets one release unit back
    two exclusive bindings; without it the validator rejects the sharing, which
    is what keeps a federation repin from silently repinning frozen
    ``execution/v1``.
    """

    capability_id: str
    scope_kind: str
    provider_id: str
    adapter_id: str
    scope_instance: str | None = None
    coupled_with: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AuthorityBinding":
        label = f"binding {raw.get('capability_id', '?')!r}"
        data = _strict(
            raw,
            {"capability_id", "scope_kind", "provider_id", "adapter_id"},
            {"scope_instance", "coupled_with"},
            label,
        )
        scope_instance = data.get("scope_instance")
        return cls(
            capability_id=_text(data["capability_id"], f"{label}: capability_id"),
            scope_kind=_member(data["scope_kind"], SCOPE_KINDS, f"{label}: scope_kind"),
            provider_id=_text(data["provider_id"], f"{label}: provider_id"),
            adapter_id=_text(data["adapter_id"], f"{label}: adapter_id"),
            scope_instance=(
                None if scope_instance is None
                else _text(scope_instance, f"{label}: scope_instance")
            ),
            coupled_with=_identifiers(data.get("coupled_with", []), f"{label}: coupled_with"),
        )

    @property
    def scope(self) -> tuple[str, str | None]:
        """What ``exclusive`` is exclusive *over*.

        A ``global`` binding has no instance, so the pair is the scope key the
        validator groups by -- and the reason rule 1 is "at most one" rather
        than "exactly one": the loader cannot enumerate the instances of a
        ``project`` or ``environment`` scope.
        """
        return (self.scope_kind, self.scope_instance)


@dataclass(frozen=True)
class MigratedFrom:
    """The predecessor a profile supersedes (§3.6).

    ``manifest_sha256`` over the predecessor's bytes, because that is the only
    whole-profile identity v3 has, and it is already the identity the installed
    composition attestation binds.
    """

    schema_version: str
    manifest_sha256: str
    equivalence_proof: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MigratedFrom":
        data = _strict(
            raw, {"schema_version", "manifest_sha256"}, {"equivalence_proof"}, "migrated_from"
        )
        digest = _text(data["manifest_sha256"], "migrated_from: manifest_sha256")
        if not _SHA256.fullmatch(digest):
            raise CompositionV4Error("migrated_from: manifest_sha256 must be a SHA-256 digest")
        if data["schema_version"] not in {PREDECESSOR_SCHEMA, PROFILE_LOCK_SCHEMA}:
            raise CompositionV4Error(
                f"migrated_from: unsupported predecessor schema {data['schema_version']!r}"
            )
        proof = data.get("equivalence_proof")
        return cls(
            schema_version=data["schema_version"],
            manifest_sha256=digest,
            equivalence_proof=(
                None if proof is None else _text(proof, "migrated_from: equivalence_proof")
            ),
        )


@dataclass(frozen=True)
class CompositionProfile:
    """A tested set of exact pins: what Vuoro *tested* (§3.6).

    A deployment shape -- ``local``, ``shared``, ``served``, ``cloud`` -- not an
    environment class and not a README mode. Profiles may bind different
    providers to the same contracts; the contracts and the validator are
    profile-independent.
    """

    schema_version: str
    profile: str
    providers: tuple[Provider, ...]
    adapters: tuple[Adapter, ...]
    bindings: tuple[AuthorityBinding, ...]
    migrated_from: MigratedFrom | None = None
    profile_sha256: str | None = None

    @classmethod
    def load(cls, path: Path) -> "CompositionProfile":
        raw = _read_json(path, "profile lock")
        # Over the file's bytes, matching what v3's attestation already binds
        # and what `migrated_from.manifest_sha256` will name. A digest recorded
        # *inside* the document it describes could not be either.
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return cls.from_dict(raw, profile_sha256=digest)

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any], *, profile_sha256: str | None = None
    ) -> "CompositionProfile":
        data = _strict(
            raw,
            {"schema_version", "profile", "providers", "adapters", "bindings"},
            {"migrated_from"},
            "profile lock",
        )
        if data["schema_version"] != PROFILE_LOCK_SCHEMA:
            raise CompositionV4Error(
                f"profile lock: unsupported schema_version {data['schema_version']!r}"
            )
        profile = _member(data["profile"], PROFILE_NAMES, "profile lock: profile")
        providers = tuple(_objects(data["providers"], Provider, "providers"))
        adapters = tuple(_objects(data["adapters"], Adapter, "adapters"))
        bindings = tuple(_objects(data["bindings"], AuthorityBinding, "bindings"))

        _unique([provider.provider_id for provider in providers], "duplicate provider ids")
        _unique([adapter.adapter_id for adapter in adapters], "duplicate adapter ids")

        provider_ids = {provider.provider_id for provider in providers}
        adapter_ids = {adapter.adapter_id for adapter in adapters}
        for adapter in adapters:
            if adapter.provider_id not in provider_ids:
                raise CompositionV4Error(
                    f"adapter {adapter.adapter_id!r}: unknown provider {adapter.provider_id!r}"
                )
        for provider in providers:
            for dependency in provider.dependencies:
                if dependency not in provider_ids:
                    raise CompositionV4Error(
                        f"provider {provider.provider_id!r}: unknown dependency {dependency!r}"
                    )
        for binding in bindings:
            if binding.provider_id not in provider_ids:
                raise CompositionV4Error(
                    f"binding {binding.capability_id!r}: unknown provider {binding.provider_id!r}"
                )
            if binding.adapter_id not in adapter_ids:
                raise CompositionV4Error(
                    f"binding {binding.capability_id!r}: unknown adapter {binding.adapter_id!r}"
                )
        migrated = data.get("migrated_from")
        return cls(
            schema_version=data["schema_version"],
            profile=profile,
            providers=providers,
            adapters=adapters,
            bindings=bindings,
            migrated_from=None if migrated is None else MigratedFrom.from_dict(migrated),
            profile_sha256=profile_sha256,
        )

    def provider(self, provider_id: str) -> Provider:
        for provider in self.providers:
            if provider.provider_id == provider_id:
                return provider
        raise CompositionV4Error(f"profile declares no provider {provider_id!r}")

    def adapter(self, adapter_id: str) -> Adapter:
        for adapter in self.adapters:
            if adapter.adapter_id == adapter_id:
                return adapter
        raise CompositionV4Error(f"profile declares no adapter {adapter_id!r}")

    def bindings_for(self, capability_id: str) -> tuple[AuthorityBinding, ...]:
        return tuple(
            binding for binding in self.bindings if binding.capability_id == capability_id
        )

    @property
    def bound_capabilities(self) -> frozenset[str]:
        return frozenset(binding.capability_id for binding in self.bindings)


def _identifiers(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CompositionV4Error(f"{label}: must be an array")
    items = tuple(_text(item, label) for item in value)
    if len(items) != len(set(items)):
        raise CompositionV4Error(f"{label}: contains duplicates")
    return items


def _objects(value: Any, record: type, label: str) -> list:
    if not isinstance(value, list):
        raise CompositionV4Error(f"profile lock: {label} must be an array")
    return [record.from_dict(item) for item in value]


def _unique(values: list[str], complaint: str) -> None:
    if len(values) != len(set(values)):
        raise CompositionV4Error(f"profile lock: {complaint}")


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CompositionV4Error(f"cannot load {label}: {path}") from error

"""Pinned Vuoro service composition with required work, audit and knowledge owners.

The service accepts domain adapters only through the checked-in composition
manifest. Deployment supplies runtime DSNs and an environment-bound identity
registry, but cannot add, replace, or remove catalog operations.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import importlib
import json
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, fields
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.cloud_service_identity import load_cloud_service_assertion_verifier
from vuoro_service.contracts import DomainCompatibility
from vuoro_service.edge_proof import (
    EdgeProofConfigurationError,
    EdgeProofVerifier,
    load_or_create_proof_key,
)
from vuoro_service.environment_record import load_environment_record
from vuoro_service.gateway_identity import (
    GatewayAssertionConfigurationError,
    GatewayAssertionIdentityResolver,
)
from vuoro_service.identity import (
    Identity,
    IdentityResolutionError,
    InvocationContext,
    StaticBearerIdentityResolver,
)
from vuoro_service.project_binding import (
    ProjectAuthorizationError,
    ProjectBinding,
    ProjectBindingError,
    compose_authorized_project_application,
    load_project_bindings,
)

#: Every deployable composition pins these required domains. An immutable release
#: may additionally pin the lanectl lane-event owner; runtime overlays cannot add it. ``knowledge`` is
#: the released vuoro-workspace owner adapter
#: (docs/architecture/knowledge-workspace-composition.md).
_REQUIRED_DOMAINS = frozenset({"work", "audit", "knowledge"})
_ADMITTED_DOMAINS = _REQUIRED_DOMAINS | {"lane"}
_KNOWLEDGE_ADAPTER_MODULE = "vuoro_workspace.vuoro_adapter"
_KNOWLEDGE_REGISTER = "register_knowledge_catalog"
_DEPLOYABLE_ENVIRONMENT_CLASSES = frozenset({"development", "production"})
_DEFAULT_PROJECT_BINDINGS_PATH = Path("/opt/vuoro/composition/project-bindings.json")
_CLOUD_PROJECT_BINDINGS_PATH = Path("/etc/vuoro/bindings/bindings.json")
_CLOUD_BINDINGS_TRUST_ROOT = Path("/etc/vuoro")
_CLOUD_GATEWAY_PUBLIC_KEY_PATH = Path("/etc/vuoro/identity/gateway-public.pem")
#: The pod-local key the protocol edge and the shell share for edge proofs: a
#: memory-backed emptyDir mounted into both containers, never a Secret.
_EDGE_PROOF_KEY_PATH = Path("/run/vuoro/edge-proof/key")
#: How far before this process started an assertion may have been issued and
#: still be accepted, on either route: the gateway's clock skew.  Anything
#: older may have been accepted by a previous instance (agentops#2519).
_REPLAY_WATERMARK_SKEW_SECONDS = 2
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_RELEASE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_LOCK_KINDS = frozenset({"adapter", "owner-dependency", "shared-dependency"})
_CANONICAL_VUORO_SOURCE_REPOSITORY = "https://github.com/bayleafwalker/vuoro"
_SHARED_DEPENDENCY_DISTRIBUTIONS = frozenset(
    {"vuoro-schema-runtime", "vuoro-adapter-kit"}
)


class CompositionError(RuntimeError):
    """The immutable release composition or runtime configuration is invalid."""


class PrivateDeliveryCapacityError(RuntimeError):
    """Private-delivery connection capacity was unavailable before the deadline."""


@dataclass(frozen=True)
class ReleaseLock:
    """Immutable identity of one wheel installed in this service release.

    This deliberately contains no domain or adapter registration information:
    the same release lock can be a companion dependency rather than a catalog
    adapter.  Runtime selection belongs to :class:`RuntimeAdapterDescriptor`.
    """

    lock_id: str
    lock_kind: str
    source_repository: str
    source_revision: str
    artifact_url: str
    artifact_sha256: str
    distribution: str
    distribution_version: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ReleaseLock":
        field_names = {field.name for field in fields(cls)}
        if set(raw) != field_names:
            raise CompositionError("release lock fields do not match the v3 contract")
        pin = cls(**{field: raw[field] for field in field_names})
        if not all(
            isinstance(getattr(pin, field), str) and getattr(pin, field)
            for field in field_names
        ):
            raise CompositionError("release lock values must be non-empty strings")
        if pin.lock_kind not in _LOCK_KINDS:
            raise CompositionError(f"{pin.lock_id}: unsupported release lock kind")
        _validate_release_lock(pin, pin.lock_id)
        return pin


def _release_wheel_identity(source_repository: str, artifact_url: str) -> tuple[str, str]:
    try:
        source = urlsplit(source_repository)
        artifact = urlsplit(artifact_url)
    except ValueError as error:
        raise CompositionError("release artifact URL is malformed") from error
    for value, parsed in ((source_repository, source), (artifact_url, artifact)):
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.netloc != "github.com"
            or parsed.query
            or parsed.fragment
            or "%" in value
        ):
            raise CompositionError("release artifacts require canonical GitHub HTTPS URLs")
    source_parts = source.path.strip("/").split("/")
    artifact_parts = artifact.path.strip("/").split("/")
    if (
        len(source_parts) != 2
        or len(artifact_parts) != 6
        or artifact_parts[:2] != source_parts
        or artifact_parts[2:4] != ["releases", "download"]
        or any(not _RELEASE_SEGMENT.fullmatch(part) for part in source_parts)
        or not _RELEASE_SEGMENT.fullmatch(artifact_parts[4])
        or artifact_parts[4] in {".", ".."}
        or not _RELEASE_SEGMENT.fullmatch(artifact_parts[5])
        or not artifact_parts[5].endswith(".whl")
    ):
        raise CompositionError("artifact_url must identify one canonical GitHub release wheel")
    return artifact_parts[4], artifact_parts[5]


def _validate_release_lock(pin: Any, label: str) -> None:
    if not _GIT_SHA.fullmatch(pin.source_revision):
        raise CompositionError(f"{label}: source_revision must be a full Git SHA")
    if not _SHA256.fullmatch(pin.artifact_sha256):
        raise CompositionError(f"{label}: artifact_sha256 must be a SHA-256 digest")
    try:
        _release_wheel_identity(pin.source_repository, pin.artifact_url)
    except CompositionError as error:
        raise CompositionError(f"{label}: {error}") from error


@dataclass(frozen=True)
class RuntimeAdapterDescriptor:
    """Serve-time adapter selection which refers only to immutable locks."""

    domain: str
    lock_id: str
    dependency_lock_ids: tuple[str, ...]
    adapter_module: str
    register: str
    api_version: str
    schema_version: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RuntimeAdapterDescriptor":
        field_names = {
            "domain",
            "lock_id",
            "dependency_lock_ids",
            "adapter_module",
            "register",
            "api_version",
            "schema_version",
        }
        if set(raw) != field_names:
            raise CompositionError("runtime descriptor fields do not match the v3 contract")
        dependency_lock_ids = raw["dependency_lock_ids"]
        if not isinstance(dependency_lock_ids, list):
            raise CompositionError("runtime descriptor dependency_lock_ids must be an array")
        pin = cls(
            **{field: raw[field] for field in field_names - {"dependency_lock_ids"}},
            dependency_lock_ids=tuple(dependency_lock_ids),
        )
        if not all(
            isinstance(getattr(pin, field), str) and getattr(pin, field)
            for field in field_names - {"dependency_lock_ids"}
        ):
            raise CompositionError("runtime descriptor values must be non-empty strings")
        if (
            not all(isinstance(lock_id, str) and lock_id for lock_id in pin.dependency_lock_ids)
            or len(pin.dependency_lock_ids) != len(set(pin.dependency_lock_ids))
            or pin.lock_id in pin.dependency_lock_ids
        ):
            raise CompositionError(f"{pin.domain}: invalid dependency lock references")
        return pin


@dataclass(frozen=True)
class AdapterPin:
    """A validated runtime descriptor joined to its immutable release locks."""

    descriptor: RuntimeAdapterDescriptor
    release_lock: ReleaseLock
    dependencies: tuple[ReleaseLock, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AdapterPin":
        """Read the former single-record shape for test/helper compatibility.

        Release manifests are parsed exclusively through the v3 split above.
        """
        fields_v1 = {
            "domain", "source_repository", "source_revision", "artifact_url",
            "artifact_sha256", "distribution", "distribution_version", "adapter_module",
            "register", "migration_entrypoint", "api_version", "schema_version",
        }
        if not fields_v1 <= set(raw) <= fields_v1 | {"dependencies"}:
            raise CompositionError("adapter pin fields do not match the v1 contract")
        dependencies_raw = raw.get("dependencies", [])
        if not isinstance(dependencies_raw, list):
            raise CompositionError("adapter dependencies must be an array")
        domain = raw["domain"]
        lock = ReleaseLock.from_dict({
            "lock_id": domain,
            "lock_kind": "adapter",
            **{key: raw[key] for key in (
                "source_repository", "source_revision", "artifact_url", "artifact_sha256",
                "distribution", "distribution_version",
            )},
        })
        dependencies = tuple(
            ReleaseLock.from_dict({
                "lock_id": f"{domain}-dependency-{index}",
                "lock_kind": "owner-dependency",
                **item,
            })
            for index, item in enumerate(dependencies_raw)
            if isinstance(item, dict)
        )
        if len(dependencies) != len(dependencies_raw):
            raise CompositionError("adapter dependencies must be objects")
        distributions = [dependency.distribution for dependency in dependencies]
        if len(distributions) != len(set(distributions)):
            raise CompositionError(f"{domain}: duplicate dependency distribution")
        if any(
            dependency.source_repository != lock.source_repository
            for dependency in dependencies
        ):
            raise CompositionError(f"{domain}: dependencies must come from the same owner repository")
        descriptor = RuntimeAdapterDescriptor(
            domain=domain,
            lock_id=domain,
            dependency_lock_ids=tuple(dependency.lock_id for dependency in dependencies),
            adapter_module=raw["adapter_module"],
            register=raw["register"],
            api_version=raw["api_version"],
            schema_version=raw["schema_version"],
        )
        if not all(isinstance(value, str) and value for value in (
            descriptor.domain, descriptor.adapter_module, descriptor.register,
            descriptor.api_version, descriptor.schema_version,
        )):
            raise CompositionError("adapter pin values must be non-empty strings")
        return cls(descriptor, lock, dependencies)

    @property
    def domain(self) -> str:
        return self.descriptor.domain

    @property
    def adapter_module(self) -> str:
        return self.descriptor.adapter_module

    @property
    def register(self) -> str:
        return self.descriptor.register

    @property
    def api_version(self) -> str:
        return self.descriptor.api_version

    @property
    def schema_version(self) -> str:
        return self.descriptor.schema_version

    @property
    def distribution(self) -> str:
        return self.release_lock.distribution

    @property
    def lock_kind(self) -> str:
        return self.release_lock.lock_kind

    @property
    def distribution_version(self) -> str:
        return self.release_lock.distribution_version

    @property
    def source_repository(self) -> str:
        return self.release_lock.source_repository

    @property
    def source_revision(self) -> str:
        return self.release_lock.source_revision

    @property
    def artifact_url(self) -> str:
        return self.release_lock.artifact_url

    @property
    def artifact_sha256(self) -> str:
        return self.release_lock.artifact_sha256


@dataclass(frozen=True)
class CompositionManifest:
    schema_version: str
    release_locks: tuple[ReleaseLock, ...]
    runtime_descriptors: tuple[RuntimeAdapterDescriptor, ...]
    adapters: tuple[AdapterPin, ...]

    @classmethod
    def load(cls, path: Path) -> "CompositionManifest":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CompositionError(f"cannot load composition manifest: {path}") from error
        expected_fields = {"schema_version", "release_locks", "runtime_descriptors"}
        if not isinstance(raw, dict) or set(raw) != expected_fields:
            raise CompositionError("composition manifest must use the v3 top-level shape")
        if (
            raw["schema_version"] != "vuoro-composition/v3"
            or not isinstance(raw["release_locks"], list)
            or not isinstance(raw["runtime_descriptors"], list)
        ):
            raise CompositionError("unsupported composition manifest")
        locks = tuple(ReleaseLock.from_dict(item) for item in raw["release_locks"] if isinstance(item, dict))
        descriptors = tuple(
            RuntimeAdapterDescriptor.from_dict(item)
            for item in raw["runtime_descriptors"]
            if isinstance(item, dict)
        )
        if len(locks) != len(raw["release_locks"]) or len(descriptors) != len(raw["runtime_descriptors"]):
            raise CompositionError("release locks and runtime descriptors must be objects")
        lock_ids = [lock.lock_id for lock in locks]
        distributions = [lock.distribution for lock in locks]
        if len(lock_ids) != len(set(lock_ids)):
            raise CompositionError("composition contains duplicate lock identifiers")
        if len(distributions) != len(set(distributions)):
            raise CompositionError("composition contains duplicate distributions")
        filename_digests: dict[str, str] = {}
        for lock in locks:
            filename = _release_wheel_identity(lock.source_repository, lock.artifact_url)[1]
            existing = filename_digests.get(filename)
            if existing is not None and existing != lock.artifact_sha256:
                raise CompositionError(
                    f"composition contains colliding artifact filenames: {filename!r} "
                    "with differing digests"
                )
            filename_digests[filename] = lock.artifact_sha256
        domains = {descriptor.domain for descriptor in descriptors}
        if not _REQUIRED_DOMAINS <= domains <= _ADMITTED_DOMAINS:
            raise CompositionError(
                "composition must pin work, audit and knowledge; only the lane adapter is additionally admitted"
            )
        if len(descriptors) != len(domains):
            raise CompositionError("composition contains duplicate runtime domains")
        by_id = {lock.lock_id: lock for lock in locks}
        referenced: list[str] = []
        primary_references: dict[str, int] = {}
        dependency_references: dict[str, list[RuntimeAdapterDescriptor]] = {}
        adapters: list[AdapterPin] = []
        for descriptor in descriptors:
            try:
                release_lock = by_id[descriptor.lock_id]
                dependencies = tuple(by_id[lock_id] for lock_id in descriptor.dependency_lock_ids)
            except KeyError as error:
                raise CompositionError(
                    f"{descriptor.domain}: runtime descriptor references an unknown release lock"
                ) from error
            if release_lock.lock_kind != "adapter":
                raise CompositionError(
                    f"{descriptor.domain}: primary release lock must have lock_kind adapter"
                )
            primary_references[release_lock.lock_id] = (
                primary_references.get(release_lock.lock_id, 0) + 1
            )
            for dependency in dependencies:
                dependency_references.setdefault(dependency.lock_id, []).append(descriptor)
                if dependency.lock_kind == "adapter":
                    raise CompositionError(
                        f"{descriptor.domain}: adapter release locks cannot be dependencies"
                    )
                if dependency.lock_kind == "owner-dependency":
                    if dependency.source_repository != release_lock.source_repository:
                        raise CompositionError(
                            f"{descriptor.domain}: owner dependencies must come from the same owner repository"
                        )
                elif dependency.lock_kind == "shared-dependency":
                    if dependency.source_repository != _CANONICAL_VUORO_SOURCE_REPOSITORY:
                        raise CompositionError(
                            f"{dependency.lock_id}: shared dependencies must come from the canonical Vuoro repository"
                        )
                    if dependency.distribution not in _SHARED_DEPENDENCY_DISTRIBUTIONS:
                        raise CompositionError(
                            f"{dependency.lock_id}: shared dependency distribution is not allowed"
                        )
            referenced.extend((descriptor.lock_id, *descriptor.dependency_lock_ids))
            adapters.append(AdapterPin(descriptor, release_lock, dependencies))
        for lock in locks:
            references = dependency_references.get(lock.lock_id, [])
            if lock.lock_kind == "adapter":
                if primary_references.get(lock.lock_id) != 1 or references:
                    raise CompositionError("adapter release locks must be exclusive primaries")
            elif lock.lock_kind == "owner-dependency":
                if len(references) != 1:
                    raise CompositionError(
                        f"{lock.lock_id}: owner dependency must be referenced by exactly one runtime descriptor"
                    )
            elif lock.lock_kind == "shared-dependency":
                if not references:
                    raise CompositionError(
                        f"{lock.lock_id}: shared dependency must be referenced by a runtime descriptor"
                    )
        if set(referenced) != set(lock_ids):
            raise CompositionError("composition contains an orphan release lock")
        return cls(
            schema_version=raw["schema_version"],
            release_locks=locks,
            runtime_descriptors=descriptors,
            adapters=tuple(adapters),
        )

    def pin(self, domain: str) -> AdapterPin:
        for pin in self.adapters:
            if pin.domain == domain:
                return pin
        raise CompositionError(f"missing required adapter: {domain}")


def verify_adapter_artifacts(manifest: CompositionManifest, wheel_dir: Path) -> None:
    """Verify bundled release wheels before importing their adapter modules."""

    seen: dict[str, str] = {}
    for pin in manifest.release_locks:
        filename = pin.artifact_url.rsplit("/", 1)[-1]
        existing = seen.get(filename)
        if existing is not None and existing != pin.artifact_sha256:
            raise CompositionError(f"artifact filename {filename} staged with differing digests")
        seen[filename] = pin.artifact_sha256
        artifact = wheel_dir / filename
        try:
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        except OSError as error:
            raise CompositionError(
                f"{pin.distribution}: pinned artifact is unavailable"
            ) from error
        if digest != pin.artifact_sha256:
            raise CompositionError(
                f"{pin.distribution}: pinned artifact checksum mismatch"
            )


def _installed_files_digest(distribution_name: str) -> tuple[str, int]:
    installed = distribution(distribution_name)
    files = installed.files
    if not files:
        raise CompositionError(f"{distribution_name}: installed RECORD is unavailable")
    entries: list[tuple[str, str]] = []
    for file in sorted(files, key=str):
        path = installed.locate_file(file)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise CompositionError(
                f"{distribution_name}: installed file is unavailable: {file}"
            ) from error
        entries.append((str(file), digest))
    encoded = json.dumps(entries, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest(), len(entries)


def verify_installed_composition(manifest: CompositionManifest, manifest_path: Path, attestation_path: Path) -> None:
    """Fail before adapter import unless installed files match the locked build record."""
    try:
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CompositionError("cannot load installed composition attestation") from error
    if (
        not isinstance(attestation, dict)
        or attestation.get("schema_version") != "vuoro-installed-composition/v1"
        or attestation.get("verified") is not True
        or attestation.get("manifest_sha256") != hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        or not isinstance(attestation.get("distributions"), list)
    ):
        raise CompositionError("installed composition attestation does not bind this release lock")
    expected = {lock.distribution: lock for lock in manifest.release_locks}
    entries = attestation["distributions"]
    if not all(isinstance(entry, dict) for entry in entries):
        raise CompositionError("installed composition attestation distributions must be objects")
    records = {entry.get("distribution"): entry for entry in entries}
    if len(records) != len(entries) or set(records) != set(expected):
        raise CompositionError("installed composition attestation distributions do not match release locks")
    for distribution_name, lock in expected.items():
        record = records[distribution_name]
        if (
            record.get("lock_id") != lock.lock_id
            or record.get("lock_kind") != lock.lock_kind
            or record.get("expected_version") != lock.distribution_version
            or record.get("artifact_sha256") != lock.artifact_sha256
        ):
            raise CompositionError(f"{distribution_name}: installed attestation does not match its release lock")
        try:
            digest, count = _installed_files_digest(distribution_name)
        except PackageNotFoundError as error:
            raise CompositionError(f"{distribution_name}: pinned distribution is not installed") from error
        if record.get("installed_files_sha256") != digest or record.get("installed_files_count") != count:
            raise CompositionError(f"{distribution_name}: installed files do not match build attestation")


WorkResourceObservationAuthorizer = Callable[[InvocationContext, str], bool]


def _load_work_resource_observation_authorizer(
    path: Path,
) -> WorkResourceObservationAuthorizer:
    """Load strict immutable actor/repository grants; references grant nothing."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CompositionError(
            "cannot load immutable work resource observation policy"
        ) from error
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "grants"}:
        raise CompositionError("work resource observation policy must use the v1 shape")
    if raw["schema_version"] != "vuoro-work-resource-observers/v1":
        raise CompositionError("unsupported work resource observation policy")
    grants = raw["grants"]
    if not isinstance(grants, list):
        raise CompositionError("work resource observation grants must be an array")
    allowed: set[tuple[str, str]] = set()
    for grant in grants:
        if (
            not isinstance(grant, dict)
            or set(grant) != {"actor", "repo_ids"}
            or not isinstance(grant["actor"], str)
            or not grant["actor"]
            or not isinstance(grant["repo_ids"], list)
            or not grant["repo_ids"]
            or any(not isinstance(repo_id, str) or not repo_id for repo_id in grant["repo_ids"])
        ):
            raise CompositionError("invalid work resource observation grant")
        for repo_id in grant["repo_ids"]:
            pair = (grant["actor"], repo_id)
            if pair in allowed:
                raise CompositionError("duplicate work resource observation grant")
            allowed.add(pair)
    immutable_allowed = frozenset(allowed)
    return lambda context, _resource_ref: (
        context.repo_id is not None
        and (context.identity.actor, context.repo_id) in immutable_allowed
    )


def _bind_work_resource_visibility(
    registry: CatalogRegistry,
    work_application: Any,
    authorizer: WorkResourceObservationAuthorizer | None,
) -> None:
    """Bind a separately injected owner-observation decision, failing closed."""

    resource_kind = "work.maintenance-capability"
    if not registry.has_resource_kind(resource_kind):
        return
    if authorizer is None:
        raise CompositionError(
            "work: resource observation policy is required when maintenance resources are registered"
        )
    registry.register_resource_visibility(
        resource_kind,
        lambda resource_ref, context: work_application._scoped_for(
            context.repo_id
        ).maintenance_resource_visible(
            resource_ref,
            authorized=authorizer(context, resource_ref) is True,
        ),
        visibility_reference_pattern=r"^smr1_[A-Za-z0-9_-]{43}$",
    )
def load_identities(path: Path, *, environment: str) -> StaticBearerIdentityResolver:
    """Load opaque environment-bound bearer identities from a mounted secret file."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CompositionError("cannot load mounted Vuoro identity registry") from error
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "identities"}:
        raise CompositionError("identity registry must use the v1 shape")
    if raw["schema_version"] != "vuoro-identities/v1" or not isinstance(raw["identities"], dict):
        raise CompositionError("unsupported Vuoro identity registry")
    identities: dict[str, Identity] = {}
    for token, identity in raw["identities"].items():
        if not isinstance(token, str) or len(token) < 32 or not isinstance(identity, dict):
            raise CompositionError("identity registry contains an invalid identity")
        allowed_fields = {"actor", "environment", "authorities", "repo_ids", "principal_id", "workspace_id"}
        # principal_id is required, not optional-with-a-fallback. Falling back to
        # actor is exactly the failure the field exists to prevent: ownership in
        # the federation authority binds this string forever and has no transfer
        # operation, so an actor rename would move every resource it owns to
        # whoever holds the name next.
        required_fields = {"actor", "environment", "authorities", "principal_id"}
        if not required_fields <= set(identity) <= allowed_fields:
            raise CompositionError("identity registry contains unsupported identity fields")
        # Native private deployments may issue an explicit workspace partition.
        # Its issuer owns this binding; request/actor/environment fields cannot
        # infer it. Absence retains the existing unbound identity behavior.
        workspace_id = identity.get("workspace_id")
        if "workspace_id" in identity and (
            not isinstance(workspace_id, str)
            or not workspace_id
            or any(character.isspace() for character in workspace_id)
        ):
            raise CompositionError("identity registry workspace_id must be nonempty and contain no whitespace")
        actor = identity["actor"]
        bound_environment = identity["environment"]
        authorities = identity["authorities"]
        repo_ids = identity.get("repo_ids", [])
        if (
            not isinstance(actor, str)
            or not actor
            or bound_environment != environment
            or not isinstance(authorities, list)
            or not all(isinstance(authority, str) and authority for authority in authorities)
            or not isinstance(repo_ids, list)
            or not all(isinstance(entry, str) and entry for entry in repo_ids)
        ):
            raise CompositionError("identity registry is not bound to this environment")
        if any(authority.startswith("work:") for authority in authorities) and not repo_ids:
            raise CompositionError(
                "identity registry entries with a work: authority must set repo_ids"
            )
        try:
            identities[token] = Identity(
                actor=actor,
                environment=bound_environment,
                authorities=frozenset(authorities),
                repo_ids=frozenset(repo_ids),
                principal_id=identity["principal_id"],
                workspace_id=workspace_id,
            )
        except IdentityResolutionError as error:
            raise CompositionError(f"identity registry: {error}") from error
    if not identities:
        raise CompositionError("identity registry must contain at least one identity")
    return StaticBearerIdentityResolver(identities)


def load_development_identities(path: Path, *, environment: str) -> StaticBearerIdentityResolver:
    """Compatibility alias for callers of the former development-only loader."""

    return load_identities(path, environment=environment)


def _runtime_env(
    name: str, environ: Mapping[str, str], *, aliases: tuple[str, ...] = ()
) -> str:
    value = environ[name] if name in environ else next(
        (environ[alias] for alias in aliases if alias in environ), None
    )
    if not value:
        raise CompositionError(f"{name} is required for three-domain composition")
    return value


def _validate_expected_environment(value: str) -> str:
    if not value or value != value.strip() or "\x00" in value:
        raise CompositionError(
            "VUORO_ENVIRONMENT_NAME must be a non-empty environment name"
        )
    return value


def _runtime_path(
    name: str,
    environ: Mapping[str, str],
    *,
    default: str | Path,
    mounted_path: str | Path = _CLOUD_PROJECT_BINDINGS_PATH,
    mount_label: str = "approved Cloud binding mount",
) -> Path:
    """Resolve the embedded file or the one approved Cloud ConfigMap path.

    The environment variable is a deployment selector, not an arbitrary file
    authority.  Vuoro Cloud owns the read-only ConfigMap mounted at the exact
    ``mounted_path``; accepting any other path would let deployment config
    substitute a different membership document.
    """

    value = environ.get(name)
    if value is None:
        return Path(default)
    if not value or value != value.strip() or "\x00" in value:
        raise CompositionError(f"{name} must name the {mount_label}")
    path = Path(value)
    if path != Path(mounted_path):
        raise CompositionError(f"{name} must name the {mount_label}")
    return path


def _load_project_bindings_file(
    path: Path,
    *,
    trusted_root: Path | None = None,
) -> tuple[ProjectBinding, ...]:
    """Read a UTF-8 JSON binding file and normalize startup failures."""

    try:
        resolved = path.resolve(strict=True)
        if trusted_root is not None:
            root = trusted_root.resolve(strict=True)
            try:
                resolved.relative_to(root)
            except ValueError as error:
                raise CompositionError(
                    "mounted project bindings resolve outside the trusted ConfigMap root"
                ) from error
        document = json.loads(resolved.read_bytes().decode("utf-8"))
    except CompositionError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompositionError("cannot load mounted project bindings") from error
    if not isinstance(document, dict):
        raise CompositionError("mounted project bindings must be a JSON object")
    try:
        return load_project_bindings(document)
    except ProjectBindingError as error:
        raise CompositionError("mounted project bindings are invalid") from error


def _load_project_binding_for_composition(
    path: Path,
    *,
    trusted_root: Path | None = None,
    expected_environment: str | None = None,
) -> ProjectBinding:
    bindings = _load_project_bindings_file(path, trusted_root=trusted_root)
    if len(bindings) != 1:
        raise CompositionError("this release must contain exactly one project binding")
    binding = bindings[0]
    if binding.environment is not None:
        if expected_environment is None:
            raise CompositionError(
                "VUORO_ENVIRONMENT_NAME is required for hosted project bindings"
            )
        _validate_expected_environment(expected_environment)
        if binding.environment != expected_environment:
            raise CompositionError(
                "hosted project binding environment does not match "
                "VUORO_ENVIRONMENT_NAME"
            )
    return binding


def _validate_identity_mode(
    project_binding: ProjectBinding, *, gateway_key_configured: bool
) -> None:
    if project_binding.environment is not None and not gateway_key_configured:
        raise CompositionError(
            "hosted project bindings require gateway assertion identity mode"
        )


def _edge_proof_key(environ: Mapping[str, str]) -> bytes | None:
    if environ.get("VUORO_EDGE_PROOF_KEY_FILE") is None:
        return None
    path = _runtime_path(
        "VUORO_EDGE_PROOF_KEY_FILE",
        environ,
        default=_EDGE_PROOF_KEY_PATH,
        mounted_path=_EDGE_PROOF_KEY_PATH,
        mount_label="pod-local edge proof key mount",
    )
    try:
        return load_or_create_proof_key(path)
    except EdgeProofConfigurationError as error:
        raise CompositionError("cannot configure the edge proof key") from error


def load_edge_proof_key(environ: Mapping[str, str] | None = None) -> bytes:
    """The pod's edge proof key, for the protocol edge (`vuoro_service.edge_proof`).

    The edge requires it: without proofs, the shell would see each forwarded
    assertion as a direct, one-use credential and refuse a tool call's second
    upstream call as a replay.
    """

    import os

    environ = os.environ if environ is None else environ
    key = _edge_proof_key(environ)
    if key is None:
        raise CompositionError("the protocol edge requires VUORO_EDGE_PROOF_KEY_FILE")
    return key


def _gateway_assertion_resolver(
    environ: Mapping[str, str],
    *,
    environment_name: str,
    project_binding: ProjectBinding,
    edge: bool = False,
) -> GatewayAssertionIdentityResolver:
    started_at = time.time()
    shell_audience = environ.get("VUORO_GATEWAY_ASSERTION_AUDIENCE", "vuoro-service")
    edge_audience = environ.get("VUORO_EDGE_GATEWAY_ASSERTION_AUDIENCE")
    if edge:
        # The edge is the first verifier on its path: it expects the edge
        # audience and accepts no edge proofs.  While that audience is the
        # shell's too, it also requires the OAuth client and grant only the
        # gateway's OAuth path mints, so a REST assertion the shell consumed
        # cannot be replayed through the edge.
        audience = shell_audience if edge_audience is None else edge_audience
        require_oauth_grant = audience == shell_audience
        edge_proofs = None
    else:
        audience = shell_audience
        require_oauth_grant = False
        proof_key = _edge_proof_key(environ)
        edge_proofs = (
            None
            if proof_key is None
            else EdgeProofVerifier(proof_key, not_before=started_at)
        )
    gateway_key_path = _runtime_path(
        "VUORO_GATEWAY_PUBLIC_KEY_FILE",
        environ,
        default=_CLOUD_GATEWAY_PUBLIC_KEY_PATH,
        mounted_path=_CLOUD_GATEWAY_PUBLIC_KEY_PATH,
        mount_label="approved Cloud gateway key mount",
    )
    try:
        return GatewayAssertionIdentityResolver.from_file(
            gateway_key_path,
            issuer=_runtime_env("VUORO_GATEWAY_ASSERTION_ISSUER", environ),
            audience=audience,
            environment=environment_name,
            expected_workspace_id=_runtime_env("VUORO_WORKSPACE_ID", environ),
            allowed_repo_ids=frozenset(project_binding.repo_ids),
            key_id=environ.get("VUORO_GATEWAY_ASSERTION_KEY_ID", "gateway-2026-01"),
            trusted_root=_CLOUD_BINDINGS_TRUST_ROOT,
            replay_not_before=started_at - _REPLAY_WATERMARK_SKEW_SECONDS,
            edge_proofs=edge_proofs,
            edge_audience=None if edge else edge_audience,
            require_oauth_grant=require_oauth_grant,
        )
    except GatewayAssertionConfigurationError as error:
        raise CompositionError("cannot configure gateway assertion identity") from error


def load_gateway_assertion_resolver(
    environ: Mapping[str, str] | None = None,
    *,
    project_bindings_path: Path | None = None,
    edge: bool = False,
) -> GatewayAssertionIdentityResolver:
    """Build the gateway assertion verifier exactly as the runtime shell does.

    For a sibling process that must trust precisely what this shell trusts:
    the same key mount, issuer, audience, key id, workspace, environment and
    project binding, read from the same variables, with the same refusals.
    It loads no DSN and no identity registry, and it refuses to start
    without gateway assertion identity mode configured.

    `edge` builds the protocol edge's verifier: the first verifier on its
    path, expecting ``VUORO_EDGE_GATEWAY_ASSERTION_AUDIENCE`` (default: the
    shell's audience) and consuming each jti itself.
    """

    import os

    environ = os.environ if environ is None else environ
    environment_name = _validate_expected_environment(
        _runtime_env(
            "VUORO_ENVIRONMENT_NAME", environ, aliases=("VUORO_ENVIRONMENT",)
        )
    )
    bindings_path = project_bindings_path or _runtime_path(
        "VUORO_PROJECT_BINDINGS_FILE",
        environ,
        default=_DEFAULT_PROJECT_BINDINGS_PATH,
    )
    project_binding = _load_project_binding_for_composition(
        bindings_path,
        expected_environment=environment_name,
        trusted_root=(
            _CLOUD_BINDINGS_TRUST_ROOT
            if bindings_path == _CLOUD_PROJECT_BINDINGS_PATH
            else None
        ),
    )
    if environ.get("VUORO_GATEWAY_PUBLIC_KEY_FILE") is None:
        raise CompositionError(
            "gateway assertion identity mode requires VUORO_GATEWAY_PUBLIC_KEY_FILE"
        )
    if project_binding.environment is None:
        raise CompositionError(
            "gateway assertion identity mode requires hosted project bindings"
        )
    return _gateway_assertion_resolver(
        environ,
        environment_name=environment_name,
        project_binding=project_binding,
        edge=edge,
    )


def _pg_connection_factory(dsn: str) -> Callable[[], Any]:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:  # pragma: no cover - dependency contract
        raise CompositionError("pinned adapter extras must provide psycopg") from error
    return lambda: psycopg.connect(dsn, row_factory=dict_row)


def _compatibility(domain: str, record: Mapping[str, Any], pin: AdapterPin) -> DomainCompatibility:
    compatible = record.get("compatible")
    if compatible is None:
        compatible = record.get("state") == "compatible"
    schema_version = record.get("schema_version") or record.get("observed_schema_version") or pin.schema_version
    if not isinstance(schema_version, str):
        schema_version = str(schema_version)
    reason = record.get("reason") or record.get("detail")
    return DomainCompatibility(
        api_version=pin.api_version,
        schema_version=schema_version,
        state="compatible" if compatible else "incompatible",
        reason=None if compatible else (str(reason) if reason else "runtime compatibility check failed"),
    )


def _load_recipient_admission_keys(path: Path) -> dict[str, Ed25519PublicKey]:
    """Load only a bounded, exact public keyset; never accept private material."""
    try:
        if path.stat().st_size > 16_384:
            raise ValueError
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_without_duplicates,
        )
        if (
            not isinstance(document, dict)
            or set(document) != {"schema", "keys"}
            or document["schema"] != "vuoro-recipient-admission-public-keys/v1"
            or not isinstance(document["keys"], dict)
            or not 1 <= len(document["keys"]) <= 16
        ):
            raise ValueError
        result = {}
        for kid, encoded in document["keys"].items():
            if (
                not isinstance(kid, str)
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", kid)
                or not isinstance(encoded, str)
                or not encoded
            ):
                raise ValueError
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode().rstrip("=") != encoded:
                raise ValueError
            result[kid] = Ed25519PublicKey.from_public_bytes(raw)
        return result
    except (OSError, ValueError, TypeError, UnicodeError, binascii.Error) as error:
        raise CompositionError("recipient-admission public keyset is unavailable") from error


def _json_object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _member_store_view(work_store: Any, repo_id: str) -> Any:
    """Return an independently scoped view over the owner's connection.

    Sprintctl stores its repository scope on the store object.  A shallow copy
    gives each long-lived member application its own fixed scope while retaining
    the one underlying database connection.  Never retarget ``work_store`` for
    a request: member views may be invoked concurrently.
    """
    member_store = copy.copy(work_store)
    member_store.repo_id = repo_id
    return member_store


def _member_work_application_factory(
    work_store: Any, work_application_type: Any
) -> Callable[[str], Any]:
    """Build fixed-scope member applications without opening member connections."""
    def make_member_application(repo_id: str) -> Any:
        member_store = _member_store_view(work_store, repo_id)
        member_store.remote_schema_version = getattr(
            work_store, "remote_schema_version", None
        )
        return work_application_type.postgres(member_store)

    return make_member_application


def _bounded_store_lease_factory(
    *,
    work_pg: Any,
    work_dsn: str,
    repository_ids: frozenset[str],
    max_connections: int = 2,
    acquire_timeout: float = 5.0,
) -> Callable[[str], Any]:
    """Create private-delivery store leases with a fixed concurrent limit."""
    if max_connections < 1:
        raise ValueError("private delivery connection limit must be positive")
    if acquire_timeout < 0:
        raise ValueError("private delivery connection acquire timeout must be non-negative")
    capacity = threading.BoundedSemaphore(max_connections)

    @contextmanager
    def store_lease_for_repo(repo_id: str):
        if repo_id not in repository_ids:
            raise ValueError("private delivery repo is outside the hosted binding")
        acquired = capacity.acquire(timeout=acquire_timeout)
        if not acquired:
            raise PrivateDeliveryCapacityError(
                "private delivery connection capacity unavailable"
            )
        store = None
        try:
            store = work_pg.get_connection(work_dsn)
            store.repo_id = repo_id
            yield store
        finally:
            try:
                if store is not None:
                    store.conn.close()
            finally:
                capacity.release()

    return store_lease_for_repo


def _mount_private_recipient_delivery(
    app: Any,
    *,
    environ: Mapping[str, str],
    work_pg: Any,
    work_dsn: str,
    workspace_id: str,
    repository_ids: frozenset[str],
    environment_name: str,
    gateway_public_key_file: str | None,
    private_ingress_guard: Callable[[], bool] | None,
) -> bool:
    """Mount owner routers only after explicit, complete deployment config."""
    enabled = environ.get("VUORO_OPERATOR_REQUEST_DELIVERY_ENABLED", "false")
    if enabled == "false":
        return False
    if enabled != "true":
        raise CompositionError("operator request delivery enable flag is invalid")
    if gateway_public_key_file is None:
        raise CompositionError("operator request delivery requires gateway identity trust")
    if not callable(private_ingress_guard):
        raise CompositionError("operator request delivery has no trusted private ingress guard")
    try:
        issuer = _runtime_env("VUORO_GATEWAY_ASSERTION_ISSUER", environ)
        gateway_key_id = environ.get("VUORO_GATEWAY_ASSERTION_KEY_ID", "gateway-2026-01")
        admission_keyset = Path(environ["VUORO_RECIPIENT_ADMISSION_PUBLIC_KEYSET_FILE"])
        admission_issuer = environ["VUORO_RECIPIENT_ADMISSION_ISSUER"]
    except (KeyError, ValueError) as error:
        raise CompositionError("operator request delivery trust configuration is incomplete") from error
    admission_keys = _load_recipient_admission_keys(admission_keyset)
    try:
        gateway_public_key = serialization.load_pem_public_key(
            Path(gateway_public_key_file).read_bytes()
        )
    except (OSError, ValueError) as error:
        raise CompositionError("gateway verification key is unavailable") from error
    if not isinstance(gateway_public_key, Ed25519PublicKey):
        raise CompositionError("gateway verification key must be Ed25519")
    service_verifier = load_cloud_service_assertion_verifier(
        dict(environ),
        workspace_id=workspace_id,
        forbidden_public_keys=(gateway_public_key, *admission_keys.values()),
    )

    try:
        from sprintctl.recipient_delivery_read_receiver import (
            PrivateDeliveryReadConfig,
            create_private_delivery_read_router,
        )
        from sprintctl.recipient_delivery_receiver import (
            PrivateDeliveryReceiverConfig,
            create_private_delivery_router,
        )
    except ImportError as error:
        raise CompositionError(
            "pinned Sprintctl adapter does not include private delivery routes"
        ) from error

    store_lease_for_repo = _bounded_store_lease_factory(
        work_pg=work_pg,
        work_dsn=work_dsn,
        repository_ids=repository_ids,
    )

    # The hosting composition must inject its trusted network guard. This
    # runtime has no data-plane primitive that can prove a Kubernetes
    # NetworkPolicy decision from HTTP request metadata.
    replay_not_before = time.time() - 1
    gateway_common = {
        "gateway_public_key_file": Path(gateway_public_key_file),
        "gateway_issuer": issuer,
        "gateway_environment": environment_name,
        "expected_workspace_id": workspace_id,
        "allowed_repo_ids": repository_ids,
        "gateway_key_id": gateway_key_id,
        "replay_not_before": replay_not_before,
        "private_ingress_guard": private_ingress_guard,
        "trusted_cloud_adapter_guard": service_verifier,
        "store_lease_for_repo": store_lease_for_repo,
    }
    delivery_config = PrivateDeliveryReceiverConfig(
        enabled=True,
        admission_keys=admission_keys,
        admission_issuer=admission_issuer,
        **gateway_common,
    )
    read_config = PrivateDeliveryReadConfig(enabled=True, **gateway_common)
    app.include_router(create_private_delivery_router(delivery_config))
    app.include_router(create_private_delivery_read_router(read_config))
    return True


def _attach_private_delivery_state(
    app: Any,
    *,
    private_app: Any,
    work_store: Any,
    mounted: bool,
) -> None:
    """Retain the bootstrap connection until the owner-only app is extracted."""
    app.state.private_delivery_app = private_app if mounted else None
    app.state.private_delivery_bootstrap_store = work_store


def _load_function(pin: AdapterPin) -> Callable[..., Any]:
    for dependency in pin.dependencies:
        try:
            installed_dependency_version = version(dependency.distribution)
        except PackageNotFoundError as error:
            raise CompositionError(
                f"{pin.domain}: pinned dependency is not installed: {dependency.distribution}"
            ) from error
        if installed_dependency_version != dependency.distribution_version:
            raise CompositionError(
                f"{pin.domain}: installed dependency version does not match the composition pin"
            )
    try:
        installed_version = version(pin.distribution)
    except PackageNotFoundError as error:
        raise CompositionError(f"{pin.domain}: pinned distribution is not installed") from error
    if installed_version != pin.distribution_version:
        raise CompositionError(
            f"{pin.domain}: installed distribution version does not match the composition pin"
        )
    module = importlib.import_module(pin.adapter_module)
    function = getattr(module, pin.register, None)
    if not callable(function):
        raise CompositionError(f"{pin.domain}: pinned adapter registration function is unavailable")
    return function


def _compose_knowledge(
    pin: AdapterPin, registry: CatalogRegistry, environ: Mapping[str, str]
) -> DomainCompatibility:
    """Register the pinned knowledge workspace adapter and gate its schema.

    The gate is the owner's read-only compatibility check: startup never
    migrates (only the operator ``vuoro-workspace migrate`` entrypoint, run as
    the migration role, executes DDL), and ``VUORO_WORKSPACE_SCHEMA_MODE`` is
    not consulted on this path.
    """

    if (
        pin.adapter_module != _KNOWLEDGE_ADAPTER_MODULE
        or pin.register != _KNOWLEDGE_REGISTER
    ):
        raise CompositionError(
            "knowledge: manifest does not select the owner adapter registration"
        )
    register = _load_function(pin)
    from vuoro_workspace.vuoro_adapter import (
        KnowledgeApplication,
        object_store_from_uri,
    )

    dsn = _runtime_env(
        "VUORO_KNOWLEDGE_RUNTIME_DSN", environ, aliases=("VUORO_KNOWLEDGE_DSN",)
    )
    try:
        object_store = object_store_from_uri(
            _runtime_env("VUORO_KNOWLEDGE_OBJECT_STORE", environ), environ
        )
    except (ValueError, RuntimeError, OSError) as error:
        raise CompositionError(f"knowledge: {error}") from error
    schema = environ.get("VUORO_KNOWLEDGE_SCHEMA") or "knowledge"
    # Required, never defaulted to the connecting role: the role check is what
    # keeps an owner/migration DSN from serving (fail-closed role_not_configured).
    runtime_role = _runtime_env("VUORO_KNOWLEDGE_RUNTIME_ROLE", environ)
    application = KnowledgeApplication.from_dsn(
        dsn, object_store, schema=schema, runtime_role=runtime_role
    )
    register(registry, application)
    try:
        record = application.compatibility()
    except Exception as error:  # connection failures are reported, never retried here
        record = {"compatible": False, "reason": f"knowledge runtime unavailable: {type(error).__name__}"}
    if record.get("compatible") and record.get("domain_api_version") != pin.api_version:
        record = {
            **record,
            "compatible": False,
            "reason": "knowledge store API version does not match the composition pin",
        }
    return _compatibility("knowledge", record, pin)



def _compose_lane(pin: AdapterPin, registry: CatalogRegistry, environ: Mapping[str, str]) -> DomainCompatibility:
    """Compose the released lane owner; its migrations and semantics stay native."""
    if (pin.adapter_module != "lanectl.vuoro_adapter" or pin.register != "register_lane_catalog"
            or pin.distribution != "lanectl" or pin.api_version != "v1" or pin.schema_version != "1"):
        raise CompositionError("lane: manifest does not select the supported owner adapter contract")
    if sys.version_info[:2] < (3, 13):
        raise CompositionError("lane: owner requires Python >=3.13")
    from lanectl.stream_store import StreamStore, StreamError
    try:
        store = StreamStore(Path(_runtime_env("VUORO_LANE_DATABASE", environ)))
    except StreamError as error:
        raise CompositionError("lane: owner database is unavailable or incompatible; run owner migration separately") from error
    _load_function(pin)(registry, store)
    return DomainCompatibility(api_version=pin.api_version, schema_version=pin.schema_version, state="compatible")

def create_composed_app(
    *,
    manifest_path: Path | None = None,
    wheel_dir: Path | None = None,
    identity_path: Path | None = None,
    project_bindings_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    work_resource_observation_authorizer: WorkResourceObservationAuthorizer | None = None,
    private_delivery_ingress_guard: Callable[[], bool] | None = None,
):
    """Create the only deployable service application: the pinned composition."""

    import os

    environ = environ or os.environ
    environment_name = _validate_expected_environment(
        _runtime_env(
            "VUORO_ENVIRONMENT_NAME", environ, aliases=("VUORO_ENVIRONMENT",)
        )
    )
    environment_class = _runtime_env("VUORO_ENVIRONMENT_CLASS", environ)
    if environment_class not in _DEPLOYABLE_ENVIRONMENT_CLASSES:
        raise CompositionError("this composition requires a deployable environment class")

    environment_constraints: tuple[str, ...] = ()
    environment_runbook_refs: tuple[str, ...] = ()
    environment_record_path = environ.get("VUORO_ENVIRONMENT_RECORD_PATH")
    if environment_record_path:
        record = load_environment_record(Path(environment_record_path))
        if record.environment_class != environment_class:
            raise CompositionError(
                "environment record environment_class "
                f"{record.environment_class!r} does not match "
                f"VUORO_ENVIRONMENT_CLASS {environment_class!r}"
            )
        environment_constraints = record.constraints
        environment_runbook_refs = record.runbook_refs
    resolved_manifest_path = manifest_path or Path(_runtime_env("VUORO_COMPOSITION_MANIFEST", environ))
    manifest = CompositionManifest.load(resolved_manifest_path)
    verify_adapter_artifacts(manifest, wheel_dir or Path(_runtime_env("VUORO_ADAPTER_WHEEL_DIR", environ)))
    verify_installed_composition(
        manifest,
        resolved_manifest_path,
        Path(_runtime_env("VUORO_INSTALLED_COMPOSITION_PATH", environ)),
    )
    if work_resource_observation_authorizer is None:
        work_resource_observation_authorizer = (
            _load_work_resource_observation_authorizer(
                Path(_runtime_env("VUORO_WORK_RESOURCE_OBSERVERS_FILE", environ))
            )
        )
    registry = CatalogRegistry()

    work_pin = manifest.pin("work")
    from sprintctl import pg as work_pg
    from sprintctl import pg_migrations as work_migrations
    from sprintctl.application import (
        ApplicationRejection,
        ProjectMemberApplication,
        ProjectWorkApplication,
        WorkApplication,
    )

    # VUORO_WORK_REPOSITORY_ID only seeds this template instance; every served
    # invocation re-scopes to the client-supplied, identity-authorized
    # repo_id from the invocation envelope (_dispatch validates it against
    # Identity.authorizes_repo before calling WorkApplication.invoke, which
    # re-scopes via _scoped_for), so this application can serve every
    # repository tenant a bound identity is authorized for -- not only this
    # one.
    work_store = work_pg.get_connection(
        _runtime_env("VUORO_WORK_RUNTIME_DSN", environ, aliases=("VUORO_WORK_DSN",))
    )
    work_store.repo_id = _runtime_env("VUORO_WORK_REPOSITORY_ID", environ)
    work_application = WorkApplication.postgres(work_store)
    # Seed the owner's runtime with the read-only observed schema before
    # registering its schema-gated operations. Sprintctl 0.23 omits all work
    # operations when the observed version is its initial sentinel (zero).
    work_handshake = work_migrations.compatibility_handshake(work_store)
    work_state = _compatibility("work", work_handshake, work_pin)
    if work_state.state == "compatible":
        remote_schema = work_handshake.get("remote_schema")
        if isinstance(remote_schema, Mapping):
            work_store.remote_schema_version = remote_schema.get("actual")
    bindings_path = project_bindings_path or _runtime_path(
        "VUORO_PROJECT_BINDINGS_FILE",
        environ,
        default=_DEFAULT_PROJECT_BINDINGS_PATH,
    )
    project_binding = _load_project_binding_for_composition(
        bindings_path,
        expected_environment=environment_name,
        trusted_root=(
            _CLOUD_BINDINGS_TRUST_ROOT
            if bindings_path == _CLOUD_PROJECT_BINDINGS_PATH
            else None
        ),
    )
    gateway_key_value = environ.get("VUORO_GATEWAY_PUBLIC_KEY_FILE")
    _validate_identity_mode(
        project_binding, gateway_key_configured=gateway_key_value is not None
    )
    if gateway_key_value is not None:
        if identity_path is not None or "VUORO_IDENTITIES_FILE" in environ:
            raise CompositionError(
                "gateway assertion identity mode cannot be combined with a static identity registry"
            )
        if project_binding.environment is None:
            raise CompositionError(
                "gateway assertion identity mode requires hosted project bindings"
            )
        resolver = _gateway_assertion_resolver(
            environ,
            environment_name=environment_name,
            project_binding=project_binding,
        )
    else:
        resolver = load_identities(
            identity_path or Path(_runtime_env("VUORO_IDENTITIES_FILE", environ)),
            environment=environment_name,
        )
    work_dsn = _runtime_env(
        "VUORO_WORK_RUNTIME_DSN", environ, aliases=("VUORO_WORK_DSN",)
    )

    make_member_application = _member_work_application_factory(
        work_store, WorkApplication
    )

    def make_project_application(
        project_id: str, members: tuple[tuple[str, WorkApplication], ...]
    ) -> ProjectWorkApplication:
        canonical_binding = {
            "project_id": project_binding.project_id,
            "home_repo": project_binding.home_repo,
            "backlog_repos": list(project_binding.repo_ids),
            "source_repository": project_binding.source_repository,
            "source_revision": project_binding.source_revision,
            "source_path": project_binding.source_path,
            "source_sha256": project_binding.source_sha256,
        }
        if project_binding.environment is not None:
            canonical_binding.update(
                {
                    "environment": project_binding.environment,
                    "descriptor_digest": project_binding.descriptor_digest,
                    "repositories": [
                        {
                            "repo_id": member.repo_id,
                            "git_remote": member.git_remote,
                            "commit_sha": member.commit_sha,
                        }
                        for member in project_binding.members
                    ],
                }
            )
        return ProjectWorkApplication(
            project_id,
            tuple(
                ProjectMemberApplication(origin_repo, application)
                for origin_repo, application in members
            ),
            canonical_binding=canonical_binding,
        )

    guarded_project_application = compose_authorized_project_application(
        project_binding,
        make_member_application=make_member_application,
        make_project_application=make_project_application,
    )

    class ProjectApplicationBridge:
        def invoke(self, operation: str, arguments: Mapping[str, Any], context: Any):
            try:
                return guarded_project_application.invoke(operation, arguments, context)
            except ProjectAuthorizationError as error:
                raise ApplicationRejection(
                    "project-repo-unauthorized", str(error), 403
                ) from error

    _load_function(work_pin)(
        registry, work_application, project_application=ProjectApplicationBridge()
    )
    # Sprintctl's schema-gated resource descriptors are additive.  Old released
    # adapters omit them entirely; once present, bind the owner's repository-
    # scoped visibility proof without transferring lifecycle authority to Vuoro.
    _bind_work_resource_visibility(
        registry, work_application, work_resource_observation_authorizer
    )

    audit_pin = manifest.pin("audit")
    from auditctl.vuoro_adapter import VuoroAuditAdapter

    audit_adapter = VuoroAuditAdapter(
        connection_factory=_pg_connection_factory(
            _runtime_env(
                "VUORO_AUDIT_RUNTIME_DSN", environ, aliases=("VUORO_AUDIT_DSN",)
            )
        ),
        schema=_runtime_env("VUORO_AUDIT_SCHEMA", environ),
    )
    # Auditctl's released adapter exposes an instance registration method,
    # unlike the shared function-registration protocol used by the other
    # domains. Keep that owner-specific exception explicit until Auditctl
    # publishes a compatible registration contract.
    if audit_pin.adapter_module != "auditctl.vuoro_adapter" or audit_pin.register != "VuoroAuditAdapter.register":
        raise CompositionError("audit: manifest does not select the owner adapter registration")
    audit_adapter.register(registry)
    audit_state = _compatibility("audit", audit_adapter.compatibility(), audit_pin)

    domains = {
        "work": work_state,
        "audit": audit_state,
    }
    domains["knowledge"] = _compose_knowledge(
        manifest.pin("knowledge"), registry, environ
    )
    if any(pin.domain == "lane" for pin in manifest.adapters):
        domains["lane"] = _compose_lane(manifest.pin("lane"), registry, environ)
    incompatible = [name for name, state in domains.items() if state.state != "compatible"]
    if incompatible:
        raise CompositionError("runtime compatibility failed for: " + ", ".join(incompatible))
    # Source support is staged without changing the immutable work-owner pin.
    # Older owners have no lookup and refuse reserved credentials. Once a
    # qualified owner release supplies the API, it owns all record checks.
    from vuoro_service.run_delegation import DelegatedBearerIdentityResolver
    resolver = DelegatedBearerIdentityResolver(
        resolver, environment=environment_name,
        lookup=getattr(work_application, "resolve_run_delegation_digest", None),
        expected_workspace_id=(
            _runtime_env("VUORO_WORKSPACE_ID", environ) if gateway_key_value is not None else None
        ),
        allowed_repo_ids=(frozenset(project_binding.repo_ids) if gateway_key_value is not None else None),
    )
    app = create_app(
        settings=ServiceSettings(
            environment_name=environment_name,
            environment_class=environment_class,
            environment_constraints=environment_constraints,
            environment_runbook_refs=environment_runbook_refs,
            domains=domains,
            compatibility_state="compatible",
        ),
        registry=registry,
        identity_resolver=resolver,
        readiness_check=work_application.served_runtime_ready,
    )
    from fastapi import FastAPI

    private_delivery_app = FastAPI(
        openapi_url=None, docs_url=None, redoc_url=None
    )
    private_delivery_mounted = _mount_private_recipient_delivery(
        private_delivery_app,
        environ=environ,
        work_pg=work_pg,
        work_dsn=work_dsn,
        workspace_id=(
            _runtime_env("VUORO_WORKSPACE_ID", environ)
            if gateway_key_value is not None
            else ""
        ),
        repository_ids=(
            frozenset(project_binding.repo_ids)
            if project_binding.environment is not None
            else frozenset()
        ),
        environment_name=environment_name,
        gateway_public_key_file=gateway_key_value,
        private_ingress_guard=private_delivery_ingress_guard,
    )
    # The private-app factory discards this general shell after extracting the
    # private child. Keep the bootstrap handle even when the private route is
    # disabled so that factory can close it on its fail-closed exit too.
    _attach_private_delivery_state(
        app,
        private_app=private_delivery_app,
        work_store=work_store,
        mounted=private_delivery_mounted,
    )
    return app


def create_private_delivery_app(
    *, private_delivery_ingress_guard: Callable[[], bool] | None = None
):
    """Build the owner-only ASGI app; host it on an isolated loopback listener."""
    app = create_composed_app(
        private_delivery_ingress_guard=private_delivery_ingress_guard
    )
    private_app = getattr(app.state, "private_delivery_app", None)
    bootstrap_store = getattr(app.state, "private_delivery_bootstrap_store", None)
    try:
        if private_app is None:
            raise CompositionError("private owner listener is disabled")
        if bootstrap_store is None:
            raise CompositionError(
                "private owner listener bootstrap store is unavailable"
            )
        return private_app
    finally:
        # The base composition opened this connection to verify owner
        # compatibility and construct the ordinary shell. The private app
        # performs owner work through fresh leases, so close the bootstrap
        # connection before discarding the general shell app, on success or
        # failure.
        if bootstrap_store is not None:
            bootstrap_store.conn.close()


__all__ = [
    "AdapterPin",
    "CompositionError",
    "CompositionManifest",
    "ReleaseLock",
    "RuntimeAdapterDescriptor",
    "create_composed_app",
    "create_private_delivery_app",
    "load_development_identities",
    "load_edge_proof_key",
    "load_gateway_assertion_resolver",
    "load_identities",
    "verify_adapter_artifacts",
    "verify_installed_composition",
]

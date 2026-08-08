"""Pinned four-domain Vuoro service composition.

The service accepts domain adapters only through the checked-in composition
manifest. Deployment supplies runtime DSNs and an environment-bound identity
registry, but cannot add, replace, or remove catalog operations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
import hashlib
import importlib
from importlib.metadata import PackageNotFoundError, distribution, version
import json
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import urlsplit

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.contracts import DomainCompatibility
from vuoro_service.environment_record import load_environment_record
from vuoro_service.identity import Identity, StaticBearerIdentityResolver
from vuoro_service.project_binding import (
    ProjectAuthorizationError,
    ProjectBindingError,
    compose_authorized_project_application,
    load_project_bindings,
)


_REQUIRED_DOMAINS = frozenset({"work", "execution", "knowledge", "audit"})
_DEPLOYABLE_ENVIRONMENT_CLASSES = frozenset({"development", "production"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_RELEASE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class CompositionError(RuntimeError):
    """The immutable release composition or runtime configuration is invalid."""


@dataclass(frozen=True)
class ReleaseLock:
    """Immutable identity of one wheel installed in this service release.

    This deliberately contains no domain or adapter registration information:
    the same release lock can be a companion dependency rather than a catalog
    adapter.  Runtime selection belongs to :class:`RuntimeAdapterDescriptor`.
    """

    lock_id: str
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
            raise CompositionError("release lock fields do not match the v2 contract")
        pin = cls(**{field: raw[field] for field in field_names})
        if not all(
            isinstance(getattr(pin, field), str) and getattr(pin, field)
            for field in field_names
        ):
            raise CompositionError("release lock values must be non-empty strings")
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
            raise CompositionError("runtime descriptor fields do not match the v2 contract")
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

        Release manifests are parsed exclusively through the v2 split above.
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
            **{key: raw[key] for key in (
                "source_repository", "source_revision", "artifact_url", "artifact_sha256",
                "distribution", "distribution_version",
            )},
        })
        dependencies = tuple(
            ReleaseLock.from_dict({"lock_id": f"{domain}-dependency-{index}", **item})
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
            raise CompositionError("composition manifest must use the v2 top-level shape")
        if (
            raw["schema_version"] != "vuoro-composition/v2"
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
        if {descriptor.domain for descriptor in descriptors} != _REQUIRED_DOMAINS:
            raise CompositionError("composition must pin exactly work, execution, knowledge, and audit")
        if len(descriptors) != len(_REQUIRED_DOMAINS):
            raise CompositionError("composition contains duplicate runtime domains")
        by_id = {lock.lock_id: lock for lock in locks}
        referenced: list[str] = []
        adapters: list[AdapterPin] = []
        for descriptor in descriptors:
            try:
                release_lock = by_id[descriptor.lock_id]
                dependencies = tuple(by_id[lock_id] for lock_id in descriptor.dependency_lock_ids)
            except KeyError as error:
                raise CompositionError(
                    f"{descriptor.domain}: runtime descriptor references an unknown release lock"
                ) from error
            if any(
                dependency.source_repository != release_lock.source_repository
                for dependency in dependencies
            ):
                raise CompositionError(
                    f"{descriptor.domain}: dependencies must come from the same owner repository"
                )
            referenced.extend((descriptor.lock_id, *descriptor.dependency_lock_ids))
            adapters.append(AdapterPin(descriptor, release_lock, dependencies))
        if len(referenced) != len(set(referenced)):
            raise CompositionError("release locks cannot be shared by runtime descriptors")
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
    for adapter in manifest.adapters:
        for pin in (adapter, *adapter.dependencies):
            filename = pin.artifact_url.rsplit("/", 1)[-1]
            if filename in seen:
                raise CompositionError(f"artifact filename collision: {filename}")
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
    records = {entry.get("distribution"): entry for entry in attestation["distributions"] if isinstance(entry, dict)}
    if set(records) != set(expected):
        raise CompositionError("installed composition attestation distributions do not match release locks")
    for distribution_name, lock in expected.items():
        record = records[distribution_name]
        if (
            record.get("lock_id") != lock.lock_id
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


def _execution_authorizer(provenance: Any, resource: str, verb: str) -> bool:
    if (resource, verb) in {
        ("execution.candidate-action.create", "create"),
        ("execution.group.manage", "create"),
        ("execution.group.manage", "update"),
    }:
        return True
    prefix = "execution.dispatch.repo:"
    if verb not in {"enqueue", "read"} or not resource.startswith(prefix):
        return False
    repo_id = resource[len(prefix):]
    repositories = tuple(provenance.authorized_repositories)
    return bool(repo_id) and ("*" in repositories or repo_id in repositories)


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
        allowed_fields = {"actor", "environment", "authorities", "repo_ids"}
        required_fields = {"actor", "environment", "authorities"}
        if not required_fields <= set(identity) <= allowed_fields:
            raise CompositionError("identity registry contains unsupported identity fields")
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
        identities[token] = Identity(
            actor=actor,
            environment=bound_environment,
            authorities=frozenset(authorities),
            repo_ids=frozenset(repo_ids),
        )
    if not identities:
        raise CompositionError("identity registry must contain at least one identity")
    return StaticBearerIdentityResolver(identities)


def load_development_identities(path: Path, *, environment: str) -> StaticBearerIdentityResolver:
    """Compatibility alias for callers of the former development-only loader."""

    return load_identities(path, environment=environment)


def _runtime_env(name: str, environ: Mapping[str, str]) -> str:
    value = environ.get(name)
    if not value:
        raise CompositionError(f"{name} is required for four-domain composition")
    return value


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


def create_composed_app(
    *,
    manifest_path: Path | None = None,
    wheel_dir: Path | None = None,
    identity_path: Path | None = None,
    project_bindings_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
):
    """Create the only deployable service application: the pinned composition."""

    import os

    environ = environ or os.environ
    environment_name = _runtime_env("VUORO_ENVIRONMENT_NAME", environ)
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
    resolver = load_identities(
        identity_path or Path(_runtime_env("VUORO_IDENTITIES_FILE", environ)),
        environment=environment_name,
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
        make_transient_credential_resolver,
    )

    # VUORO_WORK_REPOSITORY_ID only seeds this template instance; every served
    # invocation re-scopes to the client-supplied, identity-authorized
    # repo_id from the invocation envelope (_dispatch validates it against
    # Identity.authorizes_repo before calling WorkApplication.invoke, which
    # re-scopes via _scoped_for), so this application can serve every
    # repository tenant a bound identity is authorized for -- not only this
    # one.
    work_store = work_pg.get_connection(_runtime_env("VUORO_WORK_RUNTIME_DSN", environ))
    work_store.repo_id = _runtime_env("VUORO_WORK_REPOSITORY_ID", environ)
    work_credential_resolver = make_transient_credential_resolver()
    work_application = WorkApplication.postgres(
        work_store, credential_resolver=work_credential_resolver
    )
    bindings_path = project_bindings_path or Path(
        "/opt/vuoro/composition/project-bindings.json"
    )
    try:
        bindings_raw = json.loads(bindings_path.read_text(encoding="utf-8"))
        project_bindings = load_project_bindings(bindings_raw)
    except (OSError, json.JSONDecodeError, ProjectBindingError) as error:
        raise CompositionError("cannot load immutable project bindings") from error
    if len(project_bindings) != 1:
        raise CompositionError("this release must contain exactly one project binding")
    project_binding = project_bindings[0]
    work_dsn = _runtime_env("VUORO_WORK_RUNTIME_DSN", environ)

    def make_member_application(repo_id: str) -> WorkApplication:
        member_store = work_pg.get_connection(work_dsn)
        member_store.repo_id = repo_id
        return WorkApplication.postgres(
            member_store, credential_resolver=work_credential_resolver
        )

    def make_project_application(
        project_id: str, members: tuple[tuple[str, WorkApplication], ...]
    ) -> ProjectWorkApplication:
        return ProjectWorkApplication(
            project_id,
            tuple(
                ProjectMemberApplication(origin_repo, application)
                for origin_repo, application in members
            ),
            canonical_binding={
                "project_id": project_binding.project_id,
                "home_repo": project_binding.home_repo,
                "backlog_repos": list(project_binding.repo_ids),
                "source_repository": project_binding.source_repository,
                "source_revision": project_binding.source_revision,
                "source_path": project_binding.source_path,
                "source_sha256": project_binding.source_sha256,
            },
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
    work_state = _compatibility("work", work_migrations.compatibility_handshake(work_store), work_pin)

    execution_pin = manifest.pin("execution")
    from actionq.application import ActionQApplication
    from actionq import vuoro as execution_adapter

    execution_application = ActionQApplication(
        schema=_runtime_env("VUORO_EXECUTION_SCHEMA", environ),
        connection_factory=_pg_connection_factory(_runtime_env("VUORO_EXECUTION_RUNTIME_DSN", environ)),
        authorizer=_execution_authorizer,
    )
    _load_function(execution_pin)(registry, application=execution_application)
    execution_state = _compatibility("execution", execution_adapter.compatibility_record(execution_application), execution_pin)

    knowledge_pin = manifest.pin("knowledge")
    from kctl.application import CentralKnowledgeApplication
    from kctl import vuoro as knowledge_adapter

    knowledge_application = CentralKnowledgeApplication(
        schema=_runtime_env("VUORO_KNOWLEDGE_SCHEMA", environ),
        connection_factory=_pg_connection_factory(_runtime_env("VUORO_KNOWLEDGE_RUNTIME_DSN", environ)),
        expected_environment_name=environment_name,
        expected_environment_class=environment_class,
    )
    _load_function(knowledge_pin)(registry, application=knowledge_application)
    knowledge_state = _compatibility("knowledge", knowledge_adapter.compatibility_record(knowledge_application), knowledge_pin)

    audit_pin = manifest.pin("audit")
    from auditctl.vuoro_adapter import VuoroAuditAdapter

    audit_adapter = VuoroAuditAdapter(
        connection_factory=_pg_connection_factory(_runtime_env("VUORO_AUDIT_RUNTIME_DSN", environ)),
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
        "execution": execution_state,
        "knowledge": knowledge_state,
        "audit": audit_state,
    }
    incompatible = [name for name, state in domains.items() if state.state != "compatible"]
    if incompatible:
        raise CompositionError("runtime compatibility failed for: " + ", ".join(incompatible))
    return create_app(
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


__all__ = [
    "AdapterPin",
    "CompositionError",
    "CompositionManifest",
    "ReleaseLock",
    "RuntimeAdapterDescriptor",
    "create_composed_app",
    "load_development_identities",
    "load_identities",
    "verify_adapter_artifacts",
    "verify_installed_composition",
]

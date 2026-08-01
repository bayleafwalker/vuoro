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
from importlib.metadata import PackageNotFoundError, version
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
class ArtifactPin:
    source_repository: str
    source_revision: str
    artifact_url: str
    artifact_sha256: str
    distribution: str
    distribution_version: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ArtifactPin":
        field_names = {field.name for field in fields(cls)}
        if set(raw) != field_names:
            raise CompositionError("dependency pin fields do not match the v1 contract")
        pin = cls(**{field: raw[field] for field in field_names})
        if not all(
            isinstance(getattr(pin, field), str) and getattr(pin, field)
            for field in field_names
        ):
            raise CompositionError("dependency pin values must be non-empty strings")
        _validate_artifact_pin(pin, pin.distribution)
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


def _validate_artifact_pin(pin: Any, label: str) -> None:
    if not _GIT_SHA.fullmatch(pin.source_revision):
        raise CompositionError(f"{label}: source_revision must be a full Git SHA")
    if not _SHA256.fullmatch(pin.artifact_sha256):
        raise CompositionError(f"{label}: artifact_sha256 must be a SHA-256 digest")
    try:
        _release_wheel_identity(pin.source_repository, pin.artifact_url)
    except CompositionError as error:
        raise CompositionError(f"{label}: {error}") from error


@dataclass(frozen=True)
class AdapterPin(ArtifactPin):
    domain: str
    adapter_module: str
    register: str
    migration_entrypoint: str
    api_version: str
    schema_version: str
    dependencies: tuple[ArtifactPin, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AdapterPin":
        required_fields = {
            "domain",
            "source_repository",
            "source_revision",
            "artifact_url",
            "artifact_sha256",
            "distribution",
            "distribution_version",
            "adapter_module",
            "register",
            "migration_entrypoint",
            "api_version",
            "schema_version",
        }
        if not required_fields <= set(raw) <= required_fields | {"dependencies"}:
            raise CompositionError("adapter pin fields do not match the v1 contract")
        dependencies_raw = raw.get("dependencies", [])
        if not isinstance(dependencies_raw, list):
            raise CompositionError("adapter dependencies must be an array")
        values = {field: raw[field] for field in required_fields}
        pin = cls(
            **values,
            dependencies=tuple(
                ArtifactPin.from_dict(item)
                for item in dependencies_raw
                if isinstance(item, dict)
            ),
        )
        if len(pin.dependencies) != len(dependencies_raw):
            raise CompositionError("adapter dependencies must be objects")
        if not all(
            isinstance(getattr(pin, field), str) and getattr(pin, field)
            for field in required_fields
        ):
            raise CompositionError("adapter pin values must be non-empty strings")
        _validate_artifact_pin(pin, pin.domain)
        distributions = [dependency.distribution for dependency in pin.dependencies]
        if len(distributions) != len(set(distributions)):
            raise CompositionError(f"{pin.domain}: duplicate dependency distribution")
        if any(
            dependency.source_repository != pin.source_repository
            or dependency.source_revision != pin.source_revision
            for dependency in pin.dependencies
        ):
            raise CompositionError(
                f"{pin.domain}: dependencies must come from the same owner revision"
            )
        return pin


@dataclass(frozen=True)
class CompositionManifest:
    schema_version: str
    adapters: tuple[AdapterPin, ...]

    @classmethod
    def load(cls, path: Path) -> "CompositionManifest":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CompositionError(f"cannot load composition manifest: {path}") from error
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "adapters"}:
            raise CompositionError("composition manifest must use the v1 top-level shape")
        if raw["schema_version"] != "vuoro-composition/v1" or not isinstance(raw["adapters"], list):
            raise CompositionError("unsupported composition manifest")
        adapters = tuple(AdapterPin.from_dict(item) for item in raw["adapters"] if isinstance(item, dict))
        if len(adapters) != len(raw["adapters"]) or {pin.domain for pin in adapters} != _REQUIRED_DOMAINS:
            raise CompositionError("composition must pin exactly work, execution, knowledge, and audit")
        distributions = [
            pin.distribution
            for adapter in adapters
            for pin in (adapter, *adapter.dependencies)
        ]
        if len(distributions) != len(set(distributions)):
            raise CompositionError("composition contains duplicate distributions")
        return cls(schema_version=raw["schema_version"], adapters=adapters)

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
    manifest = CompositionManifest.load(
        manifest_path or Path(_runtime_env("VUORO_COMPOSITION_MANIFEST", environ))
    )
    verify_adapter_artifacts(manifest, wheel_dir or Path(_runtime_env("VUORO_ADAPTER_WHEEL_DIR", environ)))
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
    )


__all__ = [
    "AdapterPin",
    "CompositionError",
    "CompositionManifest",
    "create_composed_app",
    "load_development_identities",
    "load_identities",
    "verify_adapter_artifacts",
]

"""Pinned four-domain Vuoro service composition.

The service accepts domain adapters only through the checked-in composition
manifest.  Deployment supplies runtime DSNs and the development identity
registry, but cannot add, replace, or remove catalog operations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import importlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import re
from typing import Any, Callable

from vuoro_service.app import ServiceSettings, create_app
from vuoro_service.catalog import CatalogRegistry
from vuoro_service.contracts import DomainCompatibility
from vuoro_service.identity import Identity, StaticBearerIdentityResolver


_REQUIRED_DOMAINS = frozenset({"work", "execution", "knowledge", "audit"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class CompositionError(RuntimeError):
    """The immutable release composition or runtime configuration is invalid."""


@dataclass(frozen=True)
class AdapterPin:
    domain: str
    source_repository: str
    source_revision: str
    artifact_url: str
    artifact_sha256: str
    distribution: str
    distribution_version: str
    adapter_module: str
    register: str
    migration_entrypoint: str
    api_version: str
    schema_version: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AdapterPin":
        fields = {
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
        if set(raw) != fields:
            raise CompositionError("adapter pin fields do not match the v1 contract")
        pin = cls(**{field: raw[field] for field in fields})
        if not all(isinstance(getattr(pin, field), str) and getattr(pin, field) for field in fields):
            raise CompositionError("adapter pin values must be non-empty strings")
        if not _GIT_SHA.fullmatch(pin.source_revision):
            raise CompositionError(f"{pin.domain}: source_revision must be a full Git SHA")
        if not _SHA256.fullmatch(pin.artifact_sha256):
            raise CompositionError(f"{pin.domain}: artifact_sha256 must be a SHA-256 digest")
        if not pin.artifact_url.startswith("https://github.com/"):
            raise CompositionError(f"{pin.domain}: artifact_url must be a GitHub release URL")
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
        return cls(schema_version=raw["schema_version"], adapters=adapters)

    def pin(self, domain: str) -> AdapterPin:
        for pin in self.adapters:
            if pin.domain == domain:
                return pin
        raise CompositionError(f"missing required adapter: {domain}")


def verify_adapter_artifacts(manifest: CompositionManifest, wheel_dir: Path) -> None:
    """Verify bundled release wheels before importing their adapter modules."""

    for pin in manifest.adapters:
        filename = pin.artifact_url.rsplit("/", 1)[-1]
        artifact = wheel_dir / filename
        try:
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        except OSError as error:
            raise CompositionError(f"{pin.domain}: pinned artifact is unavailable") from error
        if digest != pin.artifact_sha256:
            raise CompositionError(f"{pin.domain}: pinned artifact checksum mismatch")


def load_development_identities(path: Path, *, environment: str) -> StaticBearerIdentityResolver:
    """Load opaque development bearer identities from a mounted secret file."""

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
        if set(identity) != {"actor", "environment", "authorities"}:
            raise CompositionError("identity registry contains unsupported identity fields")
        actor = identity["actor"]
        bound_environment = identity["environment"]
        authorities = identity["authorities"]
        if (
            not isinstance(actor, str)
            or not actor
            or bound_environment != environment
            or not isinstance(authorities, list)
            or not all(isinstance(authority, str) and authority for authority in authorities)
        ):
            raise CompositionError("identity registry is not bound to this environment")
        identities[token] = Identity(
            actor=actor,
            environment=bound_environment,
            authorities=frozenset(authorities),
        )
    if not identities:
        raise CompositionError("identity registry must contain at least one identity")
    return StaticBearerIdentityResolver(identities)


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
    environ: Mapping[str, str] | None = None,
):
    """Create the only deployable service application: the pinned composition."""

    import os

    environ = environ or os.environ
    manifest = CompositionManifest.load(
        manifest_path or Path(_runtime_env("VUORO_COMPOSITION_MANIFEST", environ))
    )
    verify_adapter_artifacts(manifest, wheel_dir or Path(_runtime_env("VUORO_ADAPTER_WHEEL_DIR", environ)))
    environment_name = _runtime_env("VUORO_ENVIRONMENT_NAME", environ)
    environment_class = _runtime_env("VUORO_ENVIRONMENT_CLASS", environ)
    if environment_class != "development":
        raise CompositionError("this composition is restricted to a development environment")
    resolver = load_development_identities(
        identity_path or Path(_runtime_env("VUORO_IDENTITIES_FILE", environ)),
        environment=environment_name,
    )
    registry = CatalogRegistry()

    work_pin = manifest.pin("work")
    from sprintctl import pg as work_pg
    from sprintctl import pg_migrations as work_migrations
    from sprintctl.application import WorkApplication

    work_store = work_pg.get_connection(_runtime_env("VUORO_WORK_RUNTIME_DSN", environ))
    work_store.repo_id = _runtime_env("VUORO_WORK_REPOSITORY_ID", environ)
    work_application = WorkApplication.postgres(work_store)
    _load_function(work_pin)(registry, work_application)
    work_state = _compatibility("work", work_migrations.compatibility_handshake(work_store), work_pin)

    execution_pin = manifest.pin("execution")
    from actionq.application import ActionQApplication
    from actionq import vuoro as execution_adapter

    execution_application = ActionQApplication(
        schema=_runtime_env("VUORO_EXECUTION_SCHEMA", environ),
        connection_factory=_pg_connection_factory(_runtime_env("VUORO_EXECUTION_RUNTIME_DSN", environ)),
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
            environment_class="development",
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
    "verify_adapter_artifacts",
]

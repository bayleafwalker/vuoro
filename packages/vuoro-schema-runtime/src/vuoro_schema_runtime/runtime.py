"""Driver-neutral central schema migration and compatibility primitives.

The runtime deliberately uses only structural connection/cursor protocols.
That makes it usable with psycopg, psycopg2, test doubles, or a future driver
without making any of those a dependency of the shared wheel.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict, dataclass
import hashlib
import re
from typing import Any, Iterable, Sequence


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class CentralSchemaError(RuntimeError):
    """Base error for explicit central-schema operations."""


class MigrationRoleError(CentralSchemaError):
    """The migration entrypoint is running under an unexpected role."""


class MigrationDriftError(CentralSchemaError):
    """The migration ledger does not match the immutable migration assets."""


class SchemaCompatibilityError(CentralSchemaError):
    """The selected runtime role cannot safely use the installed schema."""


@dataclass(frozen=True, slots=True)
class Migration:
    """One immutable SQL migration asset."""

    version: int
    name: str
    sql: str
    sha256: str | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("migration version must be positive")
        if not self.name or not self.sql:
            raise ValueError("migration name and sql must be non-empty")
        digest = hashlib.sha256(self.sql.encode("utf-8")).hexdigest()
        if self.sha256 is None:
            object.__setattr__(self, "sha256", digest)
        elif self.sha256 != digest:
            raise ValueError(f"migration {self.version} sha256 does not match sql")


@dataclass(frozen=True, slots=True)
class MigrationResult:
    schema: str
    installed_version: int
    applied_versions: tuple[int, ...]
    migration_role: str
    runtime_role: str


@dataclass(frozen=True, slots=True)
class Compatibility:
    schema: str
    domain_api_version: str
    installed_schema_version: int | None
    minimum_schema_version: int
    maximum_schema_version: int
    current_role: str
    expected_role_kind: str
    configured_role: str | None
    compatible: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


MigrationAsset = Migration
SchemaCompatibility = Compatibility


def identifier(value: str, field: str = "identifier") -> str:
    """Validate and return an unquoted PostgreSQL identifier.

    SQL identifiers are validated at the shared boundary; values interpolated
    into statements are therefore not accepted from arbitrary text.
    """

    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{field} must be an unquoted PostgreSQL identifier")
    return value


def _quoted(value: str, field: str) -> str:
    return '"' + identifier(value, field) + '"'


def load_migrations(assets: Iterable[tuple[str, str] | Migration]) -> tuple[Migration, ...]:
    """Normalize owner-supplied migration assets and enforce contiguity."""

    migrations = tuple(
        asset if isinstance(asset, Migration) else Migration(index, asset[0], asset[1])
        for index, asset in enumerate(assets, start=1)
    )
    versions = [item.version for item in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise CentralSchemaError(f"migration assets must be contiguous: {versions}")
    return migrations


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        return row[key]
    return row[index]


def _current_user(conn: Any) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT current_user")
        row = cur.fetchone()
    return str(_row_value(row, "current_user"))


def _applied(conn: Any, schema: str) -> dict[int, tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT version, name, sha256 FROM {_quoted(schema, 'schema')}.schema_migration ORDER BY version"
        )
        rows = cur.fetchall()
    return {
        int(_row_value(row, "version", 0)): (
            str(_row_value(row, "name", 1)),
            str(_row_value(row, "sha256", 2)),
        )
        for row in rows
    }


class SchemaRuntime:
    """Reusable owner-configured migration and compatibility runtime.

    ``migrations`` are immutable assets supplied by an owner. No migration is
    attempted by ``check_compatibility`` or ``require_runtime_compatibility``.
    """

    def __init__(
        self,
        *,
        domain_api_version: str,
        migrations: Sequence[Migration | tuple[str, str]],
        minimum_schema_version: int | None = None,
        maximum_schema_version: int | None = None,
        migration_lock_namespace: str = "vuoro-schema-runtime",
    ) -> None:
        if not domain_api_version:
            raise ValueError("domain_api_version must be non-empty")
        normalized: list[Migration] = []
        for index, asset in enumerate(migrations, start=1):
            normalized.append(
                asset if isinstance(asset, Migration) else Migration(index, asset[0], asset[1])
            )
        self.migrations = load_migrations(normalized)
        current = len(self.migrations)
        self.minimum_schema_version = minimum_schema_version if minimum_schema_version is not None else current
        self.maximum_schema_version = maximum_schema_version if maximum_schema_version is not None else current
        if not 1 <= self.minimum_schema_version <= self.maximum_schema_version:
            raise ValueError("schema compatibility range is invalid")
        if self.maximum_schema_version > current:
            raise ValueError("maximum_schema_version exceeds migration assets")
        self.domain_api_version = domain_api_version
        self.migration_lock_namespace = identifier(
            migration_lock_namespace.replace("-", "_"), "migration_lock_namespace"
        )

    @property
    def current_schema_version(self) -> int:
        return len(self.migrations)

    def _bootstrap_ledger(self, conn: Any, schema: str) -> None:
        schema_ident = _quoted(schema, "schema")
        with conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_ident}")
            cur.execute(
                f"""CREATE TABLE IF NOT EXISTS {schema_ident}.schema_migration (
                    version integer PRIMARY KEY CHECK (version > 0),
                    name text NOT NULL,
                    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{{64}}$')
                )"""
            )

    def migrate(
        self,
        conn: Any,
        *,
        schema: str,
        migration_role: str,
        runtime_role: str,
        target_version: int | None = None,
    ) -> MigrationResult:
        identifier(schema, "schema")
        identifier(migration_role, "migration_role")
        identifier(runtime_role, "runtime_role")
        if migration_role == runtime_role:
            raise ValueError("migration_role and runtime_role must be different roles")
        target = self.current_schema_version if target_version is None else target_version
        if not 1 <= target <= self.current_schema_version:
            raise ValueError(f"target_version must be between 1 and {self.current_schema_version}")
        transaction = conn.transaction() if hasattr(conn, "transaction") else nullcontext()
        applied_now: list[int] = []
        with transaction:
            if _current_user(conn) != migration_role:
                raise MigrationRoleError(
                    f"migration connection role is {_current_user(conn)!r}, expected {migration_role!r}"
                )
            self._bootstrap_ledger(conn, schema)
            applied = _applied(conn, schema)
            versions = sorted(applied)
            if versions and versions != list(range(1, versions[-1] + 1)):
                raise MigrationDriftError("migration ledger versions are not contiguous")
            if versions and versions[-1] > self.current_schema_version:
                raise MigrationDriftError("installed schema is newer than this package")
            schema_ident = _quoted(schema, "schema")
            for migration in self.migrations:
                recorded = applied.get(migration.version)
                if recorded is not None:
                    if recorded != (migration.name, migration.sha256):
                        raise MigrationDriftError(
                            f"migration {migration.version} checksum or name does not match the ledger"
                        )
                    continue
                if migration.version > target:
                    break
                rendered = migration.sql.replace("__SCHEMA__", schema_ident)
                with conn.cursor() as cur:
                    cur.execute(rendered)
                    cur.execute(
                        f"INSERT INTO {schema_ident}.schema_migration (version, name, sha256) VALUES (%s, %s, %s)",
                        (migration.version, migration.name, migration.sha256),
                    )
                applied_now.append(migration.version)
            installed = max(_applied(conn, schema), default=0)
        return MigrationResult(schema, installed, tuple(applied_now), migration_role, runtime_role)

    def check_compatibility(self, conn: Any, *, schema: str, expected_role_kind: str = "runtime") -> Compatibility:
        identifier(schema, "schema")
        if expected_role_kind not in {"runtime", "migration"}:
            raise ValueError("expected_role_kind must be runtime or migration")
        current_role = _current_user(conn)
        schema_ident = _quoted(schema, "schema")
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (f"{schema}.schema_migration",))
            exists = cur.fetchone()
        if exists is None or _row_value(exists, "to_regclass") is None:
            return Compatibility(schema, self.domain_api_version, None, self.minimum_schema_version, self.maximum_schema_version, current_role, expected_role_kind, None, False, ("schema_not_initialized",))
        with conn.cursor() as cur:
            cur.execute(f"SELECT max(version) AS version FROM {schema_ident}.schema_migration")
            row = cur.fetchone()
        installed = int(_row_value(row, "version") or 0)
        reasons: list[str] = []
        if installed < self.minimum_schema_version:
            reasons.append("schema_too_old")
        if installed > self.maximum_schema_version:
            reasons.append("schema_too_new")
        configured: str | None = None
        if not reasons:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT role_name::text FROM {schema_ident}.schema_principal WHERE role_kind = %s",
                    (expected_role_kind,),
                )
                row = cur.fetchone()
            if row is not None:
                configured = str(_row_value(row, "role_name"))
            if configured is None:
                reasons.append("role_not_configured")
            elif configured != current_role:
                reasons.append("role_kind_mismatch")
        return Compatibility(schema, self.domain_api_version, installed, self.minimum_schema_version, self.maximum_schema_version, current_role, expected_role_kind, configured, not reasons, tuple(reasons))

    def require_runtime_compatibility(self, conn: Any, *, schema: str) -> Compatibility:
        result = self.check_compatibility(conn, schema=schema)
        if not result.compatible:
            raise SchemaCompatibilityError(
                f"central schema is incompatible: {', '.join(result.reasons)}"
            )
        return result


def migrate(runtime: SchemaRuntime, conn: Any, **kwargs: Any) -> MigrationResult:
    return runtime.migrate(conn, **kwargs)


def check_compatibility(runtime: SchemaRuntime, conn: Any, **kwargs: Any) -> Compatibility:
    return runtime.check_compatibility(conn, **kwargs)


def require_runtime_compatibility(runtime: SchemaRuntime, conn: Any, **kwargs: Any) -> Compatibility:
    return runtime.require_runtime_compatibility(conn, **kwargs)

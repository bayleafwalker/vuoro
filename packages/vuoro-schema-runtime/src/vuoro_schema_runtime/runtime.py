"""Pure central-schema contract and migration-asset helpers.

This package deliberately does not know how to connect to a database. Domain
owners retain migration execution, DDL, roles, and driver selection. Vuoro's
shared wheel only makes immutable assets, SQL rendering, and compatibility
reports deterministic and reusable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re
from typing import Any, Iterable, Mapping, Sequence

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class CentralSchemaError(ValueError):
    """Base error for invalid shared schema contracts."""


class MigrationDriftError(CentralSchemaError):
    """A supplied ledger does not match the immutable migration assets."""


class SchemaCompatibilityError(CentralSchemaError):
    """A compatibility report is not safe for runtime use."""


def sha256_text(value: str) -> str:
    """Return the lowercase SHA-256 digest of UTF-8 text."""
    if not isinstance(value, str):
        raise TypeError("sha256_text expects str")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def identifier(value: str, field: str = "identifier") -> str:
    """Validate and return an unquoted PostgreSQL identifier."""
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{field} must be an unquoted PostgreSQL identifier")
    return value


def quote_identifier(value: str, field: str = "identifier") -> str:
    """Return a safely quoted identifier after strict validation."""
    return f'"{identifier(value, field)}"'


@dataclass(frozen=True, slots=True)
class MigrationAsset:
    """One immutable SQL migration asset and its content digest."""

    version: int
    name: str
    sql: str
    sha256: str

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("migration version must be positive")
        if not self.name or not self.sql:
            raise ValueError("migration name and sql must be non-empty")
        if self.sha256 != sha256_text(self.sql):
            raise MigrationDriftError(
                f"migration {self.version} sha256 does not match its SQL"
            )


def migration_asset(version: int, name: str, sql: str) -> MigrationAsset:
    """Create a content-addressed migration asset."""
    return MigrationAsset(version, name, sql, sha256_text(sql))


def validate_contiguous_migrations(
    assets: Iterable[MigrationAsset],
) -> tuple[MigrationAsset, ...]:
    """Validate unique, one-based, contiguous migration versions."""
    result = tuple(assets)
    if any(not isinstance(asset, MigrationAsset) for asset in result):
        raise TypeError("migration assets must be MigrationAsset values")
    versions = [asset.version for asset in result]
    expected = list(range(1, len(result) + 1))
    if versions != expected:
        raise MigrationDriftError(
            f"migration assets must be contiguous through {len(result)}: {versions}"
        )
    return result


def render_schema_sql(sql: str, schema: str) -> str:
    """Render ``__SCHEMA__`` with a safely quoted identifier."""
    if not isinstance(sql, str):
        raise TypeError("sql must be str")
    return sql.replace("__SCHEMA__", quote_identifier(schema, "schema"))


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Pure result data owners may emit after executing their own migrations."""

    schema: str
    installed_version: int
    applied_versions: tuple[int, ...] = ()
    migration_role: str | None = None
    runtime_role: str | None = None


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    """Pure, serializable result of checking a supplied migration ledger."""

    schema: str
    domain_api_version: str
    installed_schema_version: int | None
    minimum_schema_version: int
    maximum_schema_version: int
    current_role: str | None
    expected_role_kind: str
    configured_role: str | None
    compatible: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


def _ledger_rows(
    ledger: Mapping[int, tuple[str, str] | Mapping[str, str]]
    | Iterable[tuple[int, str, str] | Mapping[str, Any]],
) -> dict[int, tuple[str, str]]:
    if isinstance(ledger, Mapping):
        rows = ledger.items()
    else:
        rows = (
            ((row.get("version"), row) if isinstance(row, Mapping) else (row[0], row))
            for row in ledger
        )
    result: dict[int, tuple[str, str]] = {}
    for version, row in rows:
        if isinstance(row, Mapping):
            name, digest = row.get("name"), row.get("sha256")
        else:
            if len(row) == 3:
                _, name, digest = row
            elif len(row) == 2:
                name, digest = row
            else:
                raise TypeError("ledger rows must contain version, name, and sha256")
        if not isinstance(version, int) or not isinstance(name, str) or not isinstance(digest, str):
            raise TypeError("ledger versions, names, and digests must be typed values")
        if version in result:
            raise MigrationDriftError(f"duplicate ledger version: {version}")
        result[version] = (name, digest)
    return result


def compatibility_report(
    migrations: Sequence[MigrationAsset],
    ledger: Mapping[int, tuple[str, str] | Mapping[str, str]]
    | Iterable[tuple[int, str, str] | Mapping[str, Any]]
    | None,
    *,
    schema: str,
    domain_api_version: str,
    minimum_schema_version: int,
    maximum_schema_version: int,
    current_role: str | None = None,
    expected_role_kind: str = "runtime",
    configured_role: str | None = None,
) -> CompatibilityReport:
    """Compare assets with a complete supplied ledger, without DB access."""
    assets = validate_contiguous_migrations(migrations)
    identifier(schema, "schema")
    if not domain_api_version:
        raise ValueError("domain_api_version must be non-empty")
    if expected_role_kind not in {"runtime", "migration"}:
        raise ValueError("expected_role_kind must be runtime or migration")
    if not 1 <= minimum_schema_version <= maximum_schema_version:
        raise ValueError("schema compatibility range is invalid")
    if maximum_schema_version > len(assets):
        raise ValueError("maximum_schema_version exceeds migration assets")
    reasons: list[str] = []
    rows = {} if ledger is None else _ledger_rows(ledger)
    installed: int | None = max(rows) if rows else None
    if not rows:
        reasons.append("schema_not_initialized")
    else:
        expected_versions = set(range(1, installed + 1))
        if set(rows) != expected_versions:
            reasons.append("migration_ledger_not_contiguous")
        by_version = {asset.version: asset for asset in assets}
        for version, (name, digest) in rows.items():
            asset = by_version.get(version)
            if asset is None or asset.name != name or asset.sha256 != digest:
                reasons.append("migration_ledger_drift")
                break
    if installed is not None:
        if installed < minimum_schema_version:
            reasons.append("schema_too_old")
        if installed > maximum_schema_version:
            reasons.append("schema_too_new")
    if not reasons:
        if configured_role is None:
            reasons.append("role_not_configured")
        elif current_role != configured_role:
            reasons.append("role_kind_mismatch")
    return CompatibilityReport(
        schema=schema,
        domain_api_version=domain_api_version,
        installed_schema_version=installed,
        minimum_schema_version=minimum_schema_version,
        maximum_schema_version=maximum_schema_version,
        current_role=current_role,
        expected_role_kind=expected_role_kind,
        configured_role=configured_role,
        compatible=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
    )


check_compatibility = compatibility_report

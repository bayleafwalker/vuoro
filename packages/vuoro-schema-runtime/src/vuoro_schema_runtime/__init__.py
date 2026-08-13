"""Pure central-schema contract and migration-asset helpers."""

from .runtime import (
    CentralSchemaError,
    CompatibilityReport,
    MigrationAsset,
    MigrationDriftError,
    MigrationResult,
    SchemaCompatibilityError,
    check_compatibility,
    compatibility_report,
    identifier,
    migration_asset,
    quote_identifier,
    render_schema_sql,
    sha256_text,
    validate_contiguous_migrations,
)

__all__ = [
    "CentralSchemaError",
    "CompatibilityReport",
    "MigrationAsset",
    "MigrationDriftError",
    "MigrationResult",
    "SchemaCompatibilityError",
    "check_compatibility",
    "compatibility_report",
    "identifier",
    "migration_asset",
    "quote_identifier",
    "render_schema_sql",
    "sha256_text",
    "validate_contiguous_migrations",
]

__version__ = "0.1.0"

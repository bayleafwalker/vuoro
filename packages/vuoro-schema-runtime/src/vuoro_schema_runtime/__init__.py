"""Shared central-schema runtime primitives.

The package intentionally contains no database-driver dependency.  Owners
provide migration assets and a DB-API-compatible connection to
:class:`SchemaRuntime`.
"""

from .runtime import (
    Compatibility,
    MigrationAsset,
    SchemaCompatibility,
    CentralSchemaError,
    Migration,
    MigrationDriftError,
    MigrationResult,
    MigrationRoleError,
    SchemaCompatibilityError,
    SchemaRuntime,
    check_compatibility,
    identifier,
    load_migrations,
    migrate,
    require_runtime_compatibility,
)

__all__ = [
    "CentralSchemaError",
    "Migration",
    "MigrationDriftError",
    "MigrationResult",
    "MigrationRoleError",
    "SchemaCompatibilityError",
    "SchemaRuntime",
    "Compatibility",
    "MigrationAsset",
    "SchemaCompatibility",
    "check_compatibility",
    "identifier",
    "load_migrations",
    "migrate",
    "require_runtime_compatibility",
]

__version__ = "0.1.0"

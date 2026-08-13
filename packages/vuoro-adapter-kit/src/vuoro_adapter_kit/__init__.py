"""Small, stdlib-only building blocks for Vuoro protocol adapters."""

from .catalog import (
    AdapterOperation,
    CatalogOperation,
    CatalogRegistry,
    Definition,
    OperationDefinition,
    SCHEMA_DIALECT,
    SCHEMA_FEATURES,
    definition,
    object_schema,
    operation,
    operation_definition,
    build_object_schema,
    build_operation_definition,
    register,
    register_catalog,
    register_operations,
)

__all__ = [
    "AdapterOperation",
    "CatalogOperation",
    "CatalogRegistry",
    "Definition",
    "OperationDefinition",
    "SCHEMA_DIALECT",
    "SCHEMA_FEATURES",
    "definition",
    "object_schema",
    "operation",
    "operation_definition",
    "build_object_schema",
    "build_operation_definition",
    "register",
    "register_catalog",
    "register_operations",
]

__version__ = "0.1.0"

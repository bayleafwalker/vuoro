"""Pure, stdlib-only JSON-Schema and operation-spec builders."""

from .catalog import (
    CatalogRegistry,
    SCHEMA_DIALECT,
    SCHEMA_FEATURES,
    build_object_schema,
    build_operation_spec,
    object_schema,
    operation_spec,
)

__all__ = [
    "CatalogRegistry",
    "SCHEMA_DIALECT",
    "SCHEMA_FEATURES",
    "build_object_schema",
    "build_operation_spec",
    "object_schema",
    "operation_spec",
]

__version__ = "0.1.0"

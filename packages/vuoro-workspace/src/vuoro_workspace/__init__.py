"""Knowledge workspace store (docs/plans/2026-10-14-knowledge-workspace-phase0.md §3).

Public store names are loaded lazily so CLI help remains available without the
optional database driver installed.
"""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vuoro-workspace")
except PackageNotFoundError:
    __version__ = "0.1.1"

_EXPORT_MODULES = {
    "WorkspaceError": "errors",
    "new_ulid": "ids",
    "CURRENT_SCHEMA_VERSION": "migrations",
    "DEFAULT_SCHEMA": "migrations",
    "compatibility": "migrations",
    "migrate_schema": "migrations",
    "require_compatible_schema": "migrations",
    "startup_schema_handshake": "migrations",
    "FsObjectStore": "objectstore",
    "ObjectInfo": "objectstore",
    "ObjectStore": "objectstore",
    "S3ObjectStore": "objectstore",
    "sniff_media_type": "objectstore",
    "ADMIN": "store",
    "CONTRIBUTE": "store",
    "READ": "store",
    "REVIEW": "store",
    "ROLES": "store",
    "Principal": "store",
    "WorkspaceStore": "store",
}

__all__ = [
    "ADMIN",
    "CONTRIBUTE",
    "CURRENT_SCHEMA_VERSION",
    "DEFAULT_SCHEMA",
    "FsObjectStore",
    "ObjectInfo",
    "ObjectStore",
    "Principal",
    "READ",
    "REVIEW",
    "ROLES",
    "S3ObjectStore",
    "WorkspaceError",
    "WorkspaceStore",
    "compatibility",
    "migrate_schema",
    "new_ulid",
    "require_compatible_schema",
    "sniff_media_type",
    "startup_schema_handshake",
    "__version__",
]


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))

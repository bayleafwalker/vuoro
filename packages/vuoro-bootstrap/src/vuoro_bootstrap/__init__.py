"""Vuoro Cloud bootstrap client (filesystem and package boundary)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vuoro-bootstrap")
except PackageNotFoundError:
    __version__ = "0.1.0"

from vuoro_bootstrap.api import BootstrapApi, BootstrapError
from vuoro_bootstrap.files import (
    BootstrapFilePlan,
    BootstrapFilesError,
    plan_files,
    read_private_file,
    write_private_file,
)

__all__ = [
    "BootstrapApi",
    "BootstrapError",
    "BootstrapFilePlan",
    "BootstrapFilesError",
    "plan_files",
    "read_private_file",
    "write_private_file",
    "__version__",
]

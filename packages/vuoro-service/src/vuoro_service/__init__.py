"""Reusable Vuoro service process package."""

from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("vuoro-service")
except PackageNotFoundError:
    # Source-only tooling may import the package without an installed wheel.
    __version__ = "0.1.0"

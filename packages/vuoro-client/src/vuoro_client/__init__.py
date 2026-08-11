"""Transport-only Vuoro client package."""

from importlib.metadata import PackageNotFoundError, version

from vuoro_client.client import AsyncVuoroClient
from vuoro_client.discovery import (
    BootstrapManifest,
    BootstrapPackageVersions,
    BootstrapServiceCompatibility,
    ClientProtocolRange,
    DiscoveryContractError,
    DiscoveryDocument,
    parse_bootstrap_manifest,
    parse_discovery,
)
from vuoro_client.profile import Profile, ProfileError, load_profile
from vuoro_client.errors import ClientIncompatibleError, InvocationRejectedError

try:
    __version__ = version("vuoro-client")
except PackageNotFoundError:
    # Source-only tooling may import the package without an installed wheel.
    __version__ = "0.1.0"

__all__ = [
    "AsyncVuoroClient",
    "ClientIncompatibleError",
    "BootstrapManifest",
    "BootstrapPackageVersions",
    "BootstrapServiceCompatibility",
    "ClientProtocolRange",
    "DiscoveryContractError",
    "DiscoveryDocument",
    "Profile",
    "ProfileError",
    "InvocationRejectedError",
    "load_profile",
    "parse_bootstrap_manifest",
    "parse_discovery",
    "__version__",
]

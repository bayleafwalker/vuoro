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
from vuoro_client.sovite_execution import (
    CompleteRequest,
    ExecutionIdentity,
    ExecutionMutation,
    LookupRequest,
    PullRequest,
    PullResponse,
    StartResponse,
    RenewResponse,
    StatusResponse,
    HandbackResponse,
    CompleteResponse,
    LookupResponse,
    SoviteExecutionClient,
    SoviteExecutionError,
    SoviteExecutionUncertain,
)

try:
    __version__ = version("vuoro-client")
except PackageNotFoundError:
    # Source-only tooling may import the package without an installed wheel.
    __version__ = "0.1.2"

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
    "CompleteRequest",
    "ExecutionIdentity",
    "ExecutionMutation",
    "LookupRequest",
    "PullRequest",
    "PullResponse",
    "StartResponse",
    "RenewResponse",
    "StatusResponse",
    "HandbackResponse",
    "CompleteResponse",
    "LookupResponse",
    "SoviteExecutionClient",
    "SoviteExecutionError",
    "SoviteExecutionUncertain",
    "load_profile",
    "parse_bootstrap_manifest",
    "parse_discovery",
    "__version__",
]

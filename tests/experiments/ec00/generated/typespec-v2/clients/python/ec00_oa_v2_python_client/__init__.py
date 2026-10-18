"""A client library for accessing EC-00 disposable OA candidate v2"""

from .client import AuthenticatedClient, Client

__all__ = (
    "AuthenticatedClient",
    "Client",
)

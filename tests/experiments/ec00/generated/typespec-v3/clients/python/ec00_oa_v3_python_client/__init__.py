"""A client library for accessing EC-00 schema-validated OA candidate v3"""

from .client import AuthenticatedClient, Client

__all__ = (
    "AuthenticatedClient",
    "Client",
)

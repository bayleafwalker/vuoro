"""Registered ingress edges. Profile-specific decoding lives here and only here."""
from .registry import ingest, register, registered_profiles
from . import hostproto as _hostproto  # noqa: F401  (self-registering)

__all__ = ["ingest", "register", "registered_profiles"]

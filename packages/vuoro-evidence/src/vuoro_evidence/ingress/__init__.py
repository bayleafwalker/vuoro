"""Registered ingress edges. Profile-specific decoding lives here and only here."""
from .registry import ingest, register, registered_profiles
from . import hostproto as _hostproto, command_capture as _command_capture  # noqa: F401  (self-registering)

__all__ = ["ingest", "register", "registered_profiles"]

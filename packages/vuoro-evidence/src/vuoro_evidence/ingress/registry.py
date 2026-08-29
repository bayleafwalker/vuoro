from __future__ import annotations

from typing import Any, Callable, Mapping

from ..core.model import EvidenceItem

Decoder = Callable[..., EvidenceItem]
_REGISTRY: dict[str, Decoder] = {}


def register(profile: str, decoder: Decoder) -> None:
    if profile in _REGISTRY:
        raise ValueError(f"profile already registered: {profile}")
    _REGISTRY[profile] = decoder


def registered_profiles() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def ingest(profile: str, payload: Mapping[str, Any], **context: Any) -> EvidenceItem:
    try:
        decoder = _REGISTRY[profile]
    except KeyError:
        raise KeyError(f"no ingress registered for profile {profile!r}") from None
    return decoder(payload, **context)

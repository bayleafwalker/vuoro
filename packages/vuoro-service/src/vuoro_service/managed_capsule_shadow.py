"""Shadow comparison for the managed capsule pilot.

This module prepares receipts only. It never invokes a provider, mutates a
queue, or grants runtime authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any


class ManagedCanaryDenied(ValueError):
    pass


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


@dataclass(frozen=True)
class ManagedPilotConfig:
    managed_enabled: bool = False

    def rollback(self) -> "ManagedPilotConfig":
        return ManagedPilotConfig(managed_enabled=False)


@dataclass(frozen=True)
class ShadowReceipt:
    schema_version: str
    mode: str
    execution_changed: bool
    current_prompt_digest: str
    managed_prompt_digest: str
    prompts_equal: bool
    current_prompt_bytes: int
    managed_prompt_bytes: int
    capsule_digest: str
    rendered_prompt_digest: str
    role_preset_digest: str
    renderer_version: str
    selection_reasons: tuple[str, ...]

    def canonical_bytes(self) -> bytes:
        value = asdict(self)
        value["selection_reasons"] = list(self.selection_reasons)
        return _canonical(value)


def compare_shadow(*, current_prompt: str, managed_prompt: str, capsule: dict[str, Any]) -> ShadowReceipt:
    managed_bytes = managed_prompt.encode()
    current_bytes = current_prompt.encode()
    if capsule.get("contract_id") != "managed-dispatch-capsule/v1":
        raise ValueError("unsupported managed capsule")
    if capsule.get("rendered_prompt_digest") != _sha(managed_bytes):
        raise ValueError("managed prompt digest mismatch")
    reasons = tuple(
        item["selection_reason"]
        for field in ("instruction_sources", "dependency_context", "artifacts")
        for item in capsule.get(field, [])
        if isinstance(item, dict) and isinstance(item.get("selection_reason"), str)
    )
    return ShadowReceipt(
        schema_version="managed-shadow-receipt/v1",
        mode="shadow",
        execution_changed=False,
        current_prompt_digest=_sha(current_bytes),
        managed_prompt_digest=_sha(managed_bytes),
        prompts_equal=current_bytes == managed_bytes,
        current_prompt_bytes=len(current_bytes),
        managed_prompt_bytes=len(managed_bytes),
        capsule_digest=str(capsule.get("capsule_digest", "")),
        rendered_prompt_digest=str(capsule["rendered_prompt_digest"]),
        role_preset_digest=str(capsule.get("role_preset_digest", "")),
        renderer_version=str(capsule.get("renderer_version", "")),
        selection_reasons=reasons,
    )


def prepare_canary(
    *,
    config: ManagedPilotConfig,
    authorized: bool,
    shadow_receipt: ShadowReceipt,
) -> dict[str, Any]:
    if not config.managed_enabled:
        raise ManagedCanaryDenied("managed path is disabled")
    if not authorized:
        raise ManagedCanaryDenied("managed canary requires explicit authorization")
    if shadow_receipt.mode != "shadow" or shadow_receipt.execution_changed:
        raise ManagedCanaryDenied("clean shadow evidence is required")
    return {
        "schema_version": "managed-canary-preparation/v1",
        "authorized": True,
        "execution_started": False,
        "capsule_digest": shadow_receipt.capsule_digest,
        "rendered_prompt_digest": shadow_receipt.rendered_prompt_digest,
        "shadow_receipt_digest": _sha(shadow_receipt.canonical_bytes()),
    }

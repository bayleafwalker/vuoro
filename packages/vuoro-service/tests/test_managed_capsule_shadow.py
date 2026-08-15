from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from vuoro_service.managed_capsule_shadow import (
    ManagedCanaryDenied,
    ManagedPilotConfig,
    compare_shadow,
    prepare_canary,
)


AGENTOPS = Path(__file__).parents[4] / "agentops"
SCRIPT = AGENTOPS / "templates/dispatch/scripts/render_managed_capsule.py"
SPEC = importlib.util.spec_from_file_location("managed_renderer", SCRIPT)
assert SPEC and SPEC.loader
RENDERER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENDERER)
SOURCE = AGENTOPS / "templates/dispatch/managed-capsule/source.fixture.json"


def rendered():
    return RENDERER.render(json.loads(SOURCE.read_text(encoding="utf-8")))


def test_shadow_is_deterministic_and_never_changes_execution() -> None:
    capsule, prompt = rendered()
    first = compare_shadow(current_prompt="ordinary prompt\n", managed_prompt=prompt.decode(), capsule=capsule)
    second = compare_shadow(current_prompt="ordinary prompt\n", managed_prompt=prompt.decode(), capsule=capsule)
    assert first == second
    assert first.mode == "shadow"
    assert first.execution_changed is False
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.selection_reasons


def test_prompt_mismatch_is_rejected() -> None:
    capsule, prompt = rendered()
    with pytest.raises(ValueError, match="prompt digest"):
        compare_shadow(current_prompt="ordinary", managed_prompt=prompt.decode() + "changed", capsule=capsule)


def test_canary_requires_enabled_flag_and_explicit_authorization() -> None:
    capsule, prompt = rendered()
    receipt = compare_shadow(current_prompt="ordinary", managed_prompt=prompt.decode(), capsule=capsule)
    with pytest.raises(ManagedCanaryDenied, match="disabled"):
        prepare_canary(config=ManagedPilotConfig(), authorized=True, shadow_receipt=receipt)
    with pytest.raises(ManagedCanaryDenied, match="explicit authorization"):
        prepare_canary(config=ManagedPilotConfig(managed_enabled=True), authorized=False, shadow_receipt=receipt)
    prepared = prepare_canary(config=ManagedPilotConfig(managed_enabled=True), authorized=True, shadow_receipt=receipt)
    assert prepared["authorized"] is True
    assert prepared["execution_started"] is False


def test_rollback_disables_managed_path_and_keeps_shadow_available() -> None:
    config = ManagedPilotConfig(managed_enabled=True).rollback()
    assert config.managed_enabled is False
    capsule, prompt = rendered()
    assert compare_shadow(current_prompt="ordinary", managed_prompt=prompt.decode(), capsule=capsule).mode == "shadow"

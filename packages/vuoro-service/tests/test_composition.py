from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from vuoro_service.composition import (
    CompositionError,
    CompositionManifest,
    create_composed_app,
    load_identities,
    verify_adapter_artifacts,
)


ROOT = Path(__file__).parents[1]


def test_checked_in_manifest_pins_all_four_domains() -> None:
    manifest = CompositionManifest.load(ROOT / "composition" / "adapter-pins.json")
    assert {pin.domain for pin in manifest.adapters} == {"work", "execution", "knowledge", "audit"}
    assert all(len(pin.source_revision) == 40 for pin in manifest.adapters)
    assert all(len(pin.artifact_sha256) == 64 for pin in manifest.adapters)


def test_artifact_verification_fails_closed_on_mismatch(tmp_path: Path) -> None:
    source = ROOT / "composition" / "adapter-pins.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    artifact = tmp_path / "sprintctl-0.2.0-py3-none-any.whl"
    artifact.write_bytes(b"immutable-work-adapter")
    raw["adapters"] = [raw["adapters"][0]]
    raw["adapters"][0]["domain"] = "work"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CompositionError, match="exactly"):
        CompositionManifest.load(path)
    raw["adapters"] = json.loads(source.read_text(encoding="utf-8"))["adapters"]
    raw["adapters"][0]["artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    path.write_text(json.dumps(raw), encoding="utf-8")
    manifest = CompositionManifest.load(path)
    with pytest.raises(CompositionError, match="unavailable"):
        verify_adapter_artifacts(manifest, tmp_path)


def test_identity_registry_is_environment_bound_and_never_accepts_short_tokens(tmp_path: Path) -> None:
    path = tmp_path / "identities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "vuoro-identities/v1",
                "identities": {
                    "x" * 32: {
                        "actor": "test:developer",
                        "environment": "vuoro-dev",
                        "authorities": ["work.read"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    assert load_identities(path, environment="vuoro-dev")
    with pytest.raises(CompositionError, match="not bound"):
        load_identities(path, environment="production")


def test_identity_registry_supports_a_production_environment_binding(tmp_path: Path) -> None:
    path = tmp_path / "identities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "vuoro-identities/v1",
                "identities": {
                    "y" * 32: {
                        "actor": "test:operator",
                        "environment": "vuoro-shared",
                        "authorities": ["work.read"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    assert load_identities(path, environment="vuoro-shared")


def test_composition_allows_production_but_rejects_non_deployable_classes() -> None:
    production = {
        "VUORO_ENVIRONMENT_NAME": "vuoro-shared",
        "VUORO_ENVIRONMENT_CLASS": "production",
    }
    with pytest.raises(CompositionError, match="VUORO_COMPOSITION_MANIFEST"):
        create_composed_app(environ=production)
    with pytest.raises(CompositionError, match="deployable environment class"):
        create_composed_app(
            environ={
                "VUORO_ENVIRONMENT_NAME": "vuoro-recovery",
                "VUORO_ENVIRONMENT_CLASS": "recovery",
            }
        )

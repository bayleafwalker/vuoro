from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from vuoro_service import composition
from vuoro_service.composition import AdapterPin, CompositionError


ROOT = Path(__file__).parents[3]


def _dependency() -> dict[str, str]:
    return {
        "source_repository": "https://github.com/example/owner",
        "source_revision": "a" * 40,
        "artifact_url": "https://github.com/example/owner/releases/download/v1.0.0/companion.whl",
        "artifact_sha256": "b" * 64,
        "distribution": "companion",
        "distribution_version": "1.0.0",
    }


def _adapter() -> dict:
    return {"domain": "execution", **_dependency(), "distribution": "owner",
        "artifact_url": "https://github.com/example/owner/releases/download/v1.0.0/owner.whl",
        "adapter_module": "owner.vuoro", "register": "register", "migration_entrypoint": "owner migrate",
        "api_version": "execution/v1", "schema_version": "owner-schema/v1"}


def _release_lock(lock_id: str, raw: dict) -> dict:
    fields = {
        "source_repository", "source_revision", "artifact_url", "artifact_sha256",
        "distribution", "distribution_version",
    }
    return {"lock_id": lock_id, **{field: raw[field] for field in fields}}


def _v2_manifest(*locks: dict) -> dict:
    return {
        "schema_version": "vuoro-composition/v2",
        "release_locks": list(locks),
        "runtime_descriptors": [],
    }


def test_adapter_dependencies_are_optional_strict_and_unique() -> None:
    assert AdapterPin.from_dict(_adapter()).dependencies == ()
    raw = _adapter() | {"dependencies": [_dependency()]}
    assert AdapterPin.from_dict(raw).dependencies[0].distribution == "companion"
    with pytest.raises(CompositionError, match="fields"):
        AdapterPin.from_dict(_adapter() | {"dependencies": [_dependency() | {"extra": True}]})
    with pytest.raises(CompositionError, match="duplicate"):
        AdapterPin.from_dict(_adapter() | {"dependencies": [_dependency(), _dependency()]})
    assert AdapterPin.from_dict(
        _adapter()
        | {"dependencies": [_dependency() | {"source_revision": "c" * 40}]}
    ).dependencies[0].source_revision == "c" * 40
    with pytest.raises(CompositionError, match="same owner repository"):
        AdapterPin.from_dict(
            _adapter()
            | {"dependencies": [_dependency() | {
                "source_repository": "https://github.com/example/other",
                "artifact_url": "https://github.com/example/other/releases/download/v1.0.0/companion.whl",
            }]}
        )


def test_fetcher_rejects_dependency_filename_collisions() -> None:
    spec = importlib.util.spec_from_file_location("fetch_pins", ROOT / "scripts" / "fetch_pinned_adapters.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dependency = _dependency() | {"artifact_url": _adapter()["artifact_url"], "distribution": "companion"}
    with pytest.raises(SystemExit, match="filename collision"):
        module.artifact_pins(_v2_manifest(
            _release_lock("owner", _adapter()), _release_lock("companion", dependency)
        ))


@pytest.mark.parametrize(
    "artifact_url",
    [
        "https://github.com/example/owner/releases/download/v1/../../other/x.whl",
        "https://github.com/example/owner/releases/download/v1/x.whl?download=1",
        "https://github.com/example/owner/releases/download//x.whl",
        "https://github.com/example/owner/releases/download/v1/",
        "https://github.com/example/owner/releases/download/v1/x%2fescape.whl",
        "https://user@github.com/example/owner/releases/download/v1/x.whl",
        "https://github.com:443/example/owner/releases/download/v1/x.whl",
    ],
)
def test_runtime_and_fetcher_reject_ambiguous_release_urls(artifact_url: str) -> None:
    raw = _adapter() | {"artifact_url": artifact_url}
    with pytest.raises(CompositionError, match="canonical|release wheel"):
        AdapterPin.from_dict(raw)
    spec = importlib.util.spec_from_file_location(
        "fetch_pins", ROOT / "scripts" / "fetch_pinned_adapters.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(SystemExit, match="canonical|release wheel"):
        module.artifact_pins(_v2_manifest(_release_lock("owner", raw)))


def test_adapter_load_fails_before_import_on_companion_version_mismatch(monkeypatch) -> None:
    pin = AdapterPin.from_dict(_adapter() | {"dependencies": [_dependency()]})
    monkeypatch.setattr(composition, "version", lambda distribution: {
        "owner": "1.0.0", "companion": "0.9.0"}[distribution])
    with pytest.raises(CompositionError, match="dependency version"):
        composition._load_function(pin)

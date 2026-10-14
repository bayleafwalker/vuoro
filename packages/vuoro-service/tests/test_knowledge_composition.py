"""The knowledge domain (vuoro-workspace) is a required composition domain.

The checked-in manifest pins the released vuoro-workspace 0.1.0 owner. These
tests prove that the runtime gate, the fetcher and the attester all require
exactly work, audit and knowledge and refuse anything else. The served knowledge operations themselves are
exercised end to end in packages/vuoro-workspace/tests/test_vuoro_adapter_served.py.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from vuoro_service.catalog import CatalogRegistry
from vuoro_service.composition import (
    AdapterPin,
    CompositionError,
    CompositionManifest,
    _compose_knowledge,
)

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT.parents[1] / "scripts"
KNOWLEDGE_DESCRIPTOR = {
    "domain": "knowledge",
    "lock_id": "knowledge-adapter",
    "dependency_lock_ids": ["vuoro-adapter-kit", "vuoro-schema-runtime"],
    "adapter_module": "vuoro_workspace.vuoro_adapter",
    "register": "register_knowledge_catalog",
    "api_version": "vuoro-workspace/v1",
    "schema_version": "knowledge-schema/v1",
}


def _three_domain() -> dict:
    return json.loads((ROOT / "composition" / "adapter-pins.json").read_text(encoding="utf-8"))


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load(tmp_path: Path, raw: dict) -> CompositionManifest:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return CompositionManifest.load(path)


def test_checked_in_descriptor_is_the_owner_registration() -> None:
    raw = _three_domain()
    assert [d for d in raw["runtime_descriptors"] if d["domain"] == "knowledge"] == [KNOWLEDGE_DESCRIPTOR]


def test_runtime_fetcher_and_attester_accept_the_three_domain_manifest(tmp_path: Path) -> None:
    raw = _three_domain()
    manifest = _load(tmp_path, raw)
    assert {pin.domain for pin in manifest.adapters} == {"work", "audit", "knowledge"}
    pin = manifest.pin("knowledge")
    assert pin.distribution == "vuoro-workspace"
    assert {dep.distribution for dep in pin.dependencies} == {"vuoro-adapter-kit", "vuoro-schema-runtime"}
    fetched = {pin["artifact_url"].rsplit("/", 1)[-1] for _, pin in _script("fetch_pinned_adapters").artifact_pins(raw)}
    assert "vuoro_workspace-0.1.0-py3-none-any.whl" in fetched
    assert {entry["distribution"] for entry in _script("attest_installed_composition")._pinned(raw)} >= {
        "vuoro-workspace"
    }


def _drop(domain: str):
    def mutate(raw: dict) -> None:
        descriptor = [d for d in raw["runtime_descriptors"] if d["domain"] == domain][0]
        raw["runtime_descriptors"].remove(descriptor)
        raw["release_locks"] = [lock for lock in raw["release_locks"] if lock["lock_id"] != descriptor["lock_id"]]
    return mutate


@pytest.mark.parametrize("mutation, runtime_message, script_message", [
    (lambda raw: raw["runtime_descriptors"].append({**KNOWLEDGE_DESCRIPTOR}), "duplicate runtime domains",
     "duplicate|exactly three|exactly work, audit and knowledge"),
    (lambda raw: [d for d in raw["runtime_descriptors"] if d["domain"] == "knowledge"][0].update(domain="search"),
     "exactly work, audit and knowledge", "exactly work, audit and knowledge"),
    # Knowledge is required: a manifest without it is refused everywhere.
    (_drop("knowledge"), "exactly work, audit and knowledge",
     "exactly three|exactly work, audit and knowledge"),
    (_drop("audit"), "exactly work, audit and knowledge", "exactly three|exactly work, audit and knowledge"),
])
def test_domain_admission_is_closed_everywhere(tmp_path: Path, mutation, runtime_message, script_message) -> None:
    raw = _three_domain()
    mutation(raw)
    with pytest.raises(CompositionError, match=runtime_message):
        _load(tmp_path, raw)
    with pytest.raises(SystemExit, match=script_message):
        _script("fetch_pinned_adapters").artifact_pins(raw)
    with pytest.raises(SystemExit, match=script_message):
        _script("attest_installed_composition")._pinned(raw)


# ------------------------------------------------------------ registration

def _pin(tmp_path: Path, **descriptor: str) -> AdapterPin:
    pytest.importorskip("vuoro_workspace", reason="needs the vuoro-workspace workspace package")
    raw = _three_domain()
    [d for d in raw["runtime_descriptors"] if d["domain"] == "knowledge"][0].update(descriptor)
    return _load(tmp_path, raw).pin("knowledge")


def _environ(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        # An unreachable local socket: the read-only gate reports, never retries or migrates.
        "VUORO_KNOWLEDGE_RUNTIME_DSN": f"host={tmp_path / 'nosock'} dbname=none connect_timeout=1",
        "VUORO_KNOWLEDGE_OBJECT_STORE": f"fs:{tmp_path / 'objects'}",
        "VUORO_KNOWLEDGE_RUNTIME_ROLE": "vuoro_knowledge_runtime",
        **extra,
    }


def test_compose_knowledge_registers_the_owner_operations_and_gates_the_schema(tmp_path: Path) -> None:
    registry = CatalogRegistry()
    state = _compose_knowledge(_pin(tmp_path), registry, _environ(tmp_path))
    names = [op.name for op in registry.catalog().operations]
    assert len(names) == 18 and all(name.startswith("knowledge.") for name in names)
    assert state.state == "incompatible"
    assert state.api_version == "vuoro-workspace/v1"
    assert "unavailable" in (state.reason or "")


@pytest.mark.parametrize("missing, message", [
    ("VUORO_KNOWLEDGE_RUNTIME_DSN", "VUORO_KNOWLEDGE_RUNTIME_DSN"),
    ("VUORO_KNOWLEDGE_OBJECT_STORE", "VUORO_KNOWLEDGE_OBJECT_STORE"),
    # Never defaulted to the connecting role: an owner DSN must not pass the role check.
    ("VUORO_KNOWLEDGE_RUNTIME_ROLE", "VUORO_KNOWLEDGE_RUNTIME_ROLE"),
])
def test_compose_knowledge_requires_its_runtime_configuration(tmp_path: Path, missing: str, message: str) -> None:
    environ = _environ(tmp_path)
    del environ[missing]
    with pytest.raises(CompositionError, match=message):
        _compose_knowledge(_pin(tmp_path), CatalogRegistry(), environ)


def test_compose_knowledge_refuses_a_relative_object_store(tmp_path: Path) -> None:
    with pytest.raises(CompositionError, match="absolute"):
        _compose_knowledge(_pin(tmp_path), CatalogRegistry(),
                           _environ(tmp_path, VUORO_KNOWLEDGE_OBJECT_STORE="fs:relative/objects"))


def test_compose_knowledge_refuses_a_foreign_registration(tmp_path: Path) -> None:
    with pytest.raises(CompositionError, match="owner adapter registration"):
        _compose_knowledge(_pin(tmp_path, register="register_everything"), CatalogRegistry(), _environ(tmp_path))


def test_compose_knowledge_refuses_a_version_other_than_the_pin(tmp_path: Path) -> None:
    raw = _three_domain()
    lock = [lock for lock in raw["release_locks"] if lock["lock_id"] == "knowledge-adapter"][0]
    lock["distribution_version"] = "9.9.9"
    lock["artifact_url"] = lock["artifact_url"].replace("0.1.0", "9.9.9")
    pin = _load(tmp_path, raw).pin("knowledge")
    with pytest.raises(CompositionError, match="installed distribution version"):
        _compose_knowledge(pin, CatalogRegistry(), _environ(tmp_path))


def _record(**overrides):
    return {
        "schema": "knowledge", "domain_api_version": "vuoro-workspace/v1", "installed_schema_version": 1,
        "minimum_schema_version": 1, "maximum_schema_version": 1, "current_role": "vuoro_knowledge_runtime",
        "expected_role_kind": "runtime", "configured_role": "vuoro_knowledge_runtime", "compatible": True,
        "reasons": [], "schema_version": "knowledge-schema/v1", **overrides,
    }


def test_compose_knowledge_starts_compatible_when_the_owner_gate_passes(tmp_path: Path, monkeypatch) -> None:
    from vuoro_workspace.vuoro_adapter import KnowledgeApplication

    monkeypatch.setattr(KnowledgeApplication, "compatibility", lambda self: _record())
    state = _compose_knowledge(_pin(tmp_path), CatalogRegistry(), _environ(tmp_path))
    assert state.state == "compatible" and state.reason is None
    assert (state.api_version, state.schema_version) == ("vuoro-workspace/v1", "knowledge-schema/v1")


def test_compose_knowledge_refuses_a_store_api_other_than_the_pin(tmp_path: Path, monkeypatch) -> None:
    from vuoro_workspace.vuoro_adapter import KnowledgeApplication

    monkeypatch.setattr(KnowledgeApplication, "compatibility",
                        lambda self: _record(domain_api_version="vuoro-workspace/v2"))
    state = _compose_knowledge(_pin(tmp_path), CatalogRegistry(), _environ(tmp_path))
    assert state.state == "incompatible"
    assert "API version" in (state.reason or "")


def test_compose_knowledge_reports_an_owner_dsn_as_incompatible(tmp_path: Path, monkeypatch) -> None:
    from vuoro_workspace.vuoro_adapter import KnowledgeApplication

    monkeypatch.setattr(KnowledgeApplication, "compatibility", lambda self: _record(
        compatible=False, reasons=["runtime_role_owns_schema"], reason="runtime_role_owns_schema"))
    state = _compose_knowledge(_pin(tmp_path), CatalogRegistry(), _environ(tmp_path))
    assert state.state == "incompatible" and "owns_schema" in (state.reason or "")

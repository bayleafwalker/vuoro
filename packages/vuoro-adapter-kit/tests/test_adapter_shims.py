"""The four shims satisfy the uniform construction protocol.

Owner wheels are not installed in this workspace, so the owner modules are
injected as fakes. That bounds what these tests prove, and the bound is the
point: they prove the half that is Vuoro's -- each shim exposes ``build`` and
``register`` with the arity the composer calls, reads only settings the profile
pinned, and delegates registration without interpreting it. Whether the owner
constructor accepts what is passed is release-environment evidence and lives in
``scripts/validate_released_*_adapter.py``, which runs where the pinned wheels
are installed.

The audit shim is the one worth reading. Auditctl registers through an instance
method rather than a module-level function, and v3 handled that with a
hardcoded string comparison in service code that bypassed the shared loader.
Here the difference is absorbed inside the shim and the composer never learns
of it.
"""

from __future__ import annotations

from dataclasses import dataclass
import sys
from types import ModuleType, SimpleNamespace

import pytest

from vuoro_adapter_kit.adapters import audit, execution, knowledge, work


@dataclass(frozen=True)
class FakeRuntime:
    """The shape ``RuntimeConfiguration`` presents to an adapter.

    Duplicated rather than imported: the adapter kit is a shared distribution
    that must not depend on the service package, and an adapter that could only
    be built by importing ``vuoro_service`` would put the coupling back that
    uniform construction removes.
    """

    settings: dict
    environment_name: str = "devbox"
    environment_class: str = "development"

    def require(self, name: str) -> str:
        return self.settings[name]


def _module(name: str, **attributes) -> ModuleType:
    module = ModuleType(name)
    for attribute, value in attributes.items():
        setattr(module, attribute, value)
    sys.modules[name] = module
    return module


@pytest.fixture(autouse=True)
def _clean_owner_modules():
    injected = [
        "actionq", "actionq.application", "actionq.vuoro",
        "sprintctl", "sprintctl.pg", "sprintctl.application", "sprintctl.vuoro_adapter",
        "kctl", "kctl.application", "kctl.vuoro",
        "auditctl", "auditctl.vuoro_adapter",
        "psycopg",
    ]
    yield
    for name in injected:
        sys.modules.pop(name, None)


def test_every_shim_exposes_exactly_build_and_register() -> None:
    """No third entrypoint, and no state: the record type has nowhere to put one."""
    for module in (work, execution, knowledge, audit):
        public = {name for name in vars(module) if not name.startswith("_")}
        assert public <= {"build", "register", "annotations", "Any"}
        assert callable(module.build) and callable(module.register)


def test_execution_builds_from_the_settings_the_profile_pinned() -> None:
    captured = {}

    class ActionQApplication:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    _module("actionq")
    _module("actionq.application", ActionQApplication=ActionQApplication)
    application = execution.build(FakeRuntime({"schema": "actionq", "dsn": "postgres:///x"}))
    assert isinstance(application, ActionQApplication)
    assert captured["schema"] == "actionq"
    assert callable(captured["connection_factory"])
    assert callable(captured["authorizer"])


def test_execution_registers_through_the_owner_function() -> None:
    seen = {}
    _module("actionq")
    _module(
        "actionq.vuoro",
        register_operations=lambda registry, application: seen.update(
            registry=registry, application=application
        ),
    )
    registry, application = object(), object()
    execution.register(registry, application)
    assert seen == {"registry": registry, "application": application}


def test_work_seeds_the_repository_id_it_was_given() -> None:
    store = SimpleNamespace(repo_id=None)

    class WorkApplication:
        @staticmethod
        def postgres(given):
            return SimpleNamespace(store=given)

    _module("sprintctl")
    _module("sprintctl.pg", get_connection=lambda dsn: store)
    _module("sprintctl.application", WorkApplication=WorkApplication)
    application = work.build(FakeRuntime({"dsn": "postgres:///w", "repository_id": "vuoro"}))
    assert application.store is store
    assert store.repo_id == "vuoro"


def test_knowledge_passes_the_environment_the_composer_resolved() -> None:
    captured = {}

    class CentralKnowledgeApplication:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    _module("kctl")
    _module("kctl.application", CentralKnowledgeApplication=CentralKnowledgeApplication)
    knowledge.build(FakeRuntime({"dsn": "postgres:///k", "schema": "kctl"}))
    assert captured["expected_environment_name"] == "devbox"
    assert captured["expected_environment_class"] == "development"


def test_audit_absorbs_the_instance_method_registration() -> None:
    """The exception v3 hardcoded in service code, now invisible to the composer."""
    registered = []

    class VuoroAuditAdapter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def register(self, registry):
            registered.append(registry)

    _module("auditctl")
    _module("auditctl.vuoro_adapter", VuoroAuditAdapter=VuoroAuditAdapter)
    application = audit.build(FakeRuntime({"dsn": "postgres:///a", "schema": "auditctl"}))
    registry = object()
    audit.register(registry, application)
    assert registered == [registry]


def test_a_missing_pin_fails_as_the_composer_asks_rather_than_a_key_error() -> None:
    """``require`` exists so an unpinned setting names the capability, not a dict key."""
    _module("actionq")
    _module("actionq.application", ActionQApplication=lambda **kwargs: None)
    with pytest.raises(KeyError):
        execution.build(FakeRuntime({"dsn": "postgres:///x"}))

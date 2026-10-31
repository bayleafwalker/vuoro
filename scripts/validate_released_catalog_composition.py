"""Verify the complete three-domain (work, audit, knowledge) catalog without opening a database."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

from auditctl.vuoro_adapter import VuoroAuditAdapter
from sprintctl.vuoro_adapter import register_work_catalog
from vuoro_service.catalog import CatalogRegistry
from vuoro_workspace import FsObjectStore
from vuoro_workspace.vuoro_adapter import KnowledgeApplication, register_knowledge_catalog


EXPECTED_TOTAL = 148
EXPECTED_REVISION = "18be3136ba82e49600fb21e09a96ac9cb2b43f56de2a75be85fda88e678a1df2"
EXPECTED_DOMAIN_COUNTS = {"work": 124, "audit": 5, "knowledge": 19}


class WorkStub:
    store = SimpleNamespace(remote_schema_version=30)

    @staticmethod
    def maintenance_resource_schema_available() -> bool:
        return True


def assert_legacy_descriptors(catalog: dict) -> None:
    """Refuse drift in any published service101 descriptor outside the named delta."""
    baseline_path = Path(__file__).resolve().parents[1] / "packages/vuoro-service/tests/fixtures/service101-catalog-baseline.json"
    baseline = json.loads(baseline_path.read_text())["operation_sha256"]
    actual = {operation["name"]: copy.deepcopy(operation) for operation in catalog["operations"]}
    assert set(actual) - set(baseline) == {"work.request.continuation-receipt-v1", "knowledge.workspace.identity-v1"}
    assert set(baseline) <= set(actual)
    begin = actual["knowledge.artifact.begin-v1"]["input_schema"]
    assert "expected_principal_id" not in begin["required"]
    assert begin["properties"].pop("expected_principal_id") == {"type": "string", "minLength": 1, "maxLength": 1000}
    for name, expected in baseline.items():
        encoded = json.dumps(actual[name], sort_keys=True, separators=(",", ":")).encode()
        assert hashlib.sha256(encoded).hexdigest() == expected, name


def main() -> int:
    registry = CatalogRegistry()
    register_work_catalog(registry, WorkStub())
    VuoroAuditAdapter(connection_factory=lambda: None).register(registry)
    with tempfile.TemporaryDirectory() as root:
        # Registration opens nothing: the factory is never called.
        register_knowledge_catalog(
            registry,
            KnowledgeApplication(lambda: None, FsObjectStore(root), runtime_role="vuoro_knowledge_runtime"),
        )
    catalog = registry.catalog().model_dump(mode="json")
    counts: dict[str, int] = {}
    for operation in catalog["operations"]:
        counts[operation["owning_domain"]] = counts.get(operation["owning_domain"], 0) + 1
    assert counts == EXPECTED_DOMAIN_COUNTS, counts
    assert len(catalog["operations"]) == EXPECTED_TOTAL
    assert registry.revision == EXPECTED_REVISION, registry.revision
    assert_legacy_descriptors(catalog)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

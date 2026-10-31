"""Verify the complete three-domain (work, audit, knowledge) catalog without opening a database."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

from auditctl.vuoro_adapter import VuoroAuditAdapter
from sprintctl.vuoro_adapter import register_work_catalog
from vuoro_service.catalog import CatalogRegistry
from vuoro_workspace import FsObjectStore
from vuoro_workspace.vuoro_adapter import KnowledgeApplication, register_knowledge_catalog


EXPECTED_TOTAL = 150
EXPECTED_REVISION = "d9c1e0a8b20a5c26ab6b846506024384da6fcc345939a0f789b77c0b3d8c5ecb"
EXPECTED_DOMAIN_COUNTS = {"work": 126, "audit": 5, "knowledge": 19}


class WorkStub:
    store = SimpleNamespace(remote_schema_version=31)

    @staticmethod
    def maintenance_resource_schema_available() -> bool:
        return True


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
    import importlib.util
    path = Path(__file__).resolve().parent / "service103_catalog_guard.py"
    spec = importlib.util.spec_from_file_location("service103_catalog_guard", path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    baseline=json.loads(guard.BASELINE.read_text());review=json.loads(guard.REVIEW.read_text())
    guard.validate(catalog, baseline, review)
    assert guard.falsify_actual_catalog(catalog, baseline, review)==9
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

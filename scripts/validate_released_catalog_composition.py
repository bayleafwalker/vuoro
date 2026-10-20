"""Verify the complete three-domain (work, audit, knowledge) catalog without opening a database."""

from __future__ import annotations

import tempfile

from auditctl.vuoro_adapter import VuoroAuditAdapter
from sprintctl.vuoro_adapter import register_work_catalog
from vuoro_service.catalog import CatalogRegistry
from vuoro_workspace import FsObjectStore
from vuoro_workspace.vuoro_adapter import KnowledgeApplication, register_knowledge_catalog


EXPECTED_TOTAL = 129
EXPECTED_REVISION = "127fed8e9a23acb01fcd4a64fa4a3a1380701e8985e6d95e79aea8cb21abdb7d"
EXPECTED_DOMAIN_COUNTS = {"work": 106, "audit": 5, "knowledge": 18}


class WorkStub:
    @staticmethod
    def maintenance_resource_schema_available() -> bool:
        return False


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

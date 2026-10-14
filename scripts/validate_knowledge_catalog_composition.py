"""Verify the three-domain (work, audit, knowledge) catalog without opening a database.

Work and audit come from the pinned released wheels; knowledge comes from the
vuoro-workspace wheel built from this checkout until its first release is
pinned (docs/architecture/knowledge-workspace-composition.md). The two-domain
gate (validate_released_catalog_composition.py) stays the served catalog
until then.
"""

from __future__ import annotations

from auditctl.vuoro_adapter import VuoroAuditAdapter
from sprintctl.vuoro_adapter import register_work_catalog
from vuoro_service.catalog import CatalogRegistry
from vuoro_workspace import FsObjectStore
from vuoro_workspace.vuoro_adapter import KnowledgeApplication, register_knowledge_catalog


EXPECTED_TOTAL = 120
EXPECTED_REVISION = "7353fe7fb6130dc34f586484eb94a1180b18a6f9f5c7be6a83aadc9c327e615e"
EXPECTED_DOMAIN_COUNTS = {"work": 97, "audit": 5, "knowledge": 18}


class WorkStub:
    @staticmethod
    def maintenance_resource_schema_available() -> bool:
        return False


def main() -> int:
    import tempfile

    registry = CatalogRegistry()
    register_work_catalog(registry, WorkStub())
    VuoroAuditAdapter(connection_factory=lambda: None).register(registry)
    with tempfile.TemporaryDirectory() as root:
        register_knowledge_catalog(registry, KnowledgeApplication(lambda: None, FsObjectStore(root)))
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

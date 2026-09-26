"""Verify the complete two-domain (work, audit) catalog without opening a database."""

from __future__ import annotations

from auditctl.vuoro_adapter import VuoroAuditAdapter
from sprintctl.vuoro_adapter import register_work_catalog
from vuoro_service.catalog import CatalogRegistry


EXPECTED_TOTAL = 63
EXPECTED_REVISION = "83ebbd43d028c3a1e19a28fce3173fed9f3f3ba53196cf2a8115e02db2683a28"
EXPECTED_DOMAIN_COUNTS = {"work": 58, "audit": 5}


class WorkStub:
    @staticmethod
    def maintenance_resource_schema_available() -> bool:
        return False


def main() -> int:
    registry = CatalogRegistry()
    register_work_catalog(registry, WorkStub())
    VuoroAuditAdapter(connection_factory=lambda: None).register(registry)
    catalog = registry.catalog().model_dump(mode="json")
    assert len(catalog["operations"]) == EXPECTED_TOTAL
    assert registry.revision == EXPECTED_REVISION
    counts: dict[str, int] = {}
    for operation in catalog["operations"]:
        counts[operation["owning_domain"]] = counts.get(operation["owning_domain"], 0) + 1
    assert counts == EXPECTED_DOMAIN_COUNTS
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

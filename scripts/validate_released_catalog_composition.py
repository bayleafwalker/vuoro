"""Verify the complete two-domain (work, audit) catalog without opening a database."""

from __future__ import annotations

from auditctl.vuoro_adapter import VuoroAuditAdapter
from sprintctl.vuoro_adapter import register_work_catalog
from vuoro_service.catalog import CatalogRegistry


EXPECTED_TOTAL = 85
EXPECTED_REVISION = "4230516fcb775d72e14dfa774c8395a9f50ad5b640ccd9134648a8c659f22af3"
EXPECTED_DOMAIN_COUNTS = {"work": 80, "audit": 5}


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

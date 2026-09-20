"""Verify the complete two-domain (work, audit) catalog without opening a database."""

from __future__ import annotations

from auditctl.vuoro_adapter import VuoroAuditAdapter
from sprintctl.vuoro_adapter import register_work_catalog
from vuoro_service.catalog import CatalogRegistry


EXPECTED_TOTAL = 55
EXPECTED_REVISION = "775a16aa23aff295e28420356d3692ea46e3ac606afb2f437e053f411a3a434d"
EXPECTED_DOMAIN_COUNTS = {"work": 50, "audit": 5}


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

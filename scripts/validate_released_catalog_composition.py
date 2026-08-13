"""Verify the complete four-domain catalog without opening a database."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from actionq.vuoro import catalog_metadata, register_operations as register_execution
from auditctl.vuoro_adapter import VuoroAuditAdapter
from kctl.vuoro import register_operations as register_knowledge
from sprintctl.vuoro_adapter import register_work_catalog
from vuoro_service.catalog import CatalogRegistry


EXPECTED_TOTAL = 84
EXPECTED_REVISION = "fc308e37ff1d56eccd9bd1f5372bf782e017936acf44994b22ddba4863e9f196"
EXPECTED_DOMAIN_COUNTS = {"work": 43, "execution": 26, "knowledge": 10, "audit": 5}
EXPECTED_EXECUTION_HASH = "8d434e8b347e804c90e48a6598304be84b12f2a61ebc2dbed00a26053239a778"


class WorkStub:
    @staticmethod
    def maintenance_resource_schema_available() -> bool:
        return False


def main() -> int:
    registry = CatalogRegistry()
    register_work_catalog(registry, WorkStub())
    register_execution(registry, application=object())
    register_knowledge(registry, application=object())
    VuoroAuditAdapter(connection_factory=lambda: None).register(registry)
    catalog = registry.catalog().model_dump(mode="json")
    assert len(catalog["operations"]) == EXPECTED_TOTAL
    assert registry.revision == EXPECTED_REVISION
    counts: dict[str, int] = {}
    for operation in catalog["operations"]:
        counts[operation["owning_domain"]] = counts.get(operation["owning_domain"], 0) + 1
    assert counts == EXPECTED_DOMAIN_COUNTS
    execution = catalog_metadata()
    assert len(execution) == 26
    assert hashlib.sha256(json.dumps(execution, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == EXPECTED_EXECUTION_HASH
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

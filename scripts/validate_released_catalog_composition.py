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


EXPECTED_TOTAL = 87
EXPECTED_REVISION = "9a7621e0ab5b3765be162b6ae0fcdbf06c90bc66572873ef02d7ff5f3f14d4fd"
EXPECTED_DOMAIN_COUNTS = {"work": 46, "execution": 26, "knowledge": 10, "audit": 5}
EXPECTED_EXECUTION_HASH = "8d434e8b347e804c90e48a6598304be84b12f2a61ebc2dbed00a26053239a778"


class WorkStub:
    @staticmethod
    def maintenance_resource_schema_available() -> bool:
        return False


class ExecutionStub:
    managed_dispatch_policy = None


def main() -> int:
    registry = CatalogRegistry()
    register_work_catalog(registry, WorkStub())
    register_execution(registry, application=ExecutionStub())
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

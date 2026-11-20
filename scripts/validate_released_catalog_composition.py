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


EXPECTED_TOTAL = 157
EXPECTED_REVISION = "da5028e71607f97c28d763642df04afbc05a702f4e3c4d934cb82ceafa833886"
EXPECTED_DOMAIN_COUNTS = {"work": 133, "audit": 5, "knowledge": 19}

REVIEWED_ADDITIONS = {
    "work.item.create-v1": "a0498424a8d4380269b1d07b78a204f09aaf443a7b6aced84e2c7b710dc79954",
    "work.outcome.accept-v1": "604b3ddbff7954381a12ab875d8b1cf953c94e204b047f749a826c1d5d14a84c",
    "work.outcome.review-queue-v1": "70e992e558af39736fe9db20e4e9ac29b53a40f26494cdc0b73e814260831dcf",
    "work.outcome.review-v1": "a44310177d0af82bbba9e4982c288b7722404e9cb4363efba91bbe5aa3e5fc70",
    "work.archive.export-v1": "0539f1e8083c40fb6f95a6e73a287a12215be3d916df7bf450e674857212ca07",
    "work.archive.restore-v1": "e6ac75da4498724f7962ed3ee547f272f227b83dfbc0e384e2238b5936c82606",
    "work.archive.restored-source-v1": "e63dc5b5601da0babfa59c4c93813733262fb28cc0e4470065e02ed19a461efe",
}


class WorkStub:
    # Registration-only synthetic fixture. The real PostgreSQL migration and
    # compatibility handshake are exercised by the schema35 conformance gate.
    store = SimpleNamespace(remote_schema_version=35)

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
    additions = {
        entry["name"]: guard.digest(entry)
        for entry in catalog["operations"] if entry["name"] in REVIEWED_ADDITIONS
    }
    assert additions == REVIEWED_ADDITIONS, additions
    legacy_catalog = {
        **catalog,
        "operations": [entry for entry in catalog["operations"] if entry["name"] not in REVIEWED_ADDITIONS],
    }
    baseline = json.loads(guard.BASELINE.read_text())
    review = json.loads(guard.REVIEW.read_text())
    guard.validate(legacy_catalog, baseline, review)
    assert guard.falsify_actual_catalog(legacy_catalog, baseline, review) == 9
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

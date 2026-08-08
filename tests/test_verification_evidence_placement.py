"""Guard the boundary between generic verification results and specialist proof."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPECIALIST_PROOFS = {
    "vuoro-pre-migration-startup-item-2092.json": (
        "50fc99b626a9bb44af4f1367906de7532496c16a8d0c01a17268795966295e03"
    ),
    "vuoro-published-image-startup-v0.1.34-prehardening.json": (
        "b30a8cb22dd29cdb2eb3688f01c38a7769aaf40cd16ec3844d3dfe2b45899e53"
    ),
    "vuoro-published-image-startup-v0.1.35-candidate.json": (
        "b438fbcd6a5cf96a115d57ae028a48d7c2300232a0b4c68e389de874971a8152"
    ),
}


def test_specialist_pre_migration_proofs_are_unchanged_and_indexed() -> None:
    specialist_dir = ROOT / "verification/specialized/pre-migration-startup"
    index = (ROOT / "docs/verification/pre-migration-startup-proofs.md").read_text()

    for filename, expected_sha256 in SPECIALIST_PROOFS.items():
        proof = specialist_dir / filename
        assert proof.is_file()
        assert hashlib.sha256(proof.read_bytes()).hexdigest() == expected_sha256
        assert proof.name in index
        assert expected_sha256 in index
        assert json.loads(proof.read_text())["schema_version"] == (
            "vuoro-pre-migration-startup-proof/v1"
        )


def test_generic_results_contain_only_shared_verification_records() -> None:
    for result in (ROOT / "verification/results").glob("*.json"):
        assert json.loads(result.read_text())["schema_version"] == "verification-result/v1"

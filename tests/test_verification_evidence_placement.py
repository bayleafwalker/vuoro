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


def test_every_validator_script_is_invoked_by_ci() -> None:
    """A validator nobody runs proves nothing, and looks like it proves something.

    The v4 reference-profile validator carried the strongest claim in the
    composition work -- the real 85 operations and the pinned catalog revision --
    and for one commit it carried it nowhere: no CI step, no test, one comment
    referencing it. Reviewers caught that; this makes the next one mechanical.

    Scoped to ``scripts/validate_*.py`` deliberately. The other scripts here are
    operator tools (fetch, attest, migrate, pre-migration startup) that run
    against a deployment or are covered by their own ``--check`` test; a script
    whose name says it validates something and which nothing invokes is the
    specific shape worth failing on.
    """
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    uninvoked = sorted(
        path.name
        for path in (root / "scripts").glob("validate_*.py")
        if path.name not in workflow
    )
    assert not uninvoked, f"validator scripts no CI step runs: {uninvoked}"

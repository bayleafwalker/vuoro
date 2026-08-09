#!/usr/bin/env python3
"""Dependency-free integrity checks for Vuoro's second-owner proof."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "verification/results/sprintctl-maintenance-second-owner.json"
FREEZE = ROOT / "verification/plans/2029-sprintctl-maintenance-owner-goldens.json"
BASE = "d152799c95cf2990cd291c370cafd06e59aea6d8"
IMPLEMENTATION = "87eb7b9d5347e0c7ddec6ba77a94c43e9b45d24e"
SELF_REFERENTIAL_EVIDENCE = {
    "verification/results/sprintctl-maintenance-second-owner.json",
    "verification/validate_2029_second_owner_implementation.py",
}


def candidate_digest(paths: list[str]) -> str:
    value = hashlib.sha256()
    for relative in paths:
        if relative in SELF_REFERENTIAL_EVIDENCE:
            continue
        value.update(relative.encode() + b"\0" + (ROOT / relative).read_bytes() + b"\0")
    return value.hexdigest()


def main() -> None:
    result = json.loads(RESULT.read_text())
    freeze = json.loads(FREEZE.read_text())
    evidence = result["evidence"]
    assert result["implementation_sha"] == IMPLEMENTATION
    assert freeze["resource_kind"] == "work.maintenance-capability"
    assert freeze["identity"]["reference_pattern"] == "^smr1_[A-Za-z0-9_-]{43}$"
    assert freeze["vuoro_non_disclosure"]["status"] == 404
    assert freeze["vuoro_non_disclosure"]["handler_invoked"] is False
    assert evidence["candidate_digest"] == candidate_digest(evidence["candidate_paths"])
    assert evidence["owner_revision"] == "159647d"
    assert evidence["owner_release"] == {
        "version": "0.2.22",
        "revision": "159647d80c91fb4d0f7ae2090c7dec413ec91a8f",
        "artifact_status": "published",
        "wheel_sha256": "bd508ff25f0a586cbcd5fee9a369188361b6f5c54f7a37160ba46e84756a72d8",
    }
    changed = subprocess.run(
        ["git", "diff", "--name-only", BASE, IMPLEMENTATION],
        cwd=ROOT, check=True, text=True, capture_output=True,
    ).stdout.splitlines()
    assert evidence["candidate_paths"] == changed
    assert evidence["test_result"] == "202 passed; specialized 1 passed; both wheels built"
    print("sprintctl maintenance second-owner evidence: valid")


if __name__ == "__main__":
    main()

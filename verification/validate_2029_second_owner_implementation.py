#!/usr/bin/env python3
"""Dependency-free integrity checks for Vuoro's second-owner proof."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "verification/results/sprintctl-maintenance-second-owner.json"
FREEZE = ROOT / "verification/plans/2029-sprintctl-maintenance-owner-goldens.json"


def candidate_digest(paths: list[str]) -> str:
    value = hashlib.sha256()
    for relative in paths:
        value.update(relative.encode() + b"\0" + (ROOT / relative).read_bytes() + b"\0")
    return value.hexdigest()


def main() -> None:
    result = json.loads(RESULT.read_text())
    freeze = json.loads(FREEZE.read_text())
    evidence = result["evidence"]
    assert result["implementation_sha"] == "6503d84c7909109ef127efc5213c907c46157abd"
    assert freeze["resource_kind"] == "work.maintenance-capability"
    assert freeze["identity"]["reference_pattern"] == "^smr1_[A-Za-z0-9_-]{43}$"
    assert freeze["vuoro_non_disclosure"]["status"] == 404
    assert freeze["vuoro_non_disclosure"]["handler_invoked"] is False
    assert evidence["candidate_digest"] == candidate_digest(evidence["candidate_paths"])
    assert evidence["owner_revision"] == "159647d"
    assert evidence["owner_release"] == {
        "version": "0.2.22",
        "revision": "159647d80c91fb4d0f7ae2090c7dec413ec91a8f",
        "artifact_status": "merged-source-no-published-wheel",
    }
    assert evidence["test_result"] == "201 passed"
    print("sprintctl maintenance second-owner evidence: valid")


if __name__ == "__main__":
    main()

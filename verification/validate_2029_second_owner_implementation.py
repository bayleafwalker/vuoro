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
    assert result["implementation_sha"] == "81f0740292a2f14ff9fbccae582d7da702f2cd52"
    assert freeze["resource_kind"] == "work.maintenance-capability"
    assert freeze["identity"]["reference_pattern"] == "^smr1_[A-Za-z0-9_-]{43}$"
    assert freeze["vuoro_non_disclosure"]["status"] == 404
    assert freeze["vuoro_non_disclosure"]["handler_invoked"] is False
    assert evidence["candidate_digest"] == candidate_digest(evidence["candidate_paths"])
    assert evidence["owner_revision"] == "159647d"
    assert evidence["test_result"] == "199 passed"
    print("sprintctl maintenance second-owner evidence: valid")


if __name__ == "__main__":
    main()

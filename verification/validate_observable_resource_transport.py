#!/usr/bin/env python3
"""Dependency-free integrity checks for the observable-resource proof packet."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIONQ = ROOT / "verification/external/actionq-action-resource-owner-v1"
RESULT = ROOT / "verification/results/observable-resource-transport.json"
CANDIDATES = (
    "docs/architecture/observable-resources.md",
    "packages/vuoro-client/src/vuoro_client/client.py",
    "packages/vuoro-client/src/vuoro_client/resources.py",
    "packages/vuoro-client/tests/test_resource_transport.py",
    "packages/vuoro-service/src/vuoro_service/catalog.py",
    "packages/vuoro-service/tests/test_catalog.py",
    "verification/contexts/observable-resource-transport.json",
)
FIXTURES = {
    "manifest.json": "fefb86dabfdd7db6f390c1c4f79864606e8878d0752c95f9eb899c541fa83e3d",
    "protocol-responses.json": "4e23f09c8296572671931004e451ce4649c8339865db3e7950b59d96b9e6eb6b",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate_digest() -> str:
    value = hashlib.sha256()
    for relative in CANDIDATES:
        value.update(relative.encode() + b"\0" + (ROOT / relative).read_bytes() + b"\0")
    return value.hexdigest()


def main() -> None:
    result = json.loads(RESULT.read_text())
    source = json.loads((ACTIONQ / "source.json").read_text())
    assert source["source_repository"] == "actionq"
    assert source["source_revision"] == "9e53ce1"
    assert result["evidence"]["candidate_paths"] == list(CANDIDATES)
    assert result["evidence"]["candidate_digest"] == candidate_digest()
    assert result["evidence"]["owner_fixtures"] == FIXTURES
    assert result["evidence"]["test_command"]
    assert result["evidence"]["test_result"] == "passed"
    for name, expected in FIXTURES.items():
        assert digest(ACTIONQ / name) == expected
        assert source["source_paths"][name]["sha256"] == expected
    owner_manifest = json.loads((ACTIONQ / "manifest.json").read_text())
    assert owner_manifest["registered_files"]["protocol-responses.json"]["sha256"] == FIXTURES["protocol-responses.json"]
    print("observable-resource transport evidence: valid")


if __name__ == "__main__":
    main()

"""Python side of the shared Python/TypeScript JSON wire corpus."""
import base64
import json
from pathlib import Path

import pytest
from vuoro_contracts import ContractError, parse_json

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads((ROOT / "tools/contracts/fixtures/wire-vectors.json").read_text())


@pytest.mark.parametrize("vector", VECTORS["valid"], ids=lambda vector: vector["name"])
def test_python_matches_shared_valid_wire_vectors(vector):
    raw = vector["raw"].encode("utf-8")
    assert parse_json(raw, max_bytes=len(raw)) == vector["value"]


@pytest.mark.parametrize("vector", VECTORS["invalid"], ids=lambda vector: vector["name"])
def test_python_matches_shared_invalid_wire_vectors(vector):
    raw = (base64.b64decode(vector["base64"]) if "base64" in vector
           else vector["raw"].encode("utf-8"))
    with pytest.raises(ContractError):
        parse_json(raw, max_bytes=max(1, len(raw)))


@pytest.mark.parametrize("value", [9_007_199_254_740_992, 1e30])
def test_python_value_boundary_rejects_rounded_integer_values(value):
    raw = json.dumps(value).encode("ascii")
    with pytest.raises(ContractError):
        parse_json(raw, max_bytes=len(raw))

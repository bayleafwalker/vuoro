"""Compile the owner continuation projection with the authenticated TypeSpec tools."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shutil

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
import pytest

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "tools/contracts"
spec = importlib.util.spec_from_file_location("continuation_generator", ROOT / "scripts/contracts/generate.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


@pytest.fixture(scope="module")
def continuation_validator(tmp_path_factory):
    stage = tmp_path_factory.mktemp("compiled-continuation")
    source = stage / "authored"
    shutil.copytree(TOOLS / "fixtures/probe", source)
    main = source / "main.tsp"
    main.write_text('import "@vuoro/contracts/operator-requests";\n' + main.read_text())
    node = Path(os.environ["VUORO_CONTRACT_NODE"])
    python = Path(os.environ["VUORO_CONTRACT_PYTHON"])
    generator._verify_tools(TOOLS, node, python)
    snapshot = generator._snapshot_project(source, ROOT / "contracts", TOOLS, stage, node)
    generator._compile(snapshot / "owner", stage, TOOLS, node)
    paths = list((stage / "compiled").rglob("contract.schema.json"))
    assert len(paths) == 1
    bundle = json.loads(paths[0].read_bytes())
    registry = Registry().with_resources(
        (value["$id"], Resource.from_contents(value))
        for value in bundle["$defs"].values() if "$id" in value
    )
    return Draft202012Validator(bundle["$defs"]["RequestContinuationReady"],
                               registry=registry, format_checker=FormatChecker())


def packet():
    return {
        "schema": "operator-request-continuation-ready/v1",
        "owner": {"repo_id": "example", "read_operation": "work.request.continuation-v1"},
        "request_id": "req_01JZZZZZZZZZZZZZZZZZZZZZZZ", "request_revision": 3,
        "resolution": {"event_revision": 3, "basis": "human-attestation",
                       "event_recorded_at": "2026-10-29T10:00:00Z"},
        "dependants": [{"work_item_id": 9007199254740991, "session_id": "previous-session",
                        "work_revision": "item:example@status:pending",
                        "current_work_revision": "item:example@status:pending",
                        "state": "continuation-ready"}],
    }


def test_compiled_continuation_accepts_integer_identity_and_nullable_current_basis(continuation_validator):
    value = packet()
    # JSON Schema format is an annotation; the native consumer validates timestamps.
    assert continuation_validator.schema["$defs"]["ContinuationResolution"]["properties"]["event_recorded_at"]["format"] == "date-time"
    continuation_validator.validate(value)
    value["dependants"][0].update(current_work_revision=None, state="needs-refresh")
    continuation_validator.validate(value)


@pytest.mark.parametrize("change", ["string-id", "unsafe-id", "empty-basis", "unsupported-basis", "extra-field"])
def test_compiled_continuation_rejects_wire_drift(continuation_validator, change):
    value = deepcopy(packet())
    if change == "string-id":
        value["dependants"][0]["work_item_id"] = "55"
    elif change == "unsafe-id":
        value["dependants"][0]["work_item_id"] = 9007199254740992
    elif change == "empty-basis":
        value["dependants"][0]["work_revision"] = ""
    elif change == "unsupported-basis":
        value["resolution"]["basis"] = "caller-verified"
    else:
        value["protected_effect_authorized"] = True
    assert list(continuation_validator.iter_errors(value))

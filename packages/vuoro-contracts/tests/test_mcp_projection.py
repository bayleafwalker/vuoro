"""Adversarial tests for the definition-only MCP tool projection."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil

import pytest
from vuoro_contracts import ContractError

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).parent / "fixtures/probe"
SOURCE = ROOT / "tools/contracts/fixtures/probe"
spec = importlib.util.spec_from_file_location("mcp_projection_generator", ROOT / "scripts/contracts/generate.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def inputs():
    manifest = json.loads((FIXTURE / "operation-manifest.json").read_text())
    bundle = json.loads((FIXTURE / "schema/probe.schema.json").read_text())
    manifest["operations"]["inspect"]["exposure"] = "public"
    manifest["operations"]["submit"]["exposure"] = "public"
    mappings = {
        "inspect": {"name": "probe_inspect", "title": "Inspect probe", "description": "Read probe state.", "intent": "read"},
        "submit": {"name": "probe_submit", "title": "Propose probe", "description": "Prepare a probe proposal.", "intent": "proposal"},
    }
    return manifest, bundle, mappings


def test_explicit_read_and_proposal_projection_is_closed_and_definition_only():
    manifest, bundle, mappings = inputs()
    original = deepcopy((manifest, bundle, mappings))
    tools = generator.public_mcp_tools(manifest, bundle, mappings)
    assert [tool["name"] for tool in tools] == ["probe_inspect", "probe_submit"]
    assert [tool["intent"] for tool in tools] == ["read", "proposal"]
    assert [(tool["binding"]["operationId"], tool["binding"]["path"]) for tool in tools] == [
        ("inspect", "/probe/{key}"), ("submit", "/probe/{key}")]
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in tools)
    assert all(set(tool) == {"name", "title", "description", "intent", "inputSchema", "binding"} for tool in tools)
    assert all("handler" not in tool and "authorization" not in tool and "grant" not in tool for tool in tools)
    assert (manifest, bundle, mappings) == original


def test_only_current_public_operations_enter_the_catalog():
    manifest, bundle, _ = inputs()
    manifest["operations"]["submit"]["exposure"] = "internal"
    tools = generator.public_mcp_tools(manifest, bundle, {"inspect": {
        "name": "probe_inspect", "title": "Inspect probe", "description": "Read probe state.", "intent": "read"}})
    assert [tool["binding"]["operationId"] for tool in tools] == ["inspect"]
    with pytest.raises(ContractError):
        generator.public_mcp_tools(manifest, bundle, {"inspect": {
            "name": "probe_inspect", "title": "Inspect probe", "description": "Read probe state.", "intent": "read"},
            "submit": {"name": "probe_submit", "title": "Submit probe", "description": "Propose probe.", "intent": "proposal"}})


@pytest.mark.parametrize("change", ["missing", "extra", "internal", "duplicate-name", "unknown-intent", "header", "path-mismatch"])
def test_projection_refuses_ambiguous_or_unsafe_mappings(change):
    manifest, bundle, mappings = inputs()
    if change == "missing":
        mappings.pop("submit")
    elif change == "extra":
        mappings["retiredOperation"] = dict(mappings["inspect"])
    elif change == "internal":
        manifest["operations"]["submit"]["exposure"] = "internal"
    elif change == "duplicate-name":
        mappings["submit"]["name"] = mappings["inspect"]["name"]
    elif change == "unknown-intent":
        mappings["submit"]["intent"] = "execute"
    elif change == "header":
        manifest["operations"]["inspect"]["parameters"].append({"in": "header", "name": "X-Secret", "required": True, "schema": "Key.json"})
    elif change == "path-mismatch":
        manifest["operations"]["inspect"]["path"] = "/probe/{other}"
    with pytest.raises(ContractError):
        generator.public_mcp_tools(manifest, bundle, mappings)


def test_explicit_mapping_generates_and_authenticates_compile_output(tmp_path, monkeypatch):
    source = tmp_path / "source"
    shutil.copytree(SOURCE, source)
    metadata_path = source / "contract.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["exposures"] = {"inspect": "public", "submit": "public", "empty": "internal", "nullBody": "internal"}
    metadata["mcpTools"] = {
        "inspect": {"name": "probe_inspect", "title": "Inspect probe", "description": "Read probe state.", "intent": "read"},
        "submit": {"name": "probe_submit", "title": "Propose probe", "description": "Prepare a probe proposal.", "intent": "proposal"},
    }
    metadata_path.write_text(json.dumps(metadata) + "\n")
    output = tmp_path / "generated"
    assert generator.main(["--source", str(source), "--output", str(output), "--contract-id", "probe/v1", "--write"]) == 0
    toolset = json.loads((output / "mcp-tools.json").read_bytes())
    from jsonschema import Draft202012Validator
    schema = json.loads((ROOT / "tools/contracts/metadata/mcp-toolset.schema.json").read_bytes())
    Draft202012Validator(schema).validate(toolset)
    assert [tool["name"] for tool in toolset["tools"]] == ["probe_inspect", "probe_submit"]
    assert len(toolset["provenance"]["sourceSha256"]) == 64
    assert (output / "mcp-tools.json").relative_to(output).as_posix() in json.loads((output / "integrity.json").read_bytes())["files"]

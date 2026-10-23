"""Root-owned F1a oracle. Generic values are supporting evidence, never an owner."""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest
from vuoro_contracts import ABSENT, ContractBundle, ContractError, parse_json, validate_bundle

FIXTURE = Path(__file__).parent / "fixtures/probe"

def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")

def seal(root):
    files = {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
             for p in root.rglob("*") if p.is_file() and p.name != "integrity.json"}
    write_json(root / "integrity.json", {"schemaVersion": "vuoro-contract-integrity/v1", "files": files})
    return sha256((root / "integrity.json").read_bytes()).hexdigest()

@pytest.fixture
def generated(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "generated")
    return tmp_path / "generated"

@pytest.fixture
def contract(generated):
    return ContractBundle.load(generated, integrity_sha256=seal(generated))

@pytest.fixture
def bundle():
    return json.loads((FIXTURE / "schema/probe.schema.json").read_text())

def input_value(revision="9007199254740995"):
    return {"revision": revision, "enabled": True, "nested": {"label": "safe"}}

def test_positive_profile_and_identity_preserve_annotations(bundle):
    before = deepcopy(bundle)
    bundle["$defs"]["Input"]["description"] = "opaque annotation"
    assert validate_bundle(bundle) is None
    assert bundle["$defs"]["Input"]["properties"] == before["$defs"]["Input"]["properties"]

@pytest.mark.parametrize("keyword,value", [
    ("default", "invented"), ("format", "date-time"), ("maxLenght", 2),
    ("$dynamicRef", "Input.json"), ("$recursiveRef", "Input.json"),
    ("if", {"type": "object"}), ("dependentSchemas", {}),
])
def test_unknown_or_unqualified_keywords_refuse_at_profile(bundle, keyword, value):
    bundle["$defs"]["Input"][keyword] = value
    with pytest.raises(ContractError): validate_bundle(bundle)

@pytest.mark.parametrize("ref", ["https://untrusted.invalid/schema", "missing.json", "#", "#/properties/x", "Input.json#", "Input.json#/properties/x"])
def test_only_declared_local_named_references(bundle, ref):
    bundle["$defs"]["Input"]["properties"]["nested"] = {"$ref": ref}
    with pytest.raises(ContractError): validate_bundle(bundle)

@pytest.mark.parametrize("change", ["duplicate", "remote", "empty", "nested-id", "recursive"])
def test_resource_identity_and_cycles_refuse(bundle, change):
    if change == "duplicate": bundle["$defs"]["Input"]["$id"] = "Result.json"
    if change == "remote": bundle["$defs"]["Input"]["$id"] = "https://untrusted.invalid/input"
    if change == "empty": bundle["$defs"]["Input"]["$id"] = ""
    if change == "nested-id": bundle["$defs"]["Input"]["properties"]["enabled"]["$id"] = "Nested.json"
    if change == "recursive": bundle["$defs"]["Input"]["properties"]["nested"] = {"$ref": "Input.json"}
    with pytest.raises(ContractError): validate_bundle(bundle)

@pytest.mark.parametrize("counter", ["0", "9007199254740995", "9223372036854775807"])
def test_exact_counter_positive_no_copy_or_coercion(contract, counter):
    value = input_value(counter)
    assert contract.validate_request("submit", value) is value
    assert value["revision"] == counter

@pytest.mark.parametrize("counter", [0, 9007199254740995, True, "00", "01", "-1", "+1", "1.0", "1e2", "9223372036854775808", "1\n", None])
def test_counter_wrong_wire_or_overflow_refuses(contract, counter):
    with pytest.raises(ContractError): contract.validate_request("submit", input_value(counter))

@pytest.mark.parametrize("note", [ABSENT, None, ""])
def test_authored_optional_nullable_presence_preserved(contract, note):
    value = input_value()
    if note is not ABSENT: value["note"] = note
    before = deepcopy(value)
    assert contract.validate_request("submit", value) is value
    assert value == before
    assert ("note" in value) is (note is not ABSENT)

@pytest.mark.parametrize("mutate", [
    lambda x: x.update(extra="private-body-marker"),
    lambda x: x["nested"].update(extra="private-body-marker"),
    lambda x: x.update(enabled="true"),
    lambda x: x.update(enabled=1),
    lambda x: x.pop("revision"),
    lambda x: x.update(nested=None),
    lambda x: x.update(revision=float("nan")),
])
def test_strict_nested_commands_safe_errors(contract, mutate):
    value = input_value(); mutate(value)
    with pytest.raises(ContractError) as error: contract.validate_request("submit", value)
    assert "private-body-marker" not in str(error.value)
    assert isinstance(error.value.code, str) and isinstance(error.value.path, tuple)

@pytest.mark.parametrize("status,value", [(200,{"state":"ready","revision":"0"}), (400,{"code":"invalid"}), (503,{"code":"unavailable"})])
def test_each_status_uses_its_declared_schema(contract, status, value):
    assert contract.validate_response("submit", status, value) is value

@pytest.mark.parametrize("status,value", [(200,{"state":"future","revision":"0"}), (200,{"code":"unavailable"}), (400,{"state":"ready","revision":"0"}), (503,{"code":"future"}), (201,{"state":"ready","revision":"0"}), (True,{"state":"ready","revision":"0"}), ("200",{"state":"ready","revision":"0"})])
def test_unknown_status_or_lifecycle_never_success(contract, status, value):
    with pytest.raises(ContractError): contract.validate_response("submit", status, value)

def test_metadata_extension_is_explicit_not_lifecycle_extension(contract):
    value = {"state":"ready", "revision":"0", "metadata":{"future":{"value":3}}}
    assert contract.validate_response("submit", 200, value) is value
    value["future"] = 3
    with pytest.raises(ContractError): contract.validate_response("submit", 200, value)

def test_absent_body_is_not_null_empty_object_or_empty_text(contract):
    assert contract.validate_request("inspect", ABSENT) is ABSENT
    for value in (None, {}, "", b""):
        with pytest.raises(ContractError): contract.validate_request("inspect", value)

def test_explicit_absent_response_body(generated):
    path = generated / "operation-manifest.json"; m = json.loads(path.read_text())
    m["operations"]["inspect"]["responses"] = {"204": None}
    m["operations"]["inspect"]["responseMaxBytes"] = {"204": None}
    write_json(path,m)
    c = ContractBundle.load(generated, integrity_sha256=seal(generated))
    assert c.validate_response("inspect",204,ABSENT) is ABSENT
    for value in (None,{},""):
        with pytest.raises(ContractError): c.validate_response("inspect",204,value)

def test_unknown_operation_schema_parameter_and_location_refuse(contract):
    for action in (lambda: contract.validate_schema("missing.json", {}),
                   lambda: contract.validate_request("missing", {}),
                   lambda: contract.validate_parameter("submit","cookie","key","x"),
                   lambda: contract.validate_parameter("submit","path","other","x")):
        with pytest.raises(ContractError): action()
    assert contract.validate_parameter("submit","path","key","x") == "x"
    with pytest.raises(ContractError): contract.validate_parameter("submit","path","key",1)

@pytest.mark.parametrize("change", ["schema-id", "status", "dialect", "version", "identity", "escape", "duplicate-binding", "unknown-manifest", "bytes", "status-bounds", "duplicate-parameter"])
def test_load_refuses_invalid_manifest_even_with_consistent_integrity(generated, change):
    path = generated / "operation-manifest.json"; m=json.loads(path.read_text());op=m["operations"]["submit"]
    if change == "schema-id": op["responses"]["503"]="Missing.json"
    if change == "status": op["responses"]["default"]="Result.json"
    if change == "dialect": m["schemaDialect"]="draft7"
    if change == "version": m["schemaVersion"]="future"
    if change == "identity": m["schemaBundle"]["id"]="other.json"
    if change == "escape": m["schemaBundle"]["path"]="../probe.schema.json"
    if change == "duplicate-binding": m["operations"]["alias"]=deepcopy(op)
    if change == "unknown-manifest": m["extra"]=True
    if change == "bytes": op["requestMaxBytes"]=0
    if change == "status-bounds": op["responseMaxBytes"].pop("503")
    if change == "duplicate-parameter": op["parameters"].append(deepcopy(op["parameters"][0]))
    write_json(path,m)
    with pytest.raises(ContractError): ContractBundle.load(generated, integrity_sha256=seal(generated))

def test_load_uses_same_profile_gate(generated):
    path=generated/"schema/probe.schema.json"; b=json.loads(path.read_text())
    b["$defs"]["Input"]["default"]={};write_json(path,b)
    with pytest.raises(ContractError): ContractBundle.load(generated, integrity_sha256=seal(generated))

@pytest.mark.parametrize("change",["extra","missing","content","external-digest","symlink"])
def test_complete_inventory_and_external_digest(generated,tmp_path,change):
    digest=seal(generated)
    if change=="extra": (generated/"unexpected.bin").write_bytes(b"x")
    if change=="missing": (generated/"openapi/probe.openapi.json").unlink()
    if change=="content": (generated/"README.txt").write_text("changed")
    if change=="external-digest": digest="0"*64
    if change=="symlink":
        external=tmp_path/"outside";external.write_text("outside");(generated/"link").symlink_to(external)
    with pytest.raises(ContractError): ContractBundle.load(generated,integrity_sha256=digest)

@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":{"y":1,"y":2}}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'\xff', b'', b'{} trailing'])
def test_strict_json_before_dispatch(raw):
    with pytest.raises(ContractError): parse_json(raw,max_bytes=1024)

def test_utf8_byte_bound_not_character_count_and_original_values():
    raw='{"x":"é"}'.encode()
    assert parse_json(raw,max_bytes=len(raw)) == {"x":"é"}
    with pytest.raises(ContractError): parse_json(raw,max_bytes=len(raw)-1)
    assert parse_json(b'{"revision":"9007199254740995"}',max_bytes=128)["revision"] == "9007199254740995"


@pytest.mark.parametrize("nonjson", [float("nan"), float("inf"), float("-inf"), b"bytes", (1,2), {1:"non-string-key"}])
def test_open_metadata_still_requires_finite_json(contract, nonjson):
    value={"state":"ready","revision":"0","metadata":{"extension":nonjson}}
    with pytest.raises(ContractError):contract.validate_response("submit",200,value)


def test_duplicate_manifest_keys_refuse_before_boundary_load(generated):
    path=generated/"operation-manifest.json"
    text=path.read_text().replace('"contractId": "probe/v1",','"contractId": "probe/v1", "contractId": "other",')
    path.write_text(text)
    with pytest.raises(ContractError):ContractBundle.load(generated,integrity_sha256=seal(generated))


def test_explicit_json_null_body_differs_from_absent_body(generated):
    path=generated/"schema/probe.schema.json";b=json.loads(path.read_text())
    b["$defs"]["NullBody"]={"$schema":b["$schema"],"$id":"NullBody.json","type":"null"}
    write_json(path,b)
    path=generated/"operation-manifest.json";m=json.loads(path.read_text())
    m["operations"]["inspect"]["responses"]={"200":"NullBody.json"}
    write_json(path,m)
    c=ContractBundle.load(generated,integrity_sha256=seal(generated))
    assert c.validate_response("inspect",200,None) is None
    with pytest.raises(ContractError):c.validate_response("inspect",200,ABSENT)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), b"private-bytes", object()])
def test_error_paths_and_traceback_do_not_expose_untrusted_metadata_keys(contract, invalid):
    import traceback
    canary = "PRIVATE_UNTRUSTED_METADATA_KEY_20261023"
    value = {"state": "ready", "revision": "0", "metadata": {canary: invalid}}
    with pytest.raises(ContractError) as caught:
        contract.validate_response("submit", 200, value)
    rendered = str(caught.value) + repr(vars(caught.value)) + "".join(
        traceback.format_exception(caught.value)
    )
    assert canary not in rendered


@pytest.mark.parametrize("direction", ["request", "response"])
def test_operation_value_byte_bound_uses_utf8_with_exact_boundary(generated, direction):
    manifest_path = generated / "operation-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    value = input_value() if direction == "request" else {
        "state": "ready", "revision": "0", "metadata": {"safe": "é" * 12}
    }
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    def set_bound(bound):
        if direction == "request": manifest["operations"]["submit"]["requestMaxBytes"] = bound
        else: manifest["operations"]["submit"]["responseMaxBytes"]["200"] = bound
        write_json(manifest_path, manifest)
        return ContractBundle.load(generated, integrity_sha256=seal(generated))
    def validate(loaded):
        return loaded.validate_request("submit", value) if direction == "request" else loaded.validate_response("submit", 200, value)
    assert validate(set_bound(size)) is value
    with pytest.raises(ContractError) as caught: validate(set_bound(size - 1))
    assert caught.value.code == "body_limit"


def test_each_error_status_has_a_distinct_schema_not_first_error_fallback(generated):
    schema_path = generated / "schema/probe.schema.json"
    bundle = json.loads(schema_path.read_text())
    for name, code in (("BadRequest", "invalid"), ("Unavailable", "unavailable")):
        model = deepcopy(bundle["$defs"]["Refusal"])
        model["$id"] = name + ".json"
        model["properties"]["code"]["enum"] = [code]
        bundle["$defs"][name] = model
    write_json(schema_path, bundle)
    path = generated / "operation-manifest.json"
    manifest = json.loads(path.read_text())
    manifest["operations"]["submit"]["responses"].update({"400": "BadRequest.json", "503": "Unavailable.json"})
    write_json(path, manifest)
    loaded = ContractBundle.load(generated, integrity_sha256=seal(generated))
    bad = {"code": "invalid"}
    unavailable = {"code": "unavailable"}
    assert loaded.validate_response("submit", 400, bad) is bad
    assert loaded.validate_response("submit", 503, unavailable) is unavailable
    for status, value in ((400, unavailable), (503, bad)):
        with pytest.raises(ContractError): loaded.validate_response("submit", status, value)

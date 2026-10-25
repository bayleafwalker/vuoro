"""Pure generator oracles; no compiler, installs, SDK or server launched here."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import pytest
from vuoro_contracts import ContractError

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).parent / "fixtures/probe"
spec = importlib.util.spec_from_file_location("contract_generator", ROOT / "scripts/contracts/generate.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)

def inputs():
    manifest=json.loads((FIXTURE/"operation-manifest.json").read_text())
    return (json.loads((FIXTURE/"openapi/probe.openapi.json").read_text()),
            json.loads((FIXTURE/"schema/probe.schema.json").read_text()),
            dict(contract_id=manifest["contractId"],contract_version=manifest["contractVersion"],
                 source_sha256=manifest["provenance"]["sourceSha256"],toolchain_sha256=manifest["provenance"]["toolchainSha256"],
                 exposures={name:op["exposure"] for name,op in manifest["operations"].items()},
                 byte_bounds={name:{"requestMaxBytes":op["requestMaxBytes"],"responseMaxBytes":op["responseMaxBytes"]} for name,op in manifest["operations"].items()}))

def test_derive_all_declared_statuses_from_named_emission():
    openapi,bundle,metadata=inputs()
    expected=json.loads((FIXTURE/"operation-manifest.json").read_text())
    before=deepcopy((openapi,bundle,metadata))
    assert generator.operation_manifest(openapi,bundle,**metadata)==expected
    assert (openapi,bundle,metadata)==before

@pytest.mark.parametrize("change",["inline-request","inline-error","unknown-schema","duplicate-operation","default","range","missing-exposure","ambiguous-exposure","missing-bound","oas-version","unsupported-profile","duplicate-parameter","optional-body"])
def test_manifest_refuses_unmappable_or_unqualified_projection(change):
    o,b,m=inputs();op=o["paths"]["/probe/{key}"]["post"]
    if change=="inline-request":op["requestBody"]["content"]["application/json"]["schema"]={"type":"object"}
    if change=="inline-error":op["responses"]["503"]["content"]["application/json"]["schema"]={"type":"object"}
    if change=="unknown-schema":op["responses"]["503"]["content"]["application/json"]["schema"]={"$ref":"#/components/schemas/Missing"}
    if change=="duplicate-operation":o["paths"]["/alias"]={"post":deepcopy(op)}
    if change=="default":op["responses"]["default"]=op["responses"].pop("503")
    if change=="range":op["responses"]["5XX"]=op["responses"].pop("503")
    if change=="missing-exposure":m["exposures"].pop("submit")
    if change=="ambiguous-exposure":m["exposures"]["submit"]="inferred-public"
    if change=="missing-bound":m["byte_bounds"]["submit"]["responseMaxBytes"].pop("503")
    if change=="oas-version":o["openapi"]="3.1.0"
    if change=="unsupported-profile":b["$defs"]["Input"]["default"]={}
    if change=="duplicate-parameter":op["parameters"].append(deepcopy(op["parameters"][0]))
    if change=="optional-body":op["requestBody"]["required"]=False
    with pytest.raises(ContractError):generator.operation_manifest(o,b,**m)

def test_internal_operations_excluded_from_mcp():
    _,bundle,_=inputs();m=json.loads((FIXTURE/"operation-manifest.json").read_text())
    assert generator.public_mcp_tools(m,bundle,{})==[]

@pytest.mark.parametrize("relative",["schema/probe.schema.json","openapi/probe.openapi.json","operation-manifest.json","clients/python/client.py","clients/typescript/validated-sdk.ts"])
def test_drift_detects_each_projection_attachment_and_inventory(tmp_path,relative):
    a=tmp_path/"a";b=tmp_path/"b";shutil.copytree(FIXTURE,a);shutil.copytree(FIXTURE,b)
    for tree in (a,b):
        for name in ("clients/python/client.py","clients/typescript/validated-sdk.ts"):
            p=tree/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text("oracle marker\n")
    assert generator.compare_trees(a,b) is None
    path=b/relative;path.write_bytes(path.read_bytes()+b" ")
    with pytest.raises(ContractError):generator.compare_trees(a,b)

def test_inventory_all_bytes_no_extension_ignore_and_no_links(tmp_path):
    tree=tmp_path/"tree";tree.mkdir();(tree/"unknown.binary").write_bytes(b"\x00\xff")
    assert generator.inventory_tree(tree)=={"unknown.binary":sha256(b"\x00\xff").hexdigest()}
    outside=tmp_path/"outside";outside.write_bytes(b"x");(tree/"link").symlink_to(outside)
    with pytest.raises(ContractError):generator.inventory_tree(tree)

@pytest.mark.parametrize("change",["extra","missing"])
def test_drift_detects_complete_relative_path_inventory(tmp_path,change):
    a=tmp_path/"a";b=tmp_path/"b";shutil.copytree(FIXTURE,a);shutil.copytree(FIXTURE,b)
    if change=="extra":(b/"unknown.bin").write_bytes(b"x")
    else:(b/"README.txt").unlink()
    with pytest.raises(ContractError):generator.compare_trees(a,b)


def test_inventory_refuses_directory_symlink_instead_of_ignoring_it(tmp_path):
    tree=tmp_path/"tree";tree.mkdir();(tree/"file").write_bytes(b"x")
    outside=tmp_path/"outside";outside.mkdir();(outside/"hidden").write_bytes(b"x")
    (tree/"directory-link").symlink_to(outside,target_is_directory=True)
    with pytest.raises(ContractError):generator.inventory_tree(tree)


def test_derive_distinct_named_error_status_schemas_without_fallback():
    openapi, bundle, metadata = inputs()
    for name, code in (("BadRequest", "invalid"), ("Unavailable", "unavailable")):
        model = deepcopy(bundle["$defs"]["Refusal"])
        model["$id"] = name + ".json"
        model["properties"]["code"]["enum"] = [code]
        bundle["$defs"][name] = model
        projection = deepcopy(openapi["components"]["schemas"]["Refusal"])
        openapi["components"]["schemas"][name] = projection
    operation = openapi["paths"]["/probe/{key}"]["post"]
    for status, name in (("400", "BadRequest"), ("503", "Unavailable")):
        operation["responses"][status]["content"]["application/json"]["schema"] = {"$ref": "#/components/schemas/" + name}
    result = generator.operation_manifest(openapi, bundle, **metadata)
    assert result["operations"]["submit"]["responses"]["400"] == "BadRequest.json"
    assert result["operations"]["submit"]["responses"]["503"] == "Unavailable.json"

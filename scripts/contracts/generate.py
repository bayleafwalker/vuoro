#!/usr/bin/env python3
"""Pure F1a manifest and bundle mechanics; production CLI remains an F1b stub."""
from __future__ import annotations
from collections.abc import Mapping
import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import re
import tomllib
from hashlib import sha256
from pathlib import Path
import re
from typing import Any

from vuoro_contracts import ContractError, parse_json, validate_bundle

_METHODS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH",
            "delete": "DELETE", "head": "HEAD", "options": "OPTIONS"}
_STATUS = re.compile(r"^[1-5][0-9]{2}$")
_SCHEMA_ID = re.compile(r"^[A-Za-z0-9_.-]+$")

# Qualified source inputs are pinned to the root-admitted freeze. Upgrade work
# must requalify the artifacts, then deliberately update these reviewed pins.
_QUALIFIED_INPUT_SHA256 = {
    "tools/contracts/package.json": "b810a5a4b1201f7375989304a7be7acfa81b97a7b6b3f4633e11c15364d4303e",
    "tools/contracts/package-lock.json": "f51db6e4bd3f2d22cd3fd9bb5b6f7c2705a019bbce00b3569f1a796113ba2dce",
    "tools/contracts/pyproject.toml": "cf6acd87c346aac3ed1bec6fd111436af93b113fcf79ff709205b93aa8e6b418",
    "tools/contracts/uv.lock": "74cd467bf25ff11fa74096e35edbb62d3bb0baeb0957fd2b5f3ea5c71b870175",
    "tools/contracts/toolchain/source-pins.json": "de0d5313ce88e738a53925953f239c08cd6621004c470090e83b771f37a99d10",
    "tools/contracts/toolchain/tool-artifacts.json": "32aaca517652f189469db7eebb43db5c38d40d6dc38f73214725ea3d1ccf296e",
    "tools/contracts/toolchain/flake.lock": "27ee4fbd30db0101fea9d69cbe4028d7ccdebf5ffdc4f3cf538bcf4fb3de8578",
    "tools/contracts/toolchain/flake.nix": "93bdb4acbdb3acd1232793b7e0f3f381568d29a202ff277ae5de79b7f4019f68",
    "tools/contracts/openapi-python-client.yaml": "8137aaffde27e43f9ddea16a16ecce20780265990660bdd9330029367d6ad23e",
    "tools/contracts/tspconfig.template.yaml": "d6b5b33902b26f94a6bc1ae9bcccd8cfffac8af020f74304b215a9b7e624fe91",
    "tools/contracts/python-templates/endpoint_module.py.jinja": "9a626b839af95ca1cdb936e4dee7bedc5839e0aafbed15bc6869d9a8844d0348",
    "tools/contracts/templates/strict-json.ts": "356db0604d14267133842fa7b4b4a467fad6dd8b8b6f9bfa48d123e78c605831",
    "tools/contracts/templates/validated-client.py.template": "1135cbc41fb74eeb27dcbcf7242d107bfd035b844a1e21aee945c5d72899575f",
    "tools/contracts/templates/validated-sdk.ts.template": "c4f700947db3726b5d3ee8d7c6230e30dd12e1ce840bf4a04f1f13d7a68bb58e",
}


def _fail() -> None:
    raise ContractError("manifest")


def _schema_id(schema: Any, openapi: Mapping[str, Any], bundle: Mapping[str, Any]) -> str:
    if not isinstance(schema, Mapping) or set(schema) != {"$ref"}:
        _fail()
    ref = schema.get("$ref")
    prefix = "#/components/schemas/"
    if not isinstance(ref, str) or not ref.startswith(prefix):
        _fail()
    name = ref[len(prefix):]
    components = openapi.get("components", {}).get("schemas", {})
    definitions = bundle.get("$defs", {})
    if (not name or "/" in name or name not in components or name not in definitions or
            not isinstance(definitions[name], Mapping)):
        _fail()
    identifier = definitions[name].get("$id")
    if not isinstance(identifier, str) or not _SCHEMA_ID.fullmatch(identifier):
        _fail()
    return identifier


def _content_schema(value: Any, openapi: Mapping[str, Any], bundle: Mapping[str, Any]) -> str | None:
    if not isinstance(value, Mapping):
        _fail()
    # Response/request object references are not the direct named schema
    # boundaries qualified by F1a. In particular, a response ref cannot be
    # mistaken for an explicitly bodyless response.
    if "$ref" in value:
        _fail()
    content = value.get("content")
    if content is None:
        return None
    if not isinstance(content, Mapping) or set(content) != {"application/json"}:
        _fail()
    media = content["application/json"]
    if not isinstance(media, Mapping) or "schema" not in media:
        _fail()
    return _schema_id(media["schema"], openapi, bundle)


def operation_manifest(openapi: Mapping[str, Any], bundle: Mapping[str, Any], *,
                       contract_id: str, contract_version: str,
                       source_sha256: str, toolchain_sha256: str,
                       exposures: Mapping[str, str],
                       byte_bounds: Mapping[str, Any]) -> dict[str, Any]:
    """Derive named request/parameter/status schema IDs without field inventories."""
    try:
        if not isinstance(openapi, Mapping) or openapi.get("openapi") != "3.0.0":
            _fail()
        validate_bundle(bundle)
        if (not isinstance(contract_id, str) or not contract_id or
                not isinstance(contract_version, str) or not contract_version or
                not isinstance(source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha256) or
                not isinstance(toolchain_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", toolchain_sha256)):
            _fail()
        paths = openapi.get("paths")
        if not isinstance(paths, Mapping) or not paths:
            _fail()
        if not isinstance(exposures, Mapping) or not isinstance(byte_bounds, Mapping):
            _fail()
        operations: dict[str, Any] = {}
        bindings: set[tuple[str, str]] = set()
        for path, path_item in sorted(paths.items()):
            if not isinstance(path, str) or not path.startswith("/") or not isinstance(path_item, Mapping):
                _fail()
            for method, operation in sorted(path_item.items()):
                if method not in _METHODS:
                    # Inherited parameters and path-item refs carry semantics
                    # that this finite projection does not currently resolve.
                    if method in {"parameters", "$ref"}:
                        _fail()
                    # Descriptive path-item annotations have no boundary semantics.
                    if method in {"summary", "description", "servers"}:
                        continue
                    _fail()
                if not isinstance(operation, Mapping):
                    _fail()
                name = operation.get("operationId")
                if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in operations:
                    _fail()
                exposure = exposures.get(name)
                if exposure not in ("internal", "public"):
                    _fail()
                if not isinstance(byte_bounds.get(name), Mapping):
                    _fail()
                bounds = byte_bounds[name]
                if set(bounds) != {"requestMaxBytes", "responseMaxBytes"}:
                    _fail()
                parameters = operation.get("parameters", [])
                if not isinstance(parameters, list):
                    _fail()
                emitted_parameters: list[dict[str, Any]] = []
                seen_params: set[tuple[str, str]] = set()
                for parameter in parameters:
                    if not isinstance(parameter, Mapping):
                        _fail()
                    location, param_name = parameter.get("in"), parameter.get("name")
                    if location not in ("path", "query", "header") or not isinstance(param_name, str) or not param_name:
                        _fail()
                    key = (location, param_name)
                    if key in seen_params:
                        _fail()
                    seen_params.add(key)
                    if type(parameter.get("required")) is not bool:
                        _fail()
                    emitted_parameters.append({"in": location, "name": param_name,
                                               "required": parameter["required"],
                                               "schema": _schema_id(parameter.get("schema"), openapi, bundle)})
                request = None
                body = operation.get("requestBody")
                if body is not None:
                    if not isinstance(body, Mapping) or body.get("required") is not True:
                        _fail()
                    request = _content_schema(body, openapi, bundle)
                    if request is None:
                        _fail()
                request_cap = bounds["requestMaxBytes"]
                if request is None:
                    if request_cap is not None:
                        _fail()
                elif type(request_cap) is not int or request_cap <= 0:
                    _fail()
                response_specs = operation.get("responses")
                if not isinstance(response_specs, Mapping) or not response_specs:
                    _fail()
                responses: dict[str, str | None] = {}
                response_caps = bounds["responseMaxBytes"]
                if not isinstance(response_caps, Mapping):
                    _fail()
                for status, response in sorted(response_specs.items()):
                    if not isinstance(status, str) or not _STATUS.fullmatch(status):
                        _fail()
                    identifier = _content_schema(response, openapi, bundle)
                    if status not in response_caps:
                        _fail()
                    cap = response_caps[status]
                    if (identifier is None) != (cap is None):
                        _fail()
                    if cap is not None and (type(cap) is not int or cap <= 0):
                        _fail()
                    responses[status] = identifier
                if set(response_caps) != set(responses):
                    _fail()
                identity = (_METHODS[method], path)
                if identity in bindings:
                    _fail()
                bindings.add(identity)
                operations[name] = {"method": _METHODS[method], "path": path, "request": request,
                                    "parameters": emitted_parameters, "responses": responses,
                                    "exposure": exposure, "requestMaxBytes": request_cap,
                                    "responseMaxBytes": dict(response_caps)}
        if set(exposures) != set(operations) or set(byte_bounds) != set(operations):
            _fail()
        bundle_id = bundle.get("$id")
        if not isinstance(bundle_id, str) or not _SCHEMA_ID.fullmatch(bundle_id):
            _fail()
        result = {"schemaVersion": "vuoro-contract-bundle/v1", "contractId": contract_id,
                  "contractVersion": contract_version,
                  "schemaDialect": "https://json-schema.org/draft/2020-12/schema",
                  "schemaBundle": {"path": "schema/" + bundle_id, "id": bundle_id},
                  "provenance": {"sourceSha256": source_sha256, "toolchainSha256": toolchain_sha256},
                  "operations": operations}
        # The package schema is the normative structural contract for emitted metadata.
        from vuoro_contracts.validation import _format_schema
        from jsonschema import Draft202012Validator
        Draft202012Validator(_format_schema("operation-manifest.schema.json")).validate(result)
        return result
    except ContractError:
        raise
    except Exception:
        raise ContractError("manifest") from None


def inventory_tree(root: Path) -> dict[str, str]:
    """Return every regular file's POSIX relative path and digest; refuse links."""
    try:
        base = Path(root)
        if base.is_symlink() or not base.is_dir():
            _fail()
        result: dict[str, str] = {}
        for path in base.rglob("*"):
            if path.is_symlink():
                _fail()
            if path.is_dir():
                continue
            if not path.is_file():
                _fail()
            result[path.relative_to(base).as_posix()] = sha256(path.read_bytes()).hexdigest()
        return dict(sorted(result.items()))
    except ContractError:
        raise
    except Exception:
        raise ContractError("integrity") from None


def compare_trees(expected: Path, actual: Path) -> None:
    """Refuse every inventory or byte drift between two complete trees."""
    if inventory_tree(expected) != inventory_tree(actual):
        raise ContractError("integrity")


def public_mcp_tools(manifest: Mapping[str, Any], bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Enforce explicit exposure; public tool mapping is intentionally not specified in F1a."""
    try:
        validate_bundle(bundle)
        operations = manifest.get("operations") if isinstance(manifest, Mapping) else None
        if not isinstance(operations, Mapping):
            _fail()
        if any(not isinstance(item, Mapping) or item.get("exposure") not in ("internal", "public")
               for item in operations.values()):
            _fail()
        if any(item["exposure"] == "public" for item in operations.values()):
            _fail()
        return []
    except ContractError:
        raise
    except Exception:
        raise ContractError("manifest") from None


def main(argv: list[str] | None = None) -> int:
    """Compile an authored TypeSpec project and atomically admit its projections."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contract-id", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    try:
        args = parser.parse_args(argv)
        root = Path(__file__).resolve().parents[2]
        source_arg=args.source.absolute()
        if any(part.is_symlink() for part in (source_arg,*source_arg.parents)): _fail()
        source = source_arg.resolve(strict=True)
        output_arg=args.output.absolute()
        if any(part.is_symlink() for part in (output_arg,*output_arg.parents)): _fail()
        parent=output_arg.parent.resolve(strict=True)
        output=parent/output_arg.name
        tools = root / "tools/contracts"
        if source.is_symlink() or not source.is_dir() or output == source or source in output.parents or output in source.parents:
            _fail()
        node = Path(os.environ["VUORO_CONTRACT_NODE"])
        python = Path(os.environ["VUORO_CONTRACT_PYTHON"])
        if not node.is_absolute() or not python.is_absolute():
            _fail()
        _verify_tools(tools, node, python)
        _validate_owner_project(source,tools,node)
        _validate_shared_project(root/"contracts",tools,node)
        if output.exists() or output.is_symlink():
            if output.is_symlink() or not output.is_dir():
                _fail()
            if inventory_tree(output).get(".vuoro-generated-marker") != sha256(b"vuoro-contract-output/v1\n").hexdigest():
                _fail()
        elif args.write is False:
            _fail()
        with tempfile.TemporaryDirectory(prefix="vuoro-contract-", dir=str(parent)) as scratch:
            stage = Path(scratch) / "stage"
            stage.mkdir()
            snapshot=_snapshot_project(source,root/"contracts",tools,stage,node)
            owner=snapshot/"owner"
            from vuoro_contracts import parse_json
            metadata=parse_json((owner/"contract.json").read_bytes(),max_bytes=65536)
            source_digest=_snapshot_digest(snapshot)
            tool_digest=_toolchain_digest(tools,root,snapshot/"node_modules/@vuoro/contracts")
            _compile(owner, stage, tools, node)
            openapi_file = next(stage.rglob("contract.openapi.json"), None)
            schema_file = next(stage.rglob("contract.schema.json"), None)
            if not openapi_file or not schema_file:
                _fail()
            openapi = json.loads(openapi_file.read_bytes())
            bundle = json.loads(schema_file.read_bytes())
            validate_bundle(bundle)
            manifest = operation_manifest(openapi, bundle, contract_id=args.contract_id,
                contract_version=metadata["contractVersion"], source_sha256=source_digest,
                toolchain_sha256=tool_digest, exposures=metadata["exposures"], byte_bounds=metadata["byteBounds"])
            tree = Path(scratch) / "candidate"
            tree.mkdir()
            (tree / "openapi").mkdir(); (tree / "schema").mkdir()
            shutil.copyfile(openapi_file, tree / "openapi/contract.openapi.json")
            shutil.copyfile(schema_file, tree / ("schema/" + bundle["$id"]))
            (tree / "operation-manifest.json").write_bytes(_json_bytes(manifest))
            (tree / ".vuoro-generated-marker").write_bytes(b"vuoro-contract-output/v1\n")
            _python_sdk(openapi_file, tree, tools, python, bundle)
            _typescript_sdk(tree, tools, node, manifest, bundle)
            _remove_generation_caches(tree)
            # Validate the exact staged projection and authenticate every file.
            _write_runtime_package(tree, root, bundle, manifest)
            files = inventory_tree(tree)
            (tree / "integrity.json").write_bytes(_json_bytes({"schemaVersion":"vuoro-contract-integrity/v1", "files":files}))
            from vuoro_contracts import ContractBundle
            ContractBundle.load(tree,integrity_sha256=sha256((tree/"integrity.json").read_bytes()).hexdigest())
            if output.exists():
                if inventory_tree(output) != inventory_tree(tree):
                    if not args.write: _fail()
                    backup = Path(scratch) / "old"
                    os.replace(output, backup)
                    try: os.replace(tree, output)
                    except BaseException: os.replace(backup, output); raise
            else:
                if not args.write: _fail()
                os.replace(tree, output)
        return 0
    except (SystemExit,):
        raise
    except Exception:
        from vuoro_contracts import ContractError
        error=__import__("sys").exc_info()[1]
        if isinstance(error,ContractError): raise error
        raise ContractError("manifest") from None


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _remove_generation_caches(root:Path)->None:
    for p in list(root.rglob("*")):
        if p.is_dir() and (p.name in {".ruff_cache", ".python-pycache", "__pycache__", ".pytest_cache"}): shutil.rmtree(p,ignore_errors=True)
        elif p.is_file() and p.suffix==".pyc": p.unlink()


def _validate_owner_project(source:Path,tools:Path,node:Path)->None:
    allowed={"contract.json","tspconfig.yaml"};files=[];size=0
    for p in source.rglob("*"):
        if p.is_symlink():_fail()
        if p.is_dir():continue
        if not p.is_file():_fail()
        rel=p.relative_to(source).as_posix()
        if rel not in allowed and not rel.endswith(".tsp"):_fail()
        files.append(p);size+=p.stat().st_size
        if len(files)>256 or size>4*1024*1024:_fail()
    if not {"main.tsp","contract.json","tspconfig.yaml"}.issubset({p.relative_to(source).as_posix() for p in files}):_fail()
    if (source/"tspconfig.yaml").read_bytes()!=(tools/"tspconfig.template.yaml").read_bytes():_fail()
    metadata=parse_json((source/"contract.json").read_bytes(),max_bytes=65536)
    if not isinstance(metadata,dict) or set(metadata)!={"contractVersion","exposures","byteBounds"}:_fail()
    _validate_tsp_imports(source,files,source,tools,node)


def _validate_shared_project(shared:Path,tools:Path,node:Path)->None:
    files=[];size=0
    for p in shared.rglob("*"):
        if p.is_symlink():_fail()
        if p.is_dir():continue
        if not p.is_file():_fail()
        rel=p.relative_to(shared).as_posix()
        if rel!="package.json" and not rel.endswith(".tsp"):_fail()
        files.append(p);size+=p.stat().st_size
        if len(files)>64 or size>1024*1024:_fail()
    manifest=parse_json((shared/"package.json").read_bytes(),max_bytes=65536)
    if manifest.get("name")!="@vuoro/contracts" or manifest.get("tspMain")!="main.tsp":_fail()
    if set(manifest.get("exports",{}))!={".","./http","./primitives"}:_fail()
    expected_exports={".":"./main.tsp","./http":"./http/main.tsp","./primitives":"./primitives/main.tsp"}
    for key,target in expected_exports.items():
        if manifest["exports"][key]!={"typespec":target}:_fail()
    if manifest.get("peerDependencies")!={"@typespec/compiler":"1.17.0","@typespec/http":"1.17.0","@typespec/json-schema":"1.17.0"}:_fail()
    if manifest.get("dependencies") or manifest.get("scripts") or manifest.get("bin"):_fail()
    _validate_tsp_imports(shared,files,shared,tools,node)


def _validate_tsp_imports(source:Path,files:list[Path],local_root:Path,tools:Path,node:Path)->None:
    external={"@vuoro/contracts","@vuoro/contracts/http","@vuoro/contracts/primitives","@typespec/compiler","@typespec/http","@typespec/openapi","@typespec/openapi3","@typespec/json-schema"}
    tsp_files=[file for file in files if file.suffix==".tsp"]
    if not tsp_files:return
    parser=tools/"node_modules/@typespec/compiler/dist/src/core/parser.js"
    kinds=tools/"node_modules/@typespec/compiler/dist/src/core/types.js"
    program=("import {readFileSync} from 'node:fs';import {pathToFileURL} from 'node:url';"
             "const [parserPath,kindsPath,...files]=process.argv.slice(1);"
             "const {parse}=await import(pathToFileURL(parserPath).href);"
             "const {SyntaxKind}=await import(pathToFileURL(kindsPath).href);"
             "const output=[];for(const file of files){const ast=parse(readFileSync(file,'utf8'));"
             "if(ast.parseDiagnostics.length)process.exit(3);"
             "output.push({file,imports:ast.statements.filter(x=>x.kind===SyntaxKind.ImportStatement).map(x=>x.path.value)});}"
             "process.stdout.write(JSON.stringify(output));")
    env={"PATH":str(node.parent),"HOME":str(tools),"LANG":"C.UTF-8","LC_ALL":"C.UTF-8","NODE_OPTIONS":""}
    checked=subprocess.run([str(node),"--input-type=module","-e",program,str(parser),str(kinds),*(str(p) for p in tsp_files)],cwd=tools,env=env,capture_output=True,text=True,timeout=30)
    if checked.returncode:_fail()
    try: parsed=json.loads(checked.stdout)
    except Exception: _fail()
    if not isinstance(parsed,list) or len(parsed)!=len(tsp_files):_fail()
    by_file={Path(item["file"]):item["imports"] for item in parsed if isinstance(item,dict) and set(item)=={"file","imports"} and isinstance(item["imports"],list)}
    if set(by_file)!=set(tsp_files):_fail()
    for file in tsp_files:
        for target in by_file[file]:
            if not isinstance(target,str):_fail()
            if target in external:continue
            if not target.startswith(".") or target.endswith((".js",".mjs",".cjs")) or "\\" in target: _fail()
            candidate=(file.parent/target).resolve(strict=True)
            if not candidate.is_relative_to(local_root.resolve()) or candidate.suffix!=".tsp" or candidate.is_symlink() or not candidate.is_file():_fail()


def _snapshot_project(source:Path,shared:Path,tools:Path,stage:Path,node:Path)->Path:
    snapshot=stage/"snapshot";snapshot.mkdir()
    shutil.copytree(source,snapshot/"owner")
    package=snapshot/"node_modules/@vuoro/contracts";package.parent.mkdir(parents=True)
    shutil.copytree(shared,package)
    _validate_owner_project(snapshot/"owner",tools,node)
    _validate_shared_project(package,tools,node)
    # The compiler and its selected peer packages remain the exact authenticated
    # offline installation; only the shared source package is shadowed.
    (snapshot/"node_modules/@typespec").symlink_to(tools/"node_modules/@typespec",target_is_directory=True)
    (snapshot/"tspconfig.yaml").write_bytes((tools/"tspconfig.template.yaml").read_bytes())
    for base in (snapshot/"owner",package):
        for p in base.rglob("*"):
            if p.is_file():p.chmod(0o444)
    return snapshot


def _snapshot_digest(snapshot:Path)->str:
    h=sha256()
    for label,base in (("owner",snapshot/"owner"),("contracts",snapshot/"node_modules/@vuoro/contracts")):
        for p in sorted(base.rglob("*")):
            if p.is_symlink():_fail()
            if p.is_file():
                rel=(label+"/"+p.relative_to(base).as_posix()).encode();h.update(len(rel).to_bytes(4,"big"));h.update(rel);h.update(sha256(p.read_bytes()).digest())
    return h.hexdigest()


def _toolchain_digest(tools:Path,root:Path,shared_snapshot:Path)->str:
    candidates=[tools/"package.json",tools/"package-lock.json",tools/"pyproject.toml",tools/"uv.lock",tools/"openapi-python-client.yaml",tools/"toolchain/source-pins.json",tools/"toolchain/tool-artifacts.json",root/"scripts/contracts/generate.py"]
    candidates += list((tools/"templates").glob("*.ts"))+list((tools/"templates").glob("*.template"))+[tools/"python-templates/endpoint_module.py.jinja"]
    h=sha256()
    for p in sorted(candidates,key=lambda x:x.relative_to(root).as_posix()):
        if p.is_file():
            rel=p.relative_to(root).as_posix().encode();data=p.read_bytes();h.update(len(rel).to_bytes(4,"big"));h.update(rel);h.update(sha256(data).digest())
    for p in sorted(shared_snapshot.rglob("*")):
        if p.is_symlink():_fail()
        if p.is_file():
            rel=("shared/"+p.relative_to(shared_snapshot).as_posix()).encode();data=p.read_bytes();h.update(len(rel).to_bytes(4,"big"));h.update(rel);h.update(sha256(data).digest())
    return h.hexdigest()


def _verify_tool_inputs(tools:Path)->dict[str,Any]:
    for relative,want in _QUALIFIED_INPUT_SHA256.items():
        path=tools.parents[1]/relative
        if not path.is_file() or sha256(path.read_bytes()).hexdigest()!=want: _fail()
    package=json.loads((tools/"package.json").read_bytes())
    lock=json.loads((tools/"package-lock.json").read_bytes())
    if lock.get("packages",{}).get("",{}).get("dependencies")!=package.get("dependencies") or lock.get("packages",{}).get("",{}).get("devDependencies")!=package.get("devDependencies"): _fail()
    pyproject=tomllib.loads((tools/"pyproject.toml").read_text())
    pylock=tomllib.loads((tools/"uv.lock").read_text())
    pins=json.loads((tools/"toolchain/source-pins.json").read_bytes())
    artifact=json.loads((tools/"toolchain/tool-artifacts.json").read_bytes())
    if pins.get("schemaVersion")!="vuoro-contract-toolchain/v1" or not isinstance(pins.get("versions"),dict): _fail()
    if artifact.get("schema")!="vuoro-contract-tool-artifacts/v1" or artifact.get("system")!="x86_64-linux": _fail()
    if set(artifact.get("sets",{}))!={"node","python","python_stdlib"} or set(artifact.get("executables",{}))!={"node","python","ruff"}: _fail()
    for name,entries in artifact["sets"].items():
        if not isinstance(entries,dict) or set(entries)!={"files","symlinks"} or not isinstance(entries["files"],dict) or not isinstance(entries["symlinks"],dict): _fail()
    expected_py={}
    for dep in pyproject.get("project",{}).get("dependencies",[]):
        match=re.match(r"^([A-Za-z0-9_.-]+)==([A-Za-z0-9_.-]+)$",dep)
        if not match: _fail()
        expected_py[match.group(1).lower().replace("_","-")]=match.group(2)
    locked={entry.get("name","").lower().replace("_","-"):entry.get("version") for entry in pylock.get("package",[]) if isinstance(entry,dict)}
    if any(locked.get(name)!=version for name,version in expected_py.items()): _fail()
    node_expected={**package.get("dependencies",{}),**package.get("devDependencies",{})}
    for name,version in node_expected.items():
        entry=lock.get("packages",{}).get("node_modules/"+name)
        if not isinstance(entry,dict): _fail()
        if not version.startswith("file:") and entry.get("version")!=version: _fail()
    return {"package":package,"pyproject":pyproject,"python_expected":expected_py,"pins":pins,"artifacts":artifact}


def _hash_set(base:Path,name:str)->tuple[dict[str,str],dict[str,str]]:
    actual,links={},{}
    for path in sorted(base.rglob("*")):
        rel=path.relative_to(base).as_posix()
        if name=="python_stdlib" and "site-packages" in path.parts: continue
        if "__pycache__" in path.parts or path.suffix==".pyc" or rel==".package-lock.json" or path.name in {"RECORD","uv_cache.json","uv_build.json","uv_installer_metadata.json"}: continue
        if path.is_symlink(): links[rel]=os.readlink(path)
        elif path.is_file(): actual[rel]=sha256(path.read_bytes()).hexdigest()
    return actual,links


def _verify_tools(tools: Path, node: Path, python: Path) -> None:
    inputs=_verify_tool_inputs(tools)
    spec=inputs["artifacts"]
    ruff=(tools/".venv/bin/ruff").resolve(strict=True)
    real_python=python.resolve(strict=True)
    if real_python!=(tools/".venv/bin/python").resolve(strict=True): _fail()
    executables={"node":node,"python":real_python,"ruff":ruff}
    for key,path in executables.items():
        if not path.is_absolute() or not path.is_file() or sha256(path.read_bytes()).hexdigest()!=spec["executables"].get(key): _fail()
    python_version=inputs["pins"]["versions"].get("python")
    stdlib=real_python.parents[1]/"lib"/("python"+".".join(python_version.split(".")[:2]))
    bases={"node":tools/"node_modules","python":tools/".venv/lib"/("python"+".".join(python_version.split(".")[:2]))/"site-packages","python_stdlib":stdlib}
    for name,base in bases.items():
        if not base.is_dir(): _fail()
        actual,links=_hash_set(base,name);record=spec["sets"][name]
        if actual!=record["files"] or links!=record["symlinks"]:_fail()
    # Only after executable and complete installed-artifact authentication may
    # Python run. -I -S disables environment, sitecustomize, user site and .pth;
    # the verified site-packages path is inserted explicitly afterward.
    site=bases["python"]
    program="import sys,json;sys.path.insert(0,sys.argv.pop(1));import importlib.metadata as m;print(json.dumps({'python':'.'.join(map(str,sys.version_info[:3])),'packages':{n:m.version(n) for n in sys.argv[1:]}}))"
    env={"PATH":str(real_python.parent),"HOME":str(tools),"LANG":"C.UTF-8","LC_ALL":"C.UTF-8"}
    check=subprocess.run([str(real_python),"-I","-S","-B","-c",program,str(site),*inputs["python_expected"].keys(),"ruff"],env=env,cwd=tools,capture_output=True,text=True,timeout=20)
    if check.returncode:_fail()
    data=json.loads(check.stdout)
    if data.get("python")!=python_version:_fail()
    for name,version in inputs["python_expected"].items():
        if data.get("packages",{}).get(name)!=version:_fail()
    if data.get("packages",{}).get("ruff")!="0.16.2":_fail()
    node_version=subprocess.run([str(node),"--version"],env=env,cwd=tools,capture_output=True,text=True,timeout=20)
    if node_version.returncode or node_version.stdout.strip().lstrip("v")!=inputs["pins"]["versions"].get("node"):_fail()
    for name,version in {**inputs["package"]["dependencies"],**inputs["package"].get("devDependencies",{})}.items():
        if name=="@vuoro/contracts": continue
        package_file=tools/"node_modules"/name/"package.json"
        if not package_file.is_file() or json.loads(package_file.read_bytes()).get("version")!=version:_fail()


def _run(cmd: list[str], *, cwd: Path, env: dict[str,str]) -> None:
    result = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if result.returncode: raise RuntimeError(result.stderr.decode("utf-8", "replace")[-4000:])


def _compile(source: Path, stage: Path, tools: Path, node: Path) -> None:
    env = {"PATH": str(node.parent), "HOME": str(stage), "LANG":"C.UTF-8", "LC_ALL":"C.UTF-8", "NODE_OPTIONS":""}
    config=source.parent/"tspconfig.yaml"
    _run([str(node),str(tools/"node_modules/@typespec/compiler/cmd/tsp.js"),"compile",str(source/"main.tsp"),"--config",str(config),"--output-dir",str(stage/"compiled")],cwd=source,env=env)


def _python_sdk(openapi_file: Path, tree: Path, tools: Path, python: Path, bundle: Mapping[str,Any]) -> None:
    target = tree / "clients/python"
    target.mkdir(parents=True)
    cfgpath = tools / "openapi-python-client.yaml"
    env={"PATH":str((tools/".venv/bin").resolve()),"HOME":str(tree),"LANG":"C.UTF-8","LC_ALL":"C.UTF-8","PYTHONDONTWRITEBYTECODE":"1","PYTHONPYCACHEPREFIX":str(tree/".python-pycache"),"RUFF_CACHE_DIR":str(tree/".ruff_cache")}
    custom=tree/"python-templates";custom.mkdir()
    template=(tools/"python-templates/endpoint_module.py.jinja").read_text()
    nullable=_nullable_status_table(openapi_file,bundle)
    template=template.replace("__NULLABLE_STATUS_TABLE__",repr(nullable))
    (custom/"endpoint_module.py.jinja").write_text(template)
    pyver=json.loads((tools/"toolchain/source-pins.json").read_bytes())["versions"]["python"]
    site=(tools/".venv/lib"/("python"+".".join(pyver.split(".")[:2]))/"site-packages").resolve(strict=True)
    bootstrap="import runpy,sys;site=sys.argv[1];args=sys.argv[2:];sys.path.insert(0,site);sys.argv=args;runpy.run_module(args[0],run_name='__main__')"
    child=[str(python),"-I","-S","-X",f"pycache_prefix={tree/'.python-pycache'}","-c",bootstrap,str(site),"openapi_python_client","generate","--path",str(openapi_file),"--config",str(cfgpath),"--custom-template-path",str(custom),"--output-path",str(target/"vuoro_generated_contract_client"),"--meta","none","--overwrite"]
    _run(child,cwd=tools,env=env)
    shutil.rmtree(custom)
    _render_python_attachment(tree, bundle)


def _nullable_status_table(openapi_file:Path,bundle:Mapping[str,Any])->dict[str,dict[str,list[str]]]:
    from jsonschema import Draft202012Validator
    from referencing import Registry,Resource
    registry=Registry()
    for name,schema in bundle.get("$defs",{}).items():
        if isinstance(schema,Mapping) and isinstance(schema.get("$id"),str): registry=registry.with_resource(schema["$id"],Resource.from_contents(schema))
    openapi=json.loads(openapi_file.read_bytes()); table={}
    for path,item in openapi.get("paths",{}).items():
        for method,op in item.items():
            if method not in _METHODS or not isinstance(op,Mapping): continue
            statuses=[]
            for status,response in op.get("responses",{}).items():
                try:
                    schema=response["content"]["application/json"]["schema"]
                    ref=schema["$ref"]; component=ref.rsplit("/",1)[-1]
                    named=bundle["$defs"][component]
                    if Draft202012Validator(named,registry=registry).is_valid(None): statuses.append(str(status))
                except Exception: continue
            if statuses: table.setdefault(method.upper(),{})[path]=statuses
    return table


def _write_runtime_package(tree: Path, root: Path, bundle: Mapping[str,Any], manifest: Mapping[str,Any]) -> None:
    # Copy exact owner package format/runtime source, excluding caches and tests.
    source=root/"packages/vuoro-contracts/src/vuoro_contracts"
    dest=tree/"clients/python/vuoro_contracts"
    shutil.copytree(source,dest,ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
    (tree/"clients/python/vuoro_contracts/data").mkdir(exist_ok=True)
    (tree/"clients/python/vuoro_contracts/data/schema").mkdir(exist_ok=True)


def _render_python_attachment(tree: Path, bundle: Mapping[str,Any]) -> None:
    import json
    from vuoro_contracts import ContractError
    manifest = json.loads((tree / "operation-manifest.json").read_bytes())
    openapi=json.loads((tree/"openapi/contract.openapi.json").read_bytes())
    models_dir=tree/"clients/python/vuoro_generated_contract_client/models"
    available={}
    for path in models_dir.glob("*.py"):
        try:
            import ast
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node,ast.ClassDef): available[node.name]={"module":path.stem,"class":node.name}
        except Exception: continue
    request_models={}
    for path_item in openapi.get("paths",{}).values():
        for method,operation in path_item.items():
            if method not in _METHODS or not isinstance(operation,Mapping): continue
            name=operation.get("operationId"); body=operation.get("requestBody")
            if body is None: continue
            try: model_name=body["content"]["application/json"]["schema"]["$ref"].rsplit("/",1)[-1]
            except Exception: raise ContractError("manifest") from None
            if name not in manifest["operations"] or model_name not in available: raise ContractError("manifest")
            request_models[name]=available[model_name]
    if set(request_models)!={name for name,op in manifest["operations"].items() if op["request"] is not None}: raise ContractError("manifest")
    native_bindings=_derive_python_bindings(models_dir.parent,openapi,manifest,available,request_models)
    template=(Path(__file__).resolve().parents[2]/"tools/contracts/templates/validated-client.py.template").read_text()
    code=template.replace("__REQUEST_MODELS__",repr(request_models)).replace("__NATIVE_BINDINGS__",repr(native_bindings))
    (tree / "clients/python/vuoro_generated_contract_client/validated_client.py").write_text(code)


def _derive_python_bindings(client_root:Path,openapi:Mapping[str,Any],manifest:Mapping[str,Any],available:Mapping[str,Any],request_models:Mapping[str,Any])->dict[str,Any]:
    import ast
    from vuoro_contracts import ContractError
    expected={(op["method"],op["path"]):name for name,op in manifest["operations"].items()}
    result={}
    for file in (client_root/"api").rglob("*.py"):
        try: tree=ast.parse(file.read_text())
        except Exception: continue
        get_kwargs=next((node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=="_get_kwargs"),None)
        sync=next((node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=="sync_detailed"),None)
        if get_kwargs is None or sync is None: continue
        method=None;route=None;path_args={}
        for node in ast.walk(get_kwargs):
            if not isinstance(node,ast.Call): continue
            if isinstance(node.func,ast.Attribute) and node.func.attr=="format" and isinstance(node.func.value,ast.Constant) and isinstance(node.func.value.value,str):
                route=node.func.value.value
                path_args={kw.arg:_last_name(kw.value) for kw in node.keywords if kw.arg and _last_name(kw.value)}
            if isinstance(node.func,ast.Name) and node.func.id=="dict": pass
        for node in ast.walk(get_kwargs):
            if isinstance(node,ast.keyword) and node.arg=="method" and isinstance(node.value,ast.Constant): method=str(node.value.value).upper()
            if isinstance(node,(ast.Assign,ast.AnnAssign)):
                targets=node.targets if isinstance(node,ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target,ast.Name) and target.id=="method" and isinstance(node.value,ast.Constant): method=str(node.value.value).upper()
                    if isinstance(target,ast.Name) and target.id=="_kwargs" and isinstance(node.value,ast.Dict):
                        for key,value in zip(node.value.keys,node.value.values):
                            if isinstance(key,ast.Constant) and key.value=="method" and isinstance(value,ast.Constant): method=str(value.value).upper()
                            if isinstance(key,ast.Constant) and key.value=="url":
                                for call in ast.walk(value):
                                    if isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and call.func.attr=="format" and isinstance(call.func.value,ast.Constant):
                                        route=call.func.value.value;path_args={kw.arg:_last_name(kw.value) for kw in call.keywords if kw.arg and _last_name(kw.value)}
        if not method or not route: continue
        key=(method,route)
        if key not in expected: continue
        op_name=expected[key]
        bindings={"path":path_args,"query":{},"header":{}}
        local_maps:dict[str,dict[str,str]]={}
        for node in ast.walk(get_kwargs):
            if isinstance(node,(ast.Assign,ast.AnnAssign)):
                targets=node.targets if isinstance(node,ast.Assign) else [node.target]
                value=node.value
                for target in targets:
                    if isinstance(target,ast.Name) and isinstance(value,ast.Dict):
                        local_maps[target.id]={raw:native for key_node,item in zip(value.keys,value.values) if (raw:=_constant_string(key_node)) and (native:=_last_name(item))}
                    if isinstance(target,ast.Name) and isinstance(value,ast.Name) and value.id in local_maps:
                        local_maps[target.id]=dict(local_maps[value.id])
                    if isinstance(target,ast.Subscript) and isinstance(target.value,ast.Name) and isinstance(value,ast.Name):
                        raw=_constant_string(target.slice)
                        if raw: local_maps.setdefault(target.value.id,{})[raw]=value.id
                    if isinstance(target,ast.Name) and target.id=="_kwargs" and isinstance(value,ast.Dict):
                        for key_node,item in zip(value.keys,value.values):
                            if _constant_string(key_node)=="params":
                                if isinstance(item,ast.Dict):
                                    for raw_node,param_value in zip(item.keys,item.values):
                                        raw=_constant_string(raw_node);native=_last_name(param_value)
                                        if raw and native: bindings["query"][raw]=native
                                elif isinstance(item,ast.Name):
                                    bindings["query"].update(local_maps.get(item.id,{}))
            if not isinstance(node,ast.Assign): continue
            for target in node.targets:
                if isinstance(target,ast.Subscript) and isinstance(target.value,ast.Name) and target.value.id=="headers":
                    raw=_constant_string(target.slice);native=_last_name(node.value)
                    if raw and native: bindings["header"][raw]=native
                if isinstance(target,ast.Subscript) and isinstance(target.value,ast.Name) and target.value.id=="_kwargs" and _constant_string(target.slice)=="params":
                    if isinstance(node.value,ast.Dict):
                        for key_node,value in zip(node.value.keys,node.value.values):
                            raw=_constant_string(key_node);native=_last_name(value)
                            if raw and native: bindings["query"][raw]=native
                    elif isinstance(node.value,ast.Name):
                        bindings["query"].update(local_maps.get(node.value.id,{}))
        parammap={}
        for p in manifest["operations"][op_name]["parameters"]:
            native=bindings[p["in"]].get(p["name"])
            valid_names={a.arg for a in sync.args.args+sync.args.kwonlyargs}
            if native not in valid_names: raise ContractError("manifest")
            parammap.setdefault(p["in"],{})[p["name"]]=native
        model=request_models.get(op_name)
        body_parameter=None
        if model is not None:
            imported=any(isinstance(node,ast.ImportFrom) and node.module and node.module.endswith("models."+model["module"]) and any(alias.name==model["class"] for alias in node.names) for node in tree.body)
            annotations=[]
            for arg in get_kwargs.args.args+get_kwargs.args.kwonlyargs:
                if isinstance(arg.annotation,ast.Name) and arg.annotation.id==model["class"]: annotations.append(arg.arg)
            if not imported or len(annotations)!=1 or annotations[0] not in {a.arg for a in sync.args.args+sync.args.kwonlyargs}: raise ContractError("manifest")
            body_parameter=annotations[0]
        module=".".join(file.relative_to(client_root).with_suffix("").parts)
        result[op_name]={"module":module,"parameters":parammap,"model":model,"body_parameter":body_parameter}
    if set(result)!=set(manifest["operations"]): raise ContractError("manifest")
    return result


def _last_name(node:Any)->str|None:
    import ast
    names=[n.id for n in ast.walk(node) if isinstance(n,ast.Name)]
    return names[-1] if names else None


def _constant_string(node:Any)->str|None:
    import ast
    if isinstance(node,ast.Constant) and isinstance(node.value,str): return node.value
    return None




def _typescript_sdk(tree: Path, tools: Path, node: Path, manifest: Mapping[str,Any], bundle: Mapping[str,Any]) -> None:
    out=tree/"clients/typescript"; out.mkdir(parents=True)
    code = (tools / "templates/validated-sdk.ts.template").read_text()
    # Hey API 0.99 selects generated SDK surfaces through its plugin config.
    config=tree/"hey-api.config.json"
    config.write_text(json.dumps({"input":str(next((tree/"openapi").glob("*.json"))),"output":str(out),"plugins":["@hey-api/typescript",{"name":"@hey-api/sdk","client":"@hey-api/client-fetch"},"@hey-api/client-fetch"]}))
    _run([str(node),str(tools/"node_modules/@hey-api/openapi-ts/bin/run.js"),"--file",str(config)],cwd=tools,env={"PATH":str(node.parent),"HOME":str(tree),"NODE_PATH":str(tools/"node_modules"),"NODE_OPTIONS":""})
    config.unlink()
    (out/"strict-json.ts").write_bytes((tools/"templates/strict-json.ts").read_bytes())
    sdk_bindings=_derive_typescript_bindings(out,tools,node,manifest)
    code=code.replace("__MANIFEST__",json.dumps(manifest,separators=(",",":"),ensure_ascii=False)).replace("__BUNDLE__",json.dumps(bundle,separators=(",",":"),ensure_ascii=False)).replace("__SDK_BINDINGS__",json.dumps(sdk_bindings,separators=(",",":"),ensure_ascii=False))
    (out/"validated-sdk.ts").write_text(code)


def _derive_typescript_bindings(out:Path,tools:Path,node:Path,manifest:Mapping[str,Any])->dict[str,str]:
    sdk_file=out/"sdk.gen.ts"
    ts_file=tools/"node_modules/typescript/lib/typescript.js"
    program=("import {readFileSync} from 'node:fs';import {pathToFileURL} from 'node:url';"
             "const [tsPath,sourcePath]=process.argv.slice(1);const tsmod=await import(pathToFileURL(tsPath).href);"
             "const ts=tsmod.default??tsmod;const sf=ts.createSourceFile(sourcePath,readFileSync(sourcePath,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TS);"
             "if(sf.parseDiagnostics.length)process.exit(3);const out=[];"
             "for(const statement of sf.statements){if(!ts.isVariableStatement(statement)||!statement.modifiers?.some(m=>m.kind===ts.SyntaxKind.ExportKeyword))continue;"
             "for(const declaration of statement.declarationList.declarations){if(!ts.isIdentifier(declaration.name)||!declaration.initializer)continue;const routes=[];"
             "const visit=n=>{if(ts.isCallExpression(n)&&ts.isPropertyAccessExpression(n.expression)){for(const arg of n.arguments){if(!ts.isObjectLiteralExpression(arg))continue;"
             "const url=arg.properties.find(p=>ts.isPropertyAssignment(p)&&((ts.isIdentifier(p.name)&&p.name.text==='url')||(ts.isStringLiteral(p.name)&&p.name.text==='url'))&&ts.isStringLiteral(p.initializer));"
             "if(url)routes.push({method:n.expression.name.text.toUpperCase(),path:url.initializer.text});}}ts.forEachChild(n,visit);};visit(declaration.initializer);"
             "if(routes.length)out.push({symbol:declaration.name.text,routes});}}process.stdout.write(JSON.stringify(out));")
    env={"PATH":str(node.parent),"HOME":str(out),"LANG":"C.UTF-8","LC_ALL":"C.UTF-8","NODE_OPTIONS":""}
    parsed=subprocess.run([str(node),"--input-type=module","-e",program,str(ts_file),str(sdk_file)],cwd=tools,env=env,capture_output=True,text=True,timeout=30)
    if parsed.returncode:_fail()
    try: exports=json.loads(parsed.stdout)
    except Exception:_fail()
    expected={(op["method"],op["path"]):name for name,op in manifest["operations"].items()}
    result={};seen_symbols=set()
    if not isinstance(exports,list):_fail()
    for item in exports:
        if not isinstance(item,dict) or set(item)!={"symbol","routes"} or not isinstance(item["symbol"],str) or not isinstance(item["routes"],list):_fail()
        for route in item["routes"]:
            if not isinstance(route,dict) or set(route)!={"method","path"}:_fail()
            key=(route["method"],route["path"])
            if key not in expected:continue
            if len(item["routes"])!=1:_fail()
            name=expected[key]
            if name in result or item["symbol"] in seen_symbols:_fail()
            result[name]=item["symbol"];seen_symbols.add(item["symbol"])
    if set(result)!=set(manifest["operations"]):_fail()
    return result

if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate or verify the isolated EC-00 versioned-counter client artifacts."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
EC00 = ROOT / "tests/experiments/ec00"
CANDIDATES = EC00 / "candidates"
GENERATED = EC00 / "generated"
VERSIONED_TYPESPEC = CANDIDATES / "typespec/versioned-counters.tsp"
VERSIONED_PYTHON_CONFIG = CANDIDATES / "openapi-python-client-v2.yaml"
V1_OPENAPI = GENERATED / "typespec/openapi/ec00-oa-v1.openapi.json"
V1_TITLE = "EC-00 disposable OA candidate"
V2_TITLE = "EC-00 disposable OA candidate v2"
OPENAPI_GO_MODULE_V2 = "example.com/vuoro-ec00/openapi-go-v2"


def load_generator_helpers():
    path = ROOT / "scripts/experiments/ec00/generate_candidates.py"
    spec = importlib.util.spec_from_file_location("ec00_generate_candidates", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load pinned generator helpers from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="replace only generated/typespec-v2 outputs")
    args = parser.parse_args()

    if not VERSIONED_TYPESPEC.is_file() or not VERSIONED_PYTHON_CONFIG.is_file():
        parser.error("versioned-counters.tsp and openapi-python-client-v2.yaml must be present")
    if not any(line.strip() == "package_name_override: ec00_oa_v2_python_client"
               for line in VERSIONED_PYTHON_CONFIG.read_text().splitlines()):
        raise SystemExit("v2 Python generator config must set package_name_override to ec00_oa_v2_python_client")

    helpers = load_generator_helpers()
    helpers.run([sys.executable, str(ROOT / "scripts/experiments/ec00/generate_candidates.py")], cwd=ROOT)

    node_value = os.environ.get("EC00_NODE")
    if not node_value:
        parser.error("EC00_NODE must identify the exact pinned Node binary")
    node = Path(node_value).resolve()
    npm_bin = CANDIDATES / "node_modules/.bin"
    tsp = npm_bin / "tsp"
    openapi_ts = npm_bin / "openapi-ts"
    if not tsp.is_file() or not openapi_ts.is_file():
        raise SystemExit("run npm ci --ignore-scripts --no-audit --no-fund in candidates/ first")

    openapi_generator = shutil.which("openapi-generator-cli")
    if not openapi_generator:
        raise SystemExit("enter the locked candidates/toolchain Nix dev shell first")
    env = dict(os.environ, PATH=f"{node.parent}:{npm_bin}:{os.environ['PATH']}")
    with tempfile.TemporaryDirectory(prefix="ec00-versioned-counter-generation-") as temp:
        out = Path(temp)
        openapi_out = out / "openapi"
        openapi_out.mkdir()
        helpers.run(
            [str(tsp), "compile", str(VERSIONED_TYPESPEC.relative_to(CANDIDATES)),
             "--config", "tspconfig.yaml",
             "--option", f"@typespec/openapi3.emitter-output-dir={openapi_out}",
             "--option", "@typespec/openapi3.output-file={service-name}.openapi.json",
             "--option", "@typespec/openapi3.file-type=json"],
            cwd=CANDIDATES, env=env,
        )

        openapi_documents = sorted(openapi_out.rglob("*.json"))
        if len(openapi_documents) != 2:
            raise SystemExit(f"expected exactly two emitted OpenAPI documents, got {len(openapi_documents)}")
        by_title: dict[str, Path] = {}
        for path in openapi_documents:
            document = json.loads(path.read_text())
            title = document.get("info", {}).get("title")
            if title in by_title:
                raise SystemExit(f"duplicate emitted OpenAPI title: {title}")
            by_title[title] = path
        if set(by_title) != {V1_TITLE, V2_TITLE}:
            raise SystemExit(f"expected OpenAPI titles {V1_TITLE!r} and {V2_TITLE!r}; got {sorted(by_title)}")
        if not V1_OPENAPI.is_file() or by_title[V1_TITLE].read_bytes() != V1_OPENAPI.read_bytes():
            raise SystemExit("versioned source changed the committed v1 OpenAPI document bytes")

        generated_openapi = out / "typespec-v2/openapi"
        generated_openapi.mkdir(parents=True)
        for source in openapi_documents:
            target = generated_openapi / source.name
            if target.exists():
                raise SystemExit(f"duplicate OpenAPI output filename: {target.name}")
            shutil.copyfile(source, target)
        v2_openapi = by_title[V2_TITLE]

        python_root = out / "typespec-v2/clients/python"
        python_package = python_root / "ec00_oa_v2_python_client"
        python_root.mkdir(parents=True)
        helpers.run(
            [sys.executable, "-m", "openapi_python_client", "generate", "--meta", "none",
             "--path", str(v2_openapi), "--output-path", str(python_package),
             "--config", str(VERSIONED_PYTHON_CONFIG), "--overwrite"],
            cwd=CANDIDATES, env=env,
        )
        for path in python_package.rglob("*.py"):
            path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()).rstrip() + "\n")

        typescript_root = out / "typespec-v2/clients/typescript"
        typescript_root.mkdir(parents=True)
        helpers.run(
            [str(openapi_ts), "-i", str(v2_openapi), "-o", str(typescript_root),
             "-c", "@hey-api/client-fetch", "-p", "@hey-api/sdk"],
            cwd=CANDIDATES, env=env,
        )

        go_raw = out / "openapi-go-v2-raw"
        go_selected = out / "typespec-v2/clients/go"
        go_raw.mkdir()
        helpers.run(
            [openapi_generator, "generate", "-i", str(v2_openapi), "-g", "go", "-o", str(go_raw),
             "--global-property", "apiTests=false,modelTests=false,apiDocs=false,modelDocs=false",
             "--additional-properties=packageName=ec00oa,moduleName=" + OPENAPI_GO_MODULE_V2
             + ",hideGenerationTimestamp=true"],
            cwd=CANDIDATES, env=env,
        )
        go_module = go_raw / "go.mod"
        go_module_text = go_module.read_text()
        placeholder = "module github.com/GIT_USER_ID/GIT_REPO_ID"
        if placeholder not in go_module_text:
            raise SystemExit("OpenAPI Generator Go module placeholder changed; review normalization")
        go_module.write_text(go_module_text.replace(placeholder, "module " + OPENAPI_GO_MODULE_V2, 1))
        helpers.selected_tree(go_raw, go_selected, suffixes={".go"}, root_files={"go.mod"},
                              normalize_text=True)

        rust_raw = out / "openapi-rust-v2-raw"
        rust_selected = out / "typespec-v2/clients/rust"
        rust_raw.mkdir()
        helpers.run(
            [openapi_generator, "generate", "-i", str(v2_openapi), "-g", "rust", "-o", str(rust_raw),
             "--global-property", "apiTests=false,modelTests=false,apiDocs=false,modelDocs=false",
             "--additional-properties=packageName=ec00_oa_v2,library=reqwest,hideGenerationTimestamp=true"],
            cwd=CANDIDATES, env=env,
        )
        helpers.selected_tree(rust_raw, rust_selected, suffixes={".rs"}, root_files={"Cargo.toml"},
                              normalize_text=True)

        generated = {
            "typespec-v2/openapi": generated_openapi,
            "typespec-v2/clients/python": python_root,
            "typespec-v2/clients/typescript": typescript_root,
            "typespec-v2/clients/go": go_selected,
            "typespec-v2/clients/rust": rust_selected,
        }
        if args.write:
            for relative, source in generated.items():
                destination = GENERATED / relative
                shutil.rmtree(destination, ignore_errors=True)
                shutil.copytree(source, destination,
                                ignore=shutil.ignore_patterns(".ruff_cache", "__pycache__"))
            print("versioned-counter outputs updated; v1 generated outputs were not modified")
            return 0

        drift = []
        for relative, source in generated.items():
            expected = helpers.tree(source)
            actual_path = GENERATED / relative
            actual = helpers.tree(actual_path) if actual_path.exists() else {}
            if expected != actual:
                drift.append(relative)
        if drift:
            raise SystemExit("versioned-counter output drift: " + ", ".join(drift) + "; rerun with --write")
    print("all versioned-counter outputs match pinned inputs and toolchains")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

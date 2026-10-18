#!/usr/bin/env python3
"""Rebuild or verify disposable EC-00 generated artifacts from pinned sources."""
from __future__ import annotations

import argparse
import hashlib
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
PROTOC_SHA256 = "2c089b2c3fdcac51b420e467f9594ad0176b396998d2b4571f93b9f2ad8ee953"
NODE_SHA256 = "f89c69d163e2686ed04cae19f5680668d4c1c64a072a70c1a6e50842a7a9e433"
GO_VERSION = "go1.26.8"
RUSTC_VERSION_PREFIX = "rustc 1.98.1 "
CARGO_VERSION_PREFIX = "cargo 1.98.0 "
OPENAPI_GENERATOR_VERSION = "7.26.0"
GO_PROTOC_PLUGIN_VERSION = "protoc-gen-go v1.36.12"
GO_GRPC_PLUGIN_VERSION = "protoc-gen-go-grpc 1.6.2"
GO_IMPORT_PREFIX = "example.com/vuoro-ec00/ec00/generated/protobuf/go"
GO_MODULE = "example.com/vuoro-ec00/ec00"
OPENAPI_GO_MODULE = "example.com/vuoro-ec00/openapi-go"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): digest(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and ".ruff_cache" not in path.parts and "__pycache__" not in path.parts
    }


def run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(argv, cwd=cwd, env=env, check=True)


def output(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(argv, cwd=cwd, env=env, text=True).strip()


def selected_tree(source: Path, destination: Path, *, suffixes: set[str], root_files: set[str],
                  normalize_text: bool = False) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if (relative.parent == Path(".") and relative.name in root_files) or path.suffix in suffixes:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if normalize_text:
                # OpenAPI Generator 7.26.0 emits trailing spaces and multiple
                # blank lines at EOF; canonicalize those cosmetic bytes only.
                text = path.read_text()
                target.write_text("\n".join(line.rstrip() for line in text.splitlines()).rstrip() + "\n")
            else:
                shutil.copyfile(path, target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="replace tracked generated outputs")
    parser.add_argument("--protoc", type=Path, default=os.environ.get("EC00_PROTOC"))
    parser.add_argument("--node", type=Path, default=os.environ.get("EC00_NODE"))
    args = parser.parse_args()
    protoc = Path(args.protoc).resolve() if args.protoc else None
    node = Path(args.node).resolve() if args.node else None
    if not protoc or not node:
        parser.error("pass exact EC00_PROTOC and EC00_NODE binaries (see README toolchain receipt)")
    if digest(protoc) != PROTOC_SHA256:
        raise SystemExit("protoc binary digest mismatch")
    if digest(node) != NODE_SHA256:
        raise SystemExit("Node binary digest mismatch")
    if subprocess.check_output([str(protoc), "--version"], text=True).strip() != "libprotoc 36.2":
        raise SystemExit("unexpected protoc version")
    if subprocess.check_output([str(node), "--version"], text=True).strip() != "v24.21.0":
        raise SystemExit("unexpected Node version")
    if sys.version_info[:3] != (3, 13, 16):
        raise SystemExit("run under the pinned Python 3.13.16 from candidates/.python-version")
    try:
        from importlib.metadata import version
        if (version("openapi-python-client") != "0.29.1"
                or version("protobuf") != "7.36.2"
                or version("httpx") != "0.28.1"
                or version("grpcio") != "1.84.0"
                or version("grpcio-tools") != "1.84.0"):
            raise SystemExit("Python generator/runtime version mismatch")
    except Exception as exc:
        raise SystemExit(f"pinned Python tools unavailable: {exc}") from exc

    npm_bin = CANDIDATES / "node_modules/.bin"
    if not (npm_bin / "tsp").exists() or not (npm_bin / "openapi-ts").exists():
        raise SystemExit("run npm ci --ignore-scripts --no-audit --no-fund in candidates/ first")
    expected_npm = {
        "@bufbuild/protoc-gen-es": "2.16.0",
        "@bufbuild/protobuf": "2.16.0",
        "@hey-api/openapi-ts": "0.99.0",
        "@typespec/compiler": "1.17.0",
        "@typespec/openapi3": "1.17.0",
        "tsx": "4.23.13",
        "typescript": "5.9.3",
    }
    lock = json.loads((CANDIDATES / "package-lock.json").read_text())
    for package, expected in expected_npm.items():
        installed = json.loads(
            (CANDIDATES / "node_modules" / package / "package.json").read_text()
        )["version"]
        locked = lock["packages"].get("node_modules/" + package, {}).get("version")
        if installed != expected or locked != expected:
            raise SystemExit(f"npm package version mismatch: {package}: installed={installed}, locked={locked}")
    go = shutil.which("go")
    rustc = shutil.which("rustc")
    cargo = shutil.which("cargo")
    openapi_generator = shutil.which("openapi-generator-cli")
    if not all((go, rustc, cargo, openapi_generator)):
        raise SystemExit("enter the locked candidates/toolchain Nix dev shell first")
    if output([go, "version"], cwd=CANDIDATES) != f"go version {GO_VERSION} linux/amd64":
        raise SystemExit("Go version mismatch; use the locked candidates/toolchain flake")
    if not output([rustc, "--version"], cwd=CANDIDATES).startswith(RUSTC_VERSION_PREFIX):
        raise SystemExit("Rust compiler version mismatch; use the locked candidates/toolchain flake")
    if not output([cargo, "--version"], cwd=CANDIDATES).startswith(CARGO_VERSION_PREFIX):
        raise SystemExit("Cargo version mismatch; use the locked candidates/toolchain flake")
    if output([openapi_generator, "version"], cwd=CANDIDATES) != OPENAPI_GENERATOR_VERSION:
        raise SystemExit("OpenAPI Generator version mismatch; use the locked candidates/toolchain flake")
    env = dict(os.environ, PATH=f"{node.parent}:{npm_bin}:{os.environ['PATH']}")
    with tempfile.TemporaryDirectory(prefix="ec00-generation-") as tmp:
        out = Path(tmp)
        openapi_dir = out / "openapi"
        tsp_py_root = out / "python"
        tsp_py = tsp_py_root / "ec00_oa_python_client"
        tsp_ts = out / "typescript"
        proto_py = out / "protobuf-python"
        proto_ts = out / "protobuf-typescript"
        proto_go = out / "protobuf-go"
        openapi_go_raw = out / "openapi-go-raw"
        openapi_go = out / "openapi-go"
        openapi_rust_raw = out / "openapi-rust-raw"
        openapi_rust = out / "openapi-rust"
        go_bin = out / "go-bin"
        go_bin.mkdir()
        env["GOBIN"] = str(go_bin)
        for directory in (openapi_dir, tsp_py_root, tsp_ts, proto_py, proto_ts, proto_go,
                          openapi_go_raw, openapi_go, openapi_rust_raw, openapi_rust):
            directory.mkdir(parents=True)

        run([str(npm_bin / "tsp"), "compile", "typespec/main.tsp", "--config", "tspconfig.yaml",
             "--option", f"@typespec/openapi3.emitter-output-dir={openapi_dir}",
             "--option", "@typespec/openapi3.output-file=ec00-oa-v1.openapi.json",
             "--option", "@typespec/openapi3.file-type=json"], cwd=CANDIDATES, env=env)
        openapi = openapi_dir / "ec00-oa-v1.openapi.json"
        run([str(npm_bin / "openapi-ts"), "-i", str(openapi), "-o", str(tsp_ts),
             "-c", "@hey-api/client-fetch", "-p", "@hey-api/sdk"], cwd=CANDIDATES, env=env)
        run([sys.executable, "-m", "openapi_python_client", "generate", "--meta", "none",
             "--path", str(openapi), "--output-path", str(tsp_py),
             "--config", str(CANDIDATES / "openapi-python-client.yaml"), "--overwrite"],
            cwd=CANDIDATES, env=env)
        # The pinned Python generator emits whitespace-only lines and extra
        # EOF blanks; normalize these formatting-only bytes in generated .py.
        for path in tsp_py.rglob("*.py"):
            path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()).rstrip() + "\n")
        run([sys.executable, "-m", "grpc_tools.protoc", f"-I{CANDIDATES / 'protobuf'}",
             f"--grpc_python_out={proto_py}", str(CANDIDATES / "protobuf/oa.v1.proto")],
            cwd=CANDIDATES, env=env)
        run([go, "install", "google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.12"],
            cwd=CANDIDATES, env=env)
        run([go, "install", "google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.6.2"],
            cwd=CANDIDATES, env=env)
        if output([str(go_bin / "protoc-gen-go"), "--version"], cwd=CANDIDATES) != GO_PROTOC_PLUGIN_VERSION:
            raise SystemExit("protoc-gen-go version mismatch")
        if output([str(go_bin / "protoc-gen-go-grpc"), "--version"], cwd=CANDIDATES) != GO_GRPC_PLUGIN_VERSION:
            raise SystemExit("protoc-gen-go-grpc version mismatch")
        run([str(protoc), f"-I{CANDIDATES / 'protobuf'}",
             f"-I{protoc.parent.parent / 'include'}", f"--python_out={proto_py}",
             f"--pyi_out={proto_py}", f"--plugin=protoc-gen-es={npm_bin / 'protoc-gen-es'}",
             f"--es_out={proto_ts}", "--es_opt=target=ts", str(CANDIDATES / "protobuf/oa.v1.proto")],
            cwd=CANDIDATES, env=env)
        run([str(protoc), f"-I{CANDIDATES / 'protobuf'}",
             f"-I{protoc.parent.parent / 'include'}", "--experimental_allow_proto3_optional",
             f"--plugin=protoc-gen-go={go_bin / 'protoc-gen-go'}",
             f"--plugin=protoc-gen-go-grpc={go_bin / 'protoc-gen-go-grpc'}",
             f"--go_out={proto_go}", f"--go_opt=module={GO_IMPORT_PREFIX}",
             f"--go-grpc_out={proto_go}", f"--go-grpc_opt=module={GO_IMPORT_PREFIX}",
             str(CANDIDATES / "protobuf/oa.v1.proto")], cwd=CANDIDATES, env=env)
        run([openapi_generator, "generate", "-i", str(openapi), "-g", "go", "-o", str(openapi_go_raw),
             "--global-property", "apiTests=false,modelTests=false,apiDocs=false,modelDocs=false",
             "--additional-properties=packageName=ec00oa,moduleName=" + OPENAPI_GO_MODULE + ",hideGenerationTimestamp=true"],
            cwd=CANDIDATES, env=env)
        go_manifest = openapi_go_raw / "go.mod"
        generated_go_mod = go_manifest.read_text()
        placeholder = "module github.com/GIT_USER_ID/GIT_REPO_ID"
        if placeholder not in generated_go_mod:
            raise SystemExit("OpenAPI Generator Go module placeholder changed; review normalization")
        go_manifest.write_text(generated_go_mod.replace(placeholder, "module " + OPENAPI_GO_MODULE, 1))
        selected_tree(openapi_go_raw, openapi_go, suffixes={".go"}, root_files={"go.mod"},
                      normalize_text=True)
        run([openapi_generator, "generate", "-i", str(openapi), "-g", "rust", "-o", str(openapi_rust_raw),
             "--global-property", "apiTests=false,modelTests=false,apiDocs=false,modelDocs=false",
             "--additional-properties=packageName=ec00_oa,library=reqwest,hideGenerationTimestamp=true"],
            cwd=CANDIDATES, env=env)
        selected_tree(openapi_rust_raw, openapi_rust, suffixes={".rs"}, root_files={"Cargo.toml"},
                      normalize_text=True)
        # protoc-gen-es emits one extra blank line at EOF; normalize this
        # cosmetic artifact so ordinary diff/check tooling remains clean.
        for path in proto_ts.rglob("*.ts"):
            path.write_text(path.read_text().rstrip() + "\n")

        generated = {
            "typespec/openapi": openapi_dir,
            "typespec/clients/python": tsp_py_root,
            "typespec/clients/typescript": tsp_ts,
            "protobuf/python": proto_py,
            "protobuf/typescript": proto_ts,
            "protobuf/go": proto_go,
            "typespec/clients/go": openapi_go,
            "typespec/clients/rust": openapi_rust,
        }
        if args.write:
            for relative, source in generated.items():
                destination = GENERATED / relative
                shutil.rmtree(destination, ignore_errors=True)
                shutil.copytree(source, destination,
                                ignore=shutil.ignore_patterns(".ruff_cache", "__pycache__"))
            run([str(npm_bin / "tsc"), "--project", str(CANDIDATES / "tsconfig.json")],
                cwd=CANDIDATES, env=env)
            print("generated outputs updated")
            return 0

        drift = []
        for relative, source in generated.items():
            expected = tree(source)
            actual_dir = GENERATED / relative
            actual = tree(actual_dir) if actual_dir.exists() else {}
            if expected != actual:
                drift.append(relative)
        if drift:
            raise SystemExit("generated output drift: " + ", ".join(drift) + "; rerun with --write")
        run([str(npm_bin / "tsc"), "--project", str(CANDIDATES / "tsconfig.json")],
            cwd=CANDIDATES, env=env)
    print("all generated outputs match pinned inputs and toolchains")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

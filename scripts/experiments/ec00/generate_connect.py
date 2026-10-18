#!/usr/bin/env python3
"""Generate or verify the isolated EC-00 Python Connect service bindings."""
from __future__ import annotations

import argparse
from importlib.metadata import version
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]
EC00 = ROOT / "tests/experiments/ec00"
CANDIDATES = EC00 / "candidates"
PROTO = CANDIDATES / "protobuf/oa.v1.proto"
GENERATED = EC00 / "generated"
CONNECT_OUTPUT = GENERATED / "connect-python/ec00_connect"
PROTOBUF_PYTHON = GENERATED / "protobuf/python/oa/v1_pb2.py"
EXPECTED_PYTHON = (3, 13, 16)
EXPECTED_PROTOBUF = "7.36.2"
EXPECTED_CONNECTRPC = "0.12.1"


def load_generator_helpers():
    path = ROOT / "scripts/experiments/ec00/generate_candidates.py"
    import importlib.util

    spec = importlib.util.spec_from_file_location("ec00_generate_candidates", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load pinned generator helpers from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="replace only generated/connect-python")
    parser.add_argument("--protoc", type=Path, default=os.environ.get("EC00_PROTOC"))
    args = parser.parse_args()

    if not PROTO.is_file() or not PROTOBUF_PYTHON.is_file():
        parser.error("the EC-00 proto and canonical generated Python protobuf file must be present")
    if sys.version_info[:3] != EXPECTED_PYTHON:
        raise SystemExit("run under the pinned Python 3.13.16 from candidates/.python-version")
    try:
        if (version("protobuf") != EXPECTED_PROTOBUF
                or version("connectrpc") != EXPECTED_CONNECTRPC
                or version("protoc-gen-connectrpc") != EXPECTED_CONNECTRPC):
            raise SystemExit("Python Connect generator/runtime version mismatch")
    except Exception as exc:
        raise SystemExit(f"pinned Python Connect tools unavailable: {exc}") from exc

    helpers = load_generator_helpers()
    protoc = Path(args.protoc).resolve() if args.protoc else None
    if not protoc or not protoc.is_file():
        parser.error("pass EC00_PROTOC (see the pinned candidates/toolchain receipt)")
    if helpers.digest(protoc) != helpers.PROTOC_SHA256:
        raise SystemExit("protoc binary digest mismatch")
    if helpers.output([str(protoc), "--version"], cwd=CANDIDATES) != "libprotoc 36.2":
        raise SystemExit("unexpected protoc version")

    plugin = Path(sys.executable).absolute().with_name("protoc-gen-connectrpc")
    if not plugin.is_file():
        raise SystemExit(f"pinned protoc-gen-connectrpc executable not found beside Python: {plugin}")
    protoc_include = protoc.parent.parent / "include"

    with tempfile.TemporaryDirectory(prefix="ec00-connect-generation-") as temporary:
        temporary_root = Path(temporary)
        output = temporary_root / "ec00_connect"
        output.mkdir()
        helpers.run(
            [str(protoc), f"-I{CANDIDATES / 'protobuf'}", f"-I{protoc_include}",
             f"--plugin=protoc-gen-connectrpc={plugin}", f"--python_out={output}",
             f"--connectrpc_out=protobuf=google:{output}", str(PROTO)],
            cwd=CANDIDATES,
        )

        generated_python = output / "oa/v1_pb2.py"
        if not generated_python.is_file() or generated_python.read_bytes() != PROTOBUF_PYTHON.read_bytes():
            raise SystemExit("Connect generation changed the canonical Python protobuf bytes")

        connect_module = output / "oa_v1_connect.py"
        if not connect_module.is_file():
            raise SystemExit("protoc-gen-connectrpc did not emit oa_v1_connect.py")
        # The pinned plugin emits trailing spaces in type annotations. Normalize
        # whitespace deterministically here; generated modules are never hand-edited.
        connect_module.write_bytes(b"\n".join(line.rstrip(b" \t\r")
                                  for line in connect_module.read_bytes().splitlines()) + b"\n")
        selected = temporary_root / "connect-python"
        shutil.copytree(output, selected)

        if args.write:
            shutil.rmtree(CONNECT_OUTPUT, ignore_errors=True)
            shutil.copytree(selected, CONNECT_OUTPUT)
            print("Connect output updated; protobuf and other generated outputs were not modified")
            return 0

        expected = helpers.tree(selected)
        actual = helpers.tree(CONNECT_OUTPUT) if CONNECT_OUTPUT.exists() else {}
        if expected != actual:
            raise SystemExit("Connect output drift: generated/connect-python; rerun with --write")

    print("Connect output matches pinned proto and toolchain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

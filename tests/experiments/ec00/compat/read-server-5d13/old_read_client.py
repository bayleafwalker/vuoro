#!/usr/bin/env python3
"""Issue frozen 5d13 ListRequests and Watch calls and emit wire bytes as hex."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import site
import sys
import sysconfig

HERE = Path(__file__).resolve().parent
FROZEN_PROTOBUF = HERE / "source/generated/protobuf/python"


def isolate_import_path() -> None:
    roots = []
    configured = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        value = configured.get(key)
        if value:
            roots.append(Path(value).resolve())
    for value in site.getsitepackages():
        roots.append(Path(value).resolve())
    user_site = site.getusersitepackages()
    if isinstance(user_site, str):
        roots.append(Path(user_site).resolve())
    else:
        roots.extend(Path(value).resolve() for value in user_site)

    retained = []
    for entry in sys.path:
        if not entry:
            continue
        path = Path(entry).resolve()
        if any(path == root or root in path.parents for root in roots):
            retained.append(entry)
    sys.path[:] = [str(FROZEN_PROTOBUF), *retained]


isolate_import_path()

import grpc  # noqa: E402
from oa import v1_pb2, v1_pb2_grpc  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("grpc_target")
    parser.add_argument("mode", nargs="?", choices=("default", "legacy-empty-zero"), default="default")
    args = parser.parse_args()

    if args.mode == "default":
        request = v1_pb2.PageRequest(limit=2)
    else:
        request = v1_pb2.PageRequest(cursor="", limit=0)

    channel = grpc.insecure_channel(args.grpc_target)
    try:
        stub = v1_pb2_grpc.OwnerOperationsStub(channel)
        list_page = stub.ListRequests(request, timeout=5)
        watch_page = stub.Watch(request, timeout=5)
    finally:
        channel.close()

    print(json.dumps({
        "requestHex": request.SerializeToString().hex(),
        "listHex": list_page.SerializeToString().hex(),
        "watchHex": watch_page.SerializeToString().hex(),
    }))


if __name__ == "__main__":
    main()

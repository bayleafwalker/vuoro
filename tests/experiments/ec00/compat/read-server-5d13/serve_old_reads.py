#!/usr/bin/env python3
"""Serve the frozen 5d13 native-gRPC read boundary until stdin closes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import site
import sys
import sysconfig

HERE = Path(__file__).resolve().parent
FROZEN_SOURCE = HERE / "source"
FROZEN_PROTOBUF = FROZEN_SOURCE / "generated/protobuf/python"


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
    sys.path[:] = [str(FROZEN_PROTOBUF), str(FROZEN_SOURCE), *retained]


isolate_import_path()

from candidate_owner_http import CandidateOwnerServer  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("principal", nargs="?", default="requester-a")
    args = parser.parse_args()

    with CandidateOwnerServer(args.database, principal_id=args.principal) as server:
        print(json.dumps({"base_url": server.base_url, "grpc_target": server.grpc_target}))
        sys.stdout.flush()
        sys.stdin.readline()


if __name__ == "__main__":
    main()

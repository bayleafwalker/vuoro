"""Start the exact 59fa native-gRPC server for disposable compatibility tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "generated/protobuf/python"))

from candidate_owner_http import CandidateOwnerServer, initialize_fixture


def main() -> None:
    database = Path(sys.argv[1])
    initialize_fixture(database)
    server = CandidateOwnerServer(database)
    server.__enter__()
    try:
        print(json.dumps({"grpc": server.grpc_target}), flush=True)
        sys.stdin.read()
    finally:
        server.__exit__(None, None, None)


if __name__ == "__main__":
    main()

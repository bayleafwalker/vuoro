"""Start the frozen 5ff5d823 disposable server for local compatibility tests."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "generated/protobuf/python"))

from candidate_owner_http import CandidateOwnerServer, initialize_fixture


def main() -> None:
    db_path = Path(sys.argv[1])
    initialize_fixture(db_path)
    server = CandidateOwnerServer(db_path)
    server.__enter__()
    try:
        print(server.base_url, flush=True)
        sys.stdin.read()
    finally:
        server.__exit__(None, None, None)


if __name__ == "__main__":
    main()

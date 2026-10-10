"""Checkoutless local demo command; no live endpoint or profile input."""
from __future__ import annotations
import argparse
import asyncio
from importlib.metadata import version
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile

from .fixture import fixture
from .scenario import scenario, worker


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["_worker"]:
        value = json.load(sys.stdin)
        asyncio.run(worker(value["endpoint"], value["token"], value["item"]))
        return 0
    parser = argparse.ArgumentParser(prog="vuoro")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Run only an owned disposable loopback fixture")
    demo.add_argument("--pg-bin", type=Path)
    demo.add_argument("--receipt", type=Path, required=True)
    demo.add_argument("--omit-required-verification", action="store_true")
    demo.add_argument("--omit-required-check", action="store_true")
    args = parser.parse_args(argv)
    try:
        # Refuse an existing/symlinked output before starting any fixture process.
        fd = os.open(args.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        print("Receipt destination unavailable; demo not started", file=sys.stderr)
        return 1
    receipt = {"schema": "vuoro-settlement-demo/v1", "status": "incomplete",
        "qualification": "owned disposable PG16; scripted native HTTP; loopback evaluation bearer identities",
        "excluded_claims": ["vendor OAuth", "hosted worker", "external effect execution", "database crash durability"],
        "versions": {name: version(name) for name in ("vuoro-client", "vuoro-service", "sprintctl")},
        "steps": []}
    code = 1
    root = None
    fixture_state = {}
    try:
        expected = {"vuoro-client": "0.1.2", "vuoro-service": "0.1.92", "sprintctl": "0.18.0"}
        if receipt["versions"] != expected:
            raise ValueError("install the qualified released owner/client versions")
        root = Path(tempfile.mkdtemp(prefix="vuoro-demo-", dir="/tmp"))
        root.chmod(0o700)
        with fixture(root, args.pg_bin, state=fixture_state) as (endpoint, tokens):
            receipt["fixture"] = {"database": "new owned PG16 cluster",
                "database_transport": "private Unix socket; TCP disabled",
                "runtime_ddl": "denied by actual PostgreSQL",
                "identity_mode": "loopback evaluation bearer; distinct proposer/verifier principals"}
            receipt["result"] = asyncio.run(scenario(endpoint, tokens, root, receipt["steps"],
                omit_verification=args.omit_required_verification,
                omit_check=args.omit_required_check))
        receipt["cleanup"] = "owned shell and PostgreSQL stopped"
        receipt["status"] = "passed"
        code = 0
    except Exception as error:
        # Diagnostic type only: never serialize bearer tokens, DSNs or arbitrary exceptions.
        receipt["failure_type"] = type(error).__name__
        print("Demo incomplete: " + type(error).__name__, file=sys.stderr)
    finally:
        if root is not None:
            if fixture_state.get("cleanup") in ("complete", "not-started"):
                try:
                    shutil.rmtree(root)
                except OSError:
                    receipt["status"] = "incomplete"
                    receipt["scratch_retained"] = str(root)
                    code = 1
            else:
                receipt["status"] = "incomplete"
                receipt["scratch_retained"] = str(root)
                code = 1
    try:
        # Exclusive creation avoids overwriting a prior proof or following a symlink.
        with os.fdopen(fd, "w") as stream:
            json.dump(receipt, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        print("Receipt capture failed; qualification incomplete", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())

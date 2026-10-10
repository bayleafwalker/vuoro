"""Checkoutless local demo command; no live endpoint or profile input."""
from __future__ import annotations
import argparse
import asyncio
from importlib.metadata import version
import json
import os
import shutil
import signal
from pathlib import Path
import sys
import tempfile

from .fixture import fixture
from .scenario import scenario, worker


class DemoInterrupted(Exception):
    """Normal signal requested cancellation, without interrupting cleanup."""


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
    demo.add_argument("--wrong-artifact-digest", action="store_true")
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
        "versions": {},
        "steps": []}
    code = 1
    root = None
    fixture_state = {}
    interrupted = None
    task = None
    loop = None
    original_handlers = {}

    def mark_interruption():
        nonlocal code
        if interrupted is not None:
            receipt["interruption"] = signal.Signals(interrupted).name
            receipt["status"] = "incomplete"
            receipt.setdefault("failure_type", "DemoInterrupted")
            code = 1

    def interrupt(signum, _frame):
        nonlocal interrupted
        # Repeated signals must not cancel an already-unwinding cleanup task.
        if interrupted is None:
            interrupted = signum
            if task is not None and loop is not None and not task.done():
                loop.call_soon_threadsafe(task.cancel)

    async def consume(endpoint, tokens):
        nonlocal task, loop
        task, loop = asyncio.current_task(), asyncio.get_running_loop()
        try:
            if interrupted is not None:
                raise DemoInterrupted()
            return await scenario(endpoint, tokens, root, receipt["steps"],
                omit_verification=args.omit_required_verification,
                omit_check=args.omit_required_check,
                wrong_artifact_digest=args.wrong_artifact_digest)
        finally:
            task = loop = None

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            original_handlers[signum] = signal.signal(signum, interrupt)
        # Broken/missing installed metadata must produce an incomplete receipt.
        receipt["versions"] = {name: version(name) for name in
            ("vuoro-client", "vuoro-service", "sprintctl")}
        expected = {"vuoro-client": "0.1.2", "vuoro-service": "0.1.92", "sprintctl": "0.18.0"}
        if receipt["versions"] != expected:
            raise ValueError("install the qualified released owner/client versions")
        root = Path(tempfile.mkdtemp(prefix="vuoro-demo-", dir="/tmp"))
        root.chmod(0o700)
        marker = root / "owner.json"
        owner = {"schema": "vuoro-demo-owned-fixture/v1", "pid": os.getpid(),
            "receipt": str(args.receipt.absolute()), "phase": "prepared"}
        marker.write_text(json.dumps(owner) + "\n")
        marker.chmod(0o600)
        if interrupted is not None:
            fixture_state["cleanup"] = "not-started"
            raise DemoInterrupted()
        with fixture(root, args.pg_bin, state=fixture_state) as (endpoint, tokens):
            owner["phase"] = "running"
            marker.write_text(json.dumps(owner) + "\n")
            receipt["fixture"] = {"database": "new owned PG16 cluster",
                "database_transport": "private Unix socket; TCP disabled",
                "runtime_ddl": "denied by actual PostgreSQL",
                "identity_mode": "loopback evaluation bearer; distinct proposer/verifier principals"}
            receipt["result"] = asyncio.run(consume(endpoint, tokens))
        receipt["cleanup"] = "owned shell and PostgreSQL stopped"
        if interrupted is not None:
            raise DemoInterrupted()
        receipt["status"] = "passed"
        code = 0
    except (Exception, asyncio.CancelledError) as error:
        # Diagnostic type only: never serialize bearer tokens, DSNs or arbitrary exceptions.
        receipt["failure_type"] = type(error).__name__
        print("Demo incomplete: " + type(error).__name__, file=sys.stderr)
    finally:
        mark_interruption()
        if root is not None:
            if fixture_state.get("cleanup") in ("complete", "not-started"):
                receipt["fixture_cleanup"] = fixture_state["cleanup"]
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
        # A signal during synchronous cleanup records interruption at this boundary.
        mark_interruption()
        # Exclusive creation avoids overwriting a prior proof or following a symlink.
        with os.fdopen(fd, "w") as stream:
            json.dump(receipt, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            if interrupted is not None:
                # Also capture a first signal received during receipt serialization.
                mark_interruption()
                stream.seek(0)
                stream.truncate()
                json.dump(receipt, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
    except OSError:
        print("Receipt capture failed; qualification incomplete", file=sys.stderr)
        return 1
    finally:
        for signum, previous in original_handlers.items():
            signal.signal(signum, previous)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

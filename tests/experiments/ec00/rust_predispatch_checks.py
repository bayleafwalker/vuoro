"""Actual generated Rust SDK entry before cancellation, with independent owner rows."""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from urllib.parse import urlsplit

from lifecycle_slice_checks import HERE
from versioned_counter_checks import initialize_counter, snapshot
from versioned_counter_http import VersionedCounterServer


@functools.lru_cache(maxsize=1)
def command():
    cargo = os.environ.get("EC00_CARGO")
    if not cargo:
        raise AssertionError("use the pinned EC00 Rust toolchain")
    candidates = HERE / "candidates"
    result = subprocess.run(
        [cargo, "build", "--offline", "--quiet", "--locked", "--manifest-path",
         str(candidates / "rust-client/Cargo.toml"), "--bin", "predispatch"],
        cwd=HERE, capture_output=True, text=True, timeout=300,
    )
    if result.returncode:
        raise AssertionError(result.stderr[-5000:])
    target = Path(os.environ.get("CARGO_TARGET_DIR", candidates / "rust-client/target"))
    return [str(target / "debug/predispatch")]


class RustPredispatchTests(unittest.TestCase):
    def assert_sdk_entered(self, result):
        self.assertEqual(1, result["resolverEntered"], "an unpolled future did not enter the SDK transport")
        self.assertEqual(1, result["resolverFinished"])

    def test_generated_sdk_cancellation_and_deadline_before_owner_dispatch(self):
        for mode in ("cancel", "deadline", "control", "unpolled"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix="ec00-rust-predispatch-") as directory:
                path = Path(directory) / "owner.sqlite"
                initialize_counter(path, 1)
                before = snapshot(path)
                with VersionedCounterServer(path) as server:
                    calls = []
                    original = server.httpd.owner_call

                    def observed(operation, *args, **kwargs):
                        calls.append(operation)
                        return original(operation, *args, **kwargs)

                    server.httpd.owner_call = observed
                    result = subprocess.run(
                        command(), cwd=HERE, capture_output=True, text=True, timeout=15,
                        env=os.environ | {"EC00_PREDISPATCH_MODE": mode,
                            "EC00_PREDISPATCH_PORT": str(urlsplit(server.base_url).port)},
                    )
                    self.assertEqual(0, result.returncode, result.stderr[-5000:])
                    row = json.loads(result.stdout)
                    # The process releases its resolver before exit. Wait for
                    # any accepted fixture connection to finish as well.
                    deadline = time.monotonic() + 3
                    with server.httpd.transport_condition:
                        while server.httpd.transport_active:
                            remaining = deadline - time.monotonic()
                            self.assertGreater(remaining, 0, "fixture connection did not settle")
                            server.httpd.transport_condition.wait(remaining)
                    after = snapshot(path)
                    if mode == "control":
                        self.assert_sdk_entered(row)
                        self.assertEqual("2", row["revision"])
                        self.assertEqual(["revise"], calls)
                        self.assertEqual(1, len(after["event"]) - len(before["event"]))
                        self.assertEqual(1, len(after["idem"]) - len(before["idem"]))
                        # Prove the no-effect oracle detects an actual admitted
                        # generated call if cancellation is removed.
                        with self.assertRaises(AssertionError):
                            self.assertEqual(before, after)
                    else:
                        self.assertEqual([], calls)
                        self.assertEqual(before, after)
                        self.assertIsNone(row["revision"])
                        if mode == "unpolled":
                            self.assertEqual((0, 0), (row["resolverEntered"], row["resolverFinished"]))
                            with self.assertRaises(AssertionError):
                                self.assert_sdk_entered(row)
                        else:
                            self.assert_sdk_entered(row)


if __name__ == "__main__":
    unittest.main()

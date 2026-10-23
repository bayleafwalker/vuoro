"""Go/Rust generated v2 counter clients, run in the locked EC00 toolchain."""
import functools
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from lifecycle_slice_checks import HERE
from versioned_counter_checks import CounterAssertions, COUNTERS


@functools.lru_cache(maxsize=None)
def native_command(arm):
    """Compile once using only explicitly pinned runtimes; absence is a failure."""
    if arm not in ("typescript", "go", "rust"):
        raise AssertionError(f"unknown native language: {arm}")
    candidates = HERE / "candidates"
    if arm == "typescript":
        node = os.environ.get("EC00_NODE")
        if not node:
            raise AssertionError("use the pinned EC00 Node toolchain")
        return [node, str(candidates / "node_modules/tsx/dist/cli.mjs"),
                str(candidates / "run_versioned_counter_client.ts")]
    if arm == "go":
        tool = os.environ.get("EC00_GO")
        if not tool:
            raise AssertionError("use the pinned EC00 Go toolchain")
        directory = tempfile.mkdtemp(prefix="ec00-native-go-")
        command = [tool, "build", "-buildvcs=false", "-o", str(Path(directory) / "counter-v2"), "./candidates/go-client-v2"]
        binary = str(Path(directory) / "counter-v2")
    else:
        tool = os.environ.get("EC00_CARGO")
        if not tool:
            raise AssertionError("use the pinned EC00 Rust toolchain")
        command = [tool, "build", "--offline", "--quiet", "--locked", "--manifest-path",
                   str(candidates / "rust-client/Cargo.toml"), "--bin", "counter_v2"]
        target = Path(os.environ.get("CARGO_TARGET_DIR", candidates / "rust-client/target"))
        binary = str(target / "debug/counter_v2")
    result = subprocess.run(command, cwd=HERE, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise AssertionError(result.stderr[-5000:])
    return [binary]


def assert_native_failure(test, arm, failure, result):
    test.assertEqual(0, result["status"], result)
    error = result.get("error", "")
    test.assertTrue(error, result)
    expected = {
        ("typescript", "cancel"): "AbortError", ("typescript", "deadline"): "TimeoutError",
        ("go", "cancel"): "context canceled", ("go", "deadline"): "context deadline exceeded",
        ("rust", "cancel"): "cancelled-", ("rust", "deadline"): "deadline-elapsed-",
    }.get((arm, failure))
    if expected:
        test.assertIn(expected, error, result)
    elif arm == "typescript":
        test.assertEqual("TypeError", error, result)  # Native fetch response loss.
    elif arm == "go":
        test.assertIn("EOF", error, result)
    else:
        test.assertIn("Reqwest", error, result)


class NativeRunner:
    """A fresh native process reads its key/cursor from a persisted JSON file."""

    def __init__(self, arm, base_url, state_path):
        self.arm, self.base_url, self.state_path = arm, base_url, state_path

    def start(self, mode, failure="", predispatch=False):
        environment = {**os.environ, "EC00_BASE_URL": self.base_url,
            "EC00_NATIVE_MODE": mode, "EC00_NATIVE_STATE": str(self.state_path),
            "EC00_NATIVE_FAILURE": failure, "EC00_NATIVE_PREDISPATCH": str(int(predispatch))}
        return subprocess.Popen(native_command(self.arm), cwd=HERE, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    @staticmethod
    def finish(process, cancel=False):
        try:
            output, error = process.communicate("c" if cancel else None, timeout=15)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            _, error = process.communicate()
            raise AssertionError(f"native runner timed out: {error[-5000:]}") from exc
        if process.returncode:
            raise AssertionError(error[-5000:])
        try:
            return json.loads(output)
        except ValueError as exc:
            raise AssertionError(f"native runner did not produce evidence: {output!r} {error}") from exc

    def run(self, mode, failure="", predispatch=False):
        return self.finish(self.start(mode, failure, predispatch))


def write_native_state(path, **changes):
    state = {"key": "q2-native", "requestId": "9007199254741199",
             "expectedRevision": "1", "objective": "native bounded effect", "cursor": None}
    if path.exists():
        state.update(json.loads(path.read_text()))
    state.update(changes)
    path.write_text(json.dumps(state, sort_keys=True))
    return state


class VersionedNativeTests(CounterAssertions, unittest.TestCase):
    def test_generated_go_and_rust_exact_requests_results_and_replay(self):
        for arm in ("go", "rust"):
            for expected in COUNTERS:
                with self.subTest(arm=arm, expected=expected):
                    self.counter_case(arm, expected)


if __name__ == "__main__":
    unittest.main()

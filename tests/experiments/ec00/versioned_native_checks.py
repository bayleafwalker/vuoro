"""Go/Rust generated v2 counter clients, run in the locked EC00 toolchain."""
import unittest
from versioned_counter_checks import CounterAssertions, COUNTERS


class VersionedNativeTests(CounterAssertions, unittest.TestCase):
    def test_generated_go_and_rust_exact_requests_results_and_replay(self):
        for arm in ("go", "rust"):
            for expected in COUNTERS:
                with self.subTest(arm=arm, expected=expected):
                    self.counter_case(arm, expected)


if __name__ == "__main__":
    unittest.main()

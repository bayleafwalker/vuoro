#!/usr/bin/env python3
"""Run one review round's verification sequence, in the order that works.

Ported from actionq (``verification/run_round_checks.py``), where four W3
review rounds recorded the same mechanical failure: the suite was run, it
failed on stale derived artifacts, those were regenerated, and the suite was
run again -- four for four, no judgment involved. The fix there was ordering
alone, and the ordering is the part worth carrying.

Vuoro has no reachability manifest and no action-resource packets, so the
sequence is shorter. It is not, however, the same shape reversed: **the built
wheels are inputs to this repository's suite, not a trailing artifact of it.**
``tests/test_distribution_boundaries.py`` opens ``dist/<distribution>/*.whl``
and asserts each directory holds exactly one, so a source change that is never
rebuilt is tested against a stale wheel, and a version bump leaves two wheels
and fails the suite on an assertion about packaging rather than about the
change. That is precisely actionq's stale-derived-artifact class wearing
different clothes, which is why the build runs *first* here:

    1. wheels  -- rebuild the four distributions the suite reads, pruning
                  superseded wheels from each output directory so the
                  exactly-one invariant the suite asserts stays true by
                  construction
    2. gate    -- the falsifier-coverage gate, before the suite because it is
                  cheap and because a claim broadened past its test should be
                  the first thing a round reports, not the last
    3. suite   -- run twice, because a single green run has never been the bar

Ordering is the whole point, so the steps are not individually selectable.
``--dry-run`` reports what is stale without changing anything, which is what a
read-only reviewer wants; ``--skip-wheels`` exists only because the builds are
the slowest step and add nothing when iterating on documents.

``dist/`` is gitignored, so pruning a superseded wheel destroys nothing that is
not rebuildable by step 1 itself.

Exit status is 0 only if every step passed. A step that refreshed a derived
artifact is reported but is not itself a failure -- that is the sequence doing
its job.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = "tests/test_falsifier_coverage.py"
# The distributions tests/test_distribution_boundaries.py reads back out of
# dist/. A package absent from this tuple is not built here, because a wheel
# nothing reads cannot go stale in a way the suite can see.
BUILT_DISTRIBUTIONS = (
    "vuoro-client",
    "vuoro-service",
    "vuoro-schema-runtime",
    "vuoro-adapter-kit",
)


def _expected_wheel_version(distribution: str) -> str:
    pyproject = ROOT / "packages" / distribution / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def _wheels(distribution: str) -> list[Path]:
    return sorted((ROOT / "dist" / distribution).glob("*.whl"))


def _stale(distribution: str) -> list[str]:
    """What is wrong with this distribution's output directory right now.

    Reported per distribution rather than as one boolean so that ``--dry-run``
    can say *which* invariant a reviewer's checkout is violating: no wheel at
    all, a wheel for a version the sources no longer carry, or two wheels where
    the suite asserts one.
    """
    version = _expected_wheel_version(distribution)
    present = _wheels(distribution)
    if not present:
        return [f"~ {distribution}: no built wheel; suite reads dist/{distribution}/*.whl"]
    notes = []
    if not any(f"-{version}-" in wheel.name for wheel in present):
        notes.append(f"~ {distribution}: built wheel is not version {version}")
    if len(present) > 1:
        notes.append(
            f"~ {distribution}: {len(present)} wheels present, suite asserts exactly one"
        )
    return notes


def _build(distribution: str) -> tuple[bool, list[str]]:
    """Build one wheel and leave its directory holding only that wheel."""
    out_dir = ROOT / "dist" / distribution
    completed = subprocess.run(
        ["uv", "build", "--package", distribution, "--wheel", "--out-dir", str(out_dir)],
        cwd=ROOT,
    )
    if completed.returncode != 0:
        return False, [f"! {distribution}: wheel build failed"]
    version = _expected_wheel_version(distribution)
    current = [wheel for wheel in _wheels(distribution) if f"-{version}-" in wheel.name]
    if len(current) != 1:
        # Two wheels for one version means a local-version or build-tag suffix
        # this script does not model; deleting the wrong one would be a guess.
        return False, [
            f"! {distribution}: {len(current)} wheels match version {version}; "
            "resolve dist/ by hand"
        ]
    notes = [f"= {distribution}: {current[0].name}"]
    for wheel in _wheels(distribution):
        if wheel != current[0]:
            wheel.unlink()
            notes.append(f"~ {distribution}: pruned superseded {wheel.name}")
    return True, notes


def _run(command: list[str], *, label: str) -> bool:
    print(f"--- {label}", flush=True)
    return subprocess.run(command, cwd=ROOT).returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="report stale derived artifacts without changing anything")
    parser.add_argument("--skip-wheels", action="store_true",
                        help="skip the wheel builds when iterating on documents")
    parser.add_argument("--pytest", default="uv run --frozen pytest",
                        help="pytest invocation (default: %(default)s)")
    arguments = parser.parse_args(argv)

    if arguments.dry_run:
        print("--- wheels")
        notes = [note for distribution in BUILT_DISTRIBUTIONS
                 for note in _stale(distribution)]
        for note in notes or ["= already in line"]:
            print(f"  {note}")
        print(f"\ndry run: {len(notes)} stale wheel condition(s); gate and suite not run")
        return 0

    if not arguments.skip_wheels:
        print("--- wheels")
        for distribution in BUILT_DISTRIBUTIONS:
            built, notes = _build(distribution)
            for note in notes:
                print(f"  {note}")
            if not built:
                print("\nFAILED: wheel build.")
                return 1

    pytest = arguments.pytest.split()
    if not _run(pytest + ["-q", GATE], label="falsifier-coverage gate"):
        print("\nFAILED: falsifier-coverage gate.")
        return 1

    for attempt in (1, 2):
        if not _run(pytest + ["-q"], label=f"suite (run {attempt} of 2)"):
            print(f"\nFAILED: suite run {attempt}.")
            return 1

    print("\nOK: wheels rebuilt before the suite, gate green, suite green twice"
          + (" (wheels skipped)" if arguments.skip_wheels else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

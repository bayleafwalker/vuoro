"""Attest that the installed distributions are exactly the pinned composition.

A build log showing the right wheels being fetched is not evidence that they are
what ended up installed: a later resolution step can quietly replace a pinned
distribution with a registry copy. This runs after installation, compares the
live installed metadata against the manifest, and writes the result into the
image so the running container can be asked what it actually contains.
"""

from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import sys


def _pinned(manifest: dict) -> list[dict]:
    entries: list[dict] = []
    for adapter in manifest["adapters"]:
        entries.append(
            {
                "domain": adapter["domain"],
                "role": "adapter",
                "distribution": adapter["distribution"],
                "expected_version": adapter["distribution_version"],
                "artifact_url": adapter["artifact_url"],
                "artifact_sha256": adapter["artifact_sha256"],
                "source_repository": adapter["source_repository"],
                "source_revision": adapter["source_revision"],
            }
        )
        for dependency in adapter.get("dependencies", ()):
            entries.append(
                {
                    "domain": adapter["domain"],
                    "role": "dependency",
                    "distribution": dependency["distribution"],
                    "expected_version": dependency["distribution_version"],
                    "artifact_url": dependency["artifact_url"],
                    "artifact_sha256": dependency["artifact_sha256"],
                    "source_repository": adapter["source_repository"],
                    "source_revision": adapter["source_revision"],
                }
            )
    return entries


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 3:
        raise SystemExit(
            "usage: attest_installed_composition.py MANIFEST WHEEL_DIR OUTPUT"
        )
    manifest_path, wheel_dir, output = map(Path, argv)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    failures: list[str] = []
    attested: list[dict] = []
    for entry in _pinned(manifest):
        record = dict(entry)

        wheel = wheel_dir / entry["artifact_url"].rsplit("/", 1)[-1]
        try:
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        except OSError:
            digest = None
        record["wheel_present"] = digest is not None
        if digest != entry["artifact_sha256"]:
            failures.append(
                f"{entry['distribution']}: wheelhouse artifact missing or checksum mismatch"
            )

        try:
            installed = version(entry["distribution"])
        except PackageNotFoundError:
            installed = None
        record["installed_version"] = installed
        if installed != entry["expected_version"]:
            failures.append(
                f"{entry['distribution']}: installed {installed!r}, "
                f"composition pins {entry['expected_version']!r}"
            )
        attested.append(record)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema_version": "vuoro-installed-composition/v1",
                "verified": not failures,
                "distributions": attested,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    if failures:
        for failure in failures:
            print(f"composition attestation failed: {failure}", file=sys.stderr)
        return 1
    print(f"composition attestation passed for {len(attested)} pinned distributions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

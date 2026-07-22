"""Fetch and verify the immutable adapter wheels named by a composition manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from urllib.request import urlopen


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 2:
        raise SystemExit("usage: fetch_pinned_adapters.py MANIFEST DESTINATION")
    manifest_path, destination = map(Path, argv)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    destination.mkdir(parents=True, exist_ok=True)
    for adapter in manifest["adapters"]:
        url = adapter["artifact_url"]
        target = destination / url.rsplit("/", 1)[-1]
        with urlopen(url) as response:  # noqa: S310 - manifest allows only GitHub URLs
            payload = response.read()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != adapter["artifact_sha256"]:
            raise SystemExit(f"checksum mismatch for {adapter['domain']}")
        target.write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

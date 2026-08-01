"""Fetch and verify the immutable adapter wheels named by a composition manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from urllib.request import urlopen


_ARTIFACT_FIELDS = {
    "source_repository", "source_revision", "artifact_url", "artifact_sha256",
    "distribution", "distribution_version",
}
_RELEASE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def _release_wheel_filename(source_repository: str, artifact_url: str) -> str:
    try:
        source = urlsplit(source_repository)
        artifact = urlsplit(artifact_url)
    except ValueError as error:
        raise SystemExit("malformed release URL") from error
    for value, parsed in ((source_repository, source), (artifact_url, artifact)):
        if (parsed.scheme != "https" or parsed.hostname != "github.com"
                or parsed.netloc != "github.com" or parsed.query or parsed.fragment
                or "%" in value):
            raise SystemExit("release artifacts require canonical GitHub HTTPS URLs")
    source_parts = source.path.strip("/").split("/")
    artifact_parts = artifact.path.strip("/").split("/")
    if (len(source_parts) != 2 or len(artifact_parts) != 6
            or artifact_parts[:2] != source_parts
            or artifact_parts[2:4] != ["releases", "download"]
            or any(not _RELEASE_SEGMENT.fullmatch(part) for part in source_parts)
            or not _RELEASE_SEGMENT.fullmatch(artifact_parts[4])
            or artifact_parts[4] in {".", ".."}
            or not _RELEASE_SEGMENT.fullmatch(artifact_parts[5])
            or not artifact_parts[5].endswith(".whl")):
        raise SystemExit("artifact URL must identify one canonical GitHub release wheel")
    return artifact_parts[5]


def artifact_pins(manifest: object) -> list[tuple[str, dict[str, str]]]:
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "adapters"}:
        raise SystemExit("invalid composition manifest")
    if manifest["schema_version"] != "vuoro-composition/v1":
        raise SystemExit("unsupported composition schema_version")
    adapters = manifest["adapters"]
    if not isinstance(adapters, list):
        raise SystemExit("composition adapters must be an array")
    pins: list[tuple[str, dict[str, str]]] = []
    seen_distributions: set[str] = set()
    seen_filenames: set[str] = set()
    for adapter in adapters:
        if not isinstance(adapter, dict):
            raise SystemExit("adapter pins must be objects")
        dependencies = adapter.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise SystemExit("adapter dependencies must be an array")
        for label, pin in [(str(adapter.get("domain", "adapter")), adapter), *[("dependency", item) for item in dependencies]]:
            if not isinstance(pin, dict):
                raise SystemExit("dependency pins must be objects")
            if label == "dependency" and set(pin) != _ARTIFACT_FIELDS:
                raise SystemExit("dependency pin fields do not match the v1 contract")
            if not _ARTIFACT_FIELDS <= set(pin) or not all(isinstance(pin[key], str) and pin[key] for key in _ARTIFACT_FIELDS):
                raise SystemExit(f"invalid artifact pin for {label}")
            if not re.fullmatch(r"[0-9a-f]{40}", pin["source_revision"]):
                raise SystemExit(f"invalid source revision for {label}")
            if not re.fullmatch(r"[0-9a-f]{64}", pin["artifact_sha256"]):
                raise SystemExit(f"invalid artifact digest for {label}")
            if label == "dependency" and (
                pin["source_repository"] != adapter.get("source_repository")
                or pin["source_revision"] != adapter.get("source_revision")
            ):
                raise SystemExit("dependency must come from the adapter owner revision")
            distribution = pin["distribution"]
            filename = _release_wheel_filename(pin["source_repository"], pin["artifact_url"])
            if distribution in seen_distributions:
                raise SystemExit(f"duplicate distribution: {distribution}")
            if filename in seen_filenames:
                raise SystemExit(f"artifact filename collision: {filename}")
            seen_distributions.add(distribution)
            seen_filenames.add(filename)
            pins.append((label, pin))
    return pins


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 2:
        raise SystemExit("usage: fetch_pinned_adapters.py MANIFEST DESTINATION")
    manifest_path, destination = map(Path, argv)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    destination.mkdir(parents=True, exist_ok=True)
    for label, adapter in artifact_pins(manifest):
        url = adapter["artifact_url"]
        target = destination / _release_wheel_filename(
            adapter["source_repository"], adapter["artifact_url"]
        )
        with urlopen(url) as response:  # noqa: S310 - manifest allows only GitHub URLs
            payload = response.read()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != adapter["artifact_sha256"]:
            raise SystemExit(f"checksum mismatch for {label}")
        if target.exists():
            existing = hashlib.sha256(target.read_bytes()).hexdigest()
            if existing != digest:
                raise SystemExit(f"existing artifact mismatch for {target.name}")
        else:
            target.write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

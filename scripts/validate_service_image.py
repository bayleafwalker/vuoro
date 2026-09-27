"""Validate what the service image actually installed, from inside the image.

The 0.1.76 release found the image did not build: `vuoro-mcp-edge` needs the
workspace package `vuoro-evidence`, which was not installed from the local
tree, so pip went to the index for it. That name is unregistered on PyPI, and
anyone could register it. A workspace name that resolves from an index is a
dependency-confusion hole, whether the build then fails or quietly succeeds.

Two modes, both stdlib-only so the second can run in the slim image:

``workspace-names``
    Run on the host from the repository root. Prints the workspace package
    names from ``packages/*/pyproject.toml``, one per line.

``check NAME...``
    Run inside the image, e.g.
    ``docker run -i --rm --entrypoint python IMAGE - check NAME... < this``.
    It fails unless:

    * vuoro-service, vuoro-mcp-edge and vuoro-evidence are installed from
      their local source directories under /srv/vuoro/packages;
    * every installed distribution named like a workspace package came from
      a local file (a directory or a wheel on disk) or a hash-pinned release
      asset of this repository (an adapter's `name @ https://github.com/
      bayleafwalker/vuoro/releases/...#sha256=` requirement), never from
      an index. pip records a `direct_url.json` for both of those and none for
      an index resolution;
    * the installed-composition attestation the build wrote exists and
      says ``verified``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from importlib.metadata import distributions
from pathlib import Path
from urllib.parse import unquote, urlparse

LOCAL_SOURCE_PACKAGES = ("vuoro-service", "vuoro-mcp-edge", "vuoro-evidence")
SOURCE_ROOT = Path("/srv/vuoro/packages")
ATTESTATION_SCHEMA = "vuoro-installed-composition/v1"
RELEASE_ASSET_PREFIX = "/bayleafwalker/vuoro/releases/download/"


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def workspace_names(root: Path) -> list[str]:
    import tomllib

    names = []
    for pyproject in sorted(root.glob("packages/*/pyproject.toml")):
        with pyproject.open("rb") as handle:
            names.append(tomllib.load(handle)["project"]["name"])
    if not names:
        raise SystemExit(f"no workspace packages found under {root}/packages")
    return names


def _origin(dist) -> dict | None:
    text = dist.read_text("direct_url.json")
    return json.loads(text) if text else None


def _local_path(origin: dict | None) -> Path | None:
    if not origin:
        return None
    url = urlparse(origin.get("url", ""))
    if url.scheme != "file":
        return None
    return Path(unquote(url.path))


def _hash_pinned(origin: dict) -> bool:
    # Only this repository's own release assets: the adapters pin shared
    # wheels as `name @ https://github.com/bayleafwalker/vuoro/releases/...
    # #sha256=...`, and pip records the hash only when the requirement
    # carried one.
    url = urlparse(origin.get("url", ""))
    hashes = (origin.get("archive_info") or {}).get("hashes") or {}
    return (
        url.scheme == "https"
        and url.netloc == "github.com"
        and url.path.startswith(RELEASE_ASSET_PREFIX)
        and bool(hashes.get("sha256"))
    )


def check(names: list[str]) -> list[str]:
    failures: list[str] = []
    wanted = {canonical(name) for name in names}
    missing_sources = set(LOCAL_SOURCE_PACKAGES) - {canonical(n) for n in names}
    if missing_sources:
        failures.append(
            f"workspace name list lacks {sorted(missing_sources)}; pass every "
            "packages/*/pyproject.toml name"
        )

    installed: dict[str, object] = {}
    for dist in distributions():
        name = canonical(dist.metadata["Name"])
        if name in installed:
            failures.append(f"{name}: installed more than once")
        installed[name] = dist

    for name in LOCAL_SOURCE_PACKAGES:
        dist = installed.get(name)
        if dist is None:
            failures.append(f"{name}: not installed")
            continue
        origin = _origin(dist)
        path = _local_path(origin)
        expected = SOURCE_ROOT / name
        if path is None or "dir_info" not in origin or path != expected:
            failures.append(
                f"{name}: not installed from the local source tree {expected} "
                f"(direct_url: {origin!r})"
            )

    for name in sorted(wanted & installed.keys()):
        origin = _origin(installed[name])
        if origin is None:
            failures.append(
                f"{name}: workspace package resolved from an index "
                "(dependency-confusion risk)"
            )
        elif _local_path(origin) is None and not _hash_pinned(origin):
            failures.append(
                f"{name}: workspace package from an unpinned remote URL "
                f"(direct_url: {origin!r})"
            )

    attestation_path = Path(
        os.environ.get(
            "VUORO_INSTALLED_COMPOSITION_PATH",
            "/opt/vuoro/composition/installed-composition.json",
        )
    )
    try:
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        failures.append(f"installed-composition attestation unreadable: {error}")
    else:
        if attestation.get("schema_version") != ATTESTATION_SCHEMA:
            failures.append("installed-composition attestation has the wrong schema")
        if attestation.get("verified") is not True:
            failures.append("installed-composition attestation is not verified")
        if not attestation.get("distributions"):
            failures.append("installed-composition attestation lists no distributions")
    return failures


def main(argv: list[str]) -> int:
    if argv[:1] == ["workspace-names"] and len(argv) == 1:
        print("\n".join(workspace_names(Path.cwd())))
        return 0
    if argv[:1] == ["check"] and len(argv) > 1:
        failures = check(argv[1:])
        for failure in failures:
            print(f"service image check failed: {failure}", file=sys.stderr)
        if failures:
            return 1
        print(
            "service image check passed: "
            + ", ".join(LOCAL_SOURCE_PACKAGES)
            + " from local sources; no workspace name from an index; "
            "composition attested"
        )
        return 0
    raise SystemExit(
        "usage: validate_service_image.py workspace-names | check NAME..."
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

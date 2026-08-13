"""Validate immutable Python release metadata and built-wheel identity."""

from __future__ import annotations

import argparse
from email.parser import Parser
from pathlib import Path
import re
import zipfile


PACKAGE_NAMES = {
    "vuoro-client", "vuoro-bootstrap", "vuoro-service",
    "vuoro-schema-runtime", "vuoro-adapter-kit",
}
TAG = re.compile(
    r"^(vuoro-client|vuoro-bootstrap|vuoro-service|vuoro-schema-runtime|vuoro-adapter-kit)-v(.+)$"
)
IMMUTABLE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[a-z]+[0-9]+)?$")


def wheel_metadata(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        metadata_paths = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_paths) != 1:
            raise ValueError(f"{path}: expected exactly one wheel METADATA file")
        metadata = Parser().parsestr(archive.read(metadata_paths[0]).decode())
    name = metadata.get("Name")
    version = metadata.get("Version")
    if not name or not version:
        raise ValueError(f"{path}: wheel metadata lacks Name or Version")
    return name, version


def validate(paths: list[Path], tag: str | None = None, *, release: bool = False) -> None:
    if not paths:
        raise ValueError("at least one wheel is required")
    seen: set[str] = set()
    for path in paths:
        name, version = wheel_metadata(path)
        if name not in PACKAGE_NAMES:
            raise ValueError(f"{path}: unexpected distribution {name!r}")
        if name in seen:
            raise ValueError(f"duplicate wheel for {name}")
        seen.add(name)
        if release and not IMMUTABLE_VERSION.fullmatch(version):
            raise ValueError(f"{name}: release wheel has mutable development version {version!r}")
        if tag:
            match = TAG.fullmatch(tag)
            if not match or match.group(1) != name or match.group(2) != version:
                raise ValueError(f"{name}: wheel version {version!r} does not match tag {tag!r}")
    if tag and len(paths) != 1:
        raise ValueError("a package release tag must publish exactly one distribution")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheels", nargs="+", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()
    validate(args.wheels, args.tag, release=args.release or args.tag is not None)
    print("release contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

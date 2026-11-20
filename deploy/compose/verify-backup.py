#!/usr/bin/env python3
"""Verify the private Compose backup before starting a restore."""

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
import tarfile

FILES = frozenset({"workspace.dump", "knowledge.tar"})


def backup_file(directory: Path, name: str) -> Path:
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{name} must be a regular file in the backup directory")
    return path


def verify(directory: Path) -> None:
    checksums = backup_file(directory, "SHA256SUMS")
    if checksums.stat().st_size > 1024:
        raise ValueError("unexpected checksum manifest size")
    expected = {}
    for line in checksums.read_text(encoding="ascii").splitlines():
        entry = re.fullmatch(r"([0-9a-f]{64}) [ *](workspace\.dump|knowledge\.tar)", line)
        if entry is None or entry[2] in expected:
            raise ValueError("invalid or duplicate checksum entry")
        expected[entry[2]] = entry[1]
    if expected.keys() != FILES:
        raise ValueError("checksum manifest must name both backup files")

    for name, digest in expected.items():
        with backup_file(directory, name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError(f"checksum mismatch for {name}")

    with backup_file(directory, "workspace.dump").open("rb") as dump:
        if dump.read(5) != b"PGDMP":
            raise ValueError("workspace.dump must use the PostgreSQL custom archive format")

    paths = set()
    with tarfile.open(backup_file(directory, "knowledge.tar"), mode="r:") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if (
                member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}
                or member.mode & 0o7000
                or path.is_absolute() or ".." in path.parts or path in paths
                or (len(path.parts) <= 1 and not member.isdir())
                or (path.parts and path.parts[0] != "knowledge-objects")
            ):
                raise ValueError("knowledge.tar has an unexpected entry")
            paths.add(path)
    if PurePosixPath("knowledge-objects") not in paths:
        raise ValueError("knowledge object directory is missing")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    args = parser.parse_args()
    try:
        verify(args.backup)
    except (OSError, ValueError, tarfile.TarError) as exc:
        parser.exit(1, f"Backup verification failed: {exc}\n")
    print("Backup files and Knowledge archive verified.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Inventory the generation source locks without installing or executing tools.

This is source provenance, not an SBOM, signature, licence assessment, support
promise or qualification of an installed/upgraded generation chain.
"""

from __future__ import annotations

import argparse
import ast
import base64
import json
import re
import sys
import tomllib
from hashlib import sha256
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


class SupplyError(ValueError):
    """A stable, value-free source inventory refusal."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise SupplyError(code)


def _read(root: Path, relative: str) -> bytes:
    path = PurePosixPath(relative)
    _require(
        not path.is_absolute()
        and bool(path.parts)
        and all(part not in {".", ".."} for part in path.parts),
        "source-path",
    )
    current = root
    for part in path.parts:
        current = current / part
        _require(not current.is_symlink(), "source-symlink")
    _require(
        current.is_file() and current.stat().st_size <= 16 * 1024 * 1024, "source-file"
    )
    return current.read_bytes()


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate-json-key")
        result[key] = value
    return result


def _json(raw: bytes) -> dict:
    result = json.loads(raw, object_pairs_hook=_pairs)
    _require(isinstance(result, dict), "source-object")
    return result


def qualified_sources(root: Path) -> tuple[dict[str, bytes], str]:
    """Read pins as data; do not import or execute the generation program."""
    generator = _read(root, "scripts/contracts/generate.py")
    assignments = [
        node
        for node in ast.parse(generator).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_QUALIFIED_INPUT_SHA256"
            for target in node.targets
        )
    ]
    _require(len(assignments) == 1, "qualified-pins")
    pins = ast.literal_eval(assignments[0].value)
    _require(isinstance(pins, dict) and bool(pins), "qualified-pins")
    sources = {}
    for relative, expected in sorted(pins.items()):
        _require(
            isinstance(relative, str)
            and isinstance(expected, str)
            and re.fullmatch(r"[0-9a-f]{64}", expected) is not None,
            "qualified-pins",
        )
        raw = _read(root, relative)
        _require(sha256(raw).hexdigest() == expected, "qualified-input-drift")
        sources[relative] = raw
    return sources, sha256(generator).hexdigest()


def _https(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _integrity(value: object) -> None:
    _require(isinstance(value, str), "npm-artifact-integrity")
    match = re.fullmatch(r"(sha256|sha384|sha512)-([A-Za-z0-9+/]+={0,2})", value)
    _require(match is not None, "npm-artifact-integrity")
    try:
        decoded = base64.b64decode(match[2], validate=True)
    except ValueError as exc:
        raise SupplyError("npm-artifact-integrity") from exc
    _require(
        len(decoded) == {"sha256": 32, "sha384": 48, "sha512": 64}[match[1]],
        "npm-artifact-integrity",
    )


def npm_packages(project: dict, lock: dict) -> list[dict]:
    _require(lock.get("lockfileVersion") == 3, "npm-lock-version")
    packages = lock.get("packages")
    _require(
        isinstance(packages, dict) and isinstance(packages.get(""), dict),
        "npm-packages",
    )
    for section in ("dependencies", "devDependencies", "engines"):
        _require(
            project.get(section, {}) == packages[""].get(section, {}),
            "npm-project-lock",
        )
    direct = {**project.get("dependencies", {}), **project.get("devDependencies", {})}
    for name, version in direct.items():
        entry = packages.get("node_modules/" + name, {})
        _require(
            (
                version == "file:../../contracts"
                and name == "@vuoro/contracts"
                and entry.get("resolved") == version
            )
            or (
                isinstance(version, str)
                and re.fullmatch(r"\d+\.\d+\.\d+", version)
                and entry.get("version") == version
            ),
            "npm-direct-version",
        )
    result = []
    for location, entry in sorted(packages.items()):
        if not location:
            continue
        _require(
            isinstance(entry, dict) and isinstance(entry.get("version"), str),
            "npm-package",
        )
        local = location == "node_modules/@vuoro/contracts"
        if local:
            _require(
                entry.get("resolved") == "file:../../contracts", "npm-local-source"
            )
        else:
            _require(_https(entry.get("resolved")), "npm-artifact-source")
            _integrity(entry.get("integrity"))
        result.append(
            {
                "location": location,
                "version": entry["version"],
                "source": entry["resolved"],
                "integrity": entry.get("integrity"),
                "licence": {
                    "basis": "npm-lock-declaration-only",
                    "declared": entry.get("license"),
                    "assessment": "unknown",
                },
                "support": "unknown",
            }
        )
    return result


def python_packages(project: dict, lock: dict) -> list[dict]:
    _require(
        lock.get("version") == 1
        and lock.get("requires-python") == project["project"].get("requires-python"),
        "python-lock-version",
    )
    packages = lock.get("package")
    _require(isinstance(packages, list), "python-packages")
    by_name = {}
    result = []
    for entry in packages:
        _require(
            isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and isinstance(entry.get("version"), str),
            "python-package",
        )
        name = entry["name"]
        _require(name not in by_name, "python-package-duplicate")
        by_name[name] = entry
        if entry.get("source") == {"virtual": "."}:
            _require(name == project["project"]["name"], "python-local-source")
            continue
        _require(
            isinstance(entry.get("source"), dict)
            and set(entry["source"]) == {"registry"}
            and _https(entry["source"]["registry"]),
            "python-artifact-source",
        )
        artifacts = ([entry["sdist"]] if "sdist" in entry else []) + entry.get(
            "wheels", []
        )
        _require(bool(artifacts), "python-artifact-missing")
        for artifact in artifacts:
            _require(
                isinstance(artifact, dict)
                and _https(artifact.get("url"))
                and isinstance(artifact.get("hash"), str)
                and re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["hash"]) is not None,
                "python-artifact-integrity",
            )
        result.append(
            {
                "name": name,
                "version": entry["version"],
                "registry": entry["source"]["registry"],
                "artifacts": sorted(
                    [{"url": a["url"], "hash": a["hash"]} for a in artifacts],
                    key=lambda a: a["url"],
                ),
                "licence": {
                    "basis": "not-recorded-in-uv-lock",
                    "declared": None,
                    "assessment": "unknown",
                },
                "support": "unknown",
            }
        )
    for dependency in project["project"].get("dependencies", []):
        match = re.fullmatch(r"([a-z0-9-]+)==([^\s;]+)", dependency)
        _require(
            match is not None and by_name.get(match[1], {}).get("version") == match[2],
            "python-direct-version",
        )
    return sorted(result, key=lambda p: p["name"])


def inventory(root: Path) -> dict:
    _require(root.is_dir() and not root.is_symlink(), "source-root")
    sources, generator_digest = qualified_sources(root)
    npm = npm_packages(
        _json(sources["tools/contracts/package.json"]),
        _json(sources["tools/contracts/package-lock.json"]),
    )
    python = python_packages(
        tomllib.loads(sources["tools/contracts/pyproject.toml"].decode()),
        tomllib.loads(sources["tools/contracts/uv.lock"].decode()),
    )
    shared = root / "contracts"
    _require(shared.is_dir() and not shared.is_symlink(), "local-source")
    local = {}
    for path in sorted(shared.rglob("*")):
        _require(not path.is_symlink(), "source-symlink")
        if not path.is_dir():
            relative = path.relative_to(root).as_posix()
            local[relative] = sha256(_read(root, relative)).hexdigest()
    _require(bool(local), "local-source")
    local_package = _json(_read(root, "contracts/package.json"))
    local_locked = next(
        entry for entry in npm if entry["location"] == "node_modules/@vuoro/contracts"
    )
    _require(
        local_package.get("name") == "@vuoro/contracts"
        and local_package.get("version") == local_locked["version"],
        "local-source-version",
    )
    return {
        "schema": "vuoro-generation-source-supply/v1",
        "scope": "locked-source-metadata-only",
        "generatorSha256": generator_digest,
        "qualifiedSources": {
            name: sha256(raw).hexdigest() for name, raw in sources.items()
        },
        "localContractSources": local,
        "npm": npm,
        "python": python,
        "runtimeSources": _json(sources["tools/contracts/toolchain/source-pins.json"]),
        "unestablished": [
            "installed-artifact-verification",
            "licence-assessment",
            "support-status",
            "generation-chain-upgrade-and-rollback",
            "cold-bootstrap",
            "paid-service-dependency-assessment",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument(
        "--check", type=Path, help="compare exact bytes with a prior inventory"
    )
    args = parser.parse_args(argv)
    try:
        raw = (
            json.dumps(inventory(args.root), sort_keys=True, indent=2) + "\n"
        ).encode()
        if args.check is not None:
            _require(args.check.read_bytes() == raw, "inventory-drift")
        else:
            sys.stdout.buffer.write(raw)
    except (SupplyError, OSError, ValueError, TypeError, KeyError, SyntaxError):
        print("generation source supply inventory refused", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

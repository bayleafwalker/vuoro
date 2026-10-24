#!/usr/bin/env python3
"""Build one generated Python SDK wheel from an inventory-checked candidate tree."""
from __future__ import annotations

import argparse
import base64
import csv
from hashlib import sha256
import io
import json
from pathlib import Path, PurePosixPath
import re
import tomllib
import zipfile

from packaging.version import Version

from vuoro_contracts import ContractBundle, ContractError


_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_FILES = 4096
_MAX_BYTES = 64 * 1024 * 1024


def _fail() -> None:
    raise ContractError("integrity")


def _file_inventory(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    total = 0
    entries = 0
    try:
        if root.is_symlink() or not root.is_dir():
            _fail()
        for path in root.rglob("*"):
            entries += 1
            if entries > _MAX_FILES * 2:
                _fail()
            if path.is_symlink():
                _fail()
            if path.is_dir():
                continue
            if not path.is_file() or len(files) >= _MAX_FILES:
                _fail()
            relative = path.relative_to(root).as_posix()
            pure = PurePosixPath(relative)
            if (pure.is_absolute() or not pure.parts or
                    len(relative) > 4096 or
                    any(part in ("", ".", "..") or len(part.encode("utf-8", "strict")) > 255
                        for part in pure.parts) or "\\" in relative):
                _fail()
            with path.open("rb") as stream:
                data = stream.read(_MAX_FILE_BYTES + 1)
            if len(data) > _MAX_FILE_BYTES:
                _fail()
            total += len(data)
            if total > _MAX_BYTES:
                _fail()
            files[relative] = data
        return files
    except ContractError:
        raise
    except Exception:
        raise ContractError("integrity") from None


def _record_digest(data: bytes) -> tuple[str, str]:
    digest = base64.urlsafe_b64encode(sha256(data).digest()).rstrip(b"=").decode("ascii")
    return "sha256=" + digest, str(len(data))


def build(bundle_root: Path, output_dir: Path, repository_root: Path) -> Path:
    try:
        bundle_arg = Path(bundle_root).absolute()
        if any(path.is_symlink() for path in (bundle_arg, *bundle_arg.parents)):
            _fail()
        bundle_root = bundle_arg.resolve(strict=True)
        output_arg = Path(output_dir).absolute()
        if any(path.is_symlink() for path in (output_arg, *output_arg.parents)):
            _fail()
        output_arg.mkdir(parents=True, exist_ok=True)
        if output_arg.is_symlink() or not output_arg.is_dir():
            _fail()
        output_dir = output_arg
        manifest = json.loads((bundle_root / "operation-manifest.json").read_bytes())
        if not isinstance(manifest, dict):
            _fail()
        from generate import python_package_name  # loaded from this script's sibling

        package_name = python_package_name(manifest.get("contractId"))
        version_text = manifest.get("contractVersion")
        if not isinstance(version_text, str) or not version_text:
            _fail()
        version = Version(version_text)
        if str(version) != version_text:
            _fail()

        top_integrity = json.loads((bundle_root / "integrity.json").read_bytes())
        if not isinstance(top_integrity, dict) or not isinstance(top_integrity.get("files"), dict):
            _fail()
        sdk_relative = f"clients/python/{package_name}"
        sdk_root = bundle_root / sdk_relative
        source_files = _file_inventory(sdk_root)
        expected = {
            name.removeprefix(sdk_relative + "/"): digest
            for name, digest in top_integrity["files"].items()
            if isinstance(name, str) and name.startswith(sdk_relative + "/")
        }
        actual = {name: sha256(data).hexdigest() for name, data in source_files.items()}
        if not expected or actual != expected:
            _fail()

        external_data_digest = sha256((sdk_root / "data/integrity.json").read_bytes()).hexdigest()
        ContractBundle.load(sdk_root / "data", integrity_sha256=external_data_digest)
        ContractBundle.load(bundle_root, integrity_sha256=sha256((bundle_root / "integrity.json").read_bytes()).hexdigest())

        lock = tomllib.loads((repository_root / "uv.lock").read_text())
        locked = {entry["name"]: entry["version"] for entry in lock.get("package", [])
                  if isinstance(entry, dict) and isinstance(entry.get("name"), str)}
        runtime_project = tomllib.loads((repository_root / "packages/vuoro-contracts/pyproject.toml").read_text())
        runtime_version = runtime_project["project"]["version"]
        requirements = [
            f"attrs=={locked['attrs']}",
            f"httpx=={locked['httpx']}",
            f"vuoro-contracts=={runtime_version}",
        ]

        # Wheel filenames use hyphens as field separators, so package-name
        # separators must be encoded as underscores to keep the hashed SDK
        # namespace in the single distribution field.
        distribution = re.sub(r"[-_.]+", "_", package_name).lower()
        wheel_version = version_text.replace("-", "_")
        filename = f"{distribution}-{wheel_version}-py3-none-any.whl"
        dist_info = f"{distribution}-{version_text.replace('-', '_')}.dist-info"
        metadata = (
            "Metadata-Version: 2.3\n"
            f"Name: {package_name}\n"
            f"Version: {version_text}\n"
            "Requires-Python: >=3.12\n"
            + "".join(f"Requires-Dist: {requirement}\n" for requirement in requirements)
            + "\n"
        ).encode("utf-8")
        wheel_metadata = (
            "Wheel-Version: 1.0\n"
            "Generator: vuoro-contracts-sdk-wheel-builder\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n\n"
        ).encode("ascii")

        entries = {f"{package_name}/{name}": data for name, data in source_files.items()}
        entries[f"{dist_info}/METADATA"] = metadata
        entries[f"{dist_info}/WHEEL"] = wheel_metadata
        record = io.StringIO(newline="")
        writer = csv.writer(record, lineterminator="\n")
        for name, data in sorted(entries.items()):
            digest, size = _record_digest(data)
            writer.writerow((name, digest, size))
        record_path = f"{dist_info}/RECORD"
        writer.writerow((record_path, "", ""))
        entries[record_path] = record.getvalue().encode("utf-8")

        target = output_dir / filename
        if target.exists() or target.is_symlink():
            _fail()
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as wheel:
            for name, data in sorted(entries.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                wheel.writestr(info, data)
        return target
    except ContractError:
        raise
    except Exception:
        raise ContractError("integrity") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    print(build(args.bundle, args.output_dir, args.repository_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

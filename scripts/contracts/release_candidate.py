"""Create and verify a read-only, source-bound contracts wheel candidate."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import xml.etree.ElementTree as ET
import zipfile
from email.parser import Parser

MAX_RECEIPT = 16 * 1024
MAX_JUNIT = 4 * 1024 * 1024
MAX_WHEEL = 64 * 1024 * 1024
MAX_METADATA = 1024 * 1024
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-z]+[0-9]+)?\Z")
RECEIPT_KEYS = {
    "schema_version", "distribution", "version", "wheel_basename", "wheel_sha256",
    "source_sha", "run_id", "run_attempt", "artifact_name", "qualification",
    "toolchain_sha256", "source_pins_sha256", "tool_artifacts_sha256",
}
PIN_FILES = {
    "toolchain_sha256": "tools/contracts/toolchain/flake.lock",
    "source_pins_sha256": "tools/contracts/toolchain/source-pins.json",
    "tool_artifacts_sha256": "tools/contracts/toolchain/tool-artifacts.json",
}


def fail(message: str) -> None:
    raise ValueError(message)


def _open_dir(path: str | os.PathLike[str]) -> int:
    """Open every directory component with O_NOFOLLOW, returning its fd."""
    absolute = os.path.abspath(os.fspath(path))
    parts = [part for part in absolute.split(os.sep) if part]
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(os.sep, flags)
    try:
        for part in parts:
            nxt = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_at(dir_fd: int, name: str, limit: int) -> bytes:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        fail("expected a basename")
    fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=dir_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            fail(f"{name}: expected a regular file")
        if info.st_size > limit:
            fail(f"{name}: file exceeds size limit")
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                fail(f"{name}: file exceeds size limit")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _read_path(path: str | os.PathLike[str], limit: int) -> bytes:
    absolute = os.path.abspath(os.fspath(path))
    parent, name = os.path.split(absolute)
    fd = _open_dir(parent or os.sep)
    try:
        return _read_at(fd, name, limit)
    finally:
        os.close(fd)


def _pin_digests() -> dict[str, str]:
    root = Path(__file__).parent.parent.parent
    result = {}
    for key, relative in PIN_FILES.items():
        data = _read_path(root / relative, MAX_WHEEL)
        result[key] = hashlib.sha256(data).hexdigest()
    return result


def _directory_files(path: str, expected: set[str] | None = None) -> tuple[int, list[str]]:
    fd = _open_dir(path)
    try:
        names = os.listdir(fd)
        if expected is not None and set(names) != expected:
            fail("candidate directory contains unexpected or missing entries")
        for name in names:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                fail("candidate directory entries must be regular files")
        return fd, names
    except BaseException:
        os.close(fd)
        raise


def _json_object(raw: bytes, limit: int, label: str) -> dict:
    if len(raw) > limit:
        fail(f"{label} exceeds size limit")
    try:
        text = raw.decode("utf-8")
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    fail(f"{label} has duplicate JSON keys")
                result[key] = value
            return result
        obj = json.loads(text, object_pairs_hook=pairs, parse_constant=lambda value: fail(f"invalid JSON constant {value}"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"{label} is not valid UTF-8 JSON: {exc}")
    if type(obj) is not dict:
        fail(f"{label} must be a JSON object")
    return obj


def _positive_decimal(value: str, label: str) -> str:
    if not re.fullmatch(r"[0-9]{1,20}", value) or int(value) <= 0:
        fail(f"{label} must be a positive decimal string of at most 20 digits")
    return value


def _validate_sha(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        fail(f"{label} must be lowercase hexadecimal")
    return value


def _metadata(data: bytes) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            paths = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
            if len(paths) != 1:
                fail("wheel must contain exactly one METADATA file")
            info = archive.getinfo(paths[0])
            if info.file_size > MAX_METADATA:
                fail("wheel METADATA exceeds the 1 MiB limit")
            with archive.open(info) as metadata_file:
                raw_bytes = metadata_file.read(MAX_METADATA + 1)
            if len(raw_bytes) > MAX_METADATA or len(raw_bytes) != info.file_size:
                fail("wheel METADATA exceeds the 1 MiB limit or is truncated")
            raw = raw_bytes.decode("utf-8")
    except (zipfile.BadZipFile, UnicodeDecodeError, KeyError) as exc:
        fail(f"invalid wheel metadata: {exc}")
    metadata = Parser().parsestr(raw)
    if len(metadata.get_all("Name", [])) != 1 or len(metadata.get_all("Version", [])) != 1:
        fail("wheel metadata must have exactly one Name and Version header")
    name, version = metadata.get("Name"), metadata.get("Version")
    if not name or not version:
        fail("wheel metadata lacks Name or Version")
    return name, version


def _junit(data: bytes) -> dict[str, int]:
    if len(data) > MAX_JUNIT:
        fail("JUnit report exceeds size limit")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"JUnit report must be UTF-8: {exc}")
    if "\x00" in text:
        fail("JUnit report contains invalid XML bytes")
    if "<!DOCTYPE" in text:
        fail("JUnit report must not contain a DOCTYPE")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        fail(f"malformed JUnit report: {exc}")
    cases = ([root] if root.tag == "testcase" else []) + root.findall(".//testcase")
    counts = {"tests": len(cases), "skipped": 0, "failures": 0, "errors": 0}
    for case in cases:
        for tag in ("skipped", "failure", "error"):
            counts[{"skipped": "skipped", "failure": "failures", "error": "errors"}[tag]] += sum(1 for child in case if child.tag == tag)
    return counts


def _wheel_entry(names: list[str], fd: int) -> tuple[str, bytes, str, str]:
    wheels = [name for name in names if name.endswith(".whl")]
    if len(wheels) != 1 or len(names) != len(wheels):
        fail("wheel directory must contain exactly one wheel and no other entries")
    basename = wheels[0]
    if os.path.basename(basename) != basename or basename.startswith(".") or "/" in basename or "\\" in basename:
        fail("wheel name must be a safe basename")
    data = _read_at(fd, basename, MAX_WHEEL)
    name, version = _metadata(data)
    if name != "vuoro-contracts" or not VERSION.fullmatch(version):
        fail("wheel must be an immutable vuoro-contracts release")
    expected = f"vuoro_contracts-{version}-py3-none-any.whl"
    if basename != expected:
        fail("wheel filename does not match its distribution and version")
    return basename, data, name, version


def _receipt_shape(receipt: dict) -> None:
    if set(receipt) != RECEIPT_KEYS:
        fail("candidate receipt has unexpected or missing fields")
    if receipt["schema_version"] != "vuoro-contracts-release-candidate/v1" or receipt["distribution"] != "vuoro-contracts":
        fail("candidate schema or distribution is invalid")
    string_fields = RECEIPT_KEYS - {"qualification"}
    if any(type(receipt[key]) is not str for key in string_fields):
        fail("candidate identity fields must be strings")
    if not VERSION.fullmatch(receipt["version"]):
        fail("candidate version is not immutable")
    _validate_sha(receipt["source_sha"], SHA40, "receipt source SHA")
    _validate_sha(receipt["wheel_sha256"], SHA256, "receipt wheel SHA256")
    _positive_decimal(receipt["run_id"], "receipt run id")
    _positive_decimal(receipt["run_attempt"], "receipt run attempt")
    if receipt["artifact_name"] != f"contracts-candidate-{receipt['run_id']}-{receipt['run_attempt']}":
        fail("receipt artifact name does not match its run identity")
    for key in ("toolchain_sha256", "source_pins_sha256", "tool_artifacts_sha256"):
        _validate_sha(receipt[key], SHA256, f"receipt {key}")
    qualification = receipt["qualification"]
    if type(qualification) is not dict or set(qualification) != {"tests", "skipped", "failures", "errors"}:
        fail("candidate qualification fields are invalid")
    if any(type(value) is not int for value in qualification.values()):
        fail("candidate qualification counts must be integers")
    if qualification != {"tests": 279, "skipped": 0, "failures": 0, "errors": 0}:
        fail("candidate qualification did not pass")


def create(args) -> dict:
    source = _validate_sha(args.source_sha, SHA40, "source SHA")
    run_id = _positive_decimal(args.run_id, "run id")
    attempt = _positive_decimal(args.run_attempt, "run attempt")
    fd, names = _directory_files(args.wheel_dir)
    try:
        basename, wheel, _, version = _wheel_entry(names, fd)
    finally:
        os.close(fd)
    junit = _junit(_read_path(args.junit, MAX_JUNIT))
    if junit != {"tests": 279, "skipped": 0, "failures": 0, "errors": 0}:
        fail("production JUnit report must contain exactly 279 passing cases")
    return {
        "schema_version": "vuoro-contracts-release-candidate/v1",
        "distribution": "vuoro-contracts", "version": version,
        "wheel_basename": basename, "wheel_sha256": hashlib.sha256(wheel).hexdigest(),
        "source_sha": source, "run_id": run_id, "run_attempt": attempt,
        "artifact_name": f"contracts-candidate-{run_id}-{attempt}",
        "qualification": junit, **_pin_digests(),
    }


def verify(args) -> dict:
    expected = {
        "source_sha": _validate_sha(args.expected_source_sha, SHA40, "expected source SHA"),
        "run_id": _positive_decimal(args.expected_run_id, "expected run id"),
        "run_attempt": _positive_decimal(args.expected_run_attempt, "expected run attempt"),
        "artifact_name": args.expected_artifact_name,
        "wheel_basename": args.expected_wheel_basename,
        "wheel_sha256": _validate_sha(args.expected_wheel_sha256, SHA256, "expected wheel SHA256"),
    }
    if expected["artifact_name"] != f"contracts-candidate-{expected['run_id']}-{expected['run_attempt']}":
        fail("expected artifact name does not match run identity")
    for label in ("wheel_basename",):
        value = expected[label]
        if not value or os.path.basename(value) != value or "/" in value or "\\" in value or value in {".", ".."}:
            fail("expected wheel name must be a safe basename")
    fd, names = _directory_files(args.candidate_dir, {"candidate.json", expected["wheel_basename"]})
    try:
        receipt = _json_object(_read_at(fd, "candidate.json", MAX_RECEIPT), MAX_RECEIPT, "candidate receipt")
        _receipt_shape(receipt)
        basename, wheel, name, version = _wheel_entry([n for n in names if n != "candidate.json"], fd)
    finally:
        os.close(fd)
    if name != "vuoro-contracts" or receipt["version"] != version or not VERSION.fullmatch(version):
        fail("receipt distribution/version does not match wheel metadata")
    if basename != f"vuoro_contracts-{version}-py3-none-any.whl":
        fail("wheel filename does not match immutable metadata")
    if args.tag != f"vuoro-contracts-v{version}":
        fail("release tag does not match immutable wheel version")
    actual = hashlib.sha256(wheel).hexdigest()
    if receipt["wheel_sha256"] != actual:
        fail("receipt wheel hash does not match wheel bytes")
    for key, value in expected.items():
        if receipt[key] != value:
            fail(f"receipt {key} does not match trusted expected identity")
    if not SHA40.fullmatch(receipt["source_sha"]):
        fail("receipt source SHA is invalid")
    for key in ("run_id", "run_attempt"):
        _positive_decimal(receipt[key], f"receipt {key}")
    if receipt["artifact_name"] != f"contracts-candidate-{receipt['run_id']}-{receipt['run_attempt']}":
        fail("receipt artifact name does not match its run identity")
    for key, digest in _pin_digests().items():
        if receipt[key] != digest:
            fail("candidate toolchain pin digest is stale")
    for key in ("toolchain_sha256", "source_pins_sha256", "tool_artifacts_sha256"):
        if not isinstance(receipt[key], str) or not SHA256.fullmatch(receipt[key]):
            fail("receipt pin digest is invalid")
    return {"wheel_basename": basename, "wheel_sha256": actual, "source_sha": receipt["source_sha"], "artifact_name": receipt["artifact_name"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create_parser = commands.add_parser("create")
    create_parser.add_argument("--wheel-dir", required=True)
    create_parser.add_argument("--source-sha", required=True)
    create_parser.add_argument("--run-id", required=True)
    create_parser.add_argument("--run-attempt", required=True)
    create_parser.add_argument("--junit", required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--candidate-dir", required=True)
    verify_parser.add_argument("--expected-source-sha", required=True)
    verify_parser.add_argument("--expected-run-id", required=True)
    verify_parser.add_argument("--expected-run-attempt", required=True)
    verify_parser.add_argument("--expected-artifact-name", required=True)
    verify_parser.add_argument("--expected-wheel-basename", required=True)
    verify_parser.add_argument("--expected-wheel-sha256", required=True)
    verify_parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    try:
        receipt = create(args) if args.command == "create" else verify(args)
        sys.stdout.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError, zipfile.BadZipFile) as exc:
        print(f"release candidate: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

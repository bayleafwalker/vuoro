"""Independent stdlib wheel/receipt oracles for the contracts release boundary.

The candidate helper is a stdout-only CLI. Neither command may write files.
Synthetic wheels are metadata fixtures, not claims of a built release artifact.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/contracts/release_candidate.py"
spec = importlib.util.spec_from_file_location("release_validator", ROOT / "scripts/validate_release_contract.py")
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
SOURCE = "a" * 40
RUN = "12345"
ATTEMPT = "2"
ARTIFACT = f"contracts-candidate-{RUN}-{ATTEMPT}"
PINS = {
    "toolchain_sha256": "flake.lock",
    "source_pins_sha256": "source-pins.json",
    "tool_artifacts_sha256": "tool-artifacts.json",
}
RECEIPT_KEYS = {
    "schema_version", "distribution", "version", "wheel_basename", "wheel_sha256",
    "source_sha", "run_id", "run_attempt", "artifact_name", "qualification", *PINS,
}


def wheel(folder: Path, name: str = "vuoro-contracts", version: str = "0.1.0", *, metadata: str | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stem = name.replace("-", "_")
    path = folder / f"{stem}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{stem}-{version}.dist-info/METADATA", metadata if metadata is not None else f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n\n")
        archive.writestr(f"{stem}/__init__.py", "")
    return path


@pytest.mark.parametrize("name", ["vuoro-client", "vuoro-service", "vuoro-schema-runtime", "vuoro-adapter-kit", "vuoro-demo", "vuoro-workspace", "vuoro-contracts"])
def test_exact_supported_distribution_and_immutable_tag(tmp_path: Path, name: str) -> None:
    validator.validate([wheel(tmp_path, name)], f"{name}-v0.1.0", release=True)


@pytest.mark.parametrize("metadata", [
    "Version: 0.1.0\n\n", "Name: vuoro-client\n\n",
    "Name: vuoro-client\nName: other\nVersion: 0.1.0\n\n",
    "Name: vuoro-client\nVersion: 0.1.0\nVersion: 9.9.9\n\n",
    "Name: vuoro-client\nVersion: 0.1.0\n folded\n\n",
])
def test_release_validator_refuses_malformed_or_ambiguous_metadata(tmp_path: Path, metadata: str) -> None:
    with pytest.raises(ValueError):
        validator.validate([wheel(tmp_path, "vuoro-client", metadata=metadata)], release=True)


@pytest.mark.parametrize("case", ["unknown", "cross-package", "wrong-version", "dev", "two-distributions", "duplicate-distribution", "two-metadata", "no-metadata", "empty"])
def test_existing_release_refusals_stay_closed(tmp_path: Path, case: str) -> None:
    paths = [wheel(tmp_path / "a", "vuoro-client")]
    tag = "vuoro-client-v0.1.0"
    if case == "unknown":
        paths = [wheel(tmp_path / "b", "vuoro-unknown")]
    elif case == "cross-package":
        tag = "vuoro-service-v0.1.0"
    elif case == "wrong-version":
        tag = "vuoro-client-v0.1.1"
    elif case == "dev":
        paths = [wheel(tmp_path / "b", "vuoro-client", "0.1.0.dev1")]
        tag = "vuoro-client-v0.1.0.dev1"
    elif case == "two-distributions":
        paths.append(wheel(tmp_path / "b", "vuoro-service"))
    elif case == "duplicate-distribution":
        paths.append(wheel(tmp_path / "b", "vuoro-client"))
    elif case == "two-metadata":
        with zipfile.ZipFile(paths[0], "a") as archive:
            archive.writestr("other-0.1.0.dist-info/METADATA", "Name: other\nVersion: 0.1.0\n")
    elif case == "no-metadata":
        with zipfile.ZipFile(paths[0], "w") as archive:
            archive.writestr("data", "x")
    elif case == "empty":
        paths = []
    with pytest.raises(ValueError):
        validator.validate(paths, tag, release=True)


def invoke(*args: str) -> subprocess.CompletedProcess[str]:
    assert HELPER.is_file(), "implement the frozen stdout-only candidate helper"
    return subprocess.run([sys.executable, "-I", str(HELPER), *args], capture_output=True, text=True, timeout=10, check=False)


def command(candidate: Path, selected: Path) -> list[str]:
    return ["verify", "--candidate-dir", str(candidate), "--expected-source-sha", SOURCE,
            "--expected-run-id", RUN, "--expected-run-attempt", ATTEMPT,
            "--expected-artifact-name", ARTIFACT, "--expected-wheel-basename", selected.name,
            "--expected-wheel-sha256", hashlib.sha256(selected.read_bytes()).hexdigest(),
            "--tag", "vuoro-contracts-v0.1.0"]


@pytest.fixture
def candidate(tmp_path: Path) -> tuple[Path, Path, dict]:
    folder = tmp_path / "candidate"
    selected = wheel(folder)
    receipt = {
        "schema_version": "vuoro-contracts-release-candidate/v1", "distribution": "vuoro-contracts", "version": "0.1.0",
        "wheel_basename": selected.name, "wheel_sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
        "source_sha": SOURCE, "run_id": RUN, "run_attempt": ATTEMPT, "artifact_name": ARTIFACT,
        "qualification": {"tests": 279, "skipped": 0, "failures": 0, "errors": 0},
        **{key: hashlib.sha256((ROOT / "tools/contracts/toolchain" / file).read_bytes()).hexdigest() for key, file in PINS.items()},
    }
    (folder / "candidate.json").write_text(json.dumps(receipt))
    return folder, selected, receipt


def snapshot(folder: Path) -> dict[str, bytes | str]:
    return {str(p.relative_to(folder)): (str(p.readlink()) if p.is_symlink() else p.read_bytes()) for p in folder.rglob("*") if not p.is_dir()}


def test_independently_authored_receipt_verifies_without_writes(candidate) -> None:
    folder, selected, _ = candidate
    before = snapshot(folder)
    result = invoke(*command(folder, selected))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"wheel_basename": selected.name, "wheel_sha256": hashlib.sha256(selected.read_bytes()).hexdigest(), "source_sha": SOURCE, "artifact_name": ARTIFACT}
    assert snapshot(folder) == before


@pytest.mark.parametrize("case", ["wheel-byte", "basename", "source", "output-hash", "output-source", "output-artifact", "output-run", "pin", "extra-field", "missing-field", "bool-count", "skipped", "count", "state", "duplicate-key", "oversize", "second-wheel", "extra-file", "receipt-symlink", "wheel-symlink", "directory-symlink", "ancestor-symlink", "traversal", "wrong-tag", "wrong-package", "wrong-version", "invalid-sha", "numeric-run", "json-array", "invalid-json", "invalid-utf8"])
def test_real_helper_refuses_tamper_before_publication(candidate, tmp_path: Path, case: str) -> None:
    folder, selected, receipt = candidate
    args = command(folder, selected)  # independent trusted values captured BEFORE tamper
    receipt_path = folder / "candidate.json"
    if case == "wheel-byte":
        selected.write_bytes(selected.read_bytes() + b"tamper")
    elif case == "basename":
        selected.rename(folder / "vuoro_contracts-9.9.9-py3-none-any.whl")
    elif case == "source":
        receipt["source_sha"] = "b" * 40
    elif case in {"output-hash", "output-source", "output-artifact", "output-run"}:
        option, value = {"output-hash": ("--expected-wheel-sha256", "0" * 64), "output-source": ("--expected-source-sha", "b" * 40), "output-artifact": ("--expected-artifact-name", "other-run"), "output-run": ("--expected-run-id", "54321")}[case]
        args[args.index(option) + 1] = value
    elif case == "pin":
        receipt["source_pins_sha256"] = "0" * 64
    elif case == "extra-field":
        receipt["authority"] = True
    elif case == "missing-field":
        del receipt["source_sha"]
    elif case == "bool-count":
        receipt["qualification"]["skipped"] = False
    elif case in {"skipped", "count", "state"}:
        key, value = {"skipped": ("skipped", 1), "count": ("tests", 256), "state": ("success", True)}[case]
        receipt["qualification"][key] = value
    elif case == "second-wheel":
        wheel(folder, version="0.1.1")
    elif case == "extra-file":
        (folder / "unexpected.txt").write_text("x")
    elif case in {"receipt-symlink", "wheel-symlink"}:
        path = receipt_path if case == "receipt-symlink" else selected
        outside = tmp_path / "outside"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    elif case == "directory-symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(folder, target_is_directory=True)
        args[args.index("--candidate-dir") + 1] = str(alias)
    elif case == "ancestor-symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        args[args.index("--candidate-dir") + 1] = str(alias / "candidate")
    elif case == "traversal":
        receipt["wheel_basename"] = "../" + selected.name
    elif case == "wrong-tag":
        args[-1] = "vuoro-contracts-v0.1.1"
    elif case in {"wrong-package", "wrong-version", "invalid-sha", "numeric-run"}:
        key, value = {"wrong-package": ("distribution", "vuoro-client"), "wrong-version": ("version", "0.1.1"), "invalid-sha": ("wheel_sha256", "g" * 64), "numeric-run": ("run_id", 12345)}[case]
        receipt[key] = value
    if case not in {"receipt-symlink", "wheel-symlink"}:
        raw = json.dumps(receipt)
        if case == "duplicate-key":
            raw = raw[:-1] + ', "source_sha": "' + SOURCE + '"}'
        elif case == "oversize":
            raw += " " * 16385
        elif case == "json-array":
            raw = "[]"
        elif case == "invalid-json":
            raw = "{"
        receipt_path.write_text(raw)
        if case == "invalid-utf8":
            receipt_path.write_bytes(b"\xff")
    before = snapshot(folder)
    result = invoke(*args)
    assert result.returncode != 0, (case, result.stdout)
    assert not result.stdout.strip(), "failure must not emit publishable identity"
    assert snapshot(folder) == before


def test_create_uses_actual_junit_and_exact_pins_with_no_write(tmp_path: Path) -> None:
    folder = tmp_path / "build"
    selected = wheel(folder)
    junit = tmp_path / "tests.xml"
    junit.write_text('<testsuite>' + '<testcase name="case"/>' * 279 + '</testsuite>')
    args = ["create", "--wheel-dir", str(folder), "--source-sha", SOURCE, "--run-id", RUN, "--run-attempt", ATTEMPT, "--junit", str(junit)]
    before = snapshot(tmp_path)
    result = invoke(*args)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert set(receipt) == RECEIPT_KEYS
    assert receipt["artifact_name"] == ARTIFACT and receipt["source_sha"] == SOURCE
    assert receipt["wheel_basename"] == selected.name
    assert receipt["wheel_sha256"] == hashlib.sha256(selected.read_bytes()).hexdigest()
    assert receipt["qualification"] == {"tests": 279, "skipped": 0, "failures": 0, "errors": 0}
    for key, name in PINS.items():
        assert receipt[key] == hashlib.sha256((ROOT / "tools/contracts/toolchain" / name).read_bytes()).hexdigest()
    assert snapshot(tmp_path) == before
    (folder / "candidate.json").write_text(result.stdout)
    checked = invoke(*command(folder, selected))
    assert checked.returncode == 0, checked.stderr


@pytest.mark.parametrize("xml", [
    '<testsuite/>',
    '<testsuite>' + '<testcase/>' * 257 + '</testsuite>',
    '<testsuite>' + '<testcase/>' * 263 + '</testsuite>',
    '<testsuite>' + '<testcase/>' * 278 + '</testsuite>',
    '<testsuite>' + '<testcase/>' * 278 + '<testcase><skipped/></testcase></testsuite>',
    '<testsuite>' + '<testcase/>' * 278 + '<testcase><failure/></testcase></testsuite>',
    '<testsuite>' + '<testcase/>' * 278 + '<testcase><error/></testcase></testsuite>',
    '<!invalid>',
], ids=["empty", "original-corpus", "previous-corpus", "short", "skip", "failure", "error", "malformed"])
def test_create_cannot_convert_bad_production_junit_into_success(tmp_path: Path, xml: str) -> None:
    if any(marker in xml for marker in ('<skipped', '<failure', '<error')):
        # These faults must fail independently of the accepted corpus count.
        assert xml.count('<testcase') == 279
    wheel(tmp_path / "build")
    junit = tmp_path / "tests.xml"
    junit.write_text(xml)
    result = invoke("create", "--wheel-dir", str(tmp_path / "build"), "--source-sha", SOURCE, "--run-id", RUN, "--run-attempt", ATTEMPT, "--junit", str(junit))
    assert result.returncode != 0 and not result.stdout.strip()


@pytest.mark.parametrize("case", ["two-wheels", "wrong-package", "mutable-version", "invalid-source", "invalid-run", "wheel-symlink", "junit-symlink", "arbitrary-output"])
def test_create_is_not_a_generic_writer_or_unqualified_selector(tmp_path: Path, case: str) -> None:
    folder = tmp_path / "build"
    selected = wheel(folder, "vuoro-client" if case == "wrong-package" else "vuoro-contracts", "0.1.0.dev1" if case == "mutable-version" else "0.1.0")
    junit = tmp_path / "tests.xml"
    junit.write_text('<testsuite>' + '<testcase/>' * 279 + '</testsuite>')
    args = ["create", "--wheel-dir", str(folder), "--source-sha", SOURCE, "--run-id", RUN, "--run-attempt", ATTEMPT, "--junit", str(junit)]
    if case == "two-wheels":
        wheel(folder, version="0.1.1")
    elif case == "invalid-source":
        args[args.index("--source-sha") + 1] = "main"
    elif case == "invalid-run":
        args[args.index("--run-id") + 1] = "latest"
    elif case in {"wheel-symlink", "junit-symlink"}:
        path = selected if case == "wheel-symlink" else junit
        outside = tmp_path / "outside"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    elif case == "arbitrary-output":
        args += ["--output", str(tmp_path / "unapproved-write")]
    before = snapshot(tmp_path)
    result = invoke(*args)
    assert result.returncode != 0 and not result.stdout.strip()
    assert snapshot(tmp_path) == before

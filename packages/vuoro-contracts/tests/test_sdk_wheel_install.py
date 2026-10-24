"""Offline installed-artifact check for generated Python SDK resource roots."""
import importlib.util
import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from vuoro_contracts import ContractError

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "packages/vuoro-contracts/tests/fixtures/probe"
BUILDER_PATH = ROOT / "scripts/contracts/build_python_sdk.py"
_spec = importlib.util.spec_from_file_location("contract_wheel_builder", BUILDER_PATH)
_builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_builder)


def _reseal(root: Path) -> None:
    inventory = {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() != "integrity.json"
    }
    (root / "integrity.json").write_text(json.dumps(
        {"schemaVersion": "vuoro-contract-integrity/v1", "files": inventory},
        sort_keys=True, separators=(",", ":"),
    ) + "\n")


def _candidate(tmp_path: Path, contract_id: str = "probe/v1") -> tuple[Path, str]:
    import shutil

    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "bundle"
    shutil.copytree(FIXTURE, root)
    manifest = json.loads((root / "operation-manifest.json").read_bytes())
    manifest["contractId"] = contract_id
    (root / "operation-manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    package = "vuoro_contract_" + sha256(manifest["contractId"].encode()).hexdigest() + "_client"
    sdk = root / "clients/python" / package
    (sdk / "data").parent.mkdir(parents=True)
    shutil.copytree(FIXTURE, sdk / "data")
    (sdk / "data/operation-manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    _reseal(sdk / "data")
    (sdk / "__init__.py").write_text(
        "from .validated_client import load_bundle\n"
    )
    (sdk / "validated_client.py").write_text(
        "from hashlib import sha256\n"
        "from importlib import resources\n"
        "from vuoro_contracts import ContractBundle\n"
        "def load_bundle():\n"
        "    root = resources.files(__package__).joinpath('data')\n"
        "    digest = sha256(root.joinpath('integrity.json').read_bytes()).hexdigest()\n"
        "    return ContractBundle.load(root, integrity_sha256=digest)\n"
    )
    _reseal(root)
    return root, package


def test_sdk_wheel_installs_offline_and_loads_authenticated_resources(tmp_path, monkeypatch):
    first_bundle, first_package = _candidate(tmp_path / "first", "probe/v1")
    second_bundle, second_package = _candidate(tmp_path / "second", "probe/v2")
    with monkeypatch.context() as scoped:
        scoped.syspath_prepend(str(ROOT / "scripts/contracts"))
        first_wheel = _builder.build(first_bundle, tmp_path / "wheelhouse", ROOT)
        second_wheel = _builder.build(second_bundle, tmp_path / "wheelhouse", ROOT)
    target = tmp_path / "installed"
    result = subprocess.run(
        [sys._base_executable, "-m", "pip", "install", "--no-index", "--no-deps",
         "--disable-pip-version-check", "--target", str(target), str(first_wheel), str(second_wheel)],
        text=True, capture_output=True, timeout=90, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(target), str(ROOT / "packages/vuoro-contracts/src")))
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    source = (
        "import importlib, json; "
        f"first=importlib.import_module({first_package!r}).load_bundle().manifest['contractId']; "
        f"second=importlib.import_module({second_package!r}).load_bundle().manifest['contractId']; "
        "print(json.dumps([first, second]))"
    )
    observed = subprocess.run(
        [sys._base_executable, "-c", source], env=env, text=True, capture_output=True, timeout=30,
        check=False,
    )
    assert observed.returncode == 0, observed.stdout + observed.stderr
    assert json.loads(observed.stdout) == ["probe/v1", "probe/v2"]

    for package in (first_package, second_package):
        installed = target / package
        assert any(installed.rglob("__pycache__"))  # ordinary bytecode creation is enabled
        assert not any((installed / "data").rglob("__pycache__"))
        data_root = installed / "data"
        assert sha256((data_root / "integrity.json").read_bytes()).hexdigest()


def test_builder_rejects_tampered_sdk_inventory(tmp_path, monkeypatch):
    bundle, _package = _candidate(tmp_path)
    manifest = json.loads((bundle / "operation-manifest.json").read_bytes())
    package = "vuoro_contract_" + sha256(manifest["contractId"].encode()).hexdigest() + "_client"
    client = bundle / "clients/python" / package / "validated_client.py"
    client.write_text(client.read_text() + "# changed after inventory\n")
    with monkeypatch.context() as scoped:
        scoped.syspath_prepend(str(ROOT / "scripts/contracts"))
        with pytest.raises(ContractError, match="integrity"):
            _builder.build(bundle, tmp_path / "wheelhouse", ROOT)

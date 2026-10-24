"""Portable namespace and installed-resource gates; no compiler is launched."""
from hashlib import sha256
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import zipfile

import pytest

from vuoro_contracts import ContractBundle, ContractError


ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "packages/vuoro-contracts/tests/fixtures/probe"
TEMPLATE = ROOT / "tools/contracts/templates/validated-client.py.template"
spec = importlib.util.spec_from_file_location("portable_contract_generator", ROOT / "scripts/contracts/generate.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


@pytest.mark.parametrize("contract_id", [
    "probe/v1",
    "vuoro/contracts/v2",
    "org.example/v3",
    "unicode/é",
    "unicode/e\u0301",
    "emoji/😀",
    "Upper/LOWER",
    "leading//empty",
    "contract/" + "x" * 512,
])
def test_package_name_uses_full_hash_of_exact_utf8_contract_id(contract_id):
    expected = "vuoro_contract_" + sha256(contract_id.encode("utf-8")).hexdigest() + "_client"
    assert generator.python_package_name(contract_id) == expected
    assert len(expected) == len("vuoro_contract_") + 64 + len("_client")


@pytest.mark.parametrize("contract_id", [None, 0, b"probe/v1", "", "\ud800"])
def test_package_name_refuses_invalid_identity(contract_id):
    with pytest.raises(ContractError):
        generator.python_package_name(contract_id)


def test_released_fixed_namespace_reproduces_two_contract_import_collision(tmp_path, monkeypatch):
    old_package = "vuoro_generated_contract_client"
    first, second = tmp_path / "first", tmp_path / "second"
    for root, value in ((first, "first"), (second, "second")):
        package = root / old_package
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(f"VALUE = {value!r}\n")
    monkeypatch.syspath_prepend(str(first))
    sys.modules.pop(old_package, None)
    try:
        assert importlib.import_module(old_package).VALUE == "first"
        monkeypatch.syspath_prepend(str(second))
        # Python reuses the already loaded fixed-name module even after a second
        # contract distribution is installed earlier on sys.path.
        assert importlib.import_module(old_package).VALUE == "first"
    finally:
        sys.modules.pop(old_package, None)


def test_hashed_packages_keep_relative_imports_independent(tmp_path, monkeypatch):
    package_names = [generator.python_package_name("probe/v1"),
                     generator.python_package_name("probe/v2")]
    roots = [tmp_path / "first", tmp_path / "second"]
    for root, package_name, value in zip(roots, package_names, ("first", "second")):
        package = root / package_name
        api = package / "api"
        api.mkdir(parents=True)
        (package / "__init__.py").write_text("from .api import read_value\n")
        (package / "model.py").write_text(f"VALUE = {value!r}\n")
        (api / "__init__.py").write_text("from .client import read_value\n")
        (api / "client.py").write_text("from ..model import VALUE\n\ndef read_value():\n    return VALUE\n")
    monkeypatch.syspath_prepend(str(roots[0]))
    monkeypatch.syspath_prepend(str(roots[1]))
    imported = [importlib.import_module(name) for name in package_names]
    assert [module.read_value() for module in imported] == ["first", "second"]
    assert package_names[0] != package_names[1]
    for name in package_names:
        sys.modules.pop(name, None)
        for child in list(sys.modules):
            if child.startswith(name + "."):
                sys.modules.pop(child, None)


def test_generated_attachment_uses_package_resources_not_checkout_ancestry():
    source = TEMPLATE.read_text()
    assert 'resources.files(__package__).joinpath("data")' in source
    assert "Path(__file__).resolve().parents" not in source


def _external_digest(root: Path) -> str:
    return sha256((root / "integrity.json").read_bytes()).hexdigest()


def _copy_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    shutil.copytree(FIXTURE, root)
    return root


def _reseal(root: Path) -> None:
    files = {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file() and path.name != "integrity.json"
    }
    (root / "integrity.json").write_text(json.dumps(
        {"schemaVersion": "vuoro-contract-integrity/v1", "files": files},
        sort_keys=True, separators=(",", ":"),
    ) + "\n")


def test_filesystem_resource_root_loads_authenticated_bundle():
    assert ContractBundle.load(FIXTURE, integrity_sha256=_external_digest(FIXTURE)).manifest["contractId"] == "probe/v1"


def test_zip_resource_root_loads_without_extracting_package_data(tmp_path):
    archive = tmp_path / "contract.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in FIXTURE.rglob("*"):
            if path.is_file():
                output.write(path, "bundle/" + path.relative_to(FIXTURE).as_posix())
    with zipfile.ZipFile(archive) as source:
        resource_root = zipfile.Path(source).joinpath("bundle")
        integrity = resource_root.joinpath("integrity.json").read_bytes()
        bundle = ContractBundle.load(resource_root, integrity_sha256=sha256(integrity).hexdigest())
        assert bundle.manifest["contractId"] == "probe/v1"
    # Simulate two installed SDK distributions in one ZIP import root. Each
    # namespace resolves its own resources without extracting package data.
    package_archive = tmp_path / "installed-sdks.zip"
    package_names = [generator.python_package_name("probe/v1"),
                     generator.python_package_name("probe/v2")]
    packages: list[Path] = []
    for index, package_name in enumerate(package_names):
        package = tmp_path / package_name
        packages.append(package)
        shutil.copytree(FIXTURE, package / "data")
        manifest_path = package / "data/operation-manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["contractId"] = f"probe/v{index + 1}"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
        _reseal(package / "data")
        (package / "__init__.py").write_text(
            "from hashlib import sha256\n"
            "from importlib import resources\n"
            "from vuoro_contracts import ContractBundle\n"
            "def load():\n"
            "    root = resources.files(__package__).joinpath('data')\n"
            "    digest = sha256(root.joinpath('integrity.json').read_bytes()).hexdigest()\n"
            "    return ContractBundle.load(root, integrity_sha256=digest)\n"
        )
    with zipfile.ZipFile(package_archive, "w") as output:
        for package_name, package in zip(package_names, packages):
            for path in package.rglob("*"):
                if path.is_file():
                    relative = path.relative_to(package).as_posix()
                    data = path.read_bytes()
                    if package_name == package_names[1] and relative == "data/README.txt":
                        data += b"tampered after sealing"
                    output.writestr(package_name + "/" + relative, data)

    sys.path.insert(0, str(package_archive))
    try:
        modules = [importlib.import_module(name) for name in package_names]
        first, second = [module.load for module in modules]
        assert first().manifest["contractId"] == "probe/v1"
        with pytest.raises(ContractError, match="integrity"):
            second()
    finally:
        sys.path.remove(str(package_archive))
        for name in package_names:
            sys.modules.pop(name, None)
            for child in list(sys.modules):
                if child.startswith(name + "."):
                    sys.modules.pop(child, None)


@pytest.mark.parametrize("mutation", [
    "external-digest",
    "extra-file",
    "bytecode-file",
    "missing-file",
    "unsafe-schema-path",
    "oversized-file",
    "total-byte-bound",
])
def test_resource_roots_refuse_integrity_path_and_size_violations(tmp_path, mutation):
    if mutation == "total-byte-bound":
        class ResourceFile:
            def __init__(self, name):
                self.name = name

            def is_dir(self):
                return False

            def is_file(self):
                return True

            def read_bytes(self):
                return b"x" * (16 * 1024 * 1024)

        class ResourceRoot:
            def is_dir(self):
                return True

            def iterdir(self):
                return iter(ResourceFile(f"part-{index}") for index in range(5))

        with pytest.raises(ContractError, match="integrity"):
            ContractBundle.load(ResourceRoot())
        return

    root = _copy_fixture(tmp_path)
    digest = _external_digest(root)
    if mutation == "external-digest":
        (root / "integrity.json").write_bytes((root / "integrity.json").read_bytes() + b" ")
    elif mutation == "extra-file":
        (root / "unexpected.bin").write_bytes(b"unlisted")
    elif mutation == "bytecode-file":
        cache = root / "schema/__pycache__"
        cache.mkdir()
        (cache / "schema.cpython-313.pyc").write_bytes(b"bytecode")
    elif mutation == "missing-file":
        (root / "openapi/probe.openapi.json").unlink()
    elif mutation == "unsafe-schema-path":
        manifest_path = root / "operation-manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["schemaBundle"]["path"] = "schema/../../outside.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
        _reseal(root)
        digest = _external_digest(root)
    elif mutation == "oversized-file":
        (root / "oversized.bin").write_bytes(b"x" * (16 * 1024 * 1024 + 1))
    with pytest.raises(ContractError):
        ContractBundle.load(root, integrity_sha256=digest)

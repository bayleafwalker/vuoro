from __future__ import annotations

from email.parser import BytesParser
from pathlib import Path
import tomllib
import zipfile


ROOT = Path(__file__).parents[1]
CLIENT_ROOT = ROOT / "packages" / "vuoro-client"
SHARED_PACKAGES = {
    "vuoro-schema-runtime": "vuoro_schema_runtime",
    "vuoro-adapter-kit": "vuoro_adapter_kit",
}
SHARED_PACKAGE_VERSIONS = {
    "vuoro-schema-runtime": "0.1.0",
    # 0.2.0 adds pure strict-view metadata and admission values. Legacy shims
    # and spec bytes remain unchanged. MI-1 repins the runtime to the owners
    # compatible immutable 0.2.0; source 0.2.2 adds root oneOf and leads its release pin.
    "vuoro-adapter-kit": "0.2.2",
}
FORBIDDEN_CLIENT_TERMS = {
    "adapter",
    "adapters",
    "asyncpg",
    "database",
    "domain_core",
    "migration",
    "migrations",
    "psycopg",
    "sqlalchemy",
}


def _project(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))["project"]


def _one_wheel(distribution: str) -> Path:
    wheels = sorted((ROOT / "dist" / distribution).glob("*.whl"))
    assert len(wheels) == 1, f"expected one built {distribution} wheel, found {wheels}"
    return wheels[0]


def test_client_declares_only_transport_dependencies() -> None:
    dependencies = _project(CLIENT_ROOT / "pyproject.toml")["dependencies"]
    normalized = "\n".join(dependencies).lower()
    assert not any(term in normalized for term in FORBIDDEN_CLIENT_TERMS)
    assert any(dependency.startswith("httpx") for dependency in dependencies)
    assert any(dependency.startswith("jsonschema") for dependency in dependencies)


def test_client_source_has_no_authority_assets() -> None:
    relative_files = {
        path.relative_to(CLIENT_ROOT).as_posix().lower()
        for path in CLIENT_ROOT.rglob("*")
        if path.is_file()
    }
    assert not any(
        term in relative_file
        for relative_file in relative_files
        for term in FORBIDDEN_CLIENT_TERMS
    )


def test_built_client_wheel_contains_only_transport_package() -> None:
    with zipfile.ZipFile(_one_wheel("vuoro-client")) as wheel:
        names = wheel.namelist()
        python_modules = [name for name in names if name.endswith(".py")]
        assert python_modules
        assert all(name.startswith("vuoro_client/") for name in python_modules)
        assert not any(
            term in name.lower() for name in names for term in FORBIDDEN_CLIENT_TERMS
        )

        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = BytesParser().parsebytes(wheel.read(metadata_name))
        runtime_requires = metadata.get_all("Requires-Dist", failobj=[])
        normalized = "\n".join(runtime_requires).lower()
        assert not any(term in normalized for term in FORBIDDEN_CLIENT_TERMS)


def test_client_and_service_are_distinct_wheels() -> None:
    client = _one_wheel("vuoro-client")
    service = _one_wheel("vuoro-service")
    assert client.name.startswith("vuoro_client-")
    assert service.name.startswith("vuoro_service-")


def test_shared_packages_are_stdlib_only_and_have_isolated_wheel_boundaries() -> None:
    for distribution, module in SHARED_PACKAGES.items():
        project = _project(ROOT / "packages" / distribution / "pyproject.toml")
        # Pinned per package rather than as one literal, because these versions
        # are what the composition pins: bumping one here without repinning
        # adapter-pins.json ships a release whose manifest names a wheel that no
        # longer matches the source. During a release the source leads the pin
        # for exactly as long as it takes the tag to build, which is why this is
        # a table and not a comparison against the manifest.
        assert project["version"] == SHARED_PACKAGE_VERSIONS[distribution]
        assert project["requires-python"] == ">=3.11"
        assert project["dependencies"] == []
        with zipfile.ZipFile(_one_wheel(distribution)) as wheel:
            names = wheel.namelist()
            dist_info = next(name for name in names if name.endswith(".dist-info/METADATA")).split("/", 1)[0]
            assert any(name.startswith(f"{module}/") for name in names)
            assert all(
                name.startswith((f"{module}/", f"{distribution.replace('-', '_')}-"))
                or name.startswith(f"{dist_info}/")
                for name in names
            )
            metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
            metadata = BytesParser().parsebytes(wheel.read(metadata_name))
            requirements = metadata.get_all("Requires-Dist", failobj=[])
            assert all("extra == 'test'" in requirement for requirement in requirements)


def test_candidate_kit_wheel_contains_pure_admission_values_without_fake_owner() -> None:
    import ast
    with zipfile.ZipFile(_one_wheel("vuoro-adapter-kit")) as wheel:
        names = wheel.namelist()
        assert not any("fake" in name.lower() or (name.startswith("tests/") or "/tests/" in name) for name in names)
        for module in ("admission", "catalog_v2"):
            source = wheel.read(f"vuoro_adapter_kit/{module}.py").decode()
            tree = ast.parse(source)
            imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            imports |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            import sys
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level:
                    assert node.level == 1 and node.module in {"catalog", "admission"}
                elif isinstance(node, ast.ImportFrom):
                    assert (node.module or "").split(".", 1)[0] in sys.stdlib_module_names
                elif isinstance(node, ast.Import):
                    assert all(alias.name.split(".", 1)[0] in sys.stdlib_module_names for alias in node.names)


def test_contract_runtime_is_pure_and_wheel_has_metadata_formats(tmp_path) -> None:
    import ast
    import json
    import shutil
    import subprocess
    import sys
    package = ROOT / "packages/vuoro-contracts"
    project = _project(package / "pyproject.toml")
    assert project["dependencies"] == ["jsonschema==4.26.0", "referencing==0.37.0"]
    forbidden = {"vuoro_service", "vuoro_client", "sovite", "sqlite3", "psycopg", "asyncpg", "httpx", "requests", "socket", "subprocess"}
    for path in (package/"src").rglob("*.py"):
        tree=ast.parse(path.read_text())
        imports={node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node,ast.ImportFrom) and node.module}
        imports.update(alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node,ast.Import) for alias in node.names)
        assert not imports & forbidden
        assert "experiments" not in path.read_text()
    wheel_path=_one_wheel("vuoro-contracts")
    with zipfile.ZipFile(wheel_path) as wheel:
        names=wheel.namelist()
        assert "vuoro_contracts/formats/operation-manifest.schema.json" in names
        assert "vuoro_contracts/formats/integrity.schema.json" in names
        assert all(name.startswith(("vuoro_contracts/","vuoro_contracts-")) for name in names)
    owner_assets=tmp_path/"owner-assets"
    shutil.copytree(package/"tests/fixtures/probe",owner_assets)
    # -I plus wheel-only insertion proves the wheel does not import source/tests.
    # Qualified jsonschema/referencing must already be installed by the root gate.
    program="\n".join([
        "import sys",f"sys.path.insert(0,{str(wheel_path)!r})",
        "from hashlib import sha256", "from pathlib import Path",
        "from vuoro_contracts import ContractBundle",f"root=Path({str(owner_assets)!r})",
        "c=ContractBundle.load(root,integrity_sha256=sha256((root/'integrity.json').read_bytes()).hexdigest())",
        "v={'state':'ready','revision':'9007199254740995'}",
        "assert c.validate_response('submit',200,v) is v",
    ])
    result=subprocess.run([sys.executable,"-I","-c",program],cwd=tmp_path,text=True,capture_output=True)
    assert result.returncode==0,result.stderr

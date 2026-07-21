from __future__ import annotations

from email.parser import BytesParser
from pathlib import Path
import tomllib
import zipfile


ROOT = Path(__file__).parents[1]
CLIENT_ROOT = ROOT / "packages" / "vuoro-client"
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

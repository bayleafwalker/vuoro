import pytest

from vuoro_schema_runtime import Migration, SchemaRuntime, SchemaCompatibilityError


def test_migrations_compute_and_validate_checksums() -> None:
    migration = Migration(1, "initial", "CREATE TABLE __SCHEMA__.thing (id integer)")
    assert len(migration.sha256) == 64
    with pytest.raises(ValueError, match="sha256"):
        Migration(1, "initial", "SELECT 1", "0" * 64)


def test_runtime_rejects_invalid_compatibility_range() -> None:
    with pytest.raises(ValueError, match="compatibility range"):
        SchemaRuntime(
            domain_api_version="audit/v1",
            migrations=[("initial", "SELECT 1")],
            minimum_schema_version=2,
        )

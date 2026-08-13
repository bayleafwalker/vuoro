from dataclasses import FrozenInstanceError

import pytest

from vuoro_schema_runtime import (
    MigrationDriftError,
    compatibility_report,
    identifier,
    migration_asset,
    quote_identifier,
    render_schema_sql,
    sha256_text,
    validate_contiguous_migrations,
)


def test_assets_are_frozen_and_content_addressed() -> None:
    asset = migration_asset(1, "initial", "CREATE TABLE __SCHEMA__.thing (id integer)")
    assert asset.sha256 == sha256_text(asset.sql)
    with pytest.raises(FrozenInstanceError):
        asset.name = "changed"
    with pytest.raises(MigrationDriftError):
        type(asset)(asset.version, asset.name, asset.sql, "0" * 64)


def test_migration_validation_rendering_and_identifiers() -> None:
    assets = validate_contiguous_migrations([
        migration_asset(1, "initial", "CREATE SCHEMA __SCHEMA__"),
        migration_asset(2, "second", "CREATE TABLE __SCHEMA__.thing (id integer)"),
    ])
    assert [asset.version for asset in assets] == [1, 2]
    assert render_schema_sql(assets[0].sql, "central_schema") == 'CREATE SCHEMA "central_schema"'
    assert quote_identifier("central_schema") == '"central_schema"'
    with pytest.raises(ValueError):
        identifier("bad-name")


def test_compatibility_checks_every_ledger_row_not_only_max_version() -> None:
    assets = [migration_asset(1, "initial", "SELECT 1"), migration_asset(2, "second", "SELECT 2")]
    good = [(1, assets[0].name, assets[0].sha256), (2, assets[1].name, assets[1].sha256)]
    report = compatibility_report(
        assets, good, schema="central", domain_api_version="audit/v1",
        minimum_schema_version=2, maximum_schema_version=2,
        current_role="runtime", configured_role="runtime",
    )
    assert report.compatible is True
    broken = [(1, assets[0].name, "0" * 64), (2, assets[1].name, assets[1].sha256)]
    report = compatibility_report(
        assets, broken, schema="central", domain_api_version="audit/v1",
        minimum_schema_version=2, maximum_schema_version=2,
    )
    assert report.compatible is False
    assert "migration_ledger_drift" in report.reasons

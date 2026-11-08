import json
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

import jsonschema
import pytest

from vuoro_service.tenant_schema import UnknownServiceVersionError, lookup_tenant_schema


PACKAGE = files("vuoro_service")
ROOT = Path(__file__).resolve().parents[3]


def _mapping():
    return json.loads(PACKAGE.joinpath("tenant-runtime-schema-map.json").read_text(encoding="utf-8"))


def test_tenant_schema_map_matches_its_checked_in_json_schema():
    instance = _mapping()
    schema = json.loads(PACKAGE.joinpath("tenant-runtime-schema-map.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(instance, schema)


def test_seed_rows_and_migration_flags_match_documented_release_evidence():
    rows = _mapping()["releases"]
    assert [row["service_version"] for row in rows] == ["0.1.99", "0.1.102", "0.1.104", "0.1.105"]
    assert [row["migration_required_from_previous"] for row in rows] == [False, True, True, True]
    for row, previous in zip(rows[1:], rows[:-1]):
        assert row["migration_required_from_previous"] is (row["work_schema_target"] != previous["work_schema_target"])
    assert [row["evidence_source"]["kind"] for row in rows] == [
        "changelog_prose", "changelog_prose", "architecture_prose",
        "adapter_pin_and_compatibility_constants",
    ]


def test_current_row_tracks_adapter_pin_and_pinned_owner_compatibility_constants():
    pins = json.loads((ROOT / "packages" / "vuoro-service" / "composition" / "adapter-pins.json").read_text(encoding="utf-8"))
    work_lock = next(lock for lock in pins["release_locks"] if lock["lock_id"] == "work-adapter")
    row = lookup_tenant_schema("0.1.105")

    assert row["owner_release"] == {"distribution": "sprintctl", "version": work_lock["distribution_version"]}
    assert version("sprintctl") == work_lock["distribution_version"]
    from sprintctl import pg_migrations

    expected = {
        "work_schema_target": int(getattr(pg_migrations, "CURRENT_SCHEMA_VERSION", pg_migrations.MAXIMUM_SCHEMA_VERSION)),
        "lowest_supported_schema": int(pg_migrations.MINIMUM_SCHEMA_VERSION),
        "accepted_schema_range": {
            "minimum": int(pg_migrations.MINIMUM_SCHEMA_VERSION),
            "maximum": int(pg_migrations.MAXIMUM_SCHEMA_VERSION),
        },
    }
    assert {key: row[key] for key in expected} == expected


@pytest.mark.parametrize("release", ["0.1.105", "vuoro-service-v0.1.105@sha256:" + "a" * 64])
def test_lookup_accepts_version_and_pinned_release_form(release):
    row = lookup_tenant_schema(release)
    assert row["service_version"] == "0.1.105"
    assert row["work_schema_target"] == 34


def test_lookup_explains_unknown_and_malformed_versions():
    with pytest.raises(UnknownServiceVersionError, match="no tenant work-schema mapping.*0.1.106"):
        lookup_tenant_schema("0.1.106")
    with pytest.raises(ValueError, match="expected a service version"):
        lookup_tenant_schema("vuoro-service-v0.1.105@sha256:not-a-digest")

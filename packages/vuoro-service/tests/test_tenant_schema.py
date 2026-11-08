import json
from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
from pathlib import Path
import os
import subprocess

import jsonschema
import pytest

from vuoro_service.tenant_schema import UnknownServiceVersionError, lookup_tenant_schema

PACKAGE = files("vuoro_service")
ROOT = Path(__file__).resolve().parents[3]

def _mapping():
    return json.loads(PACKAGE.joinpath("tenant-runtime-schema-map.json").read_text(encoding="utf-8"))

def _schema():
    return json.loads(PACKAGE.joinpath("tenant-runtime-schema-map.schema.json").read_text(encoding="utf-8"))

def test_tenant_schema_map_matches_its_checked_in_json_schema():
    jsonschema.validate(_mapping(), _schema())

def test_releases_are_complete_ordered_and_migration_flags_compare_adjacent_releases():
    rows = _mapping()["releases"]
    expected = [f"0.1.{n}" for n in range(94, 106)]
    assert [r["service_version"] for r in rows] == expected
    for row, previous in zip(rows[1:], rows[:-1]):
        if row["work_schema_target"] is not None and previous["work_schema_target"] is not None:
            assert row["migration_required_from_previous"] is (row["work_schema_target"] != previous["work_schema_target"])
        else:
            assert row["migration_required_from_previous"] is None
    assert lookup_tenant_schema("0.1.104")["migration_required_from_previous"] is False
    assert lookup_tenant_schema("0.1.103")["migration_required_from_previous"] is True
    assert lookup_tenant_schema("0.1.105")["migration_required_from_previous"] is True

def test_current_service_row_matches_installed_pinned_owner():
    try:
        service_version = version("vuoro-service")
    except PackageNotFoundError:
        import tomllib
        service_version = tomllib.loads((ROOT / "packages/vuoro-service/pyproject.toml").read_text())["project"]["version"]
    row = lookup_tenant_schema(service_version)
    pins_path = ROOT / "packages/vuoro-service/composition/adapter-pins.json"
    pins = json.loads(pins_path.read_text())
    lock = next(x for x in pins["release_locks"] if x["lock_id"] == "work-adapter")
    assert row["owner_release"] == {"distribution": "sprintctl", "version": lock["distribution_version"]}
    try:
        installed = version("sprintctl")
        from sprintctl import pg_migrations
    except (PackageNotFoundError, ImportError):
        if os.getenv("REQUIRE_PINNED_OWNER") == "1":
            pytest.fail("pinned Sprintctl owner is required for this drift check")
        pytest.skip("pinned work owner wheel is not installed")
    assert installed == lock["distribution_version"]
    expected = (int(getattr(pg_migrations, "CURRENT_SCHEMA_VERSION", pg_migrations.MAXIMUM_SCHEMA_VERSION)),
                int(pg_migrations.MINIMUM_SCHEMA_VERSION), int(pg_migrations.MAXIMUM_SCHEMA_VERSION))
    assert (row["work_schema_target"], row["lowest_supported_schema"], row["accepted_schema_range"]["maximum"]) == expected

def test_historic_rows_match_adapter_pin_history_where_available():
    # These triples were independently verified from the released owner wheels;
    # adapter-pins history below verifies that each service release selected that owner.
    verified = {
        "0.1.98": (28, 28, 28), "0.1.99": (28, 28, 28),
        "0.1.100": (29, 29, 29), "0.1.101": (29, 29, 29),
        "0.1.102": (30, 30, 30), "0.1.103": (31, 31, 31),
        "0.1.104": (31, 31, 31), "0.1.105": (34, 32, 34),
    }
    rows = {row["service_version"]: row for row in _mapping()["releases"]}
    for release, triple in verified.items():
        row = rows[release]
        assert (row["work_schema_target"], row["lowest_supported_schema"], row["accepted_schema_range"]["maximum"]) == triple
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    for row in _mapping()["releases"]:
        ref = row["evidence_source"]["reference"]
        if row["evidence_source"]["kind"] != "adapter_pin_and_compatibility_constants":
            continue
        proc = subprocess.run(["git", "log", "--all", "--format=%H", "--", "packages/vuoro-service/composition/adapter-pins.json"], cwd=ROOT, text=True, capture_output=True)
        if proc.returncode:
            pytest.skip("git history unavailable")
        # Pin commits are preserved in evidence references; inspect the exact service tag snapshot.
        tag = "vuoro-service-v" + row["service_version"]
        raw = subprocess.run(["git", "show", f"{tag}:packages/vuoro-service/composition/adapter-pins.json"], cwd=ROOT, text=True, capture_output=True)
        if raw.returncode:
            continue
        pins = json.loads(raw.stdout)
        lock = next(x for x in pins["release_locks"] if x["lock_id"] == "work-adapter")
        assert lock["distribution_version"] == row["owner_release"]["version"], ref
        # Historical owner constants are independently established by the recorded release review.
        # Without local wheel bytes, retain this comparison at pin identity only.

@pytest.mark.parametrize("bad", [{}, {"schema_version":"vuoro-tenant-runtime-schema-map/v1","releases":[None]}])
def test_malformed_maps_fail_schema_validation(bad):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, _schema())

def test_lookup_accepts_version_and_pinned_release_form():
    assert lookup_tenant_schema("0.1.105")["work_schema_target"] == 34
    assert lookup_tenant_schema("vuoro-service-v0.1.105@sha256:" + "a" * 64)["service_version"] == "0.1.105"

def test_lookup_explains_unknown_and_malformed_versions():
    with pytest.raises(UnknownServiceVersionError, match="no tenant work-schema mapping.*0.1.106"):
        lookup_tenant_schema("0.1.106")
    with pytest.raises(ValueError, match="expected a service version"):
        lookup_tenant_schema("vuoro-service-v0.1.105@sha256:not-a-digest")

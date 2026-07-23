from __future__ import annotations

import json
from pathlib import Path

import pytest

from vuoro_service.environment_record import EnvironmentRecordError, load_environment_record


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "record.json"
    path.write_text(json.dumps(payload))
    return path


def _valid_record(**overrides: object) -> dict:
    payload = {
        "schema_version": "environment-record/v1",
        "id": "vuoro-shared",
        "environment_class": "production",
        "revision": 1,
        "roles": ["primary-production-authority"],
        "constraints": ["production-identities-only", "separate-from-vuoro-dev"],
        "capabilities": ["work-catalog"],
        "runbook_refs": ["/docs/runbooks/vuoro-workstation-cutover.md"],
        "identity_bindings": [{"principal": "workstation-vuoro", "roles": ["client"]}],
    }
    payload.update(overrides)
    return payload


def test_loads_bounded_non_secret_fields_only(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid_record())
    record = load_environment_record(path)
    assert record.id == "vuoro-shared"
    assert record.environment_class == "production"
    assert record.constraints == (
        "production-identities-only",
        "separate-from-vuoro-dev",
    )
    assert record.runbook_refs == ("/docs/runbooks/vuoro-workstation-cutover.md",)
    assert not hasattr(record, "roles")
    assert not hasattr(record, "capabilities")
    assert not hasattr(record, "identity_bindings")


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(EnvironmentRecordError, match="not found"):
        load_environment_record(tmp_path / "missing.json")


def test_wrong_schema_version_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid_record(schema_version="environment-record/v0"))
    with pytest.raises(EnvironmentRecordError, match="schema_version"):
        load_environment_record(path)


def test_missing_required_field_rejected(tmp_path: Path) -> None:
    payload = _valid_record()
    del payload["constraints"]
    path = _write(tmp_path, payload)
    with pytest.raises(EnvironmentRecordError, match="missing required field"):
        load_environment_record(path)


def test_unknown_environment_class_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid_record(environment_class="staging"))
    with pytest.raises(EnvironmentRecordError, match="environment_class"):
        load_environment_record(path)


@pytest.mark.parametrize(
    "key", ["secret_token", "api_credential", "db_password", "auth_token"]
)
def test_disallowed_secret_looking_keys_rejected(tmp_path: Path, key: str) -> None:
    payload = _valid_record()
    payload[key] = "should-never-be-here"
    path = _write(tmp_path, payload)
    with pytest.raises(EnvironmentRecordError, match="disallowed key"):
        load_environment_record(path)


def test_malformed_json_rejected(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_text("{not json")
    with pytest.raises(EnvironmentRecordError, match="not valid JSON"):
        load_environment_record(path)

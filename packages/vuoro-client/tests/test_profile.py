from __future__ import annotations

import json

import pytest

from vuoro_client import Profile, ProfileError, load_profile


def _document(**overrides):
    document = {
        "schema_version": "vuoro-client-profile/v1",
        "id": "workstation-vuoro-shared",
        "revision": 3,
        "source_environment_id": "workstation-linux",
        "target": {
            "environment_id": "vuoro-shared",
            "environment_class": "production",
            "endpoint": "https://vuoro.example",
        },
        "credential_ref": "file:~/.config/vuoro/credential",
        "required_authorities": ["work:read"],
        "production_endpoint_denied": False,
    }
    document.update(overrides)
    return document


def test_load_profile_freezes_transport_projection(tmp_path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")
    assert load_profile(path) == Profile(
        name="workstation-vuoro-shared",
        endpoint="https://vuoro.example",
        credential_ref="file:~/.config/vuoro/credential",
        expected_environment="vuoro-shared",
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema_version": "other"}, "schema_version"),
        ({"credential_ref": "env:TOKEN"}, "credential_ref"),
        ({"unexpected": True}, "Additional properties"),
    ],
)
def test_load_profile_rejects_contract_drift(tmp_path, change, message) -> None:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(_document(**change)), encoding="utf-8")
    with pytest.raises(ProfileError, match=message):
        load_profile(path)


def test_load_profile_rejects_self_contradictory_production_target(tmp_path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(_document(production_endpoint_denied=True)), encoding="utf-8")
    with pytest.raises(ProfileError, match="production target is denied"):
        load_profile(path)

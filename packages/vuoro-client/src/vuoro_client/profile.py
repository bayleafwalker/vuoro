"""Loading and validation for versioned Vuoro client profiles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

PROFILE_SCHEMA_VERSION = "vuoro-client-profile/v1"


class ProfileError(ValueError):
    """A client profile could not be read or did not satisfy its contract."""


@dataclass(frozen=True)
class Profile:
    name: str
    endpoint: str
    credential_ref: str
    expected_environment: str | None = None


def _profile_schema() -> dict[str, object]:
    resource = files("vuoro_client").joinpath("profile.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def load_profile(path: str | Path) -> Profile:
    """Load and reduce a ``vuoro-client-profile/v1`` document."""
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileError(f"cannot read Vuoro client profile {source}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProfileError(f"invalid JSON in Vuoro client profile {source}: {exc}") from exc

    errors = sorted(
        Draft202012Validator(_profile_schema(), format_checker=FormatChecker()).iter_errors(raw),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise ProfileError(f"invalid Vuoro client profile {source} at {location}: {error.message}")

    target = raw["target"]
    if target["environment_class"] == "production" and raw["production_endpoint_denied"]:
        raise ProfileError(f"invalid Vuoro client profile {source}: production target is denied by profile")
    return Profile(
        name=raw["id"], endpoint=target["endpoint"],
        credential_ref=raw["credential_ref"],
        expected_environment=target["environment_id"],
    )

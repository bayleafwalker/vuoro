from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "docs/architecture/work-link-v1.schema.json"
FIXTURES = Path(__file__).parent / "fixtures/work-link-v1"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
URI_PREFIX = "vuoro-work://link/v1/"


def _canonical_payload(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _uri(value: dict) -> str:
    payload = base64.urlsafe_b64encode(_canonical_payload(value)).rstrip(b"=")
    return URI_PREFIX + payload.decode("ascii")


def _uri_from_bytes(raw: bytes) -> str:
    payload = base64.urlsafe_b64encode(raw).rstrip(b"=")
    return URI_PREFIX + payload.decode("ascii")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_uri(uri: str) -> dict:
    if not uri.startswith(URI_PREFIX):
        raise ValueError("work link URI prefix is not canonical")
    if len(uri.encode("ascii")) > 4096:
        raise ValueError("work link exceeds the URI size limit")
    parts = urlsplit(uri)
    if (
        parts.scheme != "vuoro-work"
        or parts.netloc != "link"
        or parts.path.count("/") != 2
        or not parts.path.startswith("/v1/")
        or parts.query
        or parts.fragment
        or parts.username is not None
        or parts.password is not None
        or parts.port is not None
    ):
        raise ValueError("work link URI has a noncanonical locator shape")
    encoded = parts.path.removeprefix("/v1/")
    if not encoded or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for ch in encoded):
        raise ValueError("work link payload is not unpadded base64url")
    padded = encoded + "=" * (-len(encoded) % 4)
    raw = base64.b64decode(padded, altchars=b"-_", validate=True)
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != encoded:
        raise ValueError("work link base64url is not canonical")
    decoded = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_reject_duplicate_keys)
    if type(decoded) is not dict or _canonical_payload(decoded) != raw:
        raise ValueError("work link JSON payload is not canonical")
    VALIDATOR.validate(decoded)
    checkpoint = decoded.get("owner_checkpoint")
    if checkpoint is not None and (
        checkpoint["repo_id"] != decoded["repo_id"]
        or checkpoint["item_id"] != decoded["item_id"]
    ):
        raise ValueError("owner checkpoint does not match the linked work item")
    return decoded


def _maker_checkpoint(response: dict, *, repo_id: str, item_id: int, run_id: str) -> dict:
    """Contract oracle: reject owner data before making a link from it."""
    if type(response) is not dict or set(response) != {"repo_id", "delta"}:
        raise ValueError("owner response envelope is incomplete")
    if response["repo_id"] != repo_id or type(response["delta"]) is not dict:
        raise ValueError("owner repository or delta is inconsistent")
    delta = response["delta"]
    if delta.get("schema") != "sprintctl-work-continuation-delta/v1":
        raise ValueError("owner response schema is unsupported")
    authorization = delta.get("authorization_scope")
    current = delta.get("current")
    work = delta.get("work")
    checkpoint_fields = set(SCHEMA["$defs"]["owner_checkpoint"]["required"])
    if (
        type(authorization) is not dict
        or authorization.get("run_id") != run_id
        or authorization.get("run_owned") is not True
        or type(current) is not dict
        or set(current) != checkpoint_fields
        or current.get("repo_id") != repo_id
        or current.get("item_id") != item_id
        or type(work) is not dict
        or work.get("item_id") != item_id
    ):
        raise ValueError("owner response does not bind the caller run and item")
    VALIDATOR.validate(
        {
            "schema": "vuoro-work-link/v1",
            "workspace_id": "maker-workspace",
            "repo_id": repo_id,
            "item_id": item_id,
            "predecessor_run_id": run_id,
            "owner_checkpoint": current,
        }
    )
    return current


@pytest.mark.parametrize(
    "fixture_name",
    ["without-checkpoint.json", "with-checkpoint.json"],
)
def test_synthetic_work_link_fixtures_match_schema_and_canonical_uri(fixture_name):
    fixture = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    VALIDATOR.validate(fixture)
    uri = _uri(fixture)
    assert uri.startswith(URI_PREFIX)
    assert _read_uri(uri) == fixture
    assert len(uri.encode("ascii")) <= 4096


@pytest.mark.parametrize(
    "mutate",
    [
        lambda link: link.pop("workspace_id"),
        lambda link: link.update(item_id=0),
        lambda link: link.update(predecessor_run_id="run_bad"),
        lambda link: link.update(principal_id="github:1:0"),
        lambda link: link.update(access_token="secret"),
        lambda link: link.update(lease_id="lease_123"),
        lambda link: link.update(transcript="private notes"),
        lambda link: link.update(decision_bytes="private bytes"),
        lambda link: link.update(owner_checkpoint={"repo_id": "r"}),
    ],
)
def test_work_link_schema_rejects_missing_invalid_or_authority_bearing_fields(mutate):
    link = json.loads((FIXTURES / "without-checkpoint.json").read_text())
    mutate(link)
    with pytest.raises(jsonschema.ValidationError):
        VALIDATOR.validate(link)


@pytest.mark.parametrize(
    "change",
    [
        lambda link: link["owner_checkpoint"].update(repo_id="another/repo"),
        lambda link: link["owner_checkpoint"].update(item_id=28),
    ],
)
def test_checkpoint_must_match_outer_repo_and_item(change):
    link = json.loads((FIXTURES / "with-checkpoint.json").read_text())
    change(link)
    VALIDATOR.validate(link)
    with pytest.raises(ValueError, match="checkpoint"):
        _read_uri(_uri(link))


@pytest.mark.parametrize(
    "uri",
    [
        "https://example.test/work",
        URI_PREFIX + "not!base64",
        URI_PREFIX + "e30=",
        URI_PREFIX + "e30?token=secret",
        URI_PREFIX + "e30#fragment",
        "vuoro-work://user@link/v1/e30",
        "vuoro-work://link:443/v1/e30",
        "VUORO-WORK://link/v1/e30",
    ],
)
def test_noncanonical_or_remote_locator_uris_are_rejected(uri):
    with pytest.raises((ValueError, UnicodeError, json.JSONDecodeError)):
        _read_uri(uri)


@pytest.mark.parametrize(
    "change",
    [
        lambda link: link.update(workspace_id=link["workspace_id"] + "\n"),
        lambda link: link.update(repo_id=link["repo_id"] + "\n"),
        lambda link: link.update(predecessor_run_id=link["predecessor_run_id"] + "\n"),
        lambda link: link["owner_checkpoint"].update(repo_id=link["repo_id"] + "\n"),
        lambda link: link["owner_checkpoint"].update(work_edit_revision="edit:17\n"),
        lambda link: link["owner_checkpoint"].update(status_revision="status:4\n"),
        lambda link: link["owner_checkpoint"].update(current_release_digest="a" * 64 + "\n"),
        lambda link: link["owner_checkpoint"].update(current_decision_digest="sha256:" + "a" * 64 + "\n"),
        lambda link: link["owner_checkpoint"].update(current_reference_set_digest="sha256:" + "b" * 64 + "\n"),
    ],
)
def test_every_identifier_revision_and_digest_pattern_rejects_trailing_newline(change):
    link = json.loads((FIXTURES / "with-checkpoint.json").read_text())
    change(link)
    with pytest.raises(jsonschema.ValidationError):
        VALIDATOR.validate(link)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema":"vuoro-work-link/v1","schema":"vuoro-work-link/v1"}',
        json.dumps(
            json.loads((FIXTURES / "without-checkpoint.json").read_text()),
            ensure_ascii=False,
            sort_keys=False,
            separators=(",", ":"),
        ).encode("utf-8"),
        json.dumps(
            json.loads((FIXTURES / "without-checkpoint.json").read_text()),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8"),
        b"\xff",
    ],
)
def test_uri_decoder_refuses_duplicate_keys_noncanonical_json_and_invalid_utf8(raw):
    with pytest.raises((ValueError, UnicodeError, json.JSONDecodeError)):
        _read_uri(_uri_from_bytes(raw))


def test_uri_decoder_enforces_4096_ascii_byte_limit():
    oversized = URI_PREFIX + ("a" * 4096)
    with pytest.raises(ValueError, match="size limit"):
        _read_uri(oversized)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda response: response.update(repo_id="other/repository"),
        lambda response: response["delta"].update(schema="wrong/v1"),
        lambda response: response["delta"]["authorization_scope"].update(run_id="another-run"),
        lambda response: response["delta"]["authorization_scope"].update(run_owned=False),
        lambda response: response["delta"]["current"].update(repo_id="other/repository"),
        lambda response: response["delta"]["current"].update(item_id=28),
        lambda response: response["delta"]["work"].update(item_id=28),
        lambda response: response["delta"].pop("current"),
    ],
)
def test_maker_refuses_owner_response_before_link_creation(mutation):
    item = json.loads((FIXTURES / "with-checkpoint.json").read_text())
    checkpoint = item["owner_checkpoint"]
    response = {
        "repo_id": item["repo_id"],
        "delta": {
            "schema": "sprintctl-work-continuation-delta/v1",
            "authorization_scope": {
                "repo_id": item["repo_id"],
                "run_id": item["predecessor_run_id"],
                "run_owned": True,
            },
            "current": checkpoint,
            "work": {"item_id": item["item_id"]},
        },
    }
    mutation(response)
    with pytest.raises(ValueError):
        _maker_checkpoint(
            response,
            repo_id=item["repo_id"],
            item_id=item["item_id"],
            run_id=item["predecessor_run_id"],
        )


def test_maker_copies_only_exact_valid_owner_checkpoint():
    item = json.loads((FIXTURES / "with-checkpoint.json").read_text())
    response = {
        "repo_id": item["repo_id"],
        "delta": {
            "schema": "sprintctl-work-continuation-delta/v1",
            "authorization_scope": {
                "repo_id": item["repo_id"],
                "run_id": item["predecessor_run_id"],
                "run_owned": True,
            },
            "current": item["owner_checkpoint"],
            "work": {"item_id": item["item_id"]},
        },
    }
    assert _maker_checkpoint(
        response,
        repo_id=item["repo_id"],
        item_id=item["item_id"],
        run_id=item["predecessor_run_id"],
    ) == item["owner_checkpoint"]


def test_schema_has_no_remote_references_or_authority_fields():
    serialized = json.dumps(SCHEMA)
    assert SCHEMA["$id"] == "urn:vuoro:work-link:v1"
    assert "$ref" not in serialized.replace('"$ref": "#/$defs/owner_checkpoint"', "")
    assert SCHEMA["additionalProperties"] is False
    assert SCHEMA["$defs"]["owner_checkpoint"]["additionalProperties"] is False

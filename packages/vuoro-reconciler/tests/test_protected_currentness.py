"""Public state is DATA; both current work and selected release are compared."""

import copy
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from vuoro_reconciler.protected_copy_codec import ProtectedDataRefused
from vuoro_reconciler.protected_currentness import (
    decode_current_basis,
    evaluate_currentness,
)
from test_protected_copy_codec import canonical, decode, sha


def basis(effect=None, *, status="pending", state="proposed"):
    """Mechanically synthesized matching data, not an actual native response."""
    effect = decode() if effect is None else effect
    locator = json.loads(effect.locator_utf8)
    namespace = json.loads(effect.namespace_utf8)
    params = json.loads(effect.parameters_utf8)
    run = json.loads(effect.run_document_utf8)["native_run"]
    work = json.loads(effect.work_document_utf8)
    release = json.loads(effect.release_document_utf8)
    return {
        "schema": "sprintctl-effect-basis/v1",
        "repo_id": namespace["repo_id"],
        "workspace_id": namespace["workspace_id"],
        "intent": {
            "intent_id": locator["intent_id"],
            "revision": locator["intent_revision"],
            "state": state,
            "canonical_intent_digest": effect.legacy_content_digest,
            "release_digest": release["release_digest"],
            **params,
            "run_id": effect.source_run,
        },
        "run": {**run, "workspace_id": namespace["workspace_id"]},
        "work": {
            "work_item_id": work["work_item_id"],
            "aggregate_uuid": release["aggregate_uuid"],
            "status": status,
            "edit_revision": work["edit_revision"],
            "status_revision": "opaque-current-status-token",
            "item_revision": release["item_revision"],
        },
        "release": {
            "work_item_id": release["work_item_id"],
            "item_revision": release["item_revision"],
            "release_digest": release["release_digest"],
            "acceptance_contract": release["native_acceptance_contract"],
            "context_refs": release["native_context_refs"],
        },
    }


def result(value, effect=None):
    effect = decode() if effect is None else effect
    return evaluate_currentness(
        effect, decode_current_basis(canonical(value), effect=effect)
    )


@pytest.mark.parametrize("state", ["proposed", "accepted", "rejected", "applied"])
@pytest.mark.parametrize(
    "status,expected",
    [
        ("pending", "READY"),
        ("active", "READY"),
        ("blocked", "DEFERRED"),
        ("done", "REFUSED"),
    ],
)
def test_all_recognized_intent_states_data_and_actual_work_status_matrix(
    state, status, expected
):
    assert result(basis(state=state, status=status)).status == expected


def test_current_description_edit_keeps_old_selected_release_but_refuses():
    value = basis()
    original = copy.deepcopy(value["release"])
    old = value["work"]["edit_revision"]
    value["work"]["edit_revision"] = (
        old.rsplit("@sha256:", 1)[0] + "@sha256:" + "0" * 64
    )
    revise = value["work"]["item_revision"].rsplit("@revise:", 1)[1]
    value["work"]["item_revision"] = (
        value["work"]["edit_revision"] + "@revise:" + revise
    )
    parsed = decode_current_basis(canonical(value), effect=decode())
    assert json.loads(parsed.release_utf8) == original
    assert result(value).status == "REFUSED"


def test_current_revise_and_selected_release_are_independently_compared():
    value = basis()
    value["work"]["item_revision"] = value["work"]["edit_revision"] + "@revise:999"
    assert result(value).status == "REFUSED"
    value = basis()
    release = value["release"]
    release["item_revision"] = value["work"]["edit_revision"] + "@revise:999"
    release["release_digest"] = sha(
        canonical(
            {
                "aggregate_uuid": value["work"]["aggregate_uuid"],
                "item_revision": release["item_revision"],
                "acceptance_contract": release["acceptance_contract"],
                "context_refs": release["context_refs"],
            }
        ),
        False,
    )
    assert result(value).status == "REFUSED"
    assert result({**basis(), "release": None}).status == "REFUSED"


@pytest.mark.parametrize(
    "part,key",
    [
        ("intent", "state"),
        ("work", "status"),
        ("work", "item_revision"),
        ("release", "release_digest"),
    ],
)
def test_unknown_or_corrupt_typed_data_fixed_refusal(part, key):
    value = basis()
    value[part][key] = "unknown"
    with pytest.raises(ProtectedDataRefused, match="^effect-basis-invalid$") as error:
        decode_current_basis(canonical(value), effect=decode())
    assert error.value.__cause__ is None and error.value.__context__ is None


@pytest.mark.parametrize(
    "part,field",
    [
        ("intent", "accepted_at"),
        ("intent", "acceptor_principal"),
        ("intent", "verification_binding"),
        ("work", "proof"),
        (None, "verification_evidence"),
    ],
)
def test_no_public_acceptance_or_live_evidence_fields(part, field):
    value = basis()
    (value[part] if part else value)[field] = "untrusted"
    with pytest.raises(ProtectedDataRefused):
        decode_current_basis(canonical(value), effect=decode())


def test_rpc_wrapper_namespace_run_and_forged_dto_are_not_approval():
    effect = decode()
    value = basis(effect)
    with pytest.raises(ProtectedDataRefused):
        decode_current_basis(
            canonical({"status": "accepted", "result": value}), effect=effect
        )
    foreign = copy.deepcopy(value)
    foreign["repo_id"] = "another-repository"
    assert result(foreign, effect).status == "REFUSED"
    foreign = copy.deepcopy(value)
    foreign["run"]["principal_id"] = "another-public-label"
    assert result(foreign, effect).status == "REFUSED"
    for bad in (
        replace(effect, canonical_hash="sha256:" + "0" * 64),
        replace(effect, envelopes=list(effect.envelopes)),
        replace(effect, source_run="forged"),
    ):
        with pytest.raises(ProtectedDataRefused):
            decode_current_basis(canonical(value), effect=bad)
    parsed = decode_current_basis(canonical(value), effect=effect)
    with pytest.raises(ProtectedDataRefused):
        evaluate_currentness(
            effect, replace(parsed, status="pending", intent_utf8=b"{}")
        )


@pytest.mark.parametrize("state", ["proposed", "accepted", "rejected", "applied"])
def test_actual_native_basis_fixture_states_preserved_as_data(state):
    root = os.environ.get("Q4_NATIVE_BASIS_FIXTURE_DIR")
    if root is None:
        pytest.skip(
            "requires explicit owner-generated native PostgreSQL basis fixtures"
        )
    value = json.loads((Path(root) / (state + ".json")).read_bytes())
    effect = decode()
    parsed = decode_current_basis(canonical(value), effect=effect)
    assert json.loads(parsed.intent_utf8)["state"] == state
    # This independent native fixture may describe another proposal. A valid
    # public response cannot replace that proposal's protected accepted copy.
    if value["intent"]["intent_id"] != json.loads(effect.locator_utf8)["intent_id"]:
        assert evaluate_currentness(effect, parsed).status == "REFUSED"


def test_normal_unsorted_native_result_is_canonicalized():
    effect = decode()
    value = basis(effect)
    raw = json.dumps(value, indent=2, ensure_ascii=True).encode()
    assert raw != canonical(value)
    parsed = decode_current_basis(raw, effect=effect)
    assert parsed.canonical_utf8 == canonical(value)
    assert evaluate_currentness(effect, parsed).status == "READY"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema":1,"schema":2}',
        b'{"x":1.0}',
        b'{"x":NaN}',
        b'{"x":9007199254740992}',
        b'{"x":{"id":1,"id":2}}',
        b"\xff",
        b'{"x":"\\ud800"}',
        b"[]",
        b"null",
    ],
)
def test_unsigned_native_duplicate_and_json_type_negatives(raw):
    with pytest.raises(ProtectedDataRefused, match="^effect-basis-invalid$"):
        decode_current_basis(raw, effect=decode())


def test_unsigned_native_raw_and_canonical_size_boundaries():
    effect = decode()
    value = basis(effect)
    raw = canonical(value)
    padded = raw + b" " * (4 * 1024 * 1024 - len(raw))
    assert decode_current_basis(padded, effect=effect).canonical_utf8 == raw
    with pytest.raises(ProtectedDataRefused):
        decode_current_basis(padded + b" ", effect=effect)
    value["intent"]["unified_diff"] = ""
    size = 2 * 1024 * 1024
    needed = size - len(canonical(value))
    value["intent"]["unified_diff"] = "☃" * (needed // 3) + "x" * (needed % 3)
    value["intent"]["canonical_intent_digest"] = sha(
        canonical(
            {
                "schema": "sprintctl-effect-intent/v1",
                **{
                    k: value["intent"][k]
                    for k in (
                        "item_id",
                        "repository",
                        "base_commit",
                        "title",
                        "rationale",
                        "unified_diff",
                    )
                },
            }
        ),
        False,
    )
    assert len(canonical(value)) == size
    assert (
        len(decode_current_basis(canonical(value), effect=effect).canonical_utf8)
        == size
    )
    value["intent"]["unified_diff"] += "x"
    with pytest.raises(ProtectedDataRefused):
        decode_current_basis(canonical(value), effect=effect)


def test_unsigned_native_bool_revision_is_refused():
    value = basis()
    value["intent"]["revision"] = True
    with pytest.raises(ProtectedDataRefused):
        decode_current_basis(json.dumps(value).encode(), effect=decode())

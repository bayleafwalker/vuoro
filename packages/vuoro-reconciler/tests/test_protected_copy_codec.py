"""Independent wire falsifiers using the D1 PostgreSQL 18 private-route golden.

Source: agentops parked-20261013/q4-readout-actual-route-fixture/read-copy.json.
Native export, protected approval, and list/read route were exercised together."""

import base64
import copy
from dataclasses import FrozenInstanceError, replace
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from vuoro_reconciler.protected_copy_codec import (
    COPY_MAX,
    DOCUMENT_MAX,
    ProtectedDataRefused,
    decode_accepted_page,
    decode_protected_copy,
)

FIXTURE = Path(__file__).parent / "fixtures/q4-read-copy-v1.json"


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def sha(raw, prefixed=True):
    return ("sha256:" if prefixed else "") + hashlib.sha256(raw).hexdigest()


def wire():
    return json.loads(FIXTURE.read_bytes())


def decode(value=None, **overrides):
    value = wire() if value is None else value
    expected = wire()
    co = json.loads(base64.b64decode(expected["canonical_utf8_b64"]))
    kwargs = dict(
        namespace_utf8=canonical(expected["locator"]["namespace"]),
        acceptance_id=expected["acceptance"]["acceptance_id"],
        repository=co["parameters"]["repository"],
    )
    kwargs.update(overrides)
    return decode_protected_copy(canonical(value), **kwargs)


def docs(value):
    return [
        (e, json.loads(base64.b64decode(e["canonical_utf8_b64"])))
        for e in value["envelopes"]
    ]


def repack(value, entries):
    """Update every outer hash so semantic poisons cannot fail only checksum."""
    co = json.loads(base64.b64decode(value["canonical_utf8_b64"]))
    refs, envelopes = [], []
    for old, doc in entries:
        raw = canonical(doc)
        ref = next(
            r
            for r in co["evidence_refs"]
            if r["kind"] == old["kind"] and r["digest"] == old["document_digest"]
        )
        ref = {**ref, "digest": sha(raw)}
        refs.append(ref)
        envelopes.append(
            (
                canonical(ref),
                {
                    "kind": old["kind"],
                    "document_digest": sha(raw),
                    "canonical_utf8_b64": base64.b64encode(raw).decode(),
                },
            )
        )
    co["evidence_refs"] = sorted(refs, key=canonical)
    value["envelopes"] = [e for _, e in sorted(envelopes, key=lambda x: x[0])]
    raw = canonical(co)
    value["canonical_utf8_b64"] = base64.b64encode(raw).decode()
    value["canonical_hash"] = sha(raw)
    challenge = value["acceptance"]["challenge"]
    challenge["canonical_hash"] = value["canonical_hash"]
    challenge["run_envelope_digest"] = next(
        e["document_digest"]
        for e in value["envelopes"]
        if e["kind"] == "protected-run-binding"
    )
    value["acceptance"]["challenge_sha256"] = sha(canonical(challenge), False)
    value["receipt"]["challenge_sha256"] = value["acceptance"]["challenge_sha256"]
    value["receipt"]["canonical_hash"] = value["canonical_hash"]
    return value


def stamp_evidence(doc):
    projection = doc["owner_verification_projection"]
    digest = sha(canonical(projection["receipt"]))
    doc["evidence_digest"] = projection["evidence_digest"] = digest
    entry = {
        "item_id": doc["evidence_item_id"],
        "digest": digest,
        "chain_seq": doc["chain_seq"],
        "chain_prev_digest": doc["chain_prev_digest"],
    }
    native = json.dumps(
        entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    doc["entry_digest"] = projection["entry_digest"] = sha(native)
    projection["item_id"] = doc["evidence_item_id"]


def test_exact_bytes_immutable_data_and_no_private_broker_import():
    value = wire()
    effect = decode(value)
    assert effect.canonical_utf8 == base64.b64decode(value["canonical_utf8_b64"])
    assert effect.acceptance.challenge_utf8 == canonical(
        value["acceptance"]["challenge"]
    )
    assert effect.receipt.receipt_utf8 == canonical(value["receipt"])
    assert tuple(e.document_utf8 for e in effect.envelopes) == tuple(
        base64.b64decode(e["canonical_utf8_b64"]) for e in value["envelopes"]
    )
    assert importlib.util.find_spec("cred_broker") is None
    with pytest.raises(FrozenInstanceError):
        effect.repository = "forged"
    assert isinstance(effect.envelopes, tuple) and "synthetic" not in repr(effect)


def test_self_consistent_hashes_are_data_not_signature_attestation():
    value = wire()
    value["acceptance"]["signature_sha256"] = value["receipt"]["signature_sha256"] = (
        "0" * 64
    )
    assert decode(value).acceptance.signature_sha256 == "0" * 64
    # This intentionally says NOTHING about whether a signature was verified.


@pytest.mark.parametrize(
    "field",
    [
        "schema",
        "locator",
        "acceptance_revision",
        "canonical_hash",
        "legacy_content_digest",
        "canonical_utf8_b64",
        "envelopes",
        "acceptance",
        "receipt",
    ],
)
def test_every_required_top_level_field_missing_refuses(field):
    value = wire()
    del value[field]
    with pytest.raises(ProtectedDataRefused, match="^protected-copy-invalid$") as error:
        decode(value)
    assert error.value.__cause__ is None and error.value.__context__ is None


@pytest.mark.parametrize(
    "kind,path,replacement",
    [
        ("protected-run-binding", ("claimed_proposer", "principal_id"), "forged"),
        ("protected-work-binding", ("proposal_source_run",), "wrong-run"),
        (
            "protected-release-binding",
            ("aggregate_uuid",),
            "00000000-0000-0000-0000-000000000000",
        ),
        (
            "protected-artifact-verification",
            ("native_claim", "subject"),
            "wrong-intent",
        ),
        (
            "protected-artifact-verification",
            ("owner_verification_projection", "workspace_id"),
            "foreign",
        ),
        (
            "protected-artifact-verification",
            ("owner_verification_projection", "receipt", "intent_revision"),
            2,
        ),
        (
            "protected-artifact-verification",
            ("owner_verification_projection", "receipt", "checks", 0, "status"),
            "failed",
        ),
        ("protected-artifact-verification", ("chain_seq",), True),
        (
            "protected-artifact-verification",
            ("chain_prev_digest",),
            "sha256:" + "1" * 64,
        ),
    ],
)
def test_semantic_poison_refuses_even_with_recomputed_hashes(kind, path, replacement):
    value, entries = wire(), None
    entries = docs(value)
    doc = next(d for e, d in entries if e["kind"] == kind)
    target = doc
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    if kind == "protected-artifact-verification":
        stamp_evidence(doc)
    repack(value, entries)
    with pytest.raises(ProtectedDataRefused):
        decode(value)


def test_unicode_check_names_supported_and_unicode_native_entry_domain_refuses():
    value = wire()
    entries = docs(value)
    evidence = next(
        d for e, d in entries if e["kind"] == "protected-artifact-verification"
    )
    # Add a passed extra Unicode-named check, retaining any required check.
    evidence["owner_verification_projection"]["receipt"]["checks"].append(
        {"name": "vérification", "revision": "sha256:" + "8" * 64, "status": "passed"}
    )
    stamp_evidence(evidence)
    assert decode(repack(value, entries))
    value = wire()
    entries = docs(value)
    evidence = next(
        d for e, d in entries if e["kind"] == "protected-artifact-verification"
    )
    evidence["evidence_item_id"] = "élément"
    stamp_evidence(evidence)
    with pytest.raises(ProtectedDataRefused):
        decode(repack(value, entries))


def test_optional_bad_evidence_and_map_only_false_zero_evidence_refuse():
    value = wire()
    entries = docs(value)
    release = next(d for e, d in entries if e["kind"] == "protected-release-binding")
    evidence = next(
        d for e, d in entries if e["kind"] == "protected-artifact-verification"
    )
    release["effect_verification_required"] = False
    contract = release["native_acceptance_contract"]
    contract["effect_verification_required"] = False
    check = evidence["owner_verification_projection"]["receipt"]["checks"][0]
    contract["effect_check_revisions"] = {check["name"]: check["revision"]}
    release["expected_effect_check_revisions"] = contract["effect_check_revisions"]
    release["acceptance_contract_digest"] = sha(canonical(contract))
    release["release_digest"] = sha(
        canonical(
            {
                "aggregate_uuid": release["aggregate_uuid"],
                "item_revision": release["item_revision"],
                "acceptance_contract": contract,
                "context_refs": release["native_context_refs"],
            }
        ),
        False,
    )
    evidence["release_digest"] = evidence["owner_verification_projection"]["receipt"][
        "release_digest"
    ] = release["release_digest"]
    stamp_evidence(evidence)
    assert decode(repack(copy.deepcopy(value), entries))  # Original false/map retained.
    with pytest.raises(ProtectedDataRefused):
        decode(
            repack(
                value,
                [
                    (e, d)
                    for e, d in entries
                    if e["kind"] != "protected-artifact-verification"
                ],
            )
        )


@pytest.mark.parametrize(
    "part,key",
    [
        ("requester", "subject_id"),
        ("operator", "policy_revision"),
        ("challenge", "nonce"),
        ("challenge", "created_at"),
        ("challenge", "expires_at"),
    ],
)
def test_acceptance_permanent_joins_refuse(part, key):
    value = wire()
    value["acceptance"][part][key] = "poison"
    with pytest.raises(ProtectedDataRefused):
        decode(value)


def test_scope_metadata_order_kind_base64_and_limits():
    value = wire()
    for kwargs in (
        {"repository": "forgejo:foreign/repo"},
        {
            "namespace_utf8": canonical(
                {**value["locator"]["namespace"], "workspace_id": "foreign"}
            )
        },
        {"listed_metadata": replace(decode().receipt, receipt_id="iar_" + "f" * 32)},
    ):
        with pytest.raises(ProtectedDataRefused):
            decode(value, **kwargs)
    for mutation in (
        lambda v: v["envelopes"].reverse(),
        lambda v: v["envelopes"][0].update(kind="protected-evidence-binding"),
        lambda v: v.update(acceptance_revision=True),
        lambda v: v.update(canonical_utf8_b64=v["canonical_utf8_b64"] + "\n"),
    ):
        bad = copy.deepcopy(value)
        mutation(bad)
        with pytest.raises(ProtectedDataRefused):
            decode(bad)
    namespace = canonical(value["locator"]["namespace"])
    for raw in (
        b"{}" * (COPY_MAX // 2 + 1),
        b'{"schema":1,"schema":2}',
        b'{"number":1.0}',
        b'{"number":NaN}',
        b'{"text":"\\ud800"}',
    ):
        with pytest.raises(ProtectedDataRefused):
            decode_protected_copy(
                raw,
                namespace_utf8=namespace,
                acceptance_id=value["acceptance"]["acceptance_id"],
                repository="x",
            )
    assert len(base64.b64decode(value["canonical_utf8_b64"])) <= DOCUMENT_MAX


def test_list_cursor_is_position_only_exact_and_unpadded():
    value = wire()
    namespace = value["locator"]["namespace"]
    item = {
        key: value["receipt"][key]
        for key in (
            "acceptance_id",
            "preparation_id",
            "canonical_hash",
            "receipt_id",
            "accepted_at",
        )
    }
    cursor_body = {
        "schema": "cred-broker-protected-effect-list-cursor/v1",
        "namespace": namespace,
        "accepted_at": item["accepted_at"],
        "acceptance_id": item["acceptance_id"],
    }
    cursor = base64.urlsafe_b64encode(canonical(cursor_body)).decode().rstrip("=")
    page = {
        "schema": "cred-broker-protected-effect-list-accepted-response/v1",
        "namespace": namespace,
        "items": [item],
        "next_cursor": cursor,
    }
    kwargs = dict(
        namespace_utf8=canonical(namespace), requested_limit=1, previous_cursor=None
    )
    parsed = decode_accepted_page(canonical(page), **kwargs)
    assert parsed.items[0].acceptance_id == item["acceptance_id"]
    assert decode(value, listed_metadata=parsed.items[0])
    for bad in (cursor + "=", " " + cursor, "!", cursor[:-1] + "="):
        with pytest.raises(ProtectedDataRefused):
            decode_accepted_page(canonical({**page, "next_cursor": bad}), **kwargs)
    with pytest.raises(ProtectedDataRefused):
        decode_accepted_page(canonical(page), **{**kwargs, "previous_cursor": cursor})
    with pytest.raises(ProtectedDataRefused):
        decode_accepted_page(canonical(page), **{**kwargs, "requested_limit": True})


def set_requirement(value, *, required=False, check_map=False):
    entries = docs(value)
    release = next(d for e, d in entries if e["kind"] == "protected-release-binding")
    evidence = next(
        d for e, d in entries if e["kind"] == "protected-artifact-verification"
    )
    contract = release["native_acceptance_contract"]
    release["effect_verification_required"] = contract[
        "effect_verification_required"
    ] = required
    if check_map:
        check = evidence["owner_verification_projection"]["receipt"]["checks"][0]
        contract["effect_check_revisions"] = {check["name"]: check["revision"]}
    else:
        contract.pop("effect_check_revisions", None)
    release["expected_effect_check_revisions"] = contract.get("effect_check_revisions")
    release["acceptance_contract_digest"] = sha(canonical(contract))
    release["release_digest"] = sha(
        canonical(
            {
                "aggregate_uuid": release["aggregate_uuid"],
                "item_revision": release["item_revision"],
                "acceptance_contract": contract,
                "context_refs": release["native_context_refs"],
            }
        ),
        False,
    )
    evidence["release_digest"] = evidence["owner_verification_projection"]["receipt"][
        "release_digest"
    ] = release["release_digest"]
    stamp_evidence(evidence)
    return repack(value, entries)


def test_optional_zero_evidence_and_optional_poison_and_stale_named_revision():
    optional = set_requirement(wire())
    entries = docs(optional)
    assert decode(
        repack(
            copy.deepcopy(optional),
            [
                (e, d)
                for e, d in entries
                if e["kind"] != "protected-artifact-verification"
            ],
        )
    )
    evidence = next(
        d for e, d in entries if e["kind"] == "protected-artifact-verification"
    )
    evidence["owner_verification_projection"]["receipt"]["checks"][0]["status"] = (
        "failed"
    )
    stamp_evidence(evidence)
    with pytest.raises(ProtectedDataRefused):
        decode(repack(optional, entries))
    named = set_requirement(wire(), check_map=True)
    entries = docs(named)
    evidence = next(
        d for e, d in entries if e["kind"] == "protected-artifact-verification"
    )
    evidence["owner_verification_projection"]["receipt"]["checks"][0]["revision"] = (
        "sha256:" + "0" * 64
    )
    stamp_evidence(evidence)
    with pytest.raises(ProtectedDataRefused):
        decode(repack(named, entries))


def large_canonical_copy(size):
    value = wire()
    co = json.loads(base64.b64decode(value["canonical_utf8_b64"]))
    co["parameters"]["unified_diff"] = ""
    bytes_needed = size - len(canonical(co))
    co["parameters"]["unified_diff"] = "☃" * (bytes_needed // 3) + "x" * (
        bytes_needed % 3
    )
    value["canonical_utf8_b64"] = base64.b64encode(canonical(co)).decode()
    legacy = sha(
        canonical({"schema": "sprintctl-effect-intent/v1", **co["parameters"]}), False
    )
    value["legacy_content_digest"] = value["acceptance"]["challenge"][
        "legacy_content_digest"
    ] = legacy
    entries = docs(value)
    for envelope, doc in entries:
        if envelope["kind"] == "protected-artifact-verification":
            doc["intent_content_digest"] = doc["owner_verification_projection"][
                "receipt"
            ]["canonical_intent_digest"] = legacy
            digest = sha(co["parameters"]["unified_diff"].encode())
            doc["artifact_digest"] = doc["owner_verification_projection"]["receipt"][
                "artifact"
            ]["digest"] = digest
            stamp_evidence(doc)
    return repack(value, entries)


def test_exact_canonical_two_mib_boundary_not_generic_wire_limit():
    value = large_canonical_copy(DOCUMENT_MAX)
    # Changing envelope digests changes fixed-width refs only.
    assert len(base64.b64decode(value["canonical_utf8_b64"])) == DOCUMENT_MAX
    assert len(canonical(value)) < COPY_MAX
    assert len(decode(value).canonical_utf8) == DOCUMENT_MAX
    with pytest.raises(ProtectedDataRefused):
        decode(large_canonical_copy(DOCUMENT_MAX + 1))


def many_evidence_copy(count):
    value = wire()
    co = json.loads(base64.b64decode(value["canonical_utf8_b64"]))
    entries = docs(value)
    template = next(
        doc
        for envelope, doc in entries
        if envelope["kind"] == "protected-artifact-verification"
    )
    base_envelopes = [
        envelope
        for envelope, _ in entries
        if envelope["kind"] != "protected-artifact-verification"
    ]
    refs = [
        ref
        for ref in co["evidence_refs"]
        if ref["kind"] != "protected-artifact-verification"
    ]
    envelopes = list(base_envelopes)
    for index in range(count):
        doc = copy.deepcopy(template)
        doc["evidence_item_id"] = "evidence-" + str(index)
        doc["native_origin_run"]["run_id"] = "verifier-" + str(index)
        doc["owner_verification_projection"]["run_id"] = doc["native_origin_run"][
            "run_id"
        ]
        doc["chain_seq"], doc["chain_prev_digest"] = 0, None
        stamp_evidence(doc)
        raw = canonical(doc)
        ref = {
            "kind": "protected-artifact-verification",
            "uri": "synthetic:evidence:" + str(index),
            "digest": sha(raw),
        }
        refs.append(ref)
        envelopes.append(
            {
                "kind": ref["kind"],
                "document_digest": ref["digest"],
                "canonical_utf8_b64": base64.b64encode(raw).decode(),
            }
        )
    co["evidence_refs"] = sorted(refs, key=canonical)
    value["canonical_utf8_b64"] = base64.b64encode(canonical(co)).decode()
    value["envelopes"] = envelopes
    return repack(value, docs(value))


def test_all_29_receipts_included_and_30th_or_one_bad_receipt_refuses():
    value = many_evidence_copy(29)
    assert len(decode(value).envelopes) == 32
    with pytest.raises(ProtectedDataRefused):
        decode(many_evidence_copy(30))
    entries = docs(value)
    evidence = [
        doc
        for envelope, doc in entries
        if envelope["kind"] == "protected-artifact-verification"
    ][-1]
    evidence["owner_verification_projection"]["receipt"]["checks"][0]["status"] = (
        "failed"
    )
    stamp_evidence(evidence)
    with pytest.raises(ProtectedDataRefused):
        decode(repack(value, entries))


def test_duplicate_native_composite_or_chain_position_refuses_all_copies():
    for duplicate in ("item", "position"):
        value = many_evidence_copy(2)
        entries = docs(value)
        first, second = [
            doc
            for envelope, doc in entries
            if envelope["kind"] == "protected-artifact-verification"
        ]
        second["native_origin_run"]["run_id"] = first["native_origin_run"]["run_id"]
        second["owner_verification_projection"]["run_id"] = first["native_origin_run"][
            "run_id"
        ]
        if duplicate == "item":
            second["evidence_item_id"] = first["evidence_item_id"]
            second["chain_seq"] = 1
            second["chain_prev_digest"] = first["entry_digest"]
        stamp_evidence(second)
        with pytest.raises(ProtectedDataRefused):
            decode(repack(value, entries))


def test_same_item_id_in_two_native_runs_is_valid():
    value = many_evidence_copy(2)
    entries = docs(value)
    first, second = [
        d for e, d in entries if e["kind"] == "protected-artifact-verification"
    ]
    assert first["native_origin_run"]["run_id"] != second["native_origin_run"]["run_id"]
    second["evidence_item_id"] = first["evidence_item_id"]
    stamp_evidence(second)
    assert len(decode(repack(value, entries)).envelopes) == 5


def test_listed_receipt_mismatch_uses_correct_metadata_type():
    value = wire()
    item = {
        k: value["receipt"][k]
        for k in (
            "acceptance_id",
            "preparation_id",
            "canonical_hash",
            "receipt_id",
            "accepted_at",
        )
    }
    page = {
        "schema": "cred-broker-protected-effect-list-accepted-response/v1",
        "namespace": value["locator"]["namespace"],
        "items": [item],
        "next_cursor": None,
    }
    metadata = decode_accepted_page(
        canonical(page),
        namespace_utf8=canonical(page["namespace"]),
        requested_limit=64,
        previous_cursor=None,
    ).items[0]
    assert decode(value, listed_metadata=metadata)
    with pytest.raises(ProtectedDataRefused):
        decode(value, listed_metadata=replace(metadata, receipt_id="iar_" + "f" * 32))


def test_protected_copy_and_document_require_exact_canonical_bytes():
    value = wire()
    kwargs = dict(
        namespace_utf8=canonical(value["locator"]["namespace"]),
        acceptance_id=value["acceptance"]["acceptance_id"],
        repository=decode().repository,
    )
    with pytest.raises(ProtectedDataRefused):
        decode_protected_copy(json.dumps(value).encode(), **kwargs)
    value["envelopes"][0]["canonical_utf8_b64"] = base64.b64encode(
        json.dumps(docs(value)[0][1]).encode()
    ).decode()
    raw = base64.b64decode(value["envelopes"][0]["canonical_utf8_b64"])
    value["envelopes"][0]["document_digest"] = sha(raw)
    with pytest.raises(ProtectedDataRefused):
        decode(value)


def test_qualified_d1_route_golden_and_original_list_metadata():
    assert (
        hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
        == "1b2ff9c6bdf3e150561ef48295d2afbe63e72aeb2b47fa01376654fe25ef9b78"
    )
    # Original private list-accepted output from the same PG18 route exercise.
    page = {
        "schema": "cred-broker-protected-effect-list-accepted-response/v1",
        "namespace": {
            "authority_id": "synthetic-readout-owner",
            "repo_id": "itest-module-85c1cf02d2f5",
            "workspace_id": "ws-1",
        },
        "items": [
            {
                "acceptance_id": "iac_9075de8f8248438388580b58fb770af8",
                "preparation_id": "ipr_955e2c9d5ae44c07818e5d9375745377",
                "receipt_id": "iar_cf5b60fa3a52492db914a258f2830c7c",
                "accepted_at": "2026-10-13T19:41:57.997632Z",
                "canonical_hash": "sha256:b50c8848be449ea187a717cbb2b3787f058222f6273250d16d2d14ca4b3bca07",
            }
        ],
        "next_cursor": None,
    }
    metadata = decode_accepted_page(
        canonical(page),
        namespace_utf8=canonical(page["namespace"]),
        requested_limit=64,
        previous_cursor=None,
    ).items[0]
    assert decode(listed_metadata=metadata).receipt.receipt_id == metadata.receipt_id

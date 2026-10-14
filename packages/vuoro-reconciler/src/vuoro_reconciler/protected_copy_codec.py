"""Independent public wire/data checks for protected immutable copies.

No credential, authority, signature-verifier, transport or legacy acceptance
module is imported. Hash integrity and typed joins validate DATA only.
"""

from __future__ import annotations

import base64
from datetime import datetime
from functools import wraps
import hashlib
import json
import re

from .protected_effects import (
    AcceptanceData,
    AcceptedMetadata,
    AcceptedPageData,
    EnvelopeData,
    ProtectedEffectData,
    ReceiptData,
)

COPY_MAX = 8 * 1024 * 1024
LIST_MAX = 128 * 1024
DOCUMENT_MAX = 2 * 1024 * 1024
SAFE = 2**53 - 1
KINDS = {
    "protected-run-binding": "run",
    "protected-work-binding": "work",
    "protected-release-binding": "release",
    "protected-artifact-verification": "evidence",
}
PARAMETERS = "item_id repository base_commit title rationale unified_diff"
EDIT = re.compile(r"item:[0-9a-fA-F-]{36}@description:v[0-9]+@sha256:[0-9a-f]{64}")
REVISION = re.compile(EDIT.pattern + r"@revise:[0-9]+")


class ProtectedDataRefused(ValueError):
    """Fixed metadata only; never retain an upstream error or data field."""


def _boundary(code):
    def decorate(function):
        @wraps(function)
        def checked(*args, **kwargs):
            result = None
            try:
                result = function(*args, **kwargs)
            except (
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RecursionError,
                UnicodeError,
                OverflowError,
            ):
                pass
            if result is None:
                raise ProtectedDataRefused(code)
            return result

        return checked

    return decorate


def _need(condition):
    if not condition:
        raise ValueError("invalid-protected-data")


def _closed(value, fields):
    _need(type(value) is dict and set(value) == set(fields.split()))


def _text(value, limit=200, empty=False):
    _need(type(value) is str and (empty or bool(value)) and len(value) <= limit)


def _integer(value, zero=False):
    _need(type(value) is int and (0 if zero else 1) <= value <= SAFE)


def _digest(value, prefixed=True):
    _need(
        type(value) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}" if prefixed else r"[0-9a-f]{64}", value)
    )


def _hash(raw, prefixed=True):
    return ("sha256:" if prefixed else "") + hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf8")


def _tree(value, depth=0):
    _need(depth <= 32)
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        _need(abs(value) <= SAFE)
    elif type(value) is str:
        value.encode("utf8")
    elif type(value) is list:
        for child in value:
            _tree(child, depth + 1)
    elif type(value) is dict:
        for key, child in value.items():
            _need(type(key) is str and all(ord(c) <= 0xFFFF for c in key))
            _tree(key, depth + 1)
            _tree(child, depth + 1)
    else:
        _need(False)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result)
        result[key] = value
    return result


def _number(_):
    raise ValueError("invalid-protected-data")


def _read_json(raw, maximum):
    _need(type(raw) is bytes and 0 < len(raw) <= maximum)
    value = json.loads(
        raw.decode("utf8"),
        object_pairs_hook=_pairs,
        parse_float=_number,
        parse_constant=_number,
    )
    _tree(value)
    _need(type(value) is dict)
    return value


def _read(raw, maximum=DOCUMENT_MAX):
    value = _read_json(raw, maximum)
    _need(_canonical(value) == raw)
    return value


def _b64(value, maximum):
    _text(value, 4 * ((maximum + 2) // 3))
    raw = base64.b64decode(value, validate=True)
    _need(len(raw) <= maximum and base64.b64encode(raw).decode("ascii") == value)
    return raw


def _namespace(value):
    _closed(value, "authority_id workspace_id repo_id")
    for item in value.values():
        _text(item)


def _namespace_bytes(raw):
    value = _read(raw)
    _namespace(value)
    return value


def _locator(value):
    _closed(value, "schema namespace intent_id intent_revision")
    _need(value["schema"] == "protected-effect-locator/v1")
    _namespace(value["namespace"])
    _text(value["intent_id"])
    _integer(value["intent_revision"])


def _parameters(value):
    _closed(value, PARAMETERS)
    _integer(value["item_id"])
    _text(value["repository"])
    _need(
        type(value["base_commit"]) is str
        and re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", value["base_commit"])
    )
    for key, bound in (
        ("title", 200),
        ("rationale", 8000),
        ("unified_diff", 1_000_000),
    ):
        _text(value[key], bound, empty=True)


def _legacy(parameters):
    return _hash(
        _canonical({"schema": "sprintctl-effect-intent/v1", **parameters}), False
    )


def _origin(value):
    _closed(value, "run_id principal_id client_id grant_id")
    for key in ("run_id", "principal_id"):
        _text(value[key])
    for key in ("client_id", "grant_id"):
        if value[key] is not None:
            _text(value[key])


def _claimed(value, origin):
    _closed(value, "principal_id provenance")
    _need(
        value
        == {"principal_id": origin["principal_id"], "provenance": "source-asserted"}
    )


def _check_map(value):
    _need(type(value) is dict and 1 <= len(value) <= 64)
    for name, revision in value.items():
        _text(name)
        _need(bool(name.strip()))
        _digest(revision)


def _contract(value):
    _need(
        type(value) is dict
        and set(value)
        <= set(
            "review_required verification_profile evidence_obligations "
            "effect_verification_required effect_check_revisions".split()
        )
    )
    for key in ("review_required", "effect_verification_required"):
        if key in value:
            _need(type(value[key]) is bool)
    if "verification_profile" in value:
        _need(value["verification_profile"] in ("self-reported", "checked"))
    if "evidence_obligations" in value:
        labels = value["evidence_obligations"]
        _need(type(labels) is list and len(labels) <= 64)
        for label in labels:
            _text(label)
            _need(bool(label.strip()))
    if value.get("verification_profile") == "self-reported":
        _need(not value.get("evidence_obligations"))
    if "effect_check_revisions" in value:
        _check_map(value["effect_check_revisions"])


def _full_revision(value, aggregate_uuid):
    _need(type(value) is str and REVISION.fullmatch(value))
    edit, revise = value.rsplit("@revise:", 1)
    _need(int(revise) <= SAFE and edit.split("@")[0][5:].lower() == aggregate_uuid)
    return edit


def _release_fields(work_id, revision, digest, contract, refs, aggregate_uuid):
    _integer(work_id)
    _need(
        type(aggregate_uuid) is str and re.fullmatch(r"[0-9a-f-]{36}", aggregate_uuid)
    )
    _full_revision(revision, aggregate_uuid)
    _digest(digest, False)
    _contract(contract)
    _need(type(refs) is list and len(refs) <= 128)
    for ref in refs:
        _closed(ref, "ref_type url label")
        _need(all(type(v) is str for v in ref.values()))
    _need(refs == sorted(refs, key=lambda r: (r["ref_type"], r["url"], r["label"])))
    _need(
        digest
        == _hash(
            _canonical(
                {
                    "aggregate_uuid": aggregate_uuid.lower(),
                    "item_revision": revision,
                    "acceptance_contract": contract,
                    "context_refs": refs,
                }
            ),
            False,
        )
    )


def _document(value, kind, namespace):
    _need(
        value["schema"] == "protected-effect-" + kind + "-binding/v1"
        and value["namespace"] == namespace
    )
    _namespace(value["namespace"])
    common = "schema namespace "
    if kind == "run":
        _closed(
            value,
            common
            + "native_run claimed_proposer retrieval_profile_id retrieval_profile_revision",
        )
        _origin(value["native_run"])
        _claimed(value["claimed_proposer"], value["native_run"])
        _text(value["retrieval_profile_id"])
        _digest(value["retrieval_profile_revision"])
    elif kind == "work":
        _closed(
            value,
            common + "proposal_source_run work_item_id edit_revision status_revision",
        )
        _text(value["proposal_source_run"])
        _integer(value["work_item_id"])
        _need(
            type(value["edit_revision"]) is str
            and EDIT.fullmatch(value["edit_revision"])
        )
        _text(value["status_revision"])
    elif kind == "release":
        _closed(
            value,
            common + "proposal_source_run work_item_id item_revision release_digest "
            "effect_verification_required acceptance_contract_digest aggregate_uuid "
            "native_acceptance_contract native_context_refs expected_effect_check_revisions",
        )
        _text(value["proposal_source_run"])
        contract = value["native_acceptance_contract"]
        _release_fields(
            value["work_item_id"],
            value["item_revision"],
            value["release_digest"],
            contract,
            value["native_context_refs"],
            value["aggregate_uuid"],
        )
        _need(
            type(value["effect_verification_required"]) is bool
            and value["effect_verification_required"]
            == contract.get("effect_verification_required", False)
        )
        _need(
            value["expected_effect_check_revisions"]
            == contract.get("effect_check_revisions")
            and value["acceptance_contract_digest"] == _hash(_canonical(contract))
        )
    else:
        _closed(
            value,
            common
            + "proposal_source_run work_item_id native_origin_run claimed_origin "
            "evidence_item_id evidence_kind evidence_digest entry_digest chain_seq chain_prev_digest "
            "artifact_domain artifact_digest intent_content_digest release_digest retrieval_profile_id "
            "retrieval_profile_revision owner_verification_projection native_claim",
        )
        _text(value["proposal_source_run"])
        _integer(value["work_item_id"])
        _origin(value["native_origin_run"])
        _claimed(value["claimed_origin"], value["native_origin_run"])
        for key in ("evidence_item_id", "retrieval_profile_id"):
            _text(value[key])
        for key in (
            "evidence_digest",
            "entry_digest",
            "artifact_digest",
            "retrieval_profile_revision",
        ):
            _digest(value[key])
        for key in ("intent_content_digest", "release_digest"):
            _digest(value[key], False)
        _need(
            value["evidence_kind"] == "protected-artifact-verification"
            and value["artifact_domain"] == "utf8-unified-diff/v1"
        )
        _integer(value["chain_seq"], zero=True)
        _need((value["chain_seq"] == 0) == (value["chain_prev_digest"] is None))
        if value["chain_prev_digest"] is not None:
            _digest(value["chain_prev_digest"])
        origin = value["native_origin_run"]
        projection = value["owner_verification_projection"]
        _closed(
            projection,
            "run_id item_id evidence_digest entry_digest verifier_principal "
            "workspace_id client_id grant_id receipt",
        )
        _need(
            projection
            == {
                "run_id": origin["run_id"],
                "item_id": value["evidence_item_id"],
                "evidence_digest": value["evidence_digest"],
                "entry_digest": value["entry_digest"],
                "verifier_principal": origin["principal_id"],
                "workspace_id": namespace["workspace_id"],
                "client_id": origin["client_id"],
                "grant_id": origin["grant_id"],
                "receipt": projection["receipt"],
            }
        )
        entry = {
            "item_id": value["evidence_item_id"],
            "digest": value["evidence_digest"],
            "chain_seq": value["chain_seq"],
            "chain_prev_digest": value["chain_prev_digest"],
        }
        native = json.dumps(
            entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf8")
        _need(value["entry_digest"] == _hash(native) == _hash(_canonical(entry)))
        _need(value["evidence_digest"] == _hash(_canonical(projection["receipt"])))


def _evidence_join(evidence, locator, canonical, release):
    claim = evidence["native_claim"]
    _closed(claim, "claim_type subject grant_id confirms")
    _need(
        claim
        == {
            "claim_type": "observation",
            "subject": locator["intent_id"],
            "grant_id": None,
            "confirms": None,
        }
    )
    receipt = evidence["owner_verification_projection"]["receipt"]
    _closed(
        receipt,
        "schema intent_id intent_revision canonical_intent_digest release_digest artifact checks",
    )
    _integer(receipt["intent_revision"])
    _need(
        receipt["schema"] == "sprintctl-protected-artifact-verification/v1"
        and receipt["intent_id"] == locator["intent_id"]
        and receipt["intent_revision"] == locator["intent_revision"]
        and receipt["canonical_intent_digest"]
        == evidence["intent_content_digest"]
        == _legacy(canonical["parameters"])
        and receipt["release_digest"]
        == evidence["release_digest"]
        == release["release_digest"]
    )
    artifact = _hash(canonical["parameters"]["unified_diff"].encode("utf8"))
    _need(
        receipt["artifact"] == {"domain": "utf8-unified-diff/v1", "digest": artifact}
        and evidence["artifact_digest"] == artifact
    )
    checks = receipt["checks"]
    _need(type(checks) is list and 1 <= len(checks) <= 64)
    revisions = {}
    for check in checks:
        _closed(check, "name revision status")
        _text(check["name"])
        _need(
            bool(check["name"].strip())
            and check["name"] not in revisions
            and check["status"] == "passed"
        )
        _digest(check["revision"])
        revisions[check["name"]] = check["revision"]
    required = release["expected_effect_check_revisions"]
    _need(required is None or all(revisions.get(k) == v for k, v in required.items()))


def _snapshot(locator, canonical, raw_envelopes, repository):
    _closed(canonical, "schema intent_type parameters source_run evidence_refs")
    _need(
        canonical["schema"] == "credctl-intent-acceptance/v1"
        and canonical["intent_type"] == "sprintctl.effect.diff/v1"
    )
    _parameters(canonical["parameters"])
    _text(canonical["source_run"])
    _text(repository)
    _need(canonical["parameters"]["repository"] == repository)
    refs = canonical["evidence_refs"]
    _need(type(refs) is list and 3 <= len(refs) <= 32)
    ref_bytes = []
    for ref in refs:
        _closed(ref, "kind uri digest")
        _need(ref["kind"] in KINDS)
        _text(ref["uri"], 2048)
        _digest(ref["digest"])
        ref_bytes.append(_canonical(ref))
    _need(ref_bytes == sorted(set(ref_bytes)))
    _need(type(raw_envelopes) is list and len(raw_envelopes) == len(refs))
    envelopes, wrappers, documents, matched = [], [], {"evidence": []}, []
    for envelope in raw_envelopes:
        _closed(envelope, "kind document_digest canonical_utf8_b64")
        _need(envelope["kind"] in KINDS)
        kind = KINDS[envelope["kind"]]
        raw = _b64(envelope["canonical_utf8_b64"], DOCUMENT_MAX)
        document = _read(raw)
        _need(envelope["document_digest"] == _hash(raw))
        _document(document, kind, locator["namespace"])
        matching = [
            ref
            for ref in refs
            if ref["kind"] == envelope["kind"]
            and ref["digest"] == envelope["document_digest"]
        ]
        _need(len(matching) == 1)
        matched.append(_canonical(matching[0]))
        if kind == "evidence":
            documents["evidence"].append(document)
        else:
            _need(kind not in documents)
            documents[kind] = document
        envelopes.append(
            EnvelopeData(envelope["kind"], envelope["document_digest"], raw)
        )
        wrappers.append(
            {
                "schema": "protected-effect-envelope/v1",
                "kind": kind,
                "document": document,
                "canonical_utf8": raw.decode("utf8"),
                "document_digest": envelope["document_digest"],
            }
        )
    _need(
        matched == ref_bytes
        and set(documents) == {"run", "work", "release", "evidence"}
    )
    _need(
        len(_canonical(wrappers)) <= DOCUMENT_MAX - 14
        and len(_canonical({"envelopes": wrappers})) <= DOCUMENT_MAX
    )
    run, work, release = (documents[k] for k in ("run", "work", "release"))
    _need(canonical["source_run"] == run["native_run"]["run_id"])
    for doc in (work, release, *documents["evidence"]):
        _need(
            doc["proposal_source_run"] == canonical["source_run"]
            and doc["work_item_id"] == canonical["parameters"]["item_id"]
        )
    _need(
        release["item_revision"].rsplit("@revise:", 1)[0] == work["edit_revision"]
        and work["edit_revision"].split("@")[0][5:].lower() == release["aggregate_uuid"]
    )
    required = (
        release["effect_verification_required"]
        or "effect_check_revisions" in release["native_acceptance_contract"]
    )
    _need(not required or bool(documents["evidence"]))
    identities, positions = set(), set()
    for evidence in documents["evidence"]:
        _evidence_join(evidence, locator, canonical, release)
        identity = (
            evidence["native_origin_run"]["run_id"],
            evidence["evidence_item_id"],
        )
        position = (evidence["native_origin_run"]["run_id"], evidence["chain_seq"])
        _need(identity not in identities and position not in positions)
        identities.add(identity)
        positions.add(position)
    return tuple(envelopes), documents


def _timestamp(value):
    _need(
        type(value) is str
        and re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", value
        )
    )
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _id(value, prefix):
    _need(type(value) is str and re.fullmatch(prefix + r"[0-9a-f]{32}", value))


def _metadata(value):
    _closed(value, "acceptance_id preparation_id canonical_hash receipt_id accepted_at")
    _id(value["acceptance_id"], "iac_")
    _id(value["preparation_id"], "ipr_")
    _id(value["receipt_id"], "iar_")
    _digest(value["canonical_hash"])
    _timestamp(value["accepted_at"])
    return AcceptedMetadata(**value)


def _cursor(value, namespace):
    _text(value, 2048)
    _need(re.fullmatch(r"[A-Za-z0-9_-]+", value))
    raw = base64.b64decode(
        value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
    )
    _need(
        len(raw) <= 1536
        and base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") == value
    )
    cursor = _read(raw, 1536)
    _closed(cursor, "schema namespace accepted_at acceptance_id")
    _need(
        cursor["schema"] == "cred-broker-protected-effect-list-cursor/v1"
        and cursor["namespace"] == namespace
    )
    _timestamp(cursor["accepted_at"])
    _id(cursor["acceptance_id"], "iac_")
    return cursor["accepted_at"], cursor["acceptance_id"]


@_boundary("protected-list-invalid")
def decode_accepted_page(raw_utf8, *, namespace_utf8, requested_limit, previous_cursor):
    namespace = _namespace_bytes(namespace_utf8)
    _need(type(requested_limit) is int and 1 <= requested_limit <= 64)
    previous = (
        _cursor(previous_cursor, namespace) if previous_cursor is not None else None
    )
    page = _read(raw_utf8, LIST_MAX)
    _closed(page, "schema namespace items next_cursor")
    _need(
        page["schema"] == "cred-broker-protected-effect-list-accepted-response/v1"
        and page["namespace"] == namespace
        and type(page["items"]) is list
        and len(page["items"]) <= requested_limit
    )
    items = tuple(_metadata(item) for item in page["items"])
    positions = [(item.accepted_at, item.acceptance_id) for item in items]
    _need(
        positions == sorted(set(positions))
        and (previous is None or all(position > previous for position in positions))
        and len({item.acceptance_id for item in items}) == len(items)
    )
    cursor = page["next_cursor"]
    if cursor is not None:
        _need(
            len(items) == requested_limit
            and _cursor(cursor, namespace) == positions[-1]
        )
    return AcceptedPageData(namespace_utf8, items, cursor)


@_boundary("protected-copy-invalid")
def decode_protected_copy(
    raw_utf8, *, namespace_utf8, acceptance_id, repository, listed_metadata=None
):
    namespace = _namespace_bytes(namespace_utf8)
    _id(acceptance_id, "iac_")
    response = _read(raw_utf8, COPY_MAX)
    _closed(
        response,
        "schema locator acceptance_revision canonical_hash legacy_content_digest "
        "canonical_utf8_b64 envelopes acceptance receipt",
    )
    _need(
        response["schema"] == "cred-broker-protected-effect-read-copy-response/v1"
        and type(response["acceptance_revision"]) is int
        and response["acceptance_revision"] == 1
    )
    locator = response["locator"]
    _locator(locator)
    _need(locator["namespace"] == namespace)
    canonical_utf8 = _b64(response["canonical_utf8_b64"], DOCUMENT_MAX)
    canonical = _read(canonical_utf8)
    _need(
        response["canonical_hash"] == _hash(canonical_utf8)
        and response["legacy_content_digest"] == _legacy(canonical["parameters"])
    )
    envelopes, documents = _snapshot(
        locator, canonical, response["envelopes"], repository
    )
    acceptance = response["acceptance"]
    _closed(
        acceptance,
        "acceptance_id preparation_id accepted_at signature_sha256 challenge_sha256 requester operator challenge",
    )
    _need(acceptance["acceptance_id"] == acceptance_id)
    _id(acceptance["preparation_id"], "ipr_")
    for name in ("signature_sha256", "challenge_sha256"):
        _digest(acceptance[name], False)
    _closed(acceptance["requester"], "subject_id session_id host_id")
    _closed(
        acceptance["operator"],
        "identity_id identity_revision primary_fingerprint signing_fingerprint "
        "policy_revision operator_identity_revision operator_keyring_revision",
    )
    for body in (acceptance["requester"], acceptance["operator"]):
        for value in body.values():
            _text(value, 512)
    for name in ("primary_fingerprint", "signing_fingerprint"):
        _need(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", acceptance["operator"][name]))
    challenge = acceptance["challenge"]
    _closed(
        challenge,
        "schema preparation_id canonical_hash legacy_content_digest locator run_envelope_digest "
        "requester_subject_id requester_session_id requester_host_id policy_revision "
        "operator_identity_revision operator_keyring_revision nonce created_at expires_at",
    )
    raw_challenge = _canonical(challenge)
    _need(
        len(raw_challenge) <= 16384
        and acceptance["challenge_sha256"] == _hash(raw_challenge, False)
        and challenge["schema"] == "cred-broker-intent-acceptance-challenge/v1"
    )
    for key in ("preparation_id",):
        _need(challenge[key] == acceptance[key])
    for key in ("locator", "canonical_hash", "legacy_content_digest"):
        _need(challenge[key] == response[key])
    for key, value in acceptance["requester"].items():
        _need(challenge["requester_" + key] == value)
    for key in (
        "policy_revision",
        "operator_identity_revision",
        "operator_keyring_revision",
    ):
        _need(challenge[key] == acceptance["operator"][key])
    _digest(challenge["nonce"], False)
    run_envelope = next(e for e in envelopes if e.kind == "protected-run-binding")
    _need(challenge["run_envelope_digest"] == run_envelope.document_digest)
    created, expires, accepted = (
        _timestamp(challenge["created_at"]),
        _timestamp(challenge["expires_at"]),
        _timestamp(acceptance["accepted_at"]),
    )
    _need(
        0 < (expires - created).total_seconds() <= 300 and created <= accepted < expires
    )
    receipt = response["receipt"]
    _closed(
        receipt,
        "schema receipt_id acceptance_id preparation_id canonical_hash signature_sha256 challenge_sha256 accepted_at",
    )
    _need(receipt["schema"] == "cred-broker-intent-acceptance-receipt/v1")
    _id(receipt["receipt_id"], "iar_")
    for key in (
        "acceptance_id",
        "preparation_id",
        "signature_sha256",
        "challenge_sha256",
        "accepted_at",
    ):
        _need(receipt[key] == acceptance[key])
    _need(receipt["canonical_hash"] == response["canonical_hash"])
    metadata = AcceptedMetadata(
        acceptance_id,
        acceptance["preparation_id"],
        response["canonical_hash"],
        receipt["receipt_id"],
        acceptance["accepted_at"],
    )
    _need(
        listed_metadata is None
        or (type(listed_metadata) is AcceptedMetadata and listed_metadata == metadata)
    )
    return ProtectedEffectData(
        namespace_utf8,
        _canonical(locator),
        canonical_utf8,
        response["canonical_hash"],
        response["legacy_content_digest"],
        repository,
        canonical["source_run"],
        _canonical(canonical["parameters"]),
        envelopes,
        *(_canonical(documents[k]) for k in ("run", "work", "release")),
        1,
        AcceptanceData(
            acceptance_id,
            acceptance["preparation_id"],
            acceptance["accepted_at"],
            acceptance["signature_sha256"],
            acceptance["challenge_sha256"],
            _canonical(acceptance["requester"]),
            _canonical(acceptance["operator"]),
            raw_challenge,
        ),
        ReceiptData(
            *(
                receipt[k]
                for k in (
                    "receipt_id",
                    "acceptance_id",
                    "preparation_id",
                    "canonical_hash",
                    "signature_sha256",
                    "challenge_sha256",
                    "accepted_at",
                )
            ),
            _canonical(receipt),
        ),
    )


def _assert_effect_data(effect):
    """Recheck publicly constructible DTOs; their type alone is not proof."""
    _need(type(effect) is ProtectedEffectData)
    _need(
        type(effect.acceptance) is AcceptanceData
        and type(effect.receipt) is ReceiptData
        and type(effect.envelopes) is tuple
        and 3 <= len(effect.envelopes) <= 32
        and type(effect.canonical_utf8) is bytes
        and len(effect.canonical_utf8) <= DOCUMENT_MAX
    )
    for envelope in effect.envelopes:
        _need(
            type(envelope) is EnvelopeData
            and type(envelope.document_utf8) is bytes
            and len(envelope.document_utf8) <= DOCUMENT_MAX
        )
    acceptance = effect.acceptance
    response = {
        "schema": "cred-broker-protected-effect-read-copy-response/v1",
        "locator": _read(effect.locator_utf8),
        "acceptance_revision": effect.acceptance_revision,
        "canonical_hash": effect.canonical_hash,
        "legacy_content_digest": effect.legacy_content_digest,
        "canonical_utf8_b64": base64.b64encode(effect.canonical_utf8).decode("ascii"),
        "envelopes": [
            {
                "kind": e.kind,
                "document_digest": e.document_digest,
                "canonical_utf8_b64": base64.b64encode(e.document_utf8).decode("ascii"),
            }
            for e in effect.envelopes
        ],
        "acceptance": {
            **{
                k: getattr(acceptance, k)
                for k in (
                    "acceptance_id",
                    "preparation_id",
                    "accepted_at",
                    "signature_sha256",
                    "challenge_sha256",
                )
            },
            "requester": _read(acceptance.requester_utf8),
            "operator": _read(acceptance.operator_utf8),
            "challenge": _read(acceptance.challenge_utf8, 16384),
        },
        "receipt": _read(effect.receipt.receipt_utf8),
    }
    checked = decode_protected_copy(
        _canonical(response),
        namespace_utf8=effect.namespace_utf8,
        acceptance_id=acceptance.acceptance_id,
        repository=effect.repository,
    )
    _need(checked == effect)

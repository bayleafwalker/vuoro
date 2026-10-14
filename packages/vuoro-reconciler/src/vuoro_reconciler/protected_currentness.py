"""Pure current public DATA comparison, never approval or execution authority."""

import re

from .protected_effects import (
    CurrentEffectBasisData,
    CurrentnessResult,
    ProtectedEffectData,
)
from .protected_copy_codec import (
    DOCUMENT_MAX,
    EDIT,
    PARAMETERS,
    _assert_effect_data,
    _boundary,
    _canonical,
    _closed,
    _digest,
    _full_revision,
    _integer,
    _legacy,
    _need,
    _origin,
    _parameters,
    _read,
    _read_json,
    _release_fields,
    _text,
)


@_boundary("effect-basis-invalid")
def decode_current_basis(
    raw_utf8: bytes, *, effect: ProtectedEffectData
) -> CurrentEffectBasisData:
    """Decode only the closed native RESULT bytes; RPC wrappers are refused.

    A well-formed changed basis is retained as data for evaluate_currentness.
    Neither this decoder nor a public state authenticates the protected copy.
    """
    _assert_effect_data(effect)
    value = _read_json(raw_utf8, 4 * 1024 * 1024)
    canonical_utf8 = _canonical(value)
    _need(len(canonical_utf8) <= DOCUMENT_MAX)
    _closed(value, "schema repo_id workspace_id intent run work release")
    _need(value["schema"] == "sprintctl-effect-basis/v1")
    for key in ("repo_id", "workspace_id"):
        _text(value[key])
    intent, run, work, release = (
        value[k] for k in ("intent", "run", "work", "release")
    )
    _closed(
        intent,
        "intent_id revision state canonical_intent_digest release_digest "
        "item_id repository base_commit title rationale unified_diff run_id",
    )
    _text(intent["intent_id"])
    _integer(intent["revision"])
    _text(intent["run_id"])
    _need(intent["state"] in ("proposed", "accepted", "rejected", "applied"))
    params = {key: intent[key] for key in PARAMETERS.split()}
    _parameters(params)
    _digest(intent["canonical_intent_digest"], False)
    _digest(intent["release_digest"], False)
    _need(intent["canonical_intent_digest"] == _legacy(params))
    _closed(run, "run_id principal_id workspace_id client_id grant_id")
    _origin(
        {key: run[key] for key in ("run_id", "principal_id", "client_id", "grant_id")}
    )
    _need(
        run["run_id"] == intent["run_id"]
        and run["workspace_id"] == value["workspace_id"]
    )
    _closed(
        work,
        "work_item_id aggregate_uuid status edit_revision status_revision item_revision",
    )
    _integer(work["work_item_id"])
    _need(
        work["work_item_id"] == intent["item_id"]
        and work["status"] in ("pending", "active", "blocked", "done")
    )
    _text(work["status_revision"])
    _need(
        type(work["aggregate_uuid"]) is str
        and re.fullmatch(r"[0-9a-f-]{36}", work["aggregate_uuid"])
    )
    _need(
        type(work["edit_revision"]) is str
        and EDIT.fullmatch(work["edit_revision"])
        and _full_revision(work["item_revision"], work["aggregate_uuid"])
        == work["edit_revision"]
    )
    # A selected release may legitimately precede a description edit. Its OWN
    # full revision determines its digest; current work is compared separately.
    if release is not None:
        _closed(
            release,
            "work_item_id item_revision release_digest acceptance_contract context_refs",
        )
        _need(release["work_item_id"] == work["work_item_id"])
        _release_fields(
            release["work_item_id"],
            release["item_revision"],
            release["release_digest"],
            release["acceptance_contract"],
            release["context_refs"],
            work["aggregate_uuid"],
        )
    return CurrentEffectBasisData(
        canonical_utf8,
        _canonical(intent),
        _canonical(run),
        _canonical(work),
        _canonical(release) if release is not None else None,
        value["repo_id"],
        value["workspace_id"],
        work["status"],
    )


@_boundary("effect-currentness-invalid")
def evaluate_currentness(
    effect: ProtectedEffectData, basis: CurrentEffectBasisData
) -> CurrentnessResult:
    """READY means matching data only; no journal/provider grant is conferred."""
    _need(type(effect) is ProtectedEffectData and type(basis) is CurrentEffectBasisData)
    _need(decode_current_basis(basis.canonical_utf8, effect=effect) == basis)
    namespace = _read(effect.namespace_utf8)
    locator = _read(effect.locator_utf8)
    run_copy, work_copy, release_copy = (
        _read(raw)
        for raw in (
            effect.run_document_utf8,
            effect.work_document_utf8,
            effect.release_document_utf8,
        )
    )
    intent, run, work = (
        _read(raw) for raw in (basis.intent_utf8, basis.run_utf8, basis.work_utf8)
    )
    refusal = CurrentnessResult("REFUSED", "effect-basis-changed")
    if (
        basis.repo_id != namespace["repo_id"]
        or basis.workspace_id != namespace["workspace_id"]
    ):
        return refusal
    if (
        intent["intent_id"] != locator["intent_id"]
        or intent["revision"] != locator["intent_revision"]
        or {key: intent[key] for key in PARAMETERS.split()}
        != _read(effect.parameters_utf8)
        or intent["canonical_intent_digest"] != effect.legacy_content_digest
        or intent["release_digest"] != release_copy["release_digest"]
        or intent["run_id"] != effect.source_run
        or {key: run[key] for key in run_copy["native_run"]} != run_copy["native_run"]
    ):
        return refusal
    if (
        work["work_item_id"] != work_copy["work_item_id"]
        or work["aggregate_uuid"] != release_copy["aggregate_uuid"]
        or work["edit_revision"] != work_copy["edit_revision"]
        or work["item_revision"] != release_copy["item_revision"]
    ):
        return refusal
    if basis.release_utf8 is None:
        return CurrentnessResult("REFUSED", "effect-basis-unavailable")
    selected = _read(basis.release_utf8)
    expected = {
        "work_item_id": release_copy["work_item_id"],
        "item_revision": release_copy["item_revision"],
        "release_digest": release_copy["release_digest"],
        "acceptance_contract": release_copy["native_acceptance_contract"],
        "context_refs": release_copy["native_context_refs"],
    }
    if selected != expected:
        return refusal
    # Public intent state is deliberately not a veto, approval or completion
    # signal. Status revision is only a current observation token.
    if basis.status == "blocked":
        return CurrentnessResult("DEFERRED", "effect-work-blocked")
    if basis.status == "done":
        return CurrentnessResult("REFUSED", "effect-work-settled")
    _need(basis.status in ("pending", "active"))
    return CurrentnessResult("READY", "effect-basis-matches")

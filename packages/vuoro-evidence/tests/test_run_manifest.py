"""RunManifest (agentops#2479): the composition of a run is one addressable
object, evidence refers to it, and two runs that differ in a composed
parameter are distinguishable.

Every test here was proved falsifiable by breaking the code it covers and
capturing the failure (see the unit report); none of them pass because a
precondition was absent.
"""
from __future__ import annotations

from datetime import datetime, timezone

from dataclasses import replace

import pytest

from vuoro_evidence.core.model import EvidenceItem, ValidityBasis, ValidityWindow
from vuoro_evidence.core.set_builder import EvidenceSetBuilder, verify_evidence_set
from vuoro_evidence.run import (ObservedProfile, RunManifest, RunManifestError, UnknownRunError,
                                index_runs, resolve_run)

_AT = datetime(2026, 9, 22, tzinfo=timezone.utc)

_OBSERVED = ObservedProfile(
    instruction_digest="sha256:instructions-as-seen",
    skill_digests=(("handoff", "sha256:handoff-as-seen"), ("code-review", "sha256:cr-as-seen")),
)


def _manifest(run_id: str = "run-1", *, model_id: str = "model-a") -> RunManifest:
    return RunManifest(
        run_id=run_id,
        harness_id="claude-code",
        harness_build="1.4.2+deadbeef",
        model_id=model_id,
        recipe_id="recipe-rev-77",
        observed_profile=_OBSERVED,
        grant_ids=("grant-9",),
        claim_ids=("claim-3",),
    )


def _item(item_id: str, *, run_id: str | None = None) -> EvidenceItem:
    return EvidenceItem(item_id, "test", f"ref:{item_id}", f"sha256:{item_id}", "test-collector",
                        ValidityWindow(ValidityBasis.INDEFINITE, _AT), run_id=run_id)


# --- construction ----------------------------------------------------------

def test_manifest_names_every_composed_field_and_none_is_null() -> None:
    manifest = _manifest()
    assert manifest.harness_id == "claude-code"
    assert manifest.harness_build == "1.4.2+deadbeef"
    assert manifest.model_id == "model-a"
    assert manifest.recipe_id == "recipe-rev-77"
    assert manifest.observed_profile.instruction_digest == "sha256:instructions-as-seen"
    assert manifest.grant_ids == ("grant-9",)
    assert manifest.claim_ids == ("claim-3",)
    assert all(value for value in manifest.composition().values())


@pytest.mark.parametrize(
    "field", ["run_id", "harness_id", "harness_build", "model_id", "recipe_id"]
)
def test_a_blank_composed_field_is_refused(field: str) -> None:
    kwargs = dict(
        run_id="run-1", harness_id="claude-code", harness_build="1.4.2",
        model_id="model-a", recipe_id="recipe-rev-77", observed_profile=_OBSERVED,
    )
    kwargs[field] = "   "
    with pytest.raises(RunManifestError) as excinfo:
        RunManifest(**kwargs)
    assert field in str(excinfo.value)


def test_an_empty_grant_id_is_refused() -> None:
    with pytest.raises(RunManifestError):
        RunManifest(
            run_id="run-1", harness_id="claude-code", harness_build="1.4.2",
            model_id="model-a", recipe_id="recipe-rev-77", observed_profile=_OBSERVED,
            grant_ids=("",),
        )


def test_a_blank_or_malformed_observation_is_refused() -> None:
    with pytest.raises(RunManifestError):
        ObservedProfile(instruction_digest="")
    with pytest.raises(RunManifestError):
        ObservedProfile(instruction_digest="sha256:x", skill_digests=(("handoff", ""),))
    with pytest.raises(RunManifestError):
        RunManifest(
            run_id="run-1", harness_id="claude-code", harness_build="1.4.2",
            model_id="model-a", recipe_id="recipe-rev-77",
            observed_profile={"instruction_digest": "sha256:x"},  # type: ignore[arg-type]
        )


def test_observed_profile_records_digests_only() -> None:
    # TS-3: observed, not compiled. The object can carry digests and nothing
    # that would render or compile a profile.
    assert _OBSERVED.as_dict() == {
        "instruction_digest": "sha256:instructions-as-seen",
        "skill_digests": [["handoff", "sha256:handoff-as-seen"],
                          ["code-review", "sha256:cr-as-seen"]],
    }
    assert not [name for name in dir(ObservedProfile)
                if any(word in name for word in ("compile", "render", "lock", "preset"))]


# --- distinguishability ----------------------------------------------------

def test_two_runs_differing_only_in_model_are_distinguishable() -> None:
    a = _manifest("run-a", model_id="model-a")
    b = _manifest("run-b", model_id="model-b")
    assert a.model_id != b.model_id
    assert a.composition() != b.composition()
    # The comparison key ignores run_id, so this is the *composition*
    # differing, not just two different ids.
    assert a.composition_digest() != b.composition_digest()


# Every composed field must move the digest, not just model_id. R8/R9 ask that
# evidence reconstruct which model, PROFILE and RECIPE produced it, so a digest
# that silently ignores recipe, profile, harness build, grant or claim would let
# two materially different runs collide -- and ExperimentRecord (agentops#2487)
# is built on comparing exactly these compositions.
#
# Found by sabotage: before this test, popping any field but model_id out of
# composition_digest's payload left the whole suite green.
@pytest.mark.parametrize(
    "field, value",
    [
        ("harness_id", "codex"),
        ("harness_build", "9.9.9+cafebabe"),
        ("model_id", "model-z"),
        ("recipe_id", "recipe-rev-78"),
        ("observed_profile", ObservedProfile(
            instruction_digest="sha256:different-instructions",
            skill_digests=(("handoff", "sha256:handoff-as-seen"),),
        )),
        ("grant_ids", ("grant-10",)),
        ("claim_ids", ("claim-4",)),
    ],
)
def test_every_composed_field_moves_the_composition_digest(field, value) -> None:
    base = _manifest()
    changed = replace(base, **{field: value})
    assert getattr(changed, field) != getattr(base, field), "sabotage value must differ"
    assert changed.composition_digest() != base.composition_digest(), (
        f"composition_digest ignores {field}: two runs differing only in "
        f"{field} are indistinguishable"
    )


def test_same_composition_under_different_run_ids_shares_a_digest() -> None:
    # The guard above would be vacuous if composition_digest simply differed
    # for everything: two runs of the *same* composition must agree.
    assert _manifest("run-a").composition_digest() == _manifest("run-b").composition_digest()


def test_composition_reconstructs_the_whole_run_from_the_manifest_alone() -> None:
    assert _manifest().composition() == {
        "run_id": "run-1",
        "harness_id": "claude-code",
        "harness_build": "1.4.2+deadbeef",
        "model_id": "model-a",
        "recipe_id": "recipe-rev-77",
        "observed_profile": {
            "instruction_digest": "sha256:instructions-as-seen",
            "skill_digests": [["handoff", "sha256:handoff-as-seen"],
                              ["code-review", "sha256:cr-as-seen"]],
        },
        "grant_ids": ["grant-9"],
        "claim_ids": ["claim-3"],
    }


# --- linkage ---------------------------------------------------------------

def test_item_resolves_its_manifest() -> None:
    manifest = _manifest()
    resolved = resolve_run(_item("i-0", run_id="run-1"), index_runs([manifest]))
    assert resolved is manifest
    assert resolved.model_id == "model-a"


def test_unattributed_item_does_not_resolve_to_a_silent_default() -> None:
    with pytest.raises(UnknownRunError):
        resolve_run(_item("i-0"), index_runs([_manifest()]))


def test_dangling_reference_is_refused() -> None:
    with pytest.raises(UnknownRunError):
        resolve_run(_item("i-0", run_id="run-gone"), index_runs([_manifest()]))


def test_conflicting_manifests_for_one_run_id_are_refused() -> None:
    with pytest.raises(RunManifestError):
        index_runs([_manifest("run-1", model_id="model-a"),
                    _manifest("run-1", model_id="model-b")])


def test_builder_stamps_every_item_and_the_set_and_the_chain_still_verifies() -> None:
    builder = EvidenceSetBuilder("set-1", run_id="run-1")
    builder.extend([_item("i-0"), _item("i-1"), _item("i-2")])
    evidence_set = builder.build()
    assert evidence_set.run_id == "run-1"
    assert [item.run_id for item in evidence_set.items] == ["run-1"] * 3
    # Chaining still holds: the reference composes with E0's chain, it does
    # not replace or disturb it.
    assert verify_evidence_set(evidence_set).ok
    assert [item.chain_seq for item in evidence_set.items] == [0, 1, 2]
    index = index_runs([_manifest()])
    assert all(resolve_run(item, index).model_id == "model-a" for item in evidence_set.items)
    assert resolve_run(evidence_set, index).run_id == "run-1"


def test_builder_refuses_an_item_from_another_run() -> None:
    builder = EvidenceSetBuilder("set-1", run_id="run-1")
    with pytest.raises(ValueError):
        builder.add(_item("i-0", run_id="run-other"))


def test_unscoped_builder_leaves_items_unattributed() -> None:
    # The pre-2479 caller keeps working, and its items are honestly
    # unattributed rather than stamped with a made-up run.
    evidence_set = EvidenceSetBuilder("set-1")
    evidence_set.add(_item("i-0"))
    built = evidence_set.build()
    assert built.run_id is None
    assert built.items[0].run_id is None
    assert verify_evidence_set(built).ok

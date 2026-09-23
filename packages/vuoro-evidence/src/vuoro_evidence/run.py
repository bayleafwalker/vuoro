"""`RunManifest`: the addressable composition record of one run (agentops#2479).

Before this, nothing bound harness build, model, recipe, the profile as
observed, and the active grant/claim ids into one thing you could point at.
`EvidenceItem.provenance` is a freeform `Mapping[str, Any]`; a freeform dict
cannot be resolved, compared or reconstructed from, so R8 ("evidence
reconstructs which model, profile and recipe produced it") and R9 ("harness,
model and provider are parameters of a run") had nowhere to land.

A `RunManifest` is that thing. Evidence refers to it by `run_id`
(`EvidenceItem.run_id` / `EvidenceSet.run_id`) and `resolve_run` turns that
reference back into the object.

Why this module is not in `core/`
---------------------------------
`core/` is host-agnostic and `tests/test_boundary.py` enforces that
mechanically: the words "manifest" and "profile" are in its FORBIDDEN list,
because they name wire carriers and host shapes that the reducer must never
be able to say. That check is correct and is not weakened here. So `core/`
carries only the neutral reference (`run_id`, an opaque address) and the
composition record lives one level up, beside `core/`, where naming a harness
and a model is exactly the point.

Observed, not compiled (TS-3)
-----------------------------
`observed_profile` records the *digests observed at session start*. It does
not pin a rendered revision, and there is deliberately no compiled profile,
skill lock or role preset here -- that design direction was killed on
2026-09-20. `ObservedProfile` can only hold what was seen; it has no
render/compile entry point, and none may be added.

Scope: this module defines the object and the linkage only. Emitting a
manifest at the actionq-dispatcher is a separate unit, as is
`ExperimentRecord`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .core.model import EvidenceItem, EvidenceSet


class RunManifestError(ValueError):
    """A manifest was constructed without a field it must name."""


class UnknownRunError(KeyError):
    """Evidence referenced a run id no manifest was supplied for."""


def _require(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RunManifestError(f"RunManifest.{name} must be a non-empty string, got {value!r}")
    return value


def _require_ids(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise RunManifestError(f"RunManifest.{name} must be a tuple, got {type(values).__name__}")
    for value in values:
        _require(f"{name}[]", value)
    return values


@dataclass(frozen=True)
class ObservedProfile:
    """The profile *as observed* at session start: digests, nothing rendered.

    `instruction_digest` is the digest of the instruction text the run
    actually saw. `skill_digests` are (skill id, digest) pairs in the order
    observed. There is no compiled artefact here by construction (TS-3).
    """

    instruction_digest: str
    skill_digests: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _require("observed_profile.instruction_digest", self.instruction_digest)
        if not isinstance(self.skill_digests, tuple):
            raise RunManifestError("ObservedProfile.skill_digests must be a tuple of pairs")
        for pair in self.skill_digests:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise RunManifestError(
                    f"ObservedProfile.skill_digests entries must be (skill_id, digest) pairs, got {pair!r}"
                )
            _require("observed_profile.skill_digests[].skill_id", pair[0])
            _require("observed_profile.skill_digests[].digest", pair[1])

    def as_dict(self) -> dict[str, Any]:
        return {
            "instruction_digest": self.instruction_digest,
            "skill_digests": [list(pair) for pair in self.skill_digests],
        }


@dataclass(frozen=True)
class RunManifest:
    """One run's composition, addressable by `run_id`.

    Every field below is named, not freeform: the whole point is that two
    runs differing in any one of them are distinguishable without reading
    their evidence.
    """

    run_id: str
    harness_id: str            # which harness/CLI (e.g. "claude-code")
    harness_build: str         # which build of it (version, commit, image tag)
    model_id: str
    recipe_id: str             # RecipeRevision / recipe identifier
    observed_profile: ObservedProfile
    grant_ids: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require("run_id", self.run_id)
        _require("harness_id", self.harness_id)
        _require("harness_build", self.harness_build)
        _require("model_id", self.model_id)
        _require("recipe_id", self.recipe_id)
        if not isinstance(self.observed_profile, ObservedProfile):
            raise RunManifestError(
                "RunManifest.observed_profile must be an ObservedProfile, got "
                f"{type(self.observed_profile).__name__}"
            )
        _require_ids("grant_ids", self.grant_ids)
        _require_ids("claim_ids", self.claim_ids)

    def composition(self) -> dict[str, Any]:
        """The full composition of the run, from this object alone.

        R8/R9 in one call: what harness build, what model, what recipe, what
        profile was observed, under which grants and claims.
        """
        return {
            "run_id": self.run_id,
            "harness_id": self.harness_id,
            "harness_build": self.harness_build,
            "model_id": self.model_id,
            "recipe_id": self.recipe_id,
            "observed_profile": self.observed_profile.as_dict(),
            "grant_ids": list(self.grant_ids),
            "claim_ids": list(self.claim_ids),
        }

    def composition_digest(self) -> str:
        """Digest over the composition *excluding* `run_id`.

        Two runs that differ in any composed parameter -- the model, say --
        differ here; two runs of the same composition share it. This is a
        comparison key, not the chain's entry digest: evidence chaining
        (core.chain) hashes entries, and nothing here touches that.
        """
        payload = dict(self.composition())
        payload.pop("run_id")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


def index_runs(manifests: Iterable[RunManifest]) -> dict[str, RunManifest]:
    """Index manifests by `run_id`, rejecting two manifests under one id."""
    index: dict[str, RunManifest] = {}
    for manifest in manifests:
        existing = index.get(manifest.run_id)
        if existing is not None and existing != manifest:
            raise RunManifestError(f"two different manifests for run_id {manifest.run_id!r}")
        index[manifest.run_id] = manifest
    return index


def resolve_run(
    referrer: EvidenceItem | EvidenceSet, manifests: Mapping[str, RunManifest]
) -> RunManifest:
    """Resolve an item's (or set's) `run_id` to its `RunManifest`.

    Raises `UnknownRunError` when the evidence carries no run id, or carries
    one no manifest was supplied for -- an unresolvable reference is a
    missing composition record, never a silently empty one.
    """
    run_id = getattr(referrer, "run_id", None)
    if run_id is None:
        raise UnknownRunError(f"{type(referrer).__name__} carries no run_id")
    try:
        return manifests[run_id]
    except KeyError as error:
        raise UnknownRunError(f"no RunManifest for run_id {run_id!r}") from error

"""Ledger objects: EvidenceSet, EffectGrant, Decision, and the claim vocabulary.

Definitions follow vuoro/docs/plans/2026-08-22-long-term-direction.md §5.1.
Everything decoded at the edge of the ledger is a *claim*; authority (`EffectGrant`)
and judgment (`Decision`) are never decoded from the wire.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


class ClaimType(str, Enum):
    TARGET_STALE = "target_stale"                  # the thing acted on no longer matches what was observed
    PRECONDITION_REFUSED = "precondition_refused"  # the effect was refused before any invocation
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    EVIDENCE_PERSISTED = "evidence_persisted"      # durable material exists at a reference
    EFFECT_COMPLETED = "effect_completed"
    EFFECT_FAILED = "effect_failed"
    EFFECT_UNCERTAIN = "effect_uncertain"          # the effect may or may not have happened
    EFFECT_NOT_INVOKED = "effect_not_invoked"      # nothing consequential was attempted
    OBSERVATION = "observation"                    # independent look at the world; may confirm/contradict


@dataclass(frozen=True)
class Freshness:
    """Scope-local freshness: a monotonic position within one scope."""
    scope: str
    position: int


@dataclass(frozen=True)
class Claim:
    claim_type: ClaimType
    subject: str                        # correlation id of the effect/action this claim is about
    grant_id: str | None = None         # externally issued grant this effect was made under
    freshness: Freshness | None = None
    confirms: bool | None = None        # OBSERVATION only: True confirms, False contradicts, None neither
    detail: Mapping[str, Any] = field(default_factory=dict)


class ValidityBasis(str, Enum):
    INDEFINITE = "indefinite"                     # content-addressed; valid at its digest forever
    BOUNDED = "bounded"                           # valid until `valid_until`
    UNTIL_INPUTS_CHANGE = "until_inputs_change"   # valid while every component digest still holds


@dataclass(frozen=True)
class ValidityWindow:
    basis: ValidityBasis
    valid_from: datetime
    valid_until: datetime | None = None
    component_digests: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceItem:
    item_id: str
    kind: str                          # collector-chosen label; opaque to core
    ref: str
    digest: str
    collector: str
    validity: ValidityWindow
    claims: tuple[Claim, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)


class GrantUse(str, Enum):
    UNUSED = "unused"
    USED = "used"
    UNCERTAIN_USE = "uncertain_use"


@dataclass(frozen=True)
class EffectGrant:
    """Authoritative input (ActionQ / federation). Never minted here."""
    grant_id: str
    principal: str
    effect_class: str
    target_scope: str
    expiry: datetime | None = None
    constraints: Mapping[str, Any] = field(default_factory=dict)
    policy_decision_ref: str | None = None


@dataclass(frozen=True)
class EvidenceSet:
    set_id: str
    items: tuple[EvidenceItem, ...]
    grants: tuple[EffectGrant, ...] = ()


@dataclass(frozen=True)
class EvidenceExpired:
    item_id: str
    reason: str                              # "past_valid_until" | "input_changed:<component>"
    observed_at: datetime


class DecisionKind(str, Enum):
    ACCEPT = "accept"          # evidence suffices; rerun avoided
    REACQUIRE = "reacquire"    # evidence missing or expired; re-observe before acting
    RECONCILE = "reconcile"    # uncertain effect; fresh observation required before any retry
    REJECT = "reject"          # evidence contradicts the claim


@dataclass(frozen=True)
class Decision:
    decision_id: str
    kind: DecisionKind
    subject: str
    evidence_ids: tuple[str, ...]
    rationale: str
    requires_observation: bool = False
    events: tuple[EvidenceExpired, ...] = ()

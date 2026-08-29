"""Reduce an EvidenceSet to ledger state. Knows claims, not where they came from."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping

from .model import (Claim, ClaimType, EvidenceExpired, EvidenceItem, EvidenceSet, GrantUse,
                    ValidityBasis)


class EffectState(str, Enum):
    NOT_INVOKED = "not_invoked"
    REFUSED = "refused"
    COMPLETED = "completed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


@dataclass
class SubjectState:
    effect: EffectState | None = None
    grant_id: str | None = None
    confirmed_by: list[str] = field(default_factory=list)
    contradicted_by: list[str] = field(default_factory=list)
    reconciled: bool = False           # an UNCERTAIN effect later resolved by observation
    persisted: list[str] = field(default_factory=list)
    freshness: dict[str, int] = field(default_factory=dict)
    supporting_items: list[str] = field(default_factory=list)


@dataclass
class Ledger:
    subjects: dict[str, SubjectState] = field(default_factory=dict)
    grant_use: dict[str, GrantUse] = field(default_factory=dict)
    expired: dict[str, EvidenceExpired] = field(default_factory=dict)

    def valid_items_for(self, subject: str) -> list[str]:
        s = self.subjects.get(subject)
        return [i for i in (s.supporting_items if s else []) if i not in self.expired]


_REFUSALS = {ClaimType.TARGET_STALE, ClaimType.PRECONDITION_REFUSED, ClaimType.CAPABILITY_UNAVAILABLE}


def expiry_of(item: EvidenceItem, now: datetime, current_inputs: Mapping[str, str]) -> EvidenceExpired | None:
    v = item.validity
    if v.basis is ValidityBasis.BOUNDED and v.valid_until is not None and now > v.valid_until:
        return EvidenceExpired(item.item_id, "past_valid_until", now)
    if v.basis is ValidityBasis.UNTIL_INPUTS_CHANGE:
        for component, digest in v.component_digests.items():
            seen = current_inputs.get(component)
            if seen is not None and seen != digest:
                return EvidenceExpired(item.item_id, f"input_changed:{component}", now)
    return None


def _apply(ledger: Ledger, item: EvidenceItem, claim: Claim) -> None:
    s = ledger.subjects.setdefault(claim.subject, SubjectState())
    s.supporting_items.append(item.item_id)
    if claim.grant_id:
        s.grant_id = claim.grant_id
    if claim.freshness:
        s.freshness[claim.freshness.scope] = max(s.freshness.get(claim.freshness.scope, -1), claim.freshness.position)
    t = claim.claim_type
    if t in _REFUSALS:
        s.effect = EffectState.REFUSED
    elif t is ClaimType.EFFECT_NOT_INVOKED:
        s.effect = s.effect or EffectState.NOT_INVOKED
    elif t is ClaimType.EFFECT_COMPLETED:
        s.effect = EffectState.COMPLETED
    elif t is ClaimType.EFFECT_FAILED:
        s.effect = EffectState.FAILED
    elif t is ClaimType.EFFECT_UNCERTAIN:
        s.effect = EffectState.UNCERTAIN
        s.reconciled = False
    elif t is ClaimType.EVIDENCE_PERSISTED:
        s.persisted.append(item.item_id)
    elif t is ClaimType.OBSERVATION:
        if claim.confirms is True:
            s.confirmed_by.append(item.item_id)
        elif claim.confirms is False:
            s.contradicted_by.append(item.item_id)
        if s.effect is EffectState.UNCERTAIN and claim.confirms is not None:
            s.effect = EffectState.COMPLETED if claim.confirms else EffectState.FAILED
            s.reconciled = True
    # grant use is a projection of the effect state, never asserted by the item
    if s.grant_id:
        ledger.grant_use[s.grant_id] = _grant_use(s.effect)


def _grant_use(effect: EffectState | None) -> GrantUse:
    if effect is EffectState.UNCERTAIN:
        return GrantUse.UNCERTAIN_USE
    if effect in (EffectState.COMPLETED, EffectState.FAILED):
        return GrantUse.USED
    return GrantUse.UNUSED


def reduce(evidence: EvidenceSet, now: datetime, current_inputs: Mapping[str, str] | None = None) -> Ledger:
    """Items are applied in order; an expired item still records its claims
    (history is not erased) but is excluded from `valid_items_for`."""
    ledger = Ledger()
    inputs = current_inputs or {}
    for grant in evidence.grants:
        ledger.grant_use.setdefault(grant.grant_id, GrantUse.UNUSED)
    for item in evidence.items:
        expired = expiry_of(item, now, inputs)
        if expired:
            ledger.expired[item.item_id] = expired
        for claim in item.claims:
            _apply(ledger, item, claim)
    return ledger

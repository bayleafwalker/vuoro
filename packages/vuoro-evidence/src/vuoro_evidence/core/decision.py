"""The decision path: may a consequential effect be rerun, given the ledger?"""
from __future__ import annotations

from dataclasses import dataclass

from .model import Decision, DecisionKind
from .reducer import EffectState, Ledger


@dataclass(frozen=True)
class RerunQuestion:
    decision_id: str
    subject: str


def decide_rerun(ledger: Ledger, q: RerunQuestion) -> Decision:
    s = ledger.subjects.get(q.subject)
    items = tuple(ledger.valid_items_for(q.subject))
    expired = tuple(e for e in ledger.expired.values() if s and e.item_id in s.supporting_items)
    if s is None or (not items and not expired):
        return Decision(q.decision_id, DecisionKind.REACQUIRE, q.subject, (), "no evidence for subject")
    if s.contradicted_by:
        return Decision(q.decision_id, DecisionKind.REJECT, q.subject, tuple(s.contradicted_by),
                        "independent observation contradicts the recorded effect")
    if s.effect is EffectState.UNCERTAIN and not s.reconciled:
        return Decision(q.decision_id, DecisionKind.RECONCILE, q.subject, items,
                        "effect outcome uncertain; grant marked uncertain-use; observe before any retry",
                        requires_observation=True)
    if expired and not items:
        return Decision(q.decision_id, DecisionKind.REACQUIRE, q.subject, tuple(e.item_id for e in expired),
                        "supporting evidence expired: " + ", ".join(e.reason for e in expired), events=expired)
    if s.effect is EffectState.COMPLETED:
        return Decision(q.decision_id, DecisionKind.ACCEPT, q.subject, items,
                        "valid evidence of completion; rerun avoided", events=expired)
    return Decision(q.decision_id, DecisionKind.REACQUIRE, q.subject, items,
                    f"effect state {s.effect.value if s.effect else 'unknown'} does not justify trust", events=expired)

"""Wire evidence chaining into a writer and a read path (agentops#2464, E0).

`chain.link`/`chain.verify_chain` were landed as tested primitives (vuoro PR
#109) with no caller: nothing constructed a chained `EvidenceSet`, and
nothing verified one. Wiring a writer into the service/ingress layer needs a
decision this item's own design note deferred -- chain scope should key off
the run-tracking object the rebuild introduces (agentops#2479), not landed
in vuoro yet -- and that wiring is a larger, riskier `vuoro-service`
composition change to make without a design review.

This closes the gap at the smallest correct scope available today, exactly
the interim the design note recommended: an `EvidenceSet` is chained by
`set_id` as it is built. `EvidenceSetBuilder` is the writer -- every item
added is passed through `chain.link` before being stored, so the resulting
`EvidenceSet.items` is always a valid chain by construction. `verify_evidence_set`
is the read path -- it re-derives the chain-break diagnostics from the
finished set, the way a persistence layer would on load.

When that run-tracking object lands, the run-scoped chain replaces this: its
id becomes the chain key instead of `EvidenceSet.set_id`, and this builder's
`extend`/`build` shape can be reused directly against that new scope.

Update (agentops#2479): that run-tracking object now exists, and a builder
can be given its `run_id` so every item it stores carries the reference. The
chain key is still `set_id` -- re-scoping the chain is E0's own item, not
this one -- so the two ids stay separate on purpose.
"""

from __future__ import annotations

from .chain import ChainVerification, link, verify_chain
from .model import EvidenceItem, EvidenceSet


class EvidenceSetBuilder:
    """Accumulate `EvidenceItem`s into one chained `EvidenceSet`.

    Every item passed to `add` is re-issued (via `chain.link`) with the
    correct `chain_seq`/`chain_prev_digest` for its position -- a caller
    cannot forget to chain an item, or chain it against the wrong tail,
    because the builder is the only place `chain_seq` is assigned.
    """

    def __init__(self, set_id: str, *, run_id: str | None = None) -> None:
        self._set_id = set_id
        self._run_id = run_id
        self._items: list[EvidenceItem] = []

    def add(self, item: EvidenceItem) -> EvidenceItem:
        if self._run_id is not None and item.run_id not in (None, self._run_id):
            raise ValueError(
                f"item {item.item_id!r} carries run_id {item.run_id!r}, "
                f"but this set is being built for run {self._run_id!r}"
            )
        stamped = item if item.run_id is not None else _with_run_id(item, self._run_id)
        chained = link(self._items, stamped)
        self._items.append(chained)
        return chained

    def extend(self, items: "list[EvidenceItem] | tuple[EvidenceItem, ...]") -> None:
        for item in items:
            self.add(item)

    def build(self, *, grants: tuple = ()) -> EvidenceSet:
        return EvidenceSet(
            set_id=self._set_id, items=tuple(self._items), grants=grants, run_id=self._run_id
        )


def _with_run_id(item: EvidenceItem, run_id: str | None) -> EvidenceItem:
    """`item` re-issued carrying `run_id` (frozen dataclass, so a new one)."""
    return EvidenceItem(
        item_id=item.item_id, kind=item.kind, ref=item.ref, digest=item.digest,
        collector=item.collector, validity=item.validity, claims=item.claims,
        provenance=item.provenance, chain_seq=item.chain_seq,
        chain_prev_digest=item.chain_prev_digest, run_id=run_id,
    )


def verify_evidence_set(
    evidence_set: EvidenceSet, *, expected_start_prev: str | None = None
) -> ChainVerification:
    """The read-path counterpart of `EvidenceSetBuilder`: verify that
    `evidence_set.items` form one unbroken chain, exactly as a persistence
    layer should before trusting a set it loaded rather than built."""
    return verify_chain(evidence_set.items, expected_start_prev=expected_start_prev)

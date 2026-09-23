"""Evidence chaining: each item carries a hash reference to its predecessor in
the run's chain, so a hosted or edge writer cannot silently drop or reorder
entries without the break being detectable end to end (agentops#2464, E0).

The chain does not prevent a bad entry (a compromised writer can still record
a false claim) — it makes tampering with the *record itself*, after the fact,
detectable: `verify_chain` recomputes each entry's digest from its content and
checks that the next entry actually references it. Deleting, reordering or
altering an entry breaks the reference the following entry carries.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .model import EvidenceItem


def entry_digest(item: EvidenceItem) -> str:
    """The hash the *next* item in the chain must carry as `chain_prev_digest`.

    Computed over the item's own identity and content digest plus its position
    and its own declared predecessor, so altering any earlier link changes
    every digest after it (a true chain, not just a list of independent
    hashes)."""
    payload = {
        "item_id": item.item_id,
        "digest": item.digest,
        "chain_seq": item.chain_seq,
        "chain_prev_digest": item.chain_prev_digest,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def link(items: Sequence[EvidenceItem], next_item: EvidenceItem) -> EvidenceItem:
    """Return `next_item` with `chain_seq`/`chain_prev_digest` set to extend
    the chain formed by `items` (which must already be in chain order).
    `items` empty starts a new chain at seq 0 with no predecessor."""
    if not items:
        seq, prev = 0, None
    else:
        tail = items[-1]
        if tail.chain_seq is None:
            raise ValueError(f"cannot extend chain: {tail.item_id!r} is not chained")
        seq, prev = tail.chain_seq + 1, entry_digest(tail)
    return EvidenceItem(
        item_id=next_item.item_id, kind=next_item.kind, ref=next_item.ref,
        digest=next_item.digest, collector=next_item.collector, validity=next_item.validity,
        claims=next_item.claims, provenance=next_item.provenance,
        chain_seq=seq, chain_prev_digest=prev,
        # Carried, never re-derived: linking an item must not drop which run
        # produced it. `entry_digest` deliberately does not cover `run_id` --
        # the chain hashes entry identity and content, and that payload is not
        # changed here.
        run_id=next_item.run_id,
    )


class ChainBreakReason(str, Enum):
    NOT_CHAINED = "not_chained"                # item carries no chain_seq at all
    OUT_OF_ORDER = "out_of_order"               # chain_seq does not increment by 1
    MISSING_PREDECESSOR = "missing_predecessor"  # chain_prev_digest is None past seq 0
    ALTERED_PREDECESSOR = "altered_predecessor"  # chain_prev_digest doesn't match the recomputed digest of the prior item


@dataclass(frozen=True)
class ChainBreak:
    index: int              # position in the sequence passed to verify_chain
    item_id: str
    reason: ChainBreakReason
    detail: str = ""


@dataclass(frozen=True)
class ChainVerification:
    ok: bool
    breaks: tuple[ChainBreak, ...] = ()

    def __bool__(self) -> bool:
        return self.ok


def verify_chain(items: Sequence[EvidenceItem], *, expected_start_prev: str | None = None) -> ChainVerification:
    """Verify `items` (in the order given — the order under test, which may
    differ from a maliciously reordered wire order) form one unbroken chain.

    `expected_start_prev` lets a caller verify a *continuation* of a chain
    whose first entry here is not chain_seq 0 (e.g. verifying only this run's
    new entries against the last entry digest of a chain persisted earlier)."""
    breaks: list[ChainBreak] = []
    prev_item: EvidenceItem | None = None
    prev_expected_digest = expected_start_prev
    for i, item in enumerate(items):
        if item.chain_seq is None:
            breaks.append(ChainBreak(i, item.item_id, ChainBreakReason.NOT_CHAINED))
            prev_item = item
            prev_expected_digest = None
            continue
        if prev_item is not None and prev_item.chain_seq is not None:
            if item.chain_seq != prev_item.chain_seq + 1:
                breaks.append(ChainBreak(
                    i, item.item_id, ChainBreakReason.OUT_OF_ORDER,
                    f"expected chain_seq={prev_item.chain_seq + 1}, got {item.chain_seq}",
                ))
        if prev_expected_digest is not None or (prev_item is not None and item.chain_seq != 0):
            if item.chain_prev_digest is None:
                breaks.append(ChainBreak(i, item.item_id, ChainBreakReason.MISSING_PREDECESSOR))
            elif prev_expected_digest is not None and item.chain_prev_digest != prev_expected_digest:
                breaks.append(ChainBreak(
                    i, item.item_id, ChainBreakReason.ALTERED_PREDECESSOR,
                    f"expected {prev_expected_digest!r}, item carries {item.chain_prev_digest!r}",
                ))
        elif item.chain_seq == 0 and item.chain_prev_digest is not None and expected_start_prev is None:
            breaks.append(ChainBreak(
                i, item.item_id, ChainBreakReason.ALTERED_PREDECESSOR,
                "chain_seq=0 must carry chain_prev_digest=None (chain root)",
            ))
        prev_item = item
        prev_expected_digest = entry_digest(item)
    return ChainVerification(ok=not breaks, breaks=tuple(breaks))

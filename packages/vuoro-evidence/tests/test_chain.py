"""Evidence chaining (agentops#2464 acceptance (a)): a broken chain — a missing
or altered predecessor hash — is detected and rejected."""
from __future__ import annotations

from datetime import datetime, timezone

from vuoro_evidence.core.chain import ChainBreakReason, link, verify_chain
from vuoro_evidence.core.model import EvidenceItem, ValidityBasis, ValidityWindow

_AT = datetime(2026, 9, 20, tzinfo=timezone.utc)


def _item(item_id: str) -> EvidenceItem:
    return EvidenceItem(item_id, "test", f"ref:{item_id}", f"sha256:{item_id}", "test-collector",
                        ValidityWindow(ValidityBasis.INDEFINITE, _AT))


def _chain(n: int) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for i in range(n):
        items.append(link(items, _item(f"item-{i}")))
    return items


def test_freshly_built_chain_verifies() -> None:
    result = verify_chain(_chain(5))
    assert result.ok
    assert result.breaks == ()


def test_single_item_chain_verifies() -> None:
    result = verify_chain(_chain(1))
    assert result.ok


def test_empty_sequence_verifies() -> None:
    assert verify_chain([]).ok


def test_altered_predecessor_hash_is_detected_and_rejected() -> None:
    items = _chain(4)
    # Tamper with entry 2's declared predecessor, as if history were edited
    # after the fact without recomputing what follows.
    tampered = EvidenceItem(
        items[2].item_id, items[2].kind, items[2].ref, items[2].digest, items[2].collector,
        items[2].validity, items[2].claims, items[2].provenance,
        chain_seq=items[2].chain_seq, chain_prev_digest="sha256:" + "0" * 64,
    )
    broken = [items[0], items[1], tampered, items[3]]
    result = verify_chain(broken)
    assert not result.ok
    reasons = {b.reason for b in result.breaks}
    assert ChainBreakReason.ALTERED_PREDECESSOR in reasons
    assert result.breaks[0].item_id == tampered.item_id


def test_missing_predecessor_past_the_root_is_detected_and_rejected() -> None:
    items = _chain(3)
    dropped_prev = EvidenceItem(
        items[1].item_id, items[1].kind, items[1].ref, items[1].digest, items[1].collector,
        items[1].validity, items[1].claims, items[1].provenance,
        chain_seq=items[1].chain_seq, chain_prev_digest=None,
    )
    broken = [items[0], dropped_prev, items[2]]
    result = verify_chain(broken)
    assert not result.ok
    assert any(b.reason == ChainBreakReason.MISSING_PREDECESSOR for b in result.breaks)


def test_silently_dropped_entry_breaks_the_next_predecessor_reference() -> None:
    # A writer that drops item-1 entirely (not just blanks its field) still
    # leaves item-2's chain_prev_digest pointing at the digest of the removed
    # item, which no longer matches item-0's recomputed digest.
    items = _chain(3)
    broken = [items[0], items[2]]
    result = verify_chain(broken)
    assert not result.ok
    assert any(b.reason == ChainBreakReason.ALTERED_PREDECESSOR for b in result.breaks)


def test_reordered_entries_are_detected() -> None:
    items = _chain(3)
    broken = [items[0], items[2], items[1]]
    result = verify_chain(broken)
    assert not result.ok
    reasons = {b.reason for b in result.breaks}
    assert ChainBreakReason.OUT_OF_ORDER in reasons or ChainBreakReason.ALTERED_PREDECESSOR in reasons


def test_unchained_item_is_flagged_not_chained() -> None:
    plain = _item("standalone")
    result = verify_chain([plain])
    assert not result.ok
    assert result.breaks[0].reason == ChainBreakReason.NOT_CHAINED


def test_root_must_not_carry_a_predecessor() -> None:
    items = _chain(1)
    forged_root = EvidenceItem(
        items[0].item_id, items[0].kind, items[0].ref, items[0].digest, items[0].collector,
        items[0].validity, items[0].claims, items[0].provenance,
        chain_seq=0, chain_prev_digest="sha256:" + "f" * 64,
    )
    result = verify_chain([forged_root])
    assert not result.ok
    assert result.breaks[0].reason == ChainBreakReason.ALTERED_PREDECESSOR


def test_verify_continuation_against_a_persisted_tail_digest() -> None:
    from vuoro_evidence.core.chain import entry_digest

    first_run = _chain(3)
    tail_digest = entry_digest(first_run[-1])
    next_item = link(first_run, _item("item-3"))
    result = verify_chain([next_item], expected_start_prev=tail_digest)
    assert result.ok

    result_bad = verify_chain([next_item], expected_start_prev="sha256:" + "1" * 64)
    assert not result_bad.ok
    assert result_bad.breaks[0].reason == ChainBreakReason.ALTERED_PREDECESSOR


def test_link_rejects_extending_an_unchained_tail() -> None:
    import pytest

    unchained = [_item("standalone")]
    with pytest.raises(ValueError):
        link(unchained, _item("next"))

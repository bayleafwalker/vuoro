"""Wiring for evidence chaining (agentops#2464): one writer that chains its
output (`EvidenceSetBuilder`) and one read path that verifies (`verify_evidence_set`).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from vuoro_evidence.core.chain import ChainBreakReason
from vuoro_evidence.core.model import EvidenceItem, ValidityBasis, ValidityWindow
from vuoro_evidence.core.set_builder import EvidenceSetBuilder, verify_evidence_set

_AT = datetime(2026, 9, 20, tzinfo=timezone.utc)


def _item(item_id: str) -> EvidenceItem:
    return EvidenceItem(
        item_id, "test", f"ref:{item_id}", f"sha256:{item_id}", "test-collector",
        ValidityWindow(ValidityBasis.INDEFINITE, _AT),
    )


def test_builder_chains_items_as_they_are_added() -> None:
    builder = EvidenceSetBuilder("set-1")
    first = builder.add(_item("a"))
    second = builder.add(_item("b"))
    assert first.chain_seq == 0
    assert first.chain_prev_digest is None
    assert second.chain_seq == 1
    assert second.chain_prev_digest is not None


def test_build_produces_an_evidence_set_with_the_given_set_id() -> None:
    builder = EvidenceSetBuilder("set-1")
    builder.extend([_item("a"), _item("b"), _item("c")])
    evidence_set = builder.build()
    assert evidence_set.set_id == "set-1"
    assert len(evidence_set.items) == 3


def test_read_path_verifies_a_freshly_built_set() -> None:
    builder = EvidenceSetBuilder("set-1")
    builder.extend([_item("a"), _item("b"), _item("c")])
    result = verify_evidence_set(builder.build())
    assert result.ok
    assert result.breaks == ()


def test_read_path_detects_a_dropped_entry_in_a_persisted_set() -> None:
    builder = EvidenceSetBuilder("set-1")
    builder.extend([_item("a"), _item("b"), _item("c")])
    evidence_set = builder.build()
    # Simulate what a compromised or buggy persistence layer could do:
    # silently drop the middle entry before it's read back.
    tampered = replace(evidence_set, items=(evidence_set.items[0], evidence_set.items[2]))
    result = verify_evidence_set(tampered)
    assert not result.ok
    assert any(b.reason == ChainBreakReason.OUT_OF_ORDER for b in result.breaks)


def test_read_path_detects_an_altered_entry_in_a_persisted_set() -> None:
    builder = EvidenceSetBuilder("set-1")
    builder.extend([_item("a"), _item("b")])
    evidence_set = builder.build()
    tampered_item = replace(evidence_set.items[0], digest="sha256:forged")
    tampered = replace(evidence_set, items=(tampered_item, evidence_set.items[1]))
    result = verify_evidence_set(tampered)
    assert not result.ok
    assert any(b.reason == ChainBreakReason.ALTERED_PREDECESSOR for b in result.breaks)


def test_verify_evidence_set_supports_continuation_against_a_persisted_tail() -> None:
    first_run = EvidenceSetBuilder("set-1")
    first_run.extend([_item("a"), _item("b")])
    persisted_tail_digest = None
    from vuoro_evidence.core.chain import entry_digest

    built_first = first_run.build()
    persisted_tail_digest = entry_digest(built_first.items[-1])

    second_run_builder = EvidenceSetBuilder("set-1")
    # A fresh builder resumes at seq 0 unless the caller re-seeds it; here we
    # only check verify_evidence_set's continuation support directly, using
    # the second run's own new items linked onto the persisted tail by hand
    # to mirror what a real persistence layer would do.
    next_item = replace(_item("c"), chain_seq=2, chain_prev_digest=persisted_tail_digest)
    result = verify_evidence_set(
        type(built_first)(set_id="set-1", items=(next_item,)),
        expected_start_prev=persisted_tail_digest,
    )
    assert result.ok

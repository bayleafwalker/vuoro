#!/usr/bin/env python3
"""Dependency-free validator for the #2029 planning freeze."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs/plans/2029-sprintctl-maintenance-second-owner-proof.md"
BUNDLE = ROOT / "verification/plans/2029-sprintctl-maintenance-owner-goldens.json"
SPRINTCTL = ROOT.parent / "sprintctl"
EXPECTED_HISTORIES = {
    "concurrent-terminal-handoff", "cursor-at-floor", "cursor-below-floor",
    "disconnect", "duplicate-delivery", "expiry-materialization-authorized-write",
    "expiry-read-does-not-mutate", "immediate-client-compatibility",
    "max-100-batch", "non-disclosure-four-way", "parallel-owner-decoders",
    "postgres-parity", "prepare-response-loss", "prune-0-255-256-257",
    "redaction-canaries", "restart-during-wait", "spurious-wake", "sqlite-parity",
    "wait-0-immediate", "wait-30-controlled-clock", "wait-early-wake",
}
EXPECTED_OPERATIONS = {
    "work.maintenance.resource.prepare", "work.maintenance.resource.get",
    "work.maintenance.resource.changes",
}


def main() -> None:
    value = json.loads(BUNDLE.read_text())
    assert value["schema_version"] == "second-owner-proof-freeze/v1"
    assert value["status"] == "frozen-awaiting-independent-go"
    assert value["selected_owner"] == "sprintctl"
    assert value["required_sprintctl_backlog_item"]["item_id"] is None
    assert value["required_sprintctl_backlog_item"]["creation_required_before_implementation"] is True
    assert value["retention"] == {"events_per_resource": 256, "snapshot_pruned": False, "recovery_floor_nullable": False, "floor_changes_only_on_committed_pruning": True, "floor_meaning": "smallest-resumable-position", "below_floor": "cursor_expired"}
    assert value["authorization"]["indistinguishable_rejections"] == ["absent", "foreign", "malformed", "unauthorized"]
    assert set(value["operations"]) == EXPECTED_OPERATIONS
    assert value["compatibility"]["existing_operations_unchanged"] == ["work.maintenance.prepare", "work.read.maintenance-capability"]
    assert value["compatibility"]["preexisting_operation_descriptor_bytes_unchanged"] is True
    assert value["compatibility"]["preexisting_direct_response_bytes_unchanged"] is True
    assert value["compatibility"]["additive_catalog_bytes_and_revision_change_permitted"] is True
    assert value["compatibility"]["vuoro_persistence"] is False
    assert value["compatibility"]["client_owner_branches"] is False
    assert value["bounded_wait"]["operation"] == "work.maintenance.resource.changes"
    assert value["bounded_wait"]["argument"] == "wait_seconds"
    assert value["expiry_materialization"]["advances"] == ["observation-position", "revision"]
    assert value["expiry_materialization"]["advances_recovery_floor"] is False
    assert set(value["goldens"]) == {"reference", "snapshot", "changes", "not_found"}
    reference = value["goldens"]["reference"]
    assert re.fullmatch(value["identity"]["reference_pattern"], reference["reference"])
    assert reference == {
        "schema_version": "resource-reference/v1", "owner": "work",
        "resource_kind": "work.maintenance-capability",
        "reference": "smr1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "revision": "sprintctl-maintenance-revision-1",
    }
    assert value["terminal_states"] == ["aborted", "expired", "reconciled", "revoked"]
    snapshot = value["goldens"]["snapshot"]
    changes = value["goldens"]["changes"]
    event = changes["events"][0]
    assert set(snapshot) == {"schema_version", "reference", "revision", "cursor", "terminal", "state"}
    assert snapshot["schema_version"] == "resource-snapshot/v1" and snapshot["terminal"] is False
    assert set(snapshot["state"]) == set(value["snapshot_state_allowlist"])
    assert set(changes) == {"schema_version", "reference", "next_cursor", "events"}
    assert changes["schema_version"] == "resource-changes/v1" and len(changes["events"]) == 1
    assert set(event) == {"event_id", "terminal", "data"}
    assert set(event["data"]) == set(value["event_data_allowlist"])
    assert event["terminal"] is True
    for field, token in (("revision", reference["revision"]), ("revision", snapshot["revision"]), ("cursor", snapshot["cursor"]), ("cursor", changes["next_cursor"]), ("event_id", event["event_id"])):
        assert re.fullmatch(value["token_grammar"][field], token)
    assert value["goldens"]["not_found"] == {"status": 404, "body": {"code": "resource_not_found", "message": "resource not found"}}
    assert set(value["required_histories"]) == EXPECTED_HISTORIES
    disclosure = value["vuoro_non_disclosure"]
    assert disclosure["status"] == 404
    assert disclosure["ordered_headers"] == [["Cache-Control", "no-store"], ["Content-Type", "application/json"]]
    body = json.loads(disclosure["body_utf8"])
    assert list(body) == ["schema_version", "request_id", "operation", "catalog_revision", "status", "result", "error"]
    assert body["error"] == {"code": "resource_not_found", "message": "resource not found"}
    assert disclosure["body_utf8"] == json.dumps(body, separators=(",", ":"))
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=SPRINTCTL, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    assert revision == value["selected_owner_revision"]
    for relative, expected in value["source_files"].items():
        actual = hashlib.sha256((SPRINTCTL / relative).read_bytes()).hexdigest()
        assert re.fullmatch(r"[0-9a-f]{64}", expected) and actual == expected
    text = PLAN.read_text()
    for forbidden in ("jobs table", "ActionQ's action-root"):
        assert forbidden in text
    digest = hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
    print(f"vuoro-2029-second-owner-freeze sha256:{digest}")


if __name__ == "__main__":
    main()

"""Prove maintenance-capability admission safety inside the candidate image.

Runs against a migrated (schema 6) work ledger using the sprintctl the image
actually ships. These are the sprintctl #2093 guarantees, re-proven at the
composition boundary rather than only in sprintctl's own suite:

  stale pre-expiry `at` after database expiry -> expired
  future `at` before the window opens         -> rejected
  transaction opened before expiry, statement after -> rejected
  an active ordinary claim blocks activation  -> mutual exclusion preserved

The maintenance capability is part of the mutual exclusion Plan 1 relies on, so
the deployable artifact has to demonstrate it, not just the library.

Reads DSN from $URL and the work schema from $SCHEMA. Emits one JSON object.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import time
import uuid

import psycopg
from psycopg.rows import dict_row

from sprintctl import pg
from sprintctl.maintenance_capability import (
    MaintenanceCapabilityError,
    PostgresMaintenanceCapabilityStore,
)

# Deliberately far from any window: if a case still behaves correctly with this
# as `at`, the database clock is what decided it.
STALE_AT = "2026-08-02T20:00:00Z"


def stamp(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def ref(kind="verification-result", digit="1"):
    return {"kind": kind, "source": "test:evidence", "revision": "sha256:" + digit * 64}


def envelope(offset: timedelta, span: timedelta = timedelta(hours=1)):
    """A valid maintenance-envelope/v1 whose window is `offset` from now."""
    not_before, expires_at = offset, offset + span
    observed, bound = offset + timedelta(minutes=1), offset + timedelta(minutes=2)
    bind_by = offset + span - timedelta(minutes=10)
    step = {
        "id": "attest-backup", "sequence": 1, "depends_on": [],
        "repository_id": "appservice", "base_commit": "a" * 40, "commit": "b" * 40,
        "operation_id": "targeted-maintenance", "paths": ["clusters/main/vuoro"],
        "phase": "pre-migration", "commands": ["verify-backup"],
        "reviews": [{"reviewer": "reviewer", "author": "author", "verdict": "pass", "ref": ref()}],
        "verification_refs": [ref(digit="2")],
        "publication_ref": ref(kind="artifact", digit="3"),
    }
    jit = lambda name, pattern: {  # noqa: E731
        "name": name, "source": "backup-observation" if name != "drain_boundary_utc" else "clock-observation",
        "pattern": pattern, "bind_before_step": "attest-backup",
        "bind_by": stamp(bind_by), "required": True,
    }
    binding = lambda name, value: {  # noqa: E731
        "name": name, "value": value, "observed_at": stamp(observed),
        "bound_at": stamp(bound), "evidence_ref": ref(digit="4"),
        "receipt_ref": ref(kind="artifact", digit="5"),
    }
    gate = lambda d1, d2: {  # noqa: E731
        "expected_count": 0, "observed_at": stamp(observed),
        "evidence_ref": ref(digit=d1), "receipt_ref": ref(kind="artifact", digit=d2),
    }
    value = {
        "contract_id": "maintenance-envelope/v1",
        "envelope_id": f"vuoro-cutover-{uuid.uuid4().hex[:12]}",
        "plan_ref": "artifact:sha256:" + "a" * 64,
        "issued_at": stamp(not_before - timedelta(minutes=5)),
        "window": {"not_before": stamp(not_before), "expires_at": stamp(expires_at)},
        "operator": {
            "identity": "operator",
            "decision_ref": {"kind": "sprint-event", "source": "sprintctl:decision", "revision": "event:2253"},
        },
        "repositories": [{"id": "appservice", "url": "https://github.com/example/appservice.git", "commit": "a" * 40}],
        "command_registry_ref": "",
        "command_registry": [{"id": "verify-backup", "argv": ["verify-backup", "--exact"]}],
        "operations": [{
            "id": "targeted-maintenance", "owner_repository": "appservice",
            "command_id": "verify-backup", "allowed_paths": ["clusters/main/vuoro"],
            "allowed_commands": ["verify-backup"],
        }],
        "steps": [step],
        "jit_fields": [
            jit("backup_name", "^backup-[0-9]{4}$"),
            jit("backup_uid", "^[0-9a-f-]{36}$"),
            jit("drain_boundary_utc", "^[0-9TZ:-]{20}$"),
        ],
        "jit_bindings": [
            binding("backup_name", "backup-0001"),
            binding("backup_uid", "12345678-1234-1234-1234-123456789abc"),
            binding("drain_boundary_utc", stamp(observed)),
        ],
        "start_gate": {
            "plan": "plan-1",
            "dependent_implementation_sessions": gate("6", "7"),
            "active_normal_claims": gate("8", "9"),
        },
        "abort": {
            "before_migration": "restore-reviewed-pre-migration-state",
            "after_migration": "restore-uid-attested-backup",
            "forbidden": ["delete-migration-ledger", "edit-released-migration",
                          "recovery-request-authority", "unreviewed-commit"],
        },
        "recovery_policy": {
            "record_kinds": ["observation", "requested-command"], "authority": "none",
            "forbidden_uses": ["advance", "approve", "bind-jit", "claim", "grant",
                               "publish", "reconcile"],
        },
        "audit_reconciliation": {
            "incident_correlation_required": True,
            "immutable_receipts": ["abort", "command", "effect", "jit-binding",
                                   "publication", "reconciliation", "review", "start-gate"],
            "required_outcomes": ["aborted", "accepted", "duplicate", "expired",
                                  "incomplete", "rejected"],
            "redact": ["capability-secrets", "claim-tokens", "credentials"],
            "retention": "content-addressed-export", "export_required": True,
            "independent_review_required": True,
        },
    }
    value["command_registry_ref"] = "artifact:sha256:" + hashlib.sha256(
        json.dumps(value["command_registry"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value


def lifecycle(conn, repo_id):
    return PostgresMaintenanceCapabilityStore(pg.PgStore(conn=conn, repo_id=repo_id))


def attested(store, value):
    capability_id = f"mcap:{uuid.uuid4()}"
    prepared = store.prepare(capability_id=capability_id, request_id=str(uuid.uuid4()),
                             envelope=value, actor="operator", at=STALE_AT)
    result = store.transition(capability_id=capability_id, request_id=str(uuid.uuid4()),
                              action="attest", expected_revision=prepared["revision"],
                              actor="operator", at=STALE_AT, effect_ref="sha256:" + "0" * 64)
    return capability_id, result


def activate(store, capability_id, state, at):
    return store.transition(
        capability_id=capability_id, request_id=str(uuid.uuid4()), action="activate",
        expected_revision=state["revision"], actor="operator", at=at,
        step_id="attest-backup", command_id="verify-backup",
        command_ref="sha256:" + "1" * 64, effect_ref="sha256:" + "2" * 64,
    )


def main() -> int:
    url, schema = os.environ["URL"], os.environ.get("SCHEMA", "work")
    results = {}

    def case(name):
        def wrap(fn):
            conn = psycopg.connect(url, row_factory=dict_row)
            conn.execute(f"SET search_path TO {schema}")
            try:
                results[name] = fn(conn)
            except Exception as error:  # noqa: BLE001 - recorded as a failure
                results[name] = {"ok": False, "error": f"{type(error).__name__}: {error}"}
            finally:
                conn.close()
        return wrap

    @case("stale_pre_expiry_at_after_database_expiry")
    def _(conn):
        store = lifecycle(conn, "agentops")
        capability_id, state = attested(store, envelope(timedelta(hours=-3)))
        result = activate(store, capability_id, state, at=stamp(timedelta(hours=-2, minutes=-30)))
        return {"ok": result["outcome"] == "expired", "outcome": result["outcome"]}

    @case("future_at_before_window_opens")
    def _(conn):
        store = lifecycle(conn, "agentops")
        capability_id, state = attested(store, envelope(timedelta(hours=2)))
        try:
            activate(store, capability_id, state, at=stamp(timedelta(hours=2, minutes=30)))
        except MaintenanceCapabilityError as error:
            return {"ok": "not_before" in str(error), "rejected_with": str(error)}
        return {"ok": False, "rejected_with": None}

    @case("transaction_opened_before_expiry_statement_after")
    def _(conn):
        store = lifecycle(conn, "agentops")
        closes_in = 3
        value = envelope(timedelta(minutes=-30), span=timedelta(minutes=30, seconds=closes_in))
        capability_id, state = attested(store, value)
        # Pin now() to a pre-expiry instant by opening the transaction early.
        pinned = conn.execute("SELECT now() AS t").fetchone()["t"]
        time.sleep(closes_in + 2)
        row = conn.execute(
            "SELECT now() AS still, statement_timestamp() AS live"
        ).fetchone()
        # now() is fixed at transaction start so it must not have moved;
        # statement_timestamp() must have. That difference is the entire reason
        # the fix uses the latter, so check it rather than asserting it in a comment.
        now_stayed_pinned = row["still"] == pinned
        advanced = row["live"] > pinned
        result = activate(store, capability_id, state, at=STALE_AT)
        return {
            "ok": result["outcome"] == "expired" and now_stayed_pinned and advanced,
            "outcome": result["outcome"],
            "now_stayed_pinned": now_stayed_pinned,
            "statement_timestamp_advanced": advanced,
        }

    @case("active_ordinary_claim_blocks_activation")
    def _(conn):
        repo_id = f"probe-{uuid.uuid4().hex[:8]}"
        store = lifecycle(conn, repo_id)
        capability_id, state = attested(store, envelope(timedelta(minutes=-5)))
        base = pg.PgStore(conn=conn, repo_id=repo_id)
        sprint_id = pg.create_sprint(base, "capability probe", status="active")
        track_id = pg.get_or_create_track(base, sprint_id, "authority")
        item_id = pg.create_work_item(base, sprint_id, track_id, "probe target")
        pg.create_claim(base, item_id, "ordinary-agent")
        conn.commit()
        try:
            activate(store, capability_id, state, at=STALE_AT)
        except MaintenanceCapabilityError as error:
            return {"ok": "zero live ordinary claims" in str(error), "rejected_with": str(error)}
        return {"ok": False, "rejected_with": None}

    payload = {
        "schema_version": "vuoro-capability-safety-probe/v1",
        "verified": all(entry.get("ok") for entry in results.values()),
        "cases": results,
    }
    print(json.dumps(payload))
    return 0 if payload["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

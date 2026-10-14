# Agent directory and durable inbox: contract v1

Status: contract, frozen for M1 to M2c on 2026-10-14. Roadmap:
`docs/plans/2026-10-14-agent-coordination-roadmap.md`. Evidence and decision
basis: `docs/plans/2026-10-14-agent-coordination-m0-baseline.md`.

Vuoro holds this contract text, its JSON schemas and the provider-neutral
conformance suite. The store owner is Sprintctl: it implements the operations
below as served operations with its own migrations, and conforms at this
edge, as it does for the lease and ledger suites. Until the Q1(b) cutover the
single authoritative store for every record family here is the vuoro-shared
served authority. Nothing is dual-written.

Nothing in this contract assigns, schedules, retries, supervises or expires
work (TS-1), or chooses a harness or model (TS-2). The store records what
principals declare, observe, send and decide. Senders choose recipients. Host
adapters start runs. The work owner enforces work transitions.

## 1. Identities

| Level | Identifier | Minted by | Meaning |
|---|---|---|---|
| Principal | `principal_id` | the served authority, from the authenticated caller | A logical agent identity: today the host principal of a served profile. Never accepted from the wire. |
| Runtime instance | `instance_id`, `ins_<ULID>` | the runtime installation, once, kept on its host | One installation of a harness or host adapter acting as a principal. A principal may have several. |
| Run | `run_id`, `run_<ULID>` | the work owner (`work.run.register-v1`) | One execution. Never minted by this contract. |

The workspace (tenant) comes from the credential. The repository (project)
comes from the outer `repo_id` of the served call. Every record below is
scoped to one `(workspace_id, repo_id)`. A record outside the caller's scope
does not exist for that caller: reads return `not-found`, never `forbidden`,
so scope cannot be enumerated.

## 2. Directory

### 2.1 Registration

`work.agent.register-v1`, authority `work:evidence`. Arguments:
`instance_id`, `declaration` (schema `agent-declaration/v1`), and
`idempotency_key`.

The first registration of an `instance_id` binds it to the calling principal
for good. A different principal registering the same `instance_id` is refused
with `instance-not-owned`. A registration with a new declaration appends a
declaration revision and the latest one is current. History is kept.
Registration says what an instance accepts. It grants nothing: no operation
in this contract or in the work owner treats a registration as permission to
execute.

`work.agent.retire-v1` (`work:evidence`, owning principal only;
`instance_id`, `reason`, `idempotency_key`) marks an instance retired. A
retired instance stays listed with its facts. Revoking a principal is the
gateway's act (principal epoch); this store does not mirror it in v1. That
gap is recorded in the baseline.

### 2.2 Observations

`work.agent.observe-v1`, authority `work:evidence`, owning principal of the
instance only. Arguments: `instance_id`, `fact` (one of `observed`,
`reachable`, `executing`, `attended`), `channel` (required for `reachable`,
otherwise absent), `run_id` (required for `executing`, otherwise absent),
`valid_for_seconds` (1 to 3600), and `idempotency_key`.

The owner stamps `observed_at` from its own clock and `source` as
`self-reported`. `valid_until` is `observed_at + valid_for_seconds`. An
instance that is declared `non-interactive` cannot report `attended`
(`fact-not-applicable`). An observation is append-only. Another principal
cannot write an observation for an instance it does not own
(`instance-not-owned`).

### 2.3 Facts as read

`work.agent.list-v1` and `work.agent.describe-v1` (authority `work:read`)
return one entry per principal, schema `agent-directory-entry/v1`, with
`evaluated_at` taken from the owner's clock. Each instance carries the five
facts independently. Each fact has a `state` and the evidence behind it:

| Fact | `state` values | Evidence |
|---|---|---|
| registered | `current`, `retired` | the registration row and its latest declaration revision |
| observed | `current`, `stale`, `unknown` | the latest `observed` observation |
| reachable | per channel: `current`, `stale`, `unknown` | the latest `reachable` observation for that channel |
| executing | `current`, `stale`, `unknown` | the latest `executing` observation, with its `run_id` |
| attended | `current`, `stale`, `unknown`, `not-applicable` | the latest `attended` observation; `not-applicable` only for a declared non-interactive instance |

`current` means `evaluated_at < valid_until`. `stale` means the latest
evidence exists and has expired. `unknown` means no evidence was ever
recorded. No fact has a `false` state. An expired `attended` reads `stale`,
which says nothing about whether a human is present. An expired `reachable`
says the path is not evidenced, not that it is unreachable. Expiry is computed
at read time. Nothing sweeps, deletes or transitions a record when evidence
expires.

## 3. Inbox

### 3.1 Envelope

`work.message.send-v1`, authority `work:evidence`. Argument `message`, schema
`agent-message/v1`:

| Field | Rule |
|---|---|
| `schema` | `agent-message/v1` |
| `message_id` | `msg_<ULID>`, minted by the sender, the idempotency key |
| `recipient.principal_id` | required; must have a registration in scope (`recipient-unknown`) |
| `recipient.instance_id` | optional; if present it must belong to that principal |
| `kind` | `continue` in v1 |
| `mode` | `request` in v1; `delegation` is refused (`mode-not-supported`) |
| `context.item_id` or `context.predecessor_run_id` | at least one; must exist in scope (`context-not-found`). No message without a live owner record to name. |
| `context.work_link` | optional `vuoro-work://link/v1/...` URI (at most 4096 bytes) |
| `context.correlation_id` | optional, sender-chosen, 8 to 128 characters of `[A-Za-z0-9._:-]` |
| `body.text` | at most 16 KiB of UTF-8 |
| `artifacts[]` | references only: `uri`, `sha256`, `size_bytes`; at most 16 |
| `not_after` | optional RFC 3339 timestamp, the sender's validity constraint |
| `created_at` | the sender's clock, informational |

The canonical JSON of the envelope is at most 64 KiB (`payload-too-large`).
The owner stamps `sender_principal_id`, `workspace_id`, `repo_id`,
`persisted_at`, `envelope_digest` (SHA-256 of the canonical JSON) and
`inbox_seq`. `inbox_seq` is a positive integer that increases strictly in
persistence order within one `(workspace, repo)` and is never reused.

The envelope is immutable. Sending the same `message_id` again with the same
digest from the same sender returns the original stamps with
`replayed: true`. The same `message_id` with a different digest or from a
different sender is refused with `message-id-conflict`.

`not_after` binds the receiver, not the store. A receiver that reads a
message after its `not_after` refuses it (reason `sender-validity-elapsed`).
The store never changes a message's state because time passed.

### 3.2 Reading

`work.message.read-v1`, authority `work:read`. Arguments: `after_seq`
(integer, at least 0), `limit` (1 to 100), and optional `instance_id`. The
result holds the messages addressed to the calling principal with
`inbox_seq > after_seq`, in `inbox_seq` order. A message addressed to a
specific instance is returned only when that `instance_id` is passed. Each
message comes with its current receipts. The result also carries
`next_after_seq`. Reading changes nothing and removes nothing. A consumer
keeps `next_after_seq` durably and resumes from it after a restart. Reading
from 0 again replays everything that is retained.

`work.message.status-v1` (`work:read`; `message_id`) returns the envelope and
every receipt, to its sender or its recipient principal only. Anyone else
gets `not-found`.

### 3.3 Receipts

Receipts are separate, append-only records, schema `agent-message-receipt/v1`,
written with `work.message.receipt-v1` (`work:evidence`). Only the recipient
principal may write them, through one of its own instances. Arguments:
`message_id`, `instance_id`, `kind`, `idempotency_key`, plus the fields each
kind requires.

| Kind | Meaning | Required | Uniqueness |
|---|---|---|---|
| `observed` | that instance has read the message | none | one per (message, instance); a repeat returns the first |
| `acknowledged` | that instance has durably recorded responsibility for processing it. Not success. | none | one per (message, instance); a repeat returns the first |
| `accepted` | the receiver decided to act, under its own authorization | none | one decision per message, `accepted` or `refused`. A second decision by any instance is refused with `already-decided`, and the error names the deciding instance |
| `refused` | the receiver decided not to act | `reason` (code plus text) | as `accepted` |
| `run-linked` | a run is processing it | `run_id`, which must exist in scope (`run-not-found`) | requires `accepted` by the same instance (`not-accepted`); one run per message: a different `run_id` is refused with `run-already-linked`, and the same `run_id` replays |
| `result-linked` | the run finished and left evidence | `outcome` (`completed`, `failed`, `canceled`, `abandoned`), `evidence_refs[]` (kind and ref, at most 32), `summary` (at most 2 KiB) | requires `run-linked`; one per message; the same digest replays, a different one is refused with `already-resulted` |

The sender writes `cancel-requested` with `work.message.cancel-v1`
(`work:evidence`; sender only; `message_id`, `reason`, `idempotency_key`).
A cancellation is a request recorded beside the others. It does not
withdraw, roll back or block anything. A receiver that sees it before
deciding refuses with reason `canceled-by-sender`. After that point the
receiver records what actually happened through `result-linked`.

Every receipt carries the owner-stamped `recorded_at` and the writing
principal and instance. There is no delivery state machine in the store. A
consumer derives what it needs, such as "decided" or "has run", from the
receipts.

### 3.4 Links to work records

The inbox references authoritative records. It never replaces them. A
`run-linked` receipt names a run the work owner registered. A
`result-linked` receipt names evidence the work owner holds. Work state
changes only through the work owner's own operations, under the acting run's
principal and grants. A message, a receipt or a registration is never an
authority input to any work operation.

For continuation, the receiver registers its run with
`work.run.register-v1`, `idempotency_key = "inbox." + message_id`, and the
message's `context.predecessor_run_id` when one is present. The work owner's
ledger then yields one run per `(principal, message)`, however many times the
receiver retries. Owner writes that the run makes for this message use keys
derived from the same base (`"inbox." + message_id + "." + step`), so that a
retried effect is deduplicated by the owner.

### 3.5 Retention

v1 deletes nothing. When a retention policy is introduced (M3), deletion may
remove a message's `body` and `artifacts`. It must keep the envelope digest,
the stamps and every receipt, so that an outcome stays traceable.

### 3.6 Errors

The errors are the existing served error shape. New codes:
`instance-not-owned`, `fact-not-applicable`, `recipient-unknown`,
`mode-not-supported`, `context-not-found`, `payload-too-large`,
`message-id-conflict`, `already-decided`, `not-accepted`, `run-not-found`,
`run-already-linked`, `already-resulted`, `not-sender`. Reused codes:
`not-found`, `idempotency-conflict`, `invalid-arguments`.

## 4. Conformance histories

The provider-neutral suite in
`packages/vuoro-service/tests/conformance/test_inbox_contract.py` must pass
against the in-process reference provider and the sprintctl PostgreSQL
binding. It contains:

1. **Retry.** A sender retries `send` after an uncertain response with the
   same envelope, and one message exists with the same `inbox_seq`. A changed
   envelope under that `message_id` is refused.
2. **Redelivery.** Reading twice from the same cursor returns the same
   messages. A cursor kept across a provider restart resumes without loss or
   reordering.
3. **Single decision.** Two instances of the recipient principal both accept
   one message. Exactly one `accepted` exists, and the other instance gets
   `already-decided` naming the winner.
4. **Single run.** A second `run-linked` with a different `run_id` is
   refused. The same `run_id` replays.
5. **Ack is not outcome.** An `acknowledged` message without `result-linked`
   reads as such. No read path presents it as completed.
6. **Cancel races decision.** A cancel before the decision is visible to the
   receiver. A cancel after `accepted` leaves `accepted` and `run-linked`
   untouched.
7. **Scope.** A caller in another workspace or repository gets `not-found` on
   `describe`, `status`, `read` and `receipt`. `list` never returns foreign
   entries.
8. **Visibility.** A principal that is neither sender nor recipient gets
   `not-found` on `status` and cannot write receipts.
9. **Ownership.** Observations and registrations for an instance owned by
   another principal are refused.
10. **Freshness.** An observation past `valid_until` reads `stale`, not
    absent. A fact never recorded reads `unknown`. `attended` never reads
    `false`, and a non-interactive instance reads `not-applicable`.
11. **Shadow queue.** A message naming no live item or run is refused.
12. **Limits.** A 64 KiB plus 1 byte envelope is refused, and a message in
    `delegation` mode is refused.

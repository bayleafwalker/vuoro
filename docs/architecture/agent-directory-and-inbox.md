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
so scope cannot be enumerated. Registration is per repository, too: an
instance that serves several repositories registers, observes and reads in
each one, and the same `instance_id` may be used in each.

Instances are not separately authenticated in v1. The authenticated party is
the principal. An `instance_id` on the wire is the principal's assertion about
which of its installations acted, and the store records it as such. So "one
instance cannot overwrite another's observations without authority" holds
between principals and is enforced. Between instances of one principal it is
a convention that the store records but cannot enforce. Binding an instance
to its own credential subject is a named gap for M3 (baseline §4).

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
retired instance stays listed with its facts. It can no longer write
observations or receipts (`instance-retired`), and a message cannot target it
(`recipient-unknown`). Revoking a principal is the
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
| executing | per run: `current`, `stale`; `unknown` when the list is empty | the latest `executing` observation for each `run_id` |
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
| `mode` | `request` or `delegation` in the schema; the store refuses `delegation` in v1 (`mode-not-supported`) |
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
`inbox_seq`. `inbox_seq` is a positive integer, never reused, allocated within
one `(workspace, repo)` so that **commit order equals sequence order**. A
reader that has seen `inbox_seq = n` must never later find a committed
message with a lower number. A database sequence or identity column does not
give this: two concurrent sends can commit out of order. The allocation
therefore comes from a per-`(workspace, repo)` counter row, incremented with
`UPDATE … RETURNING` inside the sending transaction, which serializes sends
within one repository. Canonical JSON means keys sorted, no insignificant
whitespace and UTF-8, the same encoding the work owner's idempotency digests
use.

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
`inbox_seq > after_seq`, in `inbox_seq` order, under one cursor. Without
`instance_id`, only messages without a `recipient.instance_id` are returned.
With it, those plus the messages targeted at that instance are returned.
Each item is the stamped message: the envelope as sent, plus
`sender_principal_id`, `workspace_id`, `repo_id`, `persisted_at`,
`envelope_digest`, `inbox_seq`, and the message's current receipts in
`recorded_at` order. The result also carries `next_after_seq`: the highest
`inbox_seq` returned, or the given `after_seq` when the page is empty.
`work.message.send-v1` returns the stamps plus `replayed`. Reading changes nothing and removes nothing. A consumer
keeps `next_after_seq` durably and resumes from it after a restart. Reading
from 0 again replays everything that is retained.

`work.message.status-v1` (`work:read`; `message_id`) returns the envelope and
every receipt, to its sender or its recipient principal only. Anyone else
gets `not-found`.

### 3.3 Receipts

Receipts are separate, append-only records, schema `agent-message-receipt/v1`,
written with `work.message.receipt-v1` (`work:evidence`). Only the recipient
principal may write them, through one of its own non-retired instances.
Arguments: `message_id`, `instance_id`, `kind`, and the fields each kind
requires. A message targeted at an instance can be decided only by that
instance.

Receipts take no `idempotency_key`. Their idempotency comes from the
uniqueness rules below. A repeat that matches an existing receipt's natural
key and its **field digest** returns that receipt with `replayed: true`. The
field digest is the canonical JSON of the kind's own fields, listed in the
table; it excludes `recorded_at`, `principal_id` and `instance_id`. A repeat
that matches the natural key but not the digest is refused with the code
given. A retry after a crash therefore replays, whatever transport attempt
made it.

| Kind | Meaning | Kind's own fields | Natural key and conflict |
|---|---|---|---|
| `observed` | that instance has read the message | none | (message, instance); never conflicts |
| `acknowledged` | that instance has durably recorded responsibility for processing it. Not success. | none | (message, instance); never conflicts |
| `accepted` | the receiver decided to act, under its own authorization | none | one decision per message, `accepted` or `refused`. Another instance, or the same instance with a different decision or reason, is refused with `already-decided`, and the error names the deciding instance and kind |
| `refused` | the receiver decided not to act | `reason` (`code`, `text`) | as `accepted` |
| `run-linked` | a run is processing it | `run_id`, which must exist in scope (`run-not-found`) | requires `accepted` by the same instance (`not-accepted`); one per message; a different `run_id` is refused with `run-already-linked` |
| `launch-ambiguous` | the receiver attempted a launch, or a run registration, whose outcome it cannot establish, and it will not relaunch automatically | `detail` (at most 2 KiB), optional `run_id` | requires `accepted` by the same instance (`not-accepted`); a `run_id`, when given, must exist in scope; one per (message, instance); a different `detail` or `run_id` is refused with `already-recorded` |
| `result-linked` | processing ended, and this is what actually happened | `outcome`, `evidence_refs[]` (kind and ref, at most 32), `summary` (at most 2 KiB), `reason` (`code`, `text`; required when `outcome` is `not-started`) | one per message; a different digest is refused with `already-resulted` |

`result-linked` outcomes are `completed`, `failed`, `canceled`, `abandoned`
and `not-started`:

- `completed`, `failed` and `canceled` require `run-linked`.
- `not-started` and `abandoned` may follow `accepted` without a run. They
  close a message the receiver accepted and then did not or could not run.
  Examples are a policy re-check that now denies, a cancellation seen before
  launch, a `not_after` that elapsed before launch, or a lost host.
- The writer is the accepting instance. If that instance is retired, any
  non-retired instance of the recipient principal may write `abandoned`, and
  the receipt names the writer.

The sender writes `cancel-requested` with `work.message.cancel-v1`
(`work:evidence`; `message_id`, `reason`). Any other caller is refused with
`not-sender`. There is one per message, and a repeat returns the first. A
cancellation is a request recorded beside the others. It does not withdraw,
roll back or block anything. A receiver that sees it before deciding refuses
with reason `canceled-by-sender`. A receiver that sees it after accepting but
before launching closes with `result-linked` `not-started`. After a launch
the receiver records what actually happened, as `completed`, `failed` or
`canceled`.

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
`work.run.register-v1`. Its arguments are determined only by the envelope and
the receiver's pinned configuration:

- `idempotency_key = "inbox." + message_id`;
- `predecessor_run_id` is the message's `context.predecessor_run_id`, or is
  absent;
- `harness_id`, `harness_build`, `model_id`, `recipe_id` and
  `observed_profile` come from the receiver's configuration, never from the
  run's environment.

The work owner's ledger then yields one run per `(principal, message)`,
however many times the receiver retries. If the configuration changed between
retries, the owner answers `idempotency-conflict`. The receiver records
`launch-ambiguous` without a `run_id` and does not mint a new key.

The successor's owner writes use keys that the receiver fixes in the launch
prompt, which it generates deterministically from the envelope. A manual
relaunch from the same prompt therefore reuses them. In M2c the bounded
operation is `work.session-note.write-v1` with
`idempotency_key = "inbox." + message_id + ".note"`. A replay returns the
same `note_id`, and a different note under that key is refused.

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
`not-found`, `idempotency-conflict`, `invalid-arguments`, plus
`instance-retired` and `already-recorded`.

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
13. **Commit order.** Two concurrent sends to one repository whose
    transactions commit in the opposite order to their start: a reader
    polling between the commits never skips either message.
14. **Close without run.** An accepted message with no run can be closed
    `not-started` with a reason. `completed` without `run-linked` is refused.
    After the accepting instance retires, another instance of the principal
    can close the message `abandoned`.
15. **Receipt replay.** The same receipt sent again replays. The same natural
    key with different fields is refused with the kind's conflict code.
16. **Retirement.** A retired instance cannot observe, write receipts or be
    targeted.

# Domain-owned observable resources

Status: ratified. Implementation remains staged and evidence-gated.

Vuoro does not own a generic jobs or durable-operations subsystem. An
invocation may instead return an opaque reference to a resource owned by the
invoked domain. Sessions, dispatches, validation runs, migrations, and
operation-like resources can share observation mechanics without sharing a
lifecycle or state machine.

## Reference and catalog contracts

The instance result uses `resource-reference/v1`:

```json
{
  "schema_version": "resource-reference/v1",
  "owner": "execution",
  "resource_kind": "session",
  "reference": "actionq:session:1234",
  "revision": "actionq:event:5678"
}
```

The reference is an identifier, not a bearer credential. Authorization is
checked on every observation request. Repeating a committed invocation with
the same idempotency key must resolve to the same reference.

Static relationships belong in the immutable operation catalog, not in each
reference:

```json
{
  "result_contract": {
    "mode": "resource-reference",
    "resource_kind": "execution.session"
  }
}
```

A resource-kind descriptor declares domain operations and cursor semantics:

```json
{
  "resource_kind": "execution.session",
  "observation": {
    "snapshot_operation": "execution.session.get",
    "changes_operation": "execution.session.changes",
    "cursor_schema": "execution-event-cursor/v1",
    "supports_terminality": true
  }
}
```

Snapshot and changes are domain contracts. Polling, bounded long-polling, and
event streaming are service delivery transports advertised separately as
`observation_transports`. Observation authority is orthogonal to `read`,
`write`, `enqueue`, and `admin`.

## Snapshot and changes

The generic snapshot envelope carries only resource identity, revision,
owner-declared terminality, and domain-owned state:

```json
{
  "reference": "actionq:session:1234",
  "revision": "actionq:event:5678",
  "terminal": false,
  "state": {
    "status": "claimed",
    "claimant": "worker-17"
  }
}
```

Vuoro transports `state`; it does not interpret domain statuses, ordering,
cancellation, recovery, claim fencing, or terminal transitions.

Change delivery is at least once and ordered only per resource. Event IDs make
duplicates harmless:

```json
{
  "events": [
    {
      "id": "actionq:event:5679",
      "type": "session-started",
      "terminal": false,
      "data": {}
    }
  ],
  "next_cursor": "actionq:event:5679"
}
```

Every authoritative lifecycle notification refers to a durable owner fact.
Permanent event retention is not required: after a replay gap, the owner
returns `cursor_expired` with `snapshot_required: true`; the client obtains
current truth from a snapshot and resumes at its revision.

Lifecycle observation and high-volume logs, tokens, or process output are
different contracts. Losing every non-authoritative output frame must not
prevent lifecycle recovery.

## Initial transport and client ergonomics

Bounded long-poll is the first completion-oriented transport:

```http
GET /api/observe/v1/{reference}?after={cursor}&wait=30
```

It returns immediately after a change or at the bounded server timeout.
Neither a local client timeout nor a disconnected or slow observer cancels the
owned resource.

Client convenience is layered over snapshot/change observation:

```python
handle = await client.invoke("execution.dispatch.enqueue", arguments)
snapshot = await client.get(handle)
batch = await client.changes(handle, after=cursor, wait_timeout=30)
result = await client.wait(handle, until="terminal", timeout=900)
```

The first `wait` condition is terminality. The contract leaves room for
owner-defined conditions such as input required, approval required, paused, or
artifact available. Vuoro transports the condition to the adapter and does not
evaluate domain state itself. Transport selection must be visible in
logs/metrics.

The coordinator issues one bounded wait for a completion-oriented operation;
it does not layer a process-poll/fetch loop over this interface. A client may
renew bounded long-polls across transport timeouts, but it preserves one
logical wait, one cursor chain, and one final owner snapshot. Reconnects do not
create new dispatches or rerun commands.

For execution actions and groups, terminal state includes owner-declared
attachments rather than forcing a second fetch choreography:

```json
{
  "reference": "actionq:group:portable-wave-1",
  "revision": "actionq:event:9012",
  "terminal": true,
  "state": {"status": "completed"},
  "attachments": [
    {"rel": "execution-receipt", "reference": "artifact:sha256:..."},
    {"rel": "candidate-publication", "reference": "artifact:sha256:..."},
    {"rel": "verification-result", "reference": "artifact:sha256:..."},
    {"rel": "bounded-output", "reference": "outctl:capture:..."}
  ]
}
```

The resource-kind descriptor defines permitted `rel` values and which are
required for each terminal outcome. Attachments are references, not embedded
bulk logs and not bearer credentials. A terminal snapshot missing a required
attachment is observably incomplete; Vuoro does not fabricate it, infer it from
logs, or rerun work.

SSE is deferred until lifecycle richness, cockpit use, a native wake-up path,
or sustained long-poll load justifies it. It must project the same cursor and
event envelopes rather than introduce new semantics. Connection closure is
never proof of completion; only a durable terminal event or recovered terminal
snapshot is authoritative.

Durable callbacks/webhooks are out of scope because they introduce callback
credentials, SSRF controls, retries, acknowledgements, dead letters, and a new
state machine.

## Staged proof

1. **Actionq owner proof.** Stabilize snapshot, cursor ordering and retention,
   terminal declaration, required terminal attachments, idempotent reference
   resolution, and polling or bounded long-polling in Actionq.
2. **Minimal Vuoro contract.** Add `resource-reference/v1`, resource-kind
   descriptors, generic `get`, `changes`, and narrow `wait` ergonomics. Keep
   lifecycle interpretation in Actionq.
3. **Second-owner proof.** Exercise a validation execution or maintenance
   resource without Actionq-shaped contortions before declaring a
   substrate-wide v1.
4. **Evidence-gated SSE.** Add only as a transport projection over the proven
   change contract.

Contract and compatibility decisions are coordinator-owned. Once schemas,
fixtures, interfaces, and acceptance are frozen, Actionq adapter plumbing,
client long-poll/reconnect mechanics, protocol fixtures, and negative
authorization/cursor-recovery tests are eligible for bounded hybrid packets.

Actionq execution portability is an adjacent contract, not part of generic
observation. Execution envelopes, runner placement, candidate Git artifacts,
integration topology, and independent review are defined in
[Portable governed execution](portable-execution.md). Observable resources
expose Actionq-owned lifecycle truth without transferring that authority to
Vuoro or to a runner.

## Required acceptance histories

- No completion is missed between snapshot and observation.
- Duplicate events are harmless and deduplicable by event ID.
- Ordering is per resource; no unsupported global order is implied.
- Service restart during a wait is recoverable from the cursor.
- Cursor expiry has a deterministic snapshot recovery path.
- Slow-client disconnection does not affect the resource.
- Losing non-authoritative output does not prevent lifecycle recovery.
- An opaque reference does not grant observation authority.
- Terminal event and terminal snapshot do not contradict each other.
- Retrying enqueue after response loss returns the same resource reference.
- Existing clients retain ordinary immediate invocation responses.
- Claim fencing is unchanged and represented explicitly.
- One logical terminal wait survives multiple bounded transport timeouts
  without creating another action or resetting its cursor chain.
- Terminal execution state exposes every attachment required by the frozen
  plan; missing attachments are distinguishable from failed work and expired
  output.
- Losing or expiring a bounded-output attachment does not contradict durable
  lifecycle state or trigger command rerun.

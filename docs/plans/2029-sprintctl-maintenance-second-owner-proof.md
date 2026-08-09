# Vuoro #2029 — Sprintctl maintenance second-owner proof freeze

Status: frozen for independent review; implementation prohibited until GO.
Coordinator authority: Vuoro claim #462. Owner implementation requires a
separate, newly created Sprintctl backlog item and fresh Sprintctl claim.

## Authority boundary

Sprintctl remains the sole owner of maintenance capability identity, state,
revision ordering, expiry, transition admission, retention, authorization, and
recovery. Vuoro registers metadata, invokes owner operations, validates neutral
envelopes, and transports opaque strings. It gains no maintenance state machine,
jobs table, expiry clock, pruning policy, cancellation, recovery, or fencing.

This proof deliberately uses none of ActionQ's action-root, execution-session,
claim, outcome, result-reference, or dispatch vocabulary.

## Frozen owner contract

- Resource kind: `work.maintenance-capability`. The authority root is the
  existing repository-scoped Sprintctl maintenance capability row.
- Public reference: a new random `smr1_` plus 43 base64url-character token.
  It maps owner-side to exactly `(repo_id, capability_id)`, is never a bearer
  credential, is never derived from `mcap:*`, and is returned on successful
  `work.maintenance.prepare` retries unchanged.
- Owner revision, cursor, and event ID are independent opaque strings. Vuoro
  cannot compare or synthesize them. Sprintctl orders changes by a per-resource
  monotonically increasing observation position committed in the same
  transaction as the maintenance capability mutation.
- Snapshot reads capability projection and handoff cursor in one statement or
  repeatable-read transaction. A concurrent terminal transition cannot fall
  between the projected state and cursor.
- Neutral snapshot `state` contains only `state`, `not_before`, `expires_at`,
  and `updated_at`. Events contain only `state`, `updated_at`. Existing envelope,
  operator, plan, receipts, step/command/effect data, recovery records, repository
  identity, internal capability ID, and authority decisions are excluded.
- Terminality is owner-declared exactly for `reconciled`, `aborted`, `revoked`,
  and `expired`. Snapshot, changes, and wait are strictly read-only and never
  materialize expiry. Merely reaching wall-clock expiry is not observation
  truth. Only an authenticated, repository-authorized existing maintenance
  transition or owner sweep may lock the capability, read the database clock,
  atomically commit `expired` plus its observation event and advance the owner
  revision/observation position. It does not advance the recovery floor. The
  floor changes only in a committed pruning transaction and always equals the
  smallest resumable position. A failed authorization check occurs before lock, clock, or
  mutation. The sweep uses a distinct owner scheduler principal and the same
  transaction function; Vuoro never schedules or authorizes it.
- Retention is finite per resource: retain the newest 256 observation events
  and never prune the current snapshot. Persist a non-null monotonic recovery
  floor. A cursor below the floor returns `cursor_expired`; recovery is a fresh
  atomic snapshot. Cursor tokens expire no earlier than their underlying event.
- Changes use at-least-once delivery, strict per-resource order, maximum 100
  events. `work.maintenance.resource.changes` accepts `wait_seconds` integer
  0..30 as required by the #2028 `changes_operation`: zero is one immediate
  changes read; 30 uses an owner-controlled monotonic deadline;
  a committed newer event wakes early; a spurious wake repeats the same
  read-only predicate until change or deadline. Every response returns at most
  100 events and an owner cursor. Disconnect never changes capability state. No
  SSE is introduced.
- Snapshot/changes require existing `work:maintenance` authority and repository
  scope. The reference grants no authority. Malformed, absent, wrong-repository,
  and unauthorized references have one indistinguishable owner rejection before
  projection data is disclosed.
- Existing immediate clients, `work.maintenance.prepare`, and
  `work.read.maintenance-capability` remain byte/behavior compatible. Every
  pre-existing operation descriptor and direct response remains byte-identical.
  The catalog document bytes and revision may change only by addition of the
  three new operation descriptors, resource-kind descriptor, and bounded-poll
  capability metadata.
  They are not replaced and receive no result contract. Three additive operations
  are frozen exactly: `work.maintenance.resource.prepare`,
  `work.maintenance.resource.get`, `work.maintenance.resource.changes`, and
  no distinct wait operation exists. The additive prepare delegates the existing
  owner transaction/idempotency semantics but its registered decoder returns
  `resource-reference/v1`; retries return the original reference. Get, changes,
  and wait have their own neutral result schemas and contracts.

## Frozen Vuoro non-disclosure policy

#2029 includes a scoped generic-shell change. An optional immutable operation
descriptor field `failure_disclosure: resource-not-found/v1` is legal only on
resource observation operations. Registration requires an owner-supplied
visibility guard. After generic authentication and repository authorization,
but before the operation handler, the shell validates the opaque grammar and
calls the guard for reference visibility. Malformed, absent, foreign-repository,
and unauthorized-resource outcomes never invoke the operation handler and emit
the exact same constant response. The guard may perform the minimum owner lookup
needed for visibility but returns only visible/not-visible and may not mutate.

Canonical response bytes (UTF-8, no trailing newline) are:

```json
{"schema_version":"invocation-result/v1","request_id":"00000000-0000-0000-0000-000000000000","operation":"resource-observation","catalog_revision":"redacted","status":"rejected","result":null,"error":{"code":"resource_not_found","message":"resource not found"}}
```

The HTTP status is exactly 404. The ordered application headers are exactly
`Cache-Control: no-store` then `Content-Type: application/json`; hop-by-hop and
server-generated framing headers are outside the contract. The constant
request/operation/revision values intentionally prevent correlation differences.
The client maps this canonical invocation envelope to one
`InvocationRejectedError(resource_not_found, status_code=404)`.

## Exact owner work (new Sprintctl backlog item required)

Create one Sprintctl item titled **“Maintenance capability observable-resource
owner contract”**, blocked on independent GO for this freeze. Its scope is:

1. Add owner tables for random reference mapping, ordered observation events,
   and per-resource non-null recovery floor on SQLite and PostgreSQL.
2. Couple prepare/transition mutation, event append, reference return, and
   snapshot handoff atomically; implement bounded database-backed wake/poll.
3. Add exactly the three `work.maintenance.resource.*` operations frozen above,
   schemas, catalog descriptor (get and changes, with bounded wait on changes),
   bounded-long-poll metadata, redacted projection, and visibility guard.
4. Register an owner decoder for additive resource prepare that emits the neutral
   reference envelope without changing the direct Sprintctl result contract.
5. Publish owner goldens and disposable SQLite/PostgreSQL histories matching
   this freeze. No Vuoro source changes belong in that item.

## Vuoro proof after owner GO

Vuoro adds no schemas beyond the neutral v1 client models. Its adapter proof
loads Sprintctl's accepted goldens, invokes a test-owned/registered decoder, and
exercises catalog-driven `get`, `changes`, and `wait`. The proof must show a
second owner works without new client fields, token parsing, domain branching,
or changes to ActionQ compatibility fixtures.

Required executable histories are frozen exactly in the golden bundle. They
include prepare response loss; snapshot/terminal race; duplicate delivery;
pruning boundaries; cursor floor recovery; restart/disconnect; explicit expiry
materialization and read-only expiry; four-way non-disclosure; redaction;
backend parity; old-client bytes; parallel owner decoders; wait=0; controlled
clock wait=30; early and spurious wakes; and max-100 batching.

Gates are ordered: owner implementation claim; owner test artifacts; independent
owner GO; Vuoro adapter-only claim; client/service/full/wheel/artifact checks;
independent Vuoro GO. Any schema drift, nullable recovery floor, snapshot gap,
token derivation, leaked field, client owner branch, or new Vuoro persistence is
an automatic NO-GO.

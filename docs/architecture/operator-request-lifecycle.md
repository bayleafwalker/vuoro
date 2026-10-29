# Durable operator request lifecycle v1

Status: OA-01/OA-04 source contract, reconciled 29 October 2026. Sprintctl's
schema-28 owner implements durable request records and lifecycle operations;
Vuoro supplies thin client calls and schema-28 composition conformance tests.
AgentOps supplies local CLI/stdio MCP publication, an offline publish outbox
and a read-only continuation-ready fallback. Source-observed completion,
per-dependant delivery acknowledgements, native wake/resume and the human pilot
remain open. Environment adoption is established separately by actual owner
readback. This contract grants no effect authority. Wire models are in
`contracts/operator-requests/main.tsp`.

## Ownership

Vuoro owns this versioned public contract and its provider-neutral conformance
requirements. Sprintctl owns the durable request records and revision
transactions, stored in tables separate from work items, work decisions, and
the v1 agent inbox. Vuoro composes a pinned Sprintctl owner API. AgentOps and
native runtimes publish requests through that contract. Cloud may project the
same owner records, but does not keep request lifecycle state of its own.

This matches Vuoro's existing agent directory/inbox provider boundary while
respecting Sprintctl's ownership of repository-scoped work identity. Request
state does not change work status, operation acceptance, or protected-effect
authority. Dependants point to existing work items and source sessions; they
do not create a second work ledger.

## Packet

`operator-request/v1` has one of four typed kinds: `perform`, `authorize`,
`decide`, or `provide-information`. A packet carries the desired outcome,
reason, exact host/directory/identity/target, secure input acquisition paths,
a complete versioned procedure, and the checked blocker with source revisions
and its freshness bound. It contains no actor, workspace, repository,
authority, token, credential, or transcript fields. The authenticated owner
supplies principal, workspace, and repository metadata. Instruction text is
untrusted content and grants no authority.

Secrets must be acquired through the named secure path and must not be copied
into the packet. Procedure links or commands must be self-contained enough to
execute without reopening a chat. Raw session logs are not an input to this
contract.

## Lifecycle

Every request has a stable `req_<ULID>` identifier and an integer revision.
Writes use an idempotency key and revision compare-and-swap. Publishing the
same request twice replays the first result; a reused key with different
arguments conflicts. A stale revision conflicts. Packet updates advance the
material revision and return the request to `open`; they never reuse an old
operator acceptance. Attaching or withdrawing one dependant advances the
request revision without changing the packet. Withdrawing one dependant leaves
the other dependants active. A source withdrawal is separate from a work item
status change.

Dead or silent sessions do not resolve a request. Freshness is computed from
the checked blocker timestamp and `fresh_until`; an elapsed bound marks the
packet `needs-refresh`, without claiming that the underlying need disappeared.
Missing or inaccessible source evidence is `unknown`. Only explicit
owner-observed completion or a separately labeled human attestation can
resolve a request. A `reported-performed` state is not verified completion.

The owner implements publication, lookup/list, revision, dependant attachment
and withdrawal, procedure progress, performed/partial/uncertain reports,
separately labeled human attestation, decline and supersession. Material
procedure updates to a live request return it to `open` and invalidate its
earlier report as a basis for attestation; terminal requests stay terminal.
Source-observed verification, resolution delivery and native wake remain
separate OA-04 work. Consumers must present unsupported continuation modes
explicitly. AgentOps' continuation-ready packet reads the owner resolution
event and current work links; it neither wakes a runtime nor acknowledges
delivery or authorizes a protected effect.

`work.request.continuation-v1` supplies that metadata-only successor packet in
one owner read. A schema29-capable owner may advertise this operation; the
schema number alone does not prove the operation is available. The request,
exact human-attestation event and active dependant work revisions share one
read-only repeatable-read snapshot. Expired blocker freshness or changed work
produces `needs-refresh`. Unresolved reports and unsupported completion bases
are refused. A consumer must report an unavailable capability instead of
assembling a claimed current packet from separate request and event reads.
Separate calls can observe later work changes, so this read does not authorize
the successor's effects. Release and environment adoption are separate gates.

## Access and storage

Reads require the existing `work:read` grant; request publication and
maintenance require the existing `work:evidence` grant. No default grant is
expanded. Human attestation and decline require the separate existing
`work:approve` entitlement; it grants no protected-effect authority. All reads are scoped by the
authenticated workspace and repository. Outages are errors, never an empty
request list.

The Sprintctl migration is deployment-only. Runtime startup remains read-only
and rejects an owner that lacks the exact schema capability. The request
tables have no foreign-key cascade from a work item, so archival of source
work cannot erase the request history.

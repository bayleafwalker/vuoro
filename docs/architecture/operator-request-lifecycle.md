# Durable operator request lifecycle v1

Status: candidate source contract for OA-01/OA-04, 24 October 2026. This
increment defines the packet, API shapes, and ownership boundary. Durable
owner storage, service composition, native publication, and continuation
delivery remain unimplemented. Nothing is released or deployed, and this
contract is not an authority to perform or authorize an effect. Wire models
are in `contracts/operator-requests/main.tsp`.

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

The first owner increment implements publication, lookup/list, revision,
dependant attachment, and per-dependant withdrawal. Verification, resolution
delivery, and native wake remain separate OA-04 work. Until those qualified
interfaces are available, consumers must present unsupported continuation
modes explicitly and may expose only a continuation-ready packet.

## Access and storage

Reads require the existing `work:read` grant; request publication and
maintenance require the existing `work:evidence` grant. No default grant is
expanded. Operator completion controls require a future separately qualified
operator entitlement; this v1 does not add one. All reads are scoped by the
authenticated workspace and repository. Outages are errors, never an empty
request list.

The Sprintctl migration is deployment-only. Runtime startup remains read-only
and rejects an owner that lacks the exact schema capability. The request
tables have no foreign-key cascade from a work item, so archival of source
work cannot erase the request history.

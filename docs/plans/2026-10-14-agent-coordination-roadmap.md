# Agent directory and durable inbox: roadmap

Status: operator directive, 2026-10-14. Builds on the assessment
`docs/plans/2026-10-14-agent-reachability-and-messaging.md` (revision 2,
commit `41e927b`) without assuming that revision was reviewed, that the
gateway is reachable from every runtime, or that sprintctl provides exclusive
leased claims. M0 findings and the contract decisions are in
`docs/plans/2026-10-14-agent-coordination-m0-baseline.md`.

The roadmap should deliver **a directory and durable inbox for agent-owned
coordination**, followed by one proven cross-runtime continuation. Vuoro hosts
records and transport; sending agents choose recipients, host adapters start
runs, and domain owners enforce work transitions and effect authorization.

## 1. Scope and implementation boundaries

The first release should support:

- Registering principals and runtime capabilities.
- Showing independently evidenced presence and reachability.
- Sending durable requests to a named principal.
- Reading and acknowledging messages through outbound HTTPS.
- Starting a successor through one host adapter.
- Returning run references and evidence to the sender.
- Recovering from duplicate delivery and adapter crashes.

Automatic recipient selection, model failover, hub-driven execution, public
A2A interoperability and push delivery remain separate later decisions.

| Component | Responsibility |
|---|---|
| Sending agent | Select recipient, formulate request, choose request or delegation mode |
| Directory | Project registrations, capabilities and observations |
| Inbox | Persist messages, enforce visibility, expose delivery receipts |
| Host adapter | Read eligible requests, authorize locally, start or recover runs |
| Runtime integration | Translate adapter operations into supported harness operations |
| Sprintctl and other domain owners | Enforce authoritative transitions and record evidence |
| Effect owner | Authorize effects and prevent or reconcile duplicate effects |
| Gateway and credential broker | Authenticate callers and enforce scoped access |

**Non-negotiable distinction:** sprintctl reservations make concurrent work
visible. They do not prove exclusive ownership, prevent duplicate execution or
grant effect authority.

## 2. Milestones and dependency order

| Milestone | Deliverable | Exit condition |
|---|---|---|
| M0 | Verified baseline and approved contracts | Ownership, reachability and authorization assumptions resolved |
| M1 | Directory projection | Two runtime types registered with honest freshness reporting |
| M2a | Durable inbox | Authenticated delivery and recovery contracts pass |
| M2b | One host adapter | One supported harness can start and reconcile successor runs |
| M2c | Recorded continuation | End-to-end continuation and failure histories pass |
| M3 | Operational pilot | Bounded real usage meets reliability and authority criteria |
| M4 | Additional runtime support | Each added runtime passes the same contract suite |
| M5 | Optional capabilities | Separate decisions justify delegation, push or federation |

M2a and the adapter's local implementation can proceed independently after M0
freezes their shared contract. End-to-end validation depends on both.

## 3. M0: establish the baseline and contracts

**Work**

1. Read the complete revision and reconcile it with current target-state and
   product records.
2. Inspect the existing session, work-link, gateway scope, outbox and
   observable-resource implementations.
3. Identify which existing owner records can be reused without creating
   another authority.
4. Test gateway access from the workstation, devbox, the intended interactive
   cloud runtime, and a Codex cloud environment with the required allowlist.
5. Record each result with runtime version, configuration, timestamp and
   authenticated operation tested.
6. Verify one candidate ACP integration for the chosen harness, including
   start, run identification, status inspection and recovery behavior.

A successful HTTPS request proves connectivity only. Authentication,
permission to send or read, and the ability to wake a stopped runtime require
separate checks.

**Required contract decisions**

- Logical principal versus runtime installation versus individual run.
- Explicit recipient addressing and tenant/project boundaries.
- Request-mode authorization.
- Message identity, immutable payload and protocol versioning.
- Delivery acknowledgment versus execution outcome.
- Retention, replay, cancellation and payload limits.
- Relationship between inbox records, work links and authoritative evidence.
- Whether the existing change stream provides sufficient replay and retention.

**Exit condition**

Every implementation dependency has a named owner and either verified support
or an explicit gap. No foundational claim relies on exclusive sprintctl
reservations or assumed runtime reachability.

## 4. M1: implement the directory

**Data and API.** Store stable principal registration separately from
observations about individual runtime instances. Expose principal identity and
tenant/project visibility; declared runtime capabilities and supported message
types; runtime instance and adapter version; observations with source,
observation time and validity period; retirement or revocation status.

Keep these five facts independent:

| Fact | Meaning |
|---|---|
| Registered | An authorized registration exists |
| Observed | A runtime recently reported activity |
| Reachable | A particular communication path succeeded |
| Executing | A run is reported or verified as active |
| Attended | Explicit evidence indicates human attendance |

Expired evidence becomes unknown or stale. Human absence must never be
inferred from missing attendance evidence.

**Validation.** Cross-tenant enumeration is refused. One instance cannot
overwrite another's observations without authority. Stale and unavailable
sources remain visible as such. Multiple instances of one principal remain
distinguishable. Registration does not imply permission to execute.

**Exit condition.** Two runtime types appear correctly, and stopping either
runtime causes its observations to become stale without falsely declaring it
unreachable or unattended.

## 5. M2a: implement the durable inbox

**Message contract.** An immutable envelope containing: message ID and schema
version; authenticated sender and explicit recipient; tenant/project context;
message type, initially `continue`; work-link and correlation references;
request-mode authorization context; payload or artifact references with
integrity information; creation time and any sender-declared validity
constraint. A validity constraint may tell the receiver to refuse an outdated
request. It must not silently introduce hub scheduling or execution-expiry
authority.

**Separate records for separate facts.** Delivery receipts are maintained
independently from execution outcomes: message persisted; recipient observed
message; recipient durably acknowledged receipt; receiver accepted or refused
the request; successor run linked; result or failure evidence linked.
Acknowledgment means the recipient has durably recorded responsibility for
processing the message. It does not mean the work succeeded.

**Delivery behavior.** Repeated submission with the same identity and payload
returns the original result. Reuse of that identity with different content is
rejected. Reads support replay and restart-safe cursors. Recipients can
acknowledge messages idempotently. Polling does not remove messages.
Transport notifications may repeat. Outcomes remain traceable after retention
cleanup. Reuse the existing outbox only where its transaction and recovery
guarantees fit. Keep any execution state machine in its owning component.

**Exit condition.** Delivery survives restarts and repeated requests without
losing accepted messages or conflating acknowledgment with completion.

## 6. M2b: implement one host adapter

Choose **one host and one harness** for the first proof, from M0's measured
capabilities.

**Adapter responsibilities.** Authenticate using a narrowly scoped machine
identity; poll only its permitted inbox; validate message version, recipient,
context and integrity; independently authorize request-mode execution; persist
a processing record before attempting launch; start the successor with the
work-link and continuation context; record the native run identifier; publish
run and outcome references through owner APIs; recover unfinished processing
after restart. The adapter needs durable storage for its launch records. An
in-memory deduplication set is insufficient.

**Critical launch-recovery contract.** The adapter must handle: *the harness
starts successfully, but the adapter crashes before recording the returned run
identifier.* Preferred mechanisms, in order: (1) harness-supported idempotent
launch using a stable request identifier; (2) discovery of an existing run
through a stable correlation identifier; (3) explicit reconciliation that
records an ambiguous launch and prevents blind relaunch. If the harness
supports neither idempotency nor discovery, the first release must expose that
limitation. It cannot promise automatic duplicate-free launch.

**Exit condition.** The adapter starts and identifies a successor, survives
restart, and never treats an ambiguous launch as permission to start another
run automatically.

## 7. M2c: prove one recorded continuation

**Success path.** (1) An interactive session reads the directory. (2) It
selects a specific principal and sends `continue`. (3) The adapter reads and
independently authorizes the request. (4) The adapter starts the successor
through the chosen integration. (5) The successor reads authoritative work
state and continuation evidence. (6) It performs a bounded operation through
the owner's supported API. (7) It records evidence and publishes a result
reference. (8) The originating session can reconstruct the complete chain.
The first operation should be reversible and have an observable
owner-enforced idempotency or revision boundary.

**Required failure histories**

| Failure | Required behavior |
|---|---|
| Sender retries after an uncertain response | One logical message exists |
| Recipient receives the same message repeatedly | One processing record exists |
| Adapter crashes before launch | Processing resumes safely |
| Adapter crashes after launch, before acknowledgment | Existing run is found or ambiguity is surfaced |
| Effect succeeds before result recording fails | Effect owner deduplicates or reconciles the retry |
| Authorization changes before execution | Execution is refused under current policy |
| Cancellation races with execution | Outcome records what actually happened; cancellation never implies rollback |

Also verify overlapping reservations, stale work revisions and gateway
outages. Duplicate computation may occur where exclusivity is unavailable;
duplicate authoritative transitions and effects must have their own
protection.

**Exit condition.** A reviewer can reconstruct message, authorization,
launch, run, owner transition and result from durable records, including the
deliberately induced failures.

## 8. M3: bounded operational pilot

Enable a small, explicit set of principals and projects. Operational
requirements: per-principal and per-tenant quotas; payload and artifact size
limits; retention and deletion policy; credential rotation and revocation;
adapter restart and upgrade procedures; version compatibility and migration
rules; backup and restore rehearsal; disable-new-messages and
disable-new-launches controls; clear disposition of pending and ambiguous
requests. Measure message persistence and delivery latency, age of unread
messages, duplicate submissions and deliveries, launch failures and ambiguous
launches, authorization refusals, time to recover after adapter or gateway
outage, and the proportion of continuations reconstructible from owner
records. Set numerical service targets before the pilot begins, derived from
expected usage.

**Exit condition.** The pilot meets those targets, has no unexplained message
loss or duplicate effects, and demonstrates recovery and revocation in
practice.

## 9. M4: expand runtime coverage

Add one integration at a time: a second harness on the proven host; a second
host; another interactive sender; additional unattended providers where their
supported APIs permit it. Each integration documents read while running,
attention during a run, start after stop, native run identity, launch
idempotency or reconciliation, cancellation behavior, and credential and
permission boundaries, and passes the same conformance and failure suite.
Shared protocol support alone does not establish equivalent behavior.

## 10. M5: optional capabilities, decided separately

| Capability | Prerequisite |
|---|---|
| Delegation mode | Verified authority intersection, revocation and effect-time enforcement across the chain |
| Model failover | Harness-owned policy and evidence that failed or ambiguous attempts cannot repeat effects |
| Push notifications | Demonstrated polling limitation; polling remains the recovery path |
| Public A2A endpoint | Named external consumer and versioned interoperability tests |
| Recipient recommendations | Clear distinction between directory information and authoritative assignment |
| Automatic dispatch | Explicit reconciliation with TS-1, TS-2 and product ownership |

Do not put incomplete delegation semantics into the first release. **Request
mode is sufficient to prove the initial continuation**, because the receiver
makes its own authorization decision.

## 11. Release acceptance

The first supported release is complete when directory facts expose
provenance and freshness; messages survive retries and restarts; transport
receipts remain distinct from work outcomes; one real continuation is
reconstructible end to end; ambiguous launches are reconciled or visibly
stopped; domain owners reject invalid or stale transitions; effect
authorization and duplicate-effect handling are demonstrated; operators can
revoke access, stop new launches and recover pending work; and the
documentation states remaining limitations precisely.

The immediate implementation package is **M0 through M2c**. Estimate that
package after M0 establishes what the existing outbox, identity system and
harness integration actually provide; keep later expansion outside its
acceptance criteria.

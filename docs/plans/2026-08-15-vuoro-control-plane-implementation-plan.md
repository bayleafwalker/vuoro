---
doc_id: vuoro-cp-2026-01-multi-host-control-plane
status: superseded
supersedes: null
---

# Vuoro Multi-Host Control Plane — Implementation Planner

> **Superseded for implementation on 2026-08-20.** The historical task body
> below is retained for provenance, but it must not be decomposed into a live
> backlog. [Native-runtime and execution-federation alignment](2026-08-20-execution-federation-alignment.md)
> replaces its `vuoro-runnerd`, ActionQ daemon/queue/lease/placement, Outctl,
> and namespaced execution-control-plane assumptions.

**Plan ID:** VUORO-CP-2026-01  
**Baseline:** 15 August 2026  
**Status:** Superseded; not ready for backlog decomposition
**Key assumption:** The existing `cred-broker` is reused as part of the OpenBao rollout; this plan does not create a second broker.

## Executive direction

Build two distributable units: `vuoro-control` as a namespaced Helm/Flux release and `vuoro-runnerd` as a host-local service. Reuse `cred-broker` as a separately versioned credential service in the same namespace or through an existing endpoint. Kubernetes owns shared identity, authority, recovery, policy, grant, credential metadata, and evidence indexes. Hosts own live harnesses, worktrees, command execution, local evidence, credential injection, and interactive attachment.

### Definition of done

- Two or more hosts run independent and coordinated sessions through one canonical control plane.
- ACP abstracts local harness control and every update is durably spooled before acknowledgement.
- OpenBao plus the existing cred-broker form a stable logical credential surface for agents, runners, services, CI, and humans.
- Vuoro owns credential tickets, actor/work/session bindings, grants, effect descriptors, policy linkage, and evidence correlation.
- OPA evaluates a Vuoro-owned input schema and starts in observation mode.
- Bounded work paths support semi-independent execution without peer consensus or transparent live migration.

## Architecture

```text
                         Kubernetes: vuoro-system
                +---------------------------------------+
                | vuoro-control                         |
                | ActionQ API/reconciler                |
                | Sprintctl served authority            |
                | event/evidence ingest                 |
                | grant ledger + policy adapter         |
                | OPA                                   |
                | cred-broker (existing, reused)        |
                | PostgreSQL + object references        |
                +-------------------+-------------------+
                                    | cred-broker -> OpenBao
                                    | private runner API
                   +----------------+----------------+
                   |                                 |
            workstation                         devbox/other
            vuoro-runnerd                       vuoro-runnerd
            ACP -> local harness                ACP -> local harness
            Outctl + encrypted spool            Outctl + encrypted spool
            worktrees/tmux                      worktrees/tmux
            credential injection                credential injection
```

## Credential flow


1. An actor or runner requests a logical `credential_ref` with work/session/effect context.
2. Vuoro evaluates authority and, when required, consumes or prepares a grant.
3. Vuoro issues a short-lived signed `CredentialTicket/v1`.
4. The existing cred-broker validates the ticket, maps the logical reference to OpenBao configuration, and requests the credential/lease.
5. The broker returns wrapped or scoped material plus a non-secret `CredentialLeaseRef/v1`.
6. `vuoro-runnerd` injects the credential into one process and registers exact secret values with Outctl redaction in memory.
7. Lease, grant, policy decision, command evidence, and external result are correlated without persisting the secret.

## Repository ownership

| Area | Suggested owner |
|---|---|

| Vuoro / runner protocol | IdentitySchema, runner protocol, runnerd, session/incarnation lineage, composition contracts. |

| ActionQ | Run attempts, leases, reconciliation, placement, child-run delegation, capacity allocation. |

| Sprintctl | Work authority, readiness, plan/work-item lifecycle, portfolio projections. |

| Outctl | Evidence capture, redaction, projection, local spool, manifests, command lifecycle. |

| cred-broker | CredentialTicket validation, logical catalog mapping, OpenBao interaction, lease delivery and provider audit. |

| Policy / politctl | Rego source, tests, bundle release; PolicyInput schema remains in Vuoro core. |

| Infrastructure / appservice | Helm chart composition, Flux deployment, secrets references, NetworkPolicies, release promotion. |


## Milestones


### M0 — Contracts and reuse baseline locked

Freeze ownership and determine exactly what the existing cred-broker already supplies.

**Requires:** FND-001, FND-002, FND-003, FND-004, EVD-001, CRED-000

**Exit gate:**
- Core schemas are versioned
- cred-broker reuse/gap matrix exists
- No duplicate authority or broker is planned


### M1 — Evidence-complete single-host control path

Prove evidence, API, leases, and local durability before ACP and cross-host complexity.

**Requires:** CTL-001, CTL-002, CTL-003, CTL-004, CTL-005, RUN-001, RUN-002, RUN-003, RUN-004, EVD-002, EVD-003, EVD-004, EVD-005

**Exit gate:**
- One run claims, executes, uploads evidence, and settles
- Runner/API restart preserves state


### M2 — ACP and two-host operation

Establish the missing meta-layer and actual cross-host workflow.

**Requires:** RUN-005, RUN-006, RUN-007, ACP-001, ACP-002, ACP-003, ACP-004, ACP-005

**Exit gate:**
- Workstation and devbox run concurrently
- ACP updates survive partition
- Human-started session can be adopted and detached


### M3 — OpenBao/cred-broker credential surface

Make logical credentials stable while preserving short-lived secret and lease semantics.

**Requires:** CRED-001, CRED-002, CRED-003, CRED-004, CRED-005, CRED-006, CRED-007, CRED-008, CRED-009

**Exit gate:**
- Baseline credentials rotate without agent contract changes
- Effect-bound grants reject payload drift
- No secret enters canonical events


### M4 — Policy observation complete

Validate PolicyInput/v1 and Rego against real choices before enforcement.

**Requires:** POL-001, POL-002, POL-003, POL-004, POL-005

**Exit gate:**
- Full backlog decision coverage
- False positives and channels classified
- ToolHive decision recorded


### M5 — Bounded autonomous work paths

Enable semi-independent paths without swarm semantics.

**Requires:** AUT-001, AUT-002, AUT-003, AUT-004, AUT-005, AUT-006, AUT-007

**Exit gate:**
- Cross-host builder-test-review path completes
- Capacity/policy stops work
- No uncertain effect is replayed automatically


### M6 — Production commissioning

Prove recovery, compatibility, disclosure controls, and rollback.

**Requires:** OPS-001, OPS-002, OPS-003, OPS-004, OPS-005, OPS-006, OPS-007

**Exit gate:**
- Restore and N-1 upgrade drills pass
- Known limits are explicit
- Optional dependencies remain optional


## Workstreams and tasks


### FND — Architecture, contracts, and ownership

**Objective:** Freeze the small Vuoro-owned identity and protocol surface before distributed implementation branches.

**Gate:** Every externally visible schema is versioned, owned, compatibility-tested, and independent of any single adapter or provider.


#### FND-001 — Ratify the hybrid control-plane ADR

Record the Kubernetes/host boundary, authority ownership, cred-broker placement, and explicit non-goals.

- **Priority / size:** P0 / S
- **Depends on:** None
- **Outputs:**
  - Accepted ADR
  - Architecture diagram
  - Non-goal register
- **Acceptance:**
  - No full-swarm or transparent live-migration requirement remains implied
  - No second credential broker is introduced


#### FND-002 — Define IdentitySchema/v1

Formalize stable identities for actors, work, paths, sessions, incarnations, runs, hosts, runner boots, leases, credentials, grants, policy decisions, and evidence.

- **Priority / size:** P0 / M
- **Depends on:** FND-001
- **Outputs:**
  - JSON Schema
  - ID generation and immutability rules
  - Example lineage records
- **Acceptance:**
  - Every mutating event carries the required identity tuple
  - Opaque provider session IDs are never treated as portable global IDs


#### FND-003 — Define protocol compatibility and migration policy

Set additive-change, N-1 runner, unknown-major, deprecation, and expand/contract migration rules.

- **Priority / size:** P0 / M
- **Depends on:** FND-002
- **Outputs:**
  - Compatibility matrix
  - Schema-version negotiation
  - Unknown-major behavior
- **Acceptance:**
  - N-1 runners can drain safely
  - Unknown major versions fail before claiming work


#### FND-004 — Assign state and repository ownership

Map each durable state to its owning repository and prohibit duplicated authority.

- **Priority / size:** P0 / S
- **Depends on:** FND-001
- **Outputs:**
  - Ownership matrix
  - Adapter boundary checklist
  - Dependency exit-note template
- **Acceptance:**
  - Sprintctl, ActionQ, Outctl, Vuoro, cred-broker, and policy ownership are explicit
  - Every adopted dependency has a replacement path


### EVD — Outctl evidence envelope

**Objective:** Create a loss-aware, redaction-aware evidence substrate that remains useful even if every later adoption is abandoned.

**Gate:** Large and sensitive outputs remain auditable without leaking plaintext centrally, and every projection is traceable to its exact input.


#### EVD-001 — Specify EvidenceManifest/v1

Define capture class, raw artifact handling, redaction, projection, hashes, retention, and decision/grant references.

- **Priority / size:** P0 / M
- **Depends on:** FND-002, FND-003
- **Outputs:**
  - EvidenceManifest/v1
  - Standard/sensitive/restricted examples
- **Acceptance:**
  - Projector input hash identifies the exact transformed source
  - Manifest contains no secret material


#### EVD-002 — Implement encrypted local evidence spool

Capture stdout/stderr and events crash-safely before upload or projection.

- **Priority / size:** P0 / L
- **Depends on:** EVD-001
- **Outputs:**
  - Encrypted spool
  - Ack/watermark compaction
  - Quota/backpressure behavior
- **Acceptance:**
  - Runner restart preserves unacknowledged evidence
  - Quota exhaustion is explicit and never silently deletes required evidence


#### EVD-003 — Implement capture classification and recorded redaction

Support standard, sensitive, and restricted handling with deliberate lossy transformations.

- **Priority / size:** P0 / L
- **Depends on:** EVD-001, EVD-002
- **Outputs:**
  - Redaction pipeline
  - Ruleset versioning
  - In-memory exact-secret registration
- **Acceptance:**
  - Credential values are removed before central persistence
  - Manifest records input/output hashes, match counts, and raw expiry


#### EVD-004 — Implement projection adapters

Provide native structured, bounded raw, and optional jc projections behind a stable interface.

- **Priority / size:** P1 / M
- **Depends on:** EVD-001
- **Outputs:**
  - Projector/v1
  - bounded-text projector
  - jc adapter where useful
- **Acceptance:**
  - Projection cannot alter command exit status
  - Every lossy view reports omitted bytes and source hash


#### EVD-005 — Implement central evidence ingest and retrieval

Persist manifests in PostgreSQL and artifacts in object storage through bounded upload plans.

- **Priority / size:** P0 / L
- **Depends on:** EVD-001, CTL-002
- **Outputs:**
  - Evidence API
  - Presigned upload/download flow
  - Retention index
- **Acceptance:**
  - Hosts never receive database credentials
  - Missing or expired raw artifacts remain explicit


#### EVD-006 — Run evidence fault and disclosure tests

Exercise 200 MB output, synthetic credentials, partial upload, projector failure, and spool exhaustion.

- **Priority / size:** P0 / M
- **Depends on:** EVD-002, EVD-003, EVD-004, EVD-005
- **Outputs:**
  - Automated test suite
  - Disclosure review report
- **Acceptance:**
  - Sensitive plaintext is not centrally retained
  - Every loss, expiry, or omission is represented in metadata


### CTL — Namespaced Kubernetes control plane

**Objective:** Deploy shared authority, event ingestion, reconciliation, and evidence metadata as a clean Helm/Flux release.

**Gate:** The control plane survives restart and upgrade while two hosts continue or reconcile work without direct database access.


#### CTL-001 — Create vuoro-control Helm/Flux skeleton

Package namespaced workloads without installing hidden cluster-wide operators.

- **Priority / size:** P0 / M
- **Depends on:** FND-001, FND-004
- **Outputs:**
  - Helm chart
  - Flux examples
  - Values schema
- **Acceptance:**
  - Install/uninstall is namespace-bounded
  - cred-broker can run in-release or reference an existing endpoint


#### CTL-002 — Create explicit database schemas and migration jobs

Separate domain ownership and use expand/contract migrations.

- **Priority / size:** P0 / L
- **Depends on:** FND-003, FND-004
- **Outputs:**
  - Schema ownership map
  - Migration Job
  - Rollback-safe procedure
- **Acceptance:**
  - Application startup performs no uncontrolled DDL
  - Migrations are idempotent or fail clearly


#### CTL-003 — Implement host, session, incarnation, run, and work-path APIs

Expose canonical resources to runners and operator tooling.

- **Priority / size:** P0 / L
- **Depends on:** FND-002, CTL-002
- **Outputs:**
  - Versioned API
  - OpenAPI specification
  - Idempotency keys
- **Acceptance:**
  - All mutating requests are idempotent
  - Harness/provider metadata cannot become canonical authority


#### CTL-004 — Implement fenced leases and global capacity pools

Prevent stale runner instances from settling work and model shared provider slots.

- **Priority / size:** P0 / L
- **Depends on:** CTL-003
- **Outputs:**
  - Lease epochs
  - Capacity reservations
  - blocked_capacity state
- **Acceptance:**
  - Old epochs cannot settle, redeem credentials/grants, or create authoritative children
  - Capacity exhaustion is not recorded as execution failure


#### CTL-005 — Implement durable event ingest and watermarks

Accept host outbox and ACP update batches with deduplication.

- **Priority / size:** P0 / L
- **Depends on:** CTL-003, FND-003
- **Outputs:**
  - Event endpoint
  - Origin sequence key
  - Watermark reconciliation
- **Acceptance:**
  - Duplicate batches are harmless
  - Control-plane restart causes no silent event loss


#### CTL-006 — Implement run reconciler

Classify disconnected, uncertain, interrupted, orphaned, and settled runs without dangerous replay.

- **Priority / size:** P0 / L
- **Depends on:** CTL-004, CTL-005
- **Outputs:**
  - Reconciliation state machine
  - Operator actions
  - Safe retry rules
- **Acceptance:**
  - Uncertain external effects block automatic replay
  - Healthy hosts continue when another host disappears


#### CTL-007 — Harden namespace networking and service identity

Apply least-privilege service accounts, private ingress, and explicit service-to-service network policy.

- **Priority / size:** P0 / M
- **Depends on:** CTL-001, CTL-003
- **Outputs:**
  - RBAC
  - Default-deny NetworkPolicies
  - PDBs
  - Private ingress
- **Acceptance:**
  - Only cred-broker reaches OpenBao
  - Hosts reach only the public/private Vuoro API surface


### RUN — Host runner and multi-host execution

**Objective:** Provide a small unprivileged host daemon for workspaces, processes, local durability, and interactive session handling.

**Gate:** Workstation and devbox can run independent sessions concurrently, reconnect after partition, and cannot act with stale epochs.


#### RUN-001 — Create vuoro-runnerd service shell

Build the unprivileged daemon, configuration, health, and local state layout.

- **Priority / size:** P0 / L
- **Depends on:** FND-002, FND-003
- **Outputs:**
  - Daemon
  - Systemd service
  - Config schema
  - Health endpoint
- **Acceptance:**
  - Restart preserves local session and spool state
  - Daemon runs without root by default


#### RUN-002 — Implement runner identity and registration

Use the existing Ed25519 identity for signed registration and one boot incarnation.

- **Priority / size:** P0 / M
- **Depends on:** RUN-001, CTL-003
- **Outputs:**
  - Registration flow
  - host_ref/runner_instance_ref
  - Key rotation path
- **Acceptance:**
  - Stale boot identity cannot act as current runner
  - Control plane stores public identity only


#### RUN-003 — Implement pull-based claim and lease renewal

Let runners advertise capability and claim eligible work without inbound host control.

- **Priority / size:** P0 / L
- **Depends on:** RUN-002, CTL-004
- **Outputs:**
  - Long-poll/stream client
  - Capability advertisement
  - Lease renewal
- **Acceptance:**
  - Cluster never needs SSH into the host
  - Lost connectivity stops new claims but not permitted local continuation


#### RUN-004 — Implement runner outbox and disconnected mode

Buffer events, evidence, and status until acknowledged.

- **Priority / size:** P0 / L
- **Depends on:** RUN-001, CTL-005, EVD-002
- **Outputs:**
  - Local WAL/outbox
  - Replay logic
  - Offline policy
- **Acceptance:**
  - Reconnect resumes from watermark
  - Offline runner cannot obtain new elevated authority


#### RUN-005 — Implement workspace/worktree adapter

Create, validate, fingerprint, and clean up authorized repository workspaces.

- **Priority / size:** P0 / L
- **Depends on:** RUN-003
- **Outputs:**
  - Workspace adapter
  - Head/diff fingerprints
  - Cleanup policy
- **Acceptance:**
  - Run cannot write outside authorized roots
  - Handoff carries Git/evidence references rather than a process assumption


#### RUN-006 — Implement adopt, attach, detach, and handoff UX

Support human-started sessions and explicit successor sessions across hosts.

- **Priority / size:** P1 / M
- **Depends on:** RUN-003, RUN-005, CTL-003
- **Outputs:**
  - CLI commands
  - tmux mapping
  - SSH target resolution
  - Handoff artifact
- **Acceptance:**
  - Manual session can be adopted without losing lineage
  - Cross-host continuation creates a successor incarnation


#### RUN-007 — Package runner for workstation and devbox

Deliver reproducible installation, upgrade, and rollback for actual hosts.

- **Priority / size:** P0 / M
- **Depends on:** RUN-001, RUN-002, RUN-004
- **Outputs:**
  - Nix package/module
  - Arch/systemd path
  - Version reporting
- **Acceptance:**
  - Both hosts can roll back independently
  - Configuration contains references, not long-lived embedded secrets


### ACP — ACP harness boundary

**Objective:** Retire harness-specific session control behind a negotiated local ACP adapter and durable update sink.

**Gate:** One real backlog runs through ACP on two hosts with no silent update loss, and a second harness proves the boundary.


#### ACP-001 — Define Vuoro Runner/v1 harness contract

Create the internal interface implemented by ACP and temporary legacy adapters.

- **Priority / size:** P0 / M
- **Depends on:** FND-002, FND-004
- **Outputs:**
  - Runner/v1
  - Capability model
  - Normalized lifecycle
- **Acceptance:**
  - ActionQ contains no ACP-specific authority fields
  - Adapter replacement leaves canonical data intact


#### ACP-002 — Implement first local ACP adapter

Integrate the primary harness through local stdio and pin protocol/adapter versions.

- **Priority / size:** P0 / L
- **Depends on:** ACP-001, RUN-001
- **Outputs:**
  - ACP client
  - Session new/load/prompt/cancel mapping
  - Capability record
- **Acceptance:**
  - Negotiated capabilities persist per incarnation
  - Unsupported operations fail explicitly


#### ACP-003 — Implement durable ACP update sink

Fsync every session/update locally and forward with origin sequence and watermarks.

- **Priority / size:** P0 / L
- **Depends on:** ACP-002, RUN-004, CTL-005
- **Outputs:**
  - Per-session update WAL
  - Stream epoch
  - Central normalization
- **Acceptance:**
  - Runner/control restart loses no acknowledged update
  - Run cannot settle before prior updates are durable or explicitly unavailable


#### ACP-004 — Implement resume, cancellation, and uncertain-turn rules

Handle capability differences without pretending opaque sessions migrate between hosts.

- **Priority / size:** P0 / L
- **Depends on:** ACP-003, CTL-006
- **Outputs:**
  - Capability matrix
  - Same-host resume
  - Successor-session path
  - Cancel reconciliation
- **Acceptance:**
  - Cross-host recovery never reuses opaque ID without proof
  - Cancellation ends in an explicit final state


#### ACP-005 — Cut over one real runner profile

Use ACP as the sole path for one substantial low-consequence backlog.

- **Priority / size:** P0 / L
- **Depends on:** ACP-004, RUN-007, EVD-006
- **Outputs:**
  - Cutover record
  - Smoke-test results
  - Intervention log
- **Acceptance:**
  - Old path is non-authoritative and deletable
  - Accepted result, intervention, and retry metrics are captured


#### ACP-006 — Prove second harness and retire PTY fallback

Validate the abstraction and force an explicit fallback sunset decision.

- **Priority / size:** P1 / L
- **Depends on:** ACP-005
- **Outputs:**
  - Second adapter
  - Compatibility report
  - PTY removal or exception ADR
- **Acceptance:**
  - No automatic PTY fallback
  - Sunset reviewed by 15 November 2026


### CRED — OpenBao and existing cred-broker rollout

**Objective:** Reuse the existing cred-broker as the stable credential delivery surface while OpenBao owns secret and lease lifecycle.

**Gate:** Agents and other actors consume logical credential references; leases rotate and revoke cleanly; no secret value enters canonical Vuoro state.


#### CRED-000 — Assess existing cred-broker against target contracts

Inventory current authentication, logical naming, OpenBao engines, wrapping, lease operations, audit, idempotency, HA, and failure behavior.

- **Priority / size:** P0 / M
- **Depends on:** FND-001, FND-004
- **Outputs:**
  - Capability matrix
  - Gap list
  - Reuse decision
  - Upgrade/compatibility note
- **Acceptance:**
  - No duplicate broker is planned
  - Every later CRED task is marked reuse, extension, or configuration


#### CRED-001 — Define CredentialCatalog/v1

Expose stable credential_ref names independent of OpenBao mount/path layout.

- **Priority / size:** P0 / M
- **Depends on:** FND-002, CRED-000
- **Outputs:**
  - Catalog schema
  - Naming rules
  - Git/S3/Kubernetes/release/SMTP examples
- **Acceptance:**
  - Agents request logical references, never raw OpenBao paths
  - Catalog changes are versioned and reviewable


#### CRED-002 — Define credential ticket and actor binding

Specify the short-lived signed authorization passed to cred-broker for runners, sessions, services, CI, and humans.

- **Priority / size:** P0 / L
- **Depends on:** CRED-001, RUN-002
- **Outputs:**
  - CredentialTicket/v1
  - Actor/auth matrix
  - Replay and expiry rules
- **Acceptance:**
  - Agent sessions hold no long-lived OpenBao token
  - Ticket binds actor, work, session, host, runner boot, credential_ref, and effect digest where required


#### CRED-003 — Commission cred-broker as the OpenBao delivery plane

Deploy or reference the existing broker, align it to CredentialTicket/v1, and keep OpenBao path/role mapping inside the broker.

- **Priority / size:** P0 / L
- **Depends on:** CRED-000, CRED-002, CTL-001, CTL-007
- **Outputs:**
  - Namespaced deployment or external endpoint mode
  - OpenBao mapping configuration
  - Health and lease operations
- **Acceptance:**
  - vuoro-control does not implement a second provider adapter
  - Only cred-broker needs OpenBao provider access


#### CRED-004 — Integrate runner credential retrieval and injection

Retrieve brokered/wrapped credentials and inject them into one process without prompt or event exposure.

- **Priority / size:** P0 / L
- **Depends on:** CRED-003, RUN-003, EVD-003
- **Outputs:**
  - Runner client
  - Delivery modes
  - tmpfs/memfd cleanup
  - Redaction registration
- **Acceptance:**
  - Secret values never enter prompts or canonical events
  - Revoked/expired material is removed and cannot renew


#### CRED-005 — Implement credential classes and lease policy

Support baseline renewable, scoped work, effect-bound elevated, and break-glass classes.

- **Priority / size:** P0 / M
- **Depends on:** CRED-003, CRED-004
- **Outputs:**
  - Class policy
  - TTL/renewal defaults
  - Offline behavior
- **Acceptance:**
  - Offline runner can use only explicitly allowed low-risk leases
  - Break-glass is human-only and separately audited


#### CRED-006 — Implement Vuoro grant ledger above broker delivery

Add issued, prepared, consumed, revoked, expired, and aborted semantics.

- **Priority / size:** P0 / L
- **Depends on:** CRED-003, CTL-004
- **Outputs:**
  - Grant API/tables
  - Atomic consumption
  - Human authority reference
- **Acceptance:**
  - One-shot meaning is enforced by Vuoro state
  - Duplicate consume requests remain one use


#### CRED-007 — Implement payload-bound EffectDescriptor/v1

Bind grants to resolved manifests, stdin, scripts, patches, targets, refs, and workspace snapshots.

- **Priority / size:** P0 / L
- **Depends on:** CRED-006, RUN-005, EVD-001
- **Outputs:**
  - Canonicalization library
  - Prepare/commit comparison
  - Kubectl/git/script fixtures
- **Acceptance:**
  - Digest mismatch blocks execution
  - Arbitrary elevated shell is classified as a broader approval class


#### CRED-008 — Correlate credential, grant, policy, and evidence audit

Link broker/OpenBao lease references to actor, session, effect, Outctl run, and result.

- **Priority / size:** P0 / M
- **Depends on:** CRED-004, CRED-006, CRED-007, EVD-005
- **Outputs:**
  - Audit schema
  - Operator query/report
- **Acceptance:**
  - Reviewer can reconstruct the authorization and result without seeing secret values


#### CRED-009 — Exercise rotation, revocation, expiry, and outage

Prove routine and emergency credential lifecycle behavior.

- **Priority / size:** P0 / M
- **Depends on:** CRED-005, CRED-008
- **Outputs:**
  - Rotation drill
  - Revocation drill
  - Broker/OpenBao outage test
  - Break-glass test
- **Acceptance:**
  - Revoked leases stop renewal/future use
  - Unrelated local-safe work continues during credential-plane outage


### POL — OPA policy observation and enforcement

**Objective:** Adopt OPA behind a Vuoro-owned decision schema, author Rego directly, and enforce only after measured observation.

**Gate:** A full backlog has tested decisions, effect-channel classification, and measured divergence from operator choices.


#### POL-001 — Define PolicyInput/v1 and PolicyDecision/v1

Design Vuoro-native policy context around identity, authority, workspace, effect, credential class, and prior grants.

- **Priority / size:** P0 / L
- **Depends on:** FND-002, CRED-007
- **Outputs:**
  - JSON Schemas
  - Fixtures
  - Secret-exclusion rules
- **Acceptance:**
  - Input is sufficient to explain decisions
  - No OPA-specific field enters canonical domain state


#### POL-002 — Deploy OPA as a namespaced decision service

Run OPA separately with versioned policy delivery and masked decision logs.

- **Priority / size:** P0 / M
- **Depends on:** CTL-001, CTL-007, POL-001
- **Outputs:**
  - Deployment/service
  - Bundle/config delivery
  - Log masking
- **Acceptance:**
  - Decision ID and policy revision persist in Vuoro
  - OPA can be replaced behind the decision adapter


#### POL-003 — Author baseline Rego and tests

Write direct Rego with fixtures and opa test from the first commit.

- **Priority / size:** P0 / L
- **Depends on:** POL-001, POL-002
- **Outputs:**
  - Baseline policy
  - Unit tests
  - Negative fixtures
  - Explain examples
- **Acceptance:**
  - Every deny/grant branch has a named test
  - Policy review does not rely on production logs


#### POL-004 — Run observation mode over a full backlog

Record would-allow, would-deny, and would-require-grant without blocking.

- **Priority / size:** P0 / L
- **Depends on:** POL-003, CRED-008, ACP-005
- **Outputs:**
  - Decision capture
  - Operator comparison
  - False-positive labels
- **Acceptance:**
  - All encountered consequential effect families are covered
  - OPA outage is recorded but does not block observe-mode work


#### POL-005 — Classify effect channels and decide ToolHive gate

Measure shell, git, Kubernetes, filesystem, network, secret, MCP, and other channels.

- **Priority / size:** P1 / M
- **Depends on:** POL-004
- **Outputs:**
  - Effect-channel report
  - ToolHive adoption/rejection ADR
- **Acceptance:**
  - Decision is based on consequential MCP share and lifecycle ownership, not fashion


#### POL-006 — Promote selected families to enforcement

Enforce only well-tested families with explicit unavailable-policy behavior and rollback.

- **Priority / size:** P1 / L
- **Depends on:** POL-004, CRED-009, OPS-004
- **Outputs:**
  - Enforcement matrix
  - Feature flags
  - Rollback playbook
- **Acceptance:**
  - Consequential effects fail closed where declared
  - Local-safe work continues where declared


### AUT — Bounded autonomous work paths

**Objective:** Enable semi-independent multi-session execution with explicit budgets, capacity pools, stop conditions, and handoffs.

**Gate:** A builder-test-review path crosses two hosts without supervision and stops correctly at every injected authority or capacity boundary.


#### AUT-001 — Implement work_path domain and lineage

Group sessions and runs into explicit semi-independent paths.

- **Priority / size:** P0 / M
- **Depends on:** CTL-003, FND-002
- **Outputs:**
  - Work-path API/state
  - Parent/child lineage
  - Status projection
- **Acceptance:**
  - Every child run has a sponsoring path and authority scope
  - No peer consensus is introduced


#### AUT-002 — Implement delegation and continuation budgets

Bound repositories, profiles, child runs, concurrency, expiry, credentials, and effects.

- **Priority / size:** P0 / L
- **Depends on:** AUT-001, CTL-004, POL-001
- **Outputs:**
  - Delegation/v1
  - Budget accounting
  - Expiry handling
- **Acceptance:**
  - Coordinator cannot widen its own delegation
  - Budget exhaustion yields explicit stop reason


#### AUT-003 — Implement child-run creation and readiness selection

Allow coordinators to request ready work and spawn bounded children through ActionQ.

- **Priority / size:** P0 / L
- **Depends on:** AUT-002, CTL-004
- **Outputs:**
  - Child-run API
  - Readiness integration
  - Idempotent spawn
- **Acceptance:**
  - Duplicate spawn creates one child
  - No peer-to-peer scheduler authority appears


#### AUT-004 — Implement provider capacity allocation

Reserve global subscription/API slots across hosts and work paths.

- **Priority / size:** P0 / M
- **Depends on:** CTL-004, AUT-002
- **Outputs:**
  - Capacity allocator
  - Reservations
  - Fairness policy
- **Acceptance:**
  - Two hosts cannot overbook one pool
  - blocked_capacity differs from blocked_work


#### AUT-005 — Implement portable handoff and successor sessions

Move work through Git, plans, evidence, questions, notes, and completion-contract state.

- **Priority / size:** P0 / L
- **Depends on:** RUN-006, ACP-004, AUT-001
- **Outputs:**
  - Handoff artifact
  - Successor API
  - Operator UX
- **Acceptance:**
  - Cross-host continuation does not assume conversation-store portability
  - Unresolved risks and current evidence are included


#### AUT-006 — Implement stop conditions and escalation

Stop on denial, authority conflict, completion failure, capacity exhaustion, or uncertain external effect.

- **Priority / size:** P0 / M
- **Depends on:** AUT-003, POL-004, CRED-006, CTL-006
- **Outputs:**
  - Stop engine
  - Escalation event
  - Operator commands
- **Acceptance:**
  - No autonomous retry crosses uncertain effect
  - Escalation preserves evidence and pending work


#### AUT-007 — Run cross-host builder-test-review pilot

Exercise a bounded path across workstation and devbox with minimal intervention.

- **Priority / size:** P0 / L
- **Depends on:** AUT-004, AUT-005, AUT-006, ACP-006
- **Outputs:**
  - Pilot report
  - Accepted result
  - Intervention and spend record
- **Acceptance:**
  - At least three child runs complete across two hosts
  - Path stops correctly at injected gates


### OPS — Operations, upgrades, and commissioning

**Objective:** Make the bundle supportable through migrations, compatibility, backup/restore, fault drills, and rollback.

**Gate:** N-1 runners drain safely, restore is demonstrated, and failures result in explicit recoverable states rather than duplicate effects.


#### OPS-001 — Implement health, metrics, and operator status

Expose runner, lease, spool, credential, policy, evidence, and capacity health.

- **Priority / size:** P1 / M
- **Depends on:** CTL-003, RUN-004, CRED-008
- **Outputs:**
  - Metrics
  - Health endpoints
  - Operator status view
- **Acceptance:**
  - One view identifies blocked capacity, disconnected runners, stale leases, failed uploads, and broker/policy errors


#### OPS-002 — Implement backup, restore, and evidence reconciliation

Protect PostgreSQL state and reconcile object artifacts after restore.

- **Priority / size:** P0 / L
- **Depends on:** CTL-002, EVD-005
- **Outputs:**
  - Backup policy
  - Restore drill
  - Orphan reconciliation
- **Acceptance:**
  - Restore preserves identities, grants, watermarks, and audit linkage
  - Missing artifacts remain explicit


#### OPS-003 — Implement version report card and compatibility gates

Report control, runner, adapter, broker, OpenBao contract, OPA policy, and schema compatibility.

- **Priority / size:** P0 / M
- **Depends on:** FND-003, RUN-007, ACP-002, CRED-000
- **Outputs:**
  - Version report card
  - Compatibility endpoint
  - Drain/reject behavior
- **Acceptance:**
  - N-1 runners drain safely
  - Incompatible components fail before claiming or issuing credentials


#### OPS-004 — Write operational runbooks

Document outage, orphan, stale lease, credential compromise, policy failure, migration, and rollback.

- **Priority / size:** P0 / M
- **Depends on:** CTL-006, CRED-009, POL-004
- **Outputs:**
  - Runbook set
  - Operator command examples
- **Acceptance:**
  - Every high-risk state has a bounded recovery path and do-not-do list


#### OPS-005 — Implement controlled release and rollback

Use digest-pinned images, explicit migrations, feature flags, and Helm rollback.

- **Priority / size:** P0 / L
- **Depends on:** CTL-001, CTL-002, FND-003
- **Outputs:**
  - Release pipeline
  - Rollback procedure
  - Provenance metadata
- **Acceptance:**
  - Rollback cannot reactivate stale epochs
  - Database rollback follows expand/contract rules


#### OPS-006 — Run distributed failure-injection suite

Exercise API restart, partition, runner reboot, stale replay, harness death, broker/OpenBao outage, OPA outage, and N-1 upgrade.

- **Priority / size:** P0 / L
- **Depends on:** EVD-006, ACP-005, CRED-009, POL-004, OPS-003
- **Outputs:**
  - Automated drill harness
  - Results report
  - Remediation backlog
- **Acceptance:**
  - No silent event loss or automatic duplicate external effect
  - Healthy paths continue when one host fails


#### OPS-007 — Commission production-ready multi-host bundle

Run disclosure, restore, upgrade, rollback, and bounded autonomy reviews.

- **Priority / size:** P0 / L
- **Depends on:** AUT-007, OPS-002, OPS-005, OPS-006
- **Outputs:**
  - Commissioning report
  - Go/no-go decision
  - Known-limit register
- **Acceptance:**
  - All milestone gates pass or have accepted exceptions
  - Deferred dependencies remain disabled


### OPT — Gated optional adoptions

**Objective:** Evaluate RTK, ToolHive, DBOS, and Beads only after a named failure or topology measurement justifies them.

**Gate:** Each adoption has a trigger, bounded proof, owned-contract boundary, and written exit path.


#### OPT-001 — Measure RTK as shadow projection

Compare RTK output against the actual consumed view without making it authoritative.

- **Priority / size:** P2 / S
- **Depends on:** EVD-004, ACP-005
- **Outputs:**
  - Token/diagnostic/retry comparison
- **Acceptance:**
  - Adoption requires net reduction after retry cost and ACP structured updates


#### OPT-002 — Evaluate ToolHive only after channel measurement

Decide whether MCP carries enough consequential effects and choose one lifecycle owner.

- **Priority / size:** P2 / S
- **Depends on:** POL-005
- **Outputs:**
  - Adoption/rejection ADR
- **Acceptance:**
  - No installation without gate
  - Separate release if adopted


#### OPT-003 — Evaluate DBOS only against named ActionQ failure

Fully cut one low-consequence profile only if current durability is insufficient.

- **Priority / size:** P2 / M
- **Depends on:** OPS-006
- **Outputs:**
  - Failure statement
  - Exit note
  - Cutover pilot
- **Acceptance:**
  - No permanent shadow scheduler
  - Old path remains deletable


#### OPT-004 — Evaluate Beads only against named task-layer failure

Preserve explicit per-item authority and one-way versioned adapters.

- **Priority / size:** P2 / M
- **Depends on:** AUT-007
- **Outputs:**
  - Failure statement
  - Authority ADR
  - Importer proof
- **Acceptance:**
  - No transparent bidirectional sync
  - Unknown major versions fail closed


## Failure-injection matrix

- **Control API restart during active ACP turn:** Runner replays updates from watermark; no duplicate prompt; run remains recoverable.

- **Runner loses cluster connectivity:** Existing allowed local work continues; no new claims or elevated credentials; events spool.

- **Runner restarts with unacknowledged events:** New runner_instance_ref; WAL replays; stale boot cannot settle.

- **Old runner reconnects after replacement:** Lease epoch rejects writes, credential tickets, grants, and child creation.

- **Harness dies after external effect:** Run becomes uncertain/interrupted; Outctl evidence is available; no automatic replay.

- **Duplicate event and settle requests:** Idempotency produces one event sequence and one final result.

- **cred-broker unavailable:** No new credentials; grant state remains intact; unrelated work continues.

- **OpenBao unavailable behind broker:** Broker reports provider failure; no secret fallback; unrelated work continues.

- **Credential expires mid-run:** Renewal follows class policy; failure is explicit; material is removed.

- **OPA unavailable in observe mode:** Evaluation failure recorded; work continues.

- **OPA unavailable in enforcement mode:** Declared consequential effects fail closed; local-safe work follows policy.

- **200 MB output contains synthetic secrets:** No plaintext central retention; manifest records redaction and raw expiry.

- **N-1 runner/broker during upgrade:** Compatible work drains; incompatible claims/tickets are rejected before effect.

- **Cross-host handoff:** Successor receives Git/evidence/plan state; opaque provider session is not assumed portable.

- **Capacity pool exhausted:** Path enters blocked_capacity and resumes after slot release.


## Risk register

| Risk | Severity | Why | Mitigation |
|---|---|---|---|

| Duplicate external effect after partition | Critical | Fenced leases do not undo an effect already started. | Payload-bound grants, prepare/commit verification, uncertain state, no automatic replay. |

| Credential leakage through evidence | Critical | Tools may echo tokens or files. | Short leases, process-scoped injection, in-memory exact redaction, sensitive capture, disclosure tests. |

| cred-broker and Vuoro duplicate authority | High | A broker that decides work policy becomes a second control plane. | Broker validates signed tickets and provider constraints; Vuoro owns work, grant, and policy authority. |

| OpenBao path coupling | High | Domain or agents may depend on mount layout. | Logical CredentialCatalog/v1; mapping stays inside cred-broker; exit note required. |

| ACP adapter churn | High | Adapters may lag protocol or capabilities. | Pin versions, persist capabilities, internal Runner/v1, explicit failures. |

| Policy input omits decisive context | High | OPA can make precise but wrong decisions. | Treat input schema as core; observe full backlog; test every deny/grant branch. |

| Local spool exhaustion | High | Long outages or huge logs can fill disk. | Quotas, backpressure, alerts, bounded continuation, no silent deletion. |

| Subscription capacity misdiagnosed | Medium | More sessions do not create more provider quota. | Global capacity pools and blocked_capacity state. |

| Upgrade strands old runners/broker | High | Version skew can stall or corrupt execution. | N-1 matrix, report card, drain/reject, additive schemas. |

| Optional integration sprawl | Medium | Maintenance rises faster than value. | Named-failure gates, one-at-a-time pilots, exit note before adoption. |


## Dependency exit records


### ACP

- **Value:** Standardized local harness session control and structured updates.
- **Vuoro-owned contracts:** Runner/v1, HarnessCapabilities/v1, NormalizedSessionEvent/v1
- **Exit path:** Replace the adapter behind Runner/v1. Canonical run, session, event, evidence, credential, and grant state remains Vuoro-native.


### OPA

- **Value:** Policy evaluation, tests, decision IDs, bundles, and decision logging.
- **Vuoro-owned contracts:** PolicyInput/v1, PolicyDecision/v1
- **Exit path:** Swap the decision adapter while retaining Vuoro schemas and persisted decisions. Rego is the bounded migration surface.


### OpenBao

- **Value:** Secret storage, issuance, lease, renewal, rotation, revocation, and wrapping.
- **Vuoro-owned contracts:** CredentialCatalog/v1, CredentialTicket/v1, CredentialLeaseRef/v1, Grant/v1, EffectDescriptor/v1
- **Exit path:** Implement another provider behind cred-broker and remap logical credentials. Secret values are not migration data; Vuoro keeps metadata and audit linkage.


### cred-broker (first-party existing component)

- **Value:** Stable actor-facing credential delivery and OpenBao provider translation.
- **Vuoro-owned contracts:** CredentialTicket/v1, CredentialLeaseRef/v1, CredentialCatalog/v1
- **Exit path:** Move provider mapping/delivery behind another service that accepts the same tickets. Do not reimplement grant or policy authority inside the replacement.


## First execution slice

1. Complete `FND-001..004`, `EVD-001`, and `CRED-000`.
2. Reach M1 with evidence, control APIs, runner identity, claims, outbox, and central ingest.
3. Reach M2 with one real ACP cutover and the second host.
4. Commission the existing cred-broker and OpenBao contracts; do not build a broker inside `vuoro-control`.
5. Add the grant ledger and effect descriptors, then OPA observation, then bounded autonomy.

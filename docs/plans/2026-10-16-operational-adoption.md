---
doc_id: vuoro-operational-adoption-2026-10-16
title: Operational adoption and a verifiable public entry point
purpose: proposal
lifecycle: proposed
effective: 2026-10-16
subjects: [estate-enablement, product-direction]
---

# Operational adoption and a verifiable public entry point

16 October 2026. Implementation proposal following the operator's five-area
improvement request and public-site review. This records scope and acceptance
criteria; it does not mark the following work as provisioned, queued in
Sprintctl, released, or deployed. Cross-repository execution tracking remains
in Agentops' Sprintctl backlog.

The governing [15 October direction](../direction/2026-10-15-product-direction.md)
packages existing products around work identity, evidence, authority and
learning. Improve adoption by making one real loop discoverable, repeatable
and reconstructible. "Standard authority" is an ambition, not established
market evidence.

## Reconcile the suggestions with current artifacts

| Suggested area | What exists | What to take forward |
| --- | --- | --- |
| One local entry point and configuration | The optional [native settlement demo](../../packages/vuoro-demo/README.md) exposes `vuoro demo`, isolates PostgreSQL, separates roles and exercises the owner protocol. `vuoro-bootstrap` was retired. The transport client cannot install or initialize authority state. | Expose the evaluation path, add `vuoro doctor`, and measure a fresh user's setup. A persistent workspace installer is a later distribution slice; a demo is not that product. |
| Framework and sandbox adapters | The transport client, MCP edge, released owner operations and native observation ingress exist. Execution, sessions and retries belong to the harness. | One framework-neutral Python lifecycle consumer, then one native adapter and a supplier swap. TypeScript follows a consumer. Provision a sandbox as needed; do not build an execution manager. |
| Verification and stale-result handling | The [settlement scenario](../evidence/2026-09-29-m1-4-settlement-scenario/README.md), [owner conformance suite](../../packages/vuoro-service/tests/conformance/) and demo exercise takeover, stale-result retention, named checks and dependency release. | Make owner verification profiles usable and expose the Decision. No second settlement writer or scheduler. Lease expiry is evaluated on a call; claims fence settlement, not external effects. |
| Self-hosting and state portability | Compose/Kustomize, digest-pinned composition, explicit migrations and compatibility/restore checks exist as evaluation foundations. | Qualify one reference composition with backup, restore, upgrade and role denial. Add a loss-accounted export/import contract before promising SQLite-to-PostgreSQL migration. Helm/OpenTofu are packaging choices after qualification. |
| Visibility and Cloud onboarding | Public MCP and bounded hosted/native evidence exist; the old agent-cockpit is retired. Hosted multi-tenancy is deferred by current direction. | Link proofs, add read-only contention and evidence-completeness views at the supported surface, and improve the existing pilot journey. Broader admission needs a tenant-side protected horizon design. |

ActionQ is not a new execution queue or evidence sink; use harvested invariants
in the reference work provider. Preserve Auditctl history until verified S4
import. Kctl and Outctl do not become adoption dependencies. The disposition
register governs their lifecycle status.

## Delivery sequence and acceptance

| Slice | Owner and dependencies | Acceptance evidence | Recovery / stop condition |
| --- | --- | --- | --- |
| A0: honest public entry point | Vuoro: demo. Vuoro Cloud: pages. Agentops: cross-repo discovery. | A reader reaches an exact source revision and qualified dependencies from the first screen; prerequisites and failures are visible; links name `bayleafwalker/sprintctl`; every proof distinguishes local/hosted and recorded/illustrated. | Revert public copy or optional demo package. No production state touched. Failed setup counts as failed onboarding, not passed qualification. |
| A1: persistent evaluation composition | Vuoro packages pinned Sprintctl authority; owners supply migrations. Follows A0 feedback. | Fresh non-root Unix environment: install, initialize owned workspace, create X→Y, interrupt/take over X, refuse and retain its late report, verify/settle X, observe Y ready, restart and reconstruct history. Repeated initialization preserves state; skewed config fails before mutation. | Refuse unrecognized state directories. Export and restore-test before schema changes. No startup DDL or runtime migration credentials. |
| A2: thin integration contract | Vuoro transport/observation boundary; Sprintctl claim/report semantics; native execution. Requires explicit capability contract. | One Python consumer and independent harness share the work handle. Show call-driven expiry, heartbeat loss, generation binding, idempotent evidence retry, missing observations, cancellation and late-result retention. A provider-complete event cannot bypass checks. Repeat settlement conformance after a harness swap. | Stop settlement reporting on lost ownership. Retain unfinished observations and effect uncertainty. Lease loss does not revoke provider credentials or justify retrying an uncertain effect. |
| A3: configured acceptance checks | Sprintctl authority and protected verifier; Vuoro transports. Requires immutable artifact/Release binding. | Test and diff-scope gates bind to exact artifact and policy revision. Missing, failed, stale or changed-artifact verification leaves X unsettled and Y blocked. Valid Decision releases dependencies. LLM judgment is graded input with provenance, never a self-issued acceptance grant. | Verifier crash leaves outcome pending. Preserve refusal and evidence. Reevaluate changed artifacts; no executed-means-accepted fallback. |
| A4: qualified self-hosting and portability | Vuoro neutral packaging; owner schema/export/import; customer or Appservice rollout. | Documented composition passes install, incompatible-schema refusal, explicit upgrade, isolated restore and runtime DDL denial. Empty-target import preserves identities, dependencies, attempts, Decisions, digests and provenance; manifest reports unsupported fields/artifacts before writes. Repeat a bounded scenario after import. | Freeze source, back up and restore-test; stage target and reject conflicts. One writer after reconciliation. After target writes, rollback needs owner-supported reconciliation/restore, not reconnecting an obsolete source. |
| A5: visibility and existing pilot usability | Current work/evidence read owners and Cloud UI; no revived cockpit service. Requires timestamps and population definitions. | Show generation, advisory overlaps separately from exclusive claims, stale/failing attempts, dependency wait and verification wait. Metrics expose window, n, source age and capture gaps. Reader follows accepted result to Decision and artifacts. | Rebuildable read models; same repository/workspace access scope. Unknown remains unknown. Cloud expansion stays gated even if charts improve. |

Start A1–A4 as bounded work items, not independent platform programs. Do not
preselect LangGraph, CrewAI, AutoGen, LlamaIndex, E2B, Modal and Daytona without
a first consumer. One integration and a real swap beat unqualified adapter lists.

## Configuration decision

Do not introduce a second policy/queue authority through `vuoro.yaml`. The
persistent-workspace slice chooses one versioned distribution manifest and
validates it before side effects. It can reference endpoint/profile, repository
binding, released composition and owner-held verification-policy revision.
Keep credentials in secret references. Unknown fields, unqualified adapters
and incompatible schemas fail closed. The manifest configures composition;
it cannot grant permissions, invent queues or run arbitrary migration hooks.
YAML versus JSON is secondary to those boundaries.

## Public proof and identity requirements

The public page distinguishes these different records:

- September 24 SQLite transcript: advisory reservation and revision checks;
  history, not evidence of exclusive settlement.
- September 29 settlement scenario: scripted processes, local/public-route
  results and cleanup caveat; not commercial-harness takeover proof.
- MI-1 hosted-to-native proof: named handoff/publication and bounded capture
  sample; not blanket estate capture.
- Optional local demo: disposable PG16 with released client/service/owner pins,
  process interruption and verification/settlement checks; no vendor OAuth,
  hosted worker, external execution or database-crash-durability claim.
- Showcase replay and animated takeover: reconstructed/illustrated material,
  labelled at their point of use.

A dated evidence review is not a new live test. Never replace pending with
passed because a newer wheel exists. Recorded hosted takeover needs the actual
public MCP transcript, distinct run/attempt identities, natural lease expiry,
late refusal, current-holder Decision and downstream readiness; redact secrets
and retain raw evidence privately.

Cloud identifies the operator, links `kotona.app`, provides controller/contact
and invitation privacy information beside the form. Disclose actual retention
and processors; do not invent deletion, regional or cryptographic guarantees.
A notice does not fix retention gaps. Keep named alternatives and maturity labels.

## This change's bounded implementation

The optional demo gains `vuoro doctor [--pg-bin DIR] [--json]`: qualified package
versions, Python, non-root Unix execution, writable `/tmp`, Git and both PG16
executables, without starting the fixture. The fixture uses the same PostgreSQL
gate. Diagnosis is not settlement evidence. Deferred owner imports let missing
packages be diagnosed. The demo stays outside workspace and production image.

Remaining slices are planned work, not completion claims. This patch changes
no owner schema, production deployment, framework SDK or admission policy.

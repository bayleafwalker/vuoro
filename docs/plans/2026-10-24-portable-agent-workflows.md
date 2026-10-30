---
doc_id: portable-agent-workflows.vuoro.v1
status: proposed
supersedes: []
---

# Portable work context and execution evidence

Status: investigated implementation plan, 2026-10-24. Source surveyed at
`61df7c27217b1e2818737b19481ebe07e4c9f6bd` (canonical main).
Cross-repository dependencies, PW package definitions and qualification scenarios
are in the [programme plan](https://github.com/bayleafwalker/agentops/blob/main/docs/plans/agentops/2026-10-24-portable-agent-workflows.md).
Documentation landing does not certify implementation or deployment.

## Current boundary and decision

Vuoro coordinates narrow authorities and transports their versioned contracts.
It does not acquire a model router, worker supervisor or second work queue.
`packages/vuoro-worker/README.md` already documents a Managed Agents poller;
its existence does not qualify every vendor launch surface or repository-editing
mode. Current fake-based coverage must remain distinguishable from live
qualification. Existing product/private-workflow plans are inputs, not replaced.

Compose current Sprintctl continuation, reservations, owner read snapshots and
Sovite execution references into a portable caller contract. Preserve field
ownership and freshness. This extends OA/EC work, particularly vuoro#2725,
vuoro#2728, vuoro#2734, vuoro#2735 and vuoro#2737, rather than creating a new
coordination authority.

## Work packages

- **VU-PW1 / contract inventory:** enumerate published operations and versions
  for repo binding, work/readiness, checkpoints, predecessor context, evidence,
  grants and external execution references. Mark unsupported provider or release
  combinations explicitly. Locate missing PW-01 fields in the correct shared
  schema owner; released adapters remain pinned, with compatibility tests.
- **VU-PW2 / portable context:** expose scoped reads that let an authorized
  successor discover current work, exact governing refs, checkpoint and bounded
  deltas. Reference source/artifact bytes by immutable commit/digest and explicit
  access class. Neither a local path nor an unrelated run ID permits reading
  another repository, tenant, predecessor artifact or private coordinator note.
- **VU-PW3 / observed execution:** carry caller/environment version, provider
  operation handle and attempt correlation as provenance. Distinguish submitted,
  acknowledged, started, progress, candidate, verified, accepted, landed and
  deployed; project owner evidence with authority, freshness and unknown states.
  Do not derive accepted work from a provider's successful return or a heartbeat.
- **VU-PW4 / onboarding composition:** let agentops' proposed onboarding command
  resolve and bind an existing authority idempotently. Repository metadata,
  broker grants, host enrollment and work binding remain separate owner writes.
  Report partial progress and conflicts without broadening privileges or treating
  CWD/remotes as authentication.
- **VU-PW5 / consumers:** qualify the existing Managed Agents/native integrations
  against the same contract. Scoped MCP tools may read, coordinate, record and
  propose within grants; do not smuggle a model picker or lease authority into
  them. Consume Sovite repo-session support after its release and qualification.

VU-PW1/2 can proceed with Sprintctl against PW-01 fixtures. VU-PW3/5 depend on
Sovite's supported execution evidence and broker access. Consumer deployment
must pin released contracts, not rely on sibling source checkouts.

## Acceptance and owner safety

Run owner conformance and affected transport/adapter tests. Verify that stale
run context, wrong workspace/repository, revoked grants and missing transfer
permission fail before exposing source bytes. A predecessor link carries context,
not authority; advisory reassignment cannot cancel a live execution. Exercise
lost acknowledgements and duplicated evidence without duplicate effects; retain
old-attempt results as evidence without settling current work. Demonstrate clean
host continuation with no workstation archive, and failure explanations for
unavailable provider capabilities. Do not call qualification complete on fake
poller tests or installed declarations alone.

## October 30 refinement: capture and handoff integrity

The [substrate integration plan](2026-10-30-agent-substrate-integration.md)
adds the following requirements to VU-PW1/2/3/5. They are implementation work,
not changed wire semantics or a claim of released coverage.

- **VU-PW1/2:** the authorized successor packet joins existing work basis,
  semantic note, immutable candidate/effect evidence and source-specific capture
  limits. Native transcript formats stay with their adapters. Restricted raw
  capture and sanitized export have different digests and access decisions;
  a digest does not grant access or guarantee continued availability.
- **VU-PW3:** preserve transport delivery, inbox handling, execution admission,
  process state, observed effect, authorized output release and work settlement
  independently. Present provider/harness switches and cost basis from owner
  observations, including unavailable sources and unknown cost. Missing capture
  cannot be promoted to completeness by a successful provider exit.
- **VU-PW5:** qualify two real native harnesses and a successor on a clean host.
  With the original transcript unavailable, the note and owner evidence must
  expose verified state, unresolved effects and the next checks. Same-provider
  native resume and portable successor continuation are separate capabilities.

The [inbox v1 contract](../architecture/agent-directory-and-inbox.md) already
defines message-wide accepted/refused handling. It has no per-instance timed
offer/reoffer state. Capacity-aware preflight belongs in the consumer before
handling acceptance, with bounded deferral for transient unavailability and
fresh owner checks at execution admission. A future renewable offer protocol
requires a versioned owner contract; existing receipts cannot be reinterpreted.
No existing narrow successor grant gains repository-execution scope.

Add conformance histories for concurrent receivers, busy-instance deferral,
lost acknowledgments, an alive predecessor after lease expiry, incomplete
capture, unavailable artifact bytes, changed export destinations and late
reports. Preserve owner CAS/fences and reconcile unknown effects before retry.
Rollback optional observation/projection adapters independently; retain old
read compatibility, owner Decisions and evidence limitations.

---
doc_id: vuoro-agent-substrate-integration-2026-10-30
title: Capture, continuation and bounded decision experiments
purpose: proposal
lifecycle: proposed
effective: 2026-10-30
subjects: [evidence, learning, execution]
related: [vuoro-model-evaluation-integration-2026-10-25, vuoro-accepted-outcome-learning]
---

# Agent substrate follow-through

Status: **operator-requested implementation plan**. The useful contracts from
the Hanzo/Spool discussion extend existing owner work. This document changes no
runtime, grant, release pin, deployment or qualification status. It introduces
no new top-level evidence object, transcript store, router or fleet service.

The governing direction is the [October 15 product direction and October 29
service amendment](../direction/2026-10-15-product-direction.md). The existing
[portable-workflow programme](https://github.com/bayleafwalker/agentops/blob/main/docs/plans/agentops/2026-10-24-portable-agent-workflows.md),
[evidence-guided programme](https://github.com/bayleafwalker/agentops/blob/main/docs/plans/agentops/2026-10-25-evidence-guided-agent-workflows.md)
and [model-evaluation plan](2026-10-25-model-evaluation-integration.md) retain
their work-package identities and owners.

## Concepts retained and their implementation owners

| Concept | Existing home | Implementation requirement |
| --- | --- | --- |
| Exact capture and honest gaps | AgentOps AE-01 / PW-01 | Immutable native source segments, explicit coordinates and bounded coverage; no normalized transcript or inferred complete tail. |
| Redaction and exact-byte release | AgentOps export preparation; Sovite/resource-owner output boundary | Separate restricted raw and sanitized derivative digests; authorize the entire export manifest for its recipient/destination. Scanning is evidence, never permission. |
| Explicit handoff acceptance | Sprintctl work/inbox owner; Sovite execution owner; Vuoro VU-PW2/3 | Check capability/capacity before accepting executable work; preserve delivery, handling, execution, verification and settlement as distinct facts. |
| Small native adapters and truthful continuity | Sovite/native integration; AgentOps capture; VU-PW3/5 | Declare launch, attach, observe, collect and resume independently; record actual provider/harness/attempt/usage segments and every fallback. |
| Bounded search before inference | Existing isolated native/CI execution; AgentOps experiment; VU-ME5 comparison | Frozen edit space and verifier, candidate/time/compute budgets, ordinary acceptance path, failed attempts and total burden retained. |
| Local typed triage | AgentOps AE-04; VU-ME2 | Compare deterministic rules and an eligible local encoder on held-out cases; measure abstention, calibration and error costs. Model output cannot widen authority. |

The optional browser workroom is a consumer. Cloud or another UI may project
discussion, presence, requests and evidence links through owner APIs. It must
be removable without changing work identity, Decisions or evidence. No
shared-chat product or vendor gateway is selected by this plan.

## Source basis and limits

Clean main snapshots reviewed on 2026-10-30:

| Repository | Revision | Existing implementation/plan |
| --- | --- | --- |
| Vuoro | `2909daa57b60f9967195e65e00b5d484526c0edd` | VU-PW1..5, VU-ME1..4, owner-record learning projections |
| AgentOps | `09498925c72986122a936274a3be7d68684a7f1f` | AE-01, AE-04, historical capsule/note contracts, private artifact capture |
| Sovite | `5a57f75baefbeefeba02eadaec5b324bbb003f31` | Native answer-only adapters, execution leases, output release, repository-session/gateway plans |
| Sprintctl | `bb6e83790e54a25b2367bff2eacd70cb4e8db611` | SC-PW1..5, inbox receipts, continuation and work settlement |

These are source observations, not live-fleet qualification. AgentOps
[PR429](https://github.com/bayleafwalker/agentops/pull/429) is a pending offline
AE-01 coverage-replay candidate. Its synthetic count matrix does not qualify
native capture, source-digest retrieval or restart delivery. Reuse it when
accepted rather than creating a competing evaluator.

The external review established public Spool Hub implementation and Gen's
native-authentication path:
[delivery](https://github.com/csitea/csi-spl/blob/da916ad3d1cb003cc5fed49854cab4c8843be26b/csi-spl-api/src/go/spool-hub-api/internal/hubclient/commit.go),
[claims](https://github.com/csitea/csi-spl/blob/da916ad3d1cb003cc5fed49854cab4c8843be26b/csi-spl-api/src/go/spool-hub-api/internal/store/message_claim.go),
[harness](https://github.com/csitea/csi-spl/blob/da916ad3d1cb003cc5fed49854cab4c8843be26b/csi-spl-orc/src/bash/features/spawn-agents/scripts/spool-harness.sh),
[Gen routing](https://github.com/hanzoai/gen/blob/e0d233c945a09f77b13532596c43a29211f3f93e/src/lib.rs).
These inform requirements; they select no dependency and establish no estate
performance claim. The separate spool.pro assertions remain unverified here.

## Corrections to the illustrative capsule and handoff design

The detailed implementation profile lives in AgentOps'
[capture-integrity and export plan](https://github.com/bayleafwalker/agentops/blob/main/docs/plans/agentops/2026-10-30-capture-integrity-and-export.md).
Vuoro references its artifacts and limitations through the existing evidence
path. Preserve historical `session-capsule/v1` readers and UUID identifiers;
the sketch does not authorize a new v2 producer or resurrect retired session
mechanization. Qualify the minimal native observation profile first.

| Sketch assumption | Required correction |
| --- | --- |
| Hash the envelope minus its signature | If a future content address is introduced, exclude both self-identifier and signature and define canonical bytes. Preserve v1 identity now. |
| A hash chain makes silent loss visible | It detects changes/missing links against known anchors. An unobserved tail or omitted capsule can remain unknown; source closure and collector limits must be explicit. |
| `complete / partial / truncated / crashed` is one axis | Capture coverage and process outcome are independent. A crash may be fully captured; successful execution may have missing output. |
| One raw hash also identifies shared content | Frozen native segments and sanitized exports are distinct immutable artifacts with lineage. Bound coordinates, segment/rotation identity and interval conventions. |
| Redacted means shareable | Authorize the exact export and all attachments/metadata for a reader, destination and purpose under current local policy. A visibility label or scanner Boolean cannot supply this. |
| Capsule alone tells the successor what to do | Mechanical capture references and the semantic session note remain separate. Test the authorized context packet with raw logs unavailable, including owner facts and unresolved effects. |
| Put effects and costs directly in the capsule | Reference owner receipts/observations with evidence class, revision, availability and coverage. Preserve billed/estimated/subscription/local/unknown basis and avoid summing cumulative snapshots twice. |

Current inbox v1 `accepted/refused` is a message-wide handling decision within
the recipient principal, not a renewable per-instance offer. A busy instance
must not reject the entire message merely because another instance could
handle it. The existing predecessor-read/session-note recipe retains its
narrow grant. Timed offers/reoffers need an explicit versioned owner contract
and race qualification; receipt names cannot silently acquire Spool semantics.

Progress observations belong to the executor's renewal/recovery policy. A live
sidecar alone is insufficient; a legitimate long tool call needs a bounded
deadline. Disconnection or lease loss does not establish stopped execution.
Preserve unresolved outcomes and reconcile at the target before repeating an
effect. Work settlement fencing does not provide destination idempotency.

Cloud agents request workflows. The protected-side gateway evaluates current
local execution and egress authority; the admin lane stays separate. Existing
automatic policy decisions stay automatic. This work adds no routine human
approval step and no permission from model scores, messages or manifests.

## Implementation sequence and acceptance

| Order / existing work | Deliverable | Falsifying check |
| --- | --- | --- |
| 1 — AE-01 / PW-01 | Source-linked replay set and native capture profile | Missing stop hook, rotation, truncated output and unknown tail remain distinct; changed source bytes or wrong interval fail integrity. Resolve incident populations before quoting counts. |
| 2 — Sovite output release / SV-PW | Derivative manifest and exact-artifact release evidence | Changed derivative, added attachment, changed destination or revoked authority cannot reuse release permission. Scanner failure never exports raw bytes. |
| 3 — SC-PW and VU-PW2/3 | Portable context and honest handoff states | Concurrent receivers cannot create duplicate accepted execution. Busy-instance deferral does not falsely refuse the message. Lost acknowledgment, late report and unknown effects remain reconstructible. |
| 4 — PW qualification / VU-PW5 | Two real native harnesses and a clean-host successor | Hide the original host and native log. The scoped note/evidence packet identifies verified state, unknowns and next checks. Native resume and successor continuation remain separately labeled. |
| 5 — AE-04 / VU-ME2 and VU-ME5 | Matched optional triage/repair experiments | Include abstention, no improvement, exhausted budgets, verifier failure and unavailable models. Measure full effort/cost and independently checked accepted outcomes. |

Use the existing [Vuoro portable plan](2026-10-24-portable-agent-workflows.md),
[Sprintctl owner plan](https://github.com/bayleafwalker/sprintctl/blob/main/docs/plans/2026-10-24-portable-agent-workflows.md)
and [Sovite execution plan](https://github.com/bayleafwalker/sovite/blob/main/docs/plans/2026-10-24-portable-agent-workflows.md).
New shared wire operations, if a measured gap requires one, enter EC-01 and the
chosen EC-00 generation path in their owning repository. Source, release,
deployment and qualified behavior remain separately recorded facts.

## Hosted consumer follow-through and recovery

Cloud's existing portable-workflow/operator-request track should project the
separate statuses and freshness, link exact authorized evidence, and expose
durable questions. Test missing capture, disconnected workers and unavailable
continuation without inferring completion. Land Cloud changes through its
canonical Forgejo; its GitHub mirror does not establish canonical landing.

Rollback removes optional capture/export adapters, UI projections or trial
configuration while preserving accepted work and immutable evidence. Keep v1
reads and explicit unknown values during mixed-version operation; never
backfill old capture as complete. An unavailable optional collector must not
block otherwise permitted native work. Failed sanitization/authorization
withholds the affected export and records the failure; unrelated permitted
work continues. No schema migration is introduced here. Later migrations
follow the owner's compatibility, backup and forward-recovery procedures.

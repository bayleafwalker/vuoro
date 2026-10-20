---
doc_id: vuoro-agentic-private-workflow-2026-10-20
title: Agent-led private workflow implementation plan
purpose: proposal
lifecycle: draft
effective: 2026-10-20
subjects: [work, identity, delegation, evidence, sovite]
---

# Agent-led private workflow: implementation and acceptance plan

User direction, 20 October 2026: turn the demo's promise into a real workflow across
Vuoro, Sovite and Vuoro Cloud, including agent identities and agent-led setup.
This plan refines the existing product delivery plan B/D and Cloud CL-K06. It does
not replace their owners, waive hosted rollout stops, or authorize production mutation.

## First workflow and measured outcome

WF-01: prepare a household-service maintenance handover from a public fixture of
service observations. A setup agent authors a model/agent proposal under bounded
owner-delegated authority; the owner activates and qualifies it; a separate caller
requests analysis; a workspace-side worker retains the result privately; a successor
session retrieves the exact source/result, verifies its hash and contract, and the
work authority records separate owner acceptance. Success is a usable maintenance
handover with action owners and unresolved observations, not a claim that the model
proved household-service correctness or repaired a real service.

The browser's meeting-notes example illustrates the same contract but is not WF-01
evidence. Use C0 fixture material for the first run. Choose the household fixture
and expected observations before inference. No protected changes to household
services are part of this first read-only artifact workflow. The later protected
operation in delivery-plan B remains a separate, unclosed gate.

Measure first-task time, elapsed time to acceptance, operator interventions,
recovery actions, observed model/runtime, artifact digest, source coverage, and
reported/unknown cost. Retain a denominator including every attempted run.

## Identity and authority contract

Distinguish owner, authenticated setup-agent principal, task-caller principal,
execution-worker identity, verifier/acceptor, runtime session/attempt, and versioned
agent definition. A package/model name is not authentication or attestation.
A setup agent can initially propose model and agent definitions only within its
owner-issued endpoint/destination/harness/tool envelope. It cannot mint identities,
activate policy, sign qualification, enroll workers, read private results, or accept
work. No onward delegation in WF-01. Requests retain principal ID, issuer, expiry,
revocation state, installation/workspace, work/run, operation and request digest.

Permission is the intersection of current principal delegation, workspace policy,
Sovite grant and execution-side authority. Expired/revoked/unknown authority denies
new mutations. Currentness is checked at the actual mutation boundary. Draft
ownership, compare-and-set base generation and operation idempotency are separate
from authentication. Never accept a client-selected tenant or destination as routing
authority. Credentials and private artifact bodies stay outside Cloud.

## Ordered implementation slices

| ID | Owner | Dependency | Concrete delivery and exit evidence |
|---|---|---|---|
| WF-00 | All | Existing truth register A / Cloud 1 | Record source/release/runtime independently; pin fixture, principals, environment and supported API tuples. Unknowns stay unknown. |
| S-AI-01 | Sovite | WF-00 | Opt-in durable expiring/revocable setup principals and owned, bounded model/agent drafts. Owner-only activation uses existing revision invalidation. Negative tests: expiry, revocation, other principal/installation, scope expansion, stale base, attempted activation and credential disclosure. |
| S-AI-02 | Sovite | S-AI-01, hosted S2/S3 where applicable | Supported API/CLI setup flow; idempotent proposal/activation; qualification tied to exact revision. Agent and owner are separately authenticated. Real model must be explicitly configured; no synthetic fallback. |
| S-AI-03 | Sovite UI | S-AI-02 | Model/agent editors produce server-validated drafts; show active versus proposed revision, exact changes, qualification state and role-specific activation. Blocked actions explain the required authority. Browser state cannot activate a policy. |
| V-AI-01 | Vuoro | S-AI-02, released Sovite adapter | Generic capability invocation and durable work/run/receipt correlation. No model fields in client/service authority; Sovite adapter stays owner-owned. Reconstruct work without predecessor chat. |
| V-AI-02 | Vuoro / artifact owners | V-AI-01 | Authorized exact-source and private-result retrieval; checksum-bound verification evidence and named profile; independent owner acceptance. Access-denied/unavailable sources remain distinct. |
| WF-01 | All | Above | Run actual HTTP/worker/model path on C0 fixture; interrupt and resume in a distinct session; refuse stale settlement and missing/wrong-hash verification; preserve private output and sanitized public evidence. |
| C-AI-01 | Cloud | Cloud 1–2, CL-K06 C1–C5 / Sovite S1–S6 | Publish disabled-by-default capability using per-workspace Sovite, dedicated roles, assertion exchange and separately authorized external worker/helper enrollment. Two-workspace refusal tests precede enablement. |
| C-AI-02 | Cloud | C-AI-01, WF-01 | Independent invited user completes setup/task/recovery/review/export without manual SQL or Kubernetes edits. Enforce hosted C0/approved-C1, tenant/platform signer roles, retention and restore gates. |
| C-AI-03 | vuoro.cloud / demo | S-AI-03, V-AI-02; hosted C-AI-01 for live actions | Work/Evidence/Platform share work-run-receipt links and authorized source snippets; walkthrough covers create, activate, qualify, block, retry, verify and accept. Clearly label synthetic demo versus available hosted capability and preserve owner `bayleaf`. |
| V-AI-03 | Vuoro / Cloud / Sovite | C-AI-02 | Reusable workflows, schedules, notifications, global budget ceilings and explicit further delegation. Add only the delegation scopes supported by owner contracts and negative tests. |

First implementation batch: S-AI-01 and its disposable HTTP integration exercise,
plus API consumers/acceptance specification. This is source implementation, not a
statement that WF-01, hosted publication, real inference or target qualification passed.

## First-run protocol

1. Operator creates a disposable local installation and a short-lived setup principal
   limited to the named model endpoint, destination, harness, tools and grant bindings.
2. Setup agent creates a model and agent draft using its own bearer identity, then
   validates it. Record proposal ID, author, base generation and proposed generation.
3. Demonstrate that agent activation and identity issuance are refused. Owner activates
   the exact draft against its unchanged base. New qualification is required when the
   installation's qualification rule requires it; development exceptions are visible.
4. Caller submits the public fixture under an explicit grant/idempotency key. Worker
   executes the configured real model and writes the private artifact.
5. A new verifier session reads the exact artifact through authorized storage, checks
   receipt/work/generation/source/result bindings and hash, and submits verification
   to the owning authority. An opaque receipt alone is insufficient.
6. Owner accepts under the named profile. Repeat with blocked dispatch, revoked setup
   principal, interrupted request, wrong hash and superseded attempt; all must fail
   at their intended boundary without manufacturing success.
7. Export reconstruction evidence. Raw secrets and private output never enter public
   logs, proof summaries or Cloud coordination records. Demonstrate restore separately.

## Required product decisions and boundaries

Agent-led authoring is supported; unattended activation is a later explicitly scoped
capability, not implicit owner impersonation. Sovite owns configuration, admission,
execution and receipts; work/artifact/evidence authorities retain settlement and bytes.
Cloud owns hosting and issuer/exchange currentness, not model policy or inference.
Reuse the current issuer/owner checks; no new OPA/SPIRE control plane is selected.
The plan's local principal is deliberately not a hosted identity or runtime attestation.

## Acceptance record

For each slice record commit, PR, tests, negative cases, supported composition and
remaining gates. A source test or synthetic adapter run cannot close WF-01 real-model,
production isolation, two-tenant, protected-effect or hosted rollout acceptance.
See the matching Sovite implementation plan and Cloud capability plan, both dated
2026-10-20. Canonical copies are Git-owned; tracker state remains in the served tracker.


## Pinned first input and verification profile

The public, explicitly synthetic [maintenance source fixture](../fixtures/private-workflow-maintenance.json)
contains exact snippets M1–M3 and positive/negative content checks. Its byte SHA-256
is `ee7c6bc4670731b054494e14487a0232e7e3b2990255fa477026570472ec4e12`. Preserve the original bytes and bind their digest to the
private artifact and work evidence. The first result is a maintenance handover,
not a claim that any service was repaired.

Use a named `maintenance-handover-v1` verification profile: an independently
authorized verifier reads those source bytes and the complete private result,
checks the recorded result digest, confirms M1–M3 coverage and uncertainty, and
records each positive/negative check with its source references. Mechanical hash
matching and source coverage are necessary but cannot establish semantic truth.
A distinct work authority accepts or rejects the evidence under that profile;
Sovite receipt availability cannot accept it. Wrong digest, missing source access,
missing checks and superseded attempt all deny acceptance. The profile must be
implemented through existing owner contracts before WF-01 can be marked passed.

### Source slice started 20 October

Sovite now has source implementation for S-AI-01: migration 4, opt-in installation
identity, operator-minted bounded principals, owned proposals and operator-only
activation. Its test uses the real policy/database/worker/storage code with a
**deterministic** adapter, including blocked dispatch and retry. It establishes
that slice only. No released Sovite owner adapter is currently composed for this
new flow, no real-model WF-01 acceptance is recorded, and no hosted capability is
enabled by these changes. The next gate is S-AI-02 and V-AI-01, not a broader demo.

---
doc_id: vuoro-outcome-settlement-proposal-2026-11-06
title: Outcome settlement through one protected operations workflow
purpose: proposal
lifecycle: proposed
effective: 2026-11-06
subjects: [product-direction, evidence-home, estate-enablement]
related: [vuoro-product-delivery-plan-2026-10-17, vuoro-agentic-private-workflow-2026-10-20]
---

# Outcome settlement through one protected operations workflow

**Status:** Strengthened planning proposal requested by the operator on 6 November
2026. This document selects a bounded implementation and qualification slice for
delivery-plan B. Source landing records the plan; it does not report a completed
workflow, ratify a new product direction, or authorize a production operation.
Native work state stays with Sprintctl. Labels OS-00 through OS-05 below identify
plan slices, not registered work items.

## Decision and purpose

Build a useful customer-controlled operations workflow that retains the work,
authority, exact artifact, observed effect and acceptance across interrupted
sessions. Reuse the existing work and evidence owners, add the smallest missing
resource-owner adapter, and qualify the complete path before expanding it.

The first operation is **publish one analytics partition in a disposable lab**.
It starts from a named source snapshot and transformation revision, produces an
immutable candidate, and changes a publication pointer under current local
authority. Deliberately lose the response after publication commits. A successor
must discover the committed result and finish the work without publishing again.

The commercial hypothesis is that customers will value a portable record of
which agent work counted and enforcement under their own authority. Test that
hypothesis through this workflow. A general clearing house, outcome billing and
vendor ranking are subsequent product decisions, not prerequisites.

This develops the [accepted direction](../direction/2026-10-15-product-direction.md)
and [delivery plan](2026-10-17-product-delivery-plan.md). The 29 October amendment
keeps complete hosted workflows, isolated execution and storage, workbenches,
labs and the local resource gateway in scope. C/C2 knowledge workspace work and
the hosted-to-native code scenario retain their existing scope and gates. The
operations slice provides a stronger external-effect proof; it does not replace
the wider connected-work product with a ledger or silently cancel other work.

## Why this slice, and what is already available

Source observations below were checked on 6 November; they are not installed or
production observations.

| Existing source | Reuse and remaining question |
|---|---|
| Vuoro delivery B/D and the [private workflow plan](2026-10-20-agentic-private-workflow.md), source `97985f9a34945abf7af004b075315487a9f58883` | Keep work/run/artifact correlation, private output and separate work acceptance. The bounded read-only WF-01 result does not qualify this new protected publication. |
| Sprintctl [exact outcome basis](https://github.com/bayleafwalker/sprintctl/blob/05f919e71c01f1294c822cb37b5b0f09d5a71fad/docs/protocols/exact-outcome-basis.md) and [currentness](https://github.com/bayleafwalker/sprintctl/blob/05f919e71c01f1294c822cb37b5b0f09d5a71fad/docs/protocols/work-currentness.md) | Reuse owner Releases, Decisions, lease reports, continuation and evidence. Discover actual catalog support. A currentness observation explicitly does not authorize effects or fence a later target write. |
| Sovite [resource-owner v2 contract](https://github.com/bayleafwalker/sovite/blob/db7487fe8beb22268bed4d53fdebefaaabeb76c0/docs/contracts/resource-owner-v2.md) | Extend closed operation admission, durable receipts, output contracts and recovery. Existing model-peer/KVM operations do not establish partition-publication semantics or a business identity stable across attempts. |
| AgentOps [execution placement qualification](https://github.com/bayleafwalker/agentops/blob/fb918f787a7626b8f8f9bc24e3973024da9c36de/docs/plans/agentops/2026-11-05-execution-placement-qualification.md) and estate qualification | Reuse capability declarations, installed-path evidence and existing outcome measurement. Do not add a scheduler, a competing measurement store or fabricated native item IDs. |

The [MI-1 source record](https://github.com/bayleafwalker/agentops/blob/f0571b69e2f75101c07be2b813666c4939f73ce5/docs/evidence/2026-10-09-mi1-bindery/README.md)
remains a bounded historical proof: Track B runs 2/3 reconstructible, Track B
effects 1/1, historical ordinary runs 0/3 and historical ordinary effects n=0.
These separate small samples cannot supply a current capture baseline or a
production reliability claim. OS-00 freezes a new population and observation
window instead of turning those numbers into a percentage target.

### Market evidence and the build boundary

Primary supplier documentation inspected on 6 November supports a narrower
claim than an empty market. The table records documented capabilities, not our
qualification of the products or their adoption.

| Existing alternative | Consequence for this plan |
|---|---|
| [AGLedger delegation](https://agledger.ai/docs/guides/delegation/) separates principal and performer; its [approval flow](https://agledger.ai/docs/guides/approvals/) connects verdicts to downstream actions | Independent verdicts and a settlement ledger are not a novelty claim. Compare the existing record contract before building another store. |
| [Rundeck / Runbook Automation MCP](https://docs.rundeck.com/docs/mcp/) exposes jobs, executions and ACL operations to agents | Operations is not an empty category. Integrate an existing execution surface where it supplies the required contract. |
| [Temporal Activities](https://docs.temporal.io/activity-definition) require idempotent effect handling for safe retries | Provision durable execution where useful. A durable workflow alone cannot close an uncertain target effect. |
| [GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches) bind code integration to repository policy | Keep PRs and GitOps as domain acceptance mechanisms. A merge does not establish the effect of an unrelated external operation. |
| [Fin outcome accounting](https://www.intercom.com/help/en/articles/8205718-fin-ai-agent-outcomes) defines billable outcomes and adjustments | Outcome pricing is a buyer-discovery prompt. It does not establish demand for independent billing arbitration. |

The proposed advantage is a useful installed connection between these owners:
work continues, current authority is enforced locally, uncertain effects are
reconciled, and another reader can reconstruct what counted. Neutrality must be
demonstrated by control, export and replacement, including our own replacement.
A platform can implement these mechanisms. Customer trust is earned, not an
architectural impossibility for competitors.

Provision native execution, identity, secrets, policy evaluation, telemetry,
GitOps and established job systems. Keep Sovite as a useful execution and
resource integration component. Do not build a replacement policy language,
general agent memory, compliance suite, billing engine or revived ActionQ runner
for this slice. This sourcing rule does not cancel the accepted workspace or
managed-service obligations.

## Three decisions with different authorities

| Decision or fact | Owner and required binding | Meaning |
|---|---|---|
| Authorize the proposed effect | Resource owner; current grant/policy, operation digest, artifact, target, expected revision and local fencing generation | Permission to attempt this operation within the stated validity and revocation bounds. |
| Establish the target effect | Target journal and scoped observation; operation identity, committed target revision and exact artifact | Applied, definitively not applied, conflicting, or unknown within the observation contract. An executor success message alone is insufficient. |
| Accept the work outcome | Customer-authorized work decision authority; current Work basis, verification profile and evidence references | This result counts under that profile. It does not manufacture a missing target observation. |

Authorization and outcome acceptance may both run automatically under standing
customer policy. The performer cannot confer acceptance authority on itself;
any permitted combined role must be an explicit customer delegation visible in
the evidence. Vendor names and key possession are not substitutes for these
role and credential boundaries. No favorable metric expands a grant by itself.

The reference fixture uses distinct performer, resource executor, verifier and
Work acceptor principals/credentials. It qualifies that separated arrangement;
any broader combined-role policy requires its own explicit qualification.

Keep report, executor receipt, target acknowledgment, observation and acceptance
as distinct evidence classes. They are not five rungs of a trust score. A signed
claim establishes the origin and integrity of that claim, not the target effect.

## Reference operation and recovery contract

### Frozen input and observable result

Use a disposable PostgreSQL publication catalog and a private immutable candidate
artifact. A native pipeline prepares the candidate; the protected operation only
publishes it. The profile binds the dataset/partition, source snapshot digest,
transformation revision, candidate digest, expected current publication revision,
verification profile revision and intended output boundary. Initial data is a
public synthetic fixture. The same contract can later be exercised on approved
private data without exporting its contents.

Bind the intent, admission and receipt to the target installation/incarnation
and installed target-configuration revision as well as its logical name. A
rebound alias or replacement database is not the original target. Retained
follow-ups must address the original binding; configuration drift requires
explicit requalification. If the original target is unavailable, its effect
stays unknown even when the replacement returns not-found.

An installed verifier, controlled separately from the producing attempt, checks
the exact schema, fixture row identities/counts, expected values, source and
transformation bindings, and candidate digest. The producer cannot replace that
verifier or redefine its oracle to make its own output pass. The post-publication
reader checks the committed pointer and referenced bytes. This verifies a named
partition contract, not arbitrary analytical correctness.

### Operation identity and the commit boundary

Assign one durable business-operation identity to the publication intent before
the first attempt. Retries and successor attempts keep it. The immutable intent
digest binds the target, partition, source, transformation, artifact, expected
revision and verification contract. Reusing the identity with different bytes
conflicts. A material change creates a revised intent and fresh evaluation.

The target adapter also handles two different request IDs proposing the same
business operation: a uniqueness rule on the frozen publication intent and a
compare-and-set on the target revision prevent a new transport ID from bypassing
deduplication. Concurrent different publications to the same base revision cannot
both commit. The identifier is correlation, never an authorization token.

The first target protocol commits the publication pointer and its operation
journal receipt in **one target transaction**. Its receipt binds the intent,
previous/new revision and artifact. Resource-local fencing is checked at that
boundary; the adapter must document the serialization point for local revocation
and already-admitted operations. The gate and Work databases are not part of a
global transaction. A Work currentness read can become stale immediately after
its snapshot. Bound and test the propagation/admission window rather than
claiming instant cross-owner revocation.

Do not pass agents broad target write credentials or an arbitrary privileged
shell through the gate. Its exclusivity claim is limited to this configured
agent-originated operation class. Administrators, native controllers and other
authorized writers remain separate actors. An observed change without a matching
receipt is initially unattributed; incomplete capture is not proof of bypass.
Qualifying bypass detection requires a closed observation scope and target actor
attribution as well as the known test attempt.

### Lost response, takeover and rollback

1. The target commits publication and journal, then the test drops the response
   before the gate records success. The gate reports an unknown effect.
2. A successor obtains its own current authority and queries the target journal
   using the same operation identity. A matching committed receipt closes the
   uncertainty without rerunning publication. The independent reader checks the
   resulting artifact and current pointer separately.
3. A missing response, unavailable target, expired journal or weak/incomplete read
   leaves the effect unknown. A not-found result authorizes retry only when the
   adapter proves absence over the complete retained operation scope and excludes
   an in-flight commit. Otherwise pause or escalate under the existing policy.
4. A late predecessor report is retained with its provenance but cannot settle
   newer work. Its actual historical effect evidence is still evidence; rejecting
   a stale report must not erase a real target change.
5. Rollback is a separate authorized operation. It restores the prior pointer
   only if the target still names this publication revision. Newer writes cause
   a conflict, not an unconditional restore. Keep the original and compensation
   receipts; do not rewrite history into “never happened.” Restoring prior content
   advances the target revision instead of restoring an old revision counter, so
   a historical precondition cannot become valid again. Replaying the original
   intent after compensation returns its historical result and current state,
   and must not silently republish it.

Freeze journal and artifact retention for the full retry, audit and recovery
window before qualification. Restore is part of the protocol: invalidate old
admissions using a resource-owned recovery epoch or an equivalent existing fence,
reconcile retained target receipts, and refuse new publication until the two
states agree. Test both an older gate backup with a newer target and an older
target backup. A restored journal with missing history cannot prove nonexecution.
Record this limitation instead of reviving old authority or replaying blindly.

## The record and its custody

Construct the outcome view from existing authoritative Work, gateway, target,
artifact and evidence records. Add missing bindings in their owners. Do not make
the view a second writable source of work decisions, audit findings or receipts.

The minimum reconstruction binds objective and Work/Release basis; principal and
delegation; each attempt; the business operation and immutable artifact; policy
and verifier revisions; admission and target receipts; observations and their
scope/freshness; outcome Decision; and any compensation. Include explicit unknown
or unavailable relationships, capture denominator, export cursor and retention
bounds. A successor gets these current owner facts and the unresolved operation,
not credentials or an obligatory copy of the predecessor's entire conversation.

Customer control is a testable operating contract. The customer controls accepting
roles and resource authority, can revoke the provider, obtain a continuous
authorized export and independently reconstruct/export the record using documented
formats. Hosted storage remains permitted. Customer signing keys alone cannot
establish completeness, prevent selective omission, or guarantee service access.
Where the threat model needs it, anchor checkpoints in customer-controlled storage
and verify sequence continuity. Public coordination receives only the fields
allowed by the output contract; raw private artifacts, prompts, credentials and
unapproved detailed receipts stay in their authorized environment.

The current Sovite resource-owner v2 permits same-class output only (C0 to C0,
C1 to C1, C2 to C2); it does not support declassification. A digest, redaction or
coarse status does not automatically lower classification. The initial fixture
is C0. A private-data extension must use an authorized destination in the same
class or separately qualify a new cross-class release contract.

## Delivery sequence and repository ownership

Each slice must name its installed component versions and actual owner APIs before
execution. Schema/catalog gaps become explicit owner work. Planning can proceed
in parallel; later qualification gates depend on the evidence stated here.

| Slice | Owner and deliverable | Exit evidence |
|---|---|---|
| OS-00: Freeze the case and sourcing decision | Vuoro + AgentOps + resource owner: choose the disposable target, fixture, authority roles, oracle, native execution path, observation window and intervention baseline. Check existing gateway/job capabilities against the required contract. | A runnable boundary specification and an explicit list of missing owner semantics. If an existing product supplies the complete path more cheaply, integrate it and narrow the build. No speculative adapter catalogue. |
| OS-01: Close record joins | Sprintctl/work and artifact/evidence owners; Vuoro composes exact basis, operation, receipt, observation and acceptance views. | Independent reader identifies changed/missing links and distinguishes unknown from rejected. No acceptance written by a projection or performer credential. |
| OS-02: Add one protected publication adapter | Sovite/resource gateway and the named target owner; reuse native execution and existing admission. | Target atomic publication/journal, conditional rollback, current local authorization, scope isolation and durable operation identity pass the fault cases below. |
| OS-03: Qualify interruption and continuation | AgentOps coordinates two distinct real sessions on declared supported harness/host paths. | Installed path survives effect-committed/response-lost interruption; cold successor reconstructs and reconciles. Native launch/attach/observe/collect/resume support is declared separately. A scripted fixture alone cannot close this gate. |
| OS-04: Qualify service composition and custody | Cloud composes released owners; local resource owner retains authority. | Workspace isolation, explicit output release, revocation, restore/export, explanation and independent-user proof. Canonical Cloud source landing, release, deployed observation and qualification remain separate. |
| OS-05: Decide usefulness and expansion | Product/operator with AgentOps measurement and existing ExperimentRecord. | Compare all attempted cases against the frozen baseline, including failures and interventions. A second operator repeats the workflow; a second operation reuses the contract mainly through an adapter. Then continue, narrow or stop. |

Detailed owner plans: [Sprintctl](https://github.com/bayleafwalker/sprintctl/blob/main/docs/plans/2026-11-06-outcome-settlement-owner.md),
[Sovite](https://github.com/bayleafwalker/sovite/blob/main/docs/plans/2026-11-06-outcome-settlement-gateway.md)
and [AgentOps qualification](https://github.com/bayleafwalker/agentops/blob/main/docs/plans/agentops/2026-11-06-outcome-settlement-qualification.md).
Cloud's companion is `docs/plans/2026-11-06-outcome-settlement-service.md` in its
canonical repository; verify its canonical landing before treating it as an
available governing source. No public source link exposes private Cloud material.

### Required falsification cases

| Case | Required observation |
|---|---|
| Normal authorized publication | One committed publication, exact verified bytes, separate outcome acceptance and reconstructible joins. |
| Changed artifact, target revision or verification profile | Refusal at the applicable boundary; no unauthorized new publication or accepted stale basis. |
| Expired/revoked grant, stale attempt, wrong workspace/target | No new effect under that authority; retained evidence and declared revocation window. |
| Direct write using the agent's credentials | Target rejects it. Separate authorized admin mutation is classified externally, not reported as a proven agent bypass. |
| Concurrent/repeated same intent, including different transport IDs | One publication transition for the intent; identical replay result or explicit conflict, never a second application hidden by a new ID. |
| Commit succeeds, response lost; gate crashes before receipt | Effect is unknown until target reconciliation; successor does not reapply. |
| Target read unavailable, journal gap, ambiguous in-flight operation | Unknown remains visible; no inference of absence and no blind retry. |
| Target alias is rebound or installed configuration changes | Retained operations cannot query or publish through the replacement as though it were the original target; reconcile the original incarnation or retain unknown. |
| Late predecessor success and changed current Work basis | Historical report retained; current settlement refuses stale authority/basis. |
| Independent verifier rejects or cannot read bytes | No accepted-outcome claim under the profile; applied effect, if any, remains visible and recovery policy applies. |
| Rollback races a newer publication; original intent is replayed | Conditional rollback conflicts rather than overwriting newer work; original replay does not republish after compensation. |
| Gate/target restore with old authority or missing receipts | Old admissions stay invalid; reconciliation or an explicit unknown blocks unsafe replay. |
| Partial observation/export, denied private artifact access | Coverage and access limits remain explicit. Export hash validity is not mistaken for complete authenticated history. |

## Measurement, adoption and stop conditions

Freeze a case manifest before running: eligible tasks, assigned workflow/harness,
verification revision, environment and time window. Retain failures, abandoned
attempts, missing captures and operator interventions. Count accepted work over
all eligible cases, and reconstructible effects over the declared effect scope.
Unknown cases stay in denominators. Distinguish model-reported, measured and
unavailable cost; keep currencies and measurement windows explicit.

Measure operator minutes for setup, explanation, review, recovery and ongoing
maintenance, plus time to accepted result and safe recovery. Compare with the
existing native runbook/pipeline on equivalent cases. A reduction in review that
creates more integration maintenance is not a win. Reuse AgentOps's frozen
population measurement and [accepted-outcome learning](../architecture/accepted-outcome-learning.md);
volume alone does not make observational vendor comparisons causal.

Expand only when the operator finds the workflow useful, another operator can
repeat it without undocumented rescue, and a second operation does not require
another bespoke execution engine. Require a named buyer and recurring problem
before billing, compliance packaging or a standards submission becomes delivery
scope. Test the purchasing analogy—delegation/order, observed result/receipt,
claimed outcome/invoice—in buyer conversations, without treating it as an
implemented financial control or promising a settlement market.

Narrow or stop when carrying the work handle requires an unavailable vendor
integration; the target cannot establish safe publication/reconciliation; agents
must retain bypass credentials; operator burden exceeds the baseline; customers
do not need the portable record; or comparisons never change a real provisioning
or delegation decision. Prefer an adapter to an existing runbook platform if it
achieves the same qualified outcome with less maintenance. Retain useful owners
and exports even if the standalone commercial hypothesis fails.

## First implementation handoff

Start with OS-00: freeze the fixture, target transaction, operation identity,
authority/credential matrix and failure injection point. Reconcile existing native
work before registering anything; attach the exact committed plan revisions to
the actual owner items. Then implement only the missing OS-01/02 semantics and
their discriminating tests. Documentation and source fixtures cannot close the
installed, independent-user, hosting, recovery or commercial gates.

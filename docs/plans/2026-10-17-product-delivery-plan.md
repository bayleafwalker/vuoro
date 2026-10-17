---
doc_id: vuoro-product-delivery-plan-2026-10-17
title: Vuoro product delivery plan
purpose: proposal
lifecycle: ratified
effective: 2026-10-17
subjects: [product, work-identity, evidence, authority, learning, knowledge-workspace, distribution]
related: [vuoro-knowledge-workspace-phase0, vuoro-knowledge-workspace-roadmap]
---

# Vuoro product delivery plan

**Status:** Accepted planning scope, 17 October 2026; implementation and qualification pending. It makes the full product scope actionable without assigning dates or effort. It does not change the accepted 15 October product direction, current authority owners, deployment state, or release eligibility. Each item remains gated by its stated dependency and evidence. Capacity is operator-led with agent assistance; plans must not assume a staffed engineering team.

This plan participates in the cross-estate dependency map at [AgentOps estate evolution plan](https://github.com/bayleafwalker/agentops/blob/main/docs/plans/agentops/2026-10-17-estate-evolution-plan.md). It records product work and acceptance, not Sprintctl work-item state.

## Product outcome

Carry a human objective across agents, sessions, providers and environments through an operation that the responsible authority accepts, retaining the work identity, authority, evidence, knowledge and recovery information needed to explain and continue it. The operating outcome is reliable household services with declining operator effort. Vuoro supplies the portable contracts and operated workflow; domain products own their domain experience and truth.

Vuoro remains one product with work and evidence, connectors, and protected operations. Sprintctl and other owning repositories retain domain state machines. Vuoro composes their released capabilities. Public coordination does not take custody of customer credentials or protected effects. Accepted settlement is bounded by its named verification profile; it is not a general correctness guarantee.

## Delivery rules

- Select one repeatable end-to-end reference workflow and make it pass on the installed path before broadening scope. The workflow must begin with an identified human objective, preserve context and authority across at least two distinct agent sessions, reach an explicitly accepted operation, and leave enough records for another agent or person to reconstruct what happened and recover safely.
- Use a representative household-service improvement as the outcome reference. Homelab Analytics (HLA) is a domain application and measurement surface, not a reason to move household concepts into Vuoro.
- Treat the Knowledge Workspace as a Vuoro capability. Keep it within the existing workspace owner, version and publication contracts. Promote a separate product experience only after a distinct user and recurring demand are evidenced.
- Preserve existing Talos, Flux, Cilium, CNPG, TrueNAS/Longhorn and Nix foundations. Prefer fresh state, tested recovery, clear release artifacts, stable interfaces and reduced operator toil. A new control plane or broad infrastructure replacement requires a separate demonstrated need.
- Report source implemented, released, deployed and live-qualified as separate states. Every proof identifies its environment, artifact versions, observation time, denominator, capture coverage and limits.
- No item advances by calendar. A blocked item stays blocked until its dependency and acceptance evidence are met; conditional scope remains in this plan with a concrete trigger and gate.

## Work plan

### A. Establish the current operating truth

**Owner:** operator for fleet and release truth; each repository owner for its artifact and contract. **Depends on:** read access to current source, release registries and operational observations. **No deployment or mutation is authorized by this item.**

Record a fresh, timestamped register for Vuoro and Cloud: source commit, signed release, image digest, schema, compatibility manifest, live workload, migration state, available and held tenant populations, reconciliation state, backup freshness, restore evidence, and user-visible entry path. Reconcile incomplete shared-service cutover and held Cloud rollout from current evidence. Mark unknown values unknown; do not infer deployed state from merged code or green source CI.

**Acceptance:** the register names evidence and freshness per field; service and Cloud rows can be independently updated; every mismatch has an owner, next read or gate; no stale status page is presented as current. The Cloud plan owns its hosting rows, while appservice and host owners retain runtime and infrastructure evidence.

### B. Prove one reference workflow end to end

**Owner:** Vuoro product/technical owner with the owning domain repositories. **Depends on:** A; current release composition; a safe test workspace and a named protected operation with a recoverable effect.

Choose and document one real household-service improvement from objective through operation. Specify the objective and stopping conditions; work item and dependencies; knowledge and artifact inputs; each agent/session/attempt identity; delegated scope; required evidence and verification profile; reviewer decision; protected execution authority; target acknowledgement; independent outcome observation; and recovery behavior. Select the household outcome and measurement before implementation.

Run the workflow through the actual supported route. Interrupt it at a point where effect status may be uncertain; recover by querying the target and reconciling before retry. Include a second agent or provider continuing from durable records without private predecessor chat. Preserve rejected or superseded reports as evidence without allowing them to settle current work.

**Acceptance:** an independent reviewer reconstructs the objective, actors, exact source/artifact versions, authority decision, operation, observed target state and accepted result from retained records. A stale attempt cannot settle; the result is accepted under an explicit profile; uncertain effects are not blindly repeated; a new agent can continue; all denominator and missing-capture cases are visible. The workflow records user-perceived time to accepted outcome, operator intervention/recovery effort, and the achieved household outcome.

### C. Complete portable knowledge and artifact use as a workflow capability

**Owner:** `packages/vuoro-workspace` for workspace lifecycle; `vuoro-knowledge` for repository-authored context; Vuoro for integration and evidence references. **Depends on:** B's chosen data and access boundaries, and current package/release/deployment truth from A.

Connect the existing reviewed workspace records to the reference workflow without merging its authority with repository documentation, work settlement or Auditctl findings. Preserve exact artifact bytes and version IDs, checksums, cited source revisions, reviewer and publication history, and approved versus working scope. Resolve repository instructions through the existing Git-authoritative resolver. Keep Git projections one-way and read-only unless a separate owner decision establishes another authority.

Qualify cross-task reuse, current authorization after membership changes, stale-source review signaling, export/import and rebuild of derived indexes. Include original files plus portable Markdown/metadata/relationships in export. Do not add rich extraction, semantic search, arbitrary item permissions or a standalone workspace product without the corresponding evidence or demand trigger.

**Acceptance:** a subsequent session retrieves approved context and the exact cited artifact version; an unavailable or changed source is explicit; drafts do not leak into reader results; permission revocation applies to reads, search, downloads, relationships and feeds; export/import preserves checksums, identities/mappings, versions, relations and publication history; indexes can be rebuilt from canonical data. A distinct workspace-product proposal requires an identified buyer/user, recurring workflow, and evidence that the product wrapper solves a problem not solved by the integrated Vuoro workflow.

### C2. Qualify planned workspace extensions

**Owner:** workspace and knowledge owners, with Vuoro integration review. **Depends on:** C's version, citation, authorization and portability gates. These extension work items are in the accepted plan. Gather the requirements evidence below to define their implementation and qualification; absence of evidence is recorded and informs scope, not a reason to leave the work unplanned.

| Extension | Requirements evidence to collect | Acceptance |
|---|---|---|
| Approved Git export | Identify repository review and portability workflows, formats and users in pilot discovery | One-way export preserves stable Vuoro IDs, versions, relations and approved state; Vuoro remains authority. |
| GitHub source ingestion | Inventory useful canonical knowledge in customer repositories and required source permissions | Selected sources retain source revision, permission mapping, refresh and revocation behavior; no default repository-wide ingestion. |
| Managed Forgejo adapter | Establish native Git and self-hosting requirements, ownership and operating capacity | Repository and workspace access boundaries align; identity mapping, upgrade/restore and operator ownership are proven. Do not implement both forge adapters for hypothetical flexibility. |
| Rich slide/spreadsheet extraction | Measure misses by file type and determine which slide/sheet/cell citations users need | Extracted content cites exact slide, sheet or cell anchors; unsupported and failed extraction remains visible; original bytes stay retrievable. |
| Semantic retrieval | Maintain a representative keyword evaluation and classify misses after corpus/extraction review | The same set improves top-k source retrieval without weakening access filtering, source citations, freshness, latency or cost limits. |
| Review reminders | Determine useful staleness/source-change signals, owner responsibilities and tolerable reminder frequency | Reminders identify affected source/knowledge, owner and action; frequency is controlled and no conclusion is auto-rewritten or unpublished. |
| Item-specific permissions | Map real user audiences and workflows against workspace-wide roles and separation | Revisit the workspace boundary and threat model; prove current authorization across search, snippets, caches, relationships, downloads, exports and feeds before onboarding affected material. |
| Live collaborative editing | Measure conflict and handoff costs; assess whether versioned proposals address observed cases | Separate design defines concurrent edits, conflict resolution, attribution, recovery and publication; accepted changes remain explicit versions. |

These work items are in scope for planning now. The collected evidence refines implementation and qualification scope; where a requirement is not supported by current use, record the finding and implement the smallest safe supported form or document the exact remaining uncertainty.

### D. Make delegation, evidence and learning useful across sessions

**Owner:** Vuoro contracts and projections; native runtimes own execution/delegation internals; Auditctl owns evidence judgements; Sprintctl owns work lifecycle. **Depends on:** B's accepted workflow and owner contracts.

Specify and demonstrate portable records for objective, work, participant identity, session/attempt, delegation, messages/handoffs, artifact versions, evidence class, policy revision, verification profile, acceptance and continuation point. Record delivery, acknowledgement, completion and acceptance as distinct states. Bound delegated authority and preserve each actor's separate authority. A takeover grants authority to the successor only through its own authorization; it does not transfer predecessor credentials.

Add an outcome-linked learning record for the reference workflow: intended outcome, assumptions, intervention, accepted deliverable, observed consequence, capture coverage, cost/effort where observed, and revision to future practice. Distinguish observation from causal explanation. Suppress comparisons when sample size or capture coverage is inadequate; never treat missing as zero.

**Acceptance:** a cold successor can recover from current owner records; handoff validation detects stale source/work state; late predecessor evidence is retained but cannot settle; a material change in target/content/conditions requires reauthorization; outcome reports expose denominators and unknowns. A learning claim is traceable to the observation and method that supports it.

### E. Demonstrate supplier replacement rather than asserting it

**Owner:** Vuoro product/contract owner and the owner of each tested adapter. **Depends on:** B through D; published capability contracts for both sides.

Maintain a swap ledger for the first-use workflow. Test an execution harness/provider replacement first, then telemetry and work-ledger providers when compatible adapters exist. For each swap record the row, versions, adapter/config changes, acceptance checks, evidence classes lost or changed, and unresolved behavior. Keep model/harness choice with the user's runtime. Do not make a vendor ranking or broad independence claim from one swap.

**Acceptance:** the original use case passes after a documented swap without changing the meaning of work identity, evidence, authority or acceptance; limitations and lost observations are explicit. Rows without a successful swap remain “provisioned”, not “proven”. Identity and policy-engine swaps remain gated on their adapters and equivalent authorization/revocation proofs.

### F. Qualify an isolated pilot and a supported distribution

**Owner:** Vuoro owns neutral contracts and public packaging; vuoro-cloud owns hosted composition, tenant lifecycle, hosting operations and its operator surface; domain repositories own released semantics. **Depends on:** A–E, secure tenant boundaries, restore/export proof, support ownership and an agreed cost/adoption measurement method.

Prepare an isolated self-hosted reference distribution that runs without a proprietary coordination service and a hosted pilot path with clear separation of customer-controlled execution and protected authority. Make install, onboarding, upgrade, rollback, backup, restore, export and removal reproducible from released artifacts. Publish adapter capabilities/conformance, supported versions, compatibility and maturity. Add first-use documentation and support boundaries; define who responds to upgrade failures and data recovery.

Evaluate a small invited pilot with people beyond the operator. Capture first successful scoped read, workflow completion, second-agent continuation, accepted outcomes, restore/export rehearsal, support burden, operator time, cost per active workspace, adoption/repeat use, and evidence completeness with denominators. Keep user and workspace data portable. Pilot population and gates are set from current capacity and safety evidence; no public expansion follows automatically.

**Acceptance:** an independent user completes onboarding and the reference workflow without operator shell/database edits; a provider swap is evidenced; a tenant can export/import and restore to the declared target; rollback and upgrade can be performed from the same release contract; support and cost limits are stated; adoption and operational-burden results are sufficient for an explicit continue, narrow, or stop decision. Distribution is a product only if repeat use justifies its ongoing release, support and recovery obligations.

### G. Design multi-tenant and cross-organization authority before enabling it

**Owner:** Vuoro for portable work/authority semantics; vuoro-cloud for hosted tenancy and enforcement composition; legal/product/operator owners for policy and disclosure decisions. **Depends on:** F's pilot and packaging evidence; threat model; tenant-side protected execution and acceptance design.

Produce a design package for tenant identity, organization boundaries, membership, data residency/retention, cross-tenant references, delegation chains, consent, revocation, policy evaluation, budget aggregation, audit visibility, incident handling and accountable support. State which authority remains with each party. Ensure Cloud's role cannot silently become custodian of customer credentials or external effects. Test the case where a work lease is revoked but an external credential remains valid; describe the target-side revocation/expiry limit.

**Acceptance:** independent security and product review resolves the open question of whether vuoro.cloud may hold authority for parties other than the operator; the design demonstrates isolation of reads, search, caches, downloads, feeds, exports and restore; delegation cannot amplify issuer authority; shared budgets cannot multiply per agent; revocation limits and incident responsibilities are explicit. Multi-tenant expansion or cross-organization delegation requires a separate owner decision and passing threat-model tests; this plan does not authorize provisioning either capability.

### G2. Qualify workload identity and attestation requirements

**Owner:** Vuoro identity/authority contract owner with the runtime and broker owners. **Depends on:** B and D's participant/delegation model and evidence from the reference workflow.

Test whether the current authenticated principal, runtime, session/attempt and delegation can be distinguished strongly enough for the reference workflow and its protected operation. Document the trust chain, spoofing boundary, lifecycle, revocation/expiry and evidence available at each enforcement point. Compare workload-identity or attestation foundations only against a concrete failure the current system cannot detect; do not adopt SPIFFE/SPIRE or another control plane by default. If needed, define a bounded adapter and proof that preserves customer custody and does not treat a model name or self-description as identity.

**Acceptance:** the threat model names which identities are authenticated, which are only reported, and what an attacker with a copied token or compromised host can do; a negative test demonstrates refusal or explicitly records the residual risk; provider/runtime changes invalidate only the qualifications that depend on them; the operator can revoke or expire the relevant authority at the actual enforcement point. Without a material uncovered risk in the selected workflow, retain the documented gap and do not add attestation infrastructure.

### H. Explore sustained commitments and domain stewardship

**Owner:** product discovery with HLA/domain owners; Vuoro supplies reusable contracts only where shown useful. **Depends on:** B's observed household outcome and F's evidence about repeat use and operator burden.

Use one HLA household-service scenario to learn whether users benefit from linking a longer-lived commitment to decisions, assumptions, interventions and subsequent consequences. Select one household outcome (for example service availability or recovery time) and measure operator intervention alongside it. Define what should be remembered, what changes should trigger review, what observation would change the decision, and how a user stops or amends the commitment. Keep household semantics and source data in HLA/household systems. Do not create a generic recommendation engine or automatically reopen every dependent decision.

**Acceptance:** a user identifies a meaningful continuing responsibility, reviews a changed assumption with a bounded set of affected decisions, and can distinguish measured consequence from attribution hypothesis. If the user value does not exceed a well-maintained project record, keep this as a domain experiment and do not productize a commitment layer.

### I. Keep Bindery as bounded creative research

**Owner:** Bindery repositories; Vuoro only supplies reusable evidence/work contracts when explicitly adopted. **Depends on:** named field scenario, operator capacity and a finite test oracle.

Maintain the separate Bindery/RA2 creative track as a limited field proof. Use existing roadmap scope and acceptance limitations. Do not make it a prerequisite for Vuoro distribution or merge game-specific concepts into the core product.

**Acceptance:** the named scenario produces a reproducible evidence packet with its field limitations and operator effort recorded; otherwise retain the project as independent creative research and make no core product claim from it.

## Dependency order and decision points

The dependency chain is **A current truth → B reference workflow → C/D portable context and continuation → E documented swap → F isolated pilot/distribution decision**. C2 extensions require their specific trigger plus C's contract gates. G and G2 define required trust/authority boundaries before any expansion or additional identity infrastructure. H depends on observed domain outcomes. I stays separate and bounded. Parallel work is allowed only when it does not assume an unmet dependency or consume capacity needed to recover/operate the current estate.

Every gate ends in an explicit decision: continue with named evidence, revise the contract, narrow the scope, or stop. Demand-gated work remains listed with a concrete trigger and acceptance test; it is not silently dropped.

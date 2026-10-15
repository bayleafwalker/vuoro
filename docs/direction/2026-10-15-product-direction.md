# Vuoro product direction

Oct 15, 2026 · @Juha Huotari

Status: **accepted operator direction**, supplied by the owner on 2026-10-15. The ledger records sourcing intentions and candidate defaults, not provisioning, implementation or deployment already completed. Component choices and the open decisions below remain open.

**vuoro.cloud is a distribution of shipped agentic products plus four contracts of its own.** Agents, runtimes, identity, secrets, telemetry and policy engines are provisioned from the market. Vuoro builds only what no supplier ships and what must survive replacing any of them: work identity across providers, graded evidence, authority and delegation records, and learning from accepted outcomes.

The promise stays: use the agents and tools you choose, keep work connected, understand what happened, decide what autonomy to grant. The method is a provisioning ledger. Every capability in the environment has one of four states: provision as-is, provision with an adapter, build, or defer. A capability already shipped by a supplier is a provisioning decision, not a build order. The reference distribution uses open-source foundations where suitable; commercial execution and identity products are replaceable integrations. One supported distribution can span coordination services, native clients and protected services without combining their trust boundaries.

## Governing principles

**Coordination without custody.** vuoro.cloud coordinates. The customer-controlled protected horizon holds authority and executes effects. Nothing originating in the coordination plane becomes an effect merely because the coordination plane says it should. There is no public-initiated service path into the protected horizon. A protected-side consumer may pull proposals and artifacts, evaluate current local authority and publish receipts. A proposal never supplies its own execution authority.

**Execution neutrality.** Native harnesses and hosted runtimes supply sessions, subagents, memory, retries and tool-call policy at their own gateways. Vuoro connects work where those surfaces end: across providers, machines, sessions and people.

**The abstraction ratchet.** A decision class is delegated to agents once the record shows they handle it. Human judgment is kept only where it still changes outcomes. The human is perpendicular to workflows, not a mandatory step in every workflow. A change to a review boundary is authorized under the existing policy and recorded; a favorable metric alone cannot expand a grant.

## Provisioning ledger

Each row is a sourcing decision. A provisioned row names a product and its configuration; a swap within the row must not change a build contract. Adapter and configuration changes are recorded separately. Candidates are the first use case's defaults, not endorsements or evidence of qualification.

The four contract domains are work identity (WorkRelease), evidence (EvidenceSet), authority (EffectGrant and Decision, including protected acceptance), and learning (ExperimentRecord and revision references). Names describe product contracts; they do not require new stores, duplicate state machines or a frozen schema.

| Capability | State | Product / default | Vuoro responsibility |
| --- | --- | --- | --- |
| Agent loops, sessions, subagents, memory | Provision | Claude cloud / Managed Agents, Codex, ChatGPT | None; adapter declares what it can observe |
| Human identity, service access | Provision | Authentik OIDC; OAuth token exchange | Map principal to delegation record |
| Secret custody | Provision | OpenBao | Bind credentials to attempt, never to work item |
| Telemetry transport, storage | Provision | OpenTelemetry collector; any backend | Reference trace/span IDs; expose capture coverage |
| Durable execution, retries | Provision | Harness-native; Temporal where a provider ships it | None; out of scope by execution neutrality |
| Spend controls | Provision + adapter | Provider-specific enforced limits, quotas or alerts, as supported by the selected product and configuration | Reserve and account; record scope, enforcement, reporting delay and bypass paths; never label an alert a hard cap |
| CI/CD, GitOps, separation of duties | Provision | Forgejo Actions, Argo or the existing deployment system | Delivery and resource-owner controls stay here; protected acceptance checks the exact artifact and current authority before execution |
| Tool and agent transport | Provision + adapter | MCP (2026-07-28 request model; Tasks extension where supported), A2A v1.0 | Carry explicit work handles, delegation references and evidence classes; retain supported older protocol revisions |
| Policy evaluation | Provision + adapter | Cedar or OPA (open decision) | Supply the autonomy policy model, decision records and explanations |
| Agent identity where a provider issues it | Provision + adapter | Entra Agent ID, AgentCore Identity, WIMSE when stable | Relate issued identity to agent, attempt and delegation |
| Work ledger | Provision + adapter | Beads/Dolt or sprintctl as reference provider | Own the work-identity contract; the store is replaceable |
| Work identity across providers; claims, attempts, settlement | Build | sprintctl reference implementation; harvested ActionQ invariants | Contract object: WorkRelease; no ActionQ runner, queue or daemon dependency |
| Evidence classes and reconstruction | Build | Existing work/evidence authority; auditctl historical records until verified S4 import | Contract object: EvidenceSet, derived from authoritative evidence; no new auditctl home or parallel writable store |
| Delegation records, review boundaries, decision log | Build | — | Contract objects: EffectGrant, Decision |
| Protected acceptance of exact artifacts and operations | Build | Protected horizon executor | Digest-bound; the only effect path |
| Experiment records over accepted outcomes | Build | — | ExperimentRecord and references to recipe/profile revisions; observational change records, not an execution compiler |
| Workload attestation (SPIRE) | Defer | — | No forcing decision at homelab + VPS scale |
| Mobile and media surfaces | Defer | — | Return when a first-use-case user needs them |
| Cross-organization delegation | Defer | — | Needs a tenant-side protected horizon design |
| Hosted multi-tenant service | Defer | — | Same dependency as above |

Supplier independence is the ledger's test, not a slogan. A provisioned row earns a bounded replaceability claim through one documented swap. Existing authority and settlement conformance tests remain useful checks; the swap demonstrates the integration claim they cannot establish by themselves. Exports carry relationships, versions, decisions and original artifacts in documented formats. The self-hosted reference runs the build rows without vuoro.cloud.

**Product outcome:** Replacing any provisioned product leaves work identity, authority history and evidence intact, and the ledger says which rows that claim has been proven for.

## First use case

One operator starts a task in a hosted runtime (Claude cloud or ChatGPT). The hosted runtime reaches vuoro.cloud over MCP, claims a work item and produces an artifact. A native harness on the workstation continues the work under its own attempt. Publication happens only through the protected horizon, which accepts the exact artifact by digest. Afterwards a fresh human or agent reconstructs the request, the participants, the authority and the evidence class of the result without the original conversation.

This extends the bounded MI-1 proof into a named reference composition: a compatible hosted Claude runtime and Codex for execution, Authentik for identity, OpenBao for secrets, the OTel collector for traces, and explicitly characterized provider spend controls. MI-1 establishes the bounded handoff/publication case; qualification of this whole composition and a supplier swap remain acceptance work. The build rows supply the three joins: the work handle that crosses MCP, the delegation record the protected executor checks, and the evidence set the reader reconstructs from.

Acceptance test:

- A stale late report from the superseded attempt is refused and retained as evidence.
- An interrupted publication recovers without duplicating the effect.
- A changed artifact or a missing receipt is detected by an independent reader.
- Capture coverage is reported with its denominator.
- One provisioned row is swapped (Codex for Claude Code, or the reverse) and the four checks above still pass.

The case addresses the frictions reported by the operator: state lost at session boundaries, cost-blind reruns of live-infrastructure commands, permission denial treated as a normal loop, and the workstation and devbox never reconciling.

**Product outcome:** A hosted-to-native handoff ends in a published result whose permission and evidence can be checked without the original conversation, on a stack assembled from provisioned products.

## Work identity across sessions and harnesses

Work has an identity that outlives any session. A work item records its objective, dependencies, attempts, claimant, artifact versions and continuation point. Several attempts may contribute; their contributions and authority are recorded separately.

Protocol sessions are not a home for this. MCP's 2026-07-28 request model carries protocol metadata per request, so a work reference travels as an explicit handle passed between tool calls. Older session-based revisions remain supported where needed. Long-running tool work maps to the MCP Tasks extension only when both endpoints support it; otherwise the owner's durable resource reference supplies continuation. Cross-agent requests map to A2A tasks, messages and artifacts. Native subagent communication stays inside its harness; Vuoro bridges only where participants cannot share that mechanism.

Claims are leases. Independent work proceeds in parallel; reservations are advisory. Where a work item needs exclusive completion, a takeover supersedes the earlier claim. That claim's late report is refused as superseded and retained as evidence. This fences settlement of the work record only. Vuoro never fences external effects. Concurrent writes to shared resources resolve by version checks at the resource owner; effects are governed by protected acceptance and the provisioned CI/CD and separation-of-duties rows.

Messages and handoffs are durable and addressed. Each carries sender, recipient, work reference and artifact versions. Delivery, acknowledgment, completion and acceptance are distinct states. Duplicate delivery is tolerated by the record; idempotency of external effects belongs to the executor and the target.

**Product outcome:** Collaborators can be added, replaced or interrupted without losing what the work is or who settled it.

## Evidence classes

Every claim about an outcome carries its evidence class:

1. **Report.** What an agent says it did.
2. **Receipt.** What an executor records it ran, signed where available.
3. **Acknowledgment.** What the target system returned.
4. **Observation.** An independently observed state change.
5. **Acceptance.** A decision that the result counts: automatic under a recorded delegation, or by a named person.

A signature establishes origin and integrity, not effect. An interrupted effect stays unknown until the target is checked, so recovery reconciles rather than repeats. A successful publication links approved content, executor, target response and the resulting published identity. These classes describe different evidence, not a single trust score: acceptance does not prove execution or an observed effect. Missing links are explicit and prevent the corresponding effect claim even when a Decision exists.

Evidence references OpenTelemetry trace and span IDs where they exist. Vuoro does not re-collect telemetry. GenAI mappings are versioned per adapter against the selected semantic-convention revision. A backend swap preserves evidence classes and records any lost observations; it cannot silently promote their assurance. Evidence stays addressable after sessions end. Capture gaps, unavailable artifacts and unsupported observation paths are recorded as such, never inferred away.

**Product outcome:** A reader can tell which results were claimed, which were executed, which took effect and which were accepted.

## Authority, delegation and review boundaries

For every request Vuoro answers four questions: who is acting, for whom, on which work, under what delegation. It distinguishes the accountable person or organization, the durable agent identity, the runtime workload, the attempt, and the delegation. A model name or self-description establishes none of these. Identity issuance is provisioned: the OIDC provider today, Entra Agent ID or AgentCore Identity where a provider issues them, OAuth token exchange where supported, and WIMSE profiles when qualified for the selected environment. Vuoro owns only the binding of an issued identity to work, attempt and delegation.

A delegation names issuer, recipient, actions and resources, time and budget limits, onward-delegation rule and revocation path. Credentials are short-lived and scoped where the target supports it, and held by the provisioned secret store or the protected executor. The rules:

- Delegation can only narrow authority. Expanding it requires an authorized decision.
- A successor obtains authority for its own attempt and does not inherit the predecessor's credentials.
- Message content, tool output and protocol states are inputs, never grants. A2A states that an auth-required transition is not itself authorization; MCP forbids token passthrough.
- Revocation is effective only where it reaches the enforcement point. A changed lease does not revoke a provider token; the remaining exposure is shown.
- Concurrent attempts draw on one commitment budget. Vuoro reserves against it at grant time and accounts reported spend. Hard stops come only from an explicitly enforced provider control or a protected executor that mediates the relevant operation. Alerts, delayed billing and activity outside that enforcement path leave residual exposure that is recorded. Unreported cost renders as unknown, never zero.

People set review boundaries as policy: which action classes, resources and limits proceed automatically, and which are reserved for review. Policies are readable and analyzable before work starts. Every decision records outcome, policy revision and the facts used. Automatic decisions are accountable decisions under an established delegation. Content-specific approval binds to the artifact or operation digest; any material change re-enters evaluation.

A pending review blocks only the dependent effect; other work continues. At its recorded deadline, an unanswered review expires and the dependent effect remains unauthorized; a later attempt needs a fresh evaluation. Silence never supplies approval.

**Product outcome:** People spend judgment on the decisions they reserved. Everything else proceeds under a delegation that can be inspected afterwards.

## Learning from outcomes

Each change to how work is done is a recorded revision or an exact reference to the supplier's revision: a recipe, an observed agent profile, a model or harness choice, a provisioned product, or a review boundary. RecipeRevision and AgentProfileRevision name comparison inputs; they do not compile, select or enforce runtime instructions, skills or models. Revisions are compared on accepted outcomes, not completion events. An ExperimentRecord names the revision, the task grouping, the denominators, the evidence class behind each outcome and the capture coverage, plus rework, recovery effort, human review time and known cost. Comparisons with too few cases or too little capture are reported as inconclusive.

Providers already improve agents inside their own runtime through memory curation, rubric-graded outcomes and evaluations; those are provisioned, not rebuilt. Vuoro's contribution is comparison across providers and over time, against the operator's own acceptance decisions.

The ratchet runs on this record. Review history shows which reserved decisions are approved unchanged. Outcome history shows which delegations later needed rework. A boundary moves only with that evidence in view, and the move is itself a recorded revision.

**Product outcome:** Autonomy expands or contracts on evidence, and each step can be revisited.

## Measures

The first measures are about the record itself: reconstructible runs and effects over total runs and effects; capture coverage by source; age of the last authoritative event. The dated MI-1 sample reports Track B runs 2/3 reconstructible, Track B effects 1/1, historical ordinary runs 0/3 and historical ordinary effects n=0. These separate, small historical samples are not a current estate baseline. Establish a fresh bounded baseline with its observation window, population, denominator and failure classes before setting a coverage target.

Then, per task grouping with visible n: time to acceptance; time waiting on dependencies or review; review time; rework; recovery effort; known and unknown cost. Model, harness, host and instruction provenance support comparisons only where those observations exist.

A provider's session-complete event is linked to work acceptance, never substituted for it. Execution and model-selection decisions stay with the provisioned runtime; the measures inform the operator's provisioning and delegation choices.

**Product outcome:** Operators allocate attention on what was accepted and at what cost, and can see how much of the record is missing.

## Swap test

A provisioned row is replaceable when one documented swap has been run and the first use case's acceptance test still passes without changing the semantics of any build contract. Changes confined to the relevant adapter, provider binding or configuration are allowed and recorded. Each swap is a dated entry: row, product out, product in, what the adapter had to change, what evidence class or observation was lost. A row with no swap on record is provisioned, not proven. The ledger shows both states, and the product makes no independence claim beyond the rows with a swap on record.

The first swaps are the cheap ones: execution harness (Codex for Claude Code), telemetry backend, and work-ledger store. Identity and policy engine come later, once their adapters exist.

**Product outcome:** Supplier independence is a list of swaps actually performed, not a design property asserted.

## Relationship to prior direction

This owner direction supersedes earlier product sourcing and learning exclusions only in the following scope. It changes no running deployment, schema, grant, component observation or historical evidence record.

- The September target's native execution and coordination/authority split remain in force. ActionQ supplies harvested invariants, not a revived execution host.
- Sprintctl is the reference work provider, not the required permanent store. WorkRelease names the portable contract over its existing Release and attempt semantics; a second provider must preserve settlement behavior and pass the swap scenario.
- EvidenceSet remains a derived reconstruction over the accepted evidence home. Auditctl's historical authority and S4 preservation requirements remain in force; this direction creates no second evidence home.
- ExperimentRecord and recipe/profile revision references return to product scope as comparison records. This supersedes the earlier exclusion of those learning concepts. The old compiled AgentProfileRevision and execution-recipe machinery remain retired; TS-3's observation rule is retained.
- Multi-tenant expansion, cross-organization delegation and new media surfaces are deferred as new product investment. Existing Cloud deployments and records remain governed by their current owners; this direction does not delete or disable them.
- Supplier replacement is substantiated row by row through dated swaps. Existing provider-neutral correctness and authority conformance remain verification assets.

The [September target state](https://github.com/bayleafwalker/agentops/blob/main/docs/plans/2026-09-17-target-state.md) carries the corresponding product amendment. The [disposition register](disposition-register.yaml) retains its dated component observations; learning scope above supersedes its older intention-only exclusions, not its artifact status.

## Source notes

- [MI-1 proof and completeness strata](https://github.com/bayleafwalker/agentops/blob/f0571b69e2f75101c07be2b813666c4939f73ce5/docs/evidence/2026-10-09-mi1-bindery/README.md): bounded historical case and sample, not current estate coverage.
- [Adopted coordination and authority split](https://github.com/bayleafwalker/agentops/blob/f0571b69e2f75101c07be2b813666c4939f73ce5/docs/architecture/2026-09-27-agentic-ecosystem-and-split-horizon.md): protected authority remains separate from public coordination.
- [MCP 2026-07-28 transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports) and [authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization): per-request metadata, older-era compatibility and service-specific authorization.
- [A2A specification](https://a2a-protocol.org/latest/specification/): task/message/artifact transport and authorization responsibilities; adapter qualification must name its selected revision.
- [Google Cloud budget behavior](https://docs.cloud.google.com/billing/docs/how-to/budgets) and [AWS budget reporting delay](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html): alerts and spend enforcement differ by product, scope and configuration.

## Open decisions

1. **Policy engine:** Cedar (analyzable, matches AgentCore) or OPA/Rego (general-purpose, already familiar), or policy-as-data with a minimal evaluator until review boundaries stabilize.
2. **Work-ledger store:** keep sprintctl as reference provider, or provision Beads/Dolt behind the WorkRelease contract and let sprintctl become the first swap.
3. **Evidence schema home:** Vuoro-native, or proposed upstream as an OTel GenAI attribute set or a WIMSE audit-record profile once those drafts have standing.
4. **Hosted-runtime entry:** MCP only, or A2A as well for agent-to-agent handoff in the first use case.
5. **Budget enforcement point:** which commitments the protected executor must hard-stop versus merely account, given provider enforcement varies by service and configuration and executor enforcement covers only mediated operations.
6. **ExperimentRecord thresholds:** minimum n and capture coverage below which a comparison is suppressed.
7. **Whether vuoro.cloud ever holds authority for anyone but the operator.** If yes, the deferred rows (cross-organization delegation, multi-tenant hosting) need a tenant-side protected horizon design before any of them is provisioned.

---
doc_id: vuoro-outcome-settlement-product-centre-proposal-2026-11-06
title: Outcome settlement as the product centre
purpose: proposal
lifecycle: proposed
effective: 2026-11-06
subjects: [product-direction]
related: [vuoro-outcome-settlement-proposal-2026-11-06, vuoro-product-delivery-plan-2026-10-17, vuoro-agent-substrate-integration-2026-10-30]
---

# Outcome settlement as the product centre

> **Provenance (added 2026-11-07).** This is the earlier 6 November draft that was
> held only in a local checkout and never reached `main`. Its sibling
> [2026-11-06-outcome-settlement-proposal.md](2026-11-06-outcome-settlement-proposal.md)
> (#310; the local draft carried its `doc_id`) is the base the repository adopted: a
> bounded operations workflow. This file is kept as a separate proposal so the
> product-centre argument, business thesis and open decisions are not lost. It
> changes nothing in the sibling and remains unaccepted.

Status: **proposal, not accepted.** Drafted 2026-11-06 at the operator's
request from a positioning and market review. It changes no direction, plan,
runtime, grant, release pin, deployment or qualification status. If accepted,
it amends the [product direction](../direction/2026-10-15-product-direction.md)
and the [product delivery plan](2026-10-17-product-delivery-plan.md) as listed
under [Changes if accepted](#changes-if-accepted); until then both stand as
written. The review deliberately set current implementation state aside. This
document is about which product is worth building, not about readiness.

## Proposal

Make Vuoro's centre **an independent record of which agent work counted, held
by the customer, and the gate that makes that record binding.** Build three
layers in order: the record, the gate, then comparison over time. Take
operations actions against cluster and LAN resources as the first gated
domain. Provision or integrate everything else.

> Agents from any vendor may propose. Only accepted work counts, and only the
> customer decides what is accepted.

This is not a new direction. It keeps the four contract domains (work identity,
evidence, authority, learning) and moves the emphasis: settlement stops being
the mechanism behind continuity and becomes the product.

## Why: what the market already covers

A short scan on 2026-11-06, mostly of trade press and vendor pages, found most
of a typical "enterprise agent platform" already shipping from platforms.
Vendor documentation was not reviewed in depth; see [Sources](#sources).

| Capability | Already offered by | Assessment |
| --- | --- | --- |
| Agent inventory, identity, permissions, cost | Microsoft Agent 365 with Entra Agent ID; Workday Agent System of Record; ServiceNow AI Control Tower; AgentCore Identity | Platform-owned. Integrate. |
| Policy on tool calls | AgentCore Policy (Cedar or natural language, enforced at a gateway; GA March 2026) | Provision; the engine is already the direction's open decision 1. |
| Quality grading and evals | AgentCore Evaluations (GA March 2026) and eval vendors | Provision. |
| One control plane for several vendors' coding agents, with side-by-side comparison | GitHub Agent HQ (Claude, Codex, Copilot) | Covered for code. |
| Organizational memory | Enterprise search vendors, hyperscaler assistants, agent memory services | Crowded. |
| Private or sovereign inference | Hyperscaler sovereign offerings and several model vendors | Crowded, and competes with likely partners. |
| Audit trail for AI Act or SOC 2 | Open-source hash-chained logs, gateway logging, governance vendors | Easy to build; crowded. |
| Durable retries inside one runtime | Temporal and the harnesses | Commodity; already out of scope by execution neutrality. |

The scan did not find:

- a record of agent work held by someone other than the vendor whose agent did
  the work;
- evidence that separates what an agent claimed from what an executor ran, what
  the target confirmed and what was independently observed;
- work identity across vendors in which a superseded attempt's late result is
  refused for settlement but retained;
- comparison of agents and configurations on the organization's own accepted
  outcomes, rather than on evals, outside code.

Platforms cannot credibly offer the first item for their own agents. A
vendor-held log of that vendor's agent is the vendor vouching for itself. That
makes neutrality the defensible property, not only a stated principle.

The mechanisms are textbook and easy to copy: leases, fencing, an append-only
evidence log and an acceptance state machine. What is hard to copy is position:
being the only path to effects, holding adapters that observe targets, and
being trusted as neutral.

### Code already has settlement

| Settlement concept | Pull request equivalent |
| --- | --- |
| Artifact bound by digest | Commit SHA |
| Verification profile | Required checks |
| Acceptance Decision | Review and merge |
| Superseded attempt refused | Replaced PR closed; stale branch conflicts |
| Effect only through the protected path | Branch protection and deployment from the default branch |

GitHub now wraps this flow around several vendors' agents. Code is therefore
the weakest place to differentiate, even though most of this estate's
dogfooding happens there. The proposal is strongest where no PR equivalent
exists: imperative operations, data changes, effects in business systems, work
handed between organizations, and work that mixes people and agents across
several systems.

## The product

### Layer 1: the settlement record

An append-only record of agent work, held by the customer. For each work item
it keeps:

- the objective and the delegation it runs under;
- each attempt and the artifact it produced, by digest;
- each piece of evidence with its class: report, receipt, acknowledgment,
  observation or acceptance;
- the acceptance Decision.

A superseded attempt's late report is refused for settlement and kept as
evidence.

Two rules make this a product rather than a log:

1. **No agent vendor writes acceptance.** Vendors and their agents submit
   reports and artifacts. Acceptance is a customer Decision, made
   automatically only under a recorded delegation.
2. **The customer holds the record.** It is complete and verifiable without
   trusting the operator of any hosted service (see
   [open decision 1](#open-decisions)).

These are WorkRelease, EvidenceSet and Decision/EffectGrant from the direction.
No new contract domain or store is proposed.

**Product outcome:** for any agent result, a reader can tell whether it
counted, who decided, under which delegation and on what evidence, without
trusting the vendor that ran the agent.

### Layer 2: the gate

A customer-side executor that is the only path to effects in its domain.

1. An agent proposes an operation with an exact artifact.
2. The gate checks the artifact digest and current local authority.
3. The gate performs the operation and writes a receipt.
4. An interrupted operation stays `unknown` until the target is checked, so
   recovery reconciles before any retry.

The gate pulls proposals; there is no inbound path from the coordination plane.
It is the protected executor and the resource-owner gateway of the 29 October
amendment, with ActionQ's acceptance and reconciliation scope.

Without the gate, the record is a better audit log, and audit logs are crowded.
With it, acceptance controls what happens.

The gate also closes the capture problem for effects. Because it is the only
path, every gated effect has a proposal, an artifact and a receipt whether or
not the agent's vendor cooperates. Vendor cooperation then matters only for
claims that cause no effect. Their capture is reported with its denominator,
as the direction already requires.

**Product outcome:** in a gated domain, no effect happens without an accepted
proposal, and none is repeated because its first attempt was interrupted.

### Layer 3: comparison and the ratchet

Once accepted outcomes accumulate, the record shows which agent, model, harness
or configuration produced accepted work, with how much rework, review time,
recovery effort and known cost. Review boundaries then move on that history as
recorded revisions. This is ExperimentRecord and the abstraction ratchet as the
direction already describes them.

It comes last because it needs volume. It is also the part that compounds: a
customer's own accepted-outcome history is what a competitor cannot replicate.

**Product outcome:** autonomy and supplier choices change on the customer's
accepted outcomes, and each change can be revisited.

## First gated domain: operations actions

Examples: restarts and rollouts, Flux suspend and resume, credential rotation,
scaling, database migrations, backup and restore, incident remediation.

These actions:

- are imperative and do not pass through a PR;
- are increasingly proposed or taken by AI operations agents, each vendor
  keeping its own record;
- fail the way the gate is designed for. An action interrupted halfway leaves
  the system in a state nobody can name; the right state for it is "uncertain
  until checked", not a retry;
- fit the delivery plan's operating outcome (reliable household services with
  declining operator effort) and its workstream B, which already asks for a
  named protected operation with a recoverable effect, interrupted at a point
  where effect status is uncertain;
- dogfood with real stakes, because this estate is mostly infrastructure.

The held-rollout qualification in vuoro-cloud
`20-PRODUCT-DISTRIBUTION-PLAN.md` (fence writes, back up, migrate, roll, inspect
the target before retrying an uncertain write) is the same pattern at higher
stakes. [Open decision 3](#open-decisions) covers the order.

## Business thesis to test

Finance already has a control for this: the three-way match, in which the
purchase order, goods receipt and invoice must agree before payment. Agent work
maps onto it.

| Three-way match | Agent work |
| --- | --- |
| Purchase order | Delegation (EffectGrant) |
| Goods receipt | Receipt, acknowledgment and observation |
| Invoice | The vendor's claimed or billed outcome |

Some agent vendors already charge per outcome, for example customer-service
agents charged per resolution. When billing depends on whether work counted,
someone other than the vendor sending the invoice has to decide. "Settlement"
then means what it means in finance.

This proposal does not build billing reconciliation first. It uses the framing
in buyer conversations to learn whether it names a budgeted problem.

## Provision or integrate; do not build

| Capability | Treatment |
| --- | --- |
| Agent registry, identity, cockpit | Integrate with the identity provider and platform registries (ledger rows for human and agent identity). |
| Policy engine | Provision Cedar or OPA (the direction's open decision 1). |
| Organizational memory | Provision. Keep only the record of which exact sources were supplied to work, which is evidence. |
| Durable execution and retries | Harness-native or Temporal, as the ledger already states. |
| Private inference platform | Sovite remains an independently useful component and an execution environment the gate can serve. It is not positioned as a separate sovereign-platform product. |
| Compliance product | Export evidence packs from the record. |
| General handoff between vendors | Keep takeover and fencing as mechanisms inside the record; do not lead with them. |

## Changes if accepted

1. **Direction, product statement.** Add the settlement statement above as the
   product centre. The four contract domains, provisioning ledger, swap test
   and governing principles are unchanged.
2. **Direction, governing principle.** Add "no agent vendor writes acceptance;
   the customer holds a complete, verifiable record" beside coordination
   without custody.
3. **Delivery plan B.** Make the reference workflow an operations action
   through the resource-owner gateway. The hosted-to-native code handoff
   remains a supported scenario, as the 29 October amendment already states.
4. **Delivery plan C2.** Defer semantic retrieval, rich extraction, review
   reminders, item-specific permissions and live collaborative editing until
   the record and gate pass B. These are organizational-memory features, which
   this proposal treats as provisioned. Keep C and approved Git export, which
   preserve the exact sources supplied to work.
5. **Delivery plan F and G.** Hosted-service design follows open decision 1, so
   that operating vuoro.cloud never makes its operator the holder of a
   customer's acceptance record.

## How we would know it is wrong

| Signal | Measure | Consequence |
| --- | --- | --- |
| Capture needs vendor cooperation | Run the gated workflow with an agent whose vendor supplies no Vuoro integration. Every gated effect must still have a proposal, an artifact digest and a receipt. | If not, neutrality is a design property only; stop positioning on it. |
| Comparisons change nothing | Over a stated window, count provisioning, delegation and review-boundary revisions that cite an ExperimentRecord. | If none, reduce layer 3 to a report. |
| The gate is bypassed | Count observed state changes in the gated domain with no matching receipt. The evidence classes already distinguish an observation without a receipt. | If persistent, the gate is not the only path. Fix its latency or scope before claiming the record is complete. |

The third measure comes from the product itself: the record reports its own
bypass rate.

## Pass against product intention records

- **vuoro-cloud `19-PRODUCT-POSITIONING-AND-PROOF.md`.** Its public outcome is
  "keep long-running agent work resumable, and know which result actually
  counts". This proposal leads with the second half: settlement is the
  mechanism there and the product here. Its companion boundary, "coordinate
  the work, keep control of where it runs", is the gate. Consistent; the
  emphasis changes.
- **vuoro-cloud `00-EXECUTIVE-DECISION.md`.** Cloud hosts Sprintctl work
  state. Work state is unaffected; where acceptance records live falls under
  open decision 1.
- **[September target state](https://github.com/bayleafwalker/agentops/blob/main/docs/plans/2026-09-17-target-state.md).**
  Native execution and the coordination/authority split remain, and this
  proposal depends on both.
- **Direction principles.** Coordination without custody is strengthened.
  Execution neutrality becomes the commercial differentiator. The abstraction
  ratchet is layer 3, unchanged. The 29 October amendment's managed hosting is
  the one point of tension, handled by open decision 1.

## Open decisions

1. **Where the acceptance record is held.**
   - (a) Customer-held only. The hosted service coordinates but holds no
     acceptance. Strongest neutrality; harder hosted onboarding.
   - (b) Hosted, with acceptance Decisions signed by a customer-held key and a
     complete replica continuously exported to customer storage. Keeps managed
     hosting; the customer can verify without trusting the operator.
   - (c) Hosted as today. Simplest, but the service operator becomes the record
     holder, which weakens the neutrality claim.

   Recommendation: (b).
2. **Whether operations replaces or joins the code handoff as the reference
   workflow.** Recommendation: an operations action is B's reference workflow;
   the code handoff stays a supported scenario.
3. **First gated operation.**
   - (a) A low-blast-radius reversible action on a non-critical workload, such
     as a rollout restart or a Flux suspend and resume.
   - (b) The held Cloud rollout and tenant migration.
   - (c) Credential rotation.

   Recommendation: (a) to prove the gate, then (b) as the first high-stakes
   case, because its runbook already requires target inspection before retry.
4. **Whether to test the three-way-match framing with buyers before building
   any billing-related record.** Recommendation: yes. No billing work until a
   buyer names the problem.

## Sources

Market scan of 2026-11-06. These are mostly secondary sources; verify specifics
against vendor documentation before relying on them.

- [GitHub Agent HQ announcement coverage (iTWire)](https://itwire.com/business-it-news/open-source/github-announces-agent-hq-the-control-tower-for-every-coding-agent)
- [GitHub adds Claude and Codex to Agent HQ (i-programmer)](https://www.i-programmer.info/news/105-artificial-intelligence/18651-github-adds-claude-and-codex-to-agent-hq.html)
- [Workday Agent System of Record](https://www.workday.com/en-se/artificial-intelligence/agent-system-of-record.html)
- [ServiceNow community thread on AI Control Tower and third-party agents](https://www.servicenow.com/community/now-assist-for-creator-forum/how-does-ai-control-tower-handle-governance-for-third-party-ai/m-p/3535450)
- [AgentCore Policy and Evaluations preview (AWS)](https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-bedrock-agentcore-policy-evaluations-preview)
- [AgentCore quality evaluations and policy controls (AWS News Blog)](https://aws.amazon.com/blogs/aws/amazon-bedrock-agentcore-adds-quality-evaluations-and-policy-controls-for-deploying-trusted-ai-agents/)
- [Agent 365 and Entra Agent ID governance (iC Consult)](https://ic-consult.com/en/resources/blogs/ai-agent-governance-with-agent-365-and-entra-agent-id-discover-govern-and-secure-the-agents-in-your-tenant/)
- [The agent registry (Semperis)](https://www.semperis.com/blog/the-agent-registry/)
- [agent-audit-trail on PyPI](https://pypi.org/project/agent-audit-trail/)
- [Practitioner note on vendor-held audit trails (DEV Community)](https://dev.to/igorganapolsky/your-compliance-team-will-ask-for-an-ai-agent-audit-trail-before-august-2-heres-the-part-most-h2n)
- [Avatier Ledger: outcome-linked audit trail](https://booth.avatier.com/en/blog/ai-agent-audit-trail-avatier-ledger)

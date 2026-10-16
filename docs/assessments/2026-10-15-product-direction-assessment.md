# Assessment of the 2026-10-15 product direction

Assessed 2026-10-15 against Vuoro `9e48d2d` and AgentOps `f0571b69`.

**The direction is suitable to adopt with the scoped corrections recorded below.**
The provisioning ledger makes the product boundary actionable: buy or provision
capabilities already supplied, and retain four contract domains for work,
evidence, authority and learning. The bounded hosted-to-native case supplies a
coherent first use case. The swap test makes supplier independence an empirical,
row-specific claim.

The owner supplied the direction and requested assessment and landing. The
[landed direction](../direction/2026-10-15-product-direction.md) retains the
author's structure, provisioning choices, first use case, abstraction ratchet
and open decisions. The source attachment's SHA-256 is recorded in the local
review material; it is not a claim that the original wording passed unchanged.

## Findings resolved before adoption

| Finding | Resolution in the direction |
| --- | --- |
| Provision, build and defer could be read as statements of current implementation. | Identify these as owner sourcing intentions and candidate defaults; distinguish them from deployment and qualification. |
| The work and evidence rows named retiring ActionQ and auditctl as permanent build homes. | Use Sprintctl as the reference provider, harvest ActionQ invariants without reviving execution, and preserve the accepted evidence home and auditctl's historical-import requirements. |
| ExperimentRecord, RecipeRevision and AgentProfileRevision conflict with September's exclusions. | Explicitly reopen learning records and observed revision references. Keep retired instruction/profile compilers and execution-recipe machinery out of scope. Record the scoped successor in the AgentOps target state. |
| Provider budgets were described uniformly as hard caps. | Record each selected service's configured enforcement, scope and delay; distinguish alerts from enforced limits and bound executor enforcement to mediated operations. |
| An accepted result could be read as a higher evidence grade than an observed effect. | Treat report, receipt, acknowledgment, observation and acceptance as different evidence. A Decision does not establish a missing effect receipt or observation. |
| The text reported a current 1/3 reconstruction baseline. | Use the dated MI-1 strata: Track B runs 2/3, effects 1/1, historical ordinary runs 0/3, historical ordinary effects n=0. Require a fresh population and time window for a current baseline. |
| The named reference stack could be mistaken for the stack already proved by MI-1. | Describe the first use case as an extension of MI-1. Qualification of the named whole composition and an actual supplier swap remain work to perform. |
| A swap could appear to prohibit even its adapter implementation from changing. | Freeze build-contract semantics while allowing recorded adapter, provider-binding and configuration changes. Preserve the existing correctness/authority conformance checks. |
| No public-to-protected service path could appear to prohibit protected-side retrieval. | Prohibit public-initiated access and allow protected-initiated pull, with local authority evaluated independently. |
| Protocol revisions and Tasks support could be assumed universal. | Carry explicit work handles, negotiate supported protocol revisions and use Tasks only where both endpoints support it. Keep a durable owner reference for other clients. |
| Review timeout could fabricate a human rejection; the ratchet could imply automatic grant expansion. | Record expiration as absence of authorization, and require boundary changes to be authorized under the existing policy. |

## Evidence and scope

The [MI-1 evidence record](https://github.com/bayleafwalker/agentops/blob/f0571b69e2f75101c07be2b813666c4939f73ce5/docs/evidence/2026-10-09-mi1-bindery/README.md)
supports the historical handoff, exact-artifact publication, recovery and
reconstruction case. Its completeness table explicitly separates the bounded
proof from the precommissioning historical sample.

The [disposition register](../direction/disposition-register.yaml) and
[September target state](https://github.com/bayleafwalker/agentops/blob/f0571b69e2f75101c07be2b813666c4939f73ce5/docs/plans/2026-09-17-target-state.md)
establish the earlier owners, exclusions and historical migration requirements.
Their artifact observations remain unchanged. This adoption changes intended
product sourcing and learning scope, not the status of a running component.

The [MCP transport revision](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)
supports the per-request metadata and backward-compatibility statements.
[Google Cloud's budget documentation](https://docs.cloud.google.com/billing/docs/how-to/budgets)
distinguishes alert-only and supported spend-cap configurations;
[AWS's documentation](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)
describes billing and notification delays. These support characterizing the
chosen controls rather than asserting one universal hard-cap capability.

The policy engine, reference-store swap, evidence schema alignment, first
protocol entry, enforcement coverage, experiment thresholds and tenant-side
authority model remain the owner's open decisions. This assessment does not
select them or authorize runtime, database, grant or deployment changes.

---
doc_id: vuoro-estate-enablement-2026-10-14
title: Estate enablement - from merged capability to usable, recoverable operation
purpose: proposal
lifecycle: proposed
effective: 2026-10-14
subjects: [estate-enablement]
---

# Estate enablement: from merged capability to usable, recoverable operation

Status: operator priority, 2026-10-14. This rolls in an external assessment of
the estate (the repositories, CI runners, agent execution environments,
shared services and Cloud infrastructure).

> The strongest enabler would be a repeatable path from "merged capability"
> to "usable, recoverable operation" across the estate.

Operator direction: these four priorities move to the front of the queue.
Nothing is deferred. Work already in flight continues in parallel, as does
work the assessment suggested deferring: the agent directory and inbox, the
knowledge workspace, wider connectors and orchestration.

## Milestone

A replacement agent can discover the actual state, complete one authorized
operation, and recover it using supported interfaces, without the operator
supplying missing context or commands.

## Priorities and where they land

| # | Capability | Blocker it removes | Owner repo | First implementation (2026-10-14) |
|---|---|---|---|---|
| 1 | Qualification against the exact release composition | Components pass independently but disagree when installed together | vuoro-cloud (plus vuoro and appservice receipts) | Several Codex candidates become committed code: the same-image controller role probe over the full ten-function set, and the combined collector with a strict output schema. Also: a controller pre-claim role and catalog gate; a `vuoro-qualification-receipt/v1` that binds the service, controller, executor and tooling digests, the schema head, the permission policy and the compatibility pin, and goes stale when any of them changes; and a disposable-tenant qualification runner. |
| 2 | A supported execution environment with bounded authority | Agents can prepare work but cannot finish or recover it without bespoke operator intervention | agentops | An environment record `authority` block with three tiers: standing read, scoped time-limited execution, and explicit approval for consequential transitions. Pinned tooling and trust roots for devbox as the one maintained runner. An effect-boundary authority check, and a host adapter that refuses launches outside that authority. |
| 3 | One machine-readable view of deployed capabilities | Agents and operators act on stale status, source versions or assumed availability | agentops | `agentops estate status`, with schema `estate-status-v1`. For each environment it reports what is desired, running, usable and held, and the recovery class. Every fact carries its source and observation time, and `unknown` is a valid answer. Human status documents are generated from it. |
| 4 | Self-service recovery and diagnostic operations | Every failure becomes a fresh investigation or a request for broad access | vuoro | `vuoro-diagnostic-result/v1` operations: `uncertain-effects`, `provider-outcome-check`, `role-probe`, `catalog-probe`, `migration-preflight`, `recovery-export` and `isolated-restore`. Each is stamped with the installed composition. |

## Recovery fork

"Rollback available" must name which recovery applies. The estate report and
`migration-preflight` compute it from the schema ranges of the current and
prior images; it is never asserted.

- `cancel`: no change has been applied yet.
- `image-revert`: the deployment was compatible and the prior image is still
  schema-compatible.
- `forward-fix-or-restore`: an incompatible migration has committed. Example:
  vuoro-shared is forward-only after the schema 25 cutover on 2026-10-14.

## Constraints that still hold

vuoro-cloud's gates are unchanged. Cluster mutation, Terraform apply, Flux
reconcile, promotion and billable infrastructure each need explicit
authorization, and promotion signing needs the owner's hardware key. The
priority 1 code lands through review, and the live qualification against the
held Cloud cluster runs as an authorized step with the exact commands named in
its PR.

The sequence follows the assessment: qualify the held Cloud path first, use
that environment to activate one protected operation, then expose the
resulting state and recovery operations estate-wide.

## Status

| Priority | State |
|---|---|
| 1 | implementation in progress (vuoro-cloud PR) |
| 2 | implementation in progress (agentops PRs) |
| 3 | implementation in progress (agentops PR) |
| 4 | implementation in progress (vuoro PR) |

# Repository integration topology and verification profiles

Status: ratified direction. Implementation remains owner-staged and
evidence-gated.

[Portable governed execution](portable-execution.md) defines what a candidate
is. This document defines how candidates reach a protected default branch and
which verification runs at each boundary. Branch names are a publication
projection of a frozen plan; they are never an input from which topology is
inferred.

## Ownership

```text
Sprintctl
  owns development topology: what precedes what, and when work is ready

Dispatch-plan compiler
  freezes topology, integration lanes, and verification profile names

Actionq
  owns execution, review, and integration actions as lifecycle state

Runner
  verifies inside the envelope and holds no push authority

Trusted publisher
  owns Git push and pull-request creation

Auditctl
  records independent findings about immutable candidates
```

The trusted publisher is not the runner, not the worker, and not Vuoro. It is
the only identity in the system holding write credentials to a governed
repository.

Vuoro composes released capabilities and transports observation. It does not
hold push authority, name integration branches, select verification profiles,
or decide promotion.

## Branch topology

A persistent global `dev` branch is rejected as the substrate default. It
would become a second mutable authoritative state that exists independently of
Sprintctl plans and Actionq actions: unrelated plans interfere, failures lose
attribution, reverting one candidate disturbs later work, and the branch drifts
from the frozen plan it was supposed to realize.

The ratified topology is artifact-first, with Git used only as a publication
surface:

```text
protected main
    ▲
    │ protected pull request, repository-full profile
    │
integration/<plan-id>/<integration-id>   (short-lived, publisher-created)
    ▲
    │ trusted publication
    │
immutable integration candidate
    ▲
    │ fresh integration action, integration-broad profile
    │
immutable worker candidates
    ▲
    │ candidate-review profile
    │
disposable workers, worker-focused profile
```

Rules:

- `main` is protected in every ecosystem repository.
- No branch is authoritative execution state. The authoritative results are
  the candidate bundles, receipts, and Actionq terminal outcomes.
- Integration is a fresh action over immutable bundles in a clean environment.
  Merging into a shared mutable checkout is forbidden.
- A remote branch is materialized only when a protected pull request needs
  one, named `integration/<plan-id>/<integration-id>`.
- Deleting a published branch must not destroy evidence, and republication
  from the same integration candidate must be deterministic.
- A persistent `dev` branch is a repository-specific exception justified only
  by continuous manually authored integration work. It is never the default
  and never the integration authority for dispatched work.

## Verification profiles

Verification is stratified so the heaviest suite runs once per promotion
rather than before and after every worker attempt.

| Profile | Runs where | Selected by | Purpose |
|---|---|---|---|
| `worker-focused` | inside the worker attempt | frozen in the `ExecutionEnvelope` | narrow, adversarial falsification of the changed component; fast enough to iterate |
| `candidate-review` | after candidate publication | compiled plan | independent re-verification of one candidate; does not trust the worker's completion claim |
| `integration-broad` | in a fresh integration action | integration action | detects cross-candidate conflict in the combined tree |
| `repository-full` | at the protected pull request | promotion policy | comprehensive release-level gate |

Profile names are declared by the compiled plan and resolved to registered
commands by repository policy. A candidate never selects its own profile: an
untrusted branch author must not be able to label a change "focused only" and
bypass integration or full checks.

`worker-focused` gates must be able to reject a wrong candidate. Assertions
that only prove a call returned, that a list is non-empty, or that a filter
matched something are not falsifying gates.

### Vuoro profile resolution

Vuoro's registered commands resolve as follows. Each profile is a superset of
the one above it.

- `worker-focused` — package tests for the changed package:
  `uv run --package vuoro-client --extra test pytest packages/vuoro-client/tests`
  or the `vuoro-service` equivalent.
- `candidate-review` — both package test suites, both wheel builds, and
  `scripts/validate_verification_artifacts.py`.
- `integration-broad` — the above plus `uv run pytest` on the combined tree.
- `repository-full` — the above plus pinned adapter fetch and the released
  wheel smoke that proves the installed artifact registers and fails closed on
  incompatibility.

Changes to compatibility, migration, identity, authority, invocation, or
adapter composition paths escalate to `repository-full` regardless of the
profile the plan named, because `vuoro.dispatch.json` marks them
`required_on_change`.

## Stale base and rollback

Every candidate and integration candidate is bound to an exact source commit.
Promotion must record that base.

- If `main` has advanced past the recorded base, the integration candidate is
  stale-based. It must be re-integrated by a fresh action against the new base
  and re-verified, or explicitly rejected.
- Silent merging or automatic rebasing onto a newer `main` is forbidden. It
  invalidates the frozen-source premise and every receipt bound to the old
  base tree.
- Rollback is deletion of the published branch and disposition of the
  integration candidate. It never mutates or rewrites candidate artifacts.

## Evidence identity

- A verification result may be reused only when keyed to the exact tree
  identity it ran against, never to a branch name or pull-request number.
- Infrastructure failure and candidate failure are distinct terminal
  classifications. Only infrastructure failure is retryable without producing
  a new candidate.
- Git-host checks are verifiers that produce receipts. Branch protection is a
  promotion gate, not the evidence of record; a green check with an unsettled
  Actionq action is not an accepted candidate.
- A published pull request must be traceable to its plan, envelopes, candidate
  results, reviews, and integration receipts.

## Required failure histories

In addition to the histories required by portable execution:

- Branch publication succeeds but pull-request creation fails.
- A published branch is deleted while the candidate artifact remains valid.
- `main` advances between integration and promotion.
- A Git-host check passes while Actionq settlement or Auditctl publication is
  delayed.
- A full gate is retried after an infrastructure-only failure.
- An integration branch name collides with an existing branch.
- Independently passing candidates fail only in combination.

## Staged proof

1. **Stratify one repository.** Express the four profiles as named, separately
   invocable verification in a single canary repository. Vuoro is the canary:
   its suite is expensive enough to matter and its blast radius is small.
2. **Declare the publisher boundary.** Prove that no runner, worker, or Vuoro
   identity holds push authority to a governed repository, and that the
   publisher identity is separately held and auditable.
3. **Run one wave.** Take a two- or three-candidate wave from Sprintctl
   readiness through independent candidates, integration, and a protected pull
   request, with no persistent integration branch.
4. **Only then generalize.** Define a reusable cross-repository bootstrap for
   branch protection, required checks, profiles, and cleanup policy.

Mass rollout before the canary is out of scope.

## Not owned here

Dispatch-plan schema additions belong to the compiler's owner repository.
Publisher implementation and branch protection automation belong to the
execution and operations owners. This document constrains those owners; it
does not implement them, and Vuoro does not acquire them.

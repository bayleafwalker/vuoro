---
doc_id: vuoro-repository-integration-ci-alignment
status: draft
supersedes: []
---

# Vuoro repository integration and CI alignment

This owner-local plan converts the plan-driven dispatch and repository CI
handover into Vuoro-owned scope. It governs only what Vuoro owns.
`docs/architecture/integration-topology.md` holds the ratified direction;
sprintctl items remain the execution authority.

## Decisions taken

- **Persistent global `dev` branch: rejected.** It would create a second
  mutable authoritative state independent of Sprintctl plans and Actionq
  actions, breaking failure attribution and the frozen-source premise. It
  survives only as a repository-specific exception for continuous manually
  authored integration work, which Vuoro does not have.
- **Worker-pushed branches: rejected.** The ratified runner boundary gives no
  push authority. "Worker branch" may only mean a trusted projection of an
  immutable candidate.
- **Integration is artifact-first.** A remote branch is materialized only when
  a protected pull request needs one, named
  `integration/<plan-id>/<integration-id>`, and its deletion destroys no
  evidence.
- **CI is stratified by named verification profile**, not by branch name. The
  candidate never selects its own profile.
- **Vuoro is the canary repository** for profile stratification: a meaningful
  suite cost, an existing protected promotion path, and low operational
  consequence if the workflow needs revision.

## Current state

- `.github/workflows/ci.yml` is a single flat job running package tests, both
  wheel builds, the full suite, pinned adapter fetch, and released-wheel smoke
  on every pull request and every push to `main`. There is one profile, and it
  is the most expensive one.
- `AGENTS.md` already names the command set that the four profiles partition.
- `vuoro.dispatch.json` already marks the risk surfaces that must escalate to
  the full profile regardless of the profile a plan names.
- Vuoro #2037 composes released portable-execution capabilities. Vuoro #2028
  already owns generic `get`/`changes`/`wait(until="terminal")`; a separate
  `dispatch wait` contract would duplicate it and is not created.

## Owner-local units

### V-I1 — Stratify repository CI into named verification profiles

**Sprintctl:** Vuoro #2050.

Express `worker-focused`, `candidate-review`, `integration-broad`, and
`repository-full` as separately invocable verification in this repository,
each a superset of the one above it. Pull requests targeting `main` continue
to run `repository-full`; the cheaper profiles become addressable by name so a
frozen envelope or an integration action can request one.

Escalation to `repository-full` on `required_on_change` risk surfaces must be
enforced by repository policy, not by the requesting profile name.

### V-I2 — Declare and prove the trusted-publisher boundary

**Sprintctl:** Vuoro #2051.

Record which identity may push to this repository and open pull requests, and
prove that no runner, worker, or Vuoro service identity holds that authority.
Vuoro must not acquire push credentials as a side effect of composing
execution capabilities.

Acceptance includes a negative check: a candidate-composing path presented
with repository write credentials must fail closed rather than use them.

### V-I3 — Canary one wave-integrated dispatch end to end

**Sprintctl:** Vuoro #2052.

Take a two- or three-candidate wave through independent candidates, a fresh
integration action over immutable bundles, a published
`integration/<plan-id>/<integration-id>` branch, and one protected pull
request — with no persistent integration branch. Record stale-base behavior
when `main` advances mid-wave.

Cross-repository gated: requires the compiler and publisher owners to have
landed their side. Blocked by V-I1 and V-I2.

## Out of Vuoro scope

- Dispatch-plan schema additions (`topology`, `integration_lane`,
  `verification_profile`, `publication`) belong to the compiler's owner
  repository. Vuoro consumes compiled plans; it does not compile them.
- Trusted publisher implementation and branch-protection automation belong to
  the execution and operations owners.
- The reusable cross-repository bootstrap skill is parked until the canary
  wave completes. Defining it first would freeze a contract with no evidence.

## Dispatch posture

V-I1 is mechanical once the profile partition is frozen and is hybrid-eligible
under `mechanical_bulk`. V-I2 and V-I3 are coordinator-only: authority
boundaries and promotion evidence are `required_on_change` risk surfaces.

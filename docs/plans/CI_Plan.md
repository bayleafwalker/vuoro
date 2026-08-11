CI_Plan
# Handover: Plan-Driven Dispatch, Integration Topology, and Repository CI

**Status:** Working session handover
**Date:** 2026-07-29
**Scope:** Vuoro ecosystem repositories, plan-driven execution, candidate integration, branch protection, and CI stratification

## 1. Purpose

The next design session should determine how repository branch topology and CI execution should relate to:

* Sprintctl development plans;
* dispatch-plan compilation;
* Actionq execution groups;
* immutable candidate Git artifacts;
* independent review and integration actions;
* multi-repository rollout across the Vuoro ecosystem.

The immediate question is whether repositories should adopt a persistent unprotected or lightly protected `dev` branch beneath a fully protected `main`, and whether that branch would reduce CI cost and improve concurrent dispatch handling without introducing pointless ceremony.

This must be evaluated against the already ratified execution architecture rather than treated as an isolated Git workflow decision.

---

## 2. Current repository idea

The initial proposed repository model is approximately:

```text
worker branches
      │
      ▼
dev / integration branch
      │
      ▼
protected main
```

Possible behavior:

1. `main` is fully protected.
2. `dev` is available as the integration target for active implementation work.
3. Each worker or implementation dispatch creates a supplemental branch.
4. Worker branches run focused or relatively cheap tests.
5. Completed worker branches are integrated into `dev`.
6. `dev` runs broader integration CI.
7. Once a coherent plan or wave is complete, a pull request is opened from `dev` to `main`.
8. `main` receives the final full verification and protected merge.

The main motivations are:

* supporting concurrent implementation work;
* giving dispatched work a predictable integration destination;
* avoiding the full repository test suite on every small worker attempt;
* separating focused, integration, and release-level verification;
* standardizing the same arrangement across multiple Vuoro repositories.

The concern is that a persistent `dev` branch may add ceremony without meaningful value, particularly where migrations or small changes can currently be handled directly against `main`.

---

## 3. Ratified architectural context

Two design documents define the relevant constraints:

* `docs/architecture/portable-execution.md`
* `docs/architecture/observable-resources.md`

### 3.1 Ownership boundaries

The ratified execution ownership model is:

```text
Sprintctl
  owns work definition, readiness, dependencies, and acceptance criteria

Dispatch-plan compiler
  snapshots selected work into immutable execution envelopes

Actionq
  owns actions, claims, leases, retries, cancellation, and terminal outcomes

Runner
  materializes repositories, invokes harnesses, verifies, and publishes results

Auditctl
  records independent findings and evidence about immutable candidates
```

Vuoro may expose and coordinate released capabilities, but it does not become:

* the planning authority;
* the execution lifecycle authority;
* the interpreter of Actionq state;
* a generic durable-jobs subsystem.

An Actionq execution group realizes frozen work. It is not itself the development plan.

A Sprintctl plan may express:

```text
A and B precede C
```

An Actionq group instead realizes exact frozen envelopes such as:

```text
A@rev7 and B@rev4 against source commit abc123
```

### 3.2 Execution envelopes are the planning-to-execution ABI

The dispatch-plan compiler produces immutable `ExecutionEnvelope` instances containing such information as:

* source repository and exact source commit;
* frozen work revision;
* allowed paths;
* required capabilities;
* harness choice;
* registered commands;
* resource limits;
* network policy;
* acceptance gates.

The envelope contains no worker claim token or provider credentials.

This is the stable boundary that must work across:

* the current devbox runner;
* disposable containers;
* Kubernetes workers;
* disposable virtual machines;
* possible later runner implementations.

### 3.3 Workers do not publish branches directly

The runner:

* checks out an exact source commit;
* performs work;
* creates a local candidate commit;
* runs registered verification;
* publishes a content-addressed Git bundle and receipts;
* does not receive push authority.

The canonical result is an immutable `CandidateResult`, not:

* a surviving worktree;
* a mutable worker branch;
* a raw patch;
* a worker directly merging into `dev`.

A separate trusted publisher may materialize an accepted candidate bundle as a Git branch.

This distinction is important for the branch design. “Worker branch” should usually mean a trusted projection of an immutable candidate, not a branch directly controlled and pushed by the worker.

### 3.4 Integration topology is explicit in the plan

Every dispatch plan classifies candidate relationships as:

1. **Independent**
   Candidates may be reviewed and integrated separately.

2. **Stacked**
   A later envelope runs against an earlier candidate.

3. **Wave-integrated**
   Candidates run independently, an integration action combines them, and the next wave uses the integrated result.

Example:

```yaml
entries:
  - id: implementation-a
    base: git:abc123
    integration_lane: core

  - id: implementation-b
    base: git:abc123
    integration_lane: docs

  - id: integrate-wave-1
    kind: integration
    requires:
      - implementation-a
      - implementation-b

  - id: implementation-c
    base_from: integrate-wave-1
```

The first grouping implementation is a projection over ordinary Actionq actions with:

* bounded `max_parallel`;
* explicit `failure_policy`;
* cancellation of new claims;
* no claim of transactional batch semantics.

### 3.5 Observable resources standardize observation, not lifecycle

Actionq and other domains expose opaque resource references such as:

```json
{
  "schema_version": "resource-reference/v1",
  "owner": "execution",
  "resource_kind": "session",
  "reference": "actionq:session:1234",
  "revision": "actionq:event:5678"
}
```

Vuoro can provide generic:

* `get`;
* `changes`;
* bounded `wait(until="terminal")`;

but lifecycle truth remains owned by the domain.

This supports dispatch ergonomics without turning Vuoro into a generic job-state authority.

---

## 4. Important correction to the original branch concept

The initial discussion described workers as creating supplemental branches and merging toward `dev`.

That does not fit the ratified portable-execution boundary as written.

The architecture instead implies:

```text
worker execution
    │
    ▼
immutable candidate bundle
    │
    ├── independent review
    │
    ▼
trusted integration action
    │
    ▼
integration candidate bundle
    │
    ▼
trusted publisher / protected PR
```

Branches are therefore presentation and Git-host integration mechanisms. They are not the authoritative execution result.

A worker should not:

* hold repository push credentials;
* directly merge into `dev`;
* rely on a surviving remote branch as its result contract;
* mutate a shared integration checkout.

A trusted publisher or integration service can materialize:

* candidate review branches;
* a wave integration branch;
* a final pull-request branch.

This preserves immutable evidence and deterministic recovery.

---

## 5. CI stratification under consideration

The strongest part of the original `dev` idea is not the branch itself. It is the verification hierarchy.

The proposed verification levels are:

### Level 1: Worker-focused falsification

Run within each worker attempt against the frozen envelope.

Characteristics:

* narrow;
* executable by the worker;
* quick enough for iteration;
* adversarial rather than merely confirmatory;
* limited to registered commands;
* intended to reject bad candidates early.

Examples:

* contract tests for the changed component;
* mutation-like expectations;
* tests proving every filter excludes at least one record;
* wrong-order input cases;
* real calls through the layer claimed as covered;
* prohibitions on empty or list-only assertions.

### Level 2: Candidate or owner-focused verification

Run after candidate publication, potentially by an owner or review action.

Characteristics:

* broader than the worker gate;
* validates the candidate independently;
* may inspect changed-path manifests and receipts;
* should not trust the worker’s completion declaration;
* can publish Auditctl findings.

### Level 3: Wave integration verification

Run after an integration action combines independently passing candidates.

Characteristics:

* starts in a fresh environment;
* consumes immutable candidate bundles;
* detects cross-candidate conflicts;
* tests the combined tree;
* publishes an integration candidate and receipts;
* makes integration failure explicit.

### Level 4: Full repository or release verification

Run once per accepted wave, release candidate, or protected `main` pull request.

Characteristics:

* expensive;
* comprehensive;
* appropriate for the full seven-minute or otherwise heavy suite;
* not repeated before and after every small worker attempt.

This avoids running the heaviest test suite for every tiny dispatched task while retaining a trustworthy promotion path.

---

## 6. Persistent `dev` branch: possible interpretations

A `dev` branch could serve several different roles. They should not be conflated.

### Option A: Persistent mutable integration branch

Every accepted candidate is merged into a long-lived `dev`.

Advantages:

* familiar Git workflow;
* easy visual representation in GitHub;
* provides a stable target for manual work;
* allows accumulated integration testing.

Risks:

* state drifts away from the frozen plan;
* unrelated plans may interfere;
* failures become difficult to attribute;
* reverting one candidate may disturb later work;
* `dev` becomes another authoritative mutable state;
* plans, artifacts, and branch contents can disagree;
* stale or partially integrated work accumulates.

This is the highest-ceremony and most failure-prone interpretation.

### Option B: Short-lived integration branch per plan or wave

A trusted integration action materializes a branch such as:

```text
integration/<plan-id>/<wave-id>
```

The branch is created from:

* the exact source commit;
* explicitly selected immutable candidate bundles;
* a recorded integration action.

Advantages:

* topology matches the frozen plan;
* failures remain attributable;
* concurrent plans do not share mutable state;
* branch lifetime is bounded;
* the branch can directly back a protected pull request;
* branch deletion does not delete the canonical candidate artifacts.

Risks:

* more branch creation and cleanup;
* Git hosting UI can become noisy;
* requires reliable publisher automation.

This fits the ratified architecture better than a global `dev`.

### Option C: No integration branch until final publication

Integration actions operate entirely from bundles and publish only:

* integration candidate artifacts;
* receipts;
* Auditctl findings.

A branch is materialized only when opening a pull request to `main`.

Advantages:

* Git remains only a publication surface;
* minimal mutable repository state;
* cleanest relationship between plan and execution;
* no long-lived integration branch to manage.

Risks:

* less immediate visibility in the Git hosting UI;
* requires good artifact inspection and review tooling;
* manual maintainers may find the workflow less familiar.

This is architecturally cleanest but depends most heavily on the surrounding tooling.

---

## 7. Current provisional recommendation

The next design session should begin with the following hypothesis:

> Do not standardize a persistent global `dev` branch as the default integration authority. Standardize plan- or wave-scoped integration candidates, with optional short-lived branches materialized by a trusted publisher.

Recommended topology:

```text
protected main
    ▲
    │ protected pull request
    │
integration/<plan>/<wave-or-final>
    ▲
    │ trusted publication
    │
immutable integration candidate
    ▲
    │ fresh integration action
    │
immutable worker candidates
    ▲
    │
disposable workers
```

This retains the useful parts of the proposed approach:

* focused worker tests;
* broader integration tests;
* one expensive full-suite run per meaningful promotion;
* explicit concurrent integration;
* predictable GitHub pull-request flow;
* consistent automation across repositories.

It avoids turning `dev` into a second semi-authoritative repository state that exists independently of Sprintctl plans and Actionq actions.

A persistent `dev` branch may still be justified in a repository with continuous manually authored integration work, but it should be a repository-specific exception, not the substrate-wide default.

---

## 8. Relationship to dispatch

The dispatch system should operate on plans and immutable artifacts rather than branch names.

### Dispatch-plan compiler responsibilities

A compiled dispatch plan may need to emit:

* exact source commit;
* entries and frozen revisions;
* independent, stacked, or wave-integrated topology;
* `max_parallel`;
* failure policy;
* integration lanes;
* verification profile per entry;
* required review actions;
* integration actions;
* promotion target;
* publication policy.

Possible conceptual structure:

```yaml
plan:
  id: portable-execution-wave-1
  source:
    repository: actionq
    commit: abc123

  execution:
    max_parallel: 3
    failure_policy: stop-new-claims

  entries:
    - id: envelope-contracts
      topology: independent
      verification_profile: worker-fast

    - id: artifact-publisher
      topology: independent
      verification_profile: worker-fast

    - id: runner-boundary
      topology: independent
      verification_profile: worker-focused

  integration:
    - id: integrate-wave-1
      requires:
        - envelope-contracts
        - artifact-publisher
        - runner-boundary
      verification_profile: integration-broad
      publication:
        branch: integration/{plan_id}/{integration_id}

  promotion:
    target: main
    verification_profile: repository-full
    requires_independent_approval: true
```

Branch names should be a publication projection of this structure, not inputs used to infer the topology after the fact.

### Candidate publication flow

A likely lifecycle is:

```text
Sprintctl work becomes ready
        │
dispatch-plan compiler freezes plan
        │
Actionq creates execution actions/group
        │
runner publishes immutable candidates
        │
candidate publication enqueues independent review
        │
approval enqueues integration action
        │
integration publishes combined candidate
        │
full gate runs against combined candidate
        │
trusted publisher creates branch and PR
        │
operator disposes or merges
```

This removes manual sequences such as:

```text
wait → poll → fetch → inspect → dispatch review → wait → fetch → integrate
```

Observable resources should allow the coordinator to use a bounded wait on the durable Actionq-owned resource instead.

---

## 9. Cross-repository skills and tooling ideas

The repository rollout should be supported by reusable cross-repository skills rather than hand-built CI and dispatch behavior in every repository.

Potential skills or command modes:

### `dispatch-plan`

Add a wave-plan mode capable of:

* selecting ready Sprintctl work;
* freezing exact work revisions;
* classifying topology;
* assigning integration lanes;
* choosing verification profiles;
* producing an immutable plan;
* showing expected parallelism and promotion boundaries.

Possible interface:

```bash
dispatch-plan compile \
  --plan portable-execution \
  --wave 1 \
  --max-parallel 3 \
  --failure-policy stop-new-claims
```

### `dispatch wave`

Dispatch all eligible entries in a compiled wave:

```bash
dispatch wave enqueue <compiled-plan-ref>
```

Expected result:

```json
{
  "schema_version": "resource-reference/v1",
  "owner": "execution",
  "resource_kind": "group",
  "reference": "actionq:group:1234",
  "revision": "actionq:event:5678"
}
```

### `dispatch wait`

Observe terminality through the generic observable-resource contract:

```bash
dispatch wait actionq:group:1234 --until terminal
```

### `dispatch integrate`

Create an integration action from accepted immutable candidates:

```bash
dispatch integrate \
  --plan <plan-ref> \
  --integration integrate-wave-1
```

### `dispatch publish`

Materialize an approved integration candidate as a branch and optionally open a pull request:

```bash
dispatch publish \
  --candidate artifact:sha256:... \
  --branch integration/portable-execution/wave-1 \
  --target main
```

The trusted publisher, not the runner, owns Git push authority.

### Repository bootstrap skill

A common setup skill could install or update:

* branch protection;
* reusable CI workflows;
* focused/integration/full verification profiles;
* branch naming conventions;
* trusted publisher identity;
* required check mappings;
* cleanup policy;
* repository capability metadata.

The rollout should start with one canary repository before applying it across the ecosystem.

---

## 10. CI should follow verification profiles, not only branch names

A key design question is whether CI selection should be controlled by:

* branch name;
* pull-request target;
* changed paths;
* dispatch envelope metadata;
* candidate verification profile;
* explicit integration or promotion action.

Branch-only rules are simple but brittle.

The preferred direction is likely:

```text
worker execution:
  commands frozen in ExecutionEnvelope

candidate review:
  profile selected by compiled plan

integration:
  profile selected by integration action

GitHub PR:
  protected checks selected by promotion policy
```

Git-host CI may still use branch and PR events as triggers, but the intended verification level should be explicit in trusted metadata.

The system should prevent an untrusted branch author from labeling a change as “focused tests only” and bypassing required integration or full checks.

---

## 11. Multi-repository rollout

The eventual pattern should be consistent across Vuoro ecosystem repositories, but not blindly identical.

Shared invariants should include:

* `main` protected;
* no worker push credentials;
* candidate commits published as immutable bundles;
* trusted branch publication;
* reusable verification profiles;
* explicit integration topology;
* independent review before promotion where required;
* full checks at a defined promotion boundary;
* recoverable cleanup;
* traceability from pull request to plan, envelopes, candidate results, reviews, and integration receipts.

Repository-specific configuration may include:

* command sets;
* duration and cost of test profiles;
* required capabilities;
* path ownership;
* whether a published integration branch is useful;
* migration-specific gates;
* release requirements.

A canary rollout should establish the smallest viable common contract before mass deployment.

Suggested canary criteria:

* meaningful test suite cost;
* at least two independently dispatchable changes;
* integration conflict risk;
* existing protected `main`;
* low operational consequence if the workflow needs revision.

---

## 12. Failure and recovery questions to preserve

The branch and CI design must not weaken the required execution histories.

It must remain correct when:

* a worker dies after bundle upload but before Actionq settlement;
* a lease expires and the original worker returns late;
* the same frozen plan is realized twice;
* the source commit becomes unavailable or stale;
* the bundle uploads but a receipt does not;
* cancellation occurs while the harness is active;
* independently passing candidates fail in combination;
* a generated implementation attempts to access unrelated credentials or repositories;
* branch publication succeeds but pull-request creation fails;
* a branch is deleted while the canonical artifact remains valid;
* GitHub CI succeeds but Actionq settlement or Auditctl publication is delayed;
* a full gate is retried after an infrastructure-only failure;
* the target `main` advances before promotion.

The design should explicitly define rebasing or stale-base behavior. Silent merging against a newer `main` would violate the frozen-source premise.

---

## 13. Open design decisions

The next session should resolve or narrow the following.

### Branch topology

* Is a persistent `dev` branch needed anywhere?
* Should integration branches be scoped by plan, wave, or final candidate?
* At what point is an immutable candidate materialized as a remote branch?
* Who owns branch cleanup?
* What happens when `main` advances before the plan is promoted?

### Verification

* What are the standard verification profiles?
* Which commands run inside the worker?
* Which commands run independently after publication?
* Which suite runs at wave integration?
* Which suite is mandatory for the protected `main` PR?
* Can full-suite results be reused safely, and under what exact tree identity?
* How are infrastructure failures distinguished from candidate failures?

### Review and disposition

* Does every candidate require independent review?
* Can low-risk candidates be integrated before review?
* Does Auditctl publish findings per candidate, per integration result, or both?
* Which approval facts are machine-consumable?
* Which decisions remain operator-only?

### Dispatch-plan schema

* How are independent, stacked, and wave-integrated relationships represented?
* Does the plan name verification profiles or embed commands?
* How are repository-specific policies resolved?
* Is branch publication part of the compiled plan or a later disposition?
* How are multi-repository plans represented?

### Git hosting integration

* Which checks are required by branch protection?
* Is GitHub CI authoritative evidence, or merely another verifier producing receipts?
* How is a GitHub check linked to an Actionq action and immutable candidate tree?
* Should pull requests be opened only after all internal gates pass?

---

## 14. Suggested next-session sequence

1. Treat the ratified portable-execution and observable-resource documents as constraints.
2. Model one concrete two- or three-candidate wave in a real repository.
3. Trace it from Sprintctl readiness through final pull request.
4. Identify every mutable object and authoritative state transition.
5. Compare:

   * persistent `dev`;
   * wave-scoped integration branch;
   * artifact-only integration with branch-on-promotion.
6. Define focused, integration, and full verification profiles.
7. Define stale-base and failed-integration recovery.
8. Decide the minimum dispatch-plan schema additions.
9. Specify the trusted publisher boundary.
10. Produce a canary rollout plan for one repository.
11. Only after the canary, define the reusable cross-repository bootstrap skill.

---

## 15. Working conclusion

The original intuition is useful: full CI should not run before and after every small worker attempt, and concurrent implementation needs a deliberate integration boundary.

However, the likely durable solution is not simply:

```text
worker branch → persistent dev → main
```

The architecture points toward:

```text
frozen plan
  → immutable worker candidates
  → independent review
  → explicit fresh integration action
  → immutable integration candidate
  → optional short-lived published branch
  → protected main pull request
```

The `dev` concept is therefore best understood as an integration stage, not necessarily a permanent branch.

The next design task is to decide how much of that stage should be represented in Git and how much should remain within the Actionq, artifact, and Auditctl contracts.

---

## 16. Prompt for the continuation chat

Use the following as the initial request in the design session:

> Review this handover together with `docs/architecture/portable-execution.md` and `docs/architecture/observable-resources.md`. Design the repository integration and CI model for plan-driven concurrent dispatch across Vuoro ecosystem repositories. Compare a persistent `dev` branch, plan- or wave-scoped integration branches, and artifact-only integration with branch publication at promotion. Preserve immutable candidate bundles, runner non-push authority, Sprintctl ownership of development topology, Actionq lifecycle ownership, and independent Auditctl evidence. Produce a concrete end-to-end lifecycle, verification profiles, stale-base and rollback behavior, dispatch-plan schema changes, trusted publisher boundary, and a canary rollout plan.

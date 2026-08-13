# Portable governed execution

Status: ratified direction. Implementation remains owner-staged and
evidence-gated.

The current devbox dispatcher is an implementation, not the execution
architecture. Governed execution should cross a stable contract that a devbox
daemon, disposable container, Kubernetes worker, or disposable VM can
implement without moving planning or execution authority into Vuoro.

## Ownership

```text
Sprintctl
  owns work definition, readiness, dependencies, and acceptance criteria

Dispatch-plan compiler
  snapshots selected work into immutable execution envelopes

Actionq
  owns actions, claims, leases, retries, cancellation, terminal outcomes,
  and worktree preparation. The Runner is an internal ActionQ package that
  materializes repositories, invokes harnesses, verifies, and publishes
  results; it is not a separate repository member.

outctl
  captures command streams and produces bounded, recoverable projections

Auditctl
  records independent findings and evidence about immutable candidates
```

Vuoro may expose and coordinate released capabilities from these owners. It
does not become an execution authority, duplicate Sprintctl planning
semantics, or interpret Actionq lifecycle state.

An Actionq execution group realizes frozen work; it is not the development
plan. A Sprintctl plan can say that A and B precede C. An Actionq group says
that exact envelopes `A@rev7` and `B@rev4` execute against source commit
`abc123`.

## Portable runner boundary

The Runner is an internal ActionQ package, not a separate repository member.
It owns repository materialization, harness invocation, verification, and
result publication under ActionQ's execution authority. The first milestone
extracts these versioned structures from the existing devbox dispatcher:

- `ExecutionEnvelope`
- `ClaimReceipt`
- `CandidateResult`
- `ExecutionReceipt`
- `VerificationReceipt`
- `ArtifactPublisher`
- `HarnessAdapter`

An execution envelope freezes the source repository and commit, work revision,
allowed paths, required capabilities, harness selection, commands, resource
limits, network policy, and acceptance gates. It contains no claim token or
provider credential.

An envelope is a coherent reasoning unit, not a mandate to split work by file
or test case. Several implementation steps should share one envelope when they
use the same frozen interface, oracle, context set, writable boundary, and
acceptance history. Split when steps need different contracts, independent
disposition, incompatible risk surfaces, or distinct topology.

Harness adapters belong to the runner and expose only prepare, run, cancel,
and collect operations. Actionq knows capability labels such as
`harness:opencode`, `runtime:python-3.12`, and
`isolation:disposable-checkout`; it does not know provider session formats,
prompt configuration directories, or model-routing details.

The optional `outctl` boundary sits inside a runner or harness command adapter.
It drains stdout and stderr, retains policy-bounded host-local raw evidence,
and returns deterministic projections and opaque retrieval references. The
runner still owns subprocess invocation and cancellation; Actionq still owns
action lifecycle; Auditctl still owns findings. Vuoro may expose the released
capability or its opaque references, but neither `vuoro-client` nor
`vuoro-service` becomes a raw-output store. The normative capture contracts
remain in the [`outctl` repository](https://github.com/bayleafwalker/outctl).

## Verification strata and worker command access

Verification is intentionally asymmetric:

1. **Attempt falsifier.** The worker runs the fastest registered contract test
   capable of disproving its candidate through a narrow executor for exact
   immutable command IDs. Free-form shell authority is not implied.
2. **Candidate-focused verification.** A fresh owner-controlled checkout runs
   the focused profile after candidate publication. Worker self-report is not
   acceptance evidence.
3. **Integration/repository-full verification.** A fresh integration action
   runs the broad or full profile once after independent approval or wave
   integration, not before and after every small attempt.

For filter and parity behavior, the frozen acceptance history is adversarial:
every filter excludes at least one record; matching records arrive in the
wrong order; ignoring any filter makes a mutation-style check fail; the claimed
layer is exercised through real calls; and empty or list-only assertions cannot
pass. A worker unable to execute its focused falsifier stops with a structured
blocker rather than compensating with prose confidence.

## Context-churn boundary

An envelope may cap repeated unchanged reads, reasoning steps without a
mutation or gate result, and tokens spent against identical context. These are
stall controls, not a general campaign to minimize cheap cache writes. A
candidate-ready worker emits a structured handoff immediately instead of
continuing open-ended rereading and self-review.

Where the harness cannot interrupt mid-turn from these counters, the runner
records the limits and telemetry, rejects qualification after an exceeded hard
limit, and uses that evidence to qualify a harness-native interrupt path. A
post-hoc counter must not be described as a real-time cap.

Initially, `actionq-runner` should live inside the Actionq repository with
separate server and runner packages, dependency sets, and images. Import
boundaries must prevent the authoritative server from importing runner
packages. A separate repository is justified only by demonstrated independent
release cadence, multiple deployment environments, third-party
implementations, materially different security ownership, or multi-version
compatibility needs.

## Candidate results are immutable Git artifacts

A surviving worktree or raw patch is not a result contract. A successful
runner creates a local candidate commit without receiving push authority and
publishes content-addressed artifacts:

```json
{
  "schema_version": "candidate-result/v1",
  "source_commit": "abc123",
  "candidate_commit": "def456",
  "candidate_tree": "789abc",
  "bundle_ref": "artifact:sha256:...",
  "execution_receipt_ref": "artifact:sha256:...",
  "verification_receipt_ref": "artifact:sha256:...",
  "changed_paths": [
    "src/component.py",
    "tests/test_component.py"
  ]
}
```

The Git bundle is canonical because it preserves ancestry, modes, renames,
binary objects, and commits. A patch is a convenience projection. A separate
trusted publisher may later materialize an accepted bundle as a branch.
Review and integration actions consume immutable artifacts, never a worker's
surviving checkout.

Artifact publication and Actionq settlement are separately retryable. A
content-addressed result uploaded before a worker dies must be recoverable and
settled exactly once under the authoritative claim and lease rules.

## Integration topology is explicit

Every dispatch plan classifies candidate relationships:

1. **Independent.** Candidates can be reviewed and integrated separately.
2. **Stacked.** A later envelope executes against an earlier candidate.
3. **Wave-integrated.** Candidates execute independently, an integration
   action combines them, and the next wave uses the integrated result.

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
    requires: [implementation-a, implementation-b]

  - id: implementation-c
    base_from: integrate-wave-1
```

Transactional batch promises are out of scope. The first grouping feature is
a projection over ordinary Actionq actions with bounded `max_parallel`,
`failure_policy`, and cancellation of new claims. Sprintctl remains the owner
of development-plan dependencies and readiness.

Independent lanes run with bounded `max_parallel` in separate disposable
checkouts. Overlapping mutable worktrees are not a concurrency mechanism. If
overlap is deliberate, each worker still publishes an immutable candidate and
the plan names a fresh merge-resolution action whose conflict result is durable
evidence.

Candidate publication triggers dependent work only through an explicit,
idempotent Actionq transition bound to the immutable publication. The first
transition creates independent review; an exact approval may create the frozen
integration or repository-full action. Coordinators and Vuoro observers never
infer these transitions from a polled status string.

## Completion observation

Dispatch returns an owner-issued observable reference for its action or group.
The coordinator performs one bounded `wait(until="terminal")` instead of
repeatedly checking processes and fetching partial results. Terminal owner
state attaches execution/settlement receipts, candidate publication, focused
verification, independent review, integration results when applicable, and
bounded log/output references.

Terminality without attachments required by the frozen plan is incomplete,
not successful. Observation follows
[Domain-owned observable resources](observable-resources.md); Actionq remains
the lifecycle owner and Vuoro transports its reference, cursor, and state.

## Disposable runner isolation

A second runner must consume the same envelope and produce the same lifecycle
and evidence structure as the devbox implementation. Model output need not be
byte-identical.

A Kubernetes Job is acceptable only as an untrusted-code environment:

- no service-account token or Kubernetes API access;
- no privileged containers, host paths, or container-engine sockets;
- no shared controller, Flux, Vuoro, or infrastructure credentials;
- egress limited to required Git reads, model APIs, and artifact upload;
- exact-commit checkout and a destroyed workspace after publication.

Dedicated nodes plus a sandboxed runtime, or disposable VMs, are preferred
where generated code is meaningfully untrusted. Namespace isolation alone is
not a security boundary.

## Staged proof

1. **Formalize the devbox boundary.** Make the current dispatcher consume and
   produce the portable structures without changing placement.
2. **Publish immutable candidates.** Export Git bundles, receipts, logs, and
   changed-path manifests instead of treating a worktree as the result.
3. **Prove a second disposable runner.** Run the same envelope through both
   implementations and compare lifecycle and evidence contracts.
4. **Add action groups.** Project bounded parallelism, independent-failure
   policy, and stop-new-claims cancellation over ordinary actions.
5. **Add integration and review actions.** Verify combined candidates in a
   fresh environment, publish Auditctl evidence, and leave disposition to the
   operator.

Fleet scheduling, generalized batch orchestration, callbacks, provider
internals inside Actionq, and a separate executioner repository are not part
of this proof.

## Required failure histories

- The worker dies after bundle upload but before Actionq settlement.
- A lease expires, another worker claims, and the first worker returns late.
- The same frozen plan is realized twice.
- The source commit is unavailable or stale before execution.
- The candidate artifact uploads but a receipt does not.
- Cancellation occurs while the harness is running.
- Independently passing candidates fail after integration.
- Generated code attempts to read cluster credentials or unrelated
  repositories.
- A candidate is ready but unchanged reads continue until a churn limit; the
  receipt rejects qualification and preserves an early-handoff path.
- Candidate publication succeeds and review-action creation loses its response;
  idempotent replay returns the same review action.
- Review approval is replayed and creates exactly one integration/full-gate
  action.
- A terminal resource snapshot omits a required candidate or verification
  attachment and is rejected as incomplete.

These histories must preserve claim fencing, idempotent artifact publication,
deterministic recovery, explicit integration failure, and least-privilege
isolation. Passing only the happy path demonstrates remote command execution,
not a portable execution protocol.

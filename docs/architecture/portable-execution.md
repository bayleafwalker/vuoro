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
  owns actions, claims, leases, retries, cancellation, and terminal outcomes

Runner
  materializes repositories, invokes harnesses, verifies, and publishes results

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

The first milestone extracts these versioned structures from the existing
devbox dispatcher:

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

Harness adapters belong to the runner and expose only prepare, run, cancel,
and collect operations. Actionq knows capability labels such as
`harness:opencode`, `runtime:python-3.12`, and
`isolation:disposable-checkout`; it does not know provider session formats,
prompt configuration directories, or model-routing details.

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

These histories must preserve claim fencing, idempotent artifact publication,
deterministic recovery, explicit integration failure, and least-privilege
isolation. Passing only the happy path demonstrates remote command execution,
not a portable execution protocol.

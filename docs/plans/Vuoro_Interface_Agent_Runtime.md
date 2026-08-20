Vuoro_Interface_Agent_Runtime
# Vuoro Operator Interface and Agent Runtime — Design Handoff

> **Future-state alignment, 2026-08-20.** Preserve this handoff as design
> history, but apply [the native-runtime and execution-federation alignment](2026-08-20-execution-federation-alignment.md)
> before using its roadmap. Product-native runtimes are now the execution
> baseline; ActionQ federates their external references and assurance rather
> than owning a daemon, queue, claims, leases, runner, or harness process.
> Sprintctl reservations are advisory, and Outctl is not a required Vuoro
> dependency. The provisional schemas, shared adapter, and Vuoro-native
> runtime below are not authorized implementation targets.

## Status

Assessed 2026-07-29. The operator-interface direction remains useful, but
implementation is gated on the ratified portable-execution contracts and
owner-staged delivery. A Vuoro-native runtime remains a later comparative
experiment, not a committed product component.

This document does not authorize a broad rewrite of the existing Vuoro ecosystem or replacement of Claude Code, Codex, OpenCode, or other established agent harnesses.

### Assessment against the current architecture

This handoff predates the ratified
[portable governed execution](../architecture/portable-execution.md) and
[repository integration topology](../architecture/integration-topology.md)
directions. Those documents govern where this plan differs:

* Sprintctl owns work definition, readiness, dependencies, and acceptance.
* The agentops dispatch-plan compiler freezes work and repository state into
  immutable execution envelopes.
* Actionq owns action, claim, lease, retry, cancellation, and terminal-outcome
  semantics.
* Actionq-owned runner packages materialize workspaces, invoke harness
  adapters, verify, and publish immutable candidate artifacts.
* A separately held trusted publisher owns Git push and pull-request creation.
* Auditctl owns independent findings about immutable candidates.
* Vuoro may expose and compose released capabilities from those owners. It
  does not compile plans, run harnesses, publish candidates, infer lifecycle
  transitions, select verification profiles, or decide promotion.

The `ExecutionEnvelope`, `HarnessAdapter`, candidate bundle, receipt, and
integration contracts described by the ratified architecture supersede the
provisional `execution-request-v1`, runtime adapter, and result contracts
sketched below. New schemas must be added by their named owner rather than
created independently in this repository.

Current implementation should not be inferred from the examples in this
document. Vuoro currently ships a transport-only client and a service
composition shell with versioned handshake, catalog, and invocation
contracts. The `vuoro execute ...` command tree, normalized cross-harness
runtime event stream, TTY workbench, and Vuoro-native runtime are future work.
Direct subsystem CLIs remain the current break-glass and diagnostic surfaces.

### Backlog disposition

The immediate implementation path is already represented by:

* agentops `#2036`: compile Sprintctl work into immutable execution envelopes
  with explicit integration topology;
* Vuoro `#2037`: compose released portable-execution capabilities without
  acquiring execution authority;
* agentops `#2039`: converge cockpit network paths on owner-mediated Vuoro
  operations;
* Vuoro `#2042`: decide and implement the disconnected-recovery prototype
  disposition;
* Vuoro `#2050`–`#2052`: verification-profile, trusted-publisher, and
  wave-integration canary evidence.

The runtime-market assessment and native challenger must remain a separate,
low-priority evidence item. It may start only after two runner implementations
consume the same released envelope and candidate-result contracts. It must not
block the operator interface, portable execution, or direct harness adapters.

The proposed direction is:

1. Introduce an optional Vuoro-native operator interface over existing subsystems.
2. Keep subsystem ownership and direct CLI access intact.
3. Treat agent runtimes as replaceable execution backends.
4. Investigate a minimal API-native runtime only where existing harnesses create measurable constraints.
5. Avoid moving domain semantics into a new facade merely to produce a unified command name.

---

## 1. Context

Vuoro already exists as a served control substrate deployed in the app-service cluster. Its component systems include, among others:

* Sprintctl for work definition, readiness, dependencies, and acceptance.
* Actionq for dispatch, claims, leases, retries, cancellation, and terminal outcomes.
* Auditctl for independent findings, evidence, and assessment.
* Kctl for Kubernetes or operational control workflows.
* Execution adapters for harnesses such as Claude Code, Codex, and OpenCode.

The component CLIs can operate against:

* Local state.
* Remote databases or APIs.
* Served Vuoro deployments.

The existing agent harnesses currently act as the interactive coordination layer. An orchestrating Claude Code or Codex session interprets instructions, calls the component CLIs, dispatches work, reviews evidence, and updates sprint or audit state.

This works, but leaves cross-runtime workflow semantics partly encoded in:

* Harness-specific system prompts.
* Repository instructions.
* Session context.
* Operator habits.
* Repeated agent reasoning.

The design question is whether some of this coordination should become an explicit Vuoro interface, and whether Vuoro should eventually include a small API-native agent runtime.

---

## 2. Decision Summary

### 2.1 Build an optional Vuoro operator interface

Create a local `vuoro` client that provides a coherent operator experience across the existing subsystem interfaces.

It should be:

* Optional rather than exclusive.
* Primarily a semantic and ergonomic layer.
* Capable of operating against local or served subsystem implementations.
* Usable by both humans and agents.
* Compatible with direct use of `sprintctl`, `actionq`, `auditctl`, and `kctl`.

The existing component CLIs remain valid first-class interfaces.

### 2.2 Do not initially make Vuoro the owner of subsystem logic

Vuoro should compose subsystem operations, not duplicate them.

For example:

```text
vuoro work inspect
```

may call Sprintctl functionality, but Sprintctl remains the authority for work state and readiness.

Likewise:

```text
vuoro execute dispatch
```

may compose Sprintctl selection, dispatch-plan compilation, and Actionq submission, but it must not invent a second work or action lifecycle.

### 2.3 Treat runtimes as execution backends

Claude Code, Codex, OpenCode, and a possible Vuoro-native runtime should implement the same broad execution contract.

The Vuoro interface may choose or invoke a runtime, but the runtime does not become the planning or governance authority.

```text
Operator or coordinator
        |
        v
   Vuoro interface
        |
        +--> Sprintctl
        +--> Actionq
        +--> Auditctl
        +--> Kctl
        |
        v
 Runtime adapter / execution contract
        |
        +--> Claude Code
        +--> Codex
        +--> OpenCode
        +--> Vuoro native runtime
```

### 2.4 Defer a full native runtime

The native runtime should begin as a bounded experiment, not as an attempted recreation of Claude Code.

Its purpose is to test whether direct API-level execution provides meaningful advantages in:

* Deterministic context assembly.
* Tool routing.
* Policy enforcement.
* Event capture.
* Token efficiency.
* Checkpointing and resumability.
* Multi-model execution.
* Removal of unwanted harness prompt behaviour.

If those advantages do not materialize, existing harnesses remain the preferred execution engines.

---

## 3. Architectural Principles

### 3.1 Preserve direct subsystem access

The following must remain possible:

```text
sprintctl ...
actionq ...
auditctl ...
kctl ...
```

The Vuoro interface must not become a mandatory hop.

This preserves:

* Debuggability.
* Break-glass access.
* Lower-level automation.
* Independent subsystem evolution.
* Recovery when the Vuoro facade is broken.

### 3.2 Identical cross-runtime behaviour belongs outside prompts

A behaviour should move into Vuoro or the underlying subsystem when it must be:

* Consistent across every runtime.
* Deterministically enforced.
* Independently auditable.
* Reproducible without relying on model compliance.

Examples include:

* Work eligibility checks.
* Immutable execution envelope creation.
* Required evidence registration.
* Allowed transition validation.
* Dispatch and cancellation rules.
* Receipt publication requirements.

Model-specific interaction and coding behaviour remain within runtime adapters or harness instructions.

### 3.3 Local operation remains a first-class recovery path

The architecture must not assume the served Vuoro deployment is always reachable.

At minimum, an operator must be able to:

* Inspect local work state.
* Run local diagnostics.
* Execute defined break-glass operations.
* Capture evidence locally.
* Restore or reconcile service operation later.

This does not necessarily require every workflow to support disconnected multi-writer synchronization.

A smaller and safer first commitment is:

```text
served mode unavailable
    -> enter explicit local recovery mode
    -> use local subsystem implementations
    -> record recovery evidence
    -> reconcile deliberately after restoration
```

Automatic bidirectional synchronization should not be introduced without explicit conflict and authority semantics.

### 3.4 The interface is a workflow language, not another authority

Commands may express operator intent:

```text
vuoro inspect
vuoro plan
vuoro dispatch
vuoro follow
vuoro review
vuoro recover
```

The implementation should resolve those intentions through existing authorities rather than owning parallel state.

### 3.5 Operations are primary; chat is optional

A Vuoro-native interface should not try to clone Claude Code's chat surface.

Its stronger differentiator is an operational workbench that exposes:

* Current work.
* Dependency and readiness state.
* Queue state.
* Active executions.
* Runtime selection.
* Evidence and receipts.
* Findings.
* Recovery controls.

A conversational coordinator can be attached to this interface, but conversation should not be the canonical state.

---

## 4. Proposed Component Model

## 4.1 Vuoro served backend

Responsibilities:

* Expose released subsystem capabilities.
* Provide discovery of available services and runtimes.
* Route requests to the subsystem authorities.
* Present unified authentication and connectivity where appropriate.
* Aggregate operational read models.
* Avoid interpreting Sprintctl or Actionq lifecycle state independently.

The server may expose a capability document such as:

```json
{
  "capabilities": {
    "work": {
      "provider": "sprintctl",
      "modes": ["served"]
    },
    "execution": {
      "provider": "actionq",
      "modes": ["served"]
    },
    "audit": {
      "provider": "auditctl",
      "modes": ["served", "local"]
    },
    "runtimes": [
      {
        "id": "claude-code",
        "adapter": "claude-code/v1"
      },
      {
        "id": "codex",
        "adapter": "codex/v1"
      }
    ]
  }
}
```

This is discovery metadata, not a second domain model.

---

## 4.2 Vuoro local client

The local client provides:

* Authentication and endpoint selection.
* Capability discovery.
* Local versus served transport resolution.
* Stable command naming.
* Structured and human-readable output.
* Session and execution attachment.
* Streaming event presentation.
* TTY workbench functionality.
* Runtime selection and invocation.
* Explicit fallback into local recovery mode.

The client should be mostly stateless.

Any persistent client state should be limited to items such as:

* Profiles.
* Authentication material references.
* Endpoint configuration.
* User presentation preferences.
* Cached capability metadata.
* Explicitly created local recovery state.

The client must not silently maintain shadow work or action state.

---

## 4.3 Subsystem clients and domain libraries

Preferred direction:

```text
Subsystem domain package
    |
    +--> subsystem CLI
    +--> subsystem served API
    +--> Vuoro adapter
```

Where practical, the Vuoro adapter should invoke a stable library or API rather than shelling out.

However, command invocation is acceptable for an initial proof where:

* The command interface is already stable.
* Structured output exists.
* Exit codes are meaningful.
* Cancellation and streaming can be handled correctly.
* The integration remains visibly replaceable.

Avoid copying subsystem implementation into the Vuoro repository merely to avoid a process boundary.

---

## 4.4 Runtime adapters

Each runtime adapter translates an immutable execution request into a concrete harness invocation.

An adapter may own:

* Runtime-specific prompt construction.
* Harness configuration.
* Environment setup.
* Model selection parameters.
* Tool registration syntax.
* Streaming protocol translation.
* Runtime-specific cancellation.
* Parsing of runtime events and terminal results.

It must not own:

* Work readiness.
* Sprint state.
* Queue lease semantics.
* Acceptance decisions.
* Audit verdicts.
* Canonical execution identity.

A conceptual adapter contract:

```go
type Runtime interface {
    Describe(ctx context.Context) (Capabilities, error)
    Prepare(ctx context.Context, req ExecutionRequest) (PreparedRun, error)
    Start(ctx context.Context, run PreparedRun, sink EventSink) (Handle, error)
    Cancel(ctx context.Context, handle Handle) error
    Inspect(ctx context.Context, handle Handle) (RuntimeStatus, error)
    Collect(ctx context.Context, handle Handle) (RuntimeResult, error)
}
```

---

## 4.5 Optional Vuoro-native runtime

The native runtime should initially provide only:

1. Model API invocation.
2. Message and context assembly.
3. Tool registration and invocation.
4. A bounded agent loop.
5. Streaming event emission.
6. Cancellation.
7. Token and cost accounting.
8. Terminal result production.
9. Checkpoint capture where supported.
10. Strict execution limits.

It should consume the same immutable execution request as other adapters.

It should not initially provide:

* General planning.
* Sprint decomposition.
* Multi-agent swarms.
* Long-term memory.
* Autonomous backlog mutation.
* A custom IDE.
* A generic plugin marketplace.
* Broad compatibility with every model provider.
* Its own work, action, or audit database.

---

## 5. Execution Contract

The execution contract should be defined before substantial runtime work.

A minimum request could contain:

```yaml
apiVersion: execution.v1
executionId: exec-123
work:
  provider: sprintctl
  workId: item-456
  snapshotId: snapshot-789
repository:
  url: git@example/repository.git
  revision: abcdef1234
workspace:
  isolation: disposable-clone
runtime:
  requested: claude-code
  modelClass: substantial
policy:
  profile: implementation-standard
  maxWallTime: 90m
  maxToolCalls: 500
  network: restricted
acceptance:
  command:
    - ./scripts/validate.sh
evidence:
  required:
    - implementation-summary
    - validation-results
    - diff-stat
```

A minimum event vocabulary:

```text
execution.accepted
workspace.preparing
workspace.ready
runtime.starting
runtime.started
model.requested
model.responded
tool.requested
tool.started
tool.completed
tool.failed
checkpoint.created
validation.started
validation.completed
artifact.published
runtime.completed
runtime.failed
execution.cancelled
execution.completed
```

The event contract must distinguish:

* Runtime observation.
* Actionq lifecycle state.
* Sprintctl work state.
* Audit findings.

These may correlate, but one must not be inferred as another.

---

## 6. Candidate Vuoro Interface

The following is a design sketch, not a committed command tree.

### Work inspection

```bash
vuoro work list
vuoro work show <id>
vuoro work ready
vuoro work graph <id>
```

Likely delegates primarily to Sprintctl.

### Execution

```bash
vuoro execute prepare <work-id>
vuoro execute dispatch <work-id> --runtime claude-code
vuoro execute dispatch <work-id> --runtime native
vuoro execute list
vuoro execute follow <execution-id>
vuoro execute cancel <execution-id>
```

Likely composes dispatch-plan compilation, Actionq, and runtime selection.

### Review and evidence

```bash
vuoro evidence list <execution-id>
vuoro evidence show <evidence-id>
vuoro review run <execution-id>
vuoro findings list
```

Likely delegates to evidence stores and Auditctl.

### Environment operations

```bash
vuoro env status
vuoro env doctor
vuoro env capabilities
vuoro env mode
```

Provides endpoint, mode, and capability visibility.

### Recovery

```bash
vuoro recover enter-local
vuoro recover inspect
vuoro recover export
vuoro recover reconcile
```

Recovery commands must be explicit and conservative. There should be no cheerful automatic merging of two authorities. Distributed state is quite capable of manufacturing its own cheerfulness.

### Interactive workbench

```bash
vuoro
```

Potential panes:

```text
┌ Work ──────────────┬ Executions ────────────┐
│ Ready: 7           │ Running: 3             │
│ Blocked: 4         │ Waiting: 2             │
│ Sprint: current    │ Failed: 1              │
├ Queue ─────────────┼ Evidence ───────────────┤
│ Claims and leases  │ Recent findings        │
├────────────────────┴─────────────────────────┤
│ Selected execution event stream              │
└───────────────────────────────────────────────┘
```

This should follow the same API and command contracts as non-interactive operation.

---

## 7. Native Runtime Benefits to Test

The native runtime is justified only if it demonstrates benefits that cannot be obtained cheaply through adapters.

### 7.1 Deterministic context assembly

The runtime can construct context directly from:

* Frozen work snapshots.
* Repository metadata.
* Relevant evidence.
* Policy profiles.
* Explicitly selected source files.
* Prior execution summaries.

This reduces dependence on harness-controlled context selection.

### 7.2 Explicit prompt ownership

Vuoro can own modular prompts rather than inheriting substantial opaque harness instructions.

Potential benefits:

* Reproducibility.
* Smaller prompts.
* Better model portability.
* Easier evaluation.
* Less accidental behavioural coupling.

Potential harm:

* Loss of mature harness guidance.
* Worse tool use.
* Missing safety and recovery behaviours.
* Reimplementation of prompt tuning already performed by larger vendors.

This is an empirical question, not an assumed win.

### 7.3 Lifecycle-aware tools

Tools can be bound to execution state and policy:

```text
tool call requested
    -> policy check
    -> lease or permission check
    -> invocation
    -> structured result
    -> event and evidence capture
```

This is stronger than asking the model to remember procedural rules.

### 7.4 First-class event and cost capture

Every model and tool interaction can produce controlled telemetry:

* Tokens.
* Cost.
* Latency.
* Retries.
* Tool result size.
* Context growth.
* Compaction.
* Failure category.
* Useful versus discarded output.

This may materially improve runtime comparisons.

### 7.5 Multi-model composition

A native runtime could explicitly route:

* Bulk implementation.
* Escalation.
* Review.
* Summarization.
* Validation.

This should only be added after a single-model execution loop is stable.

---

## 8. Risks

### 8.1 Rebuilding invisible harness maturity

Existing harnesses already handle large amounts of unpleasant plumbing:

* Streaming edge cases.
* Tool-call protocol differences.
* Context truncation.
* Provider retries.
* Terminal interaction.
* Patch application.
* Model quirks.
* Authentication.
* Safety boundaries.
* Session continuation.

The project must assume this hidden surface is larger than it appears.

### 8.2 Facade duplication

A Vuoro interface that merely renames commands creates:

* More documentation.
* More compatibility obligations.
* More failure modes.
* No meaningful semantic gain.

Every workflow added to Vuoro should remove duplicated coordination or materially improve observability.

### 8.3 New central dependency

A mandatory Vuoro client or server would weaken the existing resilience model.

Direct subsystem access and local recovery must remain available.

### 8.4 Ambiguous authority

Local and served state cannot both be casually writable without explicit conflict rules.

The first implementation should prefer:

* One active authority.
* Explicit mode selection.
* Export and reconciliation workflows.
* Append-only evidence of recovery actions.

### 8.5 Chat-interface gravity

Trying to reproduce an existing coding harness UI could consume the project without improving the control substrate.

The interface should lead with operations and state, not an imitation chat box.

---

## 9. Recommended Initial Scope

Implement only enough to validate the architecture.

### First Vuoro workflow

Select one workflow with genuine cross-subsystem coordination:

```text
inspect ready work
    -> freeze selected work
    -> create execution request
    -> dispatch through Actionq
    -> select runtime adapter
    -> stream execution events
    -> collect terminal result and evidence
```

Candidate command:

```bash
vuoro execute dispatch <work-id> --runtime <runtime>
```

### First native runtime experiment

Support one provider, one model, and a small tool set:

* Read file.
* Search repository.
* Write patch.
* Run command.
* Publish result.

Execute the same frozen task through:

1. Claude Code adapter.
2. Codex or OpenCode adapter.
3. Native runtime.

Compare outcomes rather than architecture aesthetics.

---

## 10. Design Acceptance Criteria

The design is successful when:

* The same frozen work can be dispatched to multiple runtimes.
* Direct subsystem CLIs remain operational.
* Vuoro does not duplicate canonical work or action state.
* Runtime events are captured in a common representation.
* The operator can see exactly which authority handled each operation.
* Served and local modes are explicit.
* A served-mode failure has a documented local recovery path.
* Removing the Vuoro client does not make underlying state inaccessible.
* The native runtime can be removed without changing Sprintctl, Actionq, or Auditctl semantics.

---

## 11. Recommended Position

Proceed with the Vuoro operator interface as a narrow composition and observability layer.

Do not yet commit to replacing existing harnesses.

Build the execution contract and runtime adapter seam first. Then implement a deliberately small native runtime as a challenger backend and retain it only if evidence shows meaningful improvement in control, efficiency, or reproducibility.


# Vuoro Interface and Native Runtime — Roadmap Handoff

## Objective

Determine whether Vuoro should gain:

1. An optional operator-facing CLI or TTY interface that composes existing subsystem operations.
2. A minimal API-native agent runtime that can execute governed work without depending on Claude Code, Codex, or OpenCode as the primary harness.

The roadmap must avoid committing to either component before its value is demonstrated.

---

## Workstreams

Run three related lanes.

```text
Lane A — Vuoro operator interface
Lane B — Runtime market and architecture assessment
Lane C — Execution contract and comparative evidence
```

Lane C is the shared foundation and should begin first.

---

# Lane C — Execution Contract and Evidence Foundation

## Goal

Define the stable boundary between Vuoro governance and any execution runtime.

This prevents runtime research from becoming coupled to one harness or one implementation.

---

## C1. Inventory current execution semantics

Document the current end-to-end path for at least one real implementation dispatch.

Capture:

* How work is selected.
* How readiness is checked.
* How instructions are assembled.
* How repositories are materialized.
* How the harness is invoked.
* How tools are made available.
* How progress is observed.
* How cancellation works.
* How validation is executed.
* How evidence is published.
* How terminal state is recorded.
* Which behaviours exist only in prompts or operator habits.

### Deliverable

```text
docs/runtime/current-execution-path.md
```

### Evidence gate

The document must identify each behaviour's current owner:

```text
Sprintctl
Dispatch-plan compiler
Actionq
Runner
Harness
Agent prompt
Operator
Auditctl
Other
```

Do not proceed while ownership remains described as “the system handles it.”

---

## C2. Define execution request v1

Create an immutable execution request schema.

Minimum fields:

* Execution identity.
* Frozen work reference.
* Repository revision.
* Workspace isolation requirement.
* Runtime request.
* Model or model-class request.
* Policy profile.
* Limits.
* Validation commands.
* Required evidence.
* Environment inputs.
* Secret references.
* Correlation identifiers.

### Deliverables

```text
schemas/execution-request-v1.schema.json
docs/runtime/execution-request-v1.md
fixtures/execution-request-v1/
```

### Evidence gate

At least two existing runtime adapters must be able to consume the same fixture without changing its work semantics.

Runtime-specific fields should be isolated under an explicit extension namespace.

---

## C3. Define common runtime event model

Specify a common event envelope and initial vocabulary.

Required envelope fields:

```yaml
eventId:
executionId:
timestamp:
producer:
type:
sequence:
payload:
```

Required categories:

* Workspace events.
* Runtime lifecycle.
* Model requests and responses.
* Tool invocations.
* Checkpoints.
* Validation.
* Artifacts.
* Terminal outcome.

### Deliverables

```text
schemas/runtime-event-v1.schema.json
docs/runtime/runtime-events-v1.md
```

### Evidence gate

Events from at least two existing harnesses can be normalized without pretending they have identical capabilities.

Lossy mappings must be documented.

---

## C4. Establish comparison metrics

Define metrics before building a native runtime.

### Outcome metrics

* Acceptance criteria passed.
* Validation passed.
* Human corrections required.
* Defects found in review.
* Work completed or abandoned.

### Efficiency metrics

* Input tokens.
* Output tokens.
* Cached tokens where available.
* Tool-result bytes exposed to the model.
* Wall-clock duration.
* Model cost.
* Number of model turns.
* Number of tool calls.
* Number of failed or repeated tool calls.

### Governance metrics

* Policy violations prevented.
* Required evidence produced.
* Unattributed state changes.
* Missing or malformed receipts.
* Cancellation responsiveness.
* Replay or reconstruction completeness.

### Operator metrics

* Manual interventions.
* Commands required.
* Context handoffs.
* Recovery steps.
* Ability to understand current state.

### Deliverable

```text
docs/runtime/evaluation-protocol.md
```

---

# Lane A — Vuoro Operator Interface

## Goal

Test whether an optional Vuoro interface meaningfully improves cross-subsystem operation without duplicating domain logic.

---

## A1. Command and workflow inventory

Identify current recurring workflows performed through agent harnesses.

Candidate workflows:

* Inspect current sprint and ready work.
* Select and freeze work for execution.
* Dispatch work to Actionq.
* Choose a runtime.
* Follow execution.
* Cancel or retry.
* Inspect evidence.
* Start independent review.
* Diagnose environment and service state.
* Enter local recovery mode.

For each workflow, record:

* Commands currently called.
* Required reasoning.
* Repeated prompt instructions.
* Cross-subsystem sequencing.
* Error and recovery handling.
* Whether it should be deterministic or model-mediated.

### Deliverable

```text
docs/interface/workflow-inventory.md
```

### Classification

Each step should be marked:

```text
DOMAIN
  Must remain in owning subsystem.

COMPOSITION
  Suitable for Vuoro workflow.

RUNTIME
  Specific to harness or model execution.

HUMAN-JUDGMENT
  Should not be prematurely automated.
```

---

## A2. Choose one vertical workflow

Recommended first workflow:

```text
vuoro execute dispatch <work-id> --runtime <runtime>
```

Expected composition:

```text
1. Resolve configured local or served mode.
2. Ask Sprintctl for work and readiness.
3. Freeze or retrieve immutable work snapshot.
4. Compile execution request.
5. Submit to Actionq.
6. Resolve runtime capability.
7. Start execution through the selected adapter.
8. Stream normalized events.
9. Report terminal outcome and evidence references.
```

### Non-goals

Do not initially add:

* A generic workflow DSL.
* A plugin ecosystem.
* An embedded chat UI.
* Automatic sprint planning.
* Automatic finding disposition.
* Bidirectional offline synchronization.

---

## A3. Implement transport and capability discovery

The Vuoro client should determine:

* Which served endpoint is active.
* Which subsystems are available.
* Which operations support local mode.
* Which runtimes are registered.
* Which runtime capabilities differ.

Example:

```bash
vuoro env capabilities --output json
```

### Required properties

* Explicit mode reporting.
* No silent fallback from served to local writes.
* Machine-readable output.
* Stable error classes.
* No hidden shadow state.

### Validation

Test:

1. Served mode available.
2. Served mode unavailable.
3. Local subsystem available.
4. Neither available.
5. Capability mismatch.
6. Authentication failure.
7. Runtime registered but unhealthy.

---

## A4. Implement one-shot non-interactive CLI

Prioritize automation before TTY presentation.

Example:

```bash
vuoro execute dispatch sprintctl:item-123 \
  --runtime claude-code \
  --output json
```

The command should be callable from:

* A human shell.
* Claude Code.
* Codex.
* OpenCode.
* CI or another automation.
* A future Vuoro-native coordinator.

### Evidence gate

The new command must remove meaningful repeated coordination logic.

A wrapper that merely executes:

```bash
sprintctl ...
actionq ...
```

without centralizing a stable workflow does not pass.

---

## A5. Add follow and inspect operations

Candidate commands:

```bash
vuoro execute list
vuoro execute show <id>
vuoro execute follow <id>
vuoro execute cancel <id>
vuoro evidence list <id>
```

These should expose the canonical underlying identities and states.

Do not collapse:

* Action state.
* Runtime status.
* Work status.
* Audit verdict.

Present them together, but label them separately.

---

## A6. Prototype TTY workbench

Only after the command API is stable.

Initial screens:

* Work.
* Executions.
* Queue.
* Evidence.
* Findings.
* Environment status.

The TTY must call the same client API used by non-interactive commands.

### Evidence gate

Retain the TTY only if it improves one or more of:

* Time to find current state.
* Number of commands required.
* Failure diagnosis.
* Runtime comparison.
* Recovery operation.

A dashboard that looks impressive while the user returns to `jq` is ornamental infrastructure.

---

## A7. Local recovery design

Define explicit modes:

```text
served
local
recovery-local
```

Do not introduce an implicit “hybrid” write mode until authority and reconciliation semantics exist.

### Recovery workflow

```text
detect service failure
    -> operator explicitly enters recovery-local mode
    -> local subsystem operations become available
    -> every local recovery mutation is recorded
    -> state is exported after service restoration
    -> reconciliation is deliberate and auditable
```

### Deliverables

```text
docs/interface/local-recovery.md
runbooks/vuoro-served-unavailable.md
```

---

# Lane B — Runtime Market and Architecture Assessment

## Goal

Identify whether an existing runtime can be adapted or forked, and determine the minimum functionality a native runtime would need.

---

## B1. Decompose runtime responsibilities

Create a capability matrix covering:

* Provider API abstraction.
* Conversation loop.
* Tool schema registration.
* Tool execution.
* Parallel tool calls.
* Context construction.
* Context truncation or compaction.
* Streaming.
* Retry handling.
* Checkpointing.
* Session resumption.
* Cancellation.
* Workspace lifecycle.
* Shell execution.
* Patch application.
* File editing.
* Network control.
* Secret handling.
* Model routing.
* Cost accounting.
* Event emission.
* Human interruption.
* Policy hooks.
* Sandbox support.
* Remote execution.
* Multi-agent composition.

For each capability, mark:

```text
REQUIRED FOR V1
USEFUL LATER
ALREADY OWNED BY VUORO
RUNTIME-SPECIFIC
OUT OF SCOPE
```

### Deliverable

```text
docs/runtime/runtime-capability-matrix.md
```

---

## B2. Assess mature open-source candidates

At minimum, inspect:

* OpenHands.
* OpenAI Agents SDK.
* PydanticAI.
* LangGraph.
* AutoGen.
* Semantic Kernel.
* Letta or another stateful-agent runtime.
* Aider or another mature coding loop.
* OpenCode, where implementation access permits.
* Any runtime already used by the current adapters.

The assessment must use current source and documentation rather than project reputation alone.

### Per-project questions

1. What is the smallest independently reusable runtime component?
2. Is the agent loop coupled to the project's planner or UI?
3. Can external work identity and lifecycle state remain authoritative?
4. Can tools be policy-wrapped?
5. Can events be emitted into the Vuoro contract?
6. Can the workspace implementation be replaced?
7. Can context construction be controlled deterministically?
8. Can the runtime operate headlessly?
9. Can cancellation and resumption be integrated with Actionq?
10. What would a fork obligate Vuoro to maintain?
11. Is the licence compatible?
12. Is adaptation cheaper than implementing the minimal loop?

### Deliverables

```text
docs/runtime/candidates/<candidate>.md
docs/runtime/runtime-candidate-comparison.md
```

---

## B3. Select implementation strategies

Evaluate no more than four strategies:

### Strategy 1 — Existing harness adapters only

Continue using Claude Code, Codex, and OpenCode.

Improve:

* Adapter contracts.
* Prompt modularity.
* Event normalization.
* Context preparation.
* Observability.

### Strategy 2 — Embed a small agent SDK

Use an SDK such as PydanticAI or the OpenAI Agents SDK to avoid implementing provider and tool-loop plumbing.

Vuoro owns:

* Execution contract.
* Context assembly.
* Policies.
* Event capture.
* Workspace.
* Tools.

### Strategy 3 — Extract or fork a mature coding runtime

Adopt a runtime from OpenHands, Aider, OpenCode, or another project.

This is justified only if the extracted runtime is genuinely separable from its surrounding abstractions.

### Strategy 4 — Implement a minimal native loop

Build directly against one provider API.

This is acceptable if the minimal loop is smaller and more maintainable than adapting an existing framework.

### Recommended default

Start with Strategy 1 while assessing Strategies 2 and 4.

Do not fork a large coding-agent project merely because it already contains many features. Large forks are just subscriptions paid in merge conflicts.

---

## B4. Build a native runtime spike

The spike should support:

* One model provider.
* One execution at a time per worker.
* Sequential model turns.
* Structured tool calls.
* A small fixed tool set.
* Streaming.
* Cancellation.
* Token and cost accounting.
* Runtime event emission.
* Hard iteration and wall-time limits.
* Terminal result publication.

Suggested tools:

```text
repo.search
file.read
file.write
patch.apply
command.run
result.publish
```

### Explicit exclusions

* General memory.
* Autonomous subagents.
* Runtime-owned planning.
* Browser automation.
* Arbitrary plugins.
* Full terminal emulation.
* Multiple provider compatibility.
* Interactive IDE integration.

### Deliverable

```text
runtime-native/
```

or an isolated experiment repository if that better protects current subsystem boundaries.

---

## B5. Run comparative executions

Prepare a controlled corpus of tasks.

Suggested classes:

1. Small deterministic change.
2. Repository-wide mechanical change.
3. Moderate implementation requiring search and validation.
4. Failure diagnosis.
5. Task containing misleading but irrelevant context.
6. Task requiring explicit recovery after a failed command.
7. Task with a strict evidence requirement.

Each task should be frozen and run through:

* Claude Code adapter.
* Codex or OpenCode adapter.
* Native runtime.

Repeat enough runs to distinguish a pattern from one model getting lucky.

### Comparisons

* Outcome quality.
* Token consumption.
* Tool routing efficiency.
* Context growth.
* Policy compliance.
* Evidence completeness.
* Failure recovery.
* Operator intervention.
* Runtime implementation complexity.

---

# Cross-Lane Integration

## D1. Runtime registration

The Vuoro interface should discover runtime capabilities rather than hard-code implementation assumptions.

Example:

```yaml
id: native-openai
features:
  streaming: true
  resume: false
  parallelTools: false
  workspaceModes:
    - disposable-clone
models:
  - class: bulk
  - class: substantial
```

Runtime capability mismatches should fail before dispatch where possible.

---

## D2. Common execution UX

The operator experience should remain stable:

```bash
vuoro execute dispatch <work-id> --runtime claude-code
vuoro execute dispatch <work-id> --runtime codex
vuoro execute dispatch <work-id> --runtime native-openai
```

The selected runtime may change implementation details, but not:

* Work identity.
* Execution identity.
* Required evidence.
* Policy profile.
* Acceptance criteria.

---

## D3. Harness-operated Vuoro workflows

Existing harnesses remain able to call the Vuoro interface.

For example, a Claude Code coordinator may call:

```bash
vuoro work ready --output json
vuoro execute dispatch item-123 --runtime codex
vuoro execute follow exec-456 --output jsonl
vuoro review start exec-456
```

This enables gradual adoption:

```text
Phase 1:
Claude Code coordinates by calling individual subsystem CLIs.

Phase 2:
Claude Code coordinates through stable Vuoro workflows.

Phase 3:
Vuoro TTY or native coordinator becomes available.

Phase 4:
Operator chooses whichever interface best fits the task.
```

No flag day is required.

---

# Recommended Delivery Sequence

## Phase 0 — Documentation and ownership

Deliver:

* Current execution path.
* Capability matrix.
* Execution request v1.
* Runtime event v1.
* Evaluation protocol.

Exit criterion:

The team can describe the runtime boundary without referring to a specific harness.

---

## Phase 1 — Adapter normalization

Deliver:

* Shared runtime adapter interface.
* Two existing harness adapters using it.
* Common runtime event translation.
* Comparative telemetry.

Exit criterion:

The same execution request can be run through two existing harnesses.

Rollback:

Retain the existing direct adapter invocation path.

---

## Phase 2 — First Vuoro workflow

Deliver:

```bash
vuoro execute dispatch
vuoro execute follow
vuoro execute show
vuoro execute cancel
```

Exit criterion:

The workflow demonstrably removes repeated cross-subsystem coordination from harness prompts.

Rollback:

Continue calling the existing component CLIs directly.

---

## Phase 3 — Open-source runtime assessment

Deliver:

* Candidate reports.
* Comparison matrix.
* Recommendation: embed, extract, implement, or stop.

Exit criterion:

At least one candidate has been tested with a small integration spike rather than assessed only from documentation.

---

## Phase 4 — Native runtime challenger

Deliver:

* Minimal runtime.
* One provider.
* Fixed tool set.
* Shared execution contract.
* Event and cost telemetry.

Exit criterion:

The runtime completes the controlled task corpus safely enough for comparison.

It is not yet a production default.

Rollback:

Disable runtime registration. No subsystem migration is required.

---

## Phase 5 — Evidence-based decision

Choose one:

### Decision A — Keep harnesses only

Use when the native runtime provides no meaningful advantage.

Continue improving adapter and context preparation.

### Decision B — Retain native runtime for bounded classes

Use when it performs well for tasks such as:

* Bulk deterministic implementation.
* Cheap repeated validation.
* Controlled repository transformations.
* Highly governed execution.

### Decision C — Expand native runtime

Use only when evidence shows repeatable benefits across multiple task classes and the maintenance surface remains acceptable.

### Decision D — Adopt or fork an existing runtime

Use when an external codebase clearly reduces maintenance while preserving Vuoro's authority boundaries.

---

# Stop Conditions

Stop or narrow the effort if any of the following occurs:

* The Vuoro interface mostly renames individual component commands.
* The native runtime requires reproducing a large fraction of Claude Code before completing useful work.
* Runtime integration forces Actionq or Sprintctl semantics into the runtime.
* Local and served modes introduce ambiguous concurrent authority.
* Comparative runs show no meaningful efficiency, control, or reproducibility improvement.
* Maintenance cost exceeds the operational friction being removed.
* The project starts prioritizing chat UX over execution contracts and evidence.

---

# Success Criteria

The overall roadmap succeeds when:

1. Vuoro workflows can be invoked from any harness or directly by an operator.
2. Underlying subsystem authorities remain intact.
3. Existing harnesses and a native runtime can consume the same execution request.
4. Runtime choice is replaceable and observable.
5. Workflows that require deterministic governance no longer depend solely on prompt compliance.
6. Local recovery remains available when the served deployment is unavailable.
7. The native runtime is retained or rejected based on comparative evidence.
8. Each added Vuoro abstraction removes more duplicated coordination than it creates.

---

# Immediate Backlog

## Foundation

* [ ] Document one current production-like dispatch path.
* [ ] Assign ownership for every step.
* [ ] Draft `execution-request-v1`.
* [ ] Draft `runtime-event-v1`.
* [ ] Define comparison corpus and metrics.

## Vuoro interface

* [ ] Inventory repeated coordinator workflows.
* [ ] Select the first vertical workflow.
* [ ] Define local and served mode resolution.
* [ ] Define capability discovery.
* [ ] Implement structured `execute dispatch`.
* [ ] Implement `execute follow`.
* [ ] Validate direct CLI fallback.

## Runtime assessment

* [ ] Build runtime capability matrix.
* [ ] Inspect mature open-source candidates.
* [ ] Select one embeddable SDK candidate.
* [ ] Select one extract-or-fork candidate.
* [ ] Build one narrow integration spike.
* [ ] Decide whether a native loop remains justified.

## Comparative experiment

* [ ] Freeze representative tasks.
* [ ] Execute through current harness adapters.
* [ ] Execute through the native challenger.
* [ ] Compare outcomes, tokens, tool routing, recovery, and evidence.
* [ ] Publish retain, narrow, expand, or abandon decision.

---

# Final Recommendation

Build the execution contract and the first Vuoro workflow before building a substantial native runtime.

The Vuoro interface is likely valuable where it captures stable cross-subsystem workflows and provides a coherent operational view. The native runtime is potentially valuable, but only as a replaceable execution backend tested against mature harnesses.

The intended end state is not “Vuoro replaces Claude Code.”

It is:

```text
Vuoro composes governed work and execution capabilities owned by Sprintctl,
Actionq, runner implementations, and Auditctl.

Operators and coordinator agents use whichever interface is most effective.

Existing harnesses, and any later native challenger, compete behind the
owner-defined portable runner contract.
```


Tightrope2-Epidemic-Glue-Chapter-Itinerary
